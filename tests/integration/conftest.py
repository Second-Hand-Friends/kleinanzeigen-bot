# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""One real browser for all browser-based integration tests.

Starting Chromium is the slowest and flakiest part of these tests, and each start opens a window.
The tests only load offline HTML and emulate viewport widths via CDP, so they share one headless
session and reset the per-test state instead.
"""

import json
import os
import platform
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
import pytest_asyncio
from nodriver import cdp

from kleinanzeigen_bot.utils.web_scraping_mixin import WebScrapingMixin
from tests.browser_startup import start_browser_session


@pytest_asyncio.fixture(scope = "session", loop_scope = "session")
async def browser_session() -> AsyncIterator[WebScrapingMixin]:
    """Start the browser with the bot's default launch configuration once per test session."""
    web = WebScrapingMixin()
    # No window by default: tests switch pages in quick succession, and a flickering window is
    # disruptive. Set KLEINANZEIGEN_BOT_ITEST_HEADED=1 to watch the browser while debugging.
    if os.environ.get("KLEINANZEIGEN_BOT_ITEST_HEADED") != "1":
        web.browser_config.arguments.append("--headless=new")
    if platform.system() == "Linux":
        # required for Ubuntu 24.04 or newer
        web.browser_config.arguments.append("--no-sandbox")

    await start_browser_session(web)
    try:
        yield web
    finally:
        await web.close_browser_session()


@pytest_asyncio.fixture(loop_scope = "session")
async def web(browser_session:WebScrapingMixin) -> WebScrapingMixin:
    """Hand each test the shared session on a blank page with the default viewport."""
    # reload: a previous test may have written its own document into about:blank
    # (also creates the tab on first use)
    await browser_session.web_open("about:blank", reload_if_already_open = True)
    await browser_session.page.send(cdp.emulation.clear_device_metrics_override())
    return browser_session


class _BlankPageHandler(BaseHTTPRequestHandler):
    """Serve an empty page under every path, so page scripts see a real ``location``.

    ``/echo`` answers with the request's method and headers as JSON, ``/missing`` with 404.
    """

    def do_GET(self) -> None:  # noqa: N802 - HTTPServer handler contract
        """Serve the empty page, the echo or the 404."""
        if self.path == "/echo":
            self._send(200, "application/json", json.dumps({"method": self.command, "headers": dict(self.headers)}).encode())
        elif self.path == "/missing":
            self._send(404, "text/plain", b"missing")
        else:
            self._send(200, "text/html; charset=utf-8", b'<!doctype html><html lang="de"><meta charset="utf-8"><body></body></html>')

    do_POST = do_GET  # noqa: N815 - HTTPServer handler contract

    def _send(self, status:int, content_type:str, body:bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format:str, *args:object) -> None:  # noqa: A002 - HTTPServer handler contract
        """Avoid noisy HTTP request logging."""


@pytest.fixture(scope = "session")
def blank_site() -> Iterator[str]:
    """Bind an ephemeral loopback port; never access the public listing service."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _BlankPageHandler)
    thread = Thread(target = server.serve_forever, daemon = True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout = 5)


@pytest.fixture
def show_html(web:WebScrapingMixin, blank_site:str) -> Callable[..., Awaitable[None]]:
    """Return ``show_html(body, path = "/")``, which opens ``path`` with ``body`` as page content.

    Clicked elements are recorded in ``window.clicks`` by their ``innerText``; form submits are
    prevented, because a real submit would navigate away and lose the recording.
    """
    async def show(body:str, path:str = "/") -> None:
        await web.web_open(blank_site + path, reload_if_already_open = True)
        await web.web_execute(
            f"document.body.innerHTML = {json.dumps(body)}; window.clicks = [];"
            " document.addEventListener('click', event => clicks.push(event.target.closest('button, a, [role]')?.innerText"
            " ?? event.target.innerText), true);"
            " document.addEventListener('submit', event => event.preventDefault(), true); true"
        )
    return show
