# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Tests for publishing form operations (contact/location fields, category selection, city selection, pricing)."""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kleinanzeigen_bot import LOG
from kleinanzeigen_bot.app import KleinanzeigenBot
from kleinanzeigen_bot.model.ad_model import Ad, AdUpdateStrategy
from kleinanzeigen_bot.model.config_model import PublishingConfig
from kleinanzeigen_bot.publishing_form import (
    _OTHER_SHIPPING_METHODS_XPATH,  # noqa: PLC2701
    _SHIPPING_BACK_XPATH,  # noqa: PLC2701
    _select_button_combobox,  # noqa: PLC2701 - needed for coverage of React fiber selection
    _set_condition,  # noqa: PLC2701
    _set_configured_shipping_options,  # noqa: PLC2701
    _special_attribute_candidate_priority,  # noqa: PLC2701
    city_option_text,
    fill_image_section,
    read_city_selection_text,
    resolve_category_suggestions,
    select_city_combobox_option,
    set_category,
    set_contact_fields,
    set_contact_location,
    set_pricing_fields,
    set_shipping,
    set_shipping_form,
    set_shipping_options,
    set_special_attributes,
    upload_images,
)
from kleinanzeigen_bot.utils.exceptions import CategoryResolutionError
from kleinanzeigen_bot.utils.web_scraping_mixin import By, Element


class TestKleinanzeigenBotContactLocationHardening:
    @pytest.mark.asyncio
    async def test_city_option_text_falls_back_to_visible_text(self, test_bot:KleinanzeigenBot) -> None:
        """City option text falls back to visible text when aria-label is empty."""
        option = MagicMock(spec = Element)
        option.text = ""
        with patch.object(test_bot, "extract_visible_text", new_callable = AsyncMock, return_value = "  Metroville  "):
            assert await city_option_text(test_bot, option) == "Metroville"

    @pytest.mark.asyncio
    async def test_city_option_text_returns_empty_when_visible_text_times_out(self, test_bot:KleinanzeigenBot) -> None:
        """City option text returns empty string when visible text probe times out."""
        option = MagicMock(spec = Element)
        option.text = ""

        with patch.object(test_bot, "extract_visible_text", new_callable = AsyncMock, side_effect = TimeoutError("hidden")):
            assert not await city_option_text(test_bot, option)

    @pytest.mark.asyncio
    async def test_read_city_selection_text_prefers_live_input_value(self, test_bot:KleinanzeigenBot) -> None:
        """Reading city selection prefers the live input value over the selected option."""
        city_input = MagicMock(spec = Element)
        city_input.local_name = "input"
        city_input.apply = AsyncMock(return_value = "Live City")

        with (
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = city_input),
            patch.object(test_bot, "web_text", new_callable = AsyncMock) as web_text_mock,
        ):
            selected = await read_city_selection_text(test_bot)

        assert selected == "Live City"
        web_text_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_read_city_selection_text_uses_selected_option_text(self, test_bot:KleinanzeigenBot) -> None:
        """Reading city selection uses the selected option text when input value is unavailable."""
        city_button = MagicMock(spec = Element)
        city_button.local_name = "button"

        with (
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = city_button),
            patch.object(test_bot, "web_text", new_callable = AsyncMock, return_value = "Selected City") as web_text_mock,
        ):
            selected = await read_city_selection_text(test_bot)

        assert selected == "Selected City"
        web_text_mock.assert_awaited_once()
        city_button.apply.assert_not_called()

    @pytest.mark.asyncio
    async def test_read_city_selection_text_falls_back_to_element_text(self, test_bot:KleinanzeigenBot) -> None:
        """Reading city selection falls back to element text when no input or option is found."""
        city_button = MagicMock(spec = Element)
        city_button.local_name = "button"
        city_button.apply = AsyncMock(return_value = "Button City")

        with (
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = city_button),
            patch.object(test_bot, "web_text", new_callable = AsyncMock, side_effect = TimeoutError("missing selected option")),
        ):
            selected = await read_city_selection_text(test_bot)

        assert selected == "Button City"

    @pytest.mark.asyncio
    async def test_read_city_selection_text_returns_none_when_city_field_missing(self, test_bot:KleinanzeigenBot) -> None:
        """Reading city selection returns None when the city field element is missing."""
        with patch.object(test_bot, "web_find", new_callable = AsyncMock, side_effect = TimeoutError("missing city")):
            assert await read_city_selection_text(test_bot) is None

    @pytest.mark.asyncio
    async def test_set_contact_fields_fails_closed_when_zipcode_cannot_be_set(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """Setting contact fields fails closed when zipcode cannot be set."""
        ad_cfg = Ad.model_validate(base_ad_config)

        with (
            patch.object(test_bot, "web_input", new_callable = AsyncMock, side_effect = TimeoutError("zip timeout")),
            patch("kleinanzeigen_bot.publishing_form.set_contact_location", new_callable = AsyncMock) as set_location_mock,
            pytest.raises(TimeoutError, match = "Failed to set contact zipcode"),
        ):
            await set_contact_fields(test_bot, ad_cfg.contact)

        set_location_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_set_contact_fields_skips_zipcode_and_location_when_empty(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """When no zipcode is configured, both ZIP entry and location setting are skipped without error."""
        config = base_ad_config | {"contact": base_ad_config["contact"] | {"zipcode": ""}}
        ad_cfg = Ad.model_validate(config)

        with (
            patch.object(test_bot, "web_input", new_callable = AsyncMock) as web_input_mock,
            patch("kleinanzeigen_bot.publishing_form.set_contact_location", new_callable = AsyncMock) as set_location_mock,
            patch.object(test_bot, "web_check", new_callable = AsyncMock, return_value = True),
        ):
            await set_contact_fields(test_bot, ad_cfg.contact)

        web_input_mock.assert_not_awaited()
        set_location_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_set_contact_fields_sets_optional_contact_inputs(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """Setting contact fields populates optional contact inputs (phone, street)."""
        config = base_ad_config | {"contact": base_ad_config["contact"] | {"street": "Test Street 1", "phone": "+491234567"}}
        ad_cfg = Ad.model_validate(config)
        checks = {
            "ad-street": True,
            "ad-name": False,
            "ad-phone": True,
        }

        async def _web_check(_by:By, element_id:str, *_args:Any, **_kwargs:Any) -> bool:
            return checks[element_id]

        with (
            patch.object(test_bot, "web_input", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.publishing_form.set_contact_location", new_callable = AsyncMock),
            patch.object(test_bot, "web_check", new_callable = AsyncMock, side_effect = _web_check),
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as web_click_mock,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch.object(test_bot, "web_set_input_value", new_callable = AsyncMock) as set_value_mock,
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = MagicMock(spec = Element)),
        ):
            await set_contact_fields(test_bot, ad_cfg.contact)

        assert any(call.args[:2] == (By.ID, "ad-address-visibility") for call in web_click_mock.await_args_list)
        assert any(call.args[:2] == (By.ID, "ad-phone-visibility") for call in web_click_mock.await_args_list)
        set_value_mock.assert_any_await("ad-street", ad_cfg.contact.street)
        set_value_mock.assert_any_await("ad-name", ad_cfg.contact.name)
        set_value_mock.assert_any_await("ad-phone", ad_cfg.contact.phone)

    @pytest.mark.asyncio
    async def test_set_contact_fields_skips_absent_phone_field(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """Setting contact fields skips the phone field when it is absent from the form."""
        config = base_ad_config | {"contact": base_ad_config["contact"] | {"phone": "+491234567"}}
        ad_cfg = Ad.model_validate(config)

        with (
            patch.object(test_bot, "web_input", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.publishing_form.set_contact_location", new_callable = AsyncMock),
            patch.object(test_bot, "web_check", new_callable = AsyncMock, return_value = False),
            patch.object(test_bot, "web_set_input_value", new_callable = AsyncMock) as set_value_mock,
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = None) as web_probe_mock,
        ):
            await set_contact_fields(test_bot, ad_cfg.contact)

        web_probe_mock.assert_awaited_once()
        probe_args = web_probe_mock.await_args
        assert probe_args is not None
        assert probe_args.args == (By.ID, "ad-phone")
        assert all(call.args[0] != "ad-phone" for call in set_value_mock.await_args_list)

    @pytest.mark.asyncio
    async def test_set_contact_location_fails_when_city_suffix_matches_multiple_zip_codes(self, test_bot:KleinanzeigenBot) -> None:
        """When multiple ZIP codes share the same city name and no exact match, selection must fail closed."""
        city_button = MagicMock(spec = Element)
        city_button.local_name = "button"
        city_button.attrs = {"role": "combobox", "aria-controls": "ad-city-menu"}

        option_a = MagicMock(spec = Element)
        option_a.text = "10115 - Metroville"
        option_b = MagicMock(spec = Element)
        option_b.text = "12623 - Metroville"

        def _mock_city_option_text(_web:KleinanzeigenBot, elem:Element) -> str:
            return str(getattr(elem, "text", "") or "")

        async def _web_await_side_effect(condition:Callable[..., Awaitable[bool] | bool], **_:Any) -> Any:
            result = condition()
            return await result if asyncio.iscoroutine(result) else result

        with (
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = city_button),
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, return_value = [option_a, option_b]),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = _web_await_side_effect),
            patch("kleinanzeigen_bot.publishing_form.read_city_selection_text", new_callable = AsyncMock, return_value = None),
            patch("kleinanzeigen_bot.publishing_form.city_option_text", new_callable = AsyncMock, side_effect = _mock_city_option_text),
            pytest.raises(TimeoutError, match = "City combobox options are ambiguous for location: Metroville"),
        ):
            await set_contact_location(test_bot, "Metroville")

    @pytest.mark.asyncio
    async def test_set_contact_location_returns_for_blank_location(self, test_bot:KleinanzeigenBot) -> None:
        """Setting contact location returns early when the configured location is blank."""
        with patch("kleinanzeigen_bot.publishing_form.read_city_selection_text", new_callable = AsyncMock) as read_city_mock:
            await set_contact_location(test_bot, "   ")

        read_city_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_set_contact_location_raises_for_missing_city_element(self, test_bot:KleinanzeigenBot) -> None:
        """Setting contact location raises TimeoutError when the city element is missing."""
        with (
            patch("kleinanzeigen_bot.publishing_form.read_city_selection_text", new_callable = AsyncMock, return_value = None),
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = None),
            pytest.raises(TimeoutError, match = "Unsupported city element type while setting contact location: <missing>"),
        ):
            await set_contact_location(test_bot, "Metroville")

    @pytest.mark.asyncio
    async def test_set_contact_location_raises_for_unsupported_city_element(self, test_bot:KleinanzeigenBot) -> None:
        """Setting contact location raises ValueError for an unsupported city element type."""
        city_input = MagicMock(spec = Element)
        city_input.local_name = "input"
        city_input.attrs = {}

        with (
            patch("kleinanzeigen_bot.publishing_form.read_city_selection_text", new_callable = AsyncMock, return_value = None),
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = city_input),
            pytest.raises(TimeoutError, match = "Unsupported city element type while setting contact location: <input>"),
        ):
            await set_contact_location(test_bot, "Metroville")

    @pytest.mark.asyncio
    async def test_select_city_combobox_option_raises_when_options_do_not_load(self, test_bot:KleinanzeigenBot) -> None:
        """Selecting a city combobox option raises TimeoutError when options do not load."""
        city_button = MagicMock(spec = Element)
        city_button.attrs = {}

        with (
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = city_button),
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, side_effect = TimeoutError("not ready")),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = TimeoutError("condition timeout")),
            pytest.raises(TimeoutError, match = "City combobox options did not load for location: Metroville"),
        ):
            await select_city_combobox_option(test_bot, "Metroville")

    @pytest.mark.asyncio
    async def test_select_city_combobox_option_raises_when_no_option_matches(self, test_bot:KleinanzeigenBot) -> None:
        """Selecting a city combobox option raises TimeoutError when no option matches."""
        city_button = MagicMock(spec = Element)
        city_button.attrs = {"aria-controls": "custom-city-list extra-token"}
        option = MagicMock(spec = Element)
        option.text = "10115 - Metroville"

        async def _web_await_side_effect(condition:Callable[..., Awaitable[bool] | bool], **_:Any) -> Any:
            result = condition()
            return await result if asyncio.iscoroutine(result) else result

        with (
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = city_button),
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, return_value = [option]),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = _web_await_side_effect),
            pytest.raises(TimeoutError, match = "No city combobox option matched location: Rivertown"),
        ):
            await select_city_combobox_option(test_bot, "Rivertown")

    @pytest.mark.asyncio
    async def test_set_contact_location_raises_when_selection_does_not_converge(self, test_bot:KleinanzeigenBot) -> None:
        """Setting contact location raises TimeoutError when the selection does not converge."""
        city_button = MagicMock(spec = Element)
        city_button.local_name = "button"
        city_button.attrs = {"role": "combobox", "aria-controls": "ad-city-menu"}

        target_option = MagicMock(spec = Element)
        target_option.text = "10115 - Metroville"
        target_option.click = AsyncMock()

        wait_calls = 0

        async def web_await_side_effect(condition:Callable[..., Awaitable[bool] | bool], **_:Any) -> Any:
            nonlocal wait_calls
            wait_calls += 1

            result = condition()
            condition_value = await result if asyncio.iscoroutine(result) else result
            if wait_calls == 1:
                return condition_value
            raise TimeoutError("Condition not met")

        with (
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = city_button),
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, return_value = [target_option]),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = web_await_side_effect),
            patch("kleinanzeigen_bot.publishing_form.read_city_selection_text", new_callable = AsyncMock, return_value = "20095 - Rivertown"),
            pytest.raises(TimeoutError, match = "City selection did not converge"),
        ):
            await set_contact_location(test_bot, "10115 - Metroville")

    @pytest.mark.asyncio
    async def test_set_contact_location_accepts_readonly_input_with_zip_derived_value(self, test_bot:KleinanzeigenBot) -> None:
        """When ad-city is a readonly <input> with a non-empty prefilled value (zip-derived), accept it."""
        city_input = MagicMock(spec = Element)
        city_input.local_name = "input"
        city_input.attrs = {"readonly": "", "value": "Metroville - Riverside"}

        with (
            patch("kleinanzeigen_bot.publishing_form.read_city_selection_text", new_callable = AsyncMock, return_value = "Metroville - Riverside"),
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = city_input),
            patch("kleinanzeigen_bot.publishing_form.select_city_combobox_option", new_callable = AsyncMock) as combobox_mock,
        ):
            await set_contact_location(test_bot, "Metroville")
            combobox_mock.assert_not_called()

    # ------------------------------------------------------------------
    # read_city_selection_text: edge-case branches
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_read_city_selection_text_returns_none_when_find_returns_none(self, test_bot:KleinanzeigenBot) -> None:
        """web_find succeeds but returns None (not a TimeoutError) — edge case, but handled."""
        with patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = None):
            assert await read_city_selection_text(test_bot) is None

    @pytest.mark.asyncio
    async def test_read_city_selection_text_final_fallback_to_by_id_ad_city(self, test_bot:KleinanzeigenBot) -> None:
        """When selected-option and textContent both fail, fall back to web_text(By.ID, 'ad-city')."""
        city_button = MagicMock(spec = Element)
        city_button.local_name = "button"
        city_button.apply = AsyncMock(return_value = "")  # empty textContent

        with (
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = city_button),
            patch.object(test_bot, "web_text", new_callable = AsyncMock, side_effect = [
                TimeoutError("no selected option"),
                "Fallback City",
            ]),
        ):
            selected = await read_city_selection_text(test_bot)

        assert selected == "Fallback City"

    @pytest.mark.asyncio
    async def test_read_city_selection_text_final_fallback_times_out(self, test_bot:KleinanzeigenBot) -> None:
        """When every lookup strategy fails, return None."""
        city_button = MagicMock(spec = Element)
        city_button.local_name = "button"
        city_button.apply = AsyncMock(return_value = "")  # empty textContent

        with (
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = city_button),
            patch.object(test_bot, "web_text", new_callable = AsyncMock, side_effect = [
                TimeoutError("no selected option"),
                TimeoutError("ad-city also missing"),
            ]),
        ):
            selected = await read_city_selection_text(test_bot)

        assert selected is None

    @pytest.mark.asyncio
    async def test_read_city_selection_text_returns_none_when_input_apply_empty_and_web_text_times_out(
        self,
        test_bot:KleinanzeigenBot,
    ) -> None:
        """Input element with empty apply value, then both web_text calls time out — return None."""
        city_input = MagicMock(spec = Element)
        city_input.local_name = "input"
        city_input.apply = AsyncMock(return_value = "   ")  # blank after trim

        with (
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = city_input),
            patch.object(test_bot, "web_text", new_callable = AsyncMock, side_effect = [
                TimeoutError("no selected option"),
                TimeoutError("ad-city missing"),
            ]),
        ):
            selected = await read_city_selection_text(test_bot)

        assert selected is None

    # ------------------------------------------------------------------
    # select_city_combobox_option: prefix-match ambiguity branch
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_select_city_combobox_option_raises_on_ambiguous_prefix_match(self, test_bot:KleinanzeigenBot) -> None:
        """When multiple options share the same city prefix (no ' - ' in target), raise TimeoutError."""
        city_button = MagicMock(spec = Element)
        city_button.attrs = {"aria-controls": "ad-city-menu"}

        option_a = MagicMock(spec = Element)
        option_a.text = "Berlin - Mitte"
        option_b = MagicMock(spec = Element)
        option_b.text = "Berlin - Spandau"

        async def _web_await_side_effect(condition:Callable[..., Awaitable[bool] | bool], **_:Any) -> Any:
            result = condition()
            condition_value = await result if asyncio.iscoroutine(result) else result
            if condition_value:
                return condition_value
            raise TimeoutError("Condition not met")

        def _city_option_text_side(_web:KleinanzeigenBot, elem:Element) -> str:
            return str(getattr(elem, "text", "") or "").strip()

        with (
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = city_button),
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, return_value = [option_a, option_b]),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = _web_await_side_effect),
            patch("kleinanzeigen_bot.publishing_form.city_option_text", new_callable = AsyncMock, side_effect = _city_option_text_side),
            pytest.raises(TimeoutError, match = "ambiguous for location: Berlin"),
        ):
            await select_city_combobox_option(test_bot, "Berlin")

    # ------------------------------------------------------------------
    # select_city_combobox_option: web_find_all TimeoutError caught inside _options_available
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_select_city_combobox_option_handles_find_all_timeout_in_options_available(
        self,
        test_bot:KleinanzeigenBot,
    ) -> None:
        """_options_available catches TimeoutError from web_find_all and returns False, then web_await times out."""
        city_button = MagicMock(spec = Element)
        city_button.attrs = {"aria-controls": "ad-city-menu"}

        async def _web_await_driver(condition:Callable[..., Awaitable[bool] | bool], **_:Any) -> Any:
            result = condition()
            condition_value = await result if asyncio.iscoroutine(result) else result
            if condition_value:
                return condition_value
            raise TimeoutError("Condition not met")

        with (
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = city_button),
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, side_effect = TimeoutError("not ready")),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = _web_await_driver),
            pytest.raises(TimeoutError, match = "City combobox options did not load for location: Townsville"),
        ):
            await select_city_combobox_option(test_bot, "Townsville")

    # ------------------------------------------------------------------
    # set_contact_fields: non-abort TimeoutError warning paths
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_set_contact_fields_warns_on_street_timeout(self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any]) -> None:
        """A TimeoutError during street input is logged, not re-raised."""
        config = base_ad_config | {"contact": base_ad_config["contact"] | {"street": "Test Street 1"}}
        ad_cfg = Ad.model_validate(config)

        with (
            patch.object(test_bot, "web_input", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.publishing_form.set_contact_location", new_callable = AsyncMock),
            patch.object(test_bot, "web_check", new_callable = AsyncMock, side_effect = TimeoutError("street check timeout")),
            patch.object(test_bot, "web_set_input_value", new_callable = AsyncMock),
        ):
            # Should not raise — timeout is caught and logged
            await set_contact_fields(test_bot, ad_cfg.contact)

    @pytest.mark.asyncio
    async def test_set_contact_fields_warns_on_name_timeout(self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any]) -> None:
        """A TimeoutError during name input is logged, not re-raised."""
        config = base_ad_config | {"contact": base_ad_config["contact"] | {"name": "Test Name"}}
        ad_cfg = Ad.model_validate(config)

        with (
            patch.object(test_bot, "web_input", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.publishing_form.set_contact_location", new_callable = AsyncMock),
            patch.object(test_bot, "web_check", new_callable = AsyncMock, side_effect = TimeoutError("name check timeout")),
            patch.object(test_bot, "web_set_input_value", new_callable = AsyncMock),
        ):
            await set_contact_fields(test_bot, ad_cfg.contact)

    @pytest.mark.asyncio
    async def test_set_contact_fields_warns_on_phone_set_value_timeout(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """A TimeoutError during phone input is logged, not re-raised."""
        config = base_ad_config | {"contact": base_ad_config["contact"] | {"phone": "+491234567"}}
        ad_cfg = Ad.model_validate(config)

        with (
            patch.object(test_bot, "web_input", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.publishing_form.set_contact_location", new_callable = AsyncMock),
            patch.object(test_bot, "web_check", new_callable = AsyncMock, return_value = True),
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch.object(test_bot, "web_set_input_value", new_callable = AsyncMock, side_effect = TimeoutError("phone input timeout")),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = MagicMock(spec = Element)),
        ):
            await set_contact_fields(test_bot, ad_cfg.contact)


class TestCategoryProbeBehavior:
    """Tests for category marker probing without retry backoff."""

    @pytest.mark.asyncio
    async def test_set_category_uses_probe_for_auto_selected_marker(self, test_bot:KleinanzeigenBot) -> None:
        """In _set_category, category marker lookup should go through web_probe."""
        category_marker = MagicMock()
        category_marker.apply = AsyncMock(return_value = "Auto Category")

        async def probe(selector_type:Any, selector_value:str, **_kwargs:Any) -> Any:
            if selector_value == "ad-category-path":
                return category_marker
            return None  # no suggestion picker shown

        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = probe) as mock_probe,
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_find", new_callable = AsyncMock),
            patch.object(test_bot, "web_open", new_callable = AsyncMock),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
        ):
            await set_category(test_bot, root_url = test_bot.root_url, category = "185/249", ad_file = "data/my_ads/ad.yaml")

        mock_probe.assert_any_await(By.ID, "ad-category-path")

    @pytest.mark.asyncio
    async def test_set_category_without_explicit_category_requires_probe_match(self, test_bot:KleinanzeigenBot) -> None:
        """When no category is configured, missing marker should fail fast."""
        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = None),
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            pytest.raises(AssertionError, match = "No category specified"),
        ):
            await set_category(test_bot, root_url = test_bot.root_url, category = None, ad_file = "data/my_ads/ad.yaml")


