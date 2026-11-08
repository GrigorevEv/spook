"""Обвязка тестов главного шва: служба поднимается в процессе, тест действует как браузер.

Подмены внешнего мира: вход звука (образцы вместо PipeWire), детектор речи по громкости, сценарный распознаватель,
скрипты-заглушки вместо внешних программ (Claude Code, уведомления, средство открытия, инструмент
Вики), управляемые часы.
Настройки — временный TOML-файл; папки Хранилищ и каталог состояния — временные каталоги.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterator

import httpx
import numpy as np
import pytest
import uvicorn

from spook.audio import AudioInput
from spook.clock import ManualClock
from spook.cutting import SpeechDetectorFactory
from spook.fakes import FakeAudioInput, LoudnessSpeechDetector, ScriptedRecognizer
from spook.recognizer import Recognizer
from spook.serve import make_server
from spook.settings import Settings, load_settings
from spook.web import create_app

pytest_plugins = ("chrome",)  # фикстуры браузерных тестов: `browser`, `context`, `page`, `screenshot`

SAMPLE_RATE = 16_000
SESSION_START = datetime(2026, 11, 6, 14, 2, tzinfo=timezone(timedelta(hours=3)))
STUBS_DIR = Path(__file__).parent / "stubs"

NOTE_AFTER_START = (
    "---\n"
    "date: 2026-11-06\n"
    'start: "14:02"\n'
    "tags: [лекция]\n"
    "---\n"
    "\n"
    "## Транскрипт\n"
)


MEETING_NOTE_AFTER_START = (
    "---\n"
    "date: 2026-11-06\n"
    'start: "14:02"\n'
    "tags: [встреча]\n"
    "---\n"
    "\n"
    "## Транскрипт\n"
)


def note_text(spook: "Service") -> str:
    """Текст Заметки Лекции, начатой в `SESSION_START`."""
    return (spook.lectures_dir / "2026-11-06 14-02 Лекция.md").read_text(encoding="utf-8")


def meeting_note_text(spook: "Service") -> str:
    """Текст Заметки Встречи, начатой в `SESSION_START`."""
    return (spook.meetings_dir / "2026-11-06 14-02 Встреча.md").read_text(encoding="utf-8")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def speech(seconds: float) -> np.ndarray:
    """Отрезок «речи» для подменённого входа: шум, по длине равный заданным секундам."""
    rng = np.random.default_rng(seed=int(seconds * 1000))
    return rng.uniform(-0.3, 0.3, int(seconds * SAMPLE_RATE)).astype(np.float32)


@dataclass
class SseEvent:
    name: str
    data: str


class EventStream:
    """Чтение потока событий так, как его читает браузер."""

    def __init__(self, client: httpx.Client, url: str, timeout: float) -> None:
        self._stream = client.stream("GET", url, timeout=timeout)
        self._timeout = timeout
        self._lines: Iterator[str] | None = None

    def __enter__(self) -> "EventStream":
        response = self._stream.__enter__()
        assert response.status_code == 200, response.status_code
        assert response.headers["content-type"].startswith("text/event-stream")
        self._lines = response.iter_lines()
        return self

    def __exit__(self, *exc) -> None:
        self._stream.__exit__(*exc)

    def next_event(self) -> SseEvent:
        assert self._lines is not None
        name, data = "message", []
        for line in self._lines:
            if line == "":
                if data:
                    return SseEvent(name, "\n".join(data))
                continue
            if line.startswith(":"):
                continue
            key, _, value = line.partition(":")
            value = value.removeprefix(" ")
            if key == "event":
                name = value
            elif key == "data":
                data.append(value)
        raise AssertionError("поток событий закончился")

    def wait_for(self, name: str, containing: str = "") -> SseEvent:
        """Следующее событие с данным именем, содержащее текст; остальные пропускаются.

        Срок ожидания общий, а не на каждое событие: иначе непрерывные `levels` настоящего входа
        ждали бы вечно."""
        deadline = time.monotonic() + 4 * self._timeout
        while True:
            assert time.monotonic() < deadline, f"событие {name} «{containing}» не пришло"
            event = self.next_event()
            if event.name == name and containing in event.data:
                return event


@dataclass
class StubProgram:
    """Скрипт-заглушка внешней программы: запоминает вызовы и играет сценарий.

    Сама заглушка — `tests/stubs/<name>_stub.py`; служба вызывает тонкий сценарий `path`,
    в котором зашит каталог управления `control` — там вызовы, сценарий и файл отпускания.
    """

    name: str
    control: Path
    path: Path

    @classmethod
    def create(cls, control: Path, name: str) -> "StubProgram":
        control.mkdir(parents=True, exist_ok=True)
        path = control / name
        path.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{STUBS_DIR / f"{name}_stub.py"}" "{control}" "$@"\n',
            encoding="utf-8",
        )
        path.chmod(0o755)
        return cls(name, control, path)

    def calls(self) -> list[dict]:
        """Вызовы по порядку: `argv` (без имени программы) и `cwd`."""
        record = self.control / f"{self.name}-calls.jsonl"
        if not record.exists():
            return []
        return [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines() if line]

    def play(self, scenario: str) -> None:
        """Сценарий следующих вызовов; какие бывают — в шапке `tests/stubs/<name>_stub.py`."""
        (self.control / f"{self.name}-scenario").write_text(scenario, encoding="utf-8")

    def release(self) -> None:
        """Отпускает заглушку, ждущую по сценарию `wait`."""
        (self.control / f"{self.name}-release").touch()


@dataclass
class Service:
    """Служба, поднятая на подменах; доступ — только по HTTP и через файлы."""

    base_url: str
    settings: Settings
    settings_path: Path
    lectures_dir: Path
    meetings_dir: Path
    state_dir: Path
    audio: AudioInput  # в обычных тестах — FakeAudioInput с `feed`
    recognizer: Recognizer  # в обычных тестах — ScriptedRecognizer с `say` и `heard`
    clock: ManualClock
    agent: StubProgram
    notify: StubProgram
    opener: StubProgram  # средство открытия (`xdg-open`)
    wiki: StubProgram  # инструмент Вики (`wiki_html.py`)
    http: httpx.Client = field(repr=False)
    restart: Callable[[], None] = field(repr=False)
    """Перезапуск сервера на том же порту: потоки событий обрываются, Сессия в службе остаётся."""
    restart_service: Callable[[float], None] = field(repr=False)
    """Перерыв в работе службы: служба обрывается без уборки (как при сбое или выключении) и через
    столько секунд по часам поднимается заново — новая служба над тем же каталогом состояния, теми же
    Хранилищами и настройками, на том же порту. Часы, вход звука и распознаватель у новой службы свои
    (`clock`, `audio`, `recognizer` заменяются: тексты для неё задаются после перезапуска), заглушки — прежние."""

    @property
    def wiki_dir(self) -> Path:
        """Корень Вики: в тестах — папка над папкой Лекций, как `~/learn/wiki` над `lectures`."""
        return self.lectures_dir.parent

    @property
    def programs(self) -> dict[str, str]:
        return {
            "claude": str(self.agent.path),
            "notify": str(self.notify.path),
            "open": str(self.opener.path),
            "wiki": str(self.wiki.path),
        }

    def write_settings(self, lectures_dir: Path | None = None, **overrides) -> None:
        """Переписывает файл настроек, сохраняя порт, папку Встреч и пути заглушек; остальное — как у `write_settings`."""
        overrides.setdefault("meetings_dir", self.meetings_dir)
        overrides.setdefault("terms_dictionary", self.settings.terms_dictionary)
        write_settings(
            self.settings_path,
            self.settings.port,
            lectures_dir or self.lectures_dir,
            programs=self.programs,
            **overrides,
        )

    def page(self) -> httpx.Response:
        return self.http.get(f"{self.base_url}/")

    def start_lecture(self, topic: str = "", link: str = "") -> httpx.Response:
        """«Старт» Лекции; поля Темы и Ссылки страница отправляет всегда, пусть и пустыми."""
        return self.http.post(
            f"{self.base_url}/session/start", data={"kind": "lecture", "topic": topic, "link": link}
        )

    def start_meeting(self, topic: str = "") -> httpx.Response:
        """«Старт» Встречи; поле Темы страница отправляет всегда, пусть и пустым."""
        return self.http.post(f"{self.base_url}/session/start", data={"kind": "meeting", "topic": topic})

    def set_topic(self, topic: str) -> httpx.Response:
        return self.http.post(f"{self.base_url}/session/topic", data={"topic": topic})

    def set_link(self, link: str) -> httpx.Response:
        return self.http.post(f"{self.base_url}/session/link", data={"link": link})

    def pause(self) -> httpx.Response:
        return self.http.post(f"{self.base_url}/session/pause")

    def resume(self) -> httpx.Response:
        return self.http.post(f"{self.base_url}/session/resume")

    def stop(self) -> httpx.Response:
        return self.http.post(f"{self.base_url}/session/stop")

    def events(self, timeout: float = 5.0) -> EventStream:
        return EventStream(self.http, f"{self.base_url}/events", timeout)

    def wait_until(self, condition: Callable[[], bool], timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while not condition():
            if time.monotonic() > deadline:
                raise AssertionError("условие не наступило за отведённое время")
            time.sleep(0.01)


def write_settings(
    path: Path,
    port: int,
    lectures_dir: Path,
    max_utterance_seconds: float | None = None,
    auto_stop_minutes: float | None = None,
    programs: dict[str, str] | None = None,
    lecture: dict[str, str] | None = None,
    recognition: dict[str, float] | None = None,
    meetings_dir: Path | None = None,
    terms_dictionary: Path | None = None,
    agent: dict[str, object] | None = None,
) -> None:
    """Пишет файл настроек: порт, папку Лекций (корень Вики — папка над ней), необязательные длину
    Реплики и другие ключи `[recognition]`, время автостопа, ключи `[lecture]`, пути `[programs]`,
    ключи `[agent]`, папку Встреч и путь к Словарю терминов. Без `meetings_dir` папка Встреч остаётся
    умолчанием — такие тесты Встречу не начинают; без `terms_dictionary` — тоже: Агент Встречи создал
    бы заготовку Словаря у пользователя."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lecture_lines = "".join(f'{key} = "{value}"\n' for key, value in (lecture or {}).items())
    recognition_keys = dict(recognition or {})
    if max_utterance_seconds is not None:
        recognition_keys["max_utterance_seconds"] = max_utterance_seconds
    recognition_lines = "".join(f"{key} = {value}\n" for key, value in recognition_keys.items())
    recognition_section = f"\n[recognition]\n{recognition_lines}" if recognition_lines else ""
    auto_stop = "" if auto_stop_minutes is None else f"auto_stop_minutes = {auto_stop_minutes}\n"
    terms = "" if terms_dictionary is None else f'terms_dictionary = "{terms_dictionary}"\n'
    meeting = "" if meetings_dir is None else f'\n[meeting]\nfolder = "{meetings_dir}"\n'
    program_lines = "".join(f'{key} = "{value}"\n' for key, value in (programs or {}).items())
    agent_lines = "".join(f"{key} = {json.dumps(value, ensure_ascii=False)}\n" for key, value in (agent or {}).items())
    agent_section = f"\n[agent]\n{agent_lines}" if agent_lines else ""
    path.write_text(
        f"port = {port}\n"
        f'wiki = "{lectures_dir.parent}"\n'
        f"{auto_stop}"
        f"{terms}"
        "\n"
        "[lecture]\n"
        f'folder = "{lectures_dir}"\n'
        f"{lecture_lines}"
        f"{meeting}"
        f"{recognition_section}"
        f"{agent_section}"
        "\n"
        "[programs]\n"
        f"{program_lines}",
        encoding="utf-8",
    )


