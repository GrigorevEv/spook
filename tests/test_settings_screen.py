"""Экран «Настройки» — главный шов: HTTP так, как ходит браузер, файл настроек во временном каталоге,
список устройств — у подменённого входа звука.

Форма читается так, как её видит браузер: поля, их значения и выбранные пункты списков.
"""

from __future__ import annotations

import tomllib
from html.parser import HTMLParser
from pathlib import Path

import pytest

from spook.audio import Device, Source

from conftest import speech

MEETING_NOTE_NAME = "2026-11-06 14-02 Встреча"


class Form(HTMLParser):
    """Поля формы экрана: `values` — что браузер отправит, `options` — пункты списков (значение, подпись)."""

    def __init__(self, html: str) -> None:
        super().__init__()
        self.values: dict[str, str] = {}
        self.options: dict[str, list[tuple[str, str]]] = {}
        self._select: str | None = None
        self._option: str | None = None
        self.feed(html)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key: value or "" for key, value in attrs}
        if tag == "input" and "name" in attributes:
            self.values[attributes["name"]] = attributes.get("value", "")
        elif tag == "select":
            self._select = attributes["name"]
            self.options[self._select] = []
        elif tag == "option" and self._select is not None:
            self._option = attributes.get("value", "")
            if "selected" in attributes or self._select not in self.values:
                self.values[self._select] = self._option
            self.options[self._select].append((self._option, ""))

    def handle_data(self, data: str) -> None:
        if self._select is not None and self._option is not None and data.strip():
            value, label = self.options[self._select][-1]
            self.options[self._select][-1] = (value, (label + data).strip())

    def handle_endtag(self, tag: str) -> None:
        if tag == "option":
            self._option = None
        elif tag == "select":
            self._select = None


def settings_form(spook) -> Form:
    response = spook.http.get(f"{spook.base_url}/settings")
    assert response.status_code == 200
    return Form(response.text)


def test_settings_screen_shows_current_values_from_settings_file(spook):
    spook.write_settings(
        auto_stop_minutes=1.5,
        lecture={"skill": "my-lecture", "model": "sonnet", "effort": "max"},
    )

    form = settings_form(spook)

    assert form.values["meeting.folder"] == str(spook.meetings_dir)
    assert form.values["meeting.skill"] == "spook-meeting"
    assert form.values["meeting.model"] == "opus"
    assert form.values["meeting.effort"] == "high"
    assert [value for value, _ in form.options["meeting.effort"]] == ["low", "medium", "high", "xhigh", "max"]
    assert form.values["lecture.folder"] == str(spook.lectures_dir)
    assert form.values["lecture.skill"] == "my-lecture"
    assert form.values["lecture.model"] == "sonnet"
    assert form.values["lecture.effort"] == "max"
    assert form.values["auto_stop_minutes"] == "1.5"
    assert form.values["terms_dictionary"] == str(spook.meetings_dir / "Словарь терминов.md")
    assert form.values["sources.system"] == ""
    assert form.values["sources.mic"] == ""


def save(spook, **changes: str):
    """«Сохранить» на экране: форма уходит со всеми полями, как их показал экран, и с изменёнными."""
    form = settings_form(spook).values
    for key, value in changes.items():
        form[key.replace("__", ".")] = value
    return spook.http.post(f"{spook.base_url}/settings", data=form, follow_redirects=True)


def test_every_screen_links_to_settings(spook):
    for path in ("/", "/notes"):
        assert 'href="/settings"' in spook.http.get(f"{spook.base_url}{path}").text, path
    assert '<span aria-current="page">Настройки</span>' in spook.http.get(f"{spook.base_url}/settings").text


def test_settings_screen_reminds_that_skill_permissions_live_in_skill_headers(spook):
    html = spook.http.get(f"{spook.base_url}/settings").text

    assert html.count("в шапке Скилла") == 2  # у папки Встреч и у папки Лекций


