# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

"""Publishing form sections."""

from gettext import gettext as _
from typing import Any, Final, cast

from .ad_description import get_ad_description
from .ad_form_helpers import get_marker_value, location_matches_target, xpath_literal
from .model.ad_model import Ad, Contact
from .model.config_model import AdDefaults
from .utils import loggers as _loggers
from .utils.exceptions import CategoryResolutionError
from .utils.i18n import pluralize
from .utils.misc import ensure
from .utils.web_scraping_mixin import By, Element, Is, WebScrapingMixin

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


async def city_option_text(web:WebScrapingMixin, option:Element) -> str:
    text = str(getattr(option, "text", "") or "").strip()
    if text:
        return text
    try:
        return (await web._extract_visible_text(option)).strip()  # noqa: SLF001 - WebScrapingMixin visible text helper
    except TimeoutError:
        return ""


async def read_city_selection_text(web:WebScrapingMixin) -> str | None:
    city_timeout = web.timeout("default")
    quick_dom_timeout = web.timeout("quick_dom")
    try:
        city_element = await web.web_find(By.ID, "ad-city", timeout = city_timeout)
    except TimeoutError:
        return None
    if city_element is None:
        return None

    if city_element.local_name == "input":
        live_value = await city_element.apply("(elem) => (elem.value || '').trim()")
        if isinstance(live_value, str) and live_value.strip():
            return live_value

    try:
        selected_text = await web.web_text(By.ID, "ad-city-selected-option", timeout = quick_dom_timeout)
        if selected_text:
            return selected_text
    except TimeoutError:
        # #ad-city-selected-option may not exist in all DOM states; fall through to textContent
        pass

    live_text = await city_element.apply("(elem) => (elem.textContent || '').trim()")
    if isinstance(live_text, str) and live_text.strip():
        return live_text

    try:
        selected_text = await web.web_text(By.ID, "ad-city", timeout = quick_dom_timeout)
        if selected_text:
            return selected_text
    except TimeoutError:
        return None
    return None


async def select_city_combobox_option(web:WebScrapingMixin, target:str) -> None:
    quick_dom_timeout = web.timeout("quick_dom")
    city_flow_timeout = web.timeout("default")

    await web.web_click(By.ID, "ad-city", timeout = quick_dom_timeout)
    city_element = await web.web_find(By.ID, "ad-city", timeout = quick_dom_timeout)
    city_attrs = getattr(city_element, "attrs", None)
    listbox_id_raw = None
    if city_attrs is not None:
        listbox_id_raw = city_attrs.get("aria-controls") if hasattr(city_attrs, "get") else getattr(city_attrs, "aria-controls", None)
    listbox_id = next((candidate for candidate in str(listbox_id_raw or "").split() if candidate.strip()), "")
    if not listbox_id:
        listbox_id = "ad-city-menu"

    listbox_id_css = listbox_id.replace("\\", "\\\\").replace('"', '\\"')
    listbox_scope = f'[id="{listbox_id_css}"]'
    option_selector = (
        f"{listbox_scope} [role='option'], "
        f"{listbox_scope} li[aria-selected='true'], {listbox_scope} li[aria-selected='false'], "
        f"{listbox_scope} button[aria-selected='true'], {listbox_scope} button[aria-selected='false']"
    )

    candidates:list[Element] = []

    async def _options_available() -> bool:
        nonlocal candidates
        try:
            candidates = await web.web_find_all(By.CSS_SELECTOR, option_selector, timeout = quick_dom_timeout)
        except TimeoutError:
            candidates = []
        return bool(candidates)

    try:
        await web.web_await(_options_available, timeout = city_flow_timeout)
    except TimeoutError as ex:
        raise TimeoutError(_("City combobox options did not load for location: %s") % target) from ex

    def normalize(value:str) -> str:
        return " ".join(value.split()).casefold()

    target_norm = normalize(target)
    option_entries = [(candidate, normalize(await city_option_text(web, candidate))) for candidate in candidates]

    exact_match = next((entry[0] for entry in option_entries if entry[1] == target_norm), None)
    city_matches:list[Element] = []
    prefix_matches:list[Element] = []
    if " - " not in target_norm:
        city_matches = [entry[0] for entry in option_entries if entry[1] and entry[1].rsplit(" - ", maxsplit = 1)[-1] == target_norm]
        prefix_matches = [entry[0] for entry in option_entries if entry[1].startswith(f"{target_norm} - ")]

    if exact_match is None and len(city_matches) > 1:
        raise TimeoutError(_("City combobox options are ambiguous for location: %s") % target)

    if exact_match is None and not city_matches and len(prefix_matches) > 1:
        raise TimeoutError(_("City combobox options are ambiguous for location: %s") % target)

    selected_option = exact_match or (city_matches[0] if city_matches else None) or (prefix_matches[0] if len(prefix_matches) == 1 else None)
    if selected_option is None:
        raise TimeoutError(_("No city combobox option matched location: %s") % target)

    await selected_option.click()

    async def _selection_converged() -> bool:
        selected_city = await read_city_selection_text(web)
        return location_matches_target(target, selected_city)

    try:
        await web.web_await(_selection_converged, timeout = city_flow_timeout)
    except TimeoutError as ex:
        raise TimeoutError(_("City selection did not converge for location: %s") % target) from ex


