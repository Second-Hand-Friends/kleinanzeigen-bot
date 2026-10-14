# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Run the submit/validation page scripts in Node.js against a small synthetic DOM.

These tests start a Node.js process (``nodejs-wheel-binaries``) per script, so they are
integration tests. The DOM below is a hand-written fake that only models the browser
semantics the scripts use; extend it when a script relies on more.
"""

import json
import sys
from typing import Any, Literal, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nodejs_wheel import node
from nodriver.core.connection import ProtocolException

from kleinanzeigen_bot import publishing_submission
from kleinanzeigen_bot.app import KleinanzeigenBot
from kleinanzeigen_bot.model.ad_model import Ad, AdUpdateStrategy
from kleinanzeigen_bot.utils.exceptions import AdFormValidationError, ManualPackageSelectionRequiredError, PublishSubmissionUncertainError

pytestmark = pytest.mark.itest


def _make_min_ad() -> Ad:
    return Ad.model_validate({
        "title": "Test Ad Title",
        "description": "Test description for the ad listing.",
        "type": "OFFER",
        "price_type": "FIXED",
        "price": 10,
        "sell_directly": False,
        "shipping_type": "NOT_APPLICABLE",
        "category": "160",
        "special_attributes": {},
        "images": [],
        "active": True,
        "republication_interval": 7,
        "contact": {
            "name": "Test User",
            "zipcode": "12345",
            "location": "Test City",
        },
    })


_DOM_FIXTURE = r"""
const payload = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const clicks = [];
class Element {
    constructor(spec, parent = null) {
        this.tagName = (spec.tag || 'div').toUpperCase();
        this.attrs = spec.attrs || {};
        this.id = this.attrs.id || '';
        this.htmlFor = this.attrs.for || '';
        this.ownText = spec.text || '';
        this.hidden = Boolean(spec.hidden);
        this.disabled = Boolean(spec.disabled);
        this.validationMessage = spec.validationMessage || '';
        this.invalid = Boolean(spec.invalid);
        this.parentElement = parent;
        this.children = (spec.children || []).map(child => new Element(child, this));
    }
    get innerText() { return [this.ownText, ...this.children.map(child => child.innerText)].filter(Boolean).join(' '); }
    get textContent() { return this.innerText; }
    getAttribute(name) { return this.attrs[name] ?? null; }
    getClientRects() {
        for (let element = this; element; element = element.parentElement) if (element.hidden) return [];
        return [{}];
    }
    contains(element) {
        for (let candidate = element; candidate; candidate = candidate.parentElement) if (candidate === this) return true;
        return false;
    }
    matches(selector) {
        return selector.split(',').some(raw => {
            let part = raw.trim();
            if (part === '*') return true;
            if (part === ':disabled') {
                // Native semantics: own flag, or inherited from a disabled ancestor fieldset.
                for (let element = this; element; element = element.parentElement) {
                    if (element.disabled || (element.tagName === 'FIELDSET' && element.getAttribute('disabled') !== null)) return true;
                }
                return false;
            }
            if (part.startsWith('.')) return (this.attrs.class || '').split(' ').includes(part.slice(1));
            if (part.includes(':invalid')) {
                if (!this.invalid) return false;
                part = part.replace(':invalid', '');
            }
            const tag = part.match(/^[a-z]+/i)?.[0];
            if (tag && this.tagName !== tag.toUpperCase()) return false;
            return [...part.matchAll(/\[([\w-]+)(?:(\$?=)"([^"]*)")?\]/g)].every(([, name, op, value]) => {
                const actual = this.getAttribute(name);
                if (actual === null) return false;
                return !op || (op === '$=' ? actual.endsWith(value) : actual === value);
            });
        });
    }
    querySelectorAll(selector) {
        return this.children.flatMap(child => [child, ...child.querySelectorAll('*')]).filter(child => child.matches(selector));
    }
    closest(selector) {
        for (let element = this; element; element = element.parentElement) if (element.matches(selector)) return element;
        return null;
    }
    click() {
        clicks.push(this.innerText);
        if (this.getAttribute('role') === 'radio') {
            for (const radio of document.querySelectorAll('button[role="radio"]')) radio.attrs['aria-checked'] = 'false';
            this.attrs['aria-checked'] = 'true';
        }
    }
}
const body = new Element(payload.dom);
const document = {
    body,
    querySelectorAll: selector => body.querySelectorAll(selector),
    getElementById: id => [body, ...body.querySelectorAll('*')].find(element => element.id === id) || null,
};
const window = {location: {pathname: payload.pathname}};
const getComputedStyle = element => ({visibility: element.getClientRects().length ? 'visible' : 'hidden'});
const result = eval(payload.script);
process.stdout.write(JSON.stringify({result, clicks}));
"""


def _run_dom_script(script:str, dom:dict[str, Any], *, pathname:str = "/p-anzeigentypauswahl/216/123/draft") -> dict[str, Any]:
    """Execute a production script against a synthetic DOM without a browser."""
    completed = node(
        ["-e", _DOM_FIXTURE], return_completed_process = True,
        input = json.dumps({"script": script, "dom": dom, "pathname": pathname}),
        capture_output = True, text = True, encoding = "utf-8", check = True, timeout = 10,
    )
    return cast(dict[str, Any], json.loads(completed.stdout))


def _editor_dom(*, container:str = "form", errors:bool = False) -> dict[str, Any]:
    """Build an editor with aria-invalid status errors and no native error (live editor: one native form)."""
    return {"tag": "body", "children": [
        {"tag": container, "children": [
            {"tag": "input", "attrs": {"id": "ad-title"}},
            {"children": [
                {"tag": "label", "text": "Kilometerstand", "attrs": {"for": "autos.km"}},
                {"tag": "input", "attrs": {"id": "autos.km", "name": "attributeMap[autos.km]",
                                          "aria-invalid": str(errors).lower(), "aria-describedby": "km-message"}},
                {"tag": "p", "text": "Bitte gib einen Wert ein." if errors else "Trage den Kilometerstand ein.",
                 "attrs": {"id": "km-message", "role": "status"}},
            ]},
            {"tag": "button", "text": "Nächster Schritt"},
        ]},
        {"tag": "aside", "children": [{"tag": "p", "text": "Unrelated page error", "attrs": {"id": "other-error"}}]},
    ]}


class TestReactFormValidation:
    """Read referenced status errors inside the editor's native form."""

    @pytest.mark.asyncio
    async def test_reports_referenced_react_errors_without_native_invalid_state(self, test_bot:KleinanzeigenBot) -> None:
        """Report field-associated React errors even when native validity passes."""
        with patch.object(
            test_bot, "web_execute", new_callable = AsyncMock,
            side_effect = lambda script: _run_dom_script(script, _editor_dom(errors = True))["result"],
        ):
            result = await publishing_submission._get_form_validation_errors(test_bot)
        assert result == ["Kilometerstand: Bitte gib einen Wert ein."]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("container", ["main", "div"])
    async def test_without_native_form_reports_nothing(self, test_bot:KleinanzeigenBot, container:str) -> None:
        """Without a form around #ad-title there is no editor boundary, so nothing is guessed."""
        with patch.object(
            test_bot, "web_execute", new_callable = AsyncMock,
            side_effect = lambda script: _run_dom_script(script, _editor_dom(container = container, errors = True))["result"],
        ):
            assert await publishing_submission._get_form_validation_errors(test_bot) == []

    @pytest.mark.asyncio
    async def test_ignores_status_hints_and_unrelated_page_errors(self, test_bot:KleinanzeigenBot) -> None:
        """Ignore informative status text and errors outside the ad editor."""
        with patch.object(
            test_bot, "web_execute", new_callable = AsyncMock,
            side_effect = lambda script: _run_dom_script(script, _editor_dom())["result"],
        ):
            result = await publishing_submission._get_form_validation_errors(test_bot)
        assert result == []

    @pytest.mark.asyncio
    async def test_native_form_retains_its_explicit_summary_error(self, test_bot:KleinanzeigenBot) -> None:
        """Native form ownership includes a summary, but excludes sibling errors."""
        dom = {"tag": "body", "children": [
            {"tag": "form", "children": [
                {"tag": "input", "attrs": {"id": "ad-title"}},
                {"tag": "div", "text": "Bitte korrigiere die Anzeige.", "attrs": {"id": "ad-form-error"}},
            ]},
            {"tag": "div", "text": "Unrelated page notification.", "attrs": {"id": "notification-error"}},
        ]}
        with patch.object(
            test_bot, "web_execute", new_callable = AsyncMock,
            side_effect = lambda script: _run_dom_script(script, dom)["result"],
        ):
            assert await publishing_submission._get_form_validation_errors(test_bot) == ["Bitte korrigiere die Anzeige."]

    @pytest.mark.asyncio
    async def test_combobox_status_error_uses_its_label(self, test_bot:KleinanzeigenBot) -> None:
        """A redesigned combobox reports its referenced status error under its label."""
        dom = {"tag": "body", "children": [{"tag": "form", "children": [
            {"tag": "input", "attrs": {"id": "ad-title"}},
            {"children": [
                {"tag": "input", "hidden": True, "attrs": {"type": "hidden", "name": "attributeMap[autos.marke_s]"}},
                {"tag": "label", "text": "Marke", "attrs": {"for": "autos.marke_s"}},
                {"tag": "button", "attrs": {"id": "autos.marke_s", "role": "combobox", "aria-invalid": "true", "aria-describedby": "marke-message"}},
                {"tag": "p", "text": "Bitte waehle eine Marke.", "attrs": {"id": "marke-message", "role": "status"}},
            ]},
        ]}]}
        with patch.object(
            test_bot, "web_execute", new_callable = AsyncMock,
            side_effect = lambda script: _run_dom_script(script, dom)["result"],
        ):
            assert await publishing_submission._get_form_validation_errors(test_bot) == ["Marke: Bitte waehle eine Marke."]


