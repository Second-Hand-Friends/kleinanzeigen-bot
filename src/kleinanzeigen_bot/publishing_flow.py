# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import json
import re
import urllib.parse as urllib_parse
from dataclasses import replace
from gettext import gettext as _
from pathlib import Path
from typing import Any, Sequence, cast

from nodriver.core.connection import ProtocolException

from . import ad_form_helpers as _ad_form_helpers
from . import captcha_flow
from . import local_path_renaming as _local_path_renaming
from .ad_description import get_ad_description
from .model.ad_model import (
    CARRIER_CODE_BY_OPTION,
    CARRIER_CODES_BY_SIZE,
    SIZE_INFO_BY_CARRIER_CODE,
    Ad,
    AdPartial,
    AdUpdateStrategy,
    Contact,
)
from .model.config_model import AdDefaults, CaptchaConfig, Config
from .utils import dicts as _dicts
from .utils import loggers as _loggers
from .utils import misc as _misc
from .utils.exceptions import CategoryResolutionError, PublishSubmissionUncertainError
from .utils.i18n import pluralize
from .utils.misc import ainput, ensure
from .utils.web_scraping_mixin import By, Element, Is, WebScrapingMixin

LOG = _loggers.get_logger(__name__)


class PublishingFormFlow:
    def __init__(self, web:WebScrapingMixin, root_url:str, *, ad_defaults:AdDefaults) -> None:
        self._web = web
        self._root_url = root_url
        self._ad_defaults = ad_defaults

    async def fill_ad_form(
        self, ad_file:str, ad_cfg:Ad, mode:AdUpdateStrategy,
    ) -> None:
        """Fill the ad creation/edit form — category, attributes, shipping, price,
        sell-directly, description, contact, and images."""

        #############################
        # set category (before title to avoid form reset clearing title)
        #############################
        await self._set_category(ad_cfg.category, ad_file)
        await self._web.web_sleep()  # wait for category-dependent fields to render before setting attributes

        #############################
        # set special attributes
        #############################
        await self._set_special_attributes(ad_cfg)

        #############################
        # set shipping type/options/costs
        #############################
        shipping_type = ad_cfg.shipping_type
        if shipping_type != "NOT_APPLICABLE":
            if ad_cfg.type == "WANTED":
                # WANTED ads render shipping as a special-attribute combobox dropdown,
                # not as radio buttons.  Select by display text using the standard
                # DOM-based web_select_button_combobox (no React fiber internals).
                # See issue #930 for broader React fiber migration.
                display_text = _ad_form_helpers.WANTED_SHIPPING_LABELS.get(shipping_type)
                if display_text:
                    try:
                        shipping_btn = await self._web.web_find(
                            By.CSS_SELECTOR,
                            '[role="combobox"][id$=".versand"]',
                            timeout = self._web.timeout("quick_dom"),
                        )
                        btn_id = cast(str, shipping_btn.attrs.get("id"))
                        if not btn_id:
                            raise TimeoutError(_("Shipping combobox button has no id attribute"))
                        await self._web.web_select_button_combobox(btn_id, display_text)
                    except TimeoutError as ex:
                        LOG.warning("Failed to set shipping attribute for type '%s'!", shipping_type)
                        raise TimeoutError(_("Failed to set shipping attribute for type '%s'!") % shipping_type) from ex
            else:
                await self._set_shipping(ad_cfg, mode)
        else:
            LOG.debug("Shipping step skipped - reason: NOT_APPLICABLE")

        #############################
        # set price
        #############################
        price_type = ad_cfg.price_type
        if price_type != "NOT_APPLICABLE":
            price_type_options = {"FIXED": 0, "NEGOTIABLE": 1, "GIVE_AWAY": 2}
            option_idx = price_type_options.get(price_type)
            if option_idx is not None:
                try:
                    await self._web.web_click(By.ID, "ad-price-type")
                    await self._web.web_click(By.ID, f"ad-price-type-menu-option-{option_idx}")
                except TimeoutError as ex:
                    raise TimeoutError(_("Failed to set price type '%s'") % price_type) from ex
            if ad_cfg.price is not None:
                await self._web.web_set_input_value("ad-price-amount", str(ad_cfg.price))

        #############################
        # set sell_directly
        #############################
        if ad_cfg.type != "WANTED":
            sell_directly = ad_cfg.sell_directly
            quick_dom = self._web.timeout("quick_dom")
            if ad_cfg.shipping_type == "SHIPPING":
                if sell_directly and price_type in {"FIXED", "NEGOTIABLE"}:
                    buy_now_true = await self._web.web_probe(By.ID, "ad-buy-now-true", timeout = quick_dom)
                    if buy_now_true is None:
                        LOG.warning("Direct-buy (sell_directly) is not available for the selected category. Skipping.")
                    elif not await self._web.web_check(By.ID, "ad-buy-now-true", Is.SELECTED, timeout = quick_dom):
                        await self._web.web_click(By.ID, "ad-buy-now-true", timeout = quick_dom)
                else:
                    buy_now_false = await self._web.web_probe(By.ID, "ad-buy-now-false", timeout = quick_dom)
                    if buy_now_false and not await self._web.web_check(By.ID, "ad-buy-now-false", Is.SELECTED, timeout = quick_dom):
                        await self._web.web_click(By.ID, "ad-buy-now-false", timeout = quick_dom)
            else:
                # For PICKUP/other types: always opt out of buy-now if the radio exists
                buy_now_false = await self._web.web_probe(By.ID, "ad-buy-now-false", timeout = quick_dom)
                if buy_now_false and not await self._web.web_check(By.ID, "ad-buy-now-false", Is.SELECTED, timeout = quick_dom):
                    await self._web.web_click(By.ID, "ad-buy-now-false", timeout = quick_dom)

        #############################
        # set description
        #############################
        description = get_ad_description(ad_cfg, self._ad_defaults, with_affixes = True)
        await self._web.web_set_input_value("ad-description", description)

        await self._set_contact_fields(ad_cfg.contact)

        #############################
        # delete previous images to ensure a clean slate
        # (needed for MODIFY because we don't know which changed,
        #  and as defensive cleanup when the form is pre-populated with thumbnails)
        #############################
        remove_button_selector = "button[aria-label='Bild entfernen']"
        hidden_marker_selector = "input[name^='adImages'][name$='.url']"
        quick_dom = self._web.timeout("quick_dom")
        removed_count = 0

        try:
            existing_markers = await self._web.web_find_all_once(By.CSS_SELECTOR, hidden_marker_selector, quick_dom)
            existing_image_count = sum(1 for marker in existing_markers if _ad_form_helpers.get_marker_value(marker))
        except TimeoutError:
            existing_image_count = 0

        if existing_image_count:
            for idx in range(existing_image_count):
                remove_btn = await self._web.web_probe(By.CSS_SELECTOR, remove_button_selector, timeout = quick_dom)
                if remove_btn is None:
                    raise TimeoutError(
                        _("Image cleanup failed before upload. Removed %(removed)d of %(total)d existing images.")
                        % {"removed": idx, "total": existing_image_count}
                    )
                await remove_btn.click()
                removed_count += 1
                await self._web.web_sleep(300, 500)

        if removed_count > 0:
            LOG.info(" -> removed %d existing image(s) before upload", removed_count)
            # Let async DOM updates settle before capturing hidden-marker baseline
            await self._web.web_sleep(200, 350)

        #############################
        # upload images
        #############################
        await self._upload_images(ad_cfg)

    @staticmethod
    def _location_matches_target(target:str, candidate:str | None) -> bool:
        if not candidate:
            return False

        normalized_target = " ".join(target.split()).casefold()
        normalized_candidate = " ".join(candidate.split()).casefold()
        if not normalized_target or not normalized_candidate:
            return False

        if normalized_target == normalized_candidate:
            return True

        if " - " in normalized_target:
            return False

        if normalized_candidate.startswith(f"{normalized_target} - "):
            return True

        candidate_city = normalized_candidate.rsplit(" - ", maxsplit = 1)[-1]
        return normalized_target == candidate_city

    async def _city_option_text(self, option:Element) -> str:
        text = str(getattr(option, "text", "") or "").strip()
        if text:
            return text
        try:
            return (await self._web.extract_visible_text(option)).strip()
        except TimeoutError:
            return ""

    async def _read_city_selection_text(self) -> str | None:
        city_timeout = self._web.timeout("default")
        quick_dom_timeout = self._web.timeout("quick_dom")
        try:
            city_element = await self._web.web_find(By.ID, "ad-city", timeout = city_timeout)
        except TimeoutError:
            return None
        if city_element is None:
            return None

        if city_element.local_name == "input":
            live_value = await city_element.apply("(elem) => (elem.value || '').trim()")
            if isinstance(live_value, str) and live_value.strip():
                return live_value

        try:
            selected_text = await self._web.web_text(By.ID, "ad-city-selected-option", timeout = quick_dom_timeout)
            if selected_text:
                return selected_text
        except TimeoutError:
            # #ad-city-selected-option may not exist in all DOM states; fall through to textContent
            pass

        live_text = await city_element.apply("(elem) => (elem.textContent || '').trim()")
        if isinstance(live_text, str) and live_text.strip():
            return live_text

        try:
            selected_text = await self._web.web_text(By.ID, "ad-city", timeout = quick_dom_timeout)
            if selected_text:
                return selected_text
        except TimeoutError:
            return None
        return None

    async def _select_city_combobox_option(self, target:str) -> None:
        quick_dom_timeout = self._web.timeout("quick_dom")
        city_flow_timeout = self._web.timeout("default")

        await self._web.web_click(By.ID, "ad-city", timeout = quick_dom_timeout)
        city_element = await self._web.web_find(By.ID, "ad-city", timeout = quick_dom_timeout)
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
                candidates = await self._web.web_find_all(By.CSS_SELECTOR, option_selector, timeout = quick_dom_timeout)
            except TimeoutError:
                candidates = []
            return bool(candidates)

        try:
            await self._web.web_await(_options_available, timeout = city_flow_timeout)
        except TimeoutError as ex:
            raise TimeoutError(_("City combobox options did not load for location: %s") % target) from ex

        def normalize(value:str) -> str:
            return " ".join(value.split()).casefold()

        target_norm = normalize(target)
        option_entries = [(candidate, normalize(await self._city_option_text(candidate))) for candidate in candidates]

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
            selected_city = await self._read_city_selection_text()
            return self._location_matches_target(target, selected_city)

        try:
            await self._web.web_await(_selection_converged, timeout = city_flow_timeout)
        except TimeoutError as ex:
            raise TimeoutError(_("City selection did not converge for location: %s") % target) from ex

    async def _set_contact_location(self, location:str) -> None:
        target = location.strip()
        if not target:
            return

        selected_city = await self._read_city_selection_text()
        if self._location_matches_target(target, selected_city):
            return

        city_timeout = self._web.timeout("default")
        city_element = await self._web.web_find(By.ID, "ad-city", timeout = city_timeout)
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

        await self._select_city_combobox_option(target)

    async def _set_contact_fields(self, contact:Contact) -> None:
        #############################
        # set contact zipcode + location
        #############################
        if contact.zipcode:
            try:
                await self._web.web_input(By.ID, "ad-zip-code", str(contact.zipcode))
            except TimeoutError as ex:
                LOG.warning("Could not set contact zipcode: %s", ex)
                raise TimeoutError(_("Failed to set contact zipcode: %s") % contact.zipcode) from ex

            if contact.location:
                await self._set_contact_location(contact.location)

        #############################
        # set contact street
        #############################
        if contact.street:
            try:
                if await self._web.web_check(By.ID, "ad-street", Is.DISABLED):
                    await self._web.web_click(By.ID, "ad-address-visibility")
                    await self._web.web_sleep()
                await self._web.web_set_input_value("ad-street", contact.street)
            except TimeoutError:
                LOG.warning("Could not set contact street.")

        #############################
        # set contact name
        #############################
        if contact.name:
            try:
                if not await self._web.web_check(By.ID, "ad-name", Is.READONLY):
                    await self._web.web_set_input_value("ad-name", contact.name)
            except TimeoutError:
                LOG.warning("Could not set contact name.")

        #############################
        # set contact phone
        #############################
        if contact.phone:
            phone_elem = await self._web.web_probe(By.ID, "ad-phone", timeout = self._web.timeout("quick_dom"))
            if phone_elem is None:
                LOG.info(
                    "Phone number field not present on page. This is expected for many private accounts; commercial accounts may still support phone numbers."
                )
            else:
                try:
                    if await self._web.web_check(By.ID, "ad-phone", Is.DISABLED, timeout = self._web.timeout("quick_dom")):
                        await self._web.web_click(By.ID, "ad-phone-visibility", timeout = self._web.timeout("quick_dom"))
                        await self._web.web_sleep()
                    await self._web.web_set_input_value("ad-phone", contact.phone)
                except TimeoutError as ex:
                    LOG.warning("Could not set contact phone despite visible phone field: %s", ex)

    async def _set_condition(self, condition_value:str) -> bool:
        """Try to set condition via dialog path.

        Returns True when dialog handling succeeded, otherwise False to indicate
        that caller should use generic special-attribute handling.
        """
        canonical_value, legacy_value = _ad_form_helpers.normalize_condition(condition_value)
        if legacy_value is not None:
            LOG.warning("Condition value [%s] is deprecated; update your config to [%s].", legacy_value, canonical_value)

        short_timeout = self._web.timeout("quick_dom")
        condition_trigger_xpath = "//label[contains(@for, '.condition')]/following::button[@aria-haspopup='dialog' or @aria-haspopup='true'][1]"

        condition_trigger = await self._web.web_probe(By.XPATH, condition_trigger_xpath, timeout = short_timeout)
        if condition_trigger is None:
            LOG.debug("Condition dialog trigger not available for [%s]; falling back to generic handler.", condition_value)
            return False

        trigger_id = str(condition_trigger.attrs.get("id") or "")
        trigger_controls = str(condition_trigger.attrs.get("aria-controls") or "")
        LOG.debug("Condition dialog trigger resolved: id='%s', aria-controls='%s'", trigger_id, trigger_controls)

        # Some categories render condition as a combobox and the broad dialog-trigger XPath
        # may accidentally resolve to shipping controls (for example: id='ad-shipping-options').
        # In that case we deliberately skip the dialog path and fall back to generic handling.
        if "shipping" in trigger_id.lower() or "shipping" in trigger_controls.lower():
            LOG.debug(
                "Condition dialog trigger appears to be shipping-related (id='%s', aria-controls='%s'); skipping dialog path for condition_s.",
                trigger_id,
                trigger_controls,
            )
            return False

        # CONDITION_GERMAN_TO_API maps German legacy condition tiers to English API
        # values. Some legacy tiers are intentionally collapsed by the API
        # (e.g. "sehr_gut" / legacy "very good" maps to "like_new").
        # Build candidate_values by probing canonical_value first to avoid quick_dom
        # timeout delays on the current API-valued dialog, then legacy_value as fallback.
        candidate_values:list[str] = [canonical_value]
        if legacy_value is not None:
            candidate_values.append(legacy_value)

        try:
            await condition_trigger.click()
            await self._web.web_find(By.XPATH, '//*[self::dialog or @role="dialog"]', timeout = short_timeout)
            condition_radio = None
            for candidate in candidate_values:
                condition_radio = await self._web.web_probe(
                    By.XPATH,
                    f"//*[self::dialog or @role='dialog']//input[@type='radio' and @value={_ad_form_helpers.xpath_literal(candidate)}]",
                    timeout = short_timeout,
                )
                if condition_radio is not None:
                    break
            if condition_radio is None:
                raise TimeoutError(_("No condition radio matched values %(values)s") % {"values": candidate_values})
            condition_radio_id = str(condition_radio.attrs.get("id") or "")
            if condition_radio_id:
                try:
                    label_xpath = f"//*[self::dialog or @role='dialog']//label[@for={_ad_form_helpers.xpath_literal(condition_radio_id)}]"
                    await self._web.web_click(By.XPATH, label_xpath)
                except TimeoutError:
                    await condition_radio.click()
            else:
                await condition_radio.click()
        except TimeoutError as ex:
            LOG.debug("Unable to select condition [%s]", condition_value, exc_info = True)
            raise TimeoutError(_("Failed to set attribute '%s'") % "condition_s") from ex

        try:
            # Click accept button
            await self._web.web_click(By.XPATH, '//*[self::dialog or @role="dialog"]//button[.//span[text()="Bestätigen"]]')
        except TimeoutError as ex:
            raise TimeoutError(_("Unable to close condition dialog!")) from ex

        return True

    async def _set_category(self, category:str | None, ad_file:str) -> None:
        # click on something to trigger automatic category detection
        await self._web.web_click(By.ID, "ad-description")

        is_category_auto_selected = False
        category_path_elem = await self._web.web_probe(By.ID, "ad-category-path")
        if category_path_elem and await self._web.extract_visible_text(category_path_elem):
            is_category_auto_selected = True

        if category:
            await self._web.web_sleep()  # workaround for https://github.com/Second-Hand-Friends/kleinanzeigen-bot/issues/39
            await self._web.web_click(By.XPATH, "//a[contains(., 'Kategorie')] | //button[contains(., 'Kategorie')]")
            await self._web.web_find(By.XPATH, "//button[contains(., 'Weiter')]")

            category_url = f"{self._root_url}/p-kategorie-aendern.html#?path={category}"
            await self._web.web_open(category_url)
            await self._web.web_click(By.XPATH, "//button[contains(., 'Weiter')]")

            # When the configured path cannot be resolved (e.g. outdated or ambiguous),
            # the site falls back to a React category-suggestion radio picker. Handle it
            # by matching a path segment against one of the offered suggestions.
            await self._resolve_category_suggestions(category)
        else:
            ensure(is_category_auto_selected, f"No category specified in [{ad_file}] and automatic category detection failed")

    async def _resolve_category_suggestions(self, category:str) -> None:
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
        picker_timeout = self._web.timeout("quick_dom")
        picker = await self._web.web_probe(By.ID, "ad-category-picker", timeout = picker_timeout)
        if picker is None:
            return

        radio_selector = "#ad-category-picker input[type='radio'][name='category-suggestions']"
        radio_by_value:dict[str, Element] = {}
        for attempt in range(2):
            try:
                radios = await self._web.web_find_all(By.CSS_SELECTOR, radio_selector, timeout = picker_timeout)
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
                await self._web.web_sleep(200, 350)

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
                    await self._web.web_click(
                        By.XPATH,
                        f"//fieldset[@id='ad-category-picker']//label[@for={_ad_form_helpers.xpath_literal(radio_id)}]",
                        timeout = picker_timeout,
                    )
                else:
                    await radio.click()
            except TimeoutError:
                await radio.click()
            LOG.info("Category suggestion picker: selected value=%s (matched path segment).", segment)
            return

        offered = ", ".join(sorted(radio_by_value.keys())) or "(none)"
        message = _(
            "Category suggestion picker shown, but no segment of configured path '%(category)s' matched the offered suggestions [%(offered)s]. "
            "Update the ad's 'category' to an offered ID or a valid full path."
        )
        raise CategoryResolutionError(message % {"category": category, "offered": offered})

    @staticmethod
    def _special_attribute_candidate_priority(elem:Element) -> tuple[int, int]:
        local_name = elem.local_name
        elem_type = str(cast(Any, elem.attrs.get("type")) or "").lower()
        role = str(cast(Any, elem.attrs.get("role")) or "").lower()

        if local_name == "button" and role == "combobox":
            return (0, 0)
        if local_name == "input" and elem_type in {"text", ""} and role == "combobox":
            return (1, 0)
        if local_name == "select":
            return (2, 0)
        if elem_type == "checkbox":
            return (3, 0)
        if local_name in {"input", "textarea"} and elem_type != "hidden":
            return (4, 0)
        if elem_type == "hidden":
            return (9, 1)
        return (8, 0)

    @staticmethod
    def _describe_special_attribute_candidate(elem:Element) -> str:
        elem_id = cast(str | None, elem.attrs.get("id"))
        elem_name = cast(str | None, elem.attrs.get("name"))
        elem_type = cast(str | None, elem.attrs.get("type"))
        elem_role = cast(str | None, elem.attrs.get("role"))
        return f"{elem.local_name}#'{elem_id}' name='{elem_name}' type='{elem_type}' role='{elem_role}'"

    def _pick_special_attribute_candidate(self, candidates:Sequence[Element], special_attribute_key:str) -> Element:
        ensure(candidates, f"No candidates found for special attribute [{special_attribute_key}]")
        ranked_candidates = sorted(
            enumerate(candidates),
            key = lambda entry: (self._special_attribute_candidate_priority(entry[1]), entry[0]),
        )
        selected_idx, selected = ranked_candidates[0]

        if len(candidates) > 1:
            debug_candidates = ", ".join(f"#{idx}:{self._describe_special_attribute_candidate(candidate)}" for idx, candidate in enumerate(candidates))
            LOG.debug(
                "Attribute field '%s' matched %s elements. Selected #%s: %s. Candidates: %s",
                special_attribute_key,
                len(candidates),
                selected_idx,
                self._describe_special_attribute_candidate(selected),
                debug_candidates,
            )

        return selected

    async def _set_special_attributes(self, ad_cfg:Ad) -> None:
        if not ad_cfg.special_attributes:
            return

        LOG.debug("Found %i special attributes", len(ad_cfg.special_attributes))
        for special_attribute_key, special_attribute_value in ad_cfg.special_attributes.items():
            # Ensure special_attribute_value is treated as a string
            special_attribute_value_str = str(special_attribute_value)
            normalized_special_attribute_key = re.sub(r"_[a-z]+$", "", special_attribute_key).rsplit(".", maxsplit = 1)[-1]
            if not _ad_form_helpers.SPECIAL_ATTRIBUTE_TOKEN_RE.fullmatch(normalized_special_attribute_key):
                LOG.debug(
                    "Attribute field '%s' has unsupported normalized key '%s'.",
                    special_attribute_key,
                    normalized_special_attribute_key,
                )
                raise TimeoutError(_("Failed to set attribute '%s'") % special_attribute_key)

            if normalized_special_attribute_key == "condition":
                LOG.debug("Special attribute [%s]: trying dedicated condition dialog path", special_attribute_key)
                if await self._set_condition(special_attribute_value_str):
                    LOG.debug("Special attribute [%s]: condition dialog path succeeded", special_attribute_key)
                    continue

                LOG.info("Condition dialog not available, falling back to generic attribute handler for [%s]...", special_attribute_key)
                special_attribute_value_str = _ad_form_helpers.normalize_condition(special_attribute_value_str)[0]

            LOG.debug("Setting special attribute [%s] to [%s]...", special_attribute_key, special_attribute_value_str)
            id_suffix_literal = _ad_form_helpers.xpath_literal(f".{normalized_special_attribute_key}")
            name_suffix_literal = _ad_form_helpers.xpath_literal(f".{normalized_special_attribute_key}]")
            name_plus_literal = _ad_form_helpers.xpath_literal(f".{normalized_special_attribute_key}+")
            bare_id_literal = _ad_form_helpers.xpath_literal(normalized_special_attribute_key)
            bare_name_literal = _ad_form_helpers.xpath_literal(f"attributeMap[{normalized_special_attribute_key}]")
            original_key_literal = _ad_form_helpers.xpath_literal(special_attribute_key)
            # Match attribute fields by five patterns:
            # 1) exact id                 -> @id={bare_id_literal}
            # 2) dotted id suffix         -> ... = {id_suffix_literal}
            # 3) exact attributeMap name  -> @name={bare_name_literal}
            # 4) dotted name suffix       -> ... = {name_suffix_literal}
            # 5) compound key marker      -> contains(@name, {name_plus_literal})
            # Literals are derived via _ad_form_helpers.xpath_literal from normalized_special_attribute_key.
            # 6) original config key      -> contains(@name, {original_key_literal}) for compound keys
            special_attr_xpath = (
                "//*["
                f"@id={bare_id_literal}"
                f" or (contains(@id, '.') and substring(@id, string-length(@id) - string-length({id_suffix_literal}) + 1) = {id_suffix_literal})"
                f" or @name={bare_name_literal}"
                f" or (contains(@name, '.') and substring(@name, string-length(@name) - string-length({name_suffix_literal}) + 1) = {name_suffix_literal})"
                f" or contains(@name, {name_plus_literal})"
                f" or contains(@name, {original_key_literal})"
                "]"
            )
            quick_dom = self._web.timeout("quick_dom")
            try:
                if special_attribute_key == "condition_s":
                    special_attr_probe = await self._web.web_probe(By.XPATH, special_attr_xpath, timeout = quick_dom)
                    if special_attr_probe is None:
                        LOG.warning("Special attribute '%s' is not available for the selected category. Skipping.", special_attribute_key)
                        continue
                special_attr_candidates = await self._web.web_find_all(
                    By.XPATH,
                    special_attr_xpath,
                )
                special_attr_elem = self._pick_special_attribute_candidate(special_attr_candidates, special_attribute_key)
            except AssertionError as ex:
                LOG.debug(
                    "Attribute field '%s' (normalized: '%s') could not be found.",
                    special_attribute_key,
                    normalized_special_attribute_key,
                )
                if special_attribute_key == "condition_s":
                    LOG.warning("Special attribute '%s' is not available for the selected category. Skipping.", special_attribute_key)
                    continue
                raise TimeoutError(_("Failed to set attribute '%s'") % special_attribute_key) from ex

            try:
                elem_id = cast(str | None, special_attr_elem.attrs.get("id"))
                elem_type = str(cast(Any, special_attr_elem.attrs.get("type")) or "").lower()
                elem_role = str(cast(Any, special_attr_elem.attrs.get("role")) or "").lower()
                elem_selector_type = By.ID if elem_id else By.XPATH
                elem_selector_value = elem_id or special_attr_xpath

                # If the only match was a hidden backing input, search for the
                # associated <button role="combobox"> by walking up the DOM tree.
                if elem_type == "hidden":
                    LOG.debug("Attribute field '%s': only matched hidden input, searching for associated button combobox...", special_attribute_key)
                    hidden_input_name = special_attr_elem.attrs.get("name")
                    if not hidden_input_name:
                        raise TimeoutError(_("Failed to set attribute '%s'") % special_attribute_key)
                    associated_button_id = await self._find_associated_button_combobox(
                        hidden_input_name = str(hidden_input_name)
                    )
                    if associated_button_id is None:
                        raise TimeoutError(_("Failed to set attribute '%s'") % special_attribute_key)
                    LOG.debug("Attribute field '%s': found associated button combobox id='%s'", special_attribute_key, associated_button_id)
                    await self._select_button_combobox(associated_button_id, special_attribute_value_str)
                    LOG.debug("Successfully set attribute field [%s] to [%s]...", special_attribute_key, special_attribute_value_str)
                    continue

                if special_attr_elem.local_name == "select":
                    LOG.debug("Attribute field '%s' seems to be a select...", special_attribute_key)
                    await self._web.web_select(elem_selector_type, elem_selector_value, special_attribute_value_str)
                elif elem_type == "checkbox":
                    LOG.debug("Attribute field '%s' seems to be a checkbox...", special_attribute_key)
                    truthy_values = {"1", "true", "yes", "on", "ja", "checked"}
                    falsy_values = {"", "0", "false", "no", "off", "nein", "unchecked", "none"}
                    normalized_checkbox_value = special_attribute_value_str.strip().lower()
                    if normalized_checkbox_value in truthy_values:
                        desired_checked = True
                    elif normalized_checkbox_value in falsy_values:
                        desired_checked = False
                    else:
                        LOG.debug(
                            "Attribute field '%s' has unsupported checkbox value '%s'.",
                            special_attribute_key,
                            special_attribute_value_str,
                        )
                        raise TimeoutError(_("Failed to set attribute '%s'") % special_attribute_key)

                    current_checked_attr = special_attr_elem.attrs.get("checked")
                    if isinstance(current_checked_attr, bool):
                        current_checked = current_checked_attr
                    else:
                        normalized_current_checked = str(current_checked_attr).strip().lower() if current_checked_attr is not None else ""
                        current_checked = normalized_current_checked not in falsy_values

                    if desired_checked != current_checked:
                        await self._web.web_click(elem_selector_type, elem_selector_value)
                elif special_attr_elem.local_name == "button" and elem_role == "combobox":
                    LOG.debug("Attribute field '%s' seems to be a button combobox (click-to-open dropdown)...", special_attribute_key)
                    ensure(elem_id, f"No id available for button combobox special attribute [{special_attribute_key}]")
                    await self._select_button_combobox(cast(str, elem_id), special_attribute_value_str)
                elif elem_role == "combobox" and elem_type in {"text", ""} and special_attr_elem.local_name == "input":
                    LOG.debug("Attribute field '%s' seems to be a Combobox (i.e. text input with filtering dropdown)...", special_attribute_key)
                    await self._web.web_select_combobox(elem_selector_type, elem_selector_value, special_attribute_value_str)
                else:
                    LOG.debug("Attribute field '%s' seems to be a text input...", special_attribute_key)
                    await self._web.web_input(elem_selector_type, elem_selector_value, special_attribute_value_str)
            except TimeoutError as ex:
                LOG.debug("Failed to set attribute field '%s' via known input types.", special_attribute_key)
                raise TimeoutError(_("Failed to set attribute '%s'") % special_attribute_key) from ex
            LOG.debug("Successfully set attribute field [%s] to [%s]...", special_attribute_key, special_attribute_value_str)

    # TODO: Issue #930 — migrate to web_select_button_combobox (display-text-based, no React fiber)

    async def _select_button_combobox(self, elem_id:str, value:str) -> None:
        """Select an option from a <button role="combobox"> dropdown by its API value.

        Clicks the button to open the listbox, reads the options data from the React fiber
        (which maps API values to display labels), and clicks the matching option.
        """
        await self._web.web_click(By.ID, elem_id)
        listbox_id = f"{elem_id}-menu"
        await self._web.web_find(By.ID, listbox_id)
        js_btn_id = json.dumps(elem_id)
        js_listbox_id = json.dumps(listbox_id)
        js_value = json.dumps(value)
        ok = await self._web.web_execute(f"""(function() {{
            const listbox = document.getElementById({js_listbox_id});
            if (!listbox) return false;
            const liOptions = Array.from(listbox.querySelectorAll('[role="option"]'));
            const btnEl = document.getElementById({js_btn_id});
            if (!btnEl) return false;
            const fiberKey = Object.keys(btnEl).find(k => k.startsWith('__reactFiber'));
            let fiber = fiberKey ? btnEl[fiberKey] : null;
            for (let i = 0; i < 20 && fiber; i++, fiber = fiber.return) {{
                if (fiber.memoizedProps && fiber.memoizedProps.options) {{
                    const optionsData = fiber.memoizedProps.options;
                    for (let j = 0; j < optionsData.length; j++) {{
                        if (optionsData[j].value === {js_value} && liOptions[j]) {{
                            liOptions[j].click();
                            return true;
                        }}
                    }}
                    return false;
                }}
            }}
            return false;
        }})()""")
        if not ok:
            raise TimeoutError(_("Option '%(value)s' not found in button combobox '%(id)s'") % {"value": value, "id": elem_id})

    async def _find_associated_button_combobox(self, *, hidden_input_name:str) -> str | None:  # pragma: no cover — browser JS helper
        """Locate a ``<button role="combobox">`` by walking from its backing hidden input.

        The interesting logic (DOM queries, ``getElementById``, ancestor walk)
        lives in the inline JavaScript and requires a live browser session for
        meaningful coverage.  The Python wrapper (``json.dumps``, ``isinstance``
        check, return) is trivial boilerplate.  Integration-level routing is
        tested in ``tests/unit/test_publishing_flow.py`` via
        ``TestSpecialAttributesHandler``.

        Anchors to the specific hidden input identified by *hidden_input_name*
        (e.g. ``attributeMap[baby_kinderkleidung.groesse]``), derives the
        expected button ID from the ``attributeMap[...]`` value, and tries
        ``getElementById`` first.  Falls back to walking up the DOM tree from
        the hidden input to find an associated ``<button role="combobox">``.

        :param hidden_input_name: Exact ``name`` attribute of the matched
            hidden ``<input>``.
        :returns: The button's ``id`` attribute, or ``None`` if not found.
        """
        js_hidden_name = json.dumps(hidden_input_name)
        result = await self._web.web_execute(f"""(function() {{
    const name = {js_hidden_name};

    // Find the specific hidden input by exact name.
    const inp = document.querySelector("input[type='hidden'][name=" + JSON.stringify(name) + "]");
    if (!inp) return null;

    // Derive expected button ID from attributeMap[VALUE].
    const match = name.match(/^attributeMap\\[(.+)\\]$/);
    if (match) {{
        const btn = document.getElementById(match[1]);
        if (btn && btn.getAttribute('role') === 'combobox' && btn.tagName === 'BUTTON') return match[1];
    }}

    // Walk up the DOM tree to find a button[role="combobox"].
    let parent = inp.parentElement;
    for (let i = 0; i < 8 && parent; i++, parent = parent.parentElement) {{
        const btn = parent.querySelector('button[role="combobox"]');
        if (btn && btn.id) return btn.id;
    }}

    return null;
}})()""")
        if isinstance(result, str) and result:
            return result
        return None

    async def _set_shipping(self, ad_cfg:Ad, mode:AdUpdateStrategy = AdUpdateStrategy.REPLACE) -> None:
        short_timeout = self._web.timeout("quick_dom")
        if ad_cfg.shipping_type == "PICKUP":
            pickup_radio = await self._web.web_probe(By.ID, "ad-shipping-enabled-no", timeout = short_timeout)
            if pickup_radio is None:
                shipping_fieldset = await self._web.web_probe(By.ID, "ad-shipping-enabled", timeout = short_timeout)
                if shipping_fieldset is not None:
                    raise TimeoutError(
                        _("Shipping fieldset is rendered, but the pickup radio is missing; page may not be fully loaded.")
                    )
                # Some categories (notably books 76/77 and comics 76/77/15156) render no
                # shipping fieldset at all — those ads are PICKUP-only by site convention.
                LOG.debug("PICKUP: no shipping fieldset for this category; treating as already PICKUP.")
                return
            try:
                if not await self._web.web_check(By.ID, "ad-shipping-enabled-no", Is.SELECTED, timeout = short_timeout):
                    await self._web.web_click(By.ID, "ad-shipping-enabled-no", timeout = short_timeout)
            except TimeoutError as ex:
                LOG.debug(ex, exc_info = True)
                raise TimeoutError(_("Failed to set shipping attribute for type '%s'!") % ad_cfg.shipping_type) from ex
        elif ad_cfg.shipping_options:
            # Ensure shipping is enabled before opening the dialog (may already be selected)
            try:
                await self._web.web_click(By.ID, "ad-shipping-enabled-yes", timeout = short_timeout)
                await self._web.web_sleep(500, 800)
            except TimeoutError as ex:
                LOG.debug("Shipping enabled toggle not found before options dialog: %s", ex)
            await self._web.web_click(By.ID, "ad-shipping-options")

            if mode == AdUpdateStrategy.MODIFY:
                try:
                    # when "Andere Versandmethoden" is not available, go back and start over new
                    await self._web.web_find(By.XPATH, '//button[contains(., "Andere Versandmethoden")]', timeout = short_timeout)
                except TimeoutError:
                    await self._web.web_click(By.XPATH, '//button[contains(., "Zurück")]')

                    # in some categories we need to go another dialog back
                    try:
                        await self._web.web_find(By.XPATH, '//button[contains(., "Andere Versandmethoden")]', timeout = short_timeout)
                    except TimeoutError:
                        await self._web.web_click(By.XPATH, '//button[contains(., "Zurück")]')

            await self._web.web_click(By.XPATH, '//button[contains(., "Andere Versandmethoden")]')
            await self._set_shipping_options(ad_cfg, mode)
        else:
            # Ensure shipping is enabled before opening the dialog (may already be selected)
            try:
                await self._web.web_click(By.ID, "ad-shipping-enabled-yes", timeout = short_timeout)
                await self._web.web_sleep(500, 800)
            except TimeoutError as ex:
                LOG.debug("Shipping enabled toggle not found before options dialog: %s", ex)

            # no options. only costs. Set custom shipping cost
            try:
                await self._web.web_click(By.ID, "ad-shipping-options")
            except TimeoutError as ex:
                LOG.debug(ex, exc_info = True)
                LOG.warning("Shipping options dialog entry not found. Legacy '.versand_s' select UI is no longer supported and requires dedicated rebuild.")
                raise TimeoutError(_("Unable to open shipping options dialog!")) from ex

            try:
                # when "Andere Versandmethoden" is not available, then we are already on the individual page
                await self._web.web_click(By.XPATH, '//button[contains(., "Andere Versandmethoden")]')
            except TimeoutError:
                # Dialog option not present; already on the individual shipping page.
                pass

            # only click on "Individueller Versand" when the price input is not available, otherwise it's already checked
            # (important for mode = UPDATE)
            individual_price_elem = await self._web.web_probe(By.ID, "ad-individual-shipping-price", timeout = short_timeout)
            if individual_price_elem is None:
                # Input not visible yet; click the individual shipping option.
                try:
                    await self._web.web_click(By.ID, "ad-individual-shipping-checkbox-control")
                except TimeoutError as ex:
                    LOG.debug(ex, exc_info = True)
                    raise TimeoutError(_("Unable to select individual shipping option!")) from ex

            if ad_cfg.shipping_costs is not None:
                price_str = str(ad_cfg.shipping_costs).replace(".", ",")
                # Native DOM setter + React-aware events: send_keys gets wiped by
                # React re-render after the ad-individual-shipping-checkbox-control click.
                # A re-render between web_find and web_execute inside web_set_input_value can
                # also leave the write as a silent no-op, so verify and retry before "Fertig".
                max_attempts = 3
                for attempt in range(1, max_attempts + 1):
                    try:
                        await self._web.web_set_input_value("ad-individual-shipping-price", price_str)
                        actual = await self._web.web_execute("document.getElementById('ad-individual-shipping-price')?.value")
                    except TimeoutError as ex:
                        # A re-render landing on web_find inside web_set_input_value or on the
                        # readback web_execute can raise here; treat either as a transient
                        # failure so the outer loop can retry instead of bailing.
                        LOG.debug(ex, exc_info = True)
                        if attempt >= max_attempts:
                            raise TimeoutError(_("Unable to set shipping price!")) from ex
                        await self._web.web_sleep(300, 500)
                        continue
                    if actual == price_str:
                        break
                    if attempt >= max_attempts:
                        raise TimeoutError(_("Unable to set shipping price!"))
                    LOG.debug("shipping price not persisted (attempt %d/%d): got %r, expected %r", attempt, max_attempts, actual, price_str)
                    await self._web.web_sleep(300, 500)
            else:
                LOG.debug(
                    "Shipping option 'ad-individual-shipping-checkbox-control' selected but no shipping_costs provided; "
                    "leaving field 'ad-individual-shipping-price' unchanged."
                )

            try:
                await self._web.web_click(By.XPATH, '//button[contains(., "Fertig")]')
            except TimeoutError as ex:
                LOG.debug(ex, exc_info = True)
                raise TimeoutError(_("Unable to close shipping dialog!")) from ex

    async def _set_shipping_options(self, ad_cfg:Ad, mode:AdUpdateStrategy = AdUpdateStrategy.REPLACE) -> None:
        if not ad_cfg.shipping_options:
            raise ValueError(_("shipping_options must be provided"))

        # Resolve user-facing config names to carrier codes
        try:
            wanted_carrier_codes = [CARRIER_CODE_BY_OPTION[opt] for opt in set(ad_cfg.shipping_options)]
        except KeyError as ex:
            raise KeyError(_("Unknown shipping option(s), please refer to the documentation/README: %s") % ad_cfg.shipping_options) from ex

        # Determine the size group — all options must belong to the same group
        size_info = {SIZE_INFO_BY_CARRIER_CODE[code] for code in wanted_carrier_codes}
        if len(size_info) != 1:
            raise ValueError(_("You can only specify shipping options for one package size!"))
        ((shipping_size, shipping_radio_value),) = size_info
        wanted_codes = set(wanted_carrier_codes)
        all_codes_for_size = CARRIER_CODES_BY_SIZE[shipping_size]

        short_timeout = self._web.timeout("quick_dom")
        dialog = '//*[self::dialog or @role="dialog"]'

        try:
            # Select the size group via radio button value (e.g. "SMALL", "MEDIUM", "LARGE")
            size_radio_xpath = f'{dialog}//input[@type="radio" and @value="{shipping_radio_value}"]'
            shipping_size_radio = await self._web.web_find(By.XPATH, size_radio_xpath, timeout = short_timeout)
            shipping_size_radio_is_checked = shipping_size_radio.attrs.get("checked") is not None

            if not shipping_size_radio_is_checked:
                LOG.debug("Selecting size '%s' (radio value=%s)", shipping_size, shipping_radio_value)
                await self._web.web_click(By.XPATH, size_radio_xpath, timeout = short_timeout)

            await self._web.web_sleep(300, 500)
            await self._web.web_click(By.XPATH, f'{dialog}//button[contains(., "Weiter")]', timeout = short_timeout)
            await self._web.web_sleep(500, 800)

            # Toggle package checkboxes by carrier code value attribute.
            # IMPORTANT: REPLACE intentionally uses the same state-based sync as MODIFY.
            # Live DOM defaults after "Weiter" are not stable across size/category (issue #956),
            # so we must read current checkbox state and reconcile with desired state.
            LOG.debug("Using state-based shipping option sync for mode '%s'", mode)
            LOG.debug("Processing %d packages for size '%s'", len(all_codes_for_size), shipping_size)

            for carrier_code in all_codes_for_size:
                checkbox_xpath = f'{dialog}//input[@type="checkbox" and @value="{carrier_code}"]'
                checkbox = await self._web.web_find(By.XPATH, checkbox_xpath, timeout = short_timeout)
                is_checked = checkbox.attrs.get("checked") is not None
                should_be_checked = carrier_code in wanted_codes

                LOG.debug("Carrier '%s': checked=%s, wanted=%s", carrier_code, is_checked, should_be_checked)

                if is_checked != should_be_checked:
                    LOG.debug("Toggling carrier '%s'", carrier_code)
                    await self._web.web_click(By.XPATH, checkbox_xpath, timeout = short_timeout)
        except TimeoutError as ex:
            LOG.debug(ex, exc_info = True)
            raise TimeoutError(_("Failed to configure shipping options in dialog!")) from ex

        try:
            # Click apply button
            await self._web.web_click(By.XPATH, f'{dialog}//button[contains(., "Fertig")]', timeout = short_timeout)
        except TimeoutError as ex:
            raise TimeoutError(_("Unable to close shipping dialog!")) from ex

    async def _upload_images(self, ad_cfg:Ad) -> None:
        if not ad_cfg.images:
            return

        LOG.info(" -> found %s", pluralize("image", ad_cfg.images))
        hidden_marker_selector = "input[name^='adImages'][name$='.url']"
        quick_dom_timeout = self._web.timeout("quick_dom")

        # Capture marker baseline before this upload attempt to avoid counting stale values
        baseline_marker_count = 0
        try:
            baseline_markers = await self._web.web_find_all_once(By.CSS_SELECTOR, hidden_marker_selector, quick_dom_timeout)
            baseline_marker_count = sum(1 for marker in baseline_markers if _ad_form_helpers.get_marker_value(marker))
        except TimeoutError:
            baseline_marker_count = 0

        if baseline_marker_count:
            LOG.debug(" -> detected %d pre-existing image marker(s) before upload", baseline_marker_count)

        total_images = len(ad_cfg.images)
        for index, image in enumerate(ad_cfg.images, start = 1):
            image_upload:Element = await self._web.web_find(By.CSS_SELECTOR, "input[type=file]")
            LOG.info(" -> uploading image %s/%s [%s]", index, total_images, image)
            await image_upload.send_file(image)
            await self._web.web_sleep()

        # Wait for all images to be processed
        expected_count = len(ad_cfg.images)
        LOG.info(" -> waiting for %s to be processed...", pluralize("image", ad_cfg.images))

        async def count_processed_images() -> int:
            try:
                markers = await self._web.web_find_all_once(By.CSS_SELECTOR, hidden_marker_selector, quick_dom_timeout)
                marker_count = sum(1 for marker in markers if _ad_form_helpers.get_marker_value(marker))
            except TimeoutError:
                marker_count = 0

            return max(0, marker_count - baseline_marker_count)

        async def check_thumbnails_uploaded() -> bool:
            current_count = await count_processed_images()
            if current_count < expected_count:
                LOG.debug(" -> %d of %d images processed", current_count, expected_count)
            return current_count >= expected_count

        try:
            await self._web.web_await(
                check_thumbnails_uploaded,
                timeout = self._web.timeout("image_upload"),
                timeout_error_message = _("Image upload timeout exceeded"),
            )
        except TimeoutError as ex:
            # Get current count for better error message
            current_count = await count_processed_images()
            raise TimeoutError(
                _("Not all images were uploaded within timeout. Expected %(expected)d, found %(found)d processed images.")
                % {"expected": expected_count, "found": current_count}
            ) from ex

        LOG.info(" -> all images uploaded successfully")


