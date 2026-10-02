# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import logging
from pathlib import Path
from unittest.mock import patch

import pytest
from nodriver import cdp

from kleinanzeigen_bot.login_flow import has_logged_in_marker
from kleinanzeigen_bot.utils.misc import ensure
from kleinanzeigen_bot.utils.web_scraping_mixin import MIN_VIEWPORT_WIDTH, By, WebScrapingMixin

# the browser is shared across the session, see conftest.py
pytestmark = [pytest.mark.slow, pytest.mark.itest, pytest.mark.asyncio(loop_scope = "session")]

# Configure logging for integration tests
# The main bot already handles nodriver logging via silence_nodriver_logs fixture
# and pytest handles verbosity with -v flag automatically


async def test_init(web:WebScrapingMixin) -> None:
    """The browser is auto-detected and a session starts with the default launch configuration."""
    ensure(web.get_compatible_browser() is not None, "Browser not auto-detected")
    assert web.browser is not None
    assert web.page is not None


_LOGGED_IN_HEADER_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "astro_start_page_logged_in_header.html"


@pytest.mark.parametrize(("viewport_width", "marker_rendered"), [(390, False), (1024, True)])
async def test_logged_in_marker_is_detected_on_redesigned_start_page_at_any_viewport_width(
    web:WebScrapingMixin, viewport_width:int, marker_rendered:bool
) -> None:
    """The redesigned start page hides its marker header below 768px (`hidden md:block`).
    Login detection must recognise the session there as well, not only on wide windows."""
    await web.web_open(_LOGGED_IN_HEADER_FIXTURE.as_uri())
    await web.page.send(
        cdp.emulation.set_device_metrics_override(width = viewport_width, height = 800, device_scale_factor = 1, mobile = False)
    )

    # precondition: the emulated width really renders (or hides) the marker header
    # (first entry of login_flow._LOGIN_DETECTION_SELECTORS)
    visible_text, _ = await web.web_text_first_available([(By.CLASS_NAME, "mr-medium")], key = "quick_dom")
    assert bool(visible_text) is marker_rendered

    assert await has_logged_in_marker(web, username = "user@example.com") is True
    assert await has_logged_in_marker(web, username = "someone_else@example.com") is False


@pytest.mark.parametrize("viewport_width", [360, 520, MIN_VIEWPORT_WIDTH - 1, MIN_VIEWPORT_WIDTH, 1024, 1366])
async def test_narrow_viewport_warning_matches_mobile_layout_threshold(
    web:WebScrapingMixin, viewport_width:int, caplog:pytest.LogCaptureFixture
) -> None:
    """`MIN_VIEWPORT_WIDTH` must be exactly the width at which the marker header stops rendering.

    The fixture carries the live header's `hidden md:block` rule, so this pins the constant to
    the real markup: the warning has to fire precisely for those widths where the header - and
    with it the logged-in marker - is not rendered.
    """
    expect_mobile_layout = viewport_width < MIN_VIEWPORT_WIDTH

    await web.web_open(_LOGGED_IN_HEADER_FIXTURE.as_uri())
    await web.page.send(
        cdp.emulation.set_device_metrics_override(width = viewport_width, height = 800, device_scale_factor = 1, mobile = False)
    )

    assert await web._effective_viewport_width() == viewport_width

    # the header is only rendered from MIN_VIEWPORT_WIDTH upwards
    marker_text, _ = await web.web_text_first_available([(By.CLASS_NAME, "mr-medium")], key = "quick_dom")
    assert bool(marker_text) is not expect_mobile_layout

    # the fixture is not served from kleinanzeigen.de, so the host check is bypassed here
    with (
        patch.object(web, "_is_kleinanzeigen_page", return_value = True),
        caplog.at_level(logging.WARNING),
    ):
        await web._warn_if_viewport_too_narrow()

    assert ("mobile layout" in caplog.text) is expect_mobile_layout
    assert web._viewport_width_warning_emitted is expect_mobile_layout
