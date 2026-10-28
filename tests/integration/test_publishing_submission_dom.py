# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Run the submit, validation and package page scripts in the real browser against inline HTML."""

import json
import sys
from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nodriver.core.connection import ProtocolException

from kleinanzeigen_bot import publishing_submission
from kleinanzeigen_bot.utils import page_scripts
from kleinanzeigen_bot.utils.exceptions import AdFormValidationError, ManualPackageSelectionRequiredError, PublishSubmissionUncertainError
from kleinanzeigen_bot.utils.web_scraping_mixin import WebScrapingMixin

pytestmark = [pytest.mark.itest, pytest.mark.slow, pytest.mark.asyncio(loop_scope = "session")]

ShowHtml = Callable[..., Awaitable[None]]
_EDITOR_PATH = "/p-anzeige-aufgeben-schritt2.html"
_PACKAGE_PATH = "/p-anzeigentypauswahl/216/123/draft"


async def _clicks(web:WebScrapingMixin) -> list[str]:
    return list(await web.web_execute("clicks"))


# --- submit button -------------------------------------------------------------------------------


async def _submit(web:WebScrapingMixin, show_html:ShowHtml, body:str) -> list[str]:
    """Click the submit button found by the page script and return the clicked labels."""
    await show_html(body, _EDITOR_PATH)
    await web.web_execute(publishing_submission._submit_button_script("submit"))
    return await _clicks(web)


@pytest.mark.parametrize("label", ["Anzeige aufgeben", "Änderungen speichern", "Anzeige speichern"])
@pytest.mark.parametrize("outcome", ["success", "timeout", "protocol", "response-lost", "sleep-timeout"])
async def test_legacy_publish_and_update_click_once_outside_polling(
    web:WebScrapingMixin, show_html:ShowHtml, label:str, outcome:str,
) -> None:
    """Keep the final publish or update click outside polling and avoid retries after uncertainty."""
    await show_html("", _EDITOR_PATH)
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
    assert await _clicks(web) == [label]


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
    await show_html(body, _EDITOR_PATH)
    assert await web.web_execute(publishing_submission._submit_button_script("inspect")) == expected
    assert await _clicks(web) == []


async def test_package_page_is_reported_before_any_button_lookup(web:WebScrapingMixin, show_html:ShowHtml) -> None:
    """The package selection page is detected by its path, so its submit button is never clicked here."""
    await show_html("<button>Anzeige aufgeben</button>", _PACKAGE_PATH)
    assert await web.web_execute(publishing_submission._submit_button_script("submit")) == "package"
    assert await _clicks(web) == []


# --- form validation -----------------------------------------------------------------------------


def _editor_html(*, container:str = "form", errors:bool = False, described:bool = True, extra:str = "") -> str:
    """An editor with an aria-invalid status error and no native error (live editor: one native form)."""
    message = "Bitte gib einen Wert ein." if errors else "Trage den Kilometerstand ein."
    describedby = ' aria-describedby="km-message"' if described else ""
    message_id = ' id="km-message"' if described else ""
    return (
        f'<{container}><input id="ad-title" value="Titel">'
        '<div><label for="autos.km">Kilometerstand</label>'
        f'<input id="autos.km" name="attributeMap[autos.km]" aria-invalid="{str(errors).lower()}"{describedby}>'
        f'<p{message_id} role="status">{message}</p></div>'
        f"{extra}<button>Nächster Schritt</button></{container}>"
        '<aside><p id="other-error">Unrelated page error</p></aside>'
    )


async def _validation_errors(web:WebScrapingMixin, show_html:ShowHtml, body:str) -> list[str]:
    await show_html(body, _EDITOR_PATH)
    return await publishing_submission._get_form_validation_errors(web)


async def test_reports_referenced_react_errors_without_native_invalid_state(web:WebScrapingMixin, show_html:ShowHtml) -> None:
    """Report field-associated React errors even when native validity passes."""
    assert await _validation_errors(web, show_html, _editor_html(errors = True)) == ["Kilometerstand: Bitte gib einen Wert ein."]


