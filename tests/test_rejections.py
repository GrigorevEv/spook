"""Команды, которые нельзя выполнить, отклоняются с понятной причиной."""

from __future__ import annotations


def test_second_start_during_session_is_rejected(spook):
    assert spook.start_lecture().status_code == 200

    response = spook.start_lecture()

    assert response.status_code == 409
    assert "Сессия уже идёт" in response.text
    assert len(list(spook.lectures_dir.iterdir())) == 1


def test_start_is_rejected_when_lectures_folder_cannot_be_created(spook):
    blocker = spook.settings_path.parent / "не папка.txt"
    blocker.write_text("файл на месте папки", encoding="utf-8")
    spook.write_settings(lectures_dir=blocker / "lectures")

    response = spook.start_lecture()

    assert response.status_code == 409
    assert "папку Лекций" in response.text
    assert str(blocker / "lectures") in response.text
    assert "нет Сессии" in spook.page().text


def test_stop_without_session_is_rejected(spook):
    response = spook.stop()

    assert response.status_code == 409
    assert "Сессии нет" in response.text


def test_unknown_session_kind_is_rejected(spook):
    response = spook.http.post(f"{spook.base_url}/session/start", data={"kind": "podcast"})

    assert response.status_code == 409
    assert "Неизвестный вид Сессии" in response.text
