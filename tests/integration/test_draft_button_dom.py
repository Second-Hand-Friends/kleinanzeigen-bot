# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Run the draft button script in the real browser against inline HTML."""

from collections.abc import Awaitable, Callable

import pytest

from kleinanzeigen_bot import publishing_submission
from kleinanzeigen_bot.utils.web_scraping_mixin import WebScrapingMixin

pytestmark = [pytest.mark.itest, pytest.mark.slow, pytest.mark.asyncio(loop_scope = "session")]

ShowHtml = Callable[..., Awaitable[None]]
_EDITOR_PATH = "/p-anzeige-aufgeben-schritt2.html"
# Button row as observed in the ad editor (October 2026).
_EDITOR = """<form><input id="ad-title">
<button type="button">Vorschau</button>
<button type="button"><div><span>Entwurf speichern</span></div></button>
<button type="button">Anzeige aufgeben</button></form>"""


async def _run(web:WebScrapingMixin, action:str) -> object:
    return await web.web_execute(publishing_submission._draft_button_script(action))  # type: ignore[arg-type]


async def test_clicks_only_the_draft_button(web:WebScrapingMixin, show_html:ShowHtml) -> None:
    """The draft button is found by its exact text; "Anzeige aufgeben" next to it is never clicked."""
    await show_html(_EDITOR, _EDITOR_PATH)
    assert await _run(web, "inspect") == "draft"
    assert await _run(web, "click") == "clicked"
    assert await web.web_execute("clicks") == ["Entwurf speichern"]


async def test_refuses_missing_or_duplicate_button(web:WebScrapingMixin, show_html:ShowHtml) -> None:
    """Without exactly one match nothing is clicked."""
    await show_html('<form><input id="ad-title"><button>Anzeige aufgeben</button></form>', _EDITOR_PATH)
    assert await _run(web, "click") == "matches:0"
    await show_html('<form><input id="ad-title"><button>Entwurf speichern</button><button>Entwurf speichern</button></form>', _EDITOR_PATH)
    assert await _run(web, "click") == "matches:2"
    await show_html("<button>Entwurf speichern</button>", _EDITOR_PATH)
    assert await _run(web, "click") == "no-form"
    assert await web.web_execute("clicks") == []


async def test_recognizes_confirmation_dialog(web:WebScrapingMixin, show_html:ShowHtml) -> None:
    """The confirmation dialog "Entwurf gespeichert" counts as saved."""
    await show_html(_EDITOR, _EDITOR_PATH)
    assert await _run(web, "confirmed") is False
    await show_html('<div role="dialog"><h2>Entwurf gespeichert</h2></div>', _EDITOR_PATH)
    assert await _run(web, "confirmed") is True