def _log_local_path_rename_result(
    result:_local_path_renaming.LocalPathRenameResult,
    ad_id:int,
    local_path_renaming_mode:str,
) -> None:
    """Log a human-readable summary of local path renaming after a republish."""
    path_old_id = result.path_old_id if result.path_old_id is not None else result.yaml_old_id
    id_label = f"ID {path_old_id} -> ID {ad_id}"
    if result.path_old_id is not None and result.yaml_old_id is not None and result.path_old_id != result.yaml_old_id:
        id_label += f" (YAML had ID {result.yaml_old_id})"

    renamed:list[str] = []
    if result.folder_status == _local_path_renaming.RenameStatus.RENAMED:
        renamed.append(_("folder"))
    if result.file_status == _local_path_renaming.RenameStatus.RENAMED:
        renamed.append(_("ad file"))
    if result.renamed_image_count > 0:
        renamed.append(f"{result.renamed_image_count} {_('image(s)')}")

    blocked:list[str] = []
    if result.file_status in {_local_path_renaming.RenameStatus.TARGET_EXISTS, _local_path_renaming.RenameStatus.ERROR}:
        blocked.append(_("ad file"))
    if result.folder_status in {_local_path_renaming.RenameStatus.TARGET_EXISTS, _local_path_renaming.RenameStatus.ERROR}:
        blocked.append(_("ad folder"))
    if result.blocked_image_count > 0:
        blocked.append(f"{result.blocked_image_count} {_('image(s)')}")

    if renamed:
        LOG.info("Local path renaming (%s): %s", id_label, ", ".join(renamed))
        if _local_path_renaming.RenameStatus.RENAMED in {result.file_status, result.folder_status}:
            LOG.info("Updated ad file: %s", result.ad_file)

    if blocked:
        LOG.warning("Local path renaming (%s): could not rename %s (target exists or error)", id_label, ", ".join(blocked))

    if not renamed and not blocked:
        if (
            result.yaml_old_id is not None
            and result.yaml_old_id != ad_id
            and local_path_renaming_mode == "TEMPLATE_MATCH"
        ):
            LOG.info("Local path renaming (%s): no local paths needed renaming", id_label)


