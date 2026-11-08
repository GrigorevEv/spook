"""Служба и доступ: сервер отвечает только на запросы к своему адресу и принимает команды
и поток событий только со своей страницы. Входа и паролей нет; тест действует как браузер.
"""

from __future__ import annotations

import httpx
import pytest

from conftest import EventStream


def own_page(spook, name: str = "localhost") -> dict[str, str]:
    """Заголовки, с которыми браузер шлёт htmx-команду со страницы `http://<name>:<порт>/`."""
    return {
        "Host": f"{name}:{spook.settings.port}",
        "Origin": f"http://{name}:{spook.settings.port}",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "HX-Request": "true",
    }


@pytest.mark.parametrize("host", ["attacker.example:{port}", "localhost:1", "127.0.0.1"])
def test_request_to_foreign_address_is_rejected(spook, host):
    """Подмена DNS: чужое имя указывает на 127.0.0.1, но заголовок адреса выдаёт его."""
    response = spook.http.get(f"{spook.base_url}/", headers={"Host": host.format(port=spook.settings.port)})

    assert response.status_code == 403
    assert response.text == "Запрос к чужому адресу отклонён"


@pytest.mark.parametrize("name", ["localhost", "127.0.0.1"])
def test_request_to_own_address_is_served(spook, name):
    response = spook.http.get(f"{spook.base_url}/", headers={"Host": f"{name}:{spook.settings.port}"})

    assert response.status_code == 200
    assert "<title>Spook — Сессия</title>" in response.text


@pytest.mark.parametrize("origin", ["http://attacker.example", "null", "http://localhost:1"])
def test_command_from_foreign_page_is_rejected_and_session_does_not_start(spook, origin):
    """Чужой сайт в том же браузере шлёт форму на нашу команду: браузер выдаёт его источник."""
    response = spook.http.post(
        f"{spook.base_url}/session/start", data={"kind": "lecture"}, headers={"Origin": origin}
    )

    assert response.status_code == 403
    assert response.text == "Запрос с чужой страницы отклонён"
    assert "нет Сессии" in spook.page().text
    assert not spook.lectures_dir.exists()


@pytest.mark.parametrize("name", ["localhost", "127.0.0.1"])
def test_command_from_own_page_starts_session(spook, name):
    response = spook.http.post(
        f"{spook.base_url}/session/start", data={"kind": "lecture"}, headers=own_page(spook, name)
    )

    assert response.status_code == 200, response.text
    assert "идёт" in spook.page().text


FOREIGN_STREAMS = {
    # new EventSource("http://localhost:<порт>/events") со страницы чужого сайта
    "eventsource": {
        "Origin": "http://attacker.example",
        "Sec-Fetch-Site": "cross-site",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    },
    # <iframe src="http://localhost:<порт>/events"> на чужом сайте: Origin браузер не шлёт
    "iframe": {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "iframe"},
    # страница другой службы на этой же машине, http://localhost:3000
    "other-local-port": {
        "Origin": "http://localhost:3000",
        "Sec-Fetch-Site": "same-site",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    },
}


@pytest.mark.parametrize("headers", FOREIGN_STREAMS.values(), ids=FOREIGN_STREAMS.keys())
def test_event_stream_from_foreign_page_is_rejected(spook, headers):
    with spook.http.stream("GET", f"{spook.base_url}/events", headers=headers) as response:
        assert response.status_code == 403
        assert response.read().decode() == "Запрос с чужой страницы отклонён"


def test_event_stream_from_own_page_opens_with_current_state(spook):
    """EventSource со своей страницы: браузер шлёт Sec-Fetch-Site: same-origin, а Origin — нет."""
    spook.start_lecture()
    headers = {
        "Host": f"localhost:{spook.settings.port}",
        "Accept": "text/event-stream",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    }

    with httpx.Client(timeout=5.0, headers=headers) as page:
        with EventStream(page, f"{spook.base_url}/events", timeout=5.0) as events:
            state = events.next_event()

    assert state.name == "state" and "идёт" in state.data


def test_opening_page_by_link_from_another_site_is_allowed(spook):
    """Ссылка на http://localhost:<порт>/ в чужом письме или чате ведёт на нашу страницу как обычно."""
    headers = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}

    response = spook.http.get(f"{spook.base_url}/", headers=headers)

    assert response.status_code == 200
    assert "<title>Spook — Сессия</title>" in response.text


def test_embedding_page_into_another_site_is_rejected(spook):
    """<iframe src="http://localhost:<порт>/"> на чужом сайте: нашу страницу нельзя подложить под чужие кнопки."""
    headers = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "iframe"}

    response = spook.http.get(f"{spook.base_url}/", headers=headers)

    assert response.status_code == 403
    assert response.text == "Запрос с чужой страницы отклонён"
