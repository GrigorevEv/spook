"""Защита интерфейса без входа и паролей: сервер отвечает только на запросы к своему адресу
и принимает запросы только со своей страницы.

Промежуточное звено ASGI перед приложением; чужой запрос получает 403 с короткой причиной.

- Заголовок `Host` должен быть `localhost:<порт>` или `127.0.0.1:<порт>` — иначе это подмена DNS
  (чужое имя, указывающее на 127.0.0.1).
- Заголовок `Origin`, если браузер его прислал, должен называть свою страницу: `http://<свой Host>`.
  Чужой сайт, открытый в том же браузере, не может его подделать.
- Заголовок `Sec-Fetch-Site`, если браузер его прислал, должен быть `same-origin` (своя страница)
  или `none` (адрес набрал пользователь: закладка, значок). Чужому сайту дозволено одно — открыть
  нашу страницу по ссылке (`Sec-Fetch-Mode: navigate`, `Sec-Fetch-Dest: document`); команды,
  поток событий и встраивание в чужую страницу отклоняются.

Запрос без этих заголовков считается своим: так ходят программы с этой машины и сама страница
при обычной навигации.
"""

from __future__ import annotations

import logging

from starlette.datastructures import Headers
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

OWN_NAMES = frozenset({"localhost", "127.0.0.1"})
OWN_SITES = frozenset({"same-origin", "none"})
SAFE_METHODS = frozenset({"GET", "HEAD"})

FOREIGN_ADDRESS = "Запрос к чужому адресу отклонён"
FOREIGN_PAGE = "Запрос с чужой страницы отклонён"

logger = logging.getLogger(__name__)


def own_hosts(port: int) -> frozenset[str]:
    """Допустимые значения заголовка `Host`; без порта — только когда порт стандартный."""
    hosts = {f"{name}:{port}" for name in OWN_NAMES}
    if port == 80:
        hosts |= OWN_NAMES
    return frozenset(hosts)


def own_origins(port: int) -> frozenset[str]:
    return frozenset(f"http://{host}" for host in own_hosts(port))


def refusal(method: str, headers: Headers, port: int) -> str | None:
    """Причина отказа или None, если запрос свой."""
    if headers.get("host", "").lower() not in own_hosts(port):
        return FOREIGN_ADDRESS
    origin = headers.get("origin")
    if origin is not None and origin.lower() not in own_origins(port):
        return FOREIGN_PAGE
    site = headers.get("sec-fetch-site")
    if site is not None and site not in OWN_SITES and not _opens_page_by_link(method, headers):
        return FOREIGN_PAGE
    return None


def _opens_page_by_link(method: str, headers: Headers) -> bool:
    return (
        method in SAFE_METHODS
        and headers.get("sec-fetch-mode") == "navigate"
        and headers.get("sec-fetch-dest") == "document"
    )


class AccessGuard:
    """Оборачивает приложение: чужие запросы получают 403 и до обработчиков не доходят."""

    def __init__(self, app: ASGIApp, port: int) -> None:
        self.app = app
        self.port = port

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        reason = refusal(scope["method"], headers, self.port)
        if reason is None:
            await self.app(scope, receive, send)
            return
        logger.warning(
            "%s: %s %s, Host=%r, Origin=%r, Sec-Fetch-Site=%r",
            reason,
            scope["method"],
            scope["path"],
            headers.get("host"),
            headers.get("origin"),
            headers.get("sec-fetch-site"),
        )
        await PlainTextResponse(reason, status_code=403)(scope, receive, send)
