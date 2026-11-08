"""Настройки — TOML в `~/.config/spook/settings.toml`; умолчания — из спецификации.

Файл может отсутствовать или содержать только часть ключей: остальное берётся из умолчаний.
Настройки читаются заново перед каждой Сессией, поэтому правки действуют без перезапуска службы.

Экран «Настройки» правит часть ключей (`SCREEN_FIELDS`) через `screen_values` и `save_screen_settings`;
остальные ключи файла при сохранении остаются как были, комментарии теряются.
"""

from __future__ import annotations

import json
import math
import os
import re
import tomllib
from dataclasses import dataclass, field, fields, replace
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping

from spook.audio import Source


class SessionKind(StrEnum):
    MEETING = "meeting"
    LECTURE = "lecture"

    @property
    def label(self) -> str:
        return {SessionKind.MEETING: "Встреча", SessionKind.LECTURE: "Лекция"}[self]

    @property
    def tag(self) -> str:
        return {SessionKind.MEETING: "встреча", SessionKind.LECTURE: "лекция"}[self]


EFFORTS = ("low", "medium", "high", "xhigh", "max")


@dataclass(frozen=True)
class KindSettings:
    """Настройки одного вида Сессии: куда писать Заметки и чем работает Агент."""

    folder: Path
    skill: str
    model: str = "opus"
    effort: str = "high"
    agent_cwd: Path | None = None
    """Рабочий каталог запуска Агента; `None` — папка вида. Для Лекции — корень Вики (`Settings.for_kind`)."""


@dataclass(frozen=True)
class SourceSettings:
    """Закреплённые устройства Источников (`node.name` PipeWire); пустая строка — устройство по умолчанию."""

    system: str = ""
    mic: str = ""

    def for_source(self, source: Source) -> str:
        return self.system if source is Source.SYSTEM else self.mic


@dataclass(frozen=True)
class RecognitionSettings:
    """Нарезка на Реплики и модель распознавания; умолчания — выбор замера задачи 01
    (`docs/research/gigaam-benchmark.md`). Параметры детектора речи — как у `with_vad` в onnx-asr —
    действуют со следующей Сессии, параметры модели — со следующего запуска службы."""

    max_utterance_seconds: float = 20.0  # длиннее речь без паузы режется принудительно
    speech_threshold: float = 0.5  # вероятность речи, с которой начинается речь; кончается ниже на 0,15
    min_silence_duration_ms: int = 500  # пауза, закрывающая Реплику, — это плюс два запаса по краям: 0,7 с
    min_speech_duration_ms: int = 250  # речь короче — щелчок или вздох, а не Реплика
    speech_pad_ms: int = 100  # запас звука по краям Реплики
    forced_cut_min_pause_ms: int = 100  # пауза, по которой режется длинная речь; без неё — разрез жёсткий
    model_dir: Path = field(default_factory=lambda: data_dir() / "models" / "gigaam-v3-e2e-rnnt")
    model_revision: str = "322c3b29492673eb7d0b434bfa9dfb8653e34d02"  # HF istupakov/gigaam-v3-onnx
    quantization: str = "int8"
    intra_op_threads: int = 4
    inter_op_threads: int = 1


@dataclass(frozen=True)
class AgentSettings:
    """Запуск Агента.

    `timeout_minutes` — сколько ждать запуска, прежде чем завершить его с «превышено время»; больше
    четырёх минут, потому что без сети Claude Code сам сдаётся только через ~3 минуты повторов.
    `wrapper` — команда, которой обернуть запуск (по умолчанию нет: служба systemd и так даёт чистое
    окружение). Например, `systemd-run --user --wait --pipe --collect --quiet --same-dir
    -p RuntimeMaxSec={timeout}` — чистое окружение пользовательского менеджера systemd для службы,
    поднятой вручную; `{timeout}` заменяется таймаутом в секундах (прерывание `systemd-run` не
    останавливает запущенный им юнит, поэтому юниту нужен свой предел).
    """

    timeout_minutes: float = 15
    wrapper: str = ""


@dataclass(frozen=True)
class ProgramSettings:
    """Внешние программы; в тестах подменяются скриптами-заглушками.

    `claude` — сама программа Claude Code по полному пути, а не обёртка из оболочки пользователя;
    `wiki` — командная строка инструмента Вики (к ней добавляется `file <имя заметки>`). `~` в них
    раскрывается.
    """

    claude: str = str(Path("~/.local/bin/claude").expanduser())
    notify: str = "notify-send"
    open: str = "xdg-open"
    wiki: str = "python3 ~/learn/wiki/_tools/wiki_html.py"


def _home(path: str) -> Path:
    return Path(path).expanduser()


