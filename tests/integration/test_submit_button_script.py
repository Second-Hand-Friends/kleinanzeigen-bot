# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Run the submit button page script in the real browser against inline HTML."""

import json
from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nodriver.core.connection import ProtocolException

from kleinanzeigen_bot import publishing_submission
from kleinanzeigen_bot.utils.exceptions import PublishSubmissionUncertainError
from kleinanzeigen_bot.utils.web_scraping_mixin import WebScrapingMixin

pytestmark = [pytest.mark.itest, pytest.mark.slow, pytest.mark.asyncio(loop_scope = "session")]

ShowHtml = Callable[..., Awaitable[None]]


async def _submit(web:WebScrapingMixin, show_html:ShowHtml, body:str) -> list[str]:
    """Click the submit button found by the page script and return the clicked labels."""
    await show_html(body, "/p-anzeige-aufgeben-schritt2.html")
    await web.web_execute(publishing_submission._submit_button_script("submit"))
    return list(await web.web_execute("clicks"))


@pytest.mark.parametrize("label", ["Anzeige aufgeben", "Änderungen speichern", "Anzeige speichern"])
@pytest.mark.parametrize("outcome", ["success", "timeout", "protocol", "response-lost", "sleep-timeout"])
async def test_legacy_publish_and_update_click_once_outside_polling(
    web:WebScrapingMixin, show_html:ShowHtml, label:str, outcome:str,
) -> None:
    """Keep the final publish or update click outside polling and avoid retries after uncertainty."""
    await show_html("", "/p-anzeige-aufgeben-schritt2.html")
    real_execute = web.web_execute
    discovery_attempts = 0
    polling = False

    async def execute(script:str) -> Any:
        """Show the button on the second discovery and lose the response after the final click."""
        nonlocal discovery_attempts
        if script == publishing_submission._submit_button_script("inspect"):
            discovery_attempts += 1
            if discovery_attempts == 2:
                await real_execute(f"document.body.innerHTML = {json.dumps(f'<button>{label}</button>')}")
        else:
            assert not polling, "The final click must not be inside retry polling"
        result = await real_execute(script)
        if script == publishing_submission._submit_button_script("submit"):
            if outcome == "timeout":
                raise TimeoutError("response lost after final click")
            if outcome == "protocol":
                raise ProtocolException(MagicMock(), "response lost after final click", 0)
            if outcome == "response-lost":
                return None
        return result

    async def await_condition(condition:Any, **_:Any) -> Any:
        """Poll only discovery callbacks while exposing whether a click occurs during polling."""
        nonlocal polling
        polling = True
        try:
            for _attempt in range(3):
                result = await condition()
                if result:
                    return result
        finally:
            polling = False
        raise TimeoutError("No submit button")

    with (
        patch.object(web, "web_execute", side_effect = execute),
        patch.object(web, "web_await", side_effect = await_condition),
        patch.object(web, "web_sleep", new_callable = AsyncMock,
                     side_effect = TimeoutError("navigation lost after click") if outcome == "sleep-timeout" else None),
    ):
        if outcome == "success":
            await publishing_submission._click_submit_button(web)
        else:
            with pytest.raises(PublishSubmissionUncertainError):
                await publishing_submission._click_submit_button(web)
    assert discovery_attempts == 2
    assert await real_execute("clicks") == [label]


async def test_submit_label_priority_beats_dom_order(web:WebScrapingMixin, show_html:ShowHtml) -> None:
    """A secondary save button earlier in the DOM must not be clicked instead of the real submit."""
    body = "<button>Anzeige speichern</button><button>Anzeige aufgeben</button>"
    assert await _submit(web, show_html, body) == ["Anzeige aufgeben"]


async def test_submit_search_stays_inside_the_editor_form(web:WebScrapingMixin, show_html:ShowHtml) -> None:
    """A higher-priority label outside the editor form (e.g. a header) must not be clicked."""
    body = '<header><button>Anzeige aufgeben</button></header><form><input id="ad-title"><button>Anzeige speichern</button></form>'
    assert await _submit(web, show_html, body) == ["Anzeige speichern"]


@pytest.mark.parametrize("body", [
    "<fieldset disabled><button>Anzeige aufgeben</button></fieldset>",
    '<button style="display: none">Anzeige aufgeben</button>',
    '<div style="visibility: hidden"><button>Anzeige aufgeben</button></div>',
])
async def test_unusable_button_is_not_clicked(web:WebScrapingMixin, show_html:ShowHtml, body:str) -> None:
    """Disabled or invisible submit buttons must not count as submitted."""
    assert await _submit(web, show_html, body) == []
    assert await web.web_execute(publishing_submission._submit_button_script("submit")) is False


@pytest.mark.parametrize(("body", "expected"), [
    ("<button>Nächster Schritt</button>", "next"),
    ("<button>Anzeige aufgeben</button>", "submit"),
    ("", False),
])
async def test_inspect_reports_the_available_action(web:WebScrapingMixin, show_html:ShowHtml, body:str, expected:object) -> None:
    """Inspection names the available action without clicking anything."""
    await show_html(body, "/p-anzeige-aufgeben-schritt2.html")
    assert await web.web_execute(publishing_submission._submit_button_script("inspect")) == expected
    assert await web.web_execute("clicks") == []


async def test_package_page_is_reported_before_any_button_lookup(web:WebScrapingMixin, show_html:ShowHtml) -> None:
    """The package selection page is detected by its path, so its submit button is never clicked here."""
    await show_html("<button>Anzeige aufgeben</button>", "/p-anzeigentypauswahl/216/draft/test")
    assert await web.web_execute(publishing_submission._submit_button_script("submit")) == "package"
    assert await web.web_execute("clicks") == []