class TestCategorySuggestionPicker:
    """Regression tests for the post-redesign category-suggestion radio picker fallback."""

    @staticmethod
    def _picker_probe_factory(picker_present:bool) -> Callable[..., Any]:
        async def probe(selector_type:Any, selector_value:str, **_kwargs:Any) -> Any:
            if selector_value == "ad-category-path":
                marker = MagicMock()
                marker.apply = AsyncMock(return_value = "")
                return marker
            if selector_value == "ad-category-picker":
                return MagicMock() if picker_present else None
            return None

        return probe

    @staticmethod
    def _radio(value:str, radio_id:str | None = None) -> MagicMock:
        elem = MagicMock()
        elem.attrs = {"value": value}
        if radio_id is not None:
            elem.attrs["id"] = radio_id
        elem.click = AsyncMock()
        return elem

    @pytest.mark.asyncio
    async def test_picker_absent_leaves_flow_unchanged(self, test_bot:KleinanzeigenBot) -> None:
        """No picker -> no-op, no find_all / label click."""
        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = self._picker_probe_factory(picker_present = False)),
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock) as mock_find_all,
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
        ):
            await resolve_category_suggestions(test_bot, "73/76/sachbuecher")

        mock_find_all.assert_not_awaited()
        mock_click.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_picker_present_without_rendered_radios_retries_then_times_out(self, test_bot:KleinanzeigenBot) -> None:
        """Picker shell present but radios not rendered yet should fail closed after a bounded retry."""
        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = self._picker_probe_factory(picker_present = True)),
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, return_value = []) as mock_find_all,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as mock_sleep,
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            pytest.raises(TimeoutError, match = "Category suggestion picker element found but no radio suggestions rendered after waiting."),
        ):
            await resolve_category_suggestions(test_bot, "73/76/sachbuecher")

        assert mock_find_all.await_count == 2
        mock_sleep.assert_awaited_once()
        mock_click.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_picker_present_matches_leaf_segment_and_clicks_label(self, test_bot:KleinanzeigenBot) -> None:
        """Picker present with matching radio value -> label[for=ID] is clicked (value != id to catch regressions)."""
        radios = [
            self._radio("76", "category-suggestion-parent"),
            self._radio("77", "category-suggestion-leaf"),
            self._radio("240", "category-suggestion-other"),
        ]

        label_elem = MagicMock()
        label_elem.click = AsyncMock()

        async def find_side_effect(selector_type:Any, selector_value:str, **_:Any) -> Any:
            if selector_type == By.CSS_SELECTOR and selector_value == "#ad-category-picker label[for='category-suggestion-leaf']":
                return label_elem
            return MagicMock()

        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = self._picker_probe_factory(picker_present = True)),
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, return_value = radios),
            patch.object(test_bot, "web_find", new_callable = AsyncMock, side_effect = find_side_effect) as mock_find,
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
        ):
            await resolve_category_suggestions(test_bot, "73/77")

        mock_click.assert_not_awaited()
        mock_find.assert_awaited_once()
        selector_type, selector_value = mock_find.call_args.args[:2]
        assert selector_type == By.CSS_SELECTOR
        assert selector_value == "#ad-category-picker label[for='category-suggestion-leaf']"
        label_elem.click.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_picker_present_no_match_raises_with_offered_list(self, test_bot:KleinanzeigenBot) -> None:
        """Picker present but path has no matching segment -> CategoryResolutionError listing offered IDs."""
        radios = [
            self._radio("76", "category-suggestion-parent"),
            self._radio("77", "category-suggestion-leaf"),
            self._radio("240", "category-suggestion-other"),
        ]

        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = self._picker_probe_factory(picker_present = True)),
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, return_value = radios),
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            pytest.raises(CategoryResolutionError, match = r"Category suggestion picker shown.*offered") as exc_info,
        ):
            await resolve_category_suggestions(test_bot, "999/888")

        mock_click.assert_not_awaited()
        error_message = str(exc_info.value)
        # The error must name the configured (unmatched) path and every offered ID,
        # otherwise the user cannot know what to correct.
        assert "999/888" in error_message
        for offered_id in ("76", "77", "240"):
            assert offered_id in error_message

    @pytest.mark.asyncio
    async def test_picker_prefers_deepest_matching_segment(self, test_bot:KleinanzeigenBot) -> None:
        """When both parent and leaf segments match radios, the leaf (deepest) wins."""
        radios = [self._radio("76", "id-for-76"), self._radio("77", "id-for-77")]

        label_elem = MagicMock()
        label_elem.click = AsyncMock()

        async def find_side_effect(selector_type:Any, selector_value:str, **_:Any) -> Any:
            if selector_type == By.CSS_SELECTOR and selector_value == "#ad-category-picker label[for='id-for-77']":
                return label_elem
            return MagicMock()

        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = self._picker_probe_factory(picker_present = True)),
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, return_value = radios),
            patch.object(test_bot, "web_find", new_callable = AsyncMock, side_effect = find_side_effect) as mock_find,
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
        ):
            await resolve_category_suggestions(test_bot, "76/77")

        mock_click.assert_not_awaited()
        assert mock_find.call_args.args[0] == By.CSS_SELECTOR
        assert "#ad-category-picker label[for='id-for-77']" in mock_find.call_args.args[1]
        label_elem.click.assert_awaited_once()


