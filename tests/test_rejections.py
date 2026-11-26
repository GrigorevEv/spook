from __future__ import annotations


def test_second_start_during_session_is_rejected(spook):
    assert spook.start_meeting().status_code == 200

    response = spook.start_meeting()

    assert response.status_code == 409
    assert "Сессия уже идёт" in response.text
    assert len(list(spook.meetings_dir.iterdir())) == 1


def test_start_is_rejected_when_meetings_folder_cannot_be_created(spook):
    blocker = spook.settings_path.parent / "не папка.txt"
    blocker.write_text("файл на месте папки", encoding="utf-8")
    spook.write_settings(meetings_dir=blocker / "meetings")

    response = spook.start_meeting()

    assert response.status_code == 409
    assert "папку Встреч" in response.text
    assert str(blocker / "meetings") in response.text
    assert "нет Сессии" in spook.page().text


def test_stop_without_session_is_rejected(spook):
    response = spook.stop()

    assert response.status_code == 409
    assert "Сессии нет" in response.text


def test_unknown_session_kind_is_rejected(spook):
    response = spook.http.post(f"{spook.base_url}/session/start", data={"kind": "podcast"})

    assert response.status_code == 409
    assert "Неизвестный вид Сессии" in response.text