def test_saving_writes_settings_file_and_keeps_keys_that_are_not_on_screen(spook, tmp_path):
    spook.write_settings(recognition={"speech_threshold": 0.4})
    with spook.settings_path.open("a", encoding="utf-8") as file:
        file.write('\n[future]\nkey = "значение"\n')
    new_meetings = tmp_path / "Встречи с \"кавычками\""
    new_terms = tmp_path / "Словарь.md"

    response = save(
        spook,
        meeting__folder=str(new_meetings),
        meeting__skill="my-meeting",
        meeting__model="sonnet",
        meeting__effort="xhigh",
        lecture__effort="low",
        auto_stop_minutes="2,5",
        terms_dictionary=str(new_terms),
    )

    assert response.status_code == 200 and response.url.path == "/settings"
    assert "Настройки сохранены" in response.text
    saved = tomllib.loads(spook.settings_path.read_text(encoding="utf-8"))
    assert saved["meeting"] == {"folder": str(new_meetings), "skill": "my-meeting", "model": "sonnet", "effort": "xhigh"}
    assert saved["lecture"]["effort"] == "low"
    assert saved["lecture"]["folder"] == str(spook.lectures_dir)
    assert saved["auto_stop_minutes"] == 2.5
    assert saved["terms_dictionary"] == str(new_terms)
    assert saved["port"] == spook.settings.port
    assert saved["programs"] == spook.programs
    assert saved["recognition"] == {"speech_threshold": 0.4}
    assert saved["future"] == {"key": "значение"}
    form = settings_form(spook)
    assert form.values["meeting.folder"] == str(new_meetings)
    assert form.values["meeting.effort"] == "xhigh"
    assert form.values["auto_stop_minutes"] == "2.5"
    assert form.values["terms_dictionary"] == str(new_terms)


def test_device_list_comes_from_audio_input_and_default_removes_pinning(spook):
    spook.audio.known_devices[Source.SYSTEM] = [Device("alsa_output.usb", "Наушники USB"), Device("alsa_output.hdmi", "HDMI")]
    spook.audio.known_devices[Source.MIC] = [Device("alsa_input.usb", "Микрофон USB")]

    form = settings_form(spook)

    assert form.options["sources.system"] == [
        ("", "по умолчанию"),
        ("alsa_output.usb", "Наушники USB"),
        ("alsa_output.hdmi", "HDMI"),
    ]
    assert form.options["sources.mic"] == [("", "по умолчанию"), ("alsa_input.usb", "Микрофон USB")]
    assert form.values["sources.system"] == ""

    save(spook, sources__system="alsa_output.hdmi", sources__mic="alsa_input.usb")

    assert tomllib.loads(spook.settings_path.read_text(encoding="utf-8"))["sources"] == {
        "system": "alsa_output.hdmi",
        "mic": "alsa_input.usb",
    }
    assert settings_form(spook).values["sources.system"] == "alsa_output.hdmi"
    spook.start_lecture()
    assert 'title="alsa_output.hdmi"' in spook.page().text
    spook.stop()

    save(spook, sources__system="")

    assert tomllib.loads(spook.settings_path.read_text(encoding="utf-8"))["sources"]["system"] == ""
    spook.start_lecture()
    assert 'title="образцы"' in spook.page().text


def test_pinned_device_that_is_absent_now_stays_selected(spook):
    spook.audio.known_devices[Source.SYSTEM] = [Device("alsa_output.usb", "Наушники USB")]
    save(spook, sources__system="alsa_output.usb")
    spook.audio.known_devices[Source.SYSTEM] = []

    form = settings_form(spook)

    assert form.values["sources.system"] == "alsa_output.usb"
    assert ("alsa_output.usb", "alsa_output.usb (сейчас нет)") in form.options["sources.system"]


def meeting_until_auto_stop(spook, minutes: float) -> None:
    """Встреча с одной Репликой; тишина длиной `minutes` после неё завершает Сессию автостопом,
    а на секунду раньше — ещё нет."""
    spook.recognizer.say("Реплика.")
    with spook.events() as events:
        assert spook.start_meeting().status_code == 200
        spook.audio.feed(speech(20))
        events.wait_for("paragraph", containing="Реплика.")
        spook.clock.advance(minutes * 60 - 1)
        assert "идёт" in spook.page().text
        spook.clock.advance(1)
        events.wait_for("state", containing="нет Сессии")
        events.wait_for("agent", containing="готово")


