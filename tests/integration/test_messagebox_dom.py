# SPDX-FileCopyrightText: © Ollornog and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Run the messagebox page script in the real browser against the local echo server."""

import json
from collections.abc import Awaitable, Callable

import pytest

from kleinanzeigen_bot.utils import page_scripts
from kleinanzeigen_bot.utils.web_scraping_mixin import WebScrapingMixin

pytestmark = [pytest.mark.itest, pytest.mark.slow, pytest.mark.asyncio(loop_scope = "session")]

ShowHtml = Callable[..., Awaitable[None]]


@pytest.mark.parametrize(("method", "body", "content_type"), [
    pytest.param("GET", None, None, id = "get-without-body"),
    pytest.param("POST", json.dumps({"message": "Hallo"}), "application/json", id = "post-with-json-body"),
])
async def test_messagebox_request_sends_token_and_messagebox_headers(
    web:WebScrapingMixin, show_html:ShowHtml, blank_site:str, method:str, body:str | None, content_type:str | None,
) -> None:
    """The gateway call carries the access token and the web app's messagebox headers."""
    await show_html("<p>inbox</p>")
    response = await web.web_execute(page_scripts.call("messagebox_request", f"{blank_site}/echo", method, "Bearer test-token", body))

    assert response["statusCode"] == 200
    echo = json.loads(response["content"])
    headers = {key.lower(): value for key, value in echo["headers"].items()}
    assert echo["method"] == method
    assert headers["authorization"] == "Bearer test-token"
    assert headers["x-ecg-user-agent"] == "messagebox-1"
    assert headers["accept"] == "application/json"
    assert headers.get("content-type") == content_type


async def test_messagebox_request_returns_error_status(web:WebScrapingMixin, show_html:ShowHtml, blank_site:str) -> None:
    """Error statuses are returned to Python instead of being thrown in the page."""
    await show_html("<p>inbox</p>")
    response = await web.web_execute(page_scripts.call("messagebox_request", f"{blank_site}/missing", "GET", "t", None))

    assert response["statusCode"] == 404
    assert response["content"] == "missing"


async def test_messagebox_request_ignores_a_page_binding_that_shadows_fetch(web:WebScrapingMixin, show_html:ShowHtml, blank_site:str) -> None:
    """The script calls globalThis.fetch, so a page-level `fetch` binding cannot intercept the request."""
    await show_html("<p>inbox</p>")
    await web.web_execute(
        "document.head.appendChild(Object.assign(document.createElement('script'), {textContent: 'let fetch = 1;'})); true")
    assert await web.web_execute("fetch") == 1

    response = await web.web_execute(page_scripts.call("messagebox_request", f"{blank_site}/echo", "GET", "t", None))

    assert response["statusCode"] == 200
