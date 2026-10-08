"""Answer ``HEAD`` for every address that answers ``GET``.

HTTP says a server that answers ``GET`` should answer ``HEAD`` the same way, with the
same status and headers and no body. FastAPI routes declared with ``@router.get`` accept
``GET`` only, so every page on the site — the home page, ``/markets``, each Passport,
``/sitemap.xml`` — answered ``HEAD`` with ``405 Method Not Allowed``. Link checkers, SEO
tools and some crawlers ask with ``HEAD`` first, and read that 405 as a broken page.

Fixed once, in front of the whole application, rather than by adding ``HEAD`` to some
hundreds of route declarations, where the next route added would miss it.

The request is handed on as a ``GET``, so it passes through the same guards, the same
handler and the same response headers — including ``Content-Length`` — and only the body
is dropped on the way out. An address that does not answer ``GET`` still refuses.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send


class HeadAsGetMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "HEAD":
            await self.app(scope, receive, send)
            return

        async def send_without_body(message: Message) -> None:
            if message["type"] == "http.response.body":
                if message.get("more_body", False):
                    return
                message = {"type": "http.response.body", "body": b"", "more_body": False}
            await send(message)

        await self.app({**scope, "method": "GET"}, receive, send_without_body)
