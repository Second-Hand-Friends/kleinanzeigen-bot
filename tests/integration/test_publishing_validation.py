# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Exercise form-validation selectors in a real browser on offline HTML."""

import json

import pytest

from kleinanzeigen_bot import publishing_submission
from kleinanzeigen_bot.utils.web_scraping_mixin import WebScrapingMixin

# the browser is shared across the session, see conftest.py
pytestmark = [pytest.mark.itest, pytest.mark.slow, pytest.mark.asyncio(loop_scope = "session")]


async def test_visible_field_errors_are_labelled_without_reading_values(web:WebScrapingMixin) -> None:
    """Cover native, ARIA and repeated category error IDs from the new form."""
    html = """<!doctype html><html lang="en"><body>
        <div id="outside-error">Outside the ad form</div>
        <form>
            <label for="ad-title">Title</label><input id="ad-title" value="Private title">
            <div>
                <label for="art">Art</label>
                <div><input type="hidden" name="attributeMap[art]" value="private-category">
                    <button id="art" type="button">Private category selection</button>
                    <div><div id="attribute-error">Bitte gib einen Wert ein.</div></div>
                </div>
            </div>
            <div>
                <label for="condition">Zustand</label>
                <button id="condition" type="button">Private condition selection</button>
                <div id="attribute-error">Bitte gib einen Wert ein.</div>
            </div>
            <div>
                <label for="brand">Marke</label>
                <input id="brand" value="private-brand" aria-invalid="true" aria-describedby="brand-description">
                <div id="brand-description" class="text-critical">Bitte gib einen Wert ein.</div>
            </div>
            <div>
                <label for="price">Preis</label>
                <input id="price" value="private-price" aria-invalid="true"
                    aria-errormessage="price-error" aria-describedby="price-hint">
                <div id="price-hint">This is only a hint</div>
                <div id="price-error">Enter a valid price.</div>
            </div>
            <div>
                <label for="native">Required field</label>
                <input id="native" required aria-describedby="native-hint">
                <div id="native-hint">This is only another hint</div>
            </div>
            <div hidden><input required><div id="hidden-error">Hidden error</div></div>
            <div style="visibility:hidden" id="invisible-error">Invisible error</div>
            <input hidden aria-invalid="true" aria-errormessage="outside-error">
            <input aria-invalid="true" aria-describedby="hint-only">
            <div id="hint-only">A hint is not an error message</div>
        </form>
        </body></html>"""

    # Snap Chromium cannot read the host's /tmp fixture through a file URL.
    # Populate an isolated blank document so the test stays fully offline.
    await web.web_execute(f"""(() => {{
        document.open();
        document.write({json.dumps(html)});
        document.close();
    }})()""")
    await web.web_await(
        lambda: web.web_execute("document.getElementById('native') !== null"),
        timeout = 10,
        timeout_error_message = "Offline validation fixture did not load",
    )
    native_message = await web.web_execute("document.getElementById('native').validationMessage")
    errors = await publishing_submission._get_form_validation_errors(web)
    assert set(errors) == {
        "Art: Bitte gib einen Wert ein.",
        "Zustand: Bitte gib einen Wert ein.",
        "Marke: Bitte gib einen Wert ein.",
        "Preis: Enter a valid price.",
        f"Required field: {native_message}",
    }
    assert len(errors) == 5

    # Correcting the fields leaves descriptions in place, but no errors.
    await web.web_execute("""(() => {
        document.querySelectorAll('[id$="-error"], #brand-description').forEach(e => e.remove());
        document.querySelectorAll('[aria-invalid]').forEach(e => e.removeAttribute('aria-invalid'));
        document.getElementById('native').value = 'valid';
    })()""")
    assert await publishing_submission._get_form_validation_errors(web) == []

    # A confirmation/dashboard page has no ad form, even if other errors exist.
    await web.web_execute("document.querySelector('form').remove()")
    assert await publishing_submission._get_form_validation_errors(web) == []
