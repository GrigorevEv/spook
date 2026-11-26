from __future__ import annotations

import httpx
import pytest

from conftest import EventStream


def own_page(spook, name: str = "localhost") -> dict[str, str]:
    return {
        "Host": f"{name}:{spook.settings.port}",
        "Origin": f"http://{name}:{spook.settings.port}",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "HX-Request": "true",
    }


@pytest.mark.parametrize("host", ["attacker.example:{port}", "localhost:1", "127.0.0.1"])
def test_dns_rebinding_request_with_foreign_host_is_rejected(spook, host):
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
    response = spook.http.post(
        f"{spook.base_url}/session/start", data={"kind": "meeting"}, headers={"Origin": origin}
    )

    assert response.status_code == 403
    assert response.text == "Запрос с чужой страницы отклонён"
    assert "нет Сессии" in spook.page().text
    assert not spook.pages() and not list(spook.drafts_dir.glob("*.html"))


@pytest.mark.parametrize("name", ["localhost", "127.0.0.1"])
def test_command_from_own_page_starts_session(spook, name):
    response = spook.http.post(
        f"{spook.base_url}/session/start", data={"kind": "meeting"}, headers=own_page(spook, name)
    )

    assert response.status_code == 200, response.text
    assert "идёт" in spook.page().text


FOREIGN_STREAMS = {
    "eventsource": {
        "Origin": "http://attacker.example",
        "Sec-Fetch-Site": "cross-site",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    },
    "iframe": {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "iframe"},
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
    spook.start_meeting()
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
    headers = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}

    response = spook.http.get(f"{spook.base_url}/", headers=headers)

    assert response.status_code == 200
    assert "<title>Spook — Сессия</title>" in response.text


def test_embedding_page_into_another_site_is_rejected(spook):
    headers = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "iframe"}

    response = spook.http.get(f"{spook.base_url}/", headers=headers)

    assert response.status_code == 403
    assert response.text == "Запрос с чужой страницы отклонён"
