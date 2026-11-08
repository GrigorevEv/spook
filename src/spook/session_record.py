"""Сведения об идущей Сессии на диске — чтобы пережить перерыв в работе службы (сон, сбой, перезапуск).

Файл `<каталог состояния>/session.json`: путь Заметки, вид, Тема, Ссылка, состояние, время начала,
захваченное время, время последней активности и последней Реплики. Ни звука, ни текста Реплик здесь нет (ADR-0001):
Транскрипт живёт только в Заметке. Файл пишется целиком и атомарно — во временный файл рядом,
затем переименованием, — так что после сбоя на диске либо прежние сведения, либо новые.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from spook.settings import SessionKind

log = logging.getLogger(__name__)

RECORD_NAME = "session.json"


class SessionState(StrEnum):
    """Состояние Сессии: «нет Сессии» → «идёт» ⇄ «на Паузе» → «завершается» → «нет Сессии»."""

    NONE = "none"
    RUNNING = "running"
    PAUSED = "paused"
    FINISHING = "finishing"

    @property
    def label(self) -> str:
        return {
            SessionState.NONE: "нет Сессии",
            SessionState.RUNNING: "идёт",
            SessionState.PAUSED: "на Паузе",
            SessionState.FINISHING: "завершается",
        }[self]


@dataclass(frozen=True)
class SessionRecord:
    note: Path
    kind: SessionKind
    topic: str
    named_topic: str  # Тема в имени Заметки
    link: str
    state: SessionState  # идёт, на Паузе или завершается
    started_at: datetime
    captured_seconds: float  # захваченное время на момент последней активности
    last_active_at: datetime  # обновляется, пока служба работает с Сессией; при «завершается» — время конца
    paused_at: datetime | None = None
    # Последняя Реплика (или «Старт», «Продолжить»): перерыв считается тишиной от неё; нет — от последней активности.
    last_speech_at: datetime | None = None

    def to_json(self) -> str:
        data = {
            "note": str(self.note),
            "kind": self.kind.value,
            "topic": self.topic,
            "named_topic": self.named_topic,
            "link": self.link,
            "state": self.state.value,
            "started_at": self.started_at.isoformat(),
            "captured_seconds": round(self.captured_seconds, 3),
            "last_active_at": self.last_active_at.isoformat(),
            "paused_at": self.paused_at.isoformat() if self.paused_at else None,
            "last_speech_at": self.last_speech_at.isoformat() if self.last_speech_at else None,
        }
        return json.dumps(data, ensure_ascii=False, indent=2) + "\n"

    @classmethod
    def from_json(cls, text: str) -> SessionRecord:
        data = json.loads(text)
        return cls(
            note=Path(data["note"]),
            kind=SessionKind(data["kind"]),
            topic=data["topic"],
            named_topic=data["named_topic"],
            link=data["link"],
            state=SessionState(data["state"]),
            started_at=datetime.fromisoformat(data["started_at"]),
            captured_seconds=float(data["captured_seconds"]),
            last_active_at=datetime.fromisoformat(data["last_active_at"]),
            paused_at=datetime.fromisoformat(data["paused_at"]) if data.get("paused_at") else None,
            last_speech_at=datetime.fromisoformat(data["last_speech_at"]) if data.get("last_speech_at") else None,
        )


class SessionStore:
    """Файл сведений; без пути (`None`) служба ничего не хранит."""

    def __init__(self, path: Path | None) -> None:
        self._path = path

    def save(self, record: SessionRecord) -> None:
        if self._path is None:
            return
        temporary = self._path.with_name(f".{self._path.name}.tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(record.to_json(), encoding="utf-8")
            os.replace(temporary, self._path)
        except OSError:
            log.exception("Сведения о Сессии не записаны в %s", self._path)

    def load(self) -> SessionRecord | None:
        """Сведения прошлой работы службы; испорченный файл удаляется с записью в журнал."""
        if self._path is None or not self._path.exists():
            return None
        try:
            return SessionRecord.from_json(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError, KeyError, TypeError):
            log.exception("Сведения о Сессии в %s не прочитаны — удалены", self._path)
            self.remove()
            return None

    def remove(self) -> None:
        if self._path is None:
            return
        try:
            self._path.unlink(missing_ok=True)
        except OSError:
            log.exception("Сведения о Сессии %s не удалены", self._path)
