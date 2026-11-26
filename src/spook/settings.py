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

    @property
    def label(self) -> str:
        return "Встреча"

    @property
    def tag(self) -> str:
        return "встреча"


EFFORTS = ("low", "medium", "high", "xhigh", "max")


@dataclass(frozen=True)
class KindSettings:
    folder: Path
    skill: str
    model: str = "opus"
    effort: str = "high"
    agent_cwd: Path | None = None


@dataclass(frozen=True)
class SourceSettings:
    system: str = ""
    mic: str = ""

    def for_source(self, source: Source) -> str:
        return self.system if source is Source.SYSTEM else self.mic


@dataclass(frozen=True)
class RecognitionSettings:
    max_utterance_seconds: float = 20.0
    speech_threshold: float = 0.5
    min_silence_duration_ms: int = 500
    min_speech_duration_ms: int = 250
    speech_pad_ms: int = 100
    forced_cut_min_pause_ms: int = 100
    model_dir: Path = field(default_factory=lambda: data_dir() / "models" / "gigaam-v3-e2e-rnnt")
    model_revision: str = "322c3b29492673eb7d0b434bfa9dfb8653e34d02"
    quantization: str = "int8"
    intra_op_threads: int = 4
    inter_op_threads: int = 1


@dataclass(frozen=True)
class AgentSettings:
    timeout_minutes: float = 15
    wrapper: str = ""


@dataclass(frozen=True)
class ProgramSettings:
    claude: str = str(Path("~/.local/bin/claude").expanduser())
    notify: str = "notify-send"
    open: str = "xdg-open"
    wiki: str = "python3 ~/learn/wiki/_tools/wiki_html.py"


def _home(path: str) -> Path:
    return Path(path).expanduser()


@dataclass(frozen=True)
class Settings:
    port: int = 8765
    auto_stop_minutes: float = 5
    terms_dictionary: Path = field(default_factory=lambda: _home("~/Documents/notes/работа/Словарь терминов.md"))
    wiki: Path = field(default_factory=lambda: _home("~/learn/wiki"))
    meeting: KindSettings = field(
        default_factory=lambda: KindSettings(_home("~/Documents/notes/работа"), "spook-meeting")
    )
    sources: SourceSettings = field(default_factory=SourceSettings)
    recognition: RecognitionSettings = field(default_factory=RecognitionSettings)
    programs: ProgramSettings = field(default_factory=ProgramSettings)
    agent: AgentSettings = field(default_factory=AgentSettings)

    def for_kind(self, kind: SessionKind) -> KindSettings:
        return self.meeting

    def kind_folders(self) -> list[tuple[SessionKind, Path]]:
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


SCREEN_FIELDS = (
    *(f"{kind.value}.{key}" for kind in SessionKind for key in ("folder", "skill", "model", "effort")),
    *(f"sources.{source.value}" for source in Source),
    "auto_stop_minutes",
    "terms_dictionary",
)

_AGENT_ARGV_WORD = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\[\]-]{0,79}$")

WHOLE_FILE_KEY = ""


class SettingsRejected(Exception):
    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__("; ".join(errors.values()))
        self.errors = errors


def screen_values(settings: Settings) -> dict[str, str]:
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
    changes, errors = _check_screen(form)
    raw: dict[str, Any] = {}
    if path.exists():
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
            errors[WHOLE_FILE_KEY] = f"Файл настроек {path} не читается: {error}"
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
    if tomllib.loads(text) != raw:
        raise SettingsRejected({WHOLE_FILE_KEY: "Файл настроек не удалось записать без искажений"})
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _check_screen(form: Mapping[str, str]) -> tuple[dict[str, Any], dict[str, str]]:
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
    if not _AGENT_ARGV_WORD.match(text):
        return f"{what.capitalize()} — одно слово из латиницы, цифр и «.-_:» без пробелов, а не «{text}»"
    return ""


_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")
_DEL_UNESCAPED_BY_JSON = "\x7f"


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
    return value.isoformat()


def _toml_string(text: str) -> str:
    return json.dumps(text, ensure_ascii=False).replace(_DEL_UNESCAPED_BY_JSON, "\\u007f")
