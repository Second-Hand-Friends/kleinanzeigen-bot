# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Tests for publishing form operations (contact/location fields, category selection, city selection)."""

import asyncio
from collections.abc import Callable
from typing import Any, Awaitable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kleinanzeigen_bot import KleinanzeigenBot
from kleinanzeigen_bot.model.ad_model import Ad
from kleinanzeigen_bot.publishing_form import (
    city_option_text,
    read_city_selection_text,
    resolve_category_suggestions,
    select_city_combobox_option,
    set_category,
    set_contact_fields,
    set_contact_location,
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

        web_probe_mock.assert_awaited_once_with(By.ID, "ad-phone", timeout = test_bot.timeout("quick_dom"))
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