async def set_contact_location(web:WebScrapingMixin, location:str) -> None:
    target = location.strip()
    if not target:
        return

    selected_city = await read_city_selection_text(web)
    if location_matches_target(target, selected_city):
        return

    city_timeout = web.timeout("default")
    city_element = await web.web_find(By.ID, "ad-city", timeout = city_timeout)
    if city_element is None:
        raise TimeoutError(_("Unsupported city element type while setting contact location: <%s>") % "missing")
    city_tag = city_element.local_name
    city_attrs = getattr(city_element, "attrs", {}) or {}
    city_role = str(city_attrs.get("role") or "").casefold()

    # kleinanzeigen.de switched the city field to a read-only <input> whose
    # value is derived from the entered zip code; it is no longer a
    # selectable combobox. When the page already prefilled a non-empty
    # value, accept it instead of trying (and failing) to open a combobox.
    if city_tag == "input" and "readonly" in city_attrs and selected_city:
        LOG.info(
            "ad-city is a <input readonly> with value '%s' (zip-derived) - accepting instead of combobox selection.",
            selected_city,
        )
        return

    if city_tag != "button" or city_role != "combobox":
        raise TimeoutError(_("Unsupported city element type while setting contact location: <%s>") % city_tag)

    await select_city_combobox_option(web, target)


async def set_contact_fields(web:WebScrapingMixin, contact:Contact) -> None:
    #############################
    # set contact zipcode + location
    #############################
    if contact.zipcode:
        try:
            await web.web_input(By.ID, "ad-zip-code", str(contact.zipcode))
        except TimeoutError as ex:
            LOG.warning("Could not set contact zipcode: %s", ex)
            raise TimeoutError(_("Failed to set contact zipcode: %s") % contact.zipcode) from ex

        if contact.location:
            await set_contact_location(web, contact.location)

    #############################
    # set contact street
    #############################
    if contact.street:
        try:
            if await web.web_check(By.ID, "ad-street", Is.DISABLED):
                await web.web_click(By.ID, "ad-address-visibility")
                await web.web_sleep()
            await web.web_set_input_value("ad-street", contact.street)
        except TimeoutError:
            LOG.warning("Could not set contact street.")

    #############################
    # set contact name
    #############################
    if contact.name:
        try:
            if not await web.web_check(By.ID, "ad-name", Is.READONLY):
                await web.web_set_input_value("ad-name", contact.name)
        except TimeoutError:
            LOG.warning("Could not set contact name.")

    #############################
    # set contact phone
    #############################
    if contact.phone:
        phone_elem = await web.web_probe(By.ID, "ad-phone", timeout = web.timeout("quick_dom"))
        if phone_elem is None:
            LOG.info(
                "Phone number field not present on page. This is expected for many private accounts; commercial accounts may still support phone numbers."
            )
        else:
            try:
                if await web.web_check(By.ID, "ad-phone", Is.DISABLED, timeout = web.timeout("quick_dom")):
                    await web.web_click(By.ID, "ad-phone-visibility", timeout = web.timeout("quick_dom"))
                    await web.web_sleep()
                await web.web_set_input_value("ad-phone", contact.phone)
            except TimeoutError as ex:
                LOG.warning("Could not set contact phone despite visible phone field: %s", ex)


