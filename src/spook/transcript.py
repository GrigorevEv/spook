"""Транскрипт: Реплики по порядку времени, собранные в абзацы, и отметки отдельными строками курсивом.

Соседние Реплики одного Источника склеиваются в абзац с одним временем в начале. Новый абзац
начинается при смене говорящего (Источника), после молчания дольше `MAX_SILENCE_SECONDS` или когда
абзац перерос `MAX_PARAGRAPH_CHARS`. Отметка (о Паузе, смене устройства или пропуске) закрывает абзац:
Реплика после неё начинает новый.

После перерыва в работе службы Транскрипт восстанавливается из Заметки (`Transcript.restore`):
абзацы и отметки — такими, какими записаны; время Реплик внутри абзацев не хранится, поэтому
продолжение возможно только с новой Реплики после отметки.

Как подписывать время, решает Сессия: Лекция — от начала Сессии (`format_offset`), Встреча — по часам;
во Встрече абзац несёт ещё пометку говорящего: «Собеседники» (Системный звук) или «Я» (Микрофон).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Callable

from spook.audio import Source

MAX_SILENCE_SECONDS = 10.0
MAX_PARAGRAPH_CHARS = 700

SPEAKERS = {Source.SYSTEM: "Собеседники", Source.MIC: "Я"}

_PARAGRAPH_LINE = re.compile(r"^\*\*(?P<heading>[^*]+)\*\* (?P<text>.*)$")
_MARK_LINE = re.compile(r"^\*(?P<label>[^*].*)\*$")


def format_offset(seconds: float) -> str:
    """Время от начала Сессии в виде `Ч:ММ:СС`."""
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


@dataclass(frozen=True)
class Utterance:
    source: Source
    start: float  # секунды от начала Сессии
    end: float
    text: str


@dataclass(frozen=True)
class Paragraph:
    source: Source
    start: float  # секунды от начала Сессии
    end: float  # конец последней Реплики абзаца — от него отсчитывается молчание
    text: str
    number: int  # порядковый номер в Транскрипте, с единицы; по нему абзац находят на странице
    label: str  # время в начале абзаца: от начала Сессии (Лекция) или по часам (Встреча)
    speaker: str | None = None  # «Собеседники» или «Я» во Встрече; в Лекции говорящий не указывается

    @property
    def element_id(self) -> str:
        return f"paragraph-{self.number}"

    def as_markdown(self) -> str:
        heading = f"{self.label} {self.speaker}:" if self.speaker else self.label
        return f"**{heading}** {self.text}"


@dataclass(frozen=True)
class PauseMark:
    """Отметка о Паузе: `*Пауза <начало>–<конец>*`; вид времени (от начала Сессии или по часам) выбирает Сессия.
    С `title="Пропуск"` — отметка о пропуске: перерыв в работе службы."""

    start: str
    end: str
    title: str = "Пауза"

    @property
    def label(self) -> str:
        return f"{self.title} {self.start}–{self.end}"

    def as_markdown(self) -> str:
        return f"*{self.label}*"


@dataclass(frozen=True)
class DeviceMark:
    """Отметка о переходе захвата Источника на другое устройство: `*<время> <Источник> переключён на «…»*`;
    при пропаже устройства — `*<время> <Источник>: устройство «…» пропало, захват продолжается с «…»*`.
    Вид времени (от начала Сессии или по часам) выбирает Сессия."""

    time: str
    source: Source
    device_name: str  # новое устройство; пустое — устройства нет
    lost: str = ""  # название пропавшего устройства, если переход вызван его пропажей

    @property
    def text(self) -> str:
        """Отметка без времени — она же предупреждение на странице."""
        if not self.lost:
            return f"{self.source.label} переключён на «{self.device_name}»"
        continued = f"захват продолжается с «{self.device_name}»" if self.device_name else "захвата нет"
        return f"{self.source.label}: устройство «{self.lost}» пропало, {continued}"

    @property
    def label(self) -> str:
        return f"{self.time} {self.text}"

    def as_markdown(self) -> str:
        return f"*{self.label}*"


@dataclass(frozen=True)
class WrittenMark:
    """Отметка, восстановленная из Заметки после перерыва в работе службы: текст как записан."""

    label: str

    def as_markdown(self) -> str:
        return f"*{self.label}*"


Mark = PauseMark | DeviceMark | WrittenMark
Entry = Paragraph | Mark


class Transcript:
    def __init__(self, time_label: Callable[[float], str] = format_offset, speakers: bool = False) -> None:
        """`time_label` подписывает начало абзаца по его времени от начала Сессии;
        `speakers` — ставить ли пометку говорящего (Встреча)."""
        self._time_label = time_label
        self._speakers = speakers
        self._entries: list[Entry] = []

    @property
    def entries(self) -> tuple[Entry, ...]:
        """Абзацы и отметки в порядке появления."""
        return tuple(self._entries)

    @property
    def paragraphs(self) -> tuple[Paragraph, ...]:
        return tuple(entry for entry in self._entries if isinstance(entry, Paragraph))

    def add(self, utterance: Utterance) -> tuple[Paragraph, bool]:
        """Принимает Реплику; возвращает абзац после неё и признак «абзац начат этой Репликой».

        Продолжить можно только последнюю запись, и только если это абзац: отметка закрывает абзац.
        """
        last = self._entries[-1] if self._entries else None
        if isinstance(last, Paragraph) and self._continues(last, utterance):
            paragraph = replace(last, end=utterance.end, text=f"{last.text} {utterance.text}")
            self._entries[-1] = paragraph
            return paragraph, False
        paragraph = Paragraph(
            source=utterance.source,
            start=utterance.start,
            end=utterance.end,
            text=utterance.text,
            number=len(self.paragraphs) + 1,
            label=self._time_label(utterance.start),
            speaker=SPEAKERS[utterance.source] if self._speakers else None,
        )
        self._entries.append(paragraph)
        return paragraph, True

    def add_mark(self, mark: Mark) -> None:
        self._entries.append(mark)

    def restore(self, markdown: str) -> None:
        """Абзацы и отметки из текста Заметки под заголовком «Транскрипт» — в начало Транскрипта.
        Строки, которых Транскрипт не пишет, пропускаются. Время Реплик не восстанавливается:
        после восстановления первой должна идти отметка."""
        speakers = {label: source for source, label in SPEAKERS.items()}
        for line in markdown.splitlines():
            if paragraph := _PARAGRAPH_LINE.match(line):
                heading, source, speaker = paragraph["heading"], Source.SYSTEM, None
                if self._speakers and heading.endswith(":"):
                    heading, _, speaker = heading.removesuffix(":").rpartition(" ")
                    source = speakers.get(speaker, Source.SYSTEM)
                self._entries.append(
                    Paragraph(source, 0.0, 0.0, paragraph["text"], len(self.paragraphs) + 1, heading, speaker)
                )
            elif mark := _MARK_LINE.match(line):
                self._entries.append(WrittenMark(mark["label"]))

    @staticmethod
    def _continues(last: Paragraph, utterance: Utterance) -> bool:
        return (
            utterance.source is last.source
            and utterance.start - last.end <= MAX_SILENCE_SECONDS
            and len(last.text) <= MAX_PARAGRAPH_CHARS
        )