@pytest.mark.parametrize("container", ["main", "div"])
async def test_without_native_form_reports_nothing(web:WebScrapingMixin, show_html:ShowHtml, container:str) -> None:
    """Without a form around #ad-title there is no editor boundary, so nothing is guessed."""
    assert await _validation_errors(web, show_html, _editor_html(container = container, errors = True)) == []


async def test_ignores_status_hints_and_unrelated_page_errors(web:WebScrapingMixin, show_html:ShowHtml) -> None:
    """Ignore informative status text and errors outside the ad editor."""
    assert await _validation_errors(web, show_html, _editor_html()) == []


async def test_native_form_retains_its_explicit_summary_error(web:WebScrapingMixin, show_html:ShowHtml) -> None:
    """Native form ownership includes a summary, but excludes sibling errors."""
    body = ('<form><input id="ad-title" value="Titel"><div id="ad-form-error">Bitte korrigiere die Anzeige.</div></form>'
            '<div id="notification-error">Unrelated page notification.</div>')
    assert await _validation_errors(web, show_html, body) == ["Bitte korrigiere die Anzeige."]


async def test_native_invalid_input_reports_its_validation_message(web:WebScrapingMixin, show_html:ShowHtml) -> None:
    """A natively invalid required input reports the browser's own message under its label."""
    body = '<form><input id="ad-title" value="Titel"><label for="price">Preis</label><input id="price" required></form>'
    errors = await _validation_errors(web, show_html, body)
    assert len(errors) == 1
    assert errors[0].startswith("Preis: ")
    assert errors[0] != "Preis: "


async def test_combobox_status_error_uses_its_label(web:WebScrapingMixin, show_html:ShowHtml) -> None:
    """A redesigned combobox reports its referenced status error under its label."""
    body = ('<form><input id="ad-title" value="Titel"><div>'
            '<input type="hidden" name="attributeMap[autos.marke_s]"><label for="autos.marke_s">Marke</label>'
            '<button type="button" id="autos.marke_s" role="combobox" aria-invalid="true" aria-describedby="marke-message">'
            "Bitte wählen</button>"
            '<p id="marke-message" role="status">Bitte waehle eine Marke.</p></div></form>')
    assert await _validation_errors(web, show_html, body) == ["Marke: Bitte waehle eine Marke."]


async def test_unreferenced_status_error_is_scoped_to_one_invalid_field(web:WebScrapingMixin, show_html:ShowHtml) -> None:
    """An adjacent React error is read without collecting other page statuses."""
    body = _editor_html(errors = True, described = False, extra = '<p role="status">Unrelated main status</p>')
    assert await _validation_errors(web, show_html, body) == ["Kilometerstand: Bitte gib einen Wert ein."]


# --- free Basis package --------------------------------------------------------------------------


def _basis_html(*, selected:bool = True, basis_extra:str = "", basis_price:str = "<h3>Kostenlos</h3>",
                promo:str = "<p>Kostenlos inserieren – ohne zusätzliche Vorteile.</p>", plus_checked:bool | None = None,
                after_cards:str = "", submit:str = "<button>Anzeige aufgeben</button>", basis_attrs:str = "",
                shared_card:bool = False) -> str:
    """Two independent package cards like the observed listing screen."""
    plus_checked = not selected if plus_checked is None else plus_checked
    basis_radio = f'<button role="radio" aria-checked="{str(selected).lower()}"{basis_attrs}>Basis Paket</button>'
    plus_radio = f'<button role="radio" aria-checked="{str(plus_checked).lower()}">Plus Paket</button>'
    basis_body = f"{promo}{basis_price}<p>60 Tage Anzeigendauer.</p>{basis_extra}"
    if shared_card:
        # Both radios share one container, so no card belongs to Basis alone.
        cards = f"<div>{basis_radio}{basis_body}{plus_radio}<h3>11,99 €</h3></div>"
    else:
        cards = f"<div>{basis_radio}{basis_body}</div><div>{plus_radio}<h3>11,99 €</h3></div>"
    return f"<main>{cards}{after_cards}{submit}</main>"