async def fill_image_section(web:WebScrapingMixin, ad_cfg:Ad) -> None:
    #############################
    # delete previous images to ensure a clean slate
    # (needed for MODIFY because we don't know which changed,
    #  and as defensive cleanup when the form is pre-populated with thumbnails)
    #############################
    remove_button_selector = "button[aria-label='Bild entfernen']"
    hidden_marker_selector = "input[name^='adImages'][name$='.url']"
    quick_dom = web.timeout("quick_dom")
    removed_count = 0

    try:
        existing_markers = await web._web_find_all_once(By.CSS_SELECTOR, hidden_marker_selector, quick_dom)  # noqa: SLF001 - WebScrapingMixin fast marker probe
        existing_image_count = sum(1 for marker in existing_markers if get_marker_value(marker))
    except TimeoutError:
        existing_image_count = 0

    if existing_image_count:
        for idx in range(existing_image_count):
            remove_btn = await web.web_probe(By.CSS_SELECTOR, remove_button_selector, timeout = quick_dom)
            if remove_btn is None:
                raise TimeoutError(
                    _("Image cleanup failed before upload. Removed %(removed)d of %(total)d existing images.")
                    % {"removed": idx, "total": existing_image_count}
                )
            await remove_btn.click()
            removed_count += 1
            await web.web_sleep(300, 500)

    if removed_count > 0:
        LOG.info(" -> removed %d existing image(s) before upload", removed_count)
        # Let async DOM updates settle before capturing hidden-marker baseline
        await web.web_sleep(200, 350)

    #############################
    # upload images
    #############################
    await upload_images(web, ad_cfg)


async def upload_images(web:WebScrapingMixin, ad_cfg:Ad) -> None:
    if not ad_cfg.images:
        return

    LOG.info(" -> found %s", pluralize("image", ad_cfg.images))
    hidden_marker_selector = "input[name^='adImages'][name$='.url']"
    quick_dom_timeout = web.timeout("quick_dom")

    # Capture marker baseline before this upload attempt to avoid counting stale values
    baseline_marker_count = 0
    try:
        baseline_markers = await web._web_find_all_once(By.CSS_SELECTOR, hidden_marker_selector, quick_dom_timeout)  # noqa: SLF001 - WebScrapingMixin fast marker probe
        baseline_marker_count = sum(1 for marker in baseline_markers if get_marker_value(marker))
    except TimeoutError:
        baseline_marker_count = 0

    if baseline_marker_count:
        LOG.debug(" -> detected %d pre-existing image marker(s) before upload", baseline_marker_count)

    total_images = len(ad_cfg.images)
    for index, image in enumerate(ad_cfg.images, start = 1):
        image_upload:Element = await web.web_find(By.CSS_SELECTOR, "input[type=file]")
        LOG.info(" -> uploading image %s/%s [%s]", index, total_images, image)
        await image_upload.send_file(image)
        await web.web_sleep()

    # Wait for all images to be processed
    expected_count = len(ad_cfg.images)
    LOG.info(" -> waiting for %s to be processed...", pluralize("image", ad_cfg.images))

    async def count_processed_images() -> int:
        try:
            markers = await web._web_find_all_once(By.CSS_SELECTOR, hidden_marker_selector, quick_dom_timeout)  # noqa: SLF001 - WebScrapingMixin fast marker probe
            marker_count = sum(1 for marker in markers if get_marker_value(marker))
        except TimeoutError:
            marker_count = 0

        return max(0, marker_count - baseline_marker_count)

    async def check_thumbnails_uploaded() -> bool:
        current_count = await count_processed_images()
        if current_count < expected_count:
            LOG.debug(" -> %d of %d images processed", current_count, expected_count)
        return current_count >= expected_count

    try:
        await web.web_await(check_thumbnails_uploaded, timeout = web.timeout("image_upload"), timeout_error_message = _("Image upload timeout exceeded"))
    except TimeoutError as ex:
        # Get current count for better error message
        current_count = await count_processed_images()
        raise TimeoutError(
            _("Not all images were uploaded within timeout. Expected %(expected)d, found %(found)d processed images.")
            % {"expected": expected_count, "found": current_count}
        ) from ex

    LOG.info(" -> all images uploaded successfully")


