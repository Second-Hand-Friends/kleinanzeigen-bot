# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Run the WebScrapingMixin page scripts in the real browser against inline HTML."""

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from nodriver import cdp

from kleinanzeigen_bot.utils.web_scraping_mixin import By, Is, WebScrapingMixin

pytestmark = [pytest.mark.itest, pytest.mark.slow, pytest.mark.asyncio(loop_scope = "session")]

ShowHtml = Callable[..., Awaitable[None]]


@pytest_asyncio.fixture(loop_scope = "session")
async def fast_web(web:WebScrapingMixin) -> AsyncIterator[WebScrapingMixin]:
    """The shared session without the humanizing pauses between actions."""
    with patch.object(web, "web_sleep", new_callable = AsyncMock):
        yield web


async def test_visible_text_skips_hidden_content(fast_web:WebScrapingMixin, show_html:ShowHtml) -> None:
    """Visible text is what a user could select, so hidden parts are left out."""
    await show_html('<p id="text">Hallo <span style="display: none">versteckt</span>Welt</p>')
    element = await fast_web.web_find(By.ID, "text")
    assert await fast_web.extract_visible_text(element) == "Hallo Welt"


@pytest.mark.parametrize(("body", "attr", "expected"), [
    pytest.param('<button id="x">Sichtbar</button>', Is.DISPLAYED, True, id = "displayed"),
    pytest.param('<button id="x" style="visibility: hidden">x</button>', Is.DISPLAYED, False, id = "visibility-hidden"),
    pytest.param('<button id="x" style="opacity: 0">x</button>', Is.DISPLAYED, False, id = "transparent"),
    pytest.param('<span id="x"></span>', Is.DISPLAYED, False, id = "empty-size"),
    pytest.param('<input id="x" type="checkbox" checked>', Is.SELECTED, True, id = "checked-checkbox"),
    pytest.param('<input id="x" type="radio">', Is.SELECTED, False, id = "unchecked-radio"),
    pytest.param('<input id="x" type="text" value="on">', Is.SELECTED, False, id = "text-input"),
])
async def test_web_check_reads_display_and_selection_state(
    fast_web:WebScrapingMixin, show_html:ShowHtml, body:str, attr:Is, expected:bool,
) -> None:
    """Display and selection checks reflect the rendered element."""
    await show_html(body)
    assert await fast_web.web_check(By.ID, "x", attr) is expected


async def test_page_layout_summary_reports_layout_hints(fast_web:WebScrapingMixin, show_html:ShowHtml) -> None:
    """The troubleshooting summary counts islands and lists category links."""
    await show_html('<astro-island></astro-island><span data-testid="logged-in-user"></span>'
                    '<a id="cat_1">A</a><a id="cat_2">B</a><div id="ad-category-picker"></div>')
    await fast_web.page.send(cdp.emulation.set_device_metrics_override(width = 765, height = 600, device_scale_factor = 1, mobile = False))
    summary = await fast_web.page_layout_summary()
    assert summary == {"url": fast_web.page.url, "astro_islands": 1, "logged_in_marker": True, "category_ids": ["cat_1", "cat_2"],
                       "category_picker": True, "inner_width": 765, "ready_state": "complete"}


@pytest.mark.parametrize("tag", ["input", "textarea"])
async def test_set_input_value_uses_native_setter_and_dispatches_events(
    fast_web:WebScrapingMixin, show_html:ShowHtml, tag:str,
) -> None:
    """The value is set through the native setter and announced with input and change events."""
    await show_html(f'<{tag} id="field"></{tag}>')
    await fast_web.web_execute("""window.events = [];
        const field = document.getElementById('field');
        for (const type of ['input', 'change']) field.addEventListener(type, event => events.push(type + ':' + event.bubbles));
        // A framework-like instance setter must be bypassed.
        Object.defineProperty(field, 'value', {set() { events.push('instance-setter'); }, get() { return 'stale'; }});
        true""")
    await fast_web.web_set_input_value("field", 'Er sagte "Hallo"')
    assert await fast_web.web_execute("events") == ["input:true", "change:true"]
    native = "Object.getOwnPropertyDescriptor(Object.getPrototypeOf(document.getElementById('field')), 'value').get"
    assert await fast_web.web_execute(f"{native}.call(document.getElementById('field'))") == 'Er sagte "Hallo"'


async def test_web_request_sends_method_and_headers(fast_web:WebScrapingMixin, show_html:ShowHtml, blank_site:str) -> None:
    """Method and headers reach the server unchanged, quotes included, as the delete flow's CSRF header must."""
    await show_html("")
    response = await fast_web.web_request(f"{blank_site}/echo", method = "post", headers = {"x-csrf-token": 'to"k\'en'})
    assert response["statusCode"] == 200
    assert response["statusMessage"] == "OK"
    assert response["headers"]["content-type"] == "application/json"
    echo = json.loads(response["content"])
    assert echo["method"] == "POST"
    assert echo["headers"]["x-csrf-token"] == 'to"k\'en'


async def test_web_request_rejects_unexpected_status(fast_web:WebScrapingMixin, show_html:ShowHtml, blank_site:str) -> None:
    """A status outside the valid codes is an error."""
    await show_html("")
    with pytest.raises(AssertionError, match = "404"):
        await fast_web.web_request(f"{blank_site}/missing")


