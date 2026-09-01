# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Tests for the reserve/activate commands and the reserve_flow module."""

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kleinanzeigen_bot import reserve_flow, runtime_config
from kleinanzeigen_bot.app import KleinanzeigenBot
from kleinanzeigen_bot.model.ad_model import Ad
from kleinanzeigen_bot.utils import xdg_paths


@pytest.fixture
def base_ad_config_with_id() -> dict[str, Any]:
    """Provide a base ad configuration with an ID for reserve tests."""
    return {
        "id": 12345,
        "title": "Test Ad Title",
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
        "created_on": "2024-12-07T10:00:00",
        "updated_on": "2024-12-10T15:20:00",
        "contact": {"name": "Test User", "zipcode": "12345", "location": "Test City", "street": "", "phone": ""},
    }


def _published(state:str, ad_id:int = 12345) -> dict[str, Any]:
    return {"ads": [{"id": ad_id, "title": "Test Ad Title", "state": state}]}


def _patched_command_run(test_bot:KleinanzeigenBot, tmp_path:Path) -> Any:
    """Patch set that lets ``run()`` reach the command handler without a browser."""
    test_bot.config_file_path = str(tmp_path / "config.yaml")
    workspace = xdg_paths.Workspace.for_config(tmp_path / "config.yaml", "kleinanzeigen-bot")
    return (
        patch("kleinanzeigen_bot.runtime_config.resolve_workspace", return_value = workspace),
        patch(
            "kleinanzeigen_bot.runtime_config.load_config",
            return_value = runtime_config.RuntimeState(config = test_bot.config, categories = {}, timing_collector = None),
        ),
        patch("kleinanzeigen_bot.runtime_config.configure_file_logging", return_value = None),
        patch("kleinanzeigen_bot.runtime_config.apply_browser_config"),
        patch.object(test_bot, "load_ads", return_value = []),
        patch("kleinanzeigen_bot.update_checker.UpdateChecker"),
    )