class TestSingleFinalSubmit:
    """Button discovery may poll, but a final click may never be repeated."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("label", ["Anzeige aufgeben", "Änderungen speichern", "Anzeige speichern"])
    @pytest.mark.parametrize("outcome", ["success", "timeout", "protocol", "response-lost", "sleep-timeout"])
    async def test_legacy_publish_and_update_click_once_outside_polling(
        self, test_bot:KleinanzeigenBot, label:str, outcome:str,
    ) -> None:
        """Keep the final publish or update click outside polling and avoid retries after uncertainty."""
        clicked:list[str] = []
        discovery_attempts = 0
        polling = False

        async def execute(script:str) -> Any:
            """Simulate delayed button discovery and failures after the recorded final click."""
            nonlocal discovery_attempts
            if 'const action = "inspect"' in script:
                discovery_attempts += 1
                dom = {"tag": "body", "children": [] if discovery_attempts == 1 else [{"tag": "button", "text": label}]}
            else:
                assert not polling, "The final click must not be inside retry polling"
                dom = {"tag": "body", "children": [{"tag": "button", "text": label}]}
            result = _run_dom_script(script, dom, pathname = "/p-anzeige-aufgeben-schritt2.html")
            clicked.extend(result["clicks"])
            if result["clicks"]:
                if outcome == "timeout":
                    raise TimeoutError("response lost after final click")
                if outcome == "protocol":
                    raise ProtocolException(MagicMock(), "response lost after final click", 0)
                if outcome == "response-lost":
                    return None
            return result["result"]

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
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, side_effect = execute),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = await_condition),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock,
                         side_effect = TimeoutError("navigation lost after click") if outcome == "sleep-timeout" else None),
        ):
            if outcome == "success":
                await publishing_submission._click_submit_button(test_bot)
            else:
                with pytest.raises(PublishSubmissionUncertainError):
                    await publishing_submission._click_submit_button(test_bot)
        assert discovery_attempts == 2
        assert clicked == [label]

    @pytest.mark.asyncio
    async def test_submit_label_priority_beats_dom_order(self, test_bot:KleinanzeigenBot) -> None:
        """A secondary save button earlier in the DOM must not be clicked instead of the real submit."""
        dom = {"tag": "body", "children": [{"tag": "button", "text": "Anzeige speichern"}, {"tag": "button", "text": "Anzeige aufgeben"}]}
        clicked:list[str] = []

        async def execute(script:str) -> Any:
            result = _run_dom_script(script, dom, pathname = "/p-anzeige-aufgeben-schritt2.html")
            clicked.extend(result["clicks"])
            return result["result"]

        async def await_condition(condition:Any, **_:Any) -> Any:
            return await condition()

        with (
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, side_effect = execute),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = await_condition),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
        ):
            await publishing_submission._click_submit_button(test_bot)
        assert clicked == ["Anzeige aufgeben"]

    @pytest.mark.asyncio
    async def test_submit_search_stays_inside_the_editor_form(self, test_bot:KleinanzeigenBot) -> None:
        """A higher-priority label outside the editor form (e.g. a header) must not be clicked."""
        dom = {"tag": "body", "children": [
            {"tag": "header", "children": [{"tag": "button", "text": "Anzeige aufgeben"}]},
            {"tag": "form", "children": [{"tag": "input", "attrs": {"id": "ad-title"}}, {"tag": "button", "text": "Anzeige speichern"}]},
        ]}
        clicked:list[str] = []

        async def execute(script:str) -> Any:
            result = _run_dom_script(script, dom, pathname = "/p-anzeige-bearbeiten.html")
            clicked.extend(result["clicks"])
            return result["result"]

        async def await_condition(condition:Any, **_:Any) -> Any:
            return await condition()

        with (
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, side_effect = execute),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = await_condition),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
        ):
            await publishing_submission._click_submit_button(test_bot)
        assert clicked == ["Anzeige speichern"]

    @pytest.mark.asyncio
    async def test_button_disabled_by_fieldset_is_not_clicked(self, test_bot:KleinanzeigenBot) -> None:
        """A submit button inside <fieldset disabled> cannot be clicked, so it must not count as submitted."""
        dom = {"tag": "body", "children": [
            {"tag": "fieldset", "attrs": {"disabled": ""}, "children": [{"tag": "button", "text": "Anzeige aufgeben"}]},
        ]}
        clicked:list[str] = []

        async def execute(script:str) -> Any:
            result = _run_dom_script(script, dom, pathname = "/p-anzeige-aufgeben-schritt2.html")
            clicked.extend(result["clicks"])
            return result["result"]

        async def await_condition(condition:Any, **_:Any) -> Any:
            return await condition()

        with (
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, side_effect = execute),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = await_condition),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            pytest.raises(TimeoutError),
        ):
            await publishing_submission._click_submit_button(test_bot)
        assert clicked == []


@pytest.mark.asyncio
async def test_unreferenced_status_error_is_scoped_to_one_invalid_field(test_bot:KleinanzeigenBot) -> None:
    """An adjacent React error is read without collecting other page statuses."""
    dom = _editor_dom(errors = True)
    field_container = dom["children"][0]["children"][1]
    del field_container["children"][1]["attrs"]["aria-describedby"]
    field_container["children"][2]["attrs"].pop("id")
    dom["children"][0]["children"].append({"tag": "p", "text": "Unrelated main status", "attrs": {"role": "status"}})
    with patch.object(
        test_bot, "web_execute", new_callable = AsyncMock,
        side_effect = lambda script: _run_dom_script(script, dom)["result"],
    ):
        result = await publishing_submission._get_form_validation_errors(test_bot)
    assert result == ["Kilometerstand: Bitte gib einen Wert ein."]


def _basis_dom(*, selected:bool = True) -> dict[str, Any]:
    """Build two independent package cards like the observed listing screen."""
    return {"tag": "body", "children": [{"tag": "main", "children": [
        {"children": [
            {"tag": "button", "text": "Basis Paket", "attrs": {"role": "radio", "aria-checked": str(selected).lower()}},
            {"tag": "p", "text": "Kostenlos inserieren – ohne zusätzliche Vorteile."},
            {"tag": "h3", "text": "Kostenlos"},
            {"tag": "p", "text": "60 Tage Anzeigendauer."},
        ]},
        {"children": [
            {"tag": "button", "text": "Plus Paket", "attrs": {"role": "radio", "aria-checked": str(not selected).lower()}},
            {"tag": "h3", "text": "11,99 €"},
        ]},
        {"tag": "button", "text": "Anzeige aufgeben"},
    ]}]}


class TestFreeBasisPackage:
    """The real package script must never choose a paid or unverified option."""

    @pytest.mark.parametrize(("action", "selected", "expected_clicks", "expected_state"), [
        ("inspect", True, [], "ready"),
        ("select", True, [], "ready"),
        ("select", False, ["Basis Paket"], "waiting"),
        ("submit", True, ["Anzeige aufgeben"], "submitted"),
        ("submit", False, [], "unsafe"),
    ])
    def test_only_chosen_free_basis_can_be_submitted(
        self, action:str, selected:bool, expected_clicks:list[str], expected_state:str,
    ) -> None:
        """Select or submit only the independently verified free Basis package."""
        script = publishing_submission._FREE_BASIS_PACKAGE_SCRIPT.replace("ACTION", json.dumps(action))
        result = _run_dom_script(script, _basis_dom(selected = selected))
        assert result["result"]["state"] == expected_state
        assert result["clicks"] == expected_clicks

    def test_free_basis_does_not_depend_on_marketing_copy(self) -> None:
        """The free check rests on the "Kostenlos" price and the absence of an amount, not on the promo sentence."""
        dom = _basis_dom()
        basis = dom["children"][0]["children"][0]
        basis["children"] = [child for child in basis["children"] if "inserieren" not in child.get("text", "")]
        script = publishing_submission._FREE_BASIS_PACKAGE_SCRIPT.replace("ACTION", json.dumps("submit"))
        result = _run_dom_script(script, dom)
        assert result["result"]["state"] == "submitted"
        assert result["clicks"] == ["Anzeige aufgeben"]

    @pytest.mark.parametrize("case", ["paid", "extra-fee", "hidden-free-price", "duplicate-basis",
                             "shared-free-text", "no-own-card", "disabled", "double-selected", "missing-submit"])
    def test_rejects_paid_ambiguous_and_unavailable_packages(self, case:str) -> None:
        """Refuse package clicks when pricing, selection, or availability is unsafe."""
        dom = _basis_dom()
        cards = dom["children"][0]["children"]
        basis = cards[0]
        if case == "paid":
            basis["children"][2]["text"] = "9,99 €"
        elif case == "extra-fee":
            basis["children"].append({"tag": "p", "text": "Gebühr 5,00 EUR"})
        elif case == "hidden-free-price":
            basis["children"][2]["hidden"] = True
        elif case == "duplicate-basis":
            cards.append({"tag": "button", "text": "Basis Paket", "attrs": {"role": "radio"}})
        elif case == "shared-free-text":
            cards.extend(basis["children"][1:3])
            basis["children"] = basis["children"][:1]
        elif case == "no-own-card":
            # Both radios share one container, so no card belongs to Basis alone.
            plus = cards.pop(1)
            basis["children"].extend(plus["children"])
        elif case == "disabled":
            basis["children"][0]["disabled"] = True
        elif case == "double-selected":
            cards[1]["children"][0]["attrs"]["aria-checked"] = "true"
        else:
            cards.pop(2)
        script = publishing_submission._FREE_BASIS_PACKAGE_SCRIPT.replace("ACTION", json.dumps("submit"))
        result = _run_dom_script(script, dom)
        assert result["result"]["state"] == "unsafe"
        assert result["clicks"] == []

    def test_draft_route_is_not_publication_confirmation(self) -> None:
        """Leave editor drafts untouched when the package-selection page has not loaded."""
        result = _run_dom_script(
            publishing_submission._FREE_BASIS_PACKAGE_SCRIPT.replace("ACTION", json.dumps("submit")),
            _basis_dom(), pathname = "/p-anzeige-aufgeben-schritt2.html",
        )
        assert result["result"]["state"] == "waiting"
        assert result["clicks"] == []


class TestStagedPackageSubmission:
    """Exercise the complete staged boundary with production scripts and fake DOMs."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("scenario", ["normal", "already-package", "invalid", "paid", "submit-timeout", "submit-response-lost"])
    async def test_next_step_and_free_basis_are_handled_without_duplicate_submits(
        self, test_bot:KleinanzeigenBot, scenario:str,
    ) -> None:
        """Publish free Basis once after staging, or stop before unsafe package submission."""
        stage = "package" if scenario == "already-package" else "editor"
        selected = False
        clicked:list[str] = []

        async def execute(script:str) -> Any:
            """Advance the local editor and package states while recording clicks and injected failures."""
            nonlocal stage, selected
            dom = _basis_dom(selected = selected) if stage == "package" else _editor_dom(errors = scenario == "invalid")
            pathname = "/p-anzeigentypauswahl/216/123/draft" if stage == "package" else "/p-anzeige-aufgeben-schritt2.html"
            if scenario == "paid" and stage == "package":
                dom["children"][0]["children"][0]["children"][2]["text"] = "9,99 €"
            result = _run_dom_script(script, dom, pathname = pathname)
            clicked.extend(result["clicks"])
            if "Nächster Schritt" in result["clicks"] and scenario != "invalid":
                stage = "package"
            if "Basis Paket" in result["clicks"]:
                selected = True
            if "Anzeige aufgeben" in result["clicks"]:
                if scenario == "submit-timeout":
                    raise TimeoutError("response lost after final click")
                if scenario == "submit-response-lost":
                    return None
            return result["result"]

        async def await_condition(condition:Any, **_:Any) -> Any:
            """Poll the simulated browser condition with a bounded number of attempts."""
            for _attempt in range(3):
                result = await condition()
                if result:
                    return result
            raise TimeoutError("fixture condition was not met")

        with (
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, side_effect = execute),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = await_condition),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
        ):
            if scenario in {"invalid", "paid"}:
                with pytest.raises(AdFormValidationError):
                    await publishing_submission._click_submit_button(test_bot, package_selection = "BASIS")
                assert "Anzeige aufgeben" not in clicked
                assert "Basis Paket" not in clicked
            elif scenario in {"submit-timeout", "submit-response-lost"}:
                with pytest.raises(PublishSubmissionUncertainError):
                    await publishing_submission._click_submit_button(test_bot, package_selection = "BASIS")
                assert clicked.count("Anzeige aufgeben") == 1
            else:
                await publishing_submission._click_submit_button(test_bot, package_selection = "BASIS")
                assert clicked.count("Basis Paket") == 1
                assert clicked.count("Anzeige aufgeben") == 1
        assert "Plus Paket" not in clicked
        assert clicked.count("Nächster Schritt") <= 1


