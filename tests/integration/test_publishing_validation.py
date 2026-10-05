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


@pytest.mark.parametrize("layout", ["editor-container", "shared-app-root"])
async def test_formless_editor_errors_exclude_unrelated_application_controls(
    web:WebScrapingMixin, layout:str,
) -> None:
    """Use synthetic React-like markup, never captured account HTML."""
    title = '<div><label for="ad-title">Title</label><input id="ad-title" value="Synthetic listing"></div>'
    fields = """
        <div><label for="ad-description">Description</label><textarea id="ad-description">Synthetic description</textarea></div>
        <div><label for="autos.km">Mileage</label>
            <input id="autos.km" name="attributeMap[autos.km]" aria-invalid="true" aria-describedby="km-status">
            <p id="km-status" role="status">Enter mileage.</p></div>
        <div><label for="autos.brand">Brand</label>
            <input type="hidden" name="attributeMap[autos.brand]">
            <button id="autos.brand" role="combobox">Choose brand</button>
            <div><p id="attribute-error">Choose a brand.</p></div></div>
    """
    unrelated = """
        <section><label for="newsletter-email">Newsletter</label>
            <input id="newsletter-email" type="email" required aria-invalid="true" aria-errormessage="newsletter-error">
            <p id="newsletter-error" role="alert">Invalid newsletter email.</p></section>
        <div role="alert" id="notification-error">An unrelated application notification.</div>
        <header><input id="global-search" required></header>
    """
    editor = f'<section id="editor">{title}{fields}</section>{unrelated}' if layout == "editor-container" else f"{title}{unrelated}{fields}"
    html = f'<!doctype html><html lang="en"><body><div id="application-root">{editor}</div></body></html>'
    await web.web_execute(f"""(() => {{
        document.open(); document.write({json.dumps(html)}); document.close();
    }})()""")
    assert await web.web_execute("document.querySelector('form, main, [role=main]') === null") is True
    assert await web.web_execute("document.getElementById('newsletter-email').validity.valid") is False
    assert await publishing_submission._get_form_validation_errors(web) == ["Mileage: Enter mileage.", "Brand: Choose a brand."]

    await web.web_execute("""(() => {
        document.getElementById('autos.km').removeAttribute('aria-invalid');
        document.getElementById('attribute-error').remove();
    })()""")
    assert await publishing_submission._get_form_validation_errors(web) == []
    # An unrelated invalid input persists after correcting all ad fields.
    assert await web.web_execute("document.getElementById('newsletter-email').validity.valid") is False


async def test_native_form_summary_stays_scoped_to_its_form(web:WebScrapingMixin) -> None:
    """An explicit form rejection summary must terminate confirmation polling."""
    html = """<!doctype html><html><body>
        <form><input id="ad-title" value="Synthetic listing">
            <div id="ad-form-error">Bitte korrigiere die Anzeige.</div></form>
        <div id="notification-error" role="alert">Unrelated application error.</div>
    </body></html>"""
    await web.web_execute(f"""(() => {{
        document.open(); document.write({json.dumps(html)}); document.close();
    }})()""")
    assert await publishing_submission._get_form_validation_errors(web) == ["Bitte korrigiere die Anzeige."]
    await web.web_execute("document.getElementById('ad-form-error').remove()")
    assert await publishing_submission._get_form_validation_errors(web) == []


@pytest.mark.parametrize(("backing_key", "button_id"), [
    ("autos.marke_s+autos.model_s", "autos.marke_s"),
    ("autos.marke_s+autos.model_s", "vehicle-brand-control"),
    ("autos.marke_s", "vehicle-brand-control"),
])
async def test_compound_attribute_combobox_remains_an_ad_field(
    web:WebScrapingMixin, backing_key:str, button_id:str,
) -> None:
    """Associate a field-local backing input, without adopting sidebar controls."""
    html = f"""<!doctype html><html><body><div id="application-root">
        <input id="ad-title" value="Synthetic listing">
        <section><input type="hidden" name="attributeMap[{backing_key}]">
            <label for="{button_id}">Marke</label>
            <button id="{button_id}" role="combobox" aria-invalid="true" aria-describedby="marke-message">Choose brand</button>
            <p id="marke-message" role="status">Bitte waehle eine Marke.</p></section>
        <aside><label for="foreign-control">Foreign sidebar control</label>
            <button id="foreign-control" role="combobox" aria-invalid="true" aria-describedby="foreign-error">Unrelated selection</button>
            <p id="foreign-error" role="status">Unrelated sidebar error.</p></aside>
        <div id="notification-error" role="alert">Unrelated application notification.</div>
    </div></body></html>"""
    await web.web_execute(f"""(() => {{
        document.open(); document.write({json.dumps(html)}); document.close();
    }})()""")
    assert await publishing_submission._get_form_validation_errors(web) == ["Marke: Bitte waehle eine Marke."]
    await web.web_execute(f"document.getElementById({json.dumps(button_id)}).removeAttribute('aria-invalid')")
    assert await publishing_submission._get_form_validation_errors(web) == []