@pytest.fixture
def audio_input() -> AudioInput:
    """Вход звука службы; модуль тестов может подменить его своей фикстурой (например, настоящим входом)."""
    return FakeAudioInput()


@pytest.fixture
def speech_detector() -> SpeechDetectorFactory:
    """Детектор речи службы: по громкости, без модели; модуль тестов может подменить его настоящим."""
    return LoudnessSpeechDetector


@pytest.fixture
def recognizer() -> Recognizer:
    """Распознаватель службы — сценарный; модуль тестов может подменить его своей фикстурой
    (например, настоящим GigaAM в медленных тестах)."""
    return ScriptedRecognizer()


@pytest.fixture
def spook(
    tmp_path: Path,
    audio_input: AudioInput,
    recognizer: Recognizer,
    speech_detector: SpeechDetectorFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Service]:
    settings_path = tmp_path / "config" / "spook" / "settings.toml"
    lectures_dir = tmp_path / "wiki" / "lectures"
    meetings_dir = tmp_path / "notes" / "работа"
    state_dir = tmp_path / "state" / "spook"
    # Настройки лежат, как у пользователя, в `$XDG_CONFIG_HOME/spook/`: так команда `spook topic`,
    # вызванная заглушкой Агента (ей достаётся окружение службы), находит порт службы теста.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    # Пользовательские Скиллы Claude Code (`~/.claude/skills`) служба читает, чтобы проверить их шапку;
    # в тестах каталог Claude Code — временный, настоящие Скиллы пользователя не участвуют.
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    agent = StubProgram.create(tmp_path / "stubs", "claude")
    notify = StubProgram.create(tmp_path / "stubs", "notify")
    opener = StubProgram.create(tmp_path / "stubs", "open")
    wiki = StubProgram.create(tmp_path / "stubs", "wiki")
    programs = {"claude": str(agent.path), "notify": str(notify.path), "open": str(opener.path), "wiki": str(wiki.path)}
    write_settings(
        settings_path,
        free_port(),
        lectures_dir,
        programs=programs,
        meetings_dir=meetings_dir,
        terms_dictionary=meetings_dir / "Словарь терминов.md",
    )
    settings = load_settings(settings_path)

    def load_stubbed_settings() -> Settings:
        """Настройки теста; внешние программы — только заглушки из временного каталога, иначе тест
        запустил бы настоящий Claude Code или открыл окно пользователю (переписывайте настройки
        через `spook.write_settings`)."""
        loaded = load_settings(settings_path)
        programs = loaded.programs
        for program in (programs.claude, programs.notify, programs.open, programs.wiki):
            assert Path(program).is_relative_to(tmp_path), f"внешняя программа вне заглушек: {program}"
        assert loaded.terms_dictionary.is_relative_to(tmp_path), "Словарь терминов вне временного каталога"
        return loaded

    def build_app(audio: AudioInput, recognizer: Recognizer, clock: ManualClock):
        return create_app(
            load_settings=load_stubbed_settings,
            audio_input=audio,
            recognizer=recognizer,
            clock=clock,
            state_dir=state_dir,
            speech_detector=speech_detector,
            settings_path=settings_path,
        )

    audio = audio_input
    clock = ManualClock(SESSION_START)
    app = build_app(audio, recognizer, clock)

    def start_server() -> tuple[uvicorn.Server, threading.Thread]:
        server = make_server(app, port=settings.port, log_level="warning")
        thread = threading.Thread(target=server.run, name="spook-test-server", daemon=True)
        thread.start()
        deadline = time.monotonic() + 5
        while not server.started:
            assert time.monotonic() < deadline, "служба не поднялась"
            time.sleep(0.01)
        return server, thread

    def stop_server(server: uvicorn.Server, thread: threading.Thread) -> None:
        server.should_exit = True
        thread.join(timeout=5)
        assert not thread.is_alive(), "служба не остановилась"

    running = start_server()

    def restart() -> None:
        nonlocal running
        stop_server(*running)
        running = start_server()

    def restart_service(after_seconds: float) -> None:
        # Прежняя служба остаётся в памяти без дела: её часы больше не идут, вход звука и распознаватель
        # отключены (распознавание, ждущее у ворот прежнего распознавателя, так и не закончится).
        nonlocal running, app
        stop_server(*running)
        service.clock = ManualClock(service.clock.now() + timedelta(seconds=after_seconds))
        service.audio = FakeAudioInput()
        service.recognizer = ScriptedRecognizer()
        app = build_app(service.audio, service.recognizer, service.clock)
        running = start_server()

    with httpx.Client(timeout=5.0) as http:
        service = Service(
            base_url=f"http://127.0.0.1:{settings.port}",
            settings=settings,
            settings_path=settings_path,
            lectures_dir=lectures_dir,
            meetings_dir=meetings_dir,
            state_dir=state_dir,
            audio=audio,
            recognizer=recognizer,
            clock=clock,
            agent=agent,
            notify=notify,
            opener=opener,
            wiki=wiki,
            http=http,
            restart=restart,
            restart_service=restart_service,
        )
        yield service

    stop_server(*running)


@dataclass
class Demo:
    """Показ `spook demo`, запущенный отдельным процессом; доступ — только по HTTP."""

    base_url: str
    process: subprocess.Popen = field(repr=False)
    http: httpx.Client = field(repr=False)

    def events(self, timeout: float = 15.0) -> EventStream:
        return EventStream(self.http, f"{self.base_url}/events", timeout)

    def terminate(self) -> int:
        """Останавливает показ так же, как это делает systemd или Ctrl+C, и возвращает код выхода."""
        if self.process.poll() is None:
            self.process.terminate()
        return self.process.wait(timeout=10)


@pytest.fixture
def demo(tmp_path: Path) -> Iterator[Demo]:
    port = free_port()
    log = (tmp_path / "demo.log").open("w", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, "-m", "spook.cli", "demo", "--port", str(port)],
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.monotonic() + 15
        while True:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                assert process.poll() is None, f"показ завершился с кодом {process.returncode}"
                assert time.monotonic() < deadline, "показ не поднялся"
                time.sleep(0.05)
        with httpx.Client(timeout=5.0) as http:
            yield Demo(base_url=f"http://127.0.0.1:{port}", process=process, http=http)
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=10)
        log.close()
