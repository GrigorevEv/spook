from __future__ import annotations


def test_start_of_unknown_kind_is_rejected_and_session_does_not_start(spook):
    response = spook.http.post(f"{spook.base_url}/session/start", data={"kind": "podcast"})

    assert response.status_code == 409
    assert ">Неизвестный вид Сессии<" in response.text
    page = spook.page().text
    assert "нет Сессии" in page and ">Старт</button>" in page
    assert spook.audio.active_sources == set()
    assert not spook.lectures_dir.exists() and not spook.meetings_dir.exists()
    assert not (spook.state_dir / "session.json").exists()


def test_pause_and_resume_out_of_place_give_reasons_by_state_and_paused_session_stays_paused(spook):
    pause_without_session = spook.pause()
    spook.start_lecture()
    resume_while_running = spook.resume()
    spook.pause()
    pause_while_paused = spook.pause()

    assert pause_without_session.status_code == 409
    assert ">Сессии нет<" in pause_without_session.text
    assert resume_while_running.status_code == 409
    assert ">Сессия не на Паузе: идёт<" in resume_while_running.text
    assert pause_while_paused.status_code == 409
    assert ">Сессия уже на Паузе<" in pause_while_paused.text
    page = spook.page().text
    assert "на Паузе" in page and ">Продолжить</button>" in page