@pytest.mark.parametrize(("selected_value", "expected"), [("b", "b"), ("Option C", "c"), (2, "2")])
async def test_web_select_matches_value_then_text(
    fast_web:WebScrapingMixin, show_html:ShowHtml, selected_value:object, expected:str,
) -> None:
    """Options are matched by value first, then by displayed text, and the change is announced."""
    await show_html('<select id="s"><option value="a">A</option><option value="b">B</option>'
                    '<option value="c"> Option C </option><option value="2">Zwei</option></select>')
    await fast_web.web_execute("window.changes = 0; document.getElementById('s').onchange = () => changes++; true")
    await fast_web.web_select(By.ID, "s", selected_value)
    assert await fast_web.web_execute("document.getElementById('s').value") == expected
    assert await fast_web.web_execute("changes") == 1


@pytest.mark.xfail(strict = True, reason = "existing bug #1351: Element.apply returns the script's exception instead of raising it, "
                   "so web_select never reports a missing option")
async def test_web_select_unknown_option_raises_timeout(fast_web:WebScrapingMixin, show_html:ShowHtml) -> None:
    """An option that matches neither value nor text is a TimeoutError."""
    await show_html('<select id="s"><option value="a">A</option></select>')
    with pytest.raises(TimeoutError, match = "Option not found"):
        await fast_web.web_select(By.ID, "s", "z")


async def test_web_select_combobox_clicks_matching_item(fast_web:WebScrapingMixin, show_html:ShowHtml) -> None:
    """The typed value selects the list item whose normalized label matches."""
    await show_html('<input id="brand" aria-controls="brands"><ul id="brands">'
                    '<li id="rene" onclick="this.dataset.clicked = 1"><span>Logo</span><span>Rene Lezard</span></li>'
                    '<li id="esprit" onclick="this.dataset.clicked = 1"><span>Logo</span><span>Esprit</span></li></ul>')
    await fast_web.web_select_combobox(By.ID, "brand", "rene__lezard")
    assert await fast_web.web_execute("[...document.querySelectorAll('[data-clicked]')].map(li => li.id)") == ["rene"]


async def test_web_select_button_combobox_clicks_matching_option(fast_web:WebScrapingMixin, show_html:ShowHtml) -> None:
    """The option with the matching normalized text in the button's menu is clicked."""
    await show_html('<button id="color" role="combobox">Farbe</button><ul id="color-menu" role="listbox">'
                    '<li role="option">Rot</li><li role="option">  Dunkel   Blau </li></ul>')
    await fast_web.web_select_button_combobox("color", "dunkel blau")
    assert await fast_web.web_execute("clicks") == ["Farbe", "Dunkel Blau"]


async def test_web_select_button_combobox_unknown_option_raises_timeout(fast_web:WebScrapingMixin, show_html:ShowHtml) -> None:
    """Without a matching option nothing but the button is clicked."""
    await show_html('<button id="color" role="combobox">Farbe</button><ul id="color-menu"><li role="option">Rot</li></ul>')
    with pytest.raises(TimeoutError, match = "Option 'Grün' not found"):
        await fast_web.web_select_button_combobox("color", "Grün")
    assert await fast_web.web_execute("clicks") == ["Farbe"]


async def test_reset_ad_overview_state_drops_page_and_search_only(fast_web:WebScrapingMixin, show_html:ShowHtml) -> None:
    """Stored page and keyword are removed; other stored parameters stay."""
    await show_html("")
    await fast_web.web_execute("""sessionStorage.setItem('queryParams', JSON.stringify({pageNumber: '3', keyword: 'x', sort: 'NEW'})); true""")
    await fast_web.reset_ad_overview_state()
    assert await fast_web.web_execute("sessionStorage.getItem('queryParams')") == '{"sort":"NEW"}'


@pytest.mark.parametrize("stored", ["not json", "null"])
async def test_reset_ad_overview_state_leaves_unreadable_state(fast_web:WebScrapingMixin, show_html:ShowHtml, stored:str) -> None:
    """A missing or malformed entry is left untouched."""
    await show_html("")
    await fast_web.web_execute(f"sessionStorage.setItem('queryParams', {stored!r}); true")
    await fast_web.reset_ad_overview_state()
    assert await fast_web.web_execute("sessionStorage.getItem('queryParams')") == stored


@pytest.mark.parametrize(("body", "expected"), [
    pytest.param('<input type="hidden" name="attributeMap[kind.groesse]"><button id="kind.groesse" role="combobox">x</button>',
                 "kind.groesse", id = "derived-id"),
    pytest.param('<div><div><input type="hidden" name="attributeMap[kind.groesse]"></div>'
                 '<button id="other" role="combobox">x</button></div>', "other", id = "ancestor-walk"),
    pytest.param('<input type="hidden" name="attributeMap[kind.alter]"><button id="kind.groesse" role="combobox">x</button>',
                 None, id = "other-input"),
])
async def test_find_associated_button_combobox(fast_web:WebScrapingMixin, show_html:ShowHtml, body:str, expected:str | None) -> None:
    """The combobox is found by the id the hidden input names, else near the input."""
    await show_html(f"<form>{body}</form>")
    assert await fast_web._find_associated_button_combobox(hidden_input_name = "attributeMap[kind.groesse]") == expected
