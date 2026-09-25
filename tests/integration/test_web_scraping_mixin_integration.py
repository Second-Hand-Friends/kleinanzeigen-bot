# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import platform
from pathlib import Path

import pytest
from nodriver import cdp

from kleinanzeigen_bot.login_flow import has_logged_in_marker
from kleinanzeigen_bot.utils.misc import ensure
from kleinanzeigen_bot.utils.web_scraping_mixin import By, WebScrapingMixin

pytestmark = pytest.mark.slow

# Configure logging for integration tests
# The main bot already handles nodriver logging via silence_nodriver_logs fixture
# and pytest handles verbosity with -v flag automatically


@pytest.mark.flaky(reruns = 5, reruns_delay = 10)
@pytest.mark.itest
@pytest.mark.asyncio
async def test_init() -> None:
    web_scraping_mixin = WebScrapingMixin()
    if platform.system() == "Linux":
        # required for Ubuntu 24.04 or newer
        web_scraping_mixin.browser_config.arguments.append("--no-sandbox")

    browser_path = web_scraping_mixin.get_compatible_browser()
    ensure(browser_path is not None, "Browser not auto-detected")

    try:
        await web_scraping_mixin.create_browser_session()
    finally:
        await web_scraping_mixin.close_browser_session()


_LOGGED_IN_HEADER_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "astro_start_page_logged_in_header.html"


@pytest.mark.flaky(reruns = 5, reruns_delay = 10)
@pytest.mark.itest
@pytest.mark.asyncio
@pytest.mark.parametrize(("viewport_width", "marker_rendered"), [(390, False), (1024, True)])
async def test_logged_in_marker_is_detected_on_redesigned_start_page_at_any_viewport_width(
    viewport_width:int, marker_rendered:bool
) -> None:
    """The redesigned start page hides its marker header below 768px (`hidden md:block`).
    Login detection must recognise the session there as well, not only on wide windows."""
    web_scraping_mixin = WebScrapingMixin()
    if platform.system() == "Linux":
        # required for Ubuntu 24.04 or newer
        web_scraping_mixin.browser_config.arguments.append("--no-sandbox")

    try:
        await web_scraping_mixin.create_browser_session()
        await web_scraping_mixin.web_open(_LOGGED_IN_HEADER_FIXTURE.as_uri())
        await web_scraping_mixin.page.send(
            cdp.emulation.set_device_metrics_override(width = viewport_width, height = 800, device_scale_factor = 1, mobile = False)
        )

        # precondition: the emulated width really renders (or hides) the marker header
        # (first entry of login_flow._LOGIN_DETECTION_SELECTORS)
        visible_text, _ = await web_scraping_mixin.web_text_first_available([(By.CLASS_NAME, "mr-medium")], key = "quick_dom")
        assert bool(visible_text) is marker_rendered

        assert await has_logged_in_marker(web_scraping_mixin, username = "user@example.com") is True
        assert await has_logged_in_marker(web_scraping_mixin, username = "someone_else@example.com") is False
    finally:
        await web_scraping_mixin.close_browser_session()
