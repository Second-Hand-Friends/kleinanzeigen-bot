# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

"""Publishing form sections."""

from gettext import gettext as _
from typing import Any, Final, cast

from .ad_form_helpers import xpath_literal
from .utils import loggers as _loggers
from .utils.exceptions import CategoryResolutionError
from .utils.misc import ensure
from .utils.web_scraping_mixin import By, Element, WebScrapingMixin

LOG:Final[_loggers.Logger] = _loggers.get_logger(__name__)


async def set_category(web:WebScrapingMixin, *, root_url:str, category:str | None, ad_file:str) -> None:
    # click on something to trigger automatic category detection
    await web.web_click(By.ID, "ad-description")

    is_category_auto_selected = False
    category_path_elem = await web.web_probe(By.ID, "ad-category-path")
    if category_path_elem and await web._extract_visible_text(category_path_elem):  # noqa: SLF001 - WebScrapingMixin category marker helper
        is_category_auto_selected = True

    if category:
        await web.web_sleep()  # workaround for https://github.com/Second-Hand-Friends/kleinanzeigen-bot/issues/39
        await web.web_click(By.XPATH, "//a[contains(., 'Kategorie')] | //button[contains(., 'Kategorie')]")
        await web.web_find(By.XPATH, "//button[contains(., 'Weiter')]")

        category_url = f"{root_url}/p-kategorie-aendern.html#?path={category}"
        await web.web_open(category_url)
        await web.web_click(By.XPATH, "//button[contains(., 'Weiter')]")

        # When the configured path cannot be resolved (e.g. outdated or ambiguous),
        # the site falls back to a React category-suggestion radio picker. Handle it
        # by matching a path segment against one of the offered suggestions.
        await resolve_category_suggestions(web, category)
    else:
        ensure(is_category_auto_selected, f"No category specified in [{ad_file}] and automatic category detection failed")


async def resolve_category_suggestions(web:WebScrapingMixin, category:str) -> None:
    """Handle Kleinanzeigen's post-redesign category-suggestion picker.

    If ``fieldset#ad-category-picker`` is rendered after the category change
    flow (because the configured path could not be resolved), try to click
    the suggestion whose radio ``value`` matches one of the segments of
    ``category`` (deepest first). The radio input is ``sr-only``, so clicks
    go on the associated ``<label for="...">``.

    If the picker shell is present but radios have not rendered yet, retry
    once after a short pause and then raise ``TimeoutError`` so the caller
    can treat it as a retryable pre-submit failure. Raises
    ``CategoryResolutionError`` with the list of offered suggestions if none
    of the segments match — surfaces an actionable error instead of letting
    the submit retry loop trip the duplicate-guard.
    """
    picker_timeout = web.timeout("quick_dom")
    picker = await web.web_probe(By.ID, "ad-category-picker", timeout = picker_timeout)
    if picker is None:
        return

    radio_selector = "#ad-category-picker input[type='radio'][name='category-suggestions']"
    radio_by_value:dict[str, Element] = {}
    for attempt in range(2):
        try:
            radios = await web.web_find_all(By.CSS_SELECTOR, radio_selector, timeout = picker_timeout)
        except TimeoutError:
            radios = []

        radio_by_value = {}
        for radio in radios:
            value = str(cast(Any, radio.attrs.get("value")) or "").strip()
            if value and value not in radio_by_value:
                radio_by_value[value] = radio

        if radio_by_value:
            break

        if attempt == 0:
            await web.web_sleep(200, 350)

    if not radio_by_value:
        raise TimeoutError(_("Category suggestion picker element found but no radio suggestions rendered after waiting."))

    # Try deepest-first segments so "73/76/sachbuecher" first probes the leaf, then 76, then 73.
    for segment in (seg.strip() for seg in reversed(category.split("/")) if seg.strip()):
        radio = radio_by_value.get(segment)
        if radio is None:
            continue
        radio_id = str(cast(Any, radio.attrs.get("id")) or "")
        try:
            if radio_id:
                await web.web_click(
                    By.XPATH,
                    f"//fieldset[@id='ad-category-picker']//label[@for={xpath_literal(radio_id)}]",
                    timeout = picker_timeout,
                )
            else:
                await radio.click()
        except TimeoutError:
            await radio.click()
        LOG.info("Category suggestion picker: selected value=%s (matched path segment).", segment)
        return

    offered = ", ".join(sorted(radio_by_value.keys())) or "(none)"
    message = _("Category suggestion picker shown, but no segment of configured path '%(category)s' matched the offered suggestions [%(offered)s]. Update the ad's 'category' to an offered ID or a valid full path.")  # noqa: E501
    raise CategoryResolutionError(message % {"category": category, "offered": offered})
