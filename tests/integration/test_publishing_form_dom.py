# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Run the publishing form page scripts in the real browser against inline HTML."""

import json
from collections.abc import Awaitable, Callable

import pytest

from kleinanzeigen_bot import publishing_form
from kleinanzeigen_bot.utils import page_scripts
from kleinanzeigen_bot.utils.web_scraping_mixin import WebScrapingMixin

pytestmark = [pytest.mark.itest, pytest.mark.slow, pytest.mark.asyncio(loop_scope = "session")]

ShowHtml = Callable[..., Awaitable[None]]

_OPTIONS = '<li role="option" data-value="diesel">Diesel</li><li role="option" value="benzin">Benzin</li>'


async def _show_combobox(web:WebScrapingMixin, show_html:ShowHtml, body:str, menu:str) -> None:
    """Show ``body``; the button ``#fuel`` records its events and renders ``menu`` into ``#slot`` once clicked."""
    await show_html(body)
    await web.web_execute(f"""
        window.events = [];
        const button = document.getElementById('fuel');
        for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click']) {{
            button.addEventListener(type, () => events.push(type));
        }}
        button.addEventListener('click', () => setTimeout(() => {{
            document.getElementById('slot').innerHTML = {json.dumps(menu)};
        }}, 50));
        true
    """)


@pytest.mark.parametrize(("body", "menu", "value", "expected"), [
    pytest.param('<div><button id="fuel" role="combobox">Kraftstoff</button><div id="slot"></div></div>',
                 f'<ul id="fuel-menu" role="listbox">{_OPTIONS}</ul>', "diesel", "Diesel", id = "suffixed-menu-by-data-value"),
    pytest.param('<div><button id="fuel" role="combobox">Kraftstoff</button><div id="slot"></div></div>',
                 f'<ul role="listbox">{_OPTIONS}</ul>', "benzin", "Benzin", id = "sibling-listbox-by-value"),
    pytest.param('<div><button id="fuel" role="combobox" aria-controls="portal">Kraftstoff</button></div><div id="slot"></div>',
                 f'<ul id="portal" role="listbox">{_OPTIONS}</ul>', " BENZIN ", "Benzin", id = "aria-controls-by-text"),
    pytest.param('<div><button id="fuel" role="combobox" aria-owns="portal">Kraftstoff</button></div><div id="slot"></div>',
                 f'<ul id="portal" role="listbox">{_OPTIONS}</ul>', "Diesel", "Diesel", id = "aria-owns-by-text"),
    pytest.param('<div><button id="fuel" role="combobox">Kraftstoff</button></div><div id="slot"></div>',
                 f'<ul role="menu" aria-labelledby="fuel">{_OPTIONS}</ul>', "benzin", "Benzin", id = "visible-portal"),
])
async def test_button_combobox_opens_with_full_click_sequence_and_selects_option(
    web:WebScrapingMixin, show_html:ShowHtml, body:str, menu:str, value:str, expected:str,
) -> None:
    """The control opens with a native click sequence and the option is found wherever the menu renders."""
    await _show_combobox(web, show_html, body, menu)
    await publishing_form._select_button_combobox(web, "fuel", value)
    assert await web.web_execute("events") == ["pointerdown", "mousedown", "mouseup", "click"]
    assert await web.web_execute("clicks") == ["Kraftstoff", expected]


async def test_button_combobox_prefers_react_option_values(web:WebScrapingMixin, show_html:ShowHtml) -> None:
    """React props map API values to options by position, even when the visible text differs."""
    body = '<div><button id="fuel" role="combobox">Kraftstoff</button><div id="slot"></div></div>'
    await _show_combobox(web, show_html, body, '<ul id="fuel-menu"><li role="option">A</li><li role="option">B</li></ul>')
    await web.web_execute("""document.getElementById('fuel').__reactFiber$test =
        {return: {memoizedProps: {options: [{value: 'diesel'}, {value: 'benzin'}]}, return: null}}; true""")
    await publishing_form._select_button_combobox(web, "fuel", "benzin")
    assert await web.web_execute("clicks") == ["Kraftstoff", "B"]


@pytest.mark.parametrize(("body", "menu", "message"), [
    pytest.param('<div><button id="fuel" role="combobox">Kraftstoff</button><div id="slot"></div></div>',
                 f'<ul id="fuel-menu">{_OPTIONS}</ul>', "Option not found", id = "unknown-value"),
    pytest.param('<div><button id="fuel" role="combobox">Kraftstoff</button><div id="slot"></div></div>',
                 "", "No options appeared after opening", id = "no-options"),
])
async def test_button_combobox_failures_raise_timeout(
    web:WebScrapingMixin, show_html:ShowHtml, body:str, menu:str, message:str,
) -> None:
    """Every failure the script reports becomes a TimeoutError with its reason."""
    await _show_combobox(web, show_html, body, menu)
    with pytest.raises(TimeoutError, match = message):
        await publishing_form._select_button_combobox(web, "fuel", "elektro")


async def test_button_combobox_missing_button_raises_timeout(web:WebScrapingMixin, show_html:ShowHtml) -> None:
    """Without the button nothing is opened or clicked."""
    await show_html('<button id="other">x</button>')
    with pytest.raises(TimeoutError, match = "Button not found"):
        await publishing_form._select_button_combobox(web, "fuel", "elektro")
    assert await web.web_execute("clicks") == []


async def test_condition_trigger_is_marked_next_to_its_label(web:WebScrapingMixin, show_html:ShowHtml) -> None:
    """The dialog button sharing an ancestor with the condition label is marked; a stale mark is removed."""
    await show_html(
        '<button data-kab-condition-trigger="true">old</button>'
        '<div><div><label for="autos.condition">Zustand</label></div>'
        '<div><button id="cond" aria-haspopup="dialog" aria-controls="cond-dialog">Bitte wählen</button></div></div>'
    )
    result = json.loads(await web.web_execute(page_scripts.call("condition_dialog_trigger")))
    assert result == {"found": True, "id": "cond", "ariaControls": "cond-dialog"}
    assert await web.web_execute("[...document.querySelectorAll('[data-kab-condition-trigger]')].map(e => e.id)") == ["cond"]


async def test_condition_trigger_reports_missing_label(web:WebScrapingMixin, show_html:ShowHtml) -> None:
    """Without a condition label there is nothing to mark."""
    await show_html('<button aria-haspopup="dialog">Bitte wählen</button>')
    assert json.loads(await web.web_execute(page_scripts.call("condition_dialog_trigger"))) == {"found": False}
