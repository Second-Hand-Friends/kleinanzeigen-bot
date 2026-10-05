# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Exercise package selection in a real browser against a loopback-only site."""

import sys
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any, cast

import pytest

from kleinanzeigen_bot import publishing_submission
from kleinanzeigen_bot.model.ad_model import Ad, AdUpdateStrategy
from kleinanzeigen_bot.model.config_model import Config
from kleinanzeigen_bot.utils.exceptions import AdFormValidationError, PublishSubmissionUncertainError
from kleinanzeigen_bot.utils.web_scraping_mixin import WebScrapingMixin

pytestmark = [pytest.mark.itest, pytest.mark.slow, pytest.mark.asyncio(loop_scope = "session")]

_PACKAGE_HTML = """<!doctype html>
<html lang="de"><meta charset="utf-8"><title>Local package selection test</title>
<style>article {border: 1px solid black; padding: 20px; display: inline-block; vertical-align: top}
button {padding: 12px} main {padding: 20px}</style><body><main id="page"></main>
<script>
const options = new URLSearchParams(window.location.search);
window.testState = {nextClicks: 0, basisClicks: 0, plusClicks: 0, submitClicks: 0, submitted: null};
const packagePath = '/p-anzeigentypauswahl/216/local-draft/test';
function showPackages() {
    history.replaceState({}, '', packagePath);
    document.getElementById('page').innerHTML = `
        <h1>Wähle nun dein Paket aus und veröffentliche deine Anzeige.</h1>
        <section role="radiogroup">
          <article>
            <button id="basis" role="radio" aria-checked="${options.get('selected') === 'basis'}">
              <span><strong>Basis Paket</strong></span>
            </button>
            <p>Kostenlos inserieren – ohne zusätzliche Vorteile.</p>
            <h2>Kostenlos</h2>
            ${options.has('fee') ? '<p>Zusätzliche Gebühr 9,99 €</p>' : ''}
            <h3>Was beinhaltet dieses Paket?</h3><p>60 Tage Anzeigendauer.</p>
          </article>
          <article>
            <p>Empfohlen</p>
            <button id="plus" role="radio" aria-checked="${options.get('selected') === 'plus'}">
              <span><strong>Plus Paket</strong></span>
            </button>
            <p>Mehr Aufmerksamkeit &amp; unbegrenzte Anzeigendauer.</p>
            <p>11,99 € inkl. MwSt. / 14 Tage</p><p>Jederzeit kündbar</p>
          </article>
        </section>
        <button id="back">Zurück</button>
        <button id="submit"><span>${options.get('selected') === 'none' ? 'Weiter' : 'Anzeige aufgeben'}</span></button>`;
    for (const selected of ['basis', 'plus']) {
        document.getElementById(selected).addEventListener('click', () => {
            window.testState[selected + 'Clicks']++;
            // React-like asynchronous selection: the bot must wait for state.
            setTimeout(() => {
                for (const id of ['basis', 'plus']) {
                    document.getElementById(id).setAttribute('aria-checked', String(id === selected));
                }
                document.getElementById('submit').firstElementChild.textContent = 'Anzeige aufgeben';
            }, 75);
        });
    }
    document.getElementById('submit').addEventListener('click', () => {
        window.testState.submitClicks++;
        window.testState.submitted = {
            basis: document.getElementById('basis').getAttribute('aria-checked') === 'true',
            plus: document.getElementById('plus').getAttribute('aria-checked') === 'true'
        };
        history.replaceState({}, '', '/p-anzeige-aufgeben-bestaetigung.html?adId=123456789');
        document.getElementById('page').innerHTML = '<h1>Geschafft!</h1><a href="/m-meine-anzeigen.html">Zu meinen Anzeigen</a>';
    });
}
if (window.location.pathname === '/p-anzeige-aufgeben-schritt2.html') {
    document.getElementById('page').innerHTML = `
        <label for="ad-title">Titel</label><input id="ad-title" value="Local car">
        <button id="next"><span>Nächster Schritt</span></button>`;
    document.getElementById('next').addEventListener('click', () => {
        window.testState.nextClicks++;
        setTimeout(showPackages, 75);
    });
} else {
    showPackages();
}
</script></body></html>
"""


class _PackageFixtureHandler(BaseHTTPRequestHandler):
    """Serve only the inert fixture from a loopback server."""

    def do_GET(self) -> None:  # noqa: N802 - HTTPServer handler contract
        """Serve the in-memory package-selection page to the local browser."""
        body = _PACKAGE_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format:str, *args:object) -> None:  # noqa: A002 - HTTPServer handler contract
        """Avoid noisy HTTP request logging."""