async def set_pricing_fields(web:WebScrapingMixin, ad_cfg:Ad, ad_defaults:AdDefaults) -> None:
    """Set pricing, direct-buy, and description fields on the ad form.

    Args:
        web: The web scraping mixin instance.
        ad_cfg: The effective ad configuration.
        ad_defaults: The configured defaults (used for description affixes).
    """
    #############################
    # set price
    #############################
    price_type = ad_cfg.price_type
    if price_type != "NOT_APPLICABLE":
        price_type_options = {"FIXED": 0, "NEGOTIABLE": 1, "GIVE_AWAY": 2}
        option_idx = price_type_options.get(price_type)
        if option_idx is not None:
            try:
                await web.web_click(By.ID, "ad-price-type")
                await web.web_click(By.ID, f"ad-price-type-menu-option-{option_idx}")
            except TimeoutError as ex:
                raise TimeoutError(_("Failed to set price type '%s'") % price_type) from ex
        if ad_cfg.price is not None:
            await web.web_set_input_value("ad-price-amount", str(ad_cfg.price))

    #############################
    # set sell_directly
    #############################
    if ad_cfg.type != "WANTED":
        sell_directly = ad_cfg.sell_directly
        quick_dom = web.timeout("quick_dom")
        if ad_cfg.shipping_type == "SHIPPING":
            if sell_directly and price_type in {"FIXED", "NEGOTIABLE"}:
                buy_now_true = await web.web_probe(By.ID, "ad-buy-now-true", timeout = quick_dom)
                if buy_now_true is None:
                    LOG.warning("Direct-buy (sell_directly) is not available for the selected category. Skipping.")
                elif not await web.web_check(By.ID, "ad-buy-now-true", Is.SELECTED, timeout = quick_dom):
                    await web.web_click(By.ID, "ad-buy-now-true", timeout = quick_dom)
            else:
                buy_now_false = await web.web_probe(By.ID, "ad-buy-now-false", timeout = quick_dom)
                if buy_now_false and not await web.web_check(By.ID, "ad-buy-now-false", Is.SELECTED, timeout = quick_dom):
                    await web.web_click(By.ID, "ad-buy-now-false", timeout = quick_dom)
        else:
            # For PICKUP/other types: always opt out of buy-now if the radio exists
            buy_now_false = await web.web_probe(By.ID, "ad-buy-now-false", timeout = quick_dom)
            if buy_now_false and not await web.web_check(By.ID, "ad-buy-now-false", Is.SELECTED, timeout = quick_dom):
                await web.web_click(By.ID, "ad-buy-now-false", timeout = quick_dom)

    #############################
    # set description
    #############################
    description = get_ad_description(ad_cfg, ad_defaults, with_affixes = True)
    await web.web_set_input_value("ad-description", description)