class TestImageUploadProcessedMarkerFallback:
    """Regression tests for image upload completion detection via hidden marker inputs."""

    @staticmethod
    def _build_two_image_ad(base_ad_config:dict[str, Any], tmp_path:Path) -> tuple[Ad, str, str]:
        image_a = tmp_path / "img_a.jpg"
        image_b = tmp_path / "img_b.jpg"
        image_a.write_bytes(b"")
        image_b.write_bytes(b"")
        ad_cfg = Ad.model_validate(base_ad_config | {"images": [str(image_a), str(image_b)]})
        return ad_cfg, str(image_a), str(image_b)

    @staticmethod
    def _build_marker(url:str) -> MagicMock:
        marker = MagicMock()
        marker.attrs.value = url
        return marker

    @staticmethod
    @contextmanager
    def _mock_upload_dependencies(
        test_bot:KleinanzeigenBot,
        file_input:MagicMock,
        find_all_side_effect:Callable[..., Awaitable[list[MagicMock]]],
        await_side_effect:Callable[..., Awaitable[Any]],
    ) -> Iterator[None]:
        async def find_all_once_side_effect(selector_type:By, selector_value:str, *_:Any, **__:Any) -> list[MagicMock]:
            return await find_all_side_effect(selector_type, selector_value, **__)

        with (
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = file_input),
            patch.object(test_bot, "_web_find_all_once", new_callable = AsyncMock, side_effect = find_all_once_side_effect),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = await_side_effect),
        ):
            yield

    @pytest.mark.asyncio
    async def test_upload_images_succeeds_with_hidden_markers_when_thumbnails_absent(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        tmp_path:Path,
    ) -> None:
        """Hidden adImages markers should satisfy completion when thumbnail list is missing."""
        ad_cfg, image_a, image_b = self._build_two_image_ad(base_ad_config, tmp_path)

        file_input = MagicMock()
        file_input.send_file = AsyncMock()

        marker_a = self._build_marker("https://img.example/a.jpg")
        marker_b = self._build_marker("https://img.example/b.jpg")
        marker_query_count = 0

        async def find_all_side_effect(selector_type:By, selector_value:str, *_:Any, **__:Any) -> list[MagicMock]:
            nonlocal marker_query_count
            if selector_type == By.CSS_SELECTOR and selector_value == "input[name^='adImages'][name$='.url']":
                marker_query_count += 1
                if marker_query_count == 1:
                    return []
                return [marker_a, marker_b]
            return []

        async def await_side_effect(condition:Callable[[], Awaitable[bool]], **_:Any) -> bool:
            if await condition():
                return True
            raise TimeoutError("condition did not pass")

        with self._mock_upload_dependencies(test_bot, file_input, find_all_side_effect, await_side_effect):
            await upload_images(test_bot, ad_cfg)

        file_input.send_file.assert_any_await(image_a)
        file_input.send_file.assert_any_await(image_b)

    @pytest.mark.asyncio
    async def test_upload_images_refetches_file_input_per_image_to_avoid_stale_element(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        tmp_path:Path,
    ) -> None:
        """Each image upload should re-fetch the file input because the DOM replaces it after selection."""
        ad_cfg, image_a, image_b = self._build_two_image_ad(base_ad_config, tmp_path)

        first_file_input = MagicMock()
        first_file_input.send_file = AsyncMock()
        second_file_input = MagicMock()
        second_file_input.send_file = AsyncMock()

        marker_a = self._build_marker("https://img.example/a.jpg")
        marker_b = self._build_marker("https://img.example/b.jpg")
        marker_query_count = 0

        async def find_side_effect(selector_type:By, selector_value:str, **_:Any) -> MagicMock:
            assert selector_type == By.CSS_SELECTOR
            assert selector_value == "input[type=file]"
            if first_file_input.send_file.await_count == 0:
                return first_file_input
            return second_file_input

        async def find_all_side_effect(selector_type:By, selector_value:str, *_:Any, **__:Any) -> list[MagicMock]:
            nonlocal marker_query_count
            if selector_type == By.CSS_SELECTOR and selector_value == "input[name^='adImages'][name$='.url']":
                marker_query_count += 1
                if marker_query_count == 1:
                    return []
                return [marker_a, marker_b]
            return []

        async def await_side_effect(condition:Callable[[], Awaitable[bool]], **_:Any) -> bool:
            if await condition():
                return True
            raise TimeoutError("condition did not pass")

        with (
            patch.object(test_bot, "web_find", new_callable = AsyncMock, side_effect = find_side_effect) as mock_find,
            patch.object(test_bot, "_web_find_all_once", new_callable = AsyncMock, side_effect = find_all_side_effect),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = await_side_effect),
        ):
            await upload_images(test_bot, ad_cfg)

        first_file_input.send_file.assert_awaited_once_with(image_a)
        second_file_input.send_file.assert_awaited_once_with(image_b)
        assert mock_find.await_count >= 2

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("baseline_count", "post_count", "expected_found"),
        [
            pytest.param(2, 2, 0, id = "stale-only-markers"),
            pytest.param(0, 1, 1, id = "one-new-marker"),
        ],
    )
    async def test_upload_images_timeout_reports_processed_count(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        tmp_path:Path,
        baseline_count:int,
        post_count:int,
        expected_found:int,
    ) -> None:
        """Upload timeout should report the correct processed-marker count based on baseline vs post-upload markers."""
        ad_cfg, image_a, image_b = self._build_two_image_ad(base_ad_config, tmp_path)

        file_input = MagicMock()
        file_input.send_file = AsyncMock()

        marker_query_count = 0

        async def find_all_side_effect(selector_type:By, selector_value:str, **_:Any) -> list[MagicMock]:
            nonlocal marker_query_count
            if selector_type == By.CSS_SELECTOR and selector_value == "input[name^='adImages'][name$='.url']":
                marker_query_count += 1
                if marker_query_count == 1:
                    return [self._build_marker(f"https://img.example/baseline-{i}.jpg") for i in range(baseline_count)]
                return [self._build_marker(f"https://img.example/post-{i}.jpg") for i in range(post_count)]
            return []

        async def await_timeout(*_:Any, **__:Any) -> None:
            raise TimeoutError("Image upload timeout exceeded")

        with (
            pytest.raises(TimeoutError, match = rf"Expected 2, found {expected_found} processed"),
            self._mock_upload_dependencies(test_bot, file_input, find_all_side_effect, await_timeout),
        ):
            await upload_images(test_bot, ad_cfg)

        file_input.send_file.assert_any_await(image_a)
        file_input.send_file.assert_any_await(image_b)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("baseline_count", "post_count"),
        [
            pytest.param(0, 2, id = "no_baseline"),
            pytest.param(1, 3, id = "one_stale_plus_two_new"),
            pytest.param(2, 4, id = "two_stale_plus_two_new"),
        ],
    )
    async def test_upload_images_marker_delta_determines_completion(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        tmp_path:Path,
        baseline_count:int,
        post_count:int,
    ) -> None:
        """Completion should succeed when marker delta reaches expected count."""
        ad_cfg, image_a, image_b = self._build_two_image_ad(base_ad_config, tmp_path)

        file_input = MagicMock()
        file_input.send_file = AsyncMock()
        marker_query_count = 0

        async def find_all_side_effect(selector_type:By, selector_value:str, **_:Any) -> list[MagicMock]:
            nonlocal marker_query_count
            if selector_type == By.CSS_SELECTOR and selector_value == "input[name^='adImages'][name$='.url']":
                marker_query_count += 1
                if marker_query_count == 1:
                    return [self._build_marker(f"https://img.example/stale-{i}.jpg") for i in range(baseline_count)]
                return [self._build_marker(f"https://img.example/post-{i}.jpg") for i in range(post_count)]
            return []

        async def await_side_effect(condition:Callable[[], Awaitable[bool]], **_:Any) -> bool:
            if await condition():
                return True
            raise TimeoutError("condition did not pass")

        with self._mock_upload_dependencies(test_bot, file_input, find_all_side_effect, await_side_effect):
            await upload_images(test_bot, ad_cfg)

        file_input.send_file.assert_any_await(image_a)
        file_input.send_file.assert_any_await(image_b)

    @pytest.mark.asyncio
    async def test_upload_images_baseline_capture_timeout_defaults_to_zero(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        tmp_path:Path,
    ) -> None:
        """If baseline marker lookup times out, marker fallback should still work with baseline=0."""
        ad_cfg, image_a, image_b = self._build_two_image_ad(base_ad_config, tmp_path)
        file_input = MagicMock()
        file_input.send_file = AsyncMock()
        marker_query_count = 0

        async def find_all_side_effect(selector_type:By, selector_value:str, **_:Any) -> list[MagicMock]:
            nonlocal marker_query_count
            if selector_type == By.CSS_SELECTOR and selector_value == "input[name^='adImages'][name$='.url']":
                marker_query_count += 1
                if marker_query_count == 1:
                    raise TimeoutError("baseline markers unavailable")
                return [self._build_marker("https://img.example/a.jpg"), self._build_marker("https://img.example/b.jpg")]
            return []

        async def await_side_effect(condition:Callable[[], Awaitable[bool]], **_:Any) -> bool:
            if await condition():
                return True
            raise TimeoutError("condition did not pass")

        with self._mock_upload_dependencies(test_bot, file_input, find_all_side_effect, await_side_effect):
            await upload_images(test_bot, ad_cfg)

        file_input.send_file.assert_any_await(image_a)
        file_input.send_file.assert_any_await(image_b)

    @pytest.mark.asyncio
    async def test_fill_image_section_removes_existing_images_before_upload(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        tmp_path:Path,
    ) -> None:
        """Cleanup should probe and click remove buttons before upload."""
        image_path = tmp_path / "img.jpg"
        image_path.write_bytes(b"\xff\xd8\xff")
        ad_cfg = Ad.model_validate(base_ad_config | {"images": [str(image_path)]})
        probe_call_count = 0
        remove_buttons:list[MagicMock] = []
        event_log:list[str] = []

        async def probe_side_effect(selector_type:By, selector_value:str, **_:Any) -> Element | None:
            nonlocal probe_call_count
            if selector_type == By.CSS_SELECTOR and selector_value == "button[aria-label='Bild entfernen']":
                probe_call_count += 1
                if probe_call_count <= 3:
                    remove_btn = MagicMock()
                    remove_btn.click = AsyncMock(side_effect = lambda idx = probe_call_count: event_log.append(f"remove-{idx}"))
                    remove_buttons.append(remove_btn)
                    return remove_btn
                return None
            return None

        async def find_all_side_effect(selector_type:By, selector_value:str, *_:Any, **__:Any) -> list[MagicMock]:
            if selector_type == By.CSS_SELECTOR and selector_value == "input[name^='adImages'][name$='.url']":
                return [self._build_marker(f"https://img.example/{index}.jpg") for index in range(3)]
            return []

        async def upload_side_effect(*_:Any, **__:Any) -> None:
            event_log.append("upload")

        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = probe_side_effect),
            patch.object(test_bot, "_web_find_all_once", new_callable = AsyncMock, side_effect = find_all_side_effect),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.publishing_form.upload_images", new_callable = AsyncMock, side_effect = upload_side_effect) as mock_upload,
        ):
            await fill_image_section(test_bot, ad_cfg)

        assert sum(button.click.await_count for button in remove_buttons) == 3
        mock_upload.assert_awaited_once_with(test_bot, ad_cfg)
        assert event_log == ["remove-1", "remove-2", "remove-3", "upload"]