_PACKAGE_BEHAVIOR = """
document.addEventListener('click', event => {
    const radio = event.target.closest('[role="radio"]');
    if (radio) for (const r of document.querySelectorAll('[role="radio"]')) r.setAttribute('aria-checked', String(r === radio));
    if (window.nextPage && event.target.closest('button')?.innerText === 'Nächster Schritt') {
        history.replaceState({}, '', window.nextPage.path);
        document.body.innerHTML = window.nextPage.body;
    }
});
true
"""


async def _show_staged(web:WebScrapingMixin, show_html:ShowHtml, body:str, path:str, *, next_page:str | None = None) -> None:
    """Show ``body``; radios behave like a radio group, and "Nächster Schritt" opens ``next_page``."""
    await show_html(body, path)
    await web.web_execute(_PACKAGE_BEHAVIOR)
    if next_page is not None:
        await web.web_execute(f"window.nextPage = {json.dumps({'path': _PACKAGE_PATH, 'body': next_page})}")


async def _basis(web:WebScrapingMixin, show_html:ShowHtml, action:str, body:str, path:str = _PACKAGE_PATH) -> tuple[str, list[str]]:
    await _show_staged(web, show_html, body, path)
    result = await web.web_execute(page_scripts.call("free_basis_package", action))
    return result["state"], await _clicks(web)


@pytest.mark.parametrize(("action", "selected", "expected_clicks", "expected_state"), [
    ("inspect", True, [], "ready"),
    ("select", True, [], "ready"),
    ("select", False, ["Basis Paket"], "waiting"),
    ("submit", True, ["Anzeige aufgeben"], "submitted"),
    ("submit", False, [], "unsafe"),
])
async def test_only_chosen_free_basis_can_be_submitted(
    web:WebScrapingMixin, show_html:ShowHtml, action:str, selected:bool, expected_clicks:list[str], expected_state:str,
) -> None:
    """Select or submit only the independently verified free Basis package."""
    assert await _basis(web, show_html, action, _basis_html(selected = selected)) == (expected_state, expected_clicks)


async def test_free_basis_does_not_depend_on_marketing_copy(web:WebScrapingMixin, show_html:ShowHtml) -> None:
    """The free check rests on the "Kostenlos" price and the absence of an amount, not on the promo sentence."""
    assert await _basis(web, show_html, "submit", _basis_html(promo = "")) == ("submitted", ["Anzeige aufgeben"])


@pytest.mark.parametrize("body", [
    pytest.param(_basis_html(basis_price = "<h3>9,99 €</h3>"), id = "paid"),
    pytest.param(_basis_html(basis_extra = "<p>Gebühr 5,00 EUR</p>"), id = "extra-fee"),
    pytest.param(_basis_html(basis_price = "<h3 hidden>Kostenlos</h3>"), id = "hidden-free-price"),
    pytest.param(_basis_html(after_cards = '<button role="radio">Basis Paket</button>'), id = "duplicate-basis"),
    pytest.param(_basis_html(promo = "", basis_price = "", after_cards = "<p>Kostenlos inserieren</p><h3>Kostenlos</h3>"),
                 id = "shared-free-text"),
    pytest.param(_basis_html(shared_card = True), id = "no-own-card"),
    pytest.param(_basis_html(basis_attrs = " disabled"), id = "disabled"),
    pytest.param(_basis_html(plus_checked = True), id = "double-selected"),
    pytest.param(_basis_html(submit = ""), id = "missing-submit"),
])
async def test_rejects_paid_ambiguous_and_unavailable_packages(web:WebScrapingMixin, show_html:ShowHtml, body:str) -> None:
    """Refuse package clicks when pricing, selection, or availability is unsafe."""
    assert await _basis(web, show_html, "submit", body) == ("unsafe", [])


