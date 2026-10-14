# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Run the submit/validation page scripts in Node.js against a small synthetic DOM.

These tests start a Node.js process (``nodejs-wheel-binaries``) per script, so they are
integration tests. The DOM below is a hand-written fake that only models the browser
semantics the scripts use; extend it when a script relies on more.
"""

import json
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nodejs_wheel import node
from nodriver.core.connection import ProtocolException

from kleinanzeigen_bot import publishing_submission
from kleinanzeigen_bot.app import KleinanzeigenBot
from kleinanzeigen_bot.utils.exceptions import PublishSubmissionUncertainError

pytestmark = pytest.mark.itest


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