def persist_published_ad(
    ad_file:str,
    ad_cfg:Ad,
    ad_cfg_orig:dict[str, Any],
    old_ad_id:int | None,
    ad_id:int,
    mode:AdUpdateStrategy,
    *,
    config:Config,
) -> None:
    """Write the published ad ID, hash, timestamps, and counters back to the
    YAML file, then rename local paths to match the new ID."""
    ad_cfg_orig["id"] = ad_id
    # Rename referenced images before hashing/saving so the YAML content and
    # content_hash reflect only image file renames that actually succeeded.
    image_result = _local_path_renaming.rename_referenced_local_image_files_after_id_change(
        Path(ad_file),
        ad_cfg_orig.get("images"),
        old_id = old_ad_id,
        new_id = ad_id,
        ad_file_name_template = config.download.ad_file_name_template,
        enabled = config.publishing.local_path_renaming.mode == "TEMPLATE_MATCH",
    )
    if image_result.updated_images is not None:
        ad_cfg_orig["images"] = image_result.updated_images

    # Update content hash after successful publication
    # Calculate hash on original config to ensure consistent comparison on restart
    ad_cfg_orig["content_hash"] = AdPartial.model_validate(ad_cfg_orig).update_content_hash().content_hash
    ad_cfg_orig["updated_on"] = _misc.now().isoformat(timespec = "seconds")
    if not ad_cfg.created_on and not ad_cfg.id:
        ad_cfg_orig["created_on"] = ad_cfg_orig["updated_on"]

    # Increment repost_count only for REPLACE operations (actual reposts)
    if mode == AdUpdateStrategy.REPLACE:
        # Increment repost_count after successful publish
        # Note: This happens AFTER publish, so price reduction logic (which runs before publish)
        # sees the count from the PREVIOUS run. This is intentional: the first publish uses
        # repost_count=0 (no reduction), the second publish uses repost_count=1 (first reduction), etc.
        current_reposts = int(ad_cfg_orig.get("repost_count", ad_cfg.repost_count or 0))
        ad_cfg_orig["repost_count"] = current_reposts + 1
        ad_cfg.repost_count = ad_cfg_orig["repost_count"]

    # Persist price_reduction_count after successful publish/update.
    # This ensures failed submissions don't incorrectly increment the reduction counter.
    if ad_cfg.price_reduction_count is not None and ad_cfg.price_reduction_count > 0:
        ad_cfg_orig["price_reduction_count"] = ad_cfg.price_reduction_count

    if mode == AdUpdateStrategy.REPLACE:
        LOG.info(" -> SUCCESS: ad published with ID %s", ad_id)
    else:
        LOG.info(" -> SUCCESS: ad updated with ID %s", ad_id)

    try:
        _dicts.save_dict(ad_file, ad_cfg_orig)
    except Exception:
        for old_path, new_path in image_result.renamed_paths:
            try:
                new_path.rename(old_path)
            except OSError:
                LOG.warning("Failed to rollback image rename: %s -> %s", new_path, old_path)
        raise
    # Rename the YAML file and containing folder after saving, because the
    # saved file itself may move as part of this opt-in local migration.
    file_folder_result = _local_path_renaming.rename_local_ad_file_and_folder_after_id_change(
        Path(ad_file),
        old_id = old_ad_id,
        new_id = ad_id,
        ad_file_name_template = config.download.ad_file_name_template,
        folder_name_template = config.download.folder_name_template,
        enabled = config.publishing.local_path_renaming.mode == "TEMPLATE_MATCH",
    )
    rename_result = replace(
        file_folder_result,
        renamed_image_count = image_result.renamed_count,
        blocked_image_count = image_result.blocked_count,
        yaml_old_id = old_ad_id,
    )
    _log_local_path_rename_result(rename_result, ad_id, local_path_renaming_mode = config.publishing.local_path_renaming.mode)
    # NOTE: ad_file string may differ from rename_result.ad_file after the call above.
    # ad_file is stale at this point (pointing to the pre-rename path), but
    # no code in publish_ad() dereferences it after this line, so the drift
    # has no runtime impact.