async def test_draft_route_is_not_publication_confirmation(web:WebScrapingMixin, show_html:ShowHtml) -> None:
    """Leave editor drafts untouched when the package-selection page has not loaded."""
    assert await _basis(web, show_html, "submit", _basis_html(), _EDITOR_PATH) == ("waiting", [])


# --- staged package submission -------------------------------------------------------------------


async def _poll_three_times(condition:Any, **_:Any) -> Any:
    """Poll a browser condition with a bounded number of attempts."""
    for _attempt in range(3):
        result = await condition()
        if result:
            return result
    raise TimeoutError("fixture condition was not met")


@pytest.mark.parametrize("scenario", ["normal", "already-package", "invalid", "paid", "submit-timeout", "submit-response-lost"])
async def test_next_step_and_free_basis_are_handled_without_duplicate_submits(
    web:WebScrapingMixin, show_html:ShowHtml, scenario:str,
) -> None:
    """Publish free Basis once after staging, or stop before unsafe package submission."""
    package = _basis_html(selected = False, basis_price = "<h3>9,99 €</h3>" if scenario == "paid" else "<h3>Kostenlos</h3>")
    if scenario == "already-package":
        await _show_staged(web, show_html, package, _PACKAGE_PATH)
    else:
        await _show_staged(web, show_html, _editor_html(errors = scenario == "invalid"), _EDITOR_PATH,
                           next_page = None if scenario == "invalid" else package)
    real_execute = web.web_execute

    async def execute(script:str) -> Any:
        """Lose the response after the final package click."""
        result = await real_execute(script)
        if script == page_scripts.call("free_basis_package", "submit"):
            if scenario == "submit-timeout":
                raise TimeoutError("response lost after final click")
            if scenario == "submit-response-lost":
                return None
        return result

    with (
        patch.object(web, "web_execute", side_effect = execute),
        patch.object(web, "web_await", side_effect = _poll_three_times),
        patch.object(web, "web_sleep", new_callable = AsyncMock),
    ):
        if scenario in {"invalid", "paid"}:
            with pytest.raises(AdFormValidationError):
                await publishing_submission._click_submit_button(web, package_selection = "BASIS")
        elif scenario in {"submit-timeout", "submit-response-lost"}:
            with pytest.raises(PublishSubmissionUncertainError):
                await publishing_submission._click_submit_button(web, package_selection = "BASIS")
        else:
            await publishing_submission._click_submit_button(web, package_selection = "BASIS")
    clicked = await _clicks(web)
    if scenario in {"invalid", "paid"}:
        assert "Anzeige aufgeben" not in clicked
        assert "Basis Paket" not in clicked
    else:
        assert clicked.count("Basis Paket") == 1
        assert clicked.count("Anzeige aufgeben") == 1
    assert "Plus Paket" not in clicked
    assert clicked.count("Nächster Schritt") <= 1


@pytest.mark.parametrize("boundary", ["discovery", "next", "package-inspect", "package-select", "selected-state"])
@pytest.mark.parametrize("error_type", ["timeout", "protocol"])
async def test_original_transport_failure_is_preserved_before_final_click(
    web:WebScrapingMixin, show_html:ShowHtml, boundary:str, error_type:str,
) -> None:
    """Preserve retryable transport errors until the final publication click."""
    failure = TimeoutError("transport lost before publication") if error_type == "timeout" else ProtocolException(MagicMock(), "lost", 0)
    package = _basis_html(selected = False)
    if boundary in {"discovery", "next"}:
        await _show_staged(web, show_html, _editor_html(), _EDITOR_PATH, next_page = package)
    else:
        await _show_staged(web, show_html, package, _PACKAGE_PATH)
    real_execute = web.web_execute
    package_inspect = page_scripts.call("free_basis_package", "inspect")

    async def execute(script:str) -> Any:
        """Inject transport failures at the selected pre-publication boundary."""
        selected = "Basis Paket" in await real_execute("clicks")
        failing = (
            (boundary == "discovery" and script == publishing_submission._submit_button_script("inspect"))
            or (boundary == "next" and script == publishing_submission._submit_button_script("next"))
            or (boundary == "package-inspect" and script == package_inspect)
            or (boundary == "package-select" and script == page_scripts.call("free_basis_package", "select"))
            or (boundary == "selected-state" and selected and script == package_inspect)
        )
        if failing:
            raise failure
        return await real_execute(script)

    async def await_condition(condition:Any, **_:Any) -> Any:
        """Evaluate the browser condition once without masking its transport error."""
        return await condition()

    with (
        patch.object(web, "web_execute", side_effect = execute),
        patch.object(web, "web_await", side_effect = await_condition),
        patch.object(web, "web_sleep", new_callable = AsyncMock),
        pytest.raises(type(failure)) as exc_info,
    ):
        await publishing_submission._click_submit_button(web, package_selection = "BASIS")
    assert exc_info.value is failure
    assert "Anzeige aufgeben" not in await _clicks(web)


