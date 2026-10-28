# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from nodriver import cdp

from kleinanzeigen_bot.login_flow import check_sms_verification, has_logged_in_marker
from kleinanzeigen_bot.utils.misc import ensure
from kleinanzeigen_bot.utils.web_scraping_mixin import By, WebScrapingMixin
from tests.browser_startup import start_browser_session

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
    # CSS media queries evaluate against the width the bot measures for its narrow-viewport warning
    assert await web._effective_viewport_width() == viewport_width

    # precondition: the emulated width really renders (or hides) the marker header
    # (first entry of login_flow._LOGIN_DETECTION_SELECTORS)
    visible_text, _ = await web.web_text_first_available([(By.CLASS_NAME, "mr-medium")], key = "quick_dom")
    assert bool(visible_text) is marker_rendered

    assert await has_logged_in_marker(web, username = "user@example.com") is True
    assert await has_logged_in_marker(web, username = "someone_else@example.com") is False


@pytest.mark.parametrize(("markup", "requires_verification"), [
    ("<p>Wir senden dir einen 6-stelligen Code für die Telefonnummer +49 ***.</p><button>Code senden</button>", True),
    ("<p>Wir senden dir einen <strong>6-stelligen Code</strong> für die Telefonnummer.</p><button>Code senden</button>", True),
    ("<p>Wir haben dir gerade einen 6-stelligen Code für die Telefonnummer +49 *** geschickt.</p>", True),
    ("<p>Wir haben dir gerade einen <strong>6-stelligen Code</strong> für die Telefonnummer +49 *** geschickt.</p>", True),
    ("<p>Gerät bestätigen<br>Wir senden dir einen Code.</p><button>Code senden</button>", True),
    ("<p>Wir senden dir unsere Angebote per E-Mail.</p>", False),
    ("<script>const t = 'Wir senden dir einen 6-stelligen Code';</script><p>Willkommen</p>", False),
    ("<p>Willkommen</p>", False),
])
async def test_sms_verification_prompts_at_request_and_entry_stages(web:WebScrapingMixin, markup:str, requires_verification:bool) -> None:
    """Exercise real DOM matching offline; requesting/sending the SMS stays manual."""
    await web.page.send(cdp.runtime.evaluate("document.body.innerHTML = " + repr(markup)))
    with (
        patch("kleinanzeigen_bot.login_flow.ainput", new_callable = AsyncMock) as prompt,
        patch.object(web, "timeout", return_value = 0.2),
    ):
        await check_sms_verification(web)
    assert prompt.await_count == int(requires_verification)


async def test_viewport_resize_applies_window_bounds(web:WebScrapingMixin) -> None:
    """The randomized viewport is applied through CDP window bounds; headless sessions skip it at start, so test it directly."""
    window_id, original = await web.page.get_window()
    try:
        assert await web._apply_viewport_size("1111x777") is True
        _, bounds = await web.page.get_window()
        assert (bounds.width, bounds.height) == (1111, 777)
    finally:
        await web.page.send(cdp.browser.set_window_bounds(window_id, bounds = cdp.browser.Bounds(width = original.width, height = original.height)))


async def test_closing_a_session_persists_cookies_for_the_next_start(blank_site:str, tmp_path:Path) -> None:
    """A login completed right before closing must still be in the profile when the browser starts again."""
    def new_session() -> WebScrapingMixin:
        session = WebScrapingMixin()
        session.browser_config.use_private_window = False
        session.browser_config.user_data_dir = str(tmp_path / "profile")
        session.browser_config.arguments += ["--headless=new", "--no-sandbox"]
        return session

    first = new_session()
    await start_browser_session(first)
    try:
        await first.web_open(blank_site)
        await first.page.send(cdp.network.set_cookie("session", "kept", url = blank_site, expires = cdp.network.TimeSinceEpoch(4_000_000_000)))
    finally:
        await first.close_browser_session()

    second = new_session()
    await second.create_browser_session()
    try:
        await second.web_open(blank_site)
        cookies = await second.page.send(cdp.network.get_cookies([blank_site]))
        assert {cookie.name: cookie.value for cookie in cookies} == {"session": "kept"}
    finally:
        await second.close_browser_session()