async def _try_recover_ad_id_from_redirect(web:WebScrapingMixin) -> int | None:
    """Try to extract the published ad ID from page tracking data.

    Used as a fallback when the confirmation page auto-redirects before
    the URL can be polled. Checks document.referrer first, then scans
    inline script content for the confirmation URL containing adId.

    Returns:
        The extracted ad ID, or None if no ad ID could be found.
    """
    # Layer 1: check document.referrer for the confirmation URL.
    # Note: referrer reflects the most recent navigation, so a stale ID from a
    # previous publish is not a concern — the publish flow navigates to the edit
    # page first, resetting the referrer before the confirmation redirect occurs.
    try:
        referrer = str(await web.web_execute("document.referrer") or "")
    except (TimeoutError, ProtocolException) as ex:
        LOG.debug("document.referrer lookup failed (%s), skipping to script scan", type(ex).__name__)
        referrer = ""

    if "p-anzeige-aufgeben-bestaetigung.html?adId=" in referrer:
        try:
            query = urllib_parse.parse_qs(urllib_parse.urlparse(referrer).query)
            ad_id_str = query.get("adId", [])[0]
            ad_id = int(ad_id_str)
            LOG.debug("Extracted ad ID %s from document.referrer fallback", ad_id)
            return ad_id
        except (IndexError, ValueError, TypeError):
            LOG.debug("Failed to parse ad ID from document.referrer: %s", referrer)

    # Layer 2: scan inline <script> tags for confirmation URL with adId
    try:
        script_content = str(await web.web_execute(
            "[...document.querySelectorAll('script')].map(s => s.textContent).join('\\n')"
        ) or "")
        matches = {
            int(match)
            for match in re.findall(r"p-anzeige-aufgeben-bestaetigung\.html\?adId=(\d+)", script_content)
        }
        if len(matches) == 1:
            ad_id = next(iter(matches))
            LOG.debug("Extracted ad ID %s from inline script fallback", ad_id)
            return ad_id
        if len(matches) > 1:
            LOG.debug("Inline script fallback was ambiguous; refusing matches: %s", sorted(matches))
    except (TimeoutError, ProtocolException, ValueError, TypeError) as ex:
        LOG.debug("Script content scan failed (%s): %s", type(ex).__name__, ex)

    return None