async def test_omitted_selection_never_chooses_or_publishes_a_package(
    web:WebScrapingMixin, show_html:ShowHtml, monkeypatch:pytest.MonkeyPatch,
) -> None:
    """Require manual completion without choosing or submitting an unspecified package."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    await _show_staged(web, show_html, _basis_html(selected = False), _PACKAGE_PATH)
    with (
        patch.object(web, "web_await", side_effect = _poll_three_times),
        patch.object(web, "web_sleep", new_callable = AsyncMock),
        patch("kleinanzeigen_bot.publishing_submission.ainput", new_callable = AsyncMock, return_value = "") as manual_input,
        pytest.raises(PublishSubmissionUncertainError),
    ):
        await publishing_submission._click_submit_button(web)
    assert await _clicks(web) == []
    manual_input.assert_awaited_once()


async def test_manual_selection_keeps_the_legacy_submit_flow(
    web:WebScrapingMixin, show_html:ShowHtml, monkeypatch:pytest.MonkeyPatch,
) -> None:
    """Preserve single-step publishing when no package-selection page appears."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    await show_html("<button>Anzeige aufgeben</button>", _EDITOR_PATH)
    with (
        patch.object(web, "web_await", side_effect = _poll_three_times),
        patch.object(web, "web_sleep", new_callable = AsyncMock),
        patch("kleinanzeigen_bot.publishing_submission.ainput", new_callable = AsyncMock) as manual_input,
    ):
        await publishing_submission._click_submit_button(web, package_selection = "MANUAL")
    assert await _clicks(web) == ["Anzeige aufgeben"]
    manual_input.assert_not_awaited()


