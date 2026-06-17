# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Tests for publishing form operations (contact/location fields, category selection, city selection, pricing)."""

import asyncio
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Awaitable, Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kleinanzeigen_bot import KleinanzeigenBot
from kleinanzeigen_bot.ad_form_helpers import VERSAND_COMBOBOX_SELECTOR
from kleinanzeigen_bot.model.ad_model import Ad, AdUpdateStrategy
from kleinanzeigen_bot.publishing_form import (
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
    upload_images,
)
from kleinanzeigen_bot.utils.exceptions import CategoryResolutionError
from kleinanzeigen_bot.utils.web_scraping_mixin import By, Element


@pytest.fixture
def base_ad_config() -> dict[str, Any]:
    """Provide a base ad configuration that can be used across tests."""
    return {
        "id": None,
        "title": "Test Title",
        "description": "Test Description",
        "type": "OFFER",
        "price_type": "FIXED",
        "price": 100,
        "shipping_type": "SHIPPING",
        "shipping_options": [],
        "category": "160",
        "special_attributes": {},
        "sell_directly": False,
        "images": [],
        "active": True,
        "republication_interval": 7,
        "created_on": None,
        "contact": {"name": "Test User", "zipcode": "12345", "location": "Test City", "street": "", "phone": ""},
    }


class TestKleinanzeigenBotContactLocationHardening:
    @pytest.mark.asyncio
    async def test_city_option_text_falls_back_to_visible_text(self, test_bot:KleinanzeigenBot) -> None:
        option = MagicMock(spec = Element)
        option.text = ""

        with patch.object(test_bot, "_extract_visible_text", new_callable = AsyncMock, return_value = "  Metroville  "):
            assert await city_option_text(test_bot, option) == "Metroville"

    @pytest.mark.asyncio
    async def test_city_option_text_returns_empty_when_visible_text_times_out(self, test_bot:KleinanzeigenBot) -> None:
        option = MagicMock(spec = Element)
        option.text = ""

        with patch.object(test_bot, "_extract_visible_text", new_callable = AsyncMock, side_effect = TimeoutError("hidden")):
            assert not await city_option_text(test_bot, option)

    @pytest.mark.asyncio
    async def test_read_city_selection_text_prefers_live_input_value(self, test_bot:KleinanzeigenBot) -> None:
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
        with patch.object(test_bot, "web_find", new_callable = AsyncMock, side_effect = TimeoutError("missing city")):
            assert await read_city_selection_text(test_bot) is None

    @pytest.mark.asyncio
    async def test_set_contact_fields_fails_closed_when_zipcode_cannot_be_set(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
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
        with patch("kleinanzeigen_bot.publishing_form.read_city_selection_text", new_callable = AsyncMock) as read_city_mock:
            await set_contact_location(test_bot, "   ")

        read_city_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_set_contact_location_raises_for_missing_city_element(self, test_bot:KleinanzeigenBot) -> None:
        with (
            patch("kleinanzeigen_bot.publishing_form.read_city_selection_text", new_callable = AsyncMock, return_value = None),
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = None),
            pytest.raises(TimeoutError, match = "Unsupported city element type while setting contact location: <missing>"),
        ):
            await set_contact_location(test_bot, "Metroville")

    @pytest.mark.asyncio
    async def test_set_contact_location_raises_for_unsupported_city_element(self, test_bot:KleinanzeigenBot) -> None:
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

        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = self._picker_probe_factory(picker_present = True)),
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, return_value = radios),
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
        ):
            await resolve_category_suggestions(test_bot, "73/77")

        mock_click.assert_awaited_once()
        selector_type, selector_value = mock_click.call_args.args[:2]
        assert selector_type == By.XPATH
        assert "label[@for='category-suggestion-leaf']" in selector_value
        assert "'ad-category-picker'" in selector_value

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

        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = self._picker_probe_factory(picker_present = True)),
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, return_value = radios),
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
        ):
            await resolve_category_suggestions(test_bot, "76/77")

        mock_click.assert_awaited_once()
        assert "label[@for='id-for-77']" in mock_click.call_args.args[1]


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
    ) -> None:
        """Shipping ads with sell_directly enabled should continue if buy-now-true control is unavailable."""
        ad_cfg = Ad.model_validate(base_ad_config | {"shipping_type": "SHIPPING", "sell_directly": True, "price_type": "FIXED", "price": 100})

        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = None),
            patch.object(test_bot, "web_check", new_callable = AsyncMock),
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_set_input_value", new_callable = AsyncMock) as mock_set,
            patch("kleinanzeigen_bot.publishing_form.get_ad_description", return_value = "desc"),
        ):
            await set_pricing_fields(test_bot, ad_cfg, test_bot.config.ad_defaults)

        mock_set.assert_any_await("ad-description", "desc")

    @pytest.mark.asyncio
    async def test_buy_now_true_interaction_timeout_propagates(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """Existing direct-buy controls should still fail if interaction times out."""
        ad_cfg = Ad.model_validate(base_ad_config | {"shipping_type": "SHIPPING", "sell_directly": True, "price_type": "FIXED", "price": 100})

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

    shipping_combobox_selector = VERSAND_COMBOBOX_SELECTOR

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
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = shipping_combobox) as mock_probe,
            patch.object(test_bot, "web_select_button_combobox", new_callable = AsyncMock) as mock_select_combobox,
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
        ):
            await set_shipping(test_bot, ad_cfg)

        mock_probe.assert_awaited_once()
        assert mock_probe.await_args is not None
        assert mock_probe.await_args.args[:2] == (
            By.CSS_SELECTOR,
            self.shipping_combobox_selector,
        )
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

        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = [None, MagicMock()]) as mock_probe,
            patch.object(test_bot, "web_check", new_callable = AsyncMock, return_value = selected),
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
        ):
            await set_shipping(test_bot, ad_cfg)

        observed = [call.args[:2] for call in mock_probe.await_args_list]
        assert (By.CSS_SELECTOR, self.shipping_combobox_selector) in observed
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

        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = [None, MagicMock()]),
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

        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = [None, None, None]),
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

        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = [None, None, MagicMock()]) as mock_probe,
            patch.object(test_bot, "web_check", new_callable = AsyncMock) as mock_check,
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            pytest.raises(
                TimeoutError,
                match = "Shipping fieldset is rendered, but the pickup radio is missing; page may not be fully loaded.",
            ),
        ):
            await set_shipping(test_bot, ad_cfg)

        observed = [call.args[:2] for call in mock_probe.await_args_list]
        assert (By.CSS_SELECTOR, self.shipping_combobox_selector) in observed
        assert (By.ID, "ad-shipping-enabled-no") in observed
        assert (By.ID, "ad-shipping-enabled") in observed
        mock_check.assert_not_awaited()
        mock_click.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_shipping_without_options_uses_radio_and_dialog(self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any]) -> None:
        """Shipping without package options should use radio + dialog flow."""
        ad_cfg = Ad.model_validate(base_ad_config | {"shipping_type": "SHIPPING", "shipping_options": [], "shipping_costs": 4.95})

        with (
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = None),
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = MagicMock()),
            patch.object(test_bot, "web_set_input_value", new_callable = AsyncMock) as mock_set_input,
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, return_value = "4,95"),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
        ):
            await set_shipping(test_bot, ad_cfg)

            click_args = [c.args for c in mock_click.await_args_list]
            assert any(len(a) >= 2 and a[0] == By.ID and a[1] == "ad-individual-shipping-checkbox-control" for a in click_args)
            assert any("Fertig" in str(a[1]) for a in click_args if len(a) >= 2)
            mock_set_input.assert_awaited_once_with("ad-individual-shipping-price", "4,95")

    @pytest.mark.asyncio
    async def test_shipping_finish_timeout_raises(self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any]) -> None:
        """Timeout while confirming shipping dialog should raise a clear error."""
        ad_cfg = Ad.model_validate(base_ad_config | {"shipping_type": "SHIPPING", "shipping_options": []})

        async def click_side_effect(selector_type:By, selector_value:str, **_:Any) -> None:
            if selector_type == By.XPATH and "Fertig" in selector_value:
                raise TimeoutError("finish timeout")

        with (
            patch.object(test_bot, "web_click", new_callable = AsyncMock, side_effect = click_side_effect),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = None),
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = MagicMock()),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            pytest.raises(TimeoutError, match = "Unable to close shipping dialog!"),
        ):
            await set_shipping(test_bot, ad_cfg)

    @pytest.mark.asyncio
    async def test_shipping_without_options_does_not_toggle_checkbox_when_price_input_visible(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """When price input is already visible, individual-shipping checkbox is not toggled."""
        ad_cfg = Ad.model_validate(base_ad_config | {"shipping_type": "SHIPPING", "shipping_options": [], "shipping_costs": 4.95})

        with (
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = [None, MagicMock()]),
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = MagicMock()),
            patch.object(test_bot, "web_set_input_value", new_callable = AsyncMock) as mock_set_input,
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, return_value = "4,95"),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
        ):
            await set_shipping(test_bot, ad_cfg)

        click_args = [c.args for c in mock_click.await_args_list]
        assert not any(len(a) >= 2 and a[0] == By.ID and a[1] == "ad-individual-shipping-checkbox-control" for a in click_args)
        mock_set_input.assert_awaited_once_with("ad-individual-shipping-price", "4,95")

    @pytest.mark.asyncio
    async def test_shipping_price_lost_to_react_rerender_raises(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """When React re-render swallows the shipping price, the dialog must NOT be closed."""
        ad_cfg = Ad.model_validate(base_ad_config | {"shipping_type": "SHIPPING", "shipping_options": [], "shipping_costs": 4.95})

        async def set_input_side_effect(element_id:str, value:str) -> None:
            pass

        with (
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = None),
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = MagicMock()),
            patch.object(test_bot, "web_set_input_value", new_callable = AsyncMock, side_effect = set_input_side_effect),
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, return_value = None),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            pytest.raises(TimeoutError, match = "Unable to set shipping price!"),
        ):
            await set_shipping(test_bot, ad_cfg)

        # Fertig must never be clicked when the price was not confirmed
        fertig_clicks = [c for c in mock_click.await_args_list if c.args and len(c.args) >= 2 and "Fertig" in str(c.args[1])]
        assert not fertig_clicks, "Fertig was clicked despite shipping price not being set"

    @pytest.mark.asyncio
    async def test_shipping_price_recovers_when_readback_matches_on_retry(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """First attempt mismatches (readback empty), second attempt succeeds — must close the dialog normally."""
        ad_cfg = Ad.model_validate(base_ad_config | {"shipping_type": "SHIPPING", "shipping_options": [], "shipping_costs": 4.95})

        with (
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = None),
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = MagicMock()),
            patch.object(test_bot, "web_set_input_value", new_callable = AsyncMock) as mock_set_input,
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, side_effect = [None, "4,95"]),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as mock_sleep,
        ):
            await set_shipping(test_bot, ad_cfg)

        assert mock_set_input.await_count == 2, "expected exactly one retry after the first mismatch"
        inter_attempt_sleeps = [c for c in mock_sleep.await_args_list if c.args == (300, 500)]
        assert len(inter_attempt_sleeps) == 1, "expected one inter-attempt backoff sleep"
        fertig_clicks = [c for c in mock_click.await_args_list if c.args and len(c.args) >= 2 and "Fertig" in str(c.args[1])]
        assert fertig_clicks, "Fertig must be clicked once the readback confirms the price"

    @pytest.mark.asyncio
    async def test_shipping_price_retries_when_readback_raises_transiently(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """A TimeoutError from the readback web_execute must be retried, not propagated on the first occurrence."""
        ad_cfg = Ad.model_validate(base_ad_config | {"shipping_type": "SHIPPING", "shipping_options": [], "shipping_costs": 4.95})

        readback_results:list[str | Exception] = [TimeoutError("readback raced with re-render"), "4,95"]

        async def readback_side_effect(*_args:Any, **_kwargs:Any) -> str | None:
            result = readback_results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        with (
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = None),
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = MagicMock()),
            patch.object(test_bot, "web_set_input_value", new_callable = AsyncMock) as mock_set_input,
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, side_effect = readback_side_effect),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
        ):
            await set_shipping(test_bot, ad_cfg)

        assert mock_set_input.await_count == 2, "expected one retry after the readback raised"
        fertig_clicks = [c for c in mock_click.await_args_list if c.args and len(c.args) >= 2 and "Fertig" in str(c.args[1])]
        assert fertig_clicks, "Fertig must be clicked once a later readback confirms the price"


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

    @pytest.mark.parametrize(
        "case",
        [
            # SMALL pre-checked, only unwanted carriers are toggled
            {
                "options": ["Hermes_Päckchen"],
                "radio_checked": True,
                "expected_radio_click": False,
                "expected_clicked_carriers": ["HERMES_002", "DHL_001"],
                "expected_not_clicked_carriers": ["HERMES_001"],
            },
            # LARGE not checked, radio click needed and only unwanted carriers are toggled
            {
                "options": ["DHL_10"],
                "radio_checked": False,
                "expected_radio_click": True,
                "expected_clicked_carriers": ["HERMES_004", "DHL_004", "DHL_005"],
                "expected_not_clicked_carriers": ["DHL_003"],
            },
        ],
    )
    @pytest.mark.asyncio
    async def test_replace_mode_handles_radio_state(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        case:dict[str, Any],
    ) -> None:
        """REPLACE mode: handles both pre-checked and unchecked radio states."""
        ad_cfg = self._make_ad_with_options(base_ad_config, case["options"])

        radio_mock = self._mock_checkbox(checked = case["radio_checked"])

        async def find_side_effect(selector_type:By, selector_value:str, **_:Any) -> MagicMock:
            if "radio" in selector_value:
                return radio_mock
            return self._mock_checkbox(checked = True)  # all checkboxes pre-checked

        with (
            patch.object(test_bot, "web_find", new_callable = AsyncMock, side_effect = find_side_effect),
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
        ):
            await set_shipping_options(test_bot, ad_cfg, mode = AdUpdateStrategy.REPLACE)

        click_args = [(c.args[0], c.args[1]) for c in mock_click.await_args_list if len(c.args) >= 2]

        # Radio click behavior matches expectation
        radio_clicked = any("radio" in str(a[1]) for a in click_args)
        assert radio_clicked == case["expected_radio_click"]

        # Should click Weiter and Fertig
        assert any("Weiter" in str(a[1]) for a in click_args)
        assert any("Fertig" in str(a[1]) for a in click_args)

        # Should toggle exactly the expected carriers for this scenario
        for carrier_code in case["expected_clicked_carriers"]:
            assert any(carrier_code in str(a[1]) for a in click_args)

        for carrier_code in case["expected_not_clicked_carriers"]:
            assert not any(carrier_code in str(a[1]) for a in click_args)

    @pytest.mark.asyncio
    async def test_replace_mode_dom_verified_unchecked_defaults_select_wanted_carrier(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """REPLACE mode must select wanted carriers when defaults are unchecked (DOM-verified for MEDIUM/LARGE)."""
        ad_cfg = self._make_ad_with_options(base_ad_config, ["DHL_5"])

        radio_mock = self._mock_checkbox(checked = False)  # MEDIUM radio not selected yet

        async def find_side_effect(selector_type:By, selector_value:str, **_:Any) -> MagicMock:
            if "radio" in selector_value and "MEDIUM" in selector_value:
                return radio_mock
            # DOM probe confirms MEDIUM defaults can be unchecked after "Weiter"
            if "HERMES_003" in selector_value:
                return self._mock_checkbox(checked = False)
            if "DHL_002" in selector_value:
                return self._mock_checkbox(checked = False)
            return self._mock_checkbox(checked = False)

        with (
            patch.object(test_bot, "web_find", new_callable = AsyncMock, side_effect = find_side_effect),
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
        ):
            await set_shipping_options(test_bot, ad_cfg, mode = AdUpdateStrategy.REPLACE)

        click_args = [(c.args[0], c.args[1]) for c in mock_click.await_args_list if len(c.args) >= 2]

        # Regression guard for issue #956: wanted DHL_002 must be selected
        assert any("DHL_002" in str(a[1]) for a in click_args)
        # Unwanted Hermes checkbox must remain untouched when already unchecked
        assert not any("HERMES_003" in str(a[1]) for a in click_args)

    @pytest.mark.asyncio
    async def test_modify_mode_toggles_carriers(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """MODIFY mode: explicitly (de-)selects each carrier based on wanted set."""
        ad_cfg = self._make_ad_with_options(base_ad_config, ["Hermes_Päckchen", "DHL_2"])

        radio_mock = self._mock_checkbox(checked = True)  # SMALL already selected

        async def find_side_effect(selector_type:By, selector_value:str, **_:Any) -> MagicMock:
            if "radio" in selector_value and "SMALL" in selector_value:
                return radio_mock
            # HERMES_001 checked, HERMES_002 checked, DHL_001 unchecked
            if "HERMES_001" in selector_value:
                return self._mock_checkbox(checked = True)
            if "HERMES_002" in selector_value:
                return self._mock_checkbox(checked = True)
            if "DHL_001" in selector_value:
                return self._mock_checkbox(checked = False)
            return self._mock_checkbox(checked = False)

        with (
            patch.object(test_bot, "web_find", new_callable = AsyncMock, side_effect = find_side_effect),
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
        ):
            await set_shipping_options(test_bot, ad_cfg, mode = AdUpdateStrategy.MODIFY)

        click_args = [(c.args[0], c.args[1]) for c in mock_click.await_args_list if len(c.args) >= 2]
        # HERMES_002 should be deselected (was checked, not wanted)
        assert any("HERMES_002" in str(a[1]) for a in click_args)
        # DHL_001 should be selected (was unchecked, wanted via DHL_2 → DHL_001)
        assert any("DHL_001" in str(a[1]) for a in click_args)
        # HERMES_001 should NOT be clicked (was checked, wanted)
        assert not any("HERMES_001" in str(a[1]) for a in click_args)

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


class TestWantedShippingSelection:
    """Tests for WANTED shipping path via set_shipping_form.

    WANTED ads render shipping as a special-attribute combobox dropdown
    (``<button role="combobox">``) rather than radio buttons. These tests
    verify ``set_shipping_form`` directly with WANTED ad configurations.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("shipping_type", "expected_label"),
        [("SHIPPING", "Versand möglich"), ("PICKUP", "Nur Abholung")],
        ids = ["shipping", "pickup"],
    )
    async def test_wanted_shipping_selects_combobox_dropdown(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        shipping_type:str,
        expected_label:str,
    ) -> None:
        """WANTED ads should select shipping via button-combobox dropdown using VERSAND_COMBOBOX_SELECTOR."""
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
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = combobox_btn) as mock_find,
            patch.object(test_bot, "web_select_button_combobox", new_callable = AsyncMock) as mock_select_btn_combo,
        ):
            await set_shipping_form(test_bot, ad_cfg)

        mock_find.assert_awaited_once_with(
            By.CSS_SELECTOR,
            VERSAND_COMBOBOX_SELECTOR,
            timeout = test_bot.timeout("quick_dom"),
        )
        mock_select_btn_combo.assert_awaited_once_with(
            "babyausstattung.versand",
            expected_label,
        )

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
            patch.object(test_bot, "web_find", new_callable = AsyncMock, side_effect = TimeoutError("combobox not found in DOM")),
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
            patch.object(test_bot, "web_find", new_callable = AsyncMock) as mock_find,
            patch.object(test_bot, "web_select_button_combobox", new_callable = AsyncMock) as mock_select_btn_combo,
        ):
            await set_shipping_form(test_bot, ad_cfg)

        mock_find.assert_not_awaited()
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
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = combobox_btn),
            patch.object(test_bot, "web_select_button_combobox", new_callable = AsyncMock),
            pytest.raises(TimeoutError, match = "Failed to set shipping attribute for type 'SHIPPING'!"),
        ):
            await set_shipping_form(test_bot, ad_cfg)
