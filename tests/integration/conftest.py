# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""One real browser for all browser-based integration tests.

Starting Chromium is the slowest and flakiest part of these tests, and each start opens a window.
The tests only load offline HTML and emulate viewport widths via CDP, so they share one session
and reset the per-test state instead.
"""

import asyncio
import contextlib
import platform
from collections.abc import AsyncIterator

import pytest_asyncio
from nodriver import cdp

from kleinanzeigen_bot.utils.web_scraping_mixin import WebScrapingMixin

_BROWSER_START_ATTEMPTS = 5
_BROWSER_START_RETRY_DELAY = 10  # seconds, as the former per-test reruns


@pytest_asyncio.fixture(scope = "session", loop_scope = "session")
async def browser_session() -> AsyncIterator[WebScrapingMixin]:
    """Start the browser with the bot's default launch configuration once per test session."""
    web = WebScrapingMixin()
    if platform.system() == "Linux":
        # required for Ubuntu 24.04 or newer
        web.browser_config.arguments.append("--no-sandbox")

    for attempt in range(1, _BROWSER_START_ATTEMPTS + 1):
        try:
            await web.create_browser_session()
            break
        except Exception as ex:  # noqa: BLE001 - nodriver raises a bare Exception
            if "Failed to connect to browser" not in str(ex) or attempt == _BROWSER_START_ATTEMPTS:
                raise
            # best effort: a failing cleanup must neither hide the connection error nor stop the retries
            with contextlib.suppress(Exception):
                await web.close_browser_session()
            await asyncio.sleep(_BROWSER_START_RETRY_DELAY)
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
    browser_session._viewport_width_warning_emitted = False
    return browser_session