@pytest.fixture(scope = "module")
def package_site() -> Iterator[str]:
    """Bind an ephemeral port; never access the public listing service."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _PackageFixtureHandler)
    thread = Thread(target = server.serve_forever, daemon = True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout = 5)


async def _open_fixture(web:WebScrapingMixin, site:str, *, editor:bool, selected:str, fee:bool = False) -> None:
    """Open the local editor or package fixture with the requested selection and fee."""
    path = "/p-anzeige-aufgeben-schritt2.html" if editor else "/p-anzeigentypauswahl/216/local-draft/test"
    query = f"?selected={selected}" + ("&fee=1" if fee else "")
    await web.web_open(site + path + query)
    await web.web_await(lambda: web.web_execute("Boolean(window.testState)"))


def _config(selection:str | None = None) -> Config:
    """Build dummy credentials and optional package configuration for the local fixture."""
    raw_config:dict[str, Any] = {"login": {"username": "local-test-user", "password": "local-test-password"}}
    if selection is not None:
        raw_config["publishing"] = {"package_selection": selection}
    return Config.model_validate(raw_config)


@pytest.mark.parametrize(("editor", "selected"), [(False, "basis"), (False, "plus"), (True, "basis"), (True, "plus"), (False, "none")])
async def test_basis_publishes_once_after_confirmed_free_selection(
    web:WebScrapingMixin,
    package_site:str,
    editor:bool,
    selected:str,
    record_testsuite_property:Callable[[str, object], None],
) -> None:
    """Publish the verified free Basis package once from either local entry point."""
    await _open_fixture(web, package_site, editor = editor, selected = selected)
    record_testsuite_property("browser_user_agent", await web.web_execute("navigator.userAgent"))

    await publishing_submission._click_submit_button(web, package_selection = _config("BASIS").publishing.package_selection)

    state = cast(dict[str, Any], await web.web_execute("window.testState"))
    assert state == {
        "nextClicks": int(editor),
        "basisClicks": int(selected != "basis"),
        "plusClicks": 0,
        "submitClicks": 1,
        "submitted": {"basis": True, "plus": False},
    }
    assert await web.web_execute("window.location.pathname") == "/p-anzeige-aufgeben-bestaetigung.html"
    record_testsuite_property(f"click_counts_editor_{editor}_{selected}", state)


async def test_basis_with_extra_fee_is_not_submitted(web:WebScrapingMixin, package_site:str) -> None:
    """Refuse all package and submit clicks when the Basis fixture includes an extra fee."""
    await _open_fixture(web, package_site, editor = False, selected = "plus", fee = True)

    with pytest.raises(AdFormValidationError, match = "free Basis package could not be verified"):
        await publishing_submission._click_submit_button(web, package_selection = _config("BASIS").publishing.package_selection)

    state = cast(dict[str, Any], await web.web_execute("window.testState"))
    assert state["basisClicks"] == 0
    assert state["plusClicks"] == 0
    assert state["submitClicks"] == 0
    assert await web.web_execute("window.testState.submitted === null") is True


async def test_manual_waits_for_human_to_publish(
    web:WebScrapingMixin,
    package_site:str,
    monkeypatch:pytest.MonkeyPatch,
) -> None:
    """Leave package controls untouched until the simulated user completes publication."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    await _open_fixture(web, package_site, editor = True, selected = "plus")

    async def finish_in_browser(prompt:str) -> str:
        """Simulate the user submitting the already selected package on the local fixture."""
        assert "finish publishing in the browser" in prompt
        await web.web_await(lambda: web.web_execute("window.location.pathname.startsWith('/p-anzeigentypauswahl/')"))
        state = cast(dict[str, Any], await web.web_execute("window.testState"))
        assert state["basisClicks"] == 0
        assert state["plusClicks"] == 0
        assert state["submitClicks"] == 0
        # Simulate a human confirming the already selected package on the local fixture.
        await web.web_execute("document.getElementById('submit').click()")
        return ""

    monkeypatch.setattr(publishing_submission, "ainput", finish_in_browser)
    await publishing_submission._click_submit_button(web, package_selection = _config("MANUAL").publishing.package_selection)

    state = cast(dict[str, Any], await web.web_execute("window.testState"))
    assert state["nextClicks"] == 1
    assert state["basisClicks"] == 0
    assert state["plusClicks"] == 0
    assert state["submitClicks"] == 1
    assert state["submitted"] == {"basis": False, "plus": True}


@pytest.mark.parametrize("selection", [None, "MANUAL"])
async def test_manual_unfinished_draft_is_not_confirmed(
    web:WebScrapingMixin,
    package_site:str,
    monkeypatch:pytest.MonkeyPatch,
    selection:str | None,
) -> None:
    """Keep unfinished manual drafts unconfirmed without selecting or submitting a package."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    await _open_fixture(web, package_site, editor = False, selected = "plus")

    async def press_enter_without_publishing(_prompt:str) -> str:
        """Simulate confirming the prompt while leaving the local draft unpublished."""
        return ""

    monkeypatch.setattr(publishing_submission, "ainput", press_enter_without_publishing)
    with pytest.raises(PublishSubmissionUncertainError, match = "Manual package publication was not confirmed"):
        await publishing_submission._click_submit_button(web, package_selection = _config(selection).publishing.package_selection)

    state = cast(dict[str, Any], await web.web_execute("window.testState"))
    assert state["basisClicks"] == 0
    assert state["plusClicks"] == 0
    assert state["submitClicks"] == 0
    assert await web.web_execute("window.testState.submitted === null") is True


async def test_full_submission_confirms_the_new_id_after_basis_selection(web:WebScrapingMixin, package_site:str) -> None:
    """Keep captcha detection, title update, final click and ID confirmation real."""
    await _open_fixture(web, package_site, editor = True, selected = "plus")
    config = _config("BASIS")
    ad = Ad.model_validate({
        "title": "Local car listing",
        "description": "Local integration test listing.",
        "type": "OFFER",
        "price_type": "FIXED",
        "price": 10,
        "sell_directly": False,
        "shipping_type": "NOT_APPLICABLE",
        "category": "216",
        "special_attributes": {},
        "images": ["local-test.jpg"],
        "active": True,
        "republication_interval": 7,
        "contact": {"name": "Local Test", "zipcode": "12345", "location": "Local Town"},
    })

    ad_id = await publishing_submission.submit_and_confirm_ad(
        web,
        "local-test.yaml",
        ad,
        AdUpdateStrategy.REPLACE,
        captcha_config = config.captcha,
        root_url = package_site,
        package_selection = config.publishing.package_selection,
    )

    assert ad_id == 123456789
    state = cast(dict[str, Any], await web.web_execute("window.testState"))
    assert state == {
        "nextClicks": 1,
        "basisClicks": 1,
        "plusClicks": 0,
        "submitClicks": 1,
        "submitted": {"basis": True, "plus": False},
    }