def agent_launches(spook) -> list[tuple[list[str], str]]:
    """Запуски Агента: Скилл с Заметкой, модель и effort (первые шесть аргументов) и рабочий каталог."""
    return [(call["argv"][:6], call["cwd"]) for call in spook.agent.calls()]


def test_saved_folder_model_and_auto_stop_act_on_next_session_and_agent_without_restart(spook, tmp_path):
    spook.agent.play("summary")
    new_folder = tmp_path / "Новые встречи"

    save(spook, meeting__folder=str(new_folder), meeting__model="haiku", meeting__effort="max", auto_stop_minutes="2")
    meeting_until_auto_stop(spook, 2)

    note = new_folder / f"{MEETING_NOTE_NAME}.md"
    assert note.is_file()
    assert not (spook.meetings_dir / f"{MEETING_NOTE_NAME}.md").exists()
    assert agent_launches(spook) == [
        (["-p", f"/spook-meeting {note}", "--model", "haiku", "--effort", "max"], str(new_folder)),
    ]


def test_saving_during_session_acts_from_next_session_and_agent_works_in_folder_of_its_note(spook, tmp_path):
    spook.agent.play("summary")
    new_folder = tmp_path / "Новые встречи"
    spook.recognizer.say("Реплика.")
    with spook.events() as events:
        spook.start_meeting()

        response = save(spook, meeting__folder=str(new_folder), meeting__model="haiku", auto_stop_minutes="10")

        assert response.status_code == 200
        spook.audio.feed(speech(20))
        events.wait_for("paragraph", containing="Реплика.")
        spook.clock.advance(5 * 60)  # автостоп — прежний, на момент «Старт»
        events.wait_for("state", containing="нет Сессии")
        events.wait_for("agent", containing="готово")

    # Заметка — в прежней папке, Агент работает в ней; модель — уже новая: это следующий запуск Агента.
    note = spook.meetings_dir / f"{MEETING_NOTE_NAME}.md"
    assert note.is_file()
    assert agent_launches(spook) == [
        (["-p", f"/spook-meeting {note}", "--model", "haiku", "--effort", "high"], str(spook.meetings_dir)),
    ]

    spook.clock.advance(60 * 60)
    spook.agent.play("summary")
    meeting_until_auto_stop(spook, 10)

    assert (new_folder / "2026-11-06 15-07 Встреча.md").is_file()


def test_agent_names_topic_of_note_left_in_folder_changed_during_session(spook, tmp_path):
    spook.agent.play("topic")
    spook.recognizer.say("Реплика.")
    with spook.events() as events:
        spook.start_lecture()
        save(spook, lecture__folder=str(tmp_path / "Новые лекции"))
        spook.audio.feed(speech(20))
        events.wait_for("paragraph", containing="Реплика.")
        spook.stop()
        events.wait_for("agent", containing="готово")

    assert sorted(path.name for path in spook.lectures_dir.iterdir()) == ["2026-11-06 14-02 Репликация в Raft.md"]


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("meeting.folder", "", "Укажите папку Заметок вида «Встреча»"),
        ("lecture.folder", "lectures", "Нужен полный путь (от «/» или «~»), а не «lectures»"),
        ("auto_stop_minutes", "пять", "Время автостопа — число минут больше нуля, а не «пять»"),
        ("auto_stop_minutes", "0", "Время автостопа — число минут больше нуля, а не «0»"),
        ("auto_stop_minutes", "-1", "Время автостопа — число минут больше нуля, а не «-1»"),
        ("lecture.effort", "turbo", "Неизвестный effort «turbo»: допустимы low, medium, high, xhigh, max"),
        ("meeting.model", "", "Укажите модель"),
        ("terms_dictionary", "", "Укажите путь к Словарю терминов"),
    ],
)
def test_invalid_value_is_rejected_with_reason_and_settings_file_stays_unchanged(spook, field, value, reason):
    before = spook.settings_path.read_bytes()
    changes = {"auto_stop_minutes": "3", field.replace(".", "__"): value}  # среди полей есть и допустимая правка

    response = save(spook, **changes)

    assert response.status_code == 422
    assert reason in response.text
    assert spook.settings_path.read_bytes() == before
    assert Form(response.text).values[field] == value  # введённое не пропадает: его можно поправить