class TestPricingFields:
    """Tests for pricing, direct-buy, and description field filling."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("price_type", "price", "expected_idx"),
        [
            ("FIXED", 100, 0),
            ("NEGOTIABLE", 100, 1),
            ("GIVE_AWAY", None, 2),
        ],
    )
    async def test_price_type_dropdown_click(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        price_type:str,
        price:int | None,
        expected_idx:int,
    ) -> None:
        """Price type dropdown should click the correct option index."""
        ad_cfg = Ad.model_validate(base_ad_config | {"price_type": price_type, "price": price})

        with (
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            patch.object(test_bot, "web_set_input_value", new_callable = AsyncMock),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = None),
            patch.object(test_bot, "web_check", new_callable = AsyncMock, return_value = False),
            patch("kleinanzeigen_bot.publishing_form.get_ad_description", return_value = "desc"),
        ):
            await set_pricing_fields(test_bot, ad_cfg, test_bot.config.ad_defaults)

        mock_click.assert_any_await(By.ID, "ad-price-type")
        mock_click.assert_any_await(By.ID, f"ad-price-type-menu-option-{expected_idx}")

    @pytest.mark.asyncio
    async def test_price_type_not_applicable_skips_dropdown(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """NOT_APPLICABLE price type should skip all price interactions."""
        ad_cfg = Ad.model_validate(base_ad_config | {"price_type": "NOT_APPLICABLE"})

        with (
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            patch.object(test_bot, "web_set_input_value", new_callable = AsyncMock) as mock_set,
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = None),
            patch.object(test_bot, "web_check", new_callable = AsyncMock, return_value = False),
            patch("kleinanzeigen_bot.publishing_form.get_ad_description", return_value = "desc"),
        ):
            await set_pricing_fields(test_bot, ad_cfg, test_bot.config.ad_defaults)

        ad_price_type_clicks = [c for c in mock_click.call_args_list if len(c.args) >= 2 and c.args[1] == "ad-price-type"]
        assert not ad_price_type_clicks
        ad_price_amount_sets = [c for c in mock_set.call_args_list if c.args[0] == "ad-price-amount"]
        assert not ad_price_amount_sets

    @pytest.mark.asyncio
    async def test_price_amount_set_when_provided(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """Price amount should be set when price is not None."""
        ad_cfg = Ad.model_validate(base_ad_config | {"price": 42})

        with (
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_set_input_value", new_callable = AsyncMock) as mock_set,
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = None),
            patch.object(test_bot, "web_check", new_callable = AsyncMock, return_value = False),
            patch("kleinanzeigen_bot.publishing_form.get_ad_description", return_value = "desc"),
        ):
            await set_pricing_fields(test_bot, ad_cfg, test_bot.config.ad_defaults)

        mock_set.assert_any_await("ad-price-amount", "42")

    @pytest.mark.asyncio
    async def test_price_amount_not_set_when_none(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """Price amount should not be set when price is None."""
        ad_cfg = Ad.model_validate(base_ad_config | {"price_type": "NEGOTIABLE", "price": None})

        with (
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_set_input_value", new_callable = AsyncMock) as mock_set,
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = None),
            patch.object(test_bot, "web_check", new_callable = AsyncMock, return_value = False),
            patch("kleinanzeigen_bot.publishing_form.get_ad_description", return_value = "desc"),
        ):
            await set_pricing_fields(test_bot, ad_cfg, test_bot.config.ad_defaults)

        ad_price_amount_sets = [c for c in mock_set.call_args_list if c.args[0] == "ad-price-amount"]
        assert not ad_price_amount_sets

    @pytest.mark.asyncio
    async def test_price_type_timeout_raises(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """Timeout on price type click should be re-raised with meaningful message."""
        ad_cfg = Ad.model_validate(base_ad_config | {"price_type": "FIXED"})

        with (
            patch.object(test_bot, "web_click", new_callable = AsyncMock, side_effect = TimeoutError("boom")),
            patch.object(test_bot, "web_set_input_value", new_callable = AsyncMock),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = None),
            patch.object(test_bot, "web_check", new_callable = AsyncMock, return_value = False),
            patch("kleinanzeigen_bot.publishing_form.get_ad_description", return_value = "desc"),
            pytest.raises(TimeoutError, match = "Failed to set price type 'FIXED'"),
        ):
            await set_pricing_fields(test_bot, ad_cfg, test_bot.config.ad_defaults)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("scenario", "expected_click"),
        [
            ("radio_absent_swallowed", False),
            ("radio_visible_needs_click", True),
            ("radio_already_selected", False),
        ],
    )
    async def test_buy_now_radio_behavior_for_pickup(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        scenario:str,
        expected_click:bool,
    ) -> None:
        """Buy-now radio handling for PICKUP: skips when absent, clicks when needed."""
        ad_cfg = Ad.model_validate(base_ad_config | {"shipping_type": "PICKUP", "price_type": "FIXED", "price": 100})

        buy_now_elem = MagicMock()

        async def probe_side_effect(selector_type:By, selector_value:str, **_:Any) -> Element | None:
            if selector_type == By.ID and selector_value == "ad-buy-now-false":
                if scenario == "radio_absent_swallowed":
                    return None
                return buy_now_elem
            return None

        async def check_side_effect(selector_type:By, selector_value:str, *_:Any, **__:Any) -> bool:
            if selector_type == By.ID and selector_value == "ad-buy-now-false":
                return scenario == "radio_already_selected"
            return False

        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = probe_side_effect),
            patch.object(test_bot, "web_check", new_callable = AsyncMock, side_effect = check_side_effect),
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            patch.object(test_bot, "web_set_input_value", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.publishing_form.get_ad_description", return_value = "desc"),
        ):
            await set_pricing_fields(test_bot, ad_cfg, test_bot.config.ad_defaults)

        buy_now_clicks = [c for c in mock_click.call_args_list if len(c.args) >= 2 and c.args[0] == By.ID and c.args[1] == "ad-buy-now-false"]
        if expected_click:
            assert buy_now_clicks, "web_click should be called for ad-buy-now-false when visible but not selected"
        else:
            assert not buy_now_clicks, "web_click should not be called for ad-buy-now-false"

    @pytest.mark.asyncio
    async def test_buy_now_true_missing_logs_warning_and_continues(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        caplog:pytest.LogCaptureFixture,
    ) -> None:
        """Shipping ads with sell_directly enabled should continue if buy-now-true control is unavailable."""
        caplog.set_level(logging.WARNING, logger = LOG.name)
        ad_cfg = Ad.model_validate(
            base_ad_config | {
                "shipping_type": "SHIPPING",
                "sell_directly": True,
                "price_type": "FIXED",
                "price": 100,
                "shipping_options": ["DHL_2"]})

        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = None),
            patch.object(test_bot, "web_check", new_callable = AsyncMock),
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_set_input_value", new_callable = AsyncMock) as mock_set,
            patch("kleinanzeigen_bot.publishing_form.get_ad_description", return_value = "desc"),
        ):
            await set_pricing_fields(test_bot, ad_cfg, test_bot.config.ad_defaults)

        # Assert warning about category-unavailable buy-now
        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("sell_directly" in m and "not available" in m for m in warning_messages), \
            "Should log warning when buy-now-true is unavailable"

        # Assert continuation (description field was set after the warning)
        mock_set.assert_any_await("ad-description", "desc")

    @pytest.mark.asyncio
    async def test_sell_directly_without_shipping_options_raises(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """Direct-buy without predefined shipping_options should raise ValueError (publishing guard).

        Uses model_construct to bypass Phase 1 model validator and test the publishing
        guard independently.
        """
        ad_cfg = Ad.model_construct(
            **base_ad_config | {
                "shipping_type": "SHIPPING",
                "sell_directly": True,
                "price_type": "FIXED",
                "price": 100,
                "shipping_options": [],
            }
        )

        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock) as mock_probe,
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_set_input_value", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.publishing_form.get_ad_description", return_value = "desc"),
            pytest.raises(ValueError, match = "Direct-buy.*shipping_options"),
        ):
            await set_pricing_fields(test_bot, ad_cfg, test_bot.config.ad_defaults)

        # Price type dropdown may be clicked before the guard fires; the guard
        # must prevent buy-now-true from being probed/clicked.
        buy_now_probes = [c for c in mock_probe.await_args_list if len(c.args) >= 2 and "ad-buy-now" in str(c.args[1])]
        assert not buy_now_probes, "guard must prevent buy-now DOM probing"

    @pytest.mark.asyncio
    async def test_buy_now_true_interaction_timeout_propagates(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """Existing direct-buy controls should still fail if interaction times out."""
        ad_cfg = Ad.model_validate(
            base_ad_config | {
                "shipping_type": "SHIPPING",
                "sell_directly": True,
                "price_type": "FIXED",
                "price": 100,
                "shipping_options": ["DHL_2"]})

        buy_now_elem = MagicMock()

        async def probe_side_effect(selector_type:By, selector_value:str, **_:Any) -> Element | None:
            if selector_type == By.ID and selector_value == "ad-buy-now-true":
                return buy_now_elem
            return None

        async def check_side_effect(selector_type:By, selector_value:str, *_:Any, **__:Any) -> bool:
            if selector_type == By.ID and selector_value == "ad-buy-now-true":
                raise TimeoutError("check timeout")
            return False

        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = probe_side_effect),
            patch.object(test_bot, "web_check", new_callable = AsyncMock, side_effect = check_side_effect),
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_set_input_value", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.publishing_form.get_ad_description", return_value = "desc"),
            pytest.raises(TimeoutError, match = "check timeout"),
        ):
            await set_pricing_fields(test_bot, ad_cfg, test_bot.config.ad_defaults)

    @pytest.mark.asyncio
    async def test_sell_directly_false_clicks_buy_now_false(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """When sell_directly is False and shipping is SHIPPING, ad-buy-now-false should be clicked."""
        ad_cfg = Ad.model_validate(base_ad_config | {"shipping_type": "SHIPPING", "sell_directly": False, "price_type": "FIXED", "price": 100})

        buy_now_false_elem = MagicMock()

        async def probe_side_effect(selector_type:By, selector_value:str, **_:Any) -> Element | None:
            if selector_type == By.ID and selector_value == "ad-buy-now-false":
                return buy_now_false_elem
            return None

        async def check_side_effect(selector_type:By, selector_value:str, *_:Any, **__:Any) -> bool:
            return False  # not already selected

        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = probe_side_effect),
            patch.object(test_bot, "web_check", new_callable = AsyncMock, side_effect = check_side_effect),
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            patch.object(test_bot, "web_set_input_value", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.publishing_form.get_ad_description", return_value = "desc"),
        ):
            await set_pricing_fields(test_bot, ad_cfg, test_bot.config.ad_defaults)

        buy_now_false_clicks = [c for c in mock_click.call_args_list if len(c.args) >= 2 and c.args[0] == By.ID and c.args[1] == "ad-buy-now-false"]
        assert buy_now_false_clicks

    @pytest.mark.asyncio
    async def test_description_filled_via_get_ad_description(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """Description field should be set using get_ad_description result."""
        ad_cfg = Ad.model_validate(base_ad_config)

        with (
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_set_input_value", new_callable = AsyncMock) as mock_set,
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = None),
            patch.object(test_bot, "web_check", new_callable = AsyncMock, return_value = False),
            patch("kleinanzeigen_bot.publishing_form.get_ad_description", return_value = "Expected description text") as mock_desc,
        ):
            await set_pricing_fields(test_bot, ad_cfg, test_bot.config.ad_defaults)

        mock_desc.assert_called_once_with(ad_cfg, test_bot.config.ad_defaults, with_affixes = True)
        mock_set.assert_any_await("ad-description", "Expected description text")


class TestShippingDialogFlow:
    """Regression tests for shipping dialog flow using new radio selectors only."""

    # The three individual CSS selectors probed by _find_versand_combobox.
    versand_selectors = (
        'button[role="combobox"][id="versand"]',
        'button[role="combobox"][id$=".versand"]',
        'button[role="combobox"][aria-labelledby$="versand-selected-option"]',
    )

    @staticmethod
    def _versand_probe_factory(combobox:MagicMock | None) -> Callable[..., Any]:
        """Return a web_probe side effect that returns the combobox for the first matching versand selector, None otherwise."""
        async def probe(selector_type:Any, selector_value:str, **_:Any) -> Any:
            if selector_type == By.CSS_SELECTOR and selector_value in TestShippingDialogFlow.versand_selectors:
                return combobox
            return None
        return probe

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("shipping_type", "expected_label"),
        [("SHIPPING", "Versand möglich"), ("PICKUP", "Nur Abholung")],
    )
    async def test_shipping_uses_versand_combobox_when_rendered(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        shipping_type:str,
        expected_label:str,
    ) -> None:
        """Commercial accounts may render Versand as a special-attribute combobox instead of radio buttons."""
        ad_cfg = Ad.model_validate(base_ad_config | {"shipping_type": shipping_type})
        shipping_combobox = MagicMock()
        shipping_combobox.attrs = {"id": "uhren.versand"}

        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = self._versand_probe_factory(shipping_combobox)) as mock_probe,
            patch.object(test_bot, "web_select_button_combobox", new_callable = AsyncMock) as mock_select_combobox,
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
        ):
            await set_shipping(test_bot, ad_cfg)

        # At least one versand selector probe should have returned the combobox.
        versand_probes = [
            call for call in mock_probe.await_args_list
            if call.args[:2] == (By.CSS_SELECTOR, self.versand_selectors[0])
        ]
        assert versand_probes, "Expected at least one probe for the first versand CSS selector"
        mock_select_combobox.assert_awaited_once()
        assert mock_select_combobox.await_args is not None
        assert mock_select_combobox.await_args.args[:2] == ("uhren.versand", expected_label)
        mock_click.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("selected", [False, True])
    async def test_pickup_shipping_radio_selection(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        selected:bool,
    ) -> None:
        """PICKUP shipping should click the pickup radio only when it is not already selected."""
        ad_cfg = Ad.model_validate(base_ad_config | {"shipping_type": "PICKUP"})

        async def probe_side_effect(selector_type:Any, selector_value:str, **_:Any) -> Any:
            # All versand combobox probes return None (not a commercial account).
            if selector_type == By.CSS_SELECTOR and selector_value in self.versand_selectors:
                return None
            # Pickup radio probe returns a mock element.
            if selector_type == By.ID and selector_value == "ad-shipping-enabled-no":
                return MagicMock()
            return None

        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = probe_side_effect) as mock_probe,
            patch.object(test_bot, "web_check", new_callable = AsyncMock, return_value = selected),
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
        ):
            await set_shipping(test_bot, ad_cfg)

        observed = [call.args[:2] for call in mock_probe.await_args_list]
        assert (By.CSS_SELECTOR, self.versand_selectors[0]) in observed
        assert (By.ID, "ad-shipping-enabled-no") in observed
        if selected:
            mock_click.assert_not_awaited()
        else:
            mock_click.assert_awaited_once()
            assert mock_click.call_args.args[:2] == (By.ID, "ad-shipping-enabled-no")

    @pytest.mark.asyncio
    async def test_pickup_shipping_raises_when_radio_lookup_times_out(self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any]) -> None:
        """PICKUP shipping should fail fast when pickup radio selector is unavailable."""
        ad_cfg = Ad.model_validate(base_ad_config | {"shipping_type": "PICKUP"})

        async def probe_side_effect(selector_type:Any, selector_value:str, **_:Any) -> Any:
            # All versand combobox probes return None (not a commercial account).
            if selector_type == By.CSS_SELECTOR and selector_value in self.versand_selectors:
                return None
            # Pickup radio probe returns a mock element.
            if selector_type == By.ID and selector_value == "ad-shipping-enabled-no":
                return MagicMock()
            return None

        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = probe_side_effect),
            patch.object(test_bot, "web_check", new_callable = AsyncMock, side_effect = TimeoutError("pickup lookup timed out")),
            pytest.raises(TimeoutError, match = "Failed to set shipping attribute for type 'PICKUP'!"),
        ):
            await set_shipping(test_bot, ad_cfg)

    @pytest.mark.asyncio
    async def test_pickup_shipping_skips_when_toggle_not_rendered(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """Categories without a shipping fieldset (e.g. books 76/77, comics 76/77/15156)
        are PICKUP-only by site convention — the absence of both shipping selectors should
        short-circuit without calling ``web_check``/``web_click``."""
        ad_cfg = Ad.model_validate(base_ad_config | {"shipping_type": "PICKUP"})

        async def probe_side_effect(selector_type:Any, selector_value:str, **_:Any) -> Any:
            # All versand combobox probes return None.
            if selector_type == By.CSS_SELECTOR and selector_value in self.versand_selectors:
                return None
            # Pickup radio probe returns None (not rendered).
            if selector_type == By.ID and selector_value == "ad-shipping-enabled-no":
                return None
            # Shipping fieldset probe returns None (not rendered).
            if selector_type == By.ID and selector_value == "ad-shipping-enabled":
                return None
            return None

        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = probe_side_effect),
            patch.object(test_bot, "web_check", new_callable = AsyncMock) as mock_check,
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
        ):
            await set_shipping(test_bot, ad_cfg)

        mock_check.assert_not_awaited()
        mock_click.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_pickup_shipping_raises_when_fieldset_rendered_but_pickup_radio_missing(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """A rendered shipping fieldset without the pickup radio should be treated as an error."""
        ad_cfg = Ad.model_validate(base_ad_config | {"shipping_type": "PICKUP"})

        async def probe_side_effect(selector_type:Any, selector_value:str, **_:Any) -> Any:
            # All versand combobox probes return None.
            if selector_type == By.CSS_SELECTOR and selector_value in self.versand_selectors:
                return None
            # Pickup radio probe returns None (missing).
            if selector_type == By.ID and selector_value == "ad-shipping-enabled-no":
                return None
            # Shipping fieldset probe returns a mock (rendered).
            if selector_type == By.ID and selector_value == "ad-shipping-enabled":
                return MagicMock()
            return None

        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = probe_side_effect) as mock_probe,
            patch.object(test_bot, "web_check", new_callable = AsyncMock) as mock_check,
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            pytest.raises(
                TimeoutError,
                match = "Shipping fieldset is rendered, but the pickup radio is missing; page may not be fully loaded.",
            ),
        ):
            await set_shipping(test_bot, ad_cfg)

        observed = [call.args[:2] for call in mock_probe.await_args_list]
        assert (By.CSS_SELECTOR, self.versand_selectors[0]) in observed
        assert (By.ID, "ad-shipping-enabled-no") in observed
        assert (By.ID, "ad-shipping-enabled") in observed
        mock_check.assert_not_awaited()
        mock_click.assert_not_awaited()

    # ------------------------------------------------------------------
    # No-options branch: fail-fast for configured costs, platform-default for none
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_shipping_no_options_with_costs_raises(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """No shipping_options with shipping_costs: raise ValueError (fail-fast)."""
        ad_cfg = Ad.model_validate(base_ad_config | {"shipping_type": "SHIPPING", "shipping_options": [], "shipping_costs": 4.95})

        with (
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = None),
            pytest.raises(ValueError, match = "shipping_costs.*no longer supported"),
        ):
            await set_shipping(test_bot, ad_cfg)

    @pytest.mark.asyncio
    async def test_shipping_no_options_no_costs_platform_default(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        caplog:pytest.LogCaptureFixture,
    ) -> None:
        """No shipping_options and no shipping_costs: click enabled, debug log only, no warning."""
        caplog.set_level(logging.WARNING, logger = LOG.name)
        ad_cfg = Ad.model_validate(base_ad_config | {"shipping_type": "SHIPPING", "shipping_options": [], "shipping_costs": None})

        with (
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = None),
        ):
            await set_shipping(test_bot, ad_cfg)

        # Must click shipping-enabled-yes
        enabled_yes_clicks = [
            c for c in mock_click.await_args_list
            if len(c.args) >= 2 and c.args[0] == By.ID and c.args[1] == "ad-shipping-enabled-yes"
        ]
        assert enabled_yes_clicks, "Should click shipping-enabled-yes"

        # Must NOT open shipping-options dialog
        options_clicks = [
            c for c in mock_click.await_args_list
            if len(c.args) >= 2 and c.args[0] == By.ID and c.args[1] == "ad-shipping-options"
        ]
        assert not options_clicks, "Must NOT open shipping-options dialog"

        # Must NOT touch any individual-shipping selectors
        individual_clicks = [
            c for c in mock_click.await_args_list
            if len(c.args) >= 2 and "ad-individual-shipping" in str(c.args[1])
        ]
        assert not individual_clicks, "Must NOT touch individual-shipping selectors"

        # No warning should be logged (only debug)
        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert not any("shipping_costs" in m for m in warning_messages)
        assert not any("Individual shipping" in m for m in warning_messages)

    # ------------------------------------------------------------------
    # set_shipping commercial/pro combobox: error paths
    # ------------------------------------------------------------------


class TestShippingOptionsDialog:
    """Tests for set_shipping_options using carrier-code-based selectors."""

    @staticmethod
    def _make_ad_with_options(base_ad_config:dict[str, Any], options:list[str]) -> Ad:
        return Ad.model_validate(
            base_ad_config
            | {
                "shipping_type": "SHIPPING",
                "shipping_options": options,
            }
        )

    @staticmethod
    def _mock_checkbox(checked:bool = False) -> MagicMock:
        """Create a mock checkbox element with optional checked attribute."""
        el = MagicMock()
        if checked:
            el.attrs = {"checked": ""}
        else:
            el.attrs = {}
        return el

    @pytest.mark.asyncio
    async def test_open_dialog_action_is_tag_agnostic(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """The shipping action may be a button, link, span, or another clickable element."""
        ad_cfg = self._make_ad_with_options(base_ad_config, ["DHL_5"])

        other_methods_elem = MagicMock()
        other_methods_elem.click = AsyncMock()

        # Track whether "Andere Versandmethoden" has been clicked yet.
        andere_clicked = [False]

        async def probe_side_effect(selector_type:Any, selector_value:str, **_:Any) -> Any:
            if selector_type == By.CSS_SELECTOR and 'input[type="radio"]' in selector_value:
                # Before Andere Versandmethoden click: no radios. After: radios appear.
                return MagicMock() if andere_clicked[0] else None
            # "Andere Versandmethoden" text probe returns the element.
            if selector_type == By.TEXT and selector_value == "Andere Versandmethoden":
                return other_methods_elem
            return None

        async def find_side_effect(selector_type:Any, selector_value:str, **_:Any) -> Any:
            return MagicMock()

        # Set the flag when click is awaited.
        async def _track_click() -> None:
            andere_clicked[0] = True

        other_methods_elem.click.side_effect = _track_click

        with (
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(
                test_bot,
                "web_probe",
                new_callable = AsyncMock,
                side_effect = probe_side_effect,
            ) as mock_probe,
            patch.object(test_bot, "web_find", new_callable = AsyncMock, side_effect = find_side_effect),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.publishing_form.set_shipping_options", new_callable = AsyncMock),
        ):
            await _set_configured_shipping_options(
                test_bot,
                ad_cfg,
                AdUpdateStrategy.REPLACE,
                test_bot.timeout("quick_dom"),
            )

        # The "Andere Versandmethoden" action is found via By.TEXT (tag-agnostic).
        text_probes = [
            call for call in mock_probe.await_args_list
            if call.args[:2] == (By.TEXT, "Andere Versandmethoden")
        ]
        assert text_probes, "Expected a By.TEXT probe for 'Andere Versandmethoden'"
        other_methods_elem.click.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reports_clear_error_when_alternate_action_disappears(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """When size radios don't appear after clicking Andere Versandmethoden, the route returns without raising."""
        ad_cfg = self._make_ad_with_options(base_ad_config, ["DHL_5"])

        other_methods_elem = MagicMock()
        other_methods_elem.click = AsyncMock()

        async def probe_side_effect(selector_type:Any, selector_value:str, **_:Any) -> Any:
            # Size radio probes return None (radios never appear).
            if selector_type == By.CSS_SELECTOR and 'input[type="radio"]' in selector_value:
                return None
            # "Andere Versandmethoden" text probe returns the element.
            if selector_type == By.TEXT and selector_value == "Andere Versandmethoden":
                return other_methods_elem
            return None

        async def find_side_effect(selector_type:Any, selector_value:str, **_:Any) -> Any:
            return MagicMock()

        with (
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(
                test_bot,
                "web_probe",
                new_callable = AsyncMock,
                side_effect = probe_side_effect,
            ),
            patch.object(test_bot, "web_find", new_callable = AsyncMock, side_effect = find_side_effect),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.publishing_form.set_shipping_options", new_callable = AsyncMock) as options_mock,
            pytest.raises(TimeoutError, match = "Failed to configure shipping options in dialog"),
        ):
            # With web_probe returning None for all size radios, the Andere Versandmethoden
            # route completes without finding a radio — the outer loop exhausts max_back_steps
            # and raises TimeoutError.
            await _set_configured_shipping_options(
                test_bot,
                ad_cfg,
                AdUpdateStrategy.REPLACE,
                test_bot.timeout("quick_dom"),
            )

        options_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_navigates_back_to_direct_size_selection(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """A nested dialog state is unwound before selecting a package size."""
        ad_cfg = self._make_ad_with_options(base_ad_config, ["DHL_5"])

        back_elem = MagicMock()
        back_elem.click = AsyncMock()
        size_radio_elem = MagicMock()

        call_count = {"probe": 0}

        async def probe_side_effect(selector_type:Any, selector_value:str, **_:Any) -> Any:
            call_count["probe"] += 1
            # First iteration: all size radio probes return None.
            if selector_type == By.CSS_SELECTOR and 'input[type="radio"]' in selector_value:
                if call_count["probe"] <= 3:
                    return None
                # Second iteration: first size radio probe returns element.
                return size_radio_elem
            # "Andere Versandmethoden" probe returns None.
            if selector_type == By.TEXT and selector_value == "Andere Versandmethoden":
                return None
            # "Zurück" (back) probe returns the back button element.
            if selector_type == By.TEXT and selector_value == "Zurück":
                return back_elem
            return None

        with (
            patch.object(
                test_bot,
                "web_probe",
                new_callable = AsyncMock,
                side_effect = probe_side_effect,
            ) as mock_probe,
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.publishing_form.set_shipping_options", new_callable = AsyncMock),
        ):
            await _set_configured_shipping_options(
                test_bot,
                ad_cfg,
                AdUpdateStrategy.REPLACE,
                test_bot.timeout("quick_dom"),
            )

        # Back navigation happened via element.click(), not web_click.
        back_elem.click.assert_awaited_once()
        # No web_click for XPath-based back or other-methods selectors.
        assert not any(
            call.args[:2] == (By.XPATH, _SHIPPING_BACK_XPATH)
            for call in mock_click.await_args_list
        )
        assert not any(
            call.args[:2] == (By.XPATH, _OTHER_SHIPPING_METHODS_XPATH)
            for call in mock_click.await_args_list
        )
        # "Zurück" was probed via By.TEXT.
        assert any(
            call.args[:2] == (By.TEXT, "Zurück")
            for call in mock_probe.await_args_list
        )

    @pytest.mark.asyncio
    async def test_fails_when_dialog_has_no_supported_navigation(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """Fail clearly when neither a size pane nor a back route is present."""
        ad_cfg = self._make_ad_with_options(base_ad_config, ["DHL_5"])
        with (
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(
                test_bot,
                "web_probe",
                new_callable = AsyncMock,
                return_value = None,
            ),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.publishing_form.set_shipping_options", new_callable = AsyncMock) as options_mock,
            pytest.raises(TimeoutError, match = "Failed to configure shipping options"),
        ):
            await _set_configured_shipping_options(
                test_bot,
                ad_cfg,
                AdUpdateStrategy.REPLACE,
                test_bot.timeout("quick_dom"),
            )

        options_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_fails_after_bounded_back_navigation(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """Stop after two back steps when no supported size pane appears."""
        ad_cfg = self._make_ad_with_options(base_ad_config, ["DHL_5"])

        back_elems = []
        for _ in range(2):
            elem = MagicMock()
            elem.click = AsyncMock()
            back_elems.append(elem)

        zuruck_call_count = {"n": 0}

        async def probe_side_effect(selector_type:Any, selector_value:str, **_:Any) -> Any:
            # Size radio probes always return None.
            if selector_type == By.CSS_SELECTOR and 'input[type="radio"]' in selector_value:
                return None
            # "Andere Versandmethoden" probe always returns None.
            if selector_type == By.TEXT and selector_value == "Andere Versandmethoden":
                return None
            # "Zurück" (back) probe: return element for first 2 calls, None after.
            if selector_type == By.TEXT and selector_value == "Zurück":
                idx = zuruck_call_count["n"]
                zuruck_call_count["n"] += 1
                if idx < 2:
                    return back_elems[idx]
                return None
            return None

        with (
            patch.object(
                test_bot,
                "web_probe",
                new_callable = AsyncMock,
                side_effect = probe_side_effect,
            ),
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as click_mock,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.publishing_form.set_shipping_options", new_callable = AsyncMock) as options_mock,
            pytest.raises(TimeoutError, match = "Failed to configure shipping options"),
        ):
            await _set_configured_shipping_options(
                test_bot,
                ad_cfg,
                AdUpdateStrategy.REPLACE,
                test_bot.timeout("quick_dom"),
            )

        # Back navigation happened via element.click(), not web_click with XPath.
        assert all(elem.click.await_count == 1 for elem in back_elems)
        back_xpath_clicks = [
            call for call in click_mock.await_args_list
            if call.args == (By.XPATH, _SHIPPING_BACK_XPATH)
        ]
        assert len(back_xpath_clicks) == 0
        options_mock.assert_not_awaited()

    @staticmethod
    def _mock_interactable_checkbox(checked:bool = False) -> MagicMock:
        """Create a mock checkbox element with click tracking and optional checked attribute."""
        el = MagicMock()
        if checked:
            el.attrs = {"checked": ""}
        else:
            el.attrs = {}
        el.click = AsyncMock()
        return el

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "case",
        [
            {
                "options": ["DHL_2", "Hermes_P\u00e4ckchen"],
                "radio_checked": True,
                "expected_radio_click": False,
                "expected_clicked_carriers": ["HERMES_002"],
                "expected_not_clicked_carriers": ["DHL_001", "HERMES_001"],
            },
            {
                "options": ["DHL_10"],
                "radio_checked": False,
                "expected_radio_click": True,
                "expected_clicked_carriers": ["HERMES_004", "DHL_004", "DHL_005"],
                "expected_not_clicked_carriers": ["DHL_003"],
            },
        ],
    )
    async def test_replace_mode_handles_radio_state(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        case:dict[str, Any],
    ) -> None:
        """REPLACE mode: handles both pre-checked and unchecked radio states."""
        ad_cfg = self._make_ad_with_options(base_ad_config, case["options"])

        radio_mock = self._mock_interactable_checkbox(checked = case["radio_checked"])
        weiter_mock = MagicMock()
        weiter_mock.click = AsyncMock()
        fertig_mock = MagicMock()
        fertig_mock.click = AsyncMock()

        # Track all checkbox mocks so we can inspect which were clicked.
        checkbox_mocks:dict[str, MagicMock] = {}

        async def find_side_effect(selector_type:By, selector_value:str, **_:Any) -> MagicMock:
            # Size radio via CSS selector.
            if selector_type == By.CSS_SELECTOR and "radio" in selector_value:
                return radio_mock
            # Weiter button via By.TEXT.
            if selector_type == By.TEXT and selector_value == "Weiter":
                return weiter_mock
            # Fertig button via By.TEXT.
            if selector_type == By.TEXT and selector_value == "Fertig":
                return fertig_mock
            # Carrier checkbox via CSS selector — all pre-checked.
            if selector_type == By.CSS_SELECTOR and "checkbox" in selector_value:
                # Extract carrier code from the CSS selector value attribute.
                code = selector_value.rsplit('"', 2)[-2] if '"' in selector_value else ""
                if code not in checkbox_mocks:
                    checkbox_mocks[code] = self._mock_interactable_checkbox(checked = True)
                return checkbox_mocks[code]
            return MagicMock()

        with (
            patch.object(test_bot, "web_find", new_callable = AsyncMock, side_effect = find_side_effect),
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,  # noqa: F841
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
        ):
            await set_shipping_options(test_bot, ad_cfg, mode = AdUpdateStrategy.REPLACE)

        # Radio click behavior matches expectation (element.click, not web_click).
        assert (radio_mock.click.await_count == 1) == case["expected_radio_click"]

        # Weiter and Fertig were clicked via element.click().
        weiter_mock.click.assert_awaited_once()
        fertig_mock.click.assert_awaited_once()

        # web_click should never be called (all clicks are element.click()).
        mock_click.assert_not_awaited()

        # Should toggle exactly the expected carriers for this scenario.
        for carrier_code in case["expected_clicked_carriers"]:
            assert checkbox_mocks[carrier_code].click.await_count == 1, f"Expected {carrier_code} to be toggled"

        for carrier_code in case["expected_not_clicked_carriers"]:
            assert checkbox_mocks[carrier_code].click.await_count == 0, f"Expected {carrier_code} to NOT be toggled"

    @pytest.mark.asyncio
    async def test_replace_mode_dom_verified_unchecked_defaults_select_wanted_carrier(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """REPLACE mode must select wanted carriers when defaults are unchecked (DOM-verified for MEDIUM/LARGE)."""
        ad_cfg = self._make_ad_with_options(base_ad_config, ["DHL_5"])

        radio_mock = self._mock_interactable_checkbox(checked = False)  # MEDIUM radio not selected yet
        weiter_mock = MagicMock()
        weiter_mock.click = AsyncMock()
        fertig_mock = MagicMock()
        fertig_mock.click = AsyncMock()

        checkbox_mocks:dict[str, MagicMock] = {
            "HERMES_003": self._mock_interactable_checkbox(checked = False),
            "DHL_002": self._mock_interactable_checkbox(checked = False),
        }

        async def find_side_effect(selector_type:By, selector_value:str, **_:Any) -> MagicMock:
            if selector_type == By.CSS_SELECTOR and "radio" in selector_value and "MEDIUM" in selector_value:
                return radio_mock
            if selector_type == By.TEXT and selector_value == "Weiter":
                return weiter_mock
            if selector_type == By.TEXT and selector_value == "Fertig":
                return fertig_mock
            if selector_type == By.CSS_SELECTOR and "checkbox" in selector_value:
                for code, mock_el in checkbox_mocks.items():
                    if code in selector_value:
                        return mock_el
            return self._mock_interactable_checkbox(checked = False)

        with (
            patch.object(test_bot, "web_find", new_callable = AsyncMock, side_effect = find_side_effect),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
        ):
            await set_shipping_options(test_bot, ad_cfg, mode = AdUpdateStrategy.REPLACE)

        # Regression guard for issue #956: wanted DHL_002 must be selected
        assert checkbox_mocks["DHL_002"].click.await_count >= 1
        # Unwanted Hermes checkbox must remain untouched when already unchecked
        assert checkbox_mocks["HERMES_003"].click.await_count == 0

    @pytest.mark.asyncio
    async def test_modify_mode_toggles_carriers(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """MODIFY mode: explicitly (de-)selects each carrier based on wanted set."""
        ad_cfg = self._make_ad_with_options(base_ad_config, ["Hermes_Päckchen", "DHL_2"])

        radio_mock = self._mock_interactable_checkbox(checked = True)  # SMALL already selected
        weiter_mock = MagicMock()
        weiter_mock.click = AsyncMock()
        fertig_mock = MagicMock()
        fertig_mock.click = AsyncMock()

        checkbox_mocks:dict[str, MagicMock] = {
            "HERMES_001": self._mock_interactable_checkbox(checked = True),
            "HERMES_002": self._mock_interactable_checkbox(checked = True),
            "DHL_001": self._mock_interactable_checkbox(checked = False),
        }

        async def find_side_effect(selector_type:By, selector_value:str, **_:Any) -> MagicMock:
            if selector_type == By.CSS_SELECTOR and "radio" in selector_value and "SMALL" in selector_value:
                return radio_mock
            if selector_type == By.TEXT and selector_value == "Weiter":
                return weiter_mock
            if selector_type == By.TEXT and selector_value == "Fertig":
                return fertig_mock
            if selector_type == By.CSS_SELECTOR and "checkbox" in selector_value:
                for code, mock_el in checkbox_mocks.items():
                    if code in selector_value:
                        return mock_el
            return self._mock_interactable_checkbox(checked = False)

        with (
            patch.object(test_bot, "web_find", new_callable = AsyncMock, side_effect = find_side_effect),
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
        ):
            await set_shipping_options(test_bot, ad_cfg, mode = AdUpdateStrategy.MODIFY)

        # web_click should never be called (all clicks are element.click()).
        mock_click.assert_not_awaited()
        # HERMES_002 should be deselected (was checked, not wanted)
        assert checkbox_mocks["HERMES_002"].click.await_count == 1
        # DHL_001 should be selected (was unchecked, wanted via DHL_2 → DHL_001)
        assert checkbox_mocks["DHL_001"].click.await_count == 1
        # HERMES_001 should NOT be clicked (was checked, wanted)
        assert checkbox_mocks["HERMES_001"].click.await_count == 0

    @pytest.mark.asyncio
    async def test_unknown_option_raises_key_error(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """Unknown shipping option name raises KeyError with helpful message."""
        ad_cfg = self._make_ad_with_options(base_ad_config, ["NonExistent_Option"])

        with (
            patch.object(test_bot, "web_find", new_callable = AsyncMock) as mock_find,
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as mock_sleep,
            pytest.raises(KeyError, match = "Unknown shipping option"),
        ):
            await set_shipping_options(test_bot, ad_cfg)

        # Validation errors must occur before any DOM interaction
        mock_find.assert_not_awaited()
        mock_click.assert_not_awaited()
        mock_sleep.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mixed_size_options_raises_value_error(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """Options from different size groups raise ValueError."""
        ad_cfg = self._make_ad_with_options(base_ad_config, ["Hermes_Päckchen", "DHL_5"])

        with (
            patch.object(test_bot, "web_find", new_callable = AsyncMock) as mock_find,
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as mock_sleep,
            pytest.raises(ValueError, match = "one package size"),
        ):
            await set_shipping_options(test_bot, ad_cfg)

        # Validation errors must occur before any DOM interaction
        mock_find.assert_not_awaited()
        mock_click.assert_not_awaited()
        mock_sleep.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_timeout_in_dialog_raises(self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any]) -> None:
        """TimeoutError during dialog interaction is re-raised with descriptive message."""
        ad_cfg = self._make_ad_with_options(base_ad_config, ["Hermes_Päckchen"])

        with (
            patch.object(test_bot, "web_find", new_callable = AsyncMock, side_effect = TimeoutError("radio not found")),
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            pytest.raises(TimeoutError, match = "Failed to configure shipping options in dialog!"),
        ):
            await set_shipping_options(test_bot, ad_cfg)

    # ------------------------------------------------------------------
    # set_shipping_options: pre-DOM validation and close failure
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_shipping_options_missing_raises_value_error(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """Empty shipping_options should raise ValueError before any DOM interaction."""
        ad_cfg = Ad.model_validate(base_ad_config | {"shipping_type": "SHIPPING", "shipping_options": []})

        with (
            patch.object(test_bot, "web_find", new_callable = AsyncMock) as mock_find,
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as mock_sleep,
            pytest.raises(ValueError, match = "shipping_options must be provided"),
        ):
            await set_shipping_options(test_bot, ad_cfg)

        mock_find.assert_not_awaited()
        mock_click.assert_not_awaited()
        mock_sleep.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_shipping_options_close_timeout_raises(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """Timeout on the final Fertig click should raise TimeoutError."""
        ad_cfg = self._make_ad_with_options(base_ad_config, ["Hermes_P\u00e4ckchen"])

        radio_mock = self._mock_interactable_checkbox(checked = True)
        weiter_mock = MagicMock()
        weiter_mock.click = AsyncMock()
        fertig_mock = MagicMock()
        fertig_mock.click = AsyncMock(side_effect = TimeoutError("Fertig click timeout"))
        checkbox_mock = self._mock_interactable_checkbox(checked = True)

        async def find_side_effect(selector_type:By, selector_value:str, **_:Any) -> MagicMock:
            if selector_type == By.CSS_SELECTOR and "radio" in selector_value:
                return radio_mock
            if selector_type == By.TEXT and selector_value == "Weiter":
                return weiter_mock
            if selector_type == By.TEXT and selector_value == "Fertig":
                return fertig_mock
            if selector_type == By.CSS_SELECTOR and "checkbox" in selector_value:
                return checkbox_mock
            return checkbox_mock

        with (
            patch.object(test_bot, "web_find", new_callable = AsyncMock, side_effect = find_side_effect),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            pytest.raises(TimeoutError, match = "Unable to close shipping dialog!"),
        ):
            await set_shipping_options(test_bot, ad_cfg)

    @pytest.mark.asyncio
    async def test_shipping_options_mapping(self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any], tmp_path:Any) -> None:
        """Test that shipping options are mapped correctly."""
        # Create a mock page to simulate browser context
        test_bot.page = MagicMock()
        test_bot.page.url = "https://www.kleinanzeigen.de/p-anzeige-aufgeben-bestaetigung.html?adId=12345"
        test_bot.page.evaluate = AsyncMock()

        # Create ad config with specific shipping options
        ad_cfg = Ad.model_validate(
            base_ad_config
            | {
                "shipping_options": ["DHL_2", "Hermes_Päckchen"],
                "updated_on": "2024-01-01T00:00:00",  # Add created_on to prevent KeyError
                "created_on": "2024-01-01T00:00:00",  # Add updated_on for consistency
            }
        )

        # Create the original ad config and published ads list
        ad_cfg.update_content_hash()  # Add content hash to prevent republication
        ad_cfg_orig = ad_cfg.model_dump()
        published_ads:list[dict[str, Any]] = []

        # Set up default config values needed for the test
        test_bot.config.publishing = PublishingConfig.model_validate({"delete_old_ads": "BEFORE_PUBLISH", "delete_old_ads_by_title": False})

        # Create temporary file path
        ad_file = Path(tmp_path) / "test_ad.yaml"

        # Mock web_execute to handle all JavaScript calls
        async def mock_web_execute(script:str) -> Any:
            if script == "document.body.scrollHeight":
                return 0  # Return integer to prevent scrolling loop
            if "window.location.href" in script:
                return test_bot.page.url  # Return confirmation URL for ad_id extraction
            # Submit button JS click returns True (button found and clicked)
            if "querySelectorAll('button')" in script:
                return True
            return None

        # Create mock elements
        csrf_token_elem = MagicMock()
        csrf_token_elem.attrs = {"content": "csrf-token-123"}

        shipping_form_elem = MagicMock()
        shipping_form_elem.attrs = {}

        shipping_size_radio = MagicMock()
        shipping_size_radio.attrs = {"checked": ""}  # SMALL radio is pre-checked
        shipping_size_radio.click = AsyncMock()

        shipping_checkbox = MagicMock()
        shipping_checkbox.attrs = {"checked": ""}  # Simulate pre-checked carriers for SMALL
        shipping_checkbox.click = AsyncMock()

        weiter_mock = MagicMock()
        weiter_mock.click = AsyncMock()
        fertig_mock = MagicMock()
        fertig_mock.click = AsyncMock()

        category_path_elem = MagicMock()
        category_path_elem.apply = AsyncMock(return_value = "Test Category")

        # Mock the necessary web interaction methods
        with (
            patch.object(test_bot, "web_execute", side_effect = mock_web_execute),
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_find", new_callable = AsyncMock) as mock_find,
            patch.object(test_bot, "web_probe", new_callable = AsyncMock) as mock_probe,
            patch.object(test_bot, "web_select", new_callable = AsyncMock),
            patch.object(test_bot, "web_input", new_callable = AsyncMock),
            patch.object(test_bot, "web_open", new_callable = AsyncMock),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch.object(test_bot, "web_check", new_callable = AsyncMock, return_value = True),
            patch.object(test_bot, "web_request", new_callable = AsyncMock),
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock),
            patch.object(test_bot, "web_await", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.publishing_form.set_contact_fields", new_callable = AsyncMock),
            patch("builtins.input", return_value = ""),
            patch.object(test_bot, "web_scroll_page_down", new_callable = AsyncMock),
        ):

            async def probe_side_effect(selector_type:By, selector_value:str, **_:Any) -> Element | None:
                if selector_type == By.ID and selector_value == "ad-category-path":
                    return category_path_elem
                # Shipping size radio via CSS probe
                if selector_type == By.CSS_SELECTOR and "radio" in selector_value and "SMALL" in selector_value:
                    return shipping_size_radio
                return None

            mock_probe.side_effect = probe_side_effect

            # Mock web_find to simulate element detection
            async def mock_find_side_effect(selector_type:By, selector_value:str, **_:Any) -> Element | None:
                if selector_value == "meta[name=_csrf]":
                    return csrf_token_elem
                if selector_value == "myftr-shppngcrt-frm":
                    return shipping_form_elem
                # Category link via By.TEXT
                if selector_type == By.TEXT and selector_value in {"W\u00e4hle deine Kategorie", "Kategorie"}:
                    cat_link = MagicMock()
                    cat_link.click = AsyncMock()
                    return cat_link
                if selector_type == By.TEXT and selector_value == "Weiter":
                    return weiter_mock
                # New shipping dialog: size radio via CSS with value attribute
                if selector_type == By.CSS_SELECTOR and "radio" in selector_value and "SMALL" in selector_value:
                    return shipping_size_radio
                if selector_type == By.CSS_SELECTOR and "checkbox" in selector_value:
                    return shipping_checkbox
                if selector_type == By.TEXT and selector_value == "Fertig":
                    return fertig_mock
                return None

            mock_find.side_effect = mock_find_side_effect

            # Mock web_check to return True for radio button checked state
            with patch.object(test_bot, "web_check", new_callable = AsyncMock) as mock_check:
                mock_check.return_value = True

                # Test through the public interface by publishing an ad
                await test_bot.publish_ad(str(ad_file), ad_cfg, ad_cfg_orig, published_ads)

            # Verify that the shipping dialog was interacted with:
            # - web_find should have been called for the size radio (CSS with radio+SMALL)
            radio_find_calls = [c for c in mock_find.await_args_list if len(c.args) >= 2 and "radio" in str(c.args[1]) and "SMALL" in str(c.args[1])]
            assert len(radio_find_calls) >= 1, "Expected at least one web_find for size radio"

            # Verify Weiter and Fertig were found via By.TEXT
            text_finds = [str(c.args[1]) for c in mock_find.await_args_list if len(c.args) >= 2 and c.args[0] == By.TEXT]
            assert any("Weiter" in v for v in text_finds), "Expected find on Weiter button"
            assert any("Fertig" in v for v in text_finds), "Expected find on Fertig button"

            # Verify the file was created in the temporary directory
            assert ad_file.exists()


class TestWantedShippingSelection:
    """Tests for WANTED shipping path via set_shipping_form.

    WANTED ads render shipping as a special-attribute combobox dropdown
    (``<button role="combobox">``) rather than radio buttons. These tests
    verify ``set_shipping_form`` directly with WANTED ad configurations.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("shipping_type", "expected_label"),
        [("SHIPPING", "Versand m\u00f6glich"), ("PICKUP", "Nur Abholung")],
        ids = ["shipping", "pickup"],
    )
    async def test_wanted_shipping_selects_combobox_dropdown(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        shipping_type:str,
        expected_label:str,
    ) -> None:
        """WANTED ads should select shipping via button-combobox dropdown using individual CSS probes."""
        ad_cfg = Ad.model_validate(
            base_ad_config
            | {
                "type": "WANTED",
                "shipping_type": shipping_type,
                "shipping_options": [],
            }
        )

        combobox_btn = MagicMock()
        combobox_btn.attrs = {"id": "babyausstattung.versand"}

        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = combobox_btn) as mock_probe,
            patch.object(test_bot, "web_select_button_combobox", new_callable = AsyncMock) as mock_select_btn_combo,
        ):
            await set_shipping_form(test_bot, ad_cfg)

        mock_select_btn_combo.assert_awaited_once_with(
            "babyausstattung.versand",
            expected_label,
        )
        # Verify at least one CSS probe was made
        assert mock_probe.await_count >= 1
        assert mock_probe.await_args is not None
        assert mock_probe.await_args.args[0] == By.CSS_SELECTOR

    @pytest.mark.asyncio
    async def test_wanted_shipping_raises_when_combobox_not_found(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """WANTED shipping should fail with TimeoutError when the combobox button cannot be found."""
        ad_cfg = Ad.model_validate(
            base_ad_config
            | {
                "type": "WANTED",
                "shipping_type": "SHIPPING",
                "shipping_options": [],
            }
        )

        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = None),
            patch.object(test_bot, "web_select_button_combobox", new_callable = AsyncMock),
            pytest.raises(TimeoutError, match = "Failed to set shipping attribute for type 'SHIPPING'!"),
        ):
            await set_shipping_form(test_bot, ad_cfg)

    @pytest.mark.asyncio
    async def test_wanted_shipping_not_applicable_skips_combobox(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """WANTED ads with NOT_APPLICABLE shipping should skip the combobox entirely."""
        ad_cfg = Ad.model_validate(
            base_ad_config
            | {
                "type": "WANTED",
                "shipping_type": "NOT_APPLICABLE",
                "shipping_options": [],
            }
        )

        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock) as mock_probe,
            patch.object(test_bot, "web_select_button_combobox", new_callable = AsyncMock) as mock_select_btn_combo,
        ):
            await set_shipping_form(test_bot, ad_cfg)

        mock_probe.assert_not_awaited()
        mock_select_btn_combo.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_wanted_shipping_raises_when_combobox_has_no_id(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """WANTED shipping should fail with TimeoutError when the combobox button has no id attribute."""
        ad_cfg = Ad.model_validate(
            base_ad_config
            | {
                "type": "WANTED",
                "shipping_type": "SHIPPING",
                "shipping_options": [],
            }
        )

        combobox_btn = MagicMock()
        combobox_btn.attrs = {}  # No "id" key

        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = combobox_btn),
            patch.object(test_bot, "web_select_button_combobox", new_callable = AsyncMock),
            pytest.raises(TimeoutError, match = "Failed to set shipping attribute for type 'SHIPPING'!"),
        ):
            await set_shipping_form(test_bot, ad_cfg)


class TestSelectButtonCombobox:
    """Tests for _select_button_combobox (single async script combobox selection).

    ``_select_button_combobox`` is the internal helper used for special-attribute
    button comboboxes. It executes a single async JS script that opens the control
    via a full native click sequence (pointerdown → mousedown → mouseup → click),
    polls for visible options, and clicks the matching option by value or text.
    """

    @pytest.mark.asyncio
    async def test_select_button_combobox_calls_web_execute_directly(
        self,
        test_bot:KleinanzeigenBot,
    ) -> None:
        """Successful selection: calls web_execute directly with no separate click/find."""
        elem_id = "my-combobox-id"
        option_value = "option_value"

        with (
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            patch.object(test_bot, "web_find", new_callable = AsyncMock) as mock_find,
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, return_value = {"ok": True}) as mock_execute,
        ):
            await _select_button_combobox(test_bot, elem_id, option_value)

        mock_click.assert_not_awaited()
        mock_find.assert_not_awaited()
        mock_execute.assert_awaited_once()
        assert mock_execute.await_args is not None
        js = str(mock_execute.await_args.args[0])
        # Stable contract: a single browser script receives safely quoted inputs.
        assert json.dumps(elem_id) in js
        assert json.dumps(option_value) in js

    @pytest.mark.asyncio
    async def test_select_button_combobox_js_contains_full_click_sequence(
        self,
        test_bot:KleinanzeigenBot,
    ) -> None:
        """The injected JS must dispatch a full click sequence (including mouseup/click)
        and include document-level listbox search for portal-rendered menus."""
        elem_id = "my-combobox-id"
        option_value = "option_value"

        with (
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, return_value = {"ok": True}) as mock_execute,
        ):
            await _select_button_combobox(test_bot, elem_id, option_value)

        assert mock_execute.await_args is not None
        js = str(mock_execute.await_args.args[0])
        # Full click sequence: pointerdown + mousedown + mouseup + click
        assert "PointerEvent('pointerdown'" in js
        assert "MouseEvent('mousedown'" in js
        assert "MouseEvent('mouseup'" in js
        assert "MouseEvent('click'" in js
        # Scoped portal fallback: aria-controls/owns association first, then visible portal candidates with aria-labelledby
        assert "btn.getAttribute('aria-controls')" in js
        assert "document.getElementById(controlledId)" in js
        assert "btn.getAttribute('aria-owns')" in js
        assert "document.getElementById(ownedId)" in js
        assert "document.querySelectorAll('[role=\"listbox\"],[role=\"menu\"]')" in js
        assert "getBoundingClientRect" in js
        assert "aria-labelledby" in js
        # Whitespace normalization in text matching
        assert ".replace(/\\s+/g,' ').trim()" in js

    @pytest.mark.asyncio
    async def test_select_button_combobox_no_options_failure_raises_timeout(
        self,
        test_bot:KleinanzeigenBot,
    ) -> None:
        """When JS returns no_options_after_opening, TimeoutError contains the mapped label."""
        elem_id = "my-combobox-id"
        option_value = "missing_option"
        js_status = {
            "ok": False,
            "reason": "no_options_after_opening",
            "options": [],
        }

        with (
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, return_value = js_status),
            pytest.raises(TimeoutError) as exc_info,
        ):
            await _select_button_combobox(test_bot, elem_id, option_value)

        msg = str(exc_info.value)
        assert "my-combobox-id" in msg
        assert "No options appeared after opening" in msg

    @pytest.mark.asyncio
    async def test_select_button_combobox_structured_failure_raises_timeout(
        self,
        test_bot:KleinanzeigenBot,
    ) -> None:
        """When JS returns {ok: False, reason_code, ...}, TimeoutError contains the mapped label."""
        elem_id = "my-combobox-id"
        option_value = "missing_option"
        js_status = {
            "ok": False,
            "reason": "option_not_found",
            "requested": "missing_option",
            "options": ["Option A", "Option B"],
        }

        with (
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, return_value = js_status),
            pytest.raises(TimeoutError) as exc_info,
        ):
            await _select_button_combobox(test_bot, elem_id, option_value)

        msg = str(exc_info.value)
        assert "missing_option" in msg
        assert "my-combobox-id" in msg
        # JS code "option_not_found" is mapped to label "Option not found" via _().
        assert "Option not found" in msg
        assert "Option A" in msg

    @pytest.mark.asyncio
    async def test_select_button_combobox_bare_false_raises_timeout(
        self,
        test_bot:KleinanzeigenBot,
    ) -> None:
        """When JS returns a bare False (legacy fallback), TimeoutError is still raised."""
        elem_id = "my-combobox-id"
        option_value = "missing_option"

        with (
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, return_value = False),
            pytest.raises(TimeoutError),
        ):
            await _select_button_combobox(test_bot, elem_id, option_value)

    @pytest.mark.asyncio
    async def test_select_button_combobox_element_id_and_value_escaped_via_json_dumps(
        self,
        test_bot:KleinanzeigenBot,
    ) -> None:
        """elem_id and option_value must be embedded in JS via json.dumps (safe quoting)."""
        elem_id = 'danger"id'
        option_value = 'bad"value'

        with (
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, return_value = {"ok": True}) as mock_execute,
        ):
            await _select_button_combobox(test_bot, elem_id, option_value)

        assert mock_execute.await_args is not None
        js = str(mock_execute.await_args.args[0])
        assert json.dumps(elem_id) in js
        assert json.dumps(option_value) in js


class TestSpecialAttributes:
    """Tests for special attribute handling."""

    @pytest.mark.asyncio
    async def test_special_attributes_compound_name_lookup(self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any]) -> None:
        """Compound special-attribute names should be matched via original key in @name."""
        ad_cfg = Ad.model_validate(
            base_ad_config
            | {
                "special_attributes": {"autos.model_s": "a3"},
                "updated_on": "2024-01-01T00:00:00",
                "created_on": "2024-01-01T00:00:00",
            }
        )

        model_elem = MagicMock()
        model_attrs = MagicMock()
        model_attrs.id = None
        model_attrs.name = "attributeMap[autos.marke_s+autos.model_s]"
        model_attrs.get.side_effect = lambda key, default = None: {
            "id": None,
            "name": "attributeMap[autos.marke_s+autos.model_s]",
            "type": None,
            "role": None,
        }.get(key, default)
        model_elem.attrs = model_attrs
        model_elem.local_name = "select"

        with (
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock) as mock_find_all,
            patch.object(test_bot, "web_select", new_callable = AsyncMock) as mock_select,
        ):

            async def find_all_side_effect(selector_type:By, selector_value:str, **_:Any) -> list[Element]:
                if selector_type == By.XPATH and "autos.model_s" in selector_value:
                    return [model_elem]
                return []

            mock_find_all.side_effect = find_all_side_effect

            await set_special_attributes(test_bot, ad_cfg)

            assert mock_select.await_count == 1
            assert mock_select.await_args is not None
            assert mock_select.await_args.args[0] == By.CSS_SELECTOR
            assert "attributeMap[autos.marke_s+autos.model_s]" in str(mock_select.await_args.args[1])
            assert mock_select.await_args.args[2] == "a3"

    @pytest.mark.asyncio
    async def test_special_attributes_prefers_button_combobox_over_hidden_input(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """Hidden backing inputs must not win over visible button combobox controls."""
        ad_cfg = Ad.model_validate(
            base_ad_config
            | {
                "special_attributes": {"color_s": "beige"},
                "updated_on": "2024-01-01T00:00:00",
                "created_on": "2024-01-01T00:00:00",
            }
        )

        hidden_elem = MagicMock()
        hidden_attrs = MagicMock()
        hidden_attrs.id = None
        hidden_attrs.type = "hidden"
        hidden_attrs.get.side_effect = lambda key, default = None: {
            "id": None,
            "name": "attributeMap[kleidung_herren.color]",
            "type": "hidden",
            "role": None,
        }.get(key, default)
        hidden_elem.attrs = hidden_attrs
        hidden_elem.local_name = "input"

        button_elem = MagicMock()
        button_attrs = MagicMock()
        button_attrs.id = "kleidung_herren.color"
        button_attrs.type = "button"
        button_attrs.get.side_effect = lambda key, default = None: {
            "id": "kleidung_herren.color",
            "name": None,
            "type": "button",
            "role": "combobox",
        }.get(key, default)
        button_elem.attrs = button_attrs
        button_elem.local_name = "button"

        with (
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, return_value = [hidden_elem, button_elem]),
            patch("kleinanzeigen_bot.publishing_form._select_button_combobox", new_callable = AsyncMock) as mock_button_combobox,
            patch.object(test_bot, "web_input", new_callable = AsyncMock) as mock_input,
        ):
            await set_special_attributes(test_bot, ad_cfg)

        mock_button_combobox.assert_awaited_once_with(test_bot, "kleidung_herren.color", "beige")
        mock_input.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("combobox_type", ["text", None], ids = ["type-text", "type-absent"])
    async def test_special_attributes_combobox_routed_over_hidden_input(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        combobox_type:str | None,
    ) -> None:
        """Combobox <input> must be routed to web_select_combobox regardless of type attribute presence."""
        ad_cfg = Ad.model_validate(
            base_ad_config
            | {
                "special_attributes": {"brand_s": "armani"},
                "updated_on": "2024-01-01T00:00:00",
                "created_on": "2024-01-01T00:00:00",
            }
        )

        hidden_elem = MagicMock()
        hidden_attrs = MagicMock()
        hidden_attrs.id = None
        hidden_attrs.type = "hidden"
        hidden_attrs.get.side_effect = lambda key, default = None: {
            "id": None,
            "name": "attributeMap[kleidung_herren.brand]",
            "type": "hidden",
            "role": None,
        }.get(key, default)
        hidden_elem.attrs = hidden_attrs
        hidden_elem.local_name = "input"

        combobox_elem = MagicMock()
        combobox_attrs = MagicMock()
        combobox_attrs.id = "kleidung_herren.brand"
        combobox_attrs.type = combobox_type
        combobox_attrs.get.side_effect = lambda key, default = None: {
            "id": "kleidung_herren.brand",
            "name": None,
            "type": combobox_type,
            "role": "combobox",
        }.get(key, default)
        combobox_elem.attrs = combobox_attrs
        combobox_elem.local_name = "input"

        with (
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, return_value = [hidden_elem, combobox_elem]),
            patch.object(test_bot, "web_select_combobox", new_callable = AsyncMock) as mock_select_combobox,
            patch.object(test_bot, "web_input", new_callable = AsyncMock) as mock_input,
        ):
            await set_special_attributes(test_bot, ad_cfg)

        mock_select_combobox.assert_awaited_once_with(By.ID, "kleidung_herren.brand", "armani")
        mock_input.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("checked_attr", "attribute_value", "expect_click"),
        [(None, "true", True), ("checked", "true", False), ("checked", "false", True)],
    )
    async def test_special_attributes_checkbox_clicks_only_on_state_change(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        checked_attr:str | None,
        attribute_value:str,
        expect_click:bool,
    ) -> None:
        """Checkbox attributes should only click when current and desired states differ."""
        ad_cfg = Ad.model_validate(
            base_ad_config
            | {
                "special_attributes": {"feature_b": attribute_value},
                "updated_on": "2024-01-01T00:00:00",
                "created_on": "2024-01-01T00:00:00",
            }
        )

        checkbox_elem = MagicMock()
        checkbox_attrs = MagicMock()
        checkbox_attrs.id = "feature"
        checkbox_attrs.type = "checkbox"
        checkbox_attrs.get.side_effect = lambda key, default = None: {
            "id": "feature",
            "name": "attributeMap[feature]",
            "type": "checkbox",
            "role": None,
            "checked": checked_attr,
        }.get(key, default)
        checkbox_elem.attrs = checkbox_attrs
        checkbox_elem.local_name = "input"

        with (
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, return_value = [checkbox_elem]),
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
        ):
            await set_special_attributes(test_bot, ad_cfg)

        if expect_click:
            mock_click.assert_awaited_once_with(By.ID, "feature")
        else:
            mock_click.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_hidden_input_fallback_finds_associated_button_combobox(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """When XPath matches only a hidden backing input (dynamic React IDs),
        the fallback should locate the associated <button role="combobox"> and use
        _select_button_combobox.  Regression test for issue #1096."""
        ad_cfg = Ad.model_validate(
            base_ad_config
            | {
                "special_attributes": {"groesse_s": "68"},
                "updated_on": "2024-01-01T00:00:00",
                "created_on": "2024-01-01T00:00:00",
            }
        )

        hidden_elem = MagicMock()
        hidden_attrs = MagicMock()
        hidden_attrs.id = None
        hidden_attrs.type = "hidden"
        hidden_attrs.get.side_effect = lambda key, default = None: {
            "id": None,
            "name": "attributeMap[groesse]",
            "type": "hidden",
            "role": None,
        }.get(key, default)
        hidden_elem.attrs = hidden_attrs
        hidden_elem.local_name = "input"

        with (
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, return_value = [hidden_elem]),
            patch.object(
                test_bot,
                "_find_associated_button_combobox",
                new_callable = AsyncMock,
                return_value = ":r8r7:",
            ) as mock_find_button,
            patch(
                "kleinanzeigen_bot.publishing_form._select_button_combobox",
                new_callable = AsyncMock,
            ) as mock_select_combobox,
            patch.object(test_bot, "web_input", new_callable = AsyncMock) as mock_input,
        ):
            await set_special_attributes(test_bot, ad_cfg)

        mock_find_button.assert_awaited_once_with(hidden_input_name = "attributeMap[groesse]")
        mock_select_combobox.assert_awaited_once_with(test_bot, ":r8r7:", "68")
        mock_input.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_hidden_input_fallback_no_associated_button_raises(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """When the fallback cannot find an associated button combobox, a
        TimeoutError is raised instead of falling through to the text-input handler."""
        ad_cfg = Ad.model_validate(
            base_ad_config
            | {
                "special_attributes": {"color_s": "beige"},
                "updated_on": "2024-01-01T00:00:00",
                "created_on": "2024-01-01T00:00:00",
            }
        )

        hidden_elem = MagicMock()
        hidden_attrs = MagicMock()
        hidden_attrs.id = None
        hidden_attrs.type = "hidden"
        hidden_attrs.get.side_effect = lambda key, default = None: {
            "id": None,
            "name": "attributeMap[color]",
            "type": "hidden",
            "role": None,
        }.get(key, default)
        hidden_elem.attrs = hidden_attrs
        hidden_elem.local_name = "input"

        with (
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, return_value = [hidden_elem]),
            patch.object(
                test_bot,
                "_find_associated_button_combobox",
                new_callable = AsyncMock,
                return_value = None,
            ) as mock_find_button,pytest.raises(TimeoutError)
        ):
            await set_special_attributes(test_bot, ad_cfg)

        mock_find_button.assert_awaited_once_with(hidden_input_name = "attributeMap[color]")

    @pytest.mark.asyncio
    async def test_fallback_not_triggered_when_button_combobox_directly_matched(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """When the XPath directly matches a <button role="combobox"> (not just
        a hidden input), the fallback search should NOT be triggered — the normal
        dispatch path handles it.  This guards against unnecessary JS execution."""
        ad_cfg = Ad.model_validate(
            base_ad_config
            | {
                "special_attributes": {"type_s": "accessoires"},
                "updated_on": "2024-01-01T00:00:00",
                "created_on": "2024-01-01T00:00:00",
            }
        )

        button_elem = MagicMock()
        button_attrs = MagicMock()
        button_attrs.id = "kleidung_herren.type"
        button_attrs.type = "button"
        button_attrs.get.side_effect = lambda key, default = None: {
            "id": "kleidung_herren.type",
            "name": None,
            "type": "button",
            "role": "combobox",
        }.get(key, default)
        button_elem.attrs = button_attrs
        button_elem.local_name = "button"

        with (
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, return_value = [button_elem]),
            patch.object(
                test_bot,
                "_find_associated_button_combobox",
                new_callable = AsyncMock,
            ) as mock_find_button,
            patch(
                "kleinanzeigen_bot.publishing_form._select_button_combobox",
                new_callable = AsyncMock,
            ) as mock_select_combobox,
        ):
            await set_special_attributes(test_bot, ad_cfg)

        mock_find_button.assert_not_awaited()
        mock_select_combobox.assert_awaited_once_with(test_bot, "kleidung_herren.type", "accessoires")

    def test_special_attribute_candidate_priority_textarea(self) -> None:
        """A textarea element should return priority (4, 0)."""
        elem = MagicMock()
        elem.local_name = "textarea"
        elem.attrs = {}
        assert _special_attribute_candidate_priority(elem) == (4, 0)

    def test_special_attribute_candidate_priority_fallback(self) -> None:
        """An unrecognized element (e.g. div with no matching attrs) should return priority (8, 0)."""
        elem = MagicMock()
        elem.local_name = "div"
        elem.attrs = {}
        assert _special_attribute_candidate_priority(elem) == (8, 0)


class TestConditionSelector:
    """Regression tests for condition dialog selection."""

    @pytest.mark.asyncio
    async def test_condition_selects_radio_by_value(self, test_bot:KleinanzeigenBot) -> None:
        """Condition selection should resolve radios by value in the new dialog."""
        dialog = MagicMock()
        trigger_btn = MagicMock()
        trigger_btn.click = AsyncMock()
        radio = MagicMock()
        radio_attrs = MagicMock()
        radio_attrs.id = "radio-condition-ok"
        radio_attrs.get.side_effect = lambda key, default = None: "radio-condition-ok" if key == "id" else default
        radio.attrs = radio_attrs
        radio.click = AsyncMock()
        label_elem = MagicMock()
        label_elem.click = AsyncMock()
        bestaetigen_btn = MagicMock()
        bestaetigen_btn.click = AsyncMock()

        # web_execute returns a JSON string describing the located trigger button.
        trigger_info = '{"found": true, "id": "condition-trigger", "ariaControls": "condition-dialog"}'

        async def probe_side_effect(selector_type:By, selector_value:str, **_:Any) -> Element | None:
            # radio lookup returns the matching radio (CSS selector scoped to open dialog)
            if selector_type == By.CSS_SELECTOR and 'input[type="radio"]' in selector_value and '"ok"' in selector_value:
                return radio
            return None

        async def find_side_effect(selector_type:By, selector_value:str, **_:Any) -> Element:
            if selector_type == By.CSS_SELECTOR and selector_value == "dialog[open]":
                return dialog
            if selector_type == By.ID and selector_value == "condition-trigger":
                return trigger_btn
            if selector_type == By.CSS_SELECTOR and 'label[for="radio-condition-ok"]' in selector_value:
                return label_elem
            if selector_type == By.TEXT and selector_value == "Best\u00e4tigen":
                return bestaetigen_btn
            raise TimeoutError(f"unexpected find: {selector_type} {selector_value}")

        with (
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, return_value = trigger_info),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = probe_side_effect),
            patch.object(test_bot, "web_find", new_callable = AsyncMock, side_effect = find_side_effect),
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
        ):
            handled = await _set_condition(test_bot, "ok")

            assert handled is True

            # Trigger button is clicked via web_find(By.ID) + .click(), not web_click.
            trigger_btn.click.assert_awaited_once()
            # Label for the matched radio is clicked directly.
            label_elem.click.assert_awaited_once()
            # Bestätigen button is clicked directly.
            bestaetigen_btn.click.assert_awaited_once()
            # web_click is no longer used by the condition dialog path.
            mock_click.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("configured", "expected_api_value", "expect_warning"),
        [
            ("wie_neu", "like_new", True),
            ("sehr_gut", "like_new", True),
            ("new", "new", False),
            ("like_new", "like_new", False),
            ("ok", "ok", False),
            ("alright", "alright", False),
            ("defect", "defect", False),
        ],
    )
    async def test_condition_tokens_warn_only_for_legacy_values(
        self,
        test_bot:KleinanzeigenBot,
        configured:str,
        expected_api_value:str,
        expect_warning:bool,
        caplog:pytest.LogCaptureFixture,
    ) -> None:
        """Condition tokens should resolve to the current API codes and warn only for legacy German values."""
        caplog.set_level(logging.WARNING, logger = LOG.name)
        dialog = MagicMock()
        trigger_btn = MagicMock()
        trigger_btn.click = AsyncMock()
        radio = MagicMock()
        radio_attrs = MagicMock()
        radio_attrs.get.side_effect = lambda key, default = None: f"radio-condition-{expected_api_value}" if key == "id" else default
        radio.attrs = radio_attrs
        radio.click = AsyncMock()
        label_elem = MagicMock()
        label_elem.click = AsyncMock()
        bestaetigen_btn = MagicMock()
        bestaetigen_btn.click = AsyncMock()

        trigger_info = '{"found": true, "id": "condition-trigger", "ariaControls": "condition-dialog"}'
        probed_values:list[str] = []

        async def probe_side_effect(selector_type:By, selector_value:str, **_:Any) -> Element | None:
            if selector_type == By.CSS_SELECTOR and 'input[type="radio"]' in selector_value:
                if f'value="{expected_api_value}"' in selector_value:
                    probed_values.append(expected_api_value)
                    return radio
                if f'value="{configured}"' in selector_value:
                    probed_values.append(configured)
                    return radio
            return None

        async def find_side_effect(selector_type:By, selector_value:str, **_:Any) -> Element:
            if selector_type == By.CSS_SELECTOR and selector_value == "dialog[open]":
                return dialog
            if selector_type == By.ID and selector_value == "condition-trigger":
                return trigger_btn
            if selector_type == By.CSS_SELECTOR and f'label[for="radio-condition-{expected_api_value}"]' in selector_value:
                return label_elem
            if selector_type == By.TEXT and selector_value == "Best\u00e4tigen":
                return bestaetigen_btn
            raise TimeoutError(f"unexpected find: {selector_type} {selector_value}")

        with (
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, return_value = trigger_info),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = probe_side_effect),
            patch.object(test_bot, "web_find", new_callable = AsyncMock, side_effect = find_side_effect),
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
        ):
            handled = await _set_condition(test_bot, configured)

        assert handled is True
        warning_messages = [record.message for record in caplog.records if record.levelno == logging.WARNING]
        if expect_warning:
            assert len(warning_messages) == 1
            assert configured in warning_messages[0]
            assert expected_api_value in warning_messages[0]
            # Legacy German values should prefer the mapped API code and stop once it is found.
            assert probed_values == [expected_api_value]
        else:
            assert warning_messages == []
            assert probed_values == [configured]
        # The label for the resolved radio is clicked directly (not via web_click).
        label_elem.click.assert_awaited_once()
        mock_click.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_condition_legacy_value_falls_back_when_mapped_value_is_missing(
        self,
        test_bot:KleinanzeigenBot,
        caplog:pytest.LogCaptureFixture,
    ) -> None:
        """Legacy values should still work if the mapped API radio is unavailable."""
        caplog.set_level(logging.WARNING, logger = LOG.name)
        dialog = MagicMock()
        trigger_btn = MagicMock()
        trigger_btn.click = AsyncMock()
        radio = MagicMock()
        radio_attrs = MagicMock()
        radio_attrs.id = "radio-condition-wie_neu"
        radio_attrs.get.side_effect = lambda key, default = None: "radio-condition-wie_neu" if key == "id" else default
        radio.attrs = radio_attrs
        radio.click = AsyncMock()
        label_elem = MagicMock()
        label_elem.click = AsyncMock()
        bestaetigen_btn = MagicMock()
        bestaetigen_btn.click = AsyncMock()

        trigger_info = '{"found": true, "id": "condition-trigger", "ariaControls": "condition-dialog"}'
        probed_values:list[str] = []

        async def probe_side_effect(selector_type:By, selector_value:str, **_:Any) -> Element | None:
            if selector_type == By.CSS_SELECTOR and 'input[type="radio"]' in selector_value:
                if 'value="like_new"' in selector_value:
                    probed_values.append("like_new")
                    return None
                if 'value="wie_neu"' in selector_value:
                    probed_values.append("wie_neu")
                    return radio
            return None

        async def find_side_effect(selector_type:By, selector_value:str, **_:Any) -> Element:
            if selector_type == By.CSS_SELECTOR and selector_value == "dialog[open]":
                return dialog
            if selector_type == By.ID and selector_value == "condition-trigger":
                return trigger_btn
            if selector_type == By.CSS_SELECTOR and 'label[for="radio-condition-wie_neu"]' in selector_value:
                return label_elem
            if selector_type == By.TEXT and selector_value == "Best\u00e4tigen":
                return bestaetigen_btn
            raise TimeoutError(f"unexpected find: {selector_type} {selector_value}")

        with (
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, return_value = trigger_info),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = probe_side_effect),
            patch.object(test_bot, "web_find", new_callable = AsyncMock, side_effect = find_side_effect),
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
        ):
            handled = await _set_condition(test_bot, "wie_neu")

        assert handled is True
        warning_messages = [record.message for record in caplog.records if record.levelno == logging.WARNING]
        assert len(warning_messages) == 1
        assert "wie_neu" in warning_messages[0]
        assert "like_new" in warning_messages[0]
        assert probed_values == ["like_new", "wie_neu"]
        # The legacy radio's label is clicked directly.
        label_elem.click.assert_awaited_once()
        mock_click.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_condition_legacy_value_warns_even_when_not_handled(
        self,
        test_bot:KleinanzeigenBot,
        caplog:pytest.LogCaptureFixture,
    ) -> None:
        """Legacy German values should warn even when the condition dialog path is unavailable."""
        caplog.set_level(logging.WARNING, logger = LOG.name)

        with (
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, return_value = '{"found": false}'),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = None),
        ):
            handled = await _set_condition(test_bot, "wie_neu")

        assert handled is False
        warning_messages = [record.message for record in caplog.records if record.levelno == logging.WARNING]
        assert len(warning_messages) == 1
        assert "wie_neu" in warning_messages[0]
        assert "like_new" in warning_messages[0]

    @pytest.mark.asyncio
    async def test_condition_unknown_value_raises(self, test_bot:KleinanzeigenBot) -> None:
        """Unknown condition values should raise when no matching radio option is present."""
        dialog = MagicMock()
        trigger_btn = MagicMock()
        trigger_btn.click = AsyncMock()

        trigger_info = '{"found": true, "id": "condition-trigger", "ariaControls": "condition-dialog"}'

        async def probe_side_effect(selector_type:By, selector_value:str, **_:Any) -> Element | None:
            # Radio lookups for unknown values return None (no matching radio in the dialog).
            return None

        async def find_side_effect(selector_type:By, selector_value:str, **_:Any) -> Element:
            if selector_type == By.CSS_SELECTOR and selector_value == "dialog[open]":
                return dialog
            if selector_type == By.ID and selector_value == "condition-trigger":
                return trigger_btn
            raise TimeoutError(f"unexpected find: {selector_type} {selector_value}")

        with (
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, return_value = trigger_info),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = probe_side_effect),
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_find", new_callable = AsyncMock, side_effect = find_side_effect),
            pytest.raises(TimeoutError, match = "Failed to set attribute 'condition_s'"),
        ):
            await _set_condition(test_bot, "totally_unknown_value")

    @pytest.mark.asyncio
    async def test_condition_rejects_shipping_trigger(self, test_bot:KleinanzeigenBot) -> None:
        """Condition dialog path should not click shipping trigger controls."""
        # The JS trigger-location helper resolves to a shipping-related button;
        # the condition dialog path must skip it and fall back to generic handling.
        trigger_info = '{"found": true, "id": "ad-shipping-options", "ariaControls": ""}'

        with (
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, return_value = trigger_info),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = None),
            patch.object(test_bot, "web_find", new_callable = AsyncMock, side_effect = TimeoutError("unexpected find")),
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
        ):
            handled = await _set_condition(test_bot, "new")

        assert handled is False
        # Regression guard: wrong shipping trigger must never be clicked by condition handler
        mock_click.assert_not_awaited()