class TestReserveCommand:
    """Tests for wiring the reserve/activate commands into the CLI."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("command", ["reserve", "activate"])
    async def test_run_command_defaults_to_all_ads(self, test_bot:KleinanzeigenBot, tmp_path:Path, command:str) -> None:
        patches = _patched_command_run(test_bot, tmp_path)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            await test_bot.run(["script.py", command])
            assert test_bot.command == command
            assert test_bot.ads_selector == "all"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("command", ["reserve", "activate"])
    async def test_run_command_with_specific_ids(self, test_bot:KleinanzeigenBot, tmp_path:Path, command:str) -> None:
        patches = _patched_command_run(test_bot, tmp_path)
        with (
            patches[0], patches[1], patches[2], patches[3], patches[4], patches[5],
            patch.object(test_bot, "create_browser_session", new_callable = AsyncMock),
            patch.object(test_bot, "login", new_callable = AsyncMock),
        ):
            await test_bot.run(["script.py", command, "--ads=12345,67890"])
            assert test_bot.command == command
            assert test_bot.ads_selector == "12345,67890"


class TestSetReservationState:
    """Tests for the set_reservation_state() method."""

    @pytest.mark.asyncio
    async def test_skips_unpublished_ad(self, test_bot:KleinanzeigenBot, base_ad_config_with_id:dict[str, Any]) -> None:
        """An ad without an ID was never published — there is nothing to reserve."""
        ad_config = base_ad_config_with_id.copy()
        ad_config["id"] = None
        ad_cfg = Ad.model_validate(ad_config)

        with (
            patch.object(test_bot, "web_request", new_callable = AsyncMock) as mock_request,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.reserve_flow._change_ad_state", new_callable = AsyncMock) as mock_change,
        ):
            mock_request.return_value = {"content": '{"ads": []}'}

            await reserve_flow.set_reservation_state(
                web = test_bot, root_url = test_bot.root_url,
                ad_cfgs = [("test.yaml", ad_cfg, ad_config)], action = "reserve",
            )

            mock_change.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_ad_not_in_published_list(self, test_bot:KleinanzeigenBot, base_ad_config_with_id:dict[str, Any]) -> None:
        """An ad the account does not list cannot be switched — no blind request."""
        ad_cfg = Ad.model_validate(base_ad_config_with_id)

        with (
            patch.object(test_bot, "web_request", new_callable = AsyncMock) as mock_request,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.reserve_flow._change_ad_state", new_callable = AsyncMock) as mock_change,
        ):
            mock_request.return_value = {"content": '{"ads": []}'}

            await reserve_flow.set_reservation_state(
                web = test_bot, root_url = test_bot.root_url,
                ad_cfgs = [("test.yaml", ad_cfg, base_ad_config_with_id)], action = "reserve",
            )

            mock_change.assert_not_called()

    @pytest.mark.asyncio
    async def test_reserve_skips_already_reserved_ad(self, test_bot:KleinanzeigenBot, base_ad_config_with_id:dict[str, Any]) -> None:
        """Running reserve twice must be a no-op, not a failure."""
        ad_cfg = Ad.model_validate(base_ad_config_with_id)

        with (
            patch.object(test_bot, "web_request", new_callable = AsyncMock) as mock_request,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.reserve_flow._change_ad_state", new_callable = AsyncMock) as mock_change,
        ):
            mock_request.return_value = {"content": json.dumps(_published(reserve_flow.STATE_RESERVED))}

            await reserve_flow.set_reservation_state(
                web = test_bot, root_url = test_bot.root_url,
                ad_cfgs = [("test.yaml", ad_cfg, base_ad_config_with_id)], action = "reserve",
            )

            mock_change.assert_not_called()

    @pytest.mark.asyncio
    async def test_activate_skips_already_active_ad(self, test_bot:KleinanzeigenBot, base_ad_config_with_id:dict[str, Any]) -> None:
        ad_cfg = Ad.model_validate(base_ad_config_with_id)

        with (
            patch.object(test_bot, "web_request", new_callable = AsyncMock) as mock_request,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.reserve_flow._change_ad_state", new_callable = AsyncMock) as mock_change,
        ):
            mock_request.return_value = {"content": json.dumps(_published(reserve_flow.STATE_ACTIVE))}

            await reserve_flow.set_reservation_state(
                web = test_bot, root_url = test_bot.root_url,
                ad_cfgs = [("test.yaml", ad_cfg, base_ad_config_with_id)], action = "activate",
            )

            mock_change.assert_not_called()

    @pytest.mark.asyncio
    async def test_reserve_switches_active_ad(self, test_bot:KleinanzeigenBot, base_ad_config_with_id:dict[str, Any]) -> None:
        ad_cfg = Ad.model_validate(base_ad_config_with_id)

        with (
            patch.object(test_bot, "web_request", new_callable = AsyncMock) as mock_request,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.reserve_flow._change_ad_state", new_callable = AsyncMock) as mock_change,
        ):
            mock_request.return_value = {"content": json.dumps(_published(reserve_flow.STATE_ACTIVE))}
            mock_change.return_value = True

            await reserve_flow.set_reservation_state(
                web = test_bot, root_url = test_bot.root_url,
                ad_cfgs = [("test.yaml", ad_cfg, base_ad_config_with_id)], action = "reserve",
            )

            mock_change.assert_called_once()
            assert mock_change.call_args.kwargs["action"] == "reserve"

    @pytest.mark.asyncio
    async def test_activate_switches_reserved_ad(self, test_bot:KleinanzeigenBot, base_ad_config_with_id:dict[str, Any]) -> None:
        ad_cfg = Ad.model_validate(base_ad_config_with_id)

        with (
            patch.object(test_bot, "web_request", new_callable = AsyncMock) as mock_request,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.reserve_flow._change_ad_state", new_callable = AsyncMock) as mock_change,
        ):
            mock_request.return_value = {"content": json.dumps(_published(reserve_flow.STATE_RESERVED))}
            mock_change.return_value = True

            await reserve_flow.set_reservation_state(
                web = test_bot, root_url = test_bot.root_url,
                ad_cfgs = [("test.yaml", ad_cfg, base_ad_config_with_id)], action = "activate",
            )

            mock_change.assert_called_once()
            assert mock_change.call_args.kwargs["action"] == "activate"

    @pytest.mark.asyncio
    async def test_skips_ad_in_unrelated_state(self, test_bot:KleinanzeigenBot, base_ad_config_with_id:dict[str, Any]) -> None:
        """A state that is neither active nor paused (e.g. expired) is left alone."""
        ad_cfg = Ad.model_validate(base_ad_config_with_id)

        with (
            patch.object(test_bot, "web_request", new_callable = AsyncMock) as mock_request,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.reserve_flow._change_ad_state", new_callable = AsyncMock) as mock_change,
        ):
            mock_request.return_value = {"content": json.dumps(_published("expired"))}

            await reserve_flow.set_reservation_state(
                web = test_bot, root_url = test_bot.root_url,
                ad_cfgs = [("test.yaml", ad_cfg, base_ad_config_with_id)], action = "reserve",
            )

            mock_change.assert_not_called()

    @pytest.mark.asyncio
    async def test_failed_ad_does_not_stop_the_remaining_ads(
        self, test_bot:KleinanzeigenBot, base_ad_config_with_id:dict[str, Any]
    ) -> None:
        """One ad failing must neither abort the run nor be counted as switched."""
        first_cfg = Ad.model_validate(base_ad_config_with_id)
        second_config = base_ad_config_with_id | {"id": 67890}
        second_cfg = Ad.model_validate(second_config)
        published = {"ads": [
            {"id": 12345, "title": "Test Ad Title", "state": reserve_flow.STATE_ACTIVE},
            {"id": 67890, "title": "Test Ad Title", "state": reserve_flow.STATE_ACTIVE},
        ]}

        with (
            patch.object(test_bot, "web_request", new_callable = AsyncMock) as mock_request,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.reserve_flow._change_ad_state", new_callable = AsyncMock) as mock_change,
        ):
            mock_request.return_value = {"content": json.dumps(published)}
            mock_change.side_effect = [False, True]

            await reserve_flow.set_reservation_state(
                web = test_bot, root_url = test_bot.root_url,
                ad_cfgs = [("first.yaml", first_cfg, base_ad_config_with_id), ("second.yaml", second_cfg, second_config)],
                action = "reserve",
            )

            # The second ad is still attempted even though the first one failed.
            assert mock_change.await_count == 2


class TestChangeAdState:
    """Tests for the single-ad browser interaction."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("action", "label"), [("reserve", "Reservieren"), ("activate", "Aktivieren")])
    async def test_clicks_button_of_matching_row(
        self, test_bot:KleinanzeigenBot, base_ad_config_with_id:dict[str, Any],
        action:reserve_flow.ReserveAction, label:str,
    ) -> None:
        """The XPath must address the ad's own row, so a neighbouring ad is never hit."""
        ad_cfg = Ad.model_validate(base_ad_config_with_id)
        button = MagicMock()
        button.click = AsyncMock()

        async def fake_navigate(callback:Callable[[int], Awaitable[bool]], page_url:str) -> bool:  # noqa: ARG001
            return await callback(1)

        with (
            patch.object(test_bot, "web_find", new_callable = AsyncMock) as mock_find,
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "navigate_paginated_ad_overview", side_effect = fake_navigate),
        ):
            mock_find.return_value = button

            result = await reserve_flow._change_ad_state(  # noqa: SLF001
                test_bot, test_bot.root_url, ad_cfg, action = action,
            )

            assert result is True
            button.click.assert_awaited_once()
            xpath = mock_find.call_args.args[1]
            assert '@data-adid="12345"' in xpath
            assert label in xpath

    @pytest.mark.asyncio
    async def test_button_missing_on_page_continues_pagination(
        self, test_bot:KleinanzeigenBot, base_ad_config_with_id:dict[str, Any]
    ) -> None:
        """The ad may sit on a later page, so a miss must not abort the search."""
        ad_cfg = Ad.model_validate(base_ad_config_with_id)
        button = MagicMock()
        button.click = AsyncMock()
        visited_pages:list[int] = []

        async def fake_navigate(callback:Callable[[int], Awaitable[bool]], page_url:str) -> bool:  # noqa: ARG001
            for page_num in (1, 2):
                visited_pages.append(page_num)
                if await callback(page_num):
                    return True
            return False

        with (
            # Page 1 has no matching row, page 2 does.
            patch.object(test_bot, "web_find", new_callable = AsyncMock, side_effect = [TimeoutError, button]),
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "navigate_paginated_ad_overview", side_effect = fake_navigate),
        ):
            result = await reserve_flow._change_ad_state(  # noqa: SLF001
                test_bot, test_bot.root_url, ad_cfg, action = "reserve",
            )

            assert result is True
            assert visited_pages == [1, 2]
            button.click.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_navigation_timeout_is_reported_as_failure(
        self, test_bot:KleinanzeigenBot, base_ad_config_with_id:dict[str, Any]
    ) -> None:
        """A timeout while loading the overview must be caught, not propagated to the caller."""
        ad_cfg = Ad.model_validate(base_ad_config_with_id)

        with patch.object(
            test_bot, "navigate_paginated_ad_overview", new_callable = AsyncMock, side_effect = TimeoutError("overview did not load")
        ):
            result = await reserve_flow._change_ad_state(  # noqa: SLF001
                test_bot, test_bot.root_url, ad_cfg, action = "reserve",
            )

            assert result is False

    @pytest.mark.asyncio
    async def test_reports_failure_when_button_absent(
        self, test_bot:KleinanzeigenBot, base_ad_config_with_id:dict[str, Any]
    ) -> None:
        """A missing button means the state did not change — that must not read as success."""
        ad_cfg = Ad.model_validate(base_ad_config_with_id)

        with patch.object(test_bot, "navigate_paginated_ad_overview", new_callable = AsyncMock) as mock_nav:
            mock_nav.return_value = False

            result = await reserve_flow._change_ad_state(  # noqa: SLF001
                test_bot, test_bot.root_url, ad_cfg, action = "reserve",
            )

            assert result is False

    @pytest.mark.asyncio
    async def test_missing_confirmation_dialog_is_not_a_failure(
        self, test_bot:KleinanzeigenBot, base_ad_config_with_id:dict[str, Any]
    ) -> None:
        """The click already requested the change; no dialog is not an error."""
        ad_cfg = Ad.model_validate(base_ad_config_with_id)
        button = MagicMock()
        button.click = AsyncMock()

        async def fake_navigate(callback:Callable[[int], Awaitable[bool]], page_url:str) -> bool:  # noqa: ARG001
            return await callback(1)

        with (
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = button),
            patch.object(test_bot, "web_click", new_callable = AsyncMock, side_effect = TimeoutError),
            patch.object(test_bot, "navigate_paginated_ad_overview", side_effect = fake_navigate),
        ):
            result = await reserve_flow._change_ad_state(  # noqa: SLF001
                test_bot, test_bot.root_url, ad_cfg, action = "reserve",
            )

            assert result is True
