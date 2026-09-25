# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import logging
import platform
from pathlib import Path
from unittest.mock import patch

import pytest
from nodriver import cdp

from kleinanzeigen_bot.login_flow import has_logged_in_marker
from kleinanzeigen_bot.utils.misc import ensure
from kleinanzeigen_bot.utils.web_scraping_mixin import MIN_VIEWPORT_WIDTH, By, WebScrapingMixin

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


@pytest.mark.flaky(reruns = 5, reruns_delay = 10)
@pytest.mark.itest
@pytest.mark.asyncio
@pytest.mark.parametrize("viewport_width", [360, 520, MIN_VIEWPORT_WIDTH - 1, MIN_VIEWPORT_WIDTH, 1024, 1366])
async def test_narrow_viewport_warning_matches_mobile_layout_threshold(viewport_width:int, caplog:pytest.LogCaptureFixture) -> None:
    """`MIN_VIEWPORT_WIDTH` must be exactly the width at which the marker header stops rendering.

    The fixture carries the live header's `hidden md:block` rule, so this pins the constant to
    the real markup: the warning has to fire precisely for those widths where the header - and
    with it the logged-in marker - is not rendered.
    """
    web_scraping_mixin = WebScrapingMixin()
    if platform.system() == "Linux":
        # required for Ubuntu 24.04 or newer
        web_scraping_mixin.browser_config.arguments.append("--no-sandbox")

    expect_mobile_layout = viewport_width < MIN_VIEWPORT_WIDTH

    try:
        await web_scraping_mixin.create_browser_session()
        await web_scraping_mixin.web_open(_LOGGED_IN_HEADER_FIXTURE.as_uri())
        await web_scraping_mixin.page.send(
            cdp.emulation.set_device_metrics_override(width = viewport_width, height = 800, device_scale_factor = 1, mobile = False)
        )

        assert await web_scraping_mixin._effective_viewport_width() == viewport_width

        # the header is only rendered from MIN_VIEWPORT_WIDTH upwards
        marker_text, _ = await web_scraping_mixin.web_text_first_available([(By.CLASS_NAME, "mr-medium")], key = "quick_dom")
        assert bool(marker_text) is not expect_mobile_layout

        # the fixture is not served from kleinanzeigen.de, so the host check is bypassed here
        with (
            patch.object(web_scraping_mixin, "_is_kleinanzeigen_page", return_value = True),
            caplog.at_level(logging.WARNING),
        ):
            await web_scraping_mixin._warn_if_viewport_too_narrow()

        assert ("mobile layout" in caplog.text) is expect_mobile_layout
        assert web_scraping_mixin._viewport_width_warning_emitted is expect_mobile_layout
    finally:
        await web_scraping_mixin.close_browser_session()