@dataclass(frozen=True)
class Settings:
    port: int = 8765
    auto_stop_minutes: float = 5  # можно дробное: 1.5 — полторы минуты
    terms_dictionary: Path = field(default_factory=lambda: _home("~/Documents/notes/работа/Словарь терминов.md"))
    wiki: Path = field(default_factory=lambda: _home("~/learn/wiki"))
    meeting: KindSettings = field(
        default_factory=lambda: KindSettings(_home("~/Documents/notes/работа"), "spook-meeting")
    )
    lecture: KindSettings = field(
        default_factory=lambda: KindSettings(_home("~/learn/wiki/lectures"), "spook-lecture")
    )
    sources: SourceSettings = field(default_factory=SourceSettings)
    recognition: RecognitionSettings = field(default_factory=RecognitionSettings)
    programs: ProgramSettings = field(default_factory=ProgramSettings)
    agent: AgentSettings = field(default_factory=AgentSettings)

    def for_kind(self, kind: SessionKind) -> KindSettings:
        """Настройки вида; у Лекции рабочий каталог Агента — корень Вики, чтобы действовали её правила."""
        if kind is SessionKind.LECTURE:
            return replace(self.lecture, agent_cwd=self.lecture.agent_cwd or self.wiki)
        return self.meeting

    def kind_folders(self) -> list[tuple[SessionKind, Path]]:
        """Папки Заметок по видам — где служба признаёт Заметки Сессий (`note.find_session_note`)."""
        return [(kind, self.for_kind(kind).folder) for kind in SessionKind]


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
    return Path(base).expanduser() / "spook"


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or "~/.local/state"
    return Path(base).expanduser() / "spook"


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or "~/.local/share"
    return Path(base).expanduser() / "spook"


def default_settings_path() -> Path:
    return config_dir() / "settings.toml"


def load_settings(path: Path | None = None) -> Settings:
    """Читает настройки из файла; отсутствующий файл или ключ означает умолчание."""
    path = path or default_settings_path()
    raw: dict[str, Any] = {}
    if path.exists():
        with path.open("rb") as file:
            raw = tomllib.load(file)
    defaults = Settings()
    return Settings(
        port=int(raw.get("port", defaults.port)),
        auto_stop_minutes=float(raw.get("auto_stop_minutes", defaults.auto_stop_minutes)),
        terms_dictionary=_path_or(raw.get("terms_dictionary"), defaults.terms_dictionary),
        wiki=_path_or(raw.get("wiki"), defaults.wiki),
        meeting=_kind(raw.get("meeting", {}), defaults.meeting),
        lecture=_kind(raw.get("lecture", {}), defaults.lecture),
        sources=_merge(defaults.sources, raw.get("sources", {})),
        recognition=_recognition(raw.get("recognition", {}), defaults.recognition),
        programs=_programs(raw.get("programs", {}), defaults.programs),
        agent=_merge(defaults.agent, raw.get("agent", {})),
    )


def _path_or(value: Any, default: Path) -> Path:
    return _home(str(value)) if value else default


def _kind(raw: dict[str, Any], defaults: KindSettings) -> KindSettings:
    merged = _merge(defaults, raw)
    agent_cwd = _home(str(raw["agent_cwd"])) if raw.get("agent_cwd") else defaults.agent_cwd
    return replace(merged, folder=_path_or(raw.get("folder"), defaults.folder), agent_cwd=agent_cwd)


def _programs(raw: dict[str, Any], defaults: ProgramSettings) -> ProgramSettings:
    merged = _merge(defaults, raw)
    return replace(merged, claude=os.path.expanduser(merged.claude))


def _recognition(raw: dict[str, Any], defaults: RecognitionSettings) -> RecognitionSettings:
    merged = _merge(defaults, raw)
    return replace(merged, model_dir=_path_or(raw.get("model_dir"), defaults.model_dir))


def _merge(defaults: Any, raw: dict[str, Any]) -> Any:
    known = {item.name for item in fields(defaults)}
    return replace(defaults, **{key: value for key, value in raw.items() if key in known})


# --- экран «Настройки» ------------------------------------------------------------

SCREEN_FIELDS = (
    *(f"{kind.value}.{key}" for kind in SessionKind for key in ("folder", "skill", "model", "effort")),
    *(f"sources.{source.value}" for source in Source),
    "auto_stop_minutes",
    "terms_dictionary",
)
"""Поля экрана «Настройки» — ключи файла настроек, через точку — ключ таблицы."""

# Скилл и модель уходят в командную строку Агента: одно слово, без пробелов и без ведущего «-».
_AGENT_WORD = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\[\]-]{0,79}$")


class SettingsRejected(Exception):
    """Значения экрана «Настройки» недопустимы; `errors` — причина по каждому полю (пустой ключ — про весь файл)."""

    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__("; ".join(errors.values()))
        self.errors = errors


def screen_values(settings: Settings) -> dict[str, str]:
    """Текущие значения полей экрана «Настройки» строками, как их покажет форма."""
    values: dict[str, str] = {}
    for kind in SessionKind:
        kind_settings = settings.for_kind(kind)
        values[f"{kind.value}.folder"] = str(kind_settings.folder)
        values[f"{kind.value}.skill"] = kind_settings.skill
        values[f"{kind.value}.model"] = kind_settings.model
        values[f"{kind.value}.effort"] = kind_settings.effort
    for source in Source:
        values[f"sources.{source.value}"] = settings.sources.for_source(source)
    values["auto_stop_minutes"] = f"{settings.auto_stop_minutes:g}"
    values["terms_dictionary"] = str(settings.terms_dictionary)
    return values


