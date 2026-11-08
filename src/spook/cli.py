"""Команда `spook`: `spook serve` запускает службу, `spook prepare` скачивает модели распознавания
и детектора речи (её вызывает установка), `spook demo` — показ на подменах, `spook topic` — Тема
от Агента, `spook install` / `spook uninstall` — установка для пользователя (см. `install.py`).

`spook topic <путь к Заметке> <Тема>` вызывает Скилл Агента (в шапке — `Bash(spook topic *)`), когда
у Заметки нет Темы. Команда работает без переменных окружения сессии: порт службы берётся из файла
настроек (`~/.config/spook/settings.toml` или `--settings`), запрос идёт к `127.0.0.1:<порт>`.
Проверяет условия и переименовывает сама служба; команда печатает новый путь в stdout и завершается
кодом 0, при отказе — причина в stderr и код 1.
"""

from __future__ import annotations

import argparse
import logging
import sys
import urllib.error
import urllib.parse
import urllib.request
from functools import partial
from pathlib import Path

from spook.settings import Settings, default_settings_path, load_settings

TOPIC_TIMEOUT_SECONDS = 30

log = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="spook", description="Spook: звук → текст → Заметка → Выжимка Агентом")
    commands = parser.add_subparsers(dest="command", required=True)
    settings_help = f"файл настроек (по умолчанию {default_settings_path()})"
    serve_command = commands.add_parser("serve", help="запустить службу")
    serve_command.add_argument("--settings", type=Path, default=None, help=settings_help)
    prepare_command = commands.add_parser(
        "prepare", help="скачать модели распознавания и детектора речи, если их ещё нет (вызывается при установке)"
    )
    prepare_command.add_argument("--settings", type=Path, default=None, help=settings_help)
    demo_command = commands.add_parser(
        "demo", help="показ на подменах: звук из образцов, сценарный текст, Заметки во временной папке"
    )
    demo_command.add_argument("--port", type=int, default=Settings().port, help="порт (по умолчанию %(default)s)")
    topic_command = commands.add_parser(
        "topic", help="Тема от Агента: дать завершённой Заметке без Темы имя по образцу и напечатать новый путь"
    )
    topic_command.add_argument("--settings", type=Path, default=None, help=settings_help)
    topic_command.add_argument("note", type=Path, help="путь к Заметке")
    topic_command.add_argument("topic", nargs="+", help="Тема (слова можно не брать в кавычки)")
    repo_help = "репозиторий Spook (по умолчанию — тот, из которого запущена команда, или текущий каталог)"
    install_command = commands.add_parser(
        "install", help="установить: инструмент uv, служба systemd, значок в меню, Скиллы, модели"
    )
    install_command.add_argument("--repo", type=Path, default=None, help=repo_help)
    uninstall_command = commands.add_parser(
        "uninstall", help="удалить службу, значок, ссылки на Скиллы и инструмент (настройки и модели остаются)"
    )
    uninstall_command.add_argument("--repo", type=Path, default=None, help=repo_help)
    args = parser.parse_args(argv)
    if args.command == "serve":
        serve(args.settings)
    elif args.command == "prepare":
        sys.exit(prepare(args.settings))
    elif args.command == "demo":
        from spook.demo import run_demo

        run_demo(args.port)
    elif args.command == "topic":
        sys.exit(topic(args.settings, args.note, " ".join(args.topic)))
    elif args.command in ("install", "uninstall"):
        sys.exit(install(args.command, args.repo))


def serve(settings_path: Path | None) -> None:
    from spook.clock import SystemClock
    from spook.pipewire import PipeWireAudioInput
    from spook.recognizer import GigaamRecognizer, model_is_present
    from spook.serve import exit_quietly_on_stop_signals, make_server
    from spook.web import create_app

    exit_quietly_on_stop_signals()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    load = partial(load_settings, settings_path)
    settings = load()
    # Вход звука — PipeWire: устройства по умолчанию или закреплённые в `[sources]` настроек (читаются при «Старт»).
    # Распознаватель — GigaAM: модель загружается в память при первом «Старт», в простое служба только ждёт.
    # Параметры модели из `[recognition]` берутся при запуске службы.
    if not model_is_present(settings.recognition):
        log.warning("Модель распознавания не скачана в %s: выполните `spook prepare`", settings.recognition.model_dir)
    app = create_app(
        load_settings=load,
        audio_input=PipeWireAudioInput(),
        recognizer=GigaamRecognizer(settings.recognition),
        clock=SystemClock(),
        settings_path=settings_path or default_settings_path(),
    )
    make_server(app, port=settings.port).run()


def prepare(settings_path: Path | None) -> int:
    """Скачивает модели распознавания и детектора речи один раз; те, что уже есть, не трогает. Код выхода."""
    from spook import silero
    from spook.recognizer import RecognitionUnavailable, model_is_present, prepare_model

    settings = load_settings(settings_path).recognition
    steps = (
        ("распознавания", settings.model_dir, lambda: model_is_present(settings), lambda: prepare_model(settings)),
        ("детектора речи", silero.default_model_dir(), silero.model_is_present, silero.prepare_model),
    )
    for name, folder, present, download in steps:
        if present():
            print(f"Модель {name} уже есть: {folder}")
            continue
        print(f"Скачиваю модель {name} в {folder}…", flush=True)
        try:
            download()
        except RecognitionUnavailable as error:
            print(error, file=sys.stderr)
            return 1
        print(f"Модель {name} скачана: {folder}")
    return 0


def topic(settings_path: Path | None, note: Path, topic: str) -> int:
    """Сообщает службе Тему Заметки; новый путь — в stdout, код 0. Отказ службы или её отсутствие —
    причина в stderr, код 1."""
    url = f"http://127.0.0.1:{load_settings(settings_path).port}/agent/topic"
    body = urllib.parse.urlencode({"note": str(note.absolute()), "topic": topic}).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=TOPIC_TIMEOUT_SECONDS) as response:
            answer = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        print(error.read().decode("utf-8", errors="replace").strip() or f"Служба ответила {error.code}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        reason = getattr(error, "reason", error)
        print(f"Служба Spook не отвечает ({url}): {reason}", file=sys.stderr)
        return 1
    print(answer.strip())
    return 0


def install(command: str, repo: Path | None) -> int:
    """`spook install` / `spook uninstall` для текущего пользователя; код выхода."""
    from spook import install as installation

    repo = repo or installation.find_repo(Path(__file__).resolve().parent) or installation.find_repo(Path.cwd())
    if repo is None or not (repo / "pyproject.toml").is_file():
        print("Не найден репозиторий Spook: запустите из него или укажите --repo", file=sys.stderr)
        return 1
    try:
        if command == "install":
            installation.install(repo.resolve(), home=Path.home(), port=load_settings().port)
        else:
            installation.uninstall(repo.resolve(), home=Path.home())
    except installation.InstallError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    main()