class TestConditionFallbackToGenericHandler:
    """Regression tests for condition_s fallback behavior.

    When _set_condition reports "not handled" (e.g. category uses a button-combobox
    instead of a dialog), set_special_attributes should fall through to the generic
    XPath-based handler.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("condition_s", "expected_generic_value"),
        [
            ("new", "new"),
            ("wie_neu", "like_new"),
        ],
    )
    async def test_condition_falls_back_to_generic_handler_with_canonical_value(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        condition_s:str,
        expected_generic_value:str,
    ) -> None:
        """Fallback should pass the canonical condition value to the generic combobox handler."""
        ad_cfg = Ad.model_validate(base_ad_config | {"category": "185/249", "special_attributes": {"condition_s": condition_s}, "shipping_type": "PICKUP"})

        button_elem = MagicMock()
        button_attrs = MagicMock()
        button_attrs.get.side_effect = lambda key, default = None: {
            "id": "modellbau.condition",
            "type": "button",
            "role": "combobox",
            "name": None,
        }.get(key, default)
        button_elem.attrs = button_attrs
        button_elem.local_name = "button"
        probe_elem = MagicMock()
        probe_elem.attrs = {"id": "modellbau.condition"}

        with (
            patch(
                "kleinanzeigen_bot.publishing_form._set_condition",
                new_callable = AsyncMock,
                return_value = False,
            ) as mock_set_condition,
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = probe_elem),
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, return_value = [button_elem]),
            patch(
                "kleinanzeigen_bot.publishing_form._select_button_combobox",
                new_callable = AsyncMock,
            ) as mock_select_combobox,
        ):
            await set_special_attributes(test_bot, ad_cfg)

        mock_set_condition.assert_awaited_once_with(test_bot, condition_s)
        mock_select_combobox.assert_awaited_once_with(test_bot, "modellbau.condition", expected_generic_value)

    @pytest.mark.asyncio
    async def test_condition_timeout_propagates_instead_of_falling_back(self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any]) -> None:
        """Real condition dialog failures should propagate and not silently use generic fallback."""
        ad_cfg = Ad.model_validate(base_ad_config | {"category": "161/176", "special_attributes": {"condition_s": "ok"}})

        with (
            patch(
                "kleinanzeigen_bot.publishing_form._set_condition",
                new_callable = AsyncMock,
                side_effect = TimeoutError("dialog timeout"),
            ),
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock) as mock_find_all,
            pytest.raises(TimeoutError, match = "dialog timeout"),
        ):
            await set_special_attributes(test_bot, ad_cfg)

        mock_find_all.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_condition_s_missing_control_logs_warning_and_continues(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        caplog:pytest.LogCaptureFixture,
    ) -> None:
        """Missing condition_s control should warn and allow later attributes to continue."""
        caplog.set_level(logging.WARNING, logger = LOG.name)
        ad_cfg = Ad.model_validate(
            base_ad_config
            | {
                "category": "185/249",
                "special_attributes": {"condition_s": "ok", "color_s": "beige"},
                "shipping_type": "PICKUP",
            }
        )

        color_elem = MagicMock()
        color_attrs = MagicMock()
        color_attrs.id = "kleidung_herren.color"
        color_attrs.get.side_effect = lambda key, default = None: {
            "id": "kleidung_herren.color",
            "type": "button",
            "role": "combobox",
            "name": None,
        }.get(key, default)
        color_elem.attrs = color_attrs
        color_elem.local_name = "button"

        condition_probe = MagicMock()
        condition_probe.attrs = {"id": "condition_s"}

        async def find_all_side_effect(selector_type:By, selector_value:str, **_:Any) -> list[Element]:
            if selector_type == By.XPATH and "color_s" in selector_value:
                return [color_elem]
            if selector_type == By.XPATH and "condition_s" in selector_value:
                raise AssertionError("condition_s lookup should be skipped when the probe returns None")
            return []

        async def probe_side_effect(selector_type:By, selector_value:str, **_:Any) -> Element | None:
            if selector_type == By.XPATH and "condition_s" in selector_value:
                return None
            if selector_type == By.XPATH and "color_s" in selector_value:
                return condition_probe
            return None

        with (
            patch("kleinanzeigen_bot.publishing_form._set_condition", new_callable = AsyncMock, return_value = False) as mock_set_condition,
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = probe_side_effect),
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, side_effect = find_all_side_effect),
            patch("kleinanzeigen_bot.publishing_form._select_button_combobox", new_callable = AsyncMock) as mock_select_combobox,
        ):
            await set_special_attributes(test_bot, ad_cfg)

        mock_set_condition.assert_awaited_once_with(test_bot, "ok")
        mock_select_combobox.assert_awaited_once_with(test_bot, "kleidung_herren.color", "beige")
        warning_messages = [record.message for record in caplog.records if record.levelno == logging.WARNING]
        assert len([message for message in warning_messages if "Special attribute 'condition_s' is not available" in message]) == 1

    @pytest.mark.asyncio
    async def test_condition_s_lookup_timeout_warns_and_continues(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        caplog:pytest.LogCaptureFixture,
    ) -> None:
        """Lookup timeouts for condition_s now fall back to CSS selectors and, when
        those also time out, warn and continue (redesign-safe resilience) instead of
        surfacing the raw XPath TimeoutError."""
        caplog.set_level(logging.WARNING, logger = LOG.name)
        ad_cfg = Ad.model_validate(base_ad_config | {"category": "185/249", "special_attributes": {"condition_s": "ok"}, "shipping_type": "PICKUP"})

        condition_probe = MagicMock()
        condition_probe.attrs = {"id": "condition_s"}

        with (
            patch("kleinanzeigen_bot.publishing_form._set_condition", new_callable = AsyncMock, return_value = False),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = condition_probe),
            # Every web_find_all (XPath + CSS fallback) times out.
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, side_effect = TimeoutError("lookup timeout")),
        ):
            await set_special_attributes(test_bot, ad_cfg)

        # condition_s is unavailable after both XPath and CSS lookups time out;
        # the resilient fallback warns and skips instead of raising.
        warning_messages = [record.message for record in caplog.records if record.levelno == logging.WARNING]
        assert any("Special attribute 'condition_s' is not available" in m for m in warning_messages)

    @pytest.mark.asyncio
    async def test_special_attributes_text_input_fallback(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """A plain input with no special type or role should use web_input."""
        ad_cfg = Ad.model_validate(
            base_ad_config
            | {
                "special_attributes": {"title": "My Title"},
                "updated_on": "2024-01-01T00:00:00",
                "created_on": "2024-01-01T00:00:00",
            }
        )

        text_elem = MagicMock()
        text_attrs = MagicMock()
        text_attrs.get.side_effect = lambda key, default = None: {
            "id": "title",
            "type": None,
            "role": None,
        }.get(key, default)
        text_elem.attrs = text_attrs
        text_elem.local_name = "input"

        with (
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, return_value = [text_elem]),
            patch.object(test_bot, "web_input", new_callable = AsyncMock) as mock_input,
        ):
            await set_special_attributes(test_bot, ad_cfg)

        mock_input.assert_awaited_once_with(By.ID, "title", "My Title")

    @pytest.mark.asyncio
    async def test_special_attributes_unsupported_checkbox_value_raises(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """An unsupported checkbox value should raise TimeoutError."""
        ad_cfg = Ad.model_validate(
            base_ad_config
            | {
                "special_attributes": {"feature_b": "maybe"},
                "updated_on": "2024-01-01T00:00:00",
                "created_on": "2024-01-01T00:00:00",
            }
        )

        checkbox_elem = MagicMock()
        checkbox_attrs = MagicMock()
        checkbox_attrs.get.side_effect = lambda key, default = None: {
            "id": "feature",
            "name": "attributeMap[feature]",
            "type": "checkbox",
            "role": None,
        }.get(key, default)
        checkbox_elem.attrs = checkbox_attrs
        checkbox_elem.local_name = "input"

        with (
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, return_value = [checkbox_elem]),
            pytest.raises(TimeoutError, match = r"Failed to set attribute 'feature_b'"),
        ):
            await set_special_attributes(test_bot, ad_cfg)

    @pytest.mark.asyncio
    async def test_hidden_input_fallback_no_name_raises(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """A hidden input without a name attribute should raise TimeoutError."""
        ad_cfg = Ad.model_validate(
            base_ad_config
            | {
                "special_attributes": {"groesse_s": "68"},
                "updated_on": "2024-01-01T00:00:00",
                "created_on": "2024-01-01T00:00:00",
            }
        )

        hidden_elem = MagicMock()
        hidden_attrs = MagicMock()
        hidden_attrs.get.side_effect = lambda key, default = None: {
            "id": None,
            "name": None,
            "type": "hidden",
            "role": None,
        }.get(key, default)
        hidden_elem.attrs = hidden_attrs
        hidden_elem.local_name = "input"

        with (
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, return_value = [hidden_elem]),
            pytest.raises(TimeoutError, match = r"Failed to set attribute 'groesse_s'"),
        ):
            await set_special_attributes(test_bot, ad_cfg)