def save_screen_settings(path: Path, form: Mapping[str, str]) -> None:
    """Проверяет поля экрана «Настройки» и пишет их в файл настроек.

    Ключи файла, которых на экране нет (порт, `[recognition]`, `[programs]` и любые другие), остаются
    как были; комментарии теряются. При любом недопустимом значении — `SettingsRejected`, файл не меняется.
    """
    changes, errors = _check_screen(form)
    raw: dict[str, Any] = {}
    if path.exists():
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
            errors[""] = f"Файл настроек {path} не читается: {error}"
    if errors:
        raise SettingsRejected(errors)
    for key, value in changes.items():
        table, _, name = key.rpartition(".")
        target = raw
        if table:
            if not isinstance(raw.get(table), dict):
                raw[table] = {}
            target = raw[table]
        target[name] = value
    text = _toml(raw)
    if tomllib.loads(text) != raw:  # запись не должна исказить ни одного ключа
        raise SettingsRejected({"": "Файл настроек не удалось записать без искажений"})
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _check_screen(form: Mapping[str, str]) -> tuple[dict[str, Any], dict[str, str]]:
    """Значения для файла и причины отказа по полям."""
    changes: dict[str, Any] = {}
    errors: dict[str, str] = {}
    value = {key: str(form.get(key, "")).strip() for key in SCREEN_FIELDS}
    for kind in SessionKind:
        prefix = kind.value
        for key, reason in (
            (f"{prefix}.folder", _path_problem(value[f"{prefix}.folder"], f"папку Заметок вида «{kind.label}»")),
            (f"{prefix}.skill", _word_problem(value[f"{prefix}.skill"], "Скилл")),
            (f"{prefix}.model", _word_problem(value[f"{prefix}.model"], "модель")),
        ):
            if reason:
                errors[key] = reason
        if value[f"{prefix}.effort"] not in EFFORTS:
            errors[f"{prefix}.effort"] = (
                f"Неизвестный effort «{value[f'{prefix}.effort']}»: допустимы {', '.join(EFFORTS)}"
            )
    minutes_text = value["auto_stop_minutes"]
    try:
        minutes = float(minutes_text.replace(",", "."))
    except ValueError:
        minutes = math.nan
    if not (math.isfinite(minutes) and minutes > 0):
        errors["auto_stop_minutes"] = f"Время автостопа — число минут больше нуля, а не «{minutes_text}»"
    reason = _path_problem(value["terms_dictionary"], "путь к Словарю терминов")
    if reason:
        errors["terms_dictionary"] = reason
    if errors:
        return changes, errors
    for key in SCREEN_FIELDS:
        changes[key] = value[key]
    changes["auto_stop_minutes"] = int(minutes) if minutes.is_integer() else minutes
    return changes, errors


def _path_problem(text: str, what: str) -> str:
    if not text:
        return f"Укажите {what}"
    if not Path(text).expanduser().is_absolute():
        return f"Нужен полный путь (от «/» или «~»), а не «{text}»"
    return ""


def _word_problem(text: str, what: str) -> str:
    if not text:
        return f"Укажите {what}"
    if not _AGENT_WORD.match(text):
        return f"{what.capitalize()} — одно слово из латиницы, цифр и «.-_:» без пробелов, а не «{text}»"
    return ""


# --- запись TOML: всё, что даёт tomllib; массивы таблиц пишутся массивами встроенных таблиц ---

_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def _toml(raw: dict[str, Any], prefix: tuple[str, ...] = ()) -> str:
    plain = [f"{_toml_key(key)} = {_toml_value(value)}\n" for key, value in raw.items() if not isinstance(value, dict)]
    text = ""
    if prefix and (plain or not any(isinstance(value, dict) for value in raw.values())):
        text += f"[{'.'.join(_toml_key(part) for part in prefix)}]\n"
    text += "".join(plain)
    for key, value in raw.items():
        if isinstance(value, dict):
            text += ("\n" if text else "") + _toml(value, (*prefix, key))
    return text


def _toml_key(key: str) -> str:
    return key if _BARE_KEY.match(key) else _toml_string(key)


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value):
            return "nan"
        if math.isinf(value):
            return "inf" if value > 0 else "-inf"
        return repr(value)
    if isinstance(value, str):
        return _toml_string(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{_toml_key(key)} = {_toml_value(item)}" for key, item in value.items()) + " }"
    return value.isoformat()  # дата и время TOML


def _toml_string(text: str) -> str:
    # Строка JSON — допустимая базовая строка TOML; DEL TOML требует экранировать, JSON — нет.
    return json.dumps(text, ensure_ascii=False).replace("\x7f", "\\u007f")