class TestPreSubmitTransportFailures:
    """Transport loss before publication is retryable, not form rejection."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("boundary", ["discovery", "next", "package-inspect", "package-select", "selected-state"])
    @pytest.mark.parametrize("error_type", ["timeout", "protocol"])
    async def test_original_transport_failure_is_preserved_before_final_click(
        self, test_bot:KleinanzeigenBot, boundary:str, error_type:str,
    ) -> None:
        """Preserve retryable transport errors until the final publication click."""
        failure = TimeoutError("transport lost before publication") if error_type == "timeout" else ProtocolException(MagicMock(), "lost", 0)
        stage = "editor" if boundary in {"discovery", "next"} else "package"
        selected = False
        clicked:list[str] = []

        async def execute(script:str) -> Any:
            """Inject transport failures at the selected pre-publication boundary."""
            nonlocal stage, selected
            failing = (
                (boundary == "discovery" and 'const action = "inspect"' in script and "const labels =" in script)
                or (boundary == "next" and 'const action = "next"' in script)
                or (boundary == "package-inspect" and 'const action = "inspect"' in script and "const labels =" not in script)
                or (boundary == "package-select" and 'const action = "select"' in script)
                or (boundary == "selected-state" and selected and 'const action = "inspect"' in script)
            )
            if failing:
                raise failure
            dom = _editor_dom() if stage == "editor" else _basis_dom(selected = selected)
            pathname = "/p-anzeige-aufgeben-schritt2.html" if stage == "editor" else "/p-anzeigentypauswahl/local/draft"
            result = _run_dom_script(script, dom, pathname = pathname)
            clicked.extend(result["clicks"])
            if "Nächster Schritt" in result["clicks"]:
                stage = "package"
            if "Basis Paket" in result["clicks"]:
                selected = True
            return result["result"]

        async def await_condition(condition:Any, **_:Any) -> Any:
            """Evaluate the simulated browser condition once without masking its transport error."""
            return await condition()

        with (
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, side_effect = execute),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = await_condition),
            pytest.raises(type(failure)) as exc_info,
        ):
            await publishing_submission._click_submit_button(test_bot, package_selection = "BASIS")
        assert exc_info.value is failure
        assert "Anzeige aufgeben" not in clicked


class TestConfigurablePackageSelection:
    """Package choices propagate to the submit boundary without changing legacy forms."""

    @pytest.fixture(autouse = True)
    def interactive_terminal(self, monkeypatch:pytest.MonkeyPatch) -> None:
        """Manual-flow fixtures explicitly model an interactive terminal."""
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("package_selection", [None, "BASIS", "MANUAL"])
    async def test_submission_passes_the_configured_package_selection(
        self, test_bot:KleinanzeigenBot, package_selection:Literal["BASIS", "MANUAL"] | None,
    ) -> None:
        """Forward the configured package preference and use MANUAL when it is omitted."""
        selection_kwargs:dict[str, Any] = {} if package_selection is None else {"package_selection": package_selection}
        with (
            patch("kleinanzeigen_bot.captcha_flow.check_and_wait_for_captcha", new_callable = AsyncMock),
            patch.object(test_bot, "web_set_input_value", new_callable = AsyncMock),
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, return_value = ""),
            patch("kleinanzeigen_bot.publishing_submission._click_submit_button", new_callable = AsyncMock,
                  side_effect = AdFormValidationError("fixture stops before submission")) as submit,
            pytest.raises(AdFormValidationError),
        ):
            await publishing_submission.submit_and_confirm_ad(
                test_bot, "test.yaml", _make_min_ad(), AdUpdateStrategy.REPLACE,
                captcha_config = test_bot.config.captcha, root_url = test_bot.root_url,
                **selection_kwargs,
            )
        submit.assert_awaited_once_with(test_bot, package_selection = package_selection or "MANUAL")

    @pytest.mark.asyncio
    async def test_omitted_selection_never_chooses_or_publishes_a_package(self, test_bot:KleinanzeigenBot) -> None:
        """Require manual completion without choosing or submitting an unspecified package."""
        clicked:list[str] = []

        async def execute(script:str) -> Any:
            """Run the package fixture and record any attempted control clicks."""
            result = _run_dom_script(script, _basis_dom(selected = False), pathname = "/p-anzeigentypauswahl/216/123/draft")
            clicked.extend(result["clicks"])
            return result["result"]

        async def await_condition(condition:Any, **_:Any) -> Any:
            """Evaluate the package fixture condition without adding retry behavior."""
            return await condition()

        with (
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, side_effect = execute),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = await_condition),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.publishing_submission.ainput", new_callable = AsyncMock, return_value = "") as manual_input,
            pytest.raises(PublishSubmissionUncertainError),
        ):
            await publishing_submission._click_submit_button(test_bot)
        assert clicked == []
        manual_input.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_manual_selection_keeps_the_legacy_submit_flow(self, test_bot:KleinanzeigenBot) -> None:
        """Preserve single-step publishing when no package-selection page appears."""
        dom = {"tag": "body", "children": [{"tag": "button", "text": "Anzeige aufgeben"}]}
        clicked:list[str] = []

        async def execute(script:str) -> Any:
            """Run the legacy editor fixture and record the final submit click."""
            result = _run_dom_script(script, dom, pathname = "/p-anzeige-aufgeben-schritt2.html")
            clicked.extend(result["clicks"])
            return result["result"]

        async def await_condition(condition:Any, **_:Any) -> Any:
            """Evaluate the legacy submit-discovery condition once."""
            return await condition()

        with (
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, side_effect = execute),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = await_condition),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.publishing_submission.ainput", new_callable = AsyncMock) as manual_input,
        ):
            await publishing_submission._click_submit_button(test_bot, package_selection = "MANUAL")
        assert clicked == ["Anzeige aufgeben"]
        manual_input.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("initial_stage", ["editor", "package"])
    @pytest.mark.parametrize("outcome", ["completed", "still-package", "still-editor", "eof", "input-error", "page-timeout"])
    async def test_manual_package_selection_never_clicks_a_package_or_final_submit(
        self, test_bot:KleinanzeigenBot, initial_stage:str, outcome:str,
    ) -> None:
        """Leave package and final submit controls to the user throughout manual completion."""
        stage = initial_stage
        input_seen = False
        clicked:list[str] = []

        async def execute(script:str) -> Any:
            """Simulate editor, package, and confirmation states around manual completion."""
            nonlocal stage
            if input_seen and outcome == "page-timeout":
                raise TimeoutError("manual completion state unavailable")
            if stage == "package":
                dom = _basis_dom(selected = False)
                pathname = "/p-anzeigentypauswahl/216/123/draft"
            elif stage == "editor":
                dom = _editor_dom()
                pathname = "/p-anzeige-aufgeben-schritt2.html"
            else:
                dom = {"tag": "body", "children": []}
                pathname = "/p-anzeige-aufgeben-bestaetigung.html"
            result = _run_dom_script(script, dom, pathname = pathname)
            clicked.extend(result["clicks"])
            if "Nächster Schritt" in result["clicks"]:
                stage = "package"
            return result["result"]

        async def await_condition(condition:Any, **_:Any) -> Any:
            """Poll the simulated page state with a bounded number of attempts."""
            for _attempt in range(3):
                result = await condition()
                if result:
                    return result
            raise TimeoutError("fixture condition was not met")

        async def manual_completion(*_:object) -> str:
            """Simulate user completion, unfinished drafts, or interactive input failures."""
            nonlocal stage, input_seen
            assert stage == "package"
            assert "Basis Paket" not in clicked
            assert "Anzeige aufgeben" not in clicked
            input_seen = True
            if outcome == "eof":
                raise EOFError("interactive input unavailable")
            if outcome == "input-error":
                raise OSError("interactive input unavailable")
            stage = {"still-package": "package", "still-editor": "editor"}.get(outcome, "done")
            return ""

        with (
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, side_effect = execute),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = await_condition),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.publishing_submission.ainput", new_callable = AsyncMock,
                  side_effect = manual_completion) as manual_input,
        ):
            if outcome == "completed":
                await publishing_submission._click_submit_button(test_bot, package_selection = "MANUAL")
            else:
                with pytest.raises(PublishSubmissionUncertainError):
                    await publishing_submission._click_submit_button(test_bot, package_selection = "MANUAL")
        assert clicked == (["Nächster Schritt"] if initial_stage == "editor" else [])
        manual_input.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_manual_without_terminal_stops_before_next_step(
        self, test_bot:KleinanzeigenBot, monkeypatch:pytest.MonkeyPatch,
    ) -> None:
        """Unattended MANUAL runs must not click "Nächster Schritt", which would leave an unfinished draft (#1341)."""
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
        clicked:list[str] = []

        async def execute(script:str) -> Any:
            result = _run_dom_script(script, _editor_dom(), pathname = "/p-anzeige-aufgeben-schritt2.html")
            clicked.extend(result["clicks"])
            return result["result"]

        async def await_condition(condition:Any, **_:Any) -> Any:
            return await condition()

        with (
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, side_effect = execute),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = await_condition),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            pytest.raises(ManualPackageSelectionRequiredError),
        ):
            await publishing_submission._click_submit_button(test_bot, package_selection = "MANUAL")
        assert clicked == []

    @pytest.mark.asyncio
    async def test_incomplete_manual_submission_prevents_draft_id_recovery(self, test_bot:KleinanzeigenBot) -> None:
        """Prevent draft ID recovery when manual publication remains unconfirmed."""
        with (
            patch("kleinanzeigen_bot.captcha_flow.check_and_wait_for_captcha", new_callable = AsyncMock),
            patch.object(test_bot, "web_set_input_value", new_callable = AsyncMock),
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, return_value = ""),
            patch("kleinanzeigen_bot.publishing_submission._click_submit_button", new_callable = AsyncMock,
                  side_effect = PublishSubmissionUncertainError("manual submission was not confirmed")),
            patch("kleinanzeigen_bot.publishing_submission._try_recover_ad_id_from_redirect", new_callable = AsyncMock) as redirect,
            patch("kleinanzeigen_bot.publishing_submission._try_recover_ad_id_from_published_ads", new_callable = AsyncMock) as published,
            pytest.raises(PublishSubmissionUncertainError),
        ):
            await publishing_submission.submit_and_confirm_ad(
                test_bot, "test.yaml", _make_min_ad(), AdUpdateStrategy.REPLACE,
                captcha_config = test_bot.config.captcha, root_url = test_bot.root_url,
                package_selection = "MANUAL", known_published_ad_ids = frozenset({100}),
            )
        redirect.assert_not_awaited()
        published.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("result", [None, 0, "false"])
    async def test_manual_completion_requires_an_explicit_non_editor_state(
        self, test_bot:KleinanzeigenBot, result:object,
    ) -> None:
        """Reject ambiguous browser state after the user confirms manual completion."""
        with (
            patch("kleinanzeigen_bot.publishing_submission.ainput", new_callable = AsyncMock, return_value = ""),
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, return_value = result),
            pytest.raises(PublishSubmissionUncertainError),
        ):
            await publishing_submission._wait_for_manual_package_submission(test_bot)

    @pytest.mark.asyncio
    async def test_manual_completion_browser_disconnect_is_uncertain(self, test_bot:KleinanzeigenBot) -> None:
        """Keep manual completion uncertain when the browser connection is lost."""
        with (
            patch("kleinanzeigen_bot.publishing_submission.ainput", new_callable = AsyncMock, return_value = ""),
            patch.object(test_bot, "web_execute", new_callable = AsyncMock,
                         side_effect = ProtocolException(MagicMock(), "connection lost", 0)),
            pytest.raises(PublishSubmissionUncertainError),
        ):
            await publishing_submission._wait_for_manual_package_submission(test_bot)
