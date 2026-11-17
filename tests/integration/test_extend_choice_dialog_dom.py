# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Run the extension choice dialog script in the real browser against inline HTML."""

from collections.abc import Awaitable, Callable

import pytest

from kleinanzeigen_bot import extend_flow
from kleinanzeigen_bot.utils.web_scraping_mixin import WebScrapingMixin

pytestmark = [pytest.mark.itest, pytest.mark.slow, pytest.mark.asyncio(loop_scope = "session")]

ShowHtml = Callable[..., Awaitable[None]]

# Markup as observed on "Meine Anzeigen" (October 2026), reduced to the relevant structure.
_DIALOG = """
<div role="dialog" aria-modal="true">
  <h2>Wie möchtest du deine Anzeige verlängern?</h2>
  <label for="free"><input type="radio" id="free" name="ext" value="extend-only"{free_checked}> Nur verlängern</label>
  <label for="paid"><input type="radio" id="paid" name="ext" value="extend-bumpup"{paid_checked}>
    Verlängern &amp; Hochschieben für 2,99 €</label>
  <button type="button"{disabled}>Anzeige verlängern</button>
  <button type="button" aria-label="Schließen">x</button>
</div>
"""


def _dialog(*, free_checked:bool = False, paid_checked:bool = False, disabled:bool = False) -> str:
    return _DIALOG.format(
        free_checked = " checked" if free_checked else "",
        paid_checked = " checked" if paid_checked else "",
        disabled = " disabled" if disabled else "",
    )


async def _run(web:WebScrapingMixin, action:str) -> str:
    return str(await web.web_execute(extend_flow._choice_dialog_script(action)))


async def test_selects_and_confirms_only_the_free_option(web:WebScrapingMixin, show_html:ShowHtml) -> None:
    """The free option is selected and confirmed; the paid option stays unselected."""
    await show_html(_dialog())
    assert await _run(web, "state") == "choice"
    assert await _run(web, "select") == "selected"
    assert await web.web_execute("document.getElementById('free').checked") is True
    assert await web.web_execute("document.getElementById('paid').checked") is False
    assert await _run(web, "confirm") == "clicked"
    assert "Anzeige verlängern" in await web.web_execute("clicks")


async def test_refuses_to_confirm_when_paid_option_is_preselected(web:WebScrapingMixin, show_html:ShowHtml) -> None:
    """The confirm click rechecks the selection: a paid option selected in between blocks it."""
    await show_html(_dialog(paid_checked = True))
    # Radios in one group: clicking the free option unchecks the paid one, which is fine ...
    assert await _run(web, "select") == "selected"
    # ... but if the paid option gets selected again before the click, confirm refuses.
    await web.web_execute("document.getElementById('paid').checked = true")
    assert await _run(web, "confirm") == "selection-changed"
    assert "Anzeige verlängern" not in await web.web_execute("clicks")


async def test_refuses_disabled_confirm_button(web:WebScrapingMixin, show_html:ShowHtml) -> None:
    """A disabled confirm button is reported instead of clicked."""
    await show_html(_dialog(free_checked = True, disabled = True))
    assert await _run(web, "select") == "selected"
    assert await _run(web, "confirm") == "confirm-button-disabled"
    assert "Anzeige verlängern" not in await web.web_execute("clicks")


async def test_paid_only_dialog_is_recognized_and_rejected(web:WebScrapingMixin, show_html:ShowHtml) -> None:
    """A dialog without the free option is still a choice dialog; selection refuses it."""
    await show_html("""
<div role="dialog" aria-modal="true">
  <h2>Wie möchtest du deine Anzeige verlängern?</h2>
  <label for="paid"><input type="radio" id="paid" name="ext" value="extend-bumpup"> Verlängern &amp; Hochschieben für 2,99 €</label>
  <button type="button">Anzeige verlängern</button>
</div>
""")
    assert await _run(web, "state") == "choice"
    assert await _run(web, "select") == "free-option-not-unique:0/1"
    assert await web.web_execute("document.getElementById('paid').checked") is False


async def test_state_requires_a_positive_signal(web:WebScrapingMixin, show_html:ShowHtml) -> None:
    """Without a dialog nothing is clicked and "none" is reported; toast and legacy dialog are "success"."""
    await show_html("<p>Meine Anzeigen</p>")
    assert await _run(web, "select") == "no-dialog"
    assert await _run(web, "state") == "none"
    await show_html('<div role="status"><p>Anzeige wurde erfolgreich verlängert.</p></div>')
    assert await _run(web, "state") == "success"
    await show_html('<div role="dialog"><h2>Vielen Dank!</h2><button aria-label="Schließen">x</button></div>')
    assert await _run(web, "state") == "success"
