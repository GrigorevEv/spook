"""Страница «Сессия»: на русском, с кнопками, таймером и Живым текстом."""

from __future__ import annotations


def test_session_page_is_in_russian_with_start_button_timer_and_live_text(spook):
    page = spook.page()

    assert page.status_code == 200
    html = page.text
    assert '<html lang="ru">' in html
    assert "<title>Spook — Сессия</title>" in html
    assert ">Старт</button>" in html
    assert "нет Сессии" in html
    assert 'class="timer"' in html and ">0:00:00</time>" in html
    assert 'aria-label="Живой текст"' in html
    assert "запис" not in html.lower()


def test_page_during_session_shows_stop_button_and_lecture(spook):
    spook.start_lecture()
    spook.clock.advance(65)

    html = spook.page().text

    assert ">Стоп</button>" in html
    assert ">Старт</button>" not in html
    assert "Лекция" in html and "идёт" in html
    assert ">0:01:05</time>" in html


def test_page_loads_htmx_and_event_stream_from_own_static_files(spook):
    html = spook.page().text

    assert 'sse-connect="/events"' in html
    for path in ("/static/htmx.min.js", "/static/htmx-ext-sse.js", "/static/spook.js", "/static/spook.css"):
        assert path in html
        assert spook.http.get(f"{spook.base_url}{path}").status_code == 200