async def submit_and_confirm_ad(
    web:WebScrapingMixin,
    ad_file:str,
    ad_cfg:Ad,
    mode:AdUpdateStrategy,
    *,
    captcha_config:CaptchaConfig,
) -> int:
    """Submit the ad form, handle post-submit dialogs, wait for confirmation,
    and extract the published ad ID.

    Returns:
        The published ad ID.

    Raises:
        PublishSubmissionUncertainError: The submission may have succeeded
            but the ad ID could not be recovered.
        RuntimeError: An internal invariant was violated (ad_id is None
            despite the recovery path).
    """

    #############################
    # wait for captcha
    #############################
    operation_label = {
        AdUpdateStrategy.REPLACE: "publish",
        AdUpdateStrategy.MODIFY: "update",
    }.get(mode, mode.name.lower())
    await captcha_flow.check_and_wait_for_captcha(web, captcha_config, is_login_page = False, page_context = f"{operation_label} operation")

    #############################
    # set title (right before submit to prevent React re-render clearing it)
    #############################
    LOG.debug("Setting title '%s' (deferred to prevent React re-render clearing it)", ad_cfg.title)
    await web.web_set_input_value("ad-title", ad_cfg.title)

    #############################
    # submit
    #############################
    # Click is retryable — no submission can have occurred before this point.
    # Edit page uses 'Änderungen speichern' or 'Anzeige speichern'; publish page uses 'Anzeige aufgeben'
    await web.web_click(By.XPATH, "//button[contains(., 'Anzeige aufgeben') or contains(., 'Änderungen speichern') or contains(., 'Anzeige speichern')]")

    # Everything after the first click is uncertain: the ad may already have been submitted.
    ad_id:int | None = None
    try:
        quick_dom = web.timeout("quick_dom")

        # PostListingForm v2 may show an "Effektiver verkaufen" upsell
        # dialog after clicking submit.  Dismiss it so the actual form
        # POST can proceed.
        upsell_dialog = await web.web_probe(
            By.XPATH, "//dialog[@open and contains(., 'Effektiver verkaufen')]", timeout = quick_dom
        )
        if upsell_dialog is not None:
            LOG.info("Dismissing upsell dialog...")
            await web.web_click(
                By.XPATH, "//dialog[@open]//button[contains(., 'Ohne Hochschieben weiter')]",
                timeout = quick_dom,
            )
            await web.web_sleep(500)  # let the dialog close animation finish

        imprint_btn = await web.web_probe(By.ID, "imprint-guidance-submit", timeout = quick_dom)
        if imprint_btn is not None:
            await imprint_btn.click()

        # check for no image question
        if not ad_cfg.images:
            image_hint_xpath = '//button[contains(., "Ohne Bild veröffentlichen")]'
            image_hint_button = await web.web_probe(By.XPATH, image_hint_xpath, timeout = quick_dom)
            if image_hint_button is not None:
                await image_hint_button.click()

        #############################
        # wait for payment form if commercial account is used
        #############################
        payment_form = await web.web_probe(By.ID, "myftr-shppngcrt-frm", timeout = quick_dom)
        if payment_form is not None:
            LOG.warning("############################################")
            LOG.warning("# Payment form detected! Please proceed with payment.")
            LOG.warning("############################################")
            await web.web_scroll_page_down()
            await ainput(_("Press a key to continue..."))

        confirmation_timeout = web.timeout("publishing_confirmation")

        async def _check_confirmation_url() -> bool:
            url = str(await web.web_execute("window.location.href"))
            return "p-anzeige-aufgeben-bestaetigung.html?adId=" in url

        await web.web_await(_check_confirmation_url, timeout = confirmation_timeout)

        # extract the ad id from the URL's query parameter (use JS for fresh URL, not stale page url)
        current_url = str(await web.web_execute("window.location.href"))
        current_url_query_params = urllib_parse.parse_qs(urllib_parse.urlparse(current_url).query)
        ad_id = int(current_url_query_params.get("adId", [])[0])

    except (TimeoutError, ProtocolException, IndexError, ValueError, TypeError) as ex:
        # The confirmation page may have auto-redirected before we could poll it,
        # or the URL was redirected between polling and extraction (race condition).
        # Try to recover the ad ID from tracking data on the current page.
        LOG.debug("Confirmation URL polling or extraction failed (%s), attempting tracking data fallback...", type(ex).__name__)
        try:
            ad_id = await _try_recover_ad_id_from_redirect(web)
        except Exception as fallback_ex:  # noqa: BLE001
            LOG.debug("Tracking data fallback failed: %s", fallback_ex)

        if ad_id is None:
            raise PublishSubmissionUncertainError("submission may have succeeded before failure") from ex

        LOG.warning(
            "Confirmation page redirected too fast; extracted ad ID %s from page tracking data",
            ad_id,
        )

    # Defensive guard: ad_id must be set by now — either from the confirmation URL
    # (try block) or the tracking fallback (except block). The except block always
    # either sets ad_id or raises PublishSubmissionUncertainError, making this
    # unreachable in the current code. Guards against future regressions.
    if ad_id is None:
        msg = _("ad_id is unexpectedly None after confirmation flow for %s") % ad_file
        raise RuntimeError(msg)

    return ad_id