@pytest.mark.parametrize("initial_stage", ["editor", "package"])
@pytest.mark.parametrize("outcome", ["completed", "still-package", "still-editor", "eof", "input-error", "page-timeout"])
async def test_manual_package_selection_never_clicks_a_package_or_final_submit(
    web:WebScrapingMixin, show_html:ShowHtml, monkeypatch:pytest.MonkeyPatch, initial_stage:str, outcome:str,
) -> None:
    """Leave package and final submit controls to the user throughout manual completion."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    package = _basis_html(selected = False)
    if initial_stage == "editor":
        await _show_staged(web, show_html, _editor_html(), _EDITOR_PATH, next_page = package)
    else:
        await _show_staged(web, show_html, package, _PACKAGE_PATH)
    real_execute = web.web_execute
    input_seen = False

    async def execute(script:str) -> Any:
        """Lose the browser state once the user has answered, if the scenario asks for it."""
        if input_seen and outcome == "page-timeout":
            raise TimeoutError("manual completion state unavailable")
        return await real_execute(script)

    async def manual_completion(*_:object) -> str:
        """Act as the user: finish, leave a draft behind, or fail to answer."""
        nonlocal input_seen
        assert await real_execute("location.pathname") == _PACKAGE_PATH
        clicked = list(await real_execute("clicks"))
        assert "Basis Paket" not in clicked
        assert "Anzeige aufgeben" not in clicked
        input_seen = True
        if outcome == "eof":
            raise EOFError("interactive input unavailable")
        if outcome == "input-error":
            raise OSError("interactive input unavailable")
        path = {"still-package": _PACKAGE_PATH, "still-editor": _EDITOR_PATH}.get(outcome, "/p-anzeige-aufgeben-bestaetigung.html")
        await real_execute(f"history.replaceState({{}}, '', {json.dumps(path)}); true")
        return ""

    with (
        patch.object(web, "web_execute", side_effect = execute),
        patch.object(web, "web_await", side_effect = _poll_three_times),
        patch.object(web, "web_sleep", new_callable = AsyncMock),
        patch("kleinanzeigen_bot.publishing_submission.ainput", new_callable = AsyncMock, side_effect = manual_completion) as manual_input,
    ):
        if outcome == "completed":
            await publishing_submission._click_submit_button(web, package_selection = "MANUAL")
        else:
            with pytest.raises(PublishSubmissionUncertainError):
                await publishing_submission._click_submit_button(web, package_selection = "MANUAL")
    assert list(await real_execute("clicks")) == (["Nächster Schritt"] if initial_stage == "editor" else [])
    manual_input.assert_awaited_once()


async def test_manual_without_terminal_stops_before_next_step(
    web:WebScrapingMixin, show_html:ShowHtml, monkeypatch:pytest.MonkeyPatch,
) -> None:
    """Unattended MANUAL runs must not click "Nächster Schritt", which would leave an unfinished draft (#1341)."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    await _show_staged(web, show_html, _editor_html(), _EDITOR_PATH, next_page = _basis_html(selected = False))
    with (
        patch.object(web, "web_await", side_effect = _poll_three_times),
        patch.object(web, "web_sleep", new_callable = AsyncMock),
        pytest.raises(ManualPackageSelectionRequiredError),
    ):
        await publishing_submission._click_submit_button(web, package_selection = "MANUAL")
    assert await _clicks(web) == []


# --- publish outcome -----------------------------------------------------------------------------


@pytest.mark.parametrize(("body", "expected"), [
    ('<h1>Geschafft!</h1><a href="/m-meine-anzeigen.html">Zu meinen Anzeigen</a>', True),
    ("<h1>Geschafft!</h1>", False),
    ('<a href="/m-meine-anzeigen.html">Zu meinen Anzeigen</a>', False),
])
async def test_idless_success_page_needs_message_and_manage_ads_link(
    web:WebScrapingMixin, show_html:ShowHtml, body:str, expected:bool,
) -> None:
    """The redesigned success page is recognized only by its message together with the manage-ads control."""
    await show_html(body)
    assert await publishing_submission._is_idless_publish_success_page(web) is expected


@pytest.mark.parametrize(("body", "path", "expected"), [
    ("<button>Ohne Bild veröffentlichen</button>", _EDITOR_PATH, "hint"),
    ("", _PACKAGE_PATH, "package"),
    ("<button>Nächster Schritt</button>", _EDITOR_PATH, None),
])
async def test_next_step_outcome_tells_hint_from_package_page(
    web:WebScrapingMixin, show_html:ShowHtml, body:str, path:str, expected:str | None,
) -> None:
    """After "Nächster Schritt" the script names the no-image hint or the package page, else nothing yet."""
    await show_html(body, path)
    assert await web.web_execute(page_scripts.call("next_step_outcome")) == expected


@pytest.mark.parametrize("marker", ["EditAdSuccess", "EditAd"])
async def test_update_success_snapshot_in_browser(
    web:WebScrapingMixin, show_html:ShowHtml, blank_site:str, marker:str,
) -> None:
    """Read the native marker and real navigation referrer together on a local page."""
    await show_html('<a href="/done">Continue</a>', "/p-anzeige-bearbeiten.html?adId=777")
    await web.web_execute("document.querySelector('a').click()")
    await web.web_await(lambda: web.web_execute("window.location.pathname === '/done'"))
    await web.web_execute(f"window.pageType = {json.dumps(marker)}")
    assert await publishing_submission._is_update_success_page(web, root_url = blank_site, ad_id = 777) is (marker == "EditAdSuccess")
