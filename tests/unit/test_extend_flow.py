# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Tests for the extend command and extend_flow module."""

import json
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kleinanzeigen_bot import extend_flow, runtime_config
from kleinanzeigen_bot.app import KleinanzeigenBot
from kleinanzeigen_bot.model.ad_model import Ad
from kleinanzeigen_bot.utils import dicts, misc, xdg_paths


@pytest.fixture
def base_ad_config_with_id() -> dict[str, Any]:
    """Provide a base ad configuration with an ID for extend tests."""
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


class TestExtendCommand:
    """Tests for the extend command functionality."""

    @pytest.mark.asyncio
    async def test_run_extend_command_no_ads(self, test_bot:KleinanzeigenBot, tmp_path:Path) -> None:
        """Test running extend command with no ads."""
        test_bot.config_file_path = str(tmp_path / "config.yaml")
        workspace = xdg_paths.Workspace.for_config(tmp_path / "config.yaml", "kleinanzeigen-bot")
        with (
            patch("kleinanzeigen_bot.runtime_config.resolve_workspace", return_value = workspace),
            patch(
                "kleinanzeigen_bot.runtime_config.load_config",
                return_value = runtime_config.RuntimeState(config = test_bot.config, categories = {}, timing_collector = None),
            ),
            patch("kleinanzeigen_bot.runtime_config.configure_file_logging", return_value = None),
            patch("kleinanzeigen_bot.runtime_config.apply_browser_config"),
            patch.object(test_bot, "load_ads", return_value = []),
            patch("kleinanzeigen_bot.update_checker.UpdateChecker"),
        ):
            await test_bot.run(["script.py", "extend"])
            assert test_bot.command == "extend"
            assert test_bot.ads_selector == "all"

    @pytest.mark.asyncio
    async def test_run_extend_command_with_specific_ids(self, test_bot:KleinanzeigenBot, tmp_path:Path) -> None:
        """Test running extend command with specific ad IDs."""
        test_bot.config_file_path = str(tmp_path / "config.yaml")
        workspace = xdg_paths.Workspace.for_config(tmp_path / "config.yaml", "kleinanzeigen-bot")
        with (
            patch("kleinanzeigen_bot.runtime_config.resolve_workspace", return_value = workspace),
            patch(
                "kleinanzeigen_bot.runtime_config.load_config",
                return_value = runtime_config.RuntimeState(config = test_bot.config, categories = {}, timing_collector = None),
            ),
            patch("kleinanzeigen_bot.runtime_config.configure_file_logging", return_value = None),
            patch("kleinanzeigen_bot.runtime_config.apply_browser_config"),
            patch.object(test_bot, "load_ads", return_value = []),
            patch.object(test_bot, "create_browser_session", new_callable = AsyncMock),
            patch.object(test_bot, "login", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.update_checker.UpdateChecker"),
        ):
            await test_bot.run(["script.py", "extend", "--ads=12345,67890"])
            assert test_bot.command == "extend"
            assert test_bot.ads_selector == "12345,67890"


class TestExtendAdsMethod:
    """Tests for the extend_ads() method."""

    @pytest.fixture(autouse = True)
    def overview(self, test_bot:KleinanzeigenBot) -> Any:
        async def navigate(callback:Callable[[int], Awaitable[bool]], page_url:str) -> bool:  # noqa: ARG001
            return await callback(1)

        rows = [MagicMock(attrs = {"data-adid": str(ad_id)}) for ad_id in (12345, 67890)]
        with (
            patch.object(test_bot, "navigate_paginated_ad_overview", side_effect = navigate),
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, return_value = rows),
        ):
            yield

    @pytest.mark.asyncio
    async def test_extend_ads_skips_unpublished_ad(self, test_bot:KleinanzeigenBot, base_ad_config_with_id:dict[str, Any]) -> None:
        """Test that extend_ads skips ads without an ID (unpublished)."""
        # Create ad without ID
        ad_config = base_ad_config_with_id.copy()
        ad_config["id"] = None
        ad_cfg = Ad.model_validate(ad_config)

        with patch.object(test_bot, "web_request", new_callable = AsyncMock) as mock_request, patch.object(test_bot, "web_sleep", new_callable = AsyncMock):
            mock_request.return_value = {"content": '{"ads": []}'}

            await extend_flow.extend_ads(web = test_bot, root_url = test_bot.root_url, ad_cfgs = [("test.yaml", ad_cfg, ad_config)])

            # Verify no extension was attempted
            mock_request.assert_called_once()  # Only the API call to get published ads

    @pytest.mark.asyncio
    async def test_extend_ads_skips_ad_not_in_published_list(self, test_bot:KleinanzeigenBot, base_ad_config_with_id:dict[str, Any]) -> None:
        """Test that extend_ads skips ads not found in the published ads API response."""
        ad_cfg = Ad.model_validate(base_ad_config_with_id)

        with patch.object(test_bot, "web_request", new_callable = AsyncMock) as mock_request, patch.object(test_bot, "web_sleep", new_callable = AsyncMock):
            # Return empty published ads list
            mock_request.return_value = {"content": '{"ads": []}'}

            await extend_flow.extend_ads(web = test_bot, root_url = test_bot.root_url, ad_cfgs = [("test.yaml", ad_cfg, base_ad_config_with_id)])

            # Verify no extension was attempted
            mock_request.assert_called_once()

    @pytest.mark.asyncio
    async def test_extend_ads_skips_inactive_ad(self, test_bot:KleinanzeigenBot, base_ad_config_with_id:dict[str, Any]) -> None:
        """Test that extend_ads skips ads with state != 'active'."""
        ad_cfg = Ad.model_validate(base_ad_config_with_id)

        published_ads_json = {
            "ads": [
                {
                    "id": 12345,
                    "title": "Test Ad Title",
                    "state": "paused",  # Not active
                    "endDate": "05.02.2026",
                }
            ]
        }

        with (
            patch.object(test_bot, "web_request", new_callable = AsyncMock) as mock_request,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.extend_flow._extend_ad", new_callable = AsyncMock) as mock_extend_ad,
        ):
            mock_request.return_value = {"content": json.dumps(published_ads_json)}

            await extend_flow.extend_ads(web = test_bot, root_url = test_bot.root_url, ad_cfgs = [("test.yaml", ad_cfg, base_ad_config_with_id)])

            # Verify extend_ad was not called
            mock_extend_ad.assert_not_called()

    @pytest.mark.asyncio
    async def test_extend_ads_skips_ad_without_enddate(self, test_bot:KleinanzeigenBot, base_ad_config_with_id:dict[str, Any]) -> None:
        """Test that extend_ads skips ads without endDate in API response."""
        ad_cfg = Ad.model_validate(base_ad_config_with_id)

        published_ads_json = {
            "ads": [
                {
                    "id": 12345,
                    "title": "Test Ad Title",
                    "state": "active",
                    # No endDate field
                }
            ]
        }

        with (
            patch.object(test_bot, "web_request", new_callable = AsyncMock) as mock_request,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.extend_flow._extend_ad", new_callable = AsyncMock) as mock_extend_ad,
        ):
            mock_request.return_value = {"content": json.dumps(published_ads_json)}

            await extend_flow.extend_ads(web = test_bot, root_url = test_bot.root_url, ad_cfgs = [("test.yaml", ad_cfg, base_ad_config_with_id)])

            # Verify extend_ad was not called
            mock_extend_ad.assert_not_called()

    @pytest.mark.asyncio
    async def test_extend_ads_skips_ad_outside_window(self, test_bot:KleinanzeigenBot, base_ad_config_with_id:dict[str, Any]) -> None:
        """Test that extend_ads skips ads expiring more than 8 days in the future."""
        ad_cfg = Ad.model_validate(base_ad_config_with_id)

        # Set end date to 30 days from now (outside 8-day window)
        future_date = misc.now() + timedelta(days = 30)
        end_date_str = future_date.strftime("%d.%m.%Y")

        published_ads_json = {"ads": [{"id": 12345, "title": "Test Ad Title", "state": "active", "endDate": end_date_str}]}

        with (
            patch.object(test_bot, "web_request", new_callable = AsyncMock) as mock_request,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.extend_flow._extend_ad", new_callable = AsyncMock) as mock_extend_ad,
        ):
            mock_request.return_value = {"content": json.dumps(published_ads_json)}

            await extend_flow.extend_ads(web = test_bot, root_url = test_bot.root_url, ad_cfgs = [("test.yaml", ad_cfg, base_ad_config_with_id)])

            # Verify extend_ad was not called
            mock_extend_ad.assert_not_called()

    @pytest.mark.asyncio
    async def test_extend_ads_extends_ad_within_window(self, test_bot:KleinanzeigenBot, base_ad_config_with_id:dict[str, Any]) -> None:
        """Test that extend_ads extends ads within the 8-day window."""
        ad_cfg = Ad.model_validate(base_ad_config_with_id)

        # Set end date to 5 days from now (within 8-day window)
        future_date = misc.now() + timedelta(days = 5)
        end_date_str = future_date.strftime("%d.%m.%Y")

        published_ads_json = {"ads": [{"id": 12345, "title": "Test Ad Title", "state": "active", "endDate": end_date_str}]}

        with (
            patch.object(test_bot, "web_request", new_callable = AsyncMock) as mock_request,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.extend_flow._extend_ad", new_callable = AsyncMock) as mock_extend_ad,
        ):
            mock_request.return_value = {"content": json.dumps(published_ads_json)}
            mock_extend_ad.return_value = True

            await extend_flow.extend_ads(web = test_bot, root_url = test_bot.root_url, ad_cfgs = [("test.yaml", ad_cfg, base_ad_config_with_id)])

            # Verify extend_ad was called
            mock_extend_ad.assert_called_once()

    @pytest.mark.asyncio
    async def test_extend_ads_handles_multiple_ads(self, test_bot:KleinanzeigenBot, base_ad_config_with_id:dict[str, Any]) -> None:
        """Test that extend_ads processes multiple ads correctly."""
        ad_cfg1 = Ad.model_validate(base_ad_config_with_id)

        # Create second ad
        ad_config2 = base_ad_config_with_id.copy()
        ad_config2["id"] = 67890
        ad_config2["title"] = "Second Test Ad"
        ad_cfg2 = Ad.model_validate(ad_config2)

        # Set end dates - one within window, one outside
        within_window = misc.now() + timedelta(days = 5)
        outside_window = misc.now() + timedelta(days = 30)

        published_ads_json = {
            "ads": [
                {"id": 12345, "title": "Test Ad Title", "state": "active", "endDate": within_window.strftime("%d.%m.%Y")},
                {"id": 67890, "title": "Second Test Ad", "state": "active", "endDate": outside_window.strftime("%d.%m.%Y")},
            ]
        }

        with (
            patch.object(test_bot, "web_request", new_callable = AsyncMock) as mock_request,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.extend_flow._extend_ad", new_callable = AsyncMock) as mock_extend_ad,
        ):
            mock_request.return_value = {"content": json.dumps(published_ads_json)}
            mock_extend_ad.return_value = True

            await extend_flow.extend_ads(
                web = test_bot, root_url = test_bot.root_url,
                ad_cfgs = [("test1.yaml", ad_cfg1, base_ad_config_with_id), ("test2.yaml", ad_cfg2, ad_config2)],
            )

            # Verify extend_ad was called only once (for the ad within window)
            assert mock_extend_ad.call_count == 1

    @pytest.mark.asyncio
    async def test_extend_ads_skips_ad_with_invalid_enddate(self, test_bot:KleinanzeigenBot, base_ad_config_with_id:dict[str, Any]) -> None:
        """Test that extend_ads gracefully skips ads with invalid endDate format instead of crashing."""
        ad_cfg = Ad.model_validate(base_ad_config_with_id)

        published_ads_json = {
            "ads": [
                {
                    "id": 12345,
                    "title": "Test Ad Title",
                    "state": "active",
                    "endDate": "not-a-date",  # Invalid format
                }
            ]
        }

        with (
            patch.object(test_bot, "web_request", new_callable = AsyncMock) as mock_request,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.extend_flow._extend_ad", new_callable = AsyncMock) as mock_extend_ad,
        ):
            mock_request.return_value = {"content": json.dumps(published_ads_json)}

            # Should not raise — gracefully skips invalid endDate
            await extend_flow.extend_ads(web = test_bot, root_url = test_bot.root_url, ad_cfgs = [("test.yaml", ad_cfg, base_ad_config_with_id)])

            # Verify extend_ad was NOT called (invalid endDate → skip)
            mock_extend_ad.assert_not_called()


class FakeExtendPage:
    """Stand-in for "Meine Anzeigen" that answers the extension flow's page script calls.

    ``state`` follows extend_choice_dialog.js: "choice" while the choice dialog is open,
    "success" once the page confirms the extension, "none" otherwise.
    """

    def __init__(
        self, *,
        dialog:bool = True,
        legacy_result:str = "success",
        select_result:str = "selected",
        confirm_result:str = "clicked",
        after_confirm:str = "success",
        delay:int = 0,
        stale_success:bool = False,
    ) -> None:
        self.dialog = dialog
        self.legacy_result = legacy_result
        self.select_result = select_result
        self.confirm_result = confirm_result
        self.after_confirm = after_confirm
        self.delay = delay
        self.state = "success" if stale_success else "none"
        self.confirmed = False
        self.clicked = False
        self.close_states:list[str] = []
        self.button = MagicMock(click = AsyncMock(side_effect = self._click_extend))

    async def _click_extend(self) -> None:
        self.clicked = True
        self.state = "choice" if self.dialog else self.legacy_result

    async def execute(self, js:str) -> str:
        # recognize the exact page script call
        if js == extend_flow._choice_dialog_script("state"):
            if self.clicked and self.delay > 0:
                self.delay -= 1
                return "none"
            return self.state
        if js == extend_flow._choice_dialog_script("select"):
            return self.select_result
        if js == extend_flow._choice_dialog_script("confirm"):
            if self.confirm_result == "clicked":
                self.confirmed = True
                self.state = self.after_confirm
            return self.confirm_result
        raise AssertionError(f"unexpected script: {js[:80]}")

    async def close(self, *_args:Any, **_kwargs:Any) -> None:
        self.close_states.append(self.state)


async def _await_polling(condition:Callable[[], Awaitable[Any]], **_kwargs:Any) -> Any:
    """web_await replacement: poll a few times, then time out."""
    for _ in range(5):
        if result := await condition():
            return result
    raise TimeoutError("condition not met")


async def _run_extend(test_bot:KleinanzeigenBot, page:FakeExtendPage, ad_cfg_dict:dict[str, Any], tmp_path:Path) -> tuple[bool, dict[str, Any]]:
    ad_file = tmp_path / "test_ad.yaml"
    dicts.save_dict(str(ad_file), ad_cfg_dict)
    with (
        patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = page.button),
        patch.object(test_bot, "web_execute", side_effect = page.execute),
        patch.object(test_bot, "web_await", side_effect = _await_polling),
        patch.object(test_bot, "web_click", side_effect = page.close),
        patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
    ):
        result = await extend_flow._extend_ad(
            web = test_bot, page_num = 1, ad_file = str(ad_file),
            ad_cfg = Ad.model_validate(ad_cfg_dict), ad_cfg_orig = dict(ad_cfg_dict),
        )
    return result, dicts.load_dict(str(ad_file))


_UNCHANGED = "2024-12-10T15:20:00"


class TestExtendAdMethod:
    """Tests for current-page button handling and extension persistence."""

    @pytest.mark.asyncio
    async def test_extend_ad_legacy_success_dialog(self, test_bot:KleinanzeigenBot, base_ad_config_with_id:dict[str, Any], tmp_path:Path) -> None:
        """Legacy flow: the "Vielen Dank!" dialog is the positive signal; updated_on is persisted."""
        page = FakeExtendPage(dialog = False, legacy_result = "success")
        with patch("kleinanzeigen_bot.utils.misc.now") as mock_now:
            # Test mock datetime - timezone not relevant for timestamp formatting test
            mock_now.return_value = datetime(2025, 1, 28, 14, 30, 0)  # noqa: DTZ001
            result, saved = await _run_extend(test_bot, page, base_ad_config_with_id, tmp_path)

        assert result is True
        assert saved["updated_on"] == "2025-01-28T14:30:00"
        assert page.close_states == ["success"]

    @pytest.mark.asyncio
    async def test_extend_ad_without_any_signal_fails(self, test_bot:KleinanzeigenBot, base_ad_config_with_id:dict[str, Any], tmp_path:Path) -> None:
        """Neither choice dialog nor success message: the extension is not recorded."""
        page = FakeExtendPage(dialog = False, legacy_result = "none")
        result, saved = await _run_extend(test_bot, page, base_ad_config_with_id, tmp_path)

        assert result is False
        assert saved["updated_on"] == _UNCHANGED

    @pytest.mark.asyncio
    async def test_extend_ad_waits_while_previous_success_message_is_visible(
        self, test_bot:KleinanzeigenBot, base_ad_config_with_id:dict[str, Any], tmp_path:Path,
    ) -> None:
        """A lingering toast of the previous ad must not count as success for this one."""
        page = FakeExtendPage(stale_success = True)
        result, saved = await _run_extend(test_bot, page, base_ad_config_with_id, tmp_path)

        assert result is False
        assert page.clicked is False
        assert saved["updated_on"] == _UNCHANGED

    @pytest.mark.asyncio
    async def test_extend_ad_button_not_found(self, test_bot:KleinanzeigenBot, base_ad_config_with_id:dict[str, Any], tmp_path:Path) -> None:
        """Test _extend_ad when the Verlängern button is not found."""
        ad_cfg = Ad.model_validate(base_ad_config_with_id)

        # Create temporary YAML file
        ad_file = tmp_path / "test_ad.yaml"
        dicts.save_dict(str(ad_file), base_ad_config_with_id)

        with patch.object(test_bot, "web_find", new_callable = AsyncMock) as mock_find:
            # The current row has no extend button.
            mock_find.side_effect = TimeoutError("Button not found")

            result = await extend_flow._extend_ad(
                web = test_bot, page_num = 1,
                ad_file = str(ad_file), ad_cfg = ad_cfg,
                ad_cfg_orig = base_ad_config_with_id,
            )

            assert result is False
            assert mock_find.call_count == 1

    @pytest.mark.asyncio
    async def test_extend_ad_exception_handling(self, test_bot:KleinanzeigenBot, base_ad_config_with_id:dict[str, Any], tmp_path:Path) -> None:
        """Test _extend_ad propagates unexpected exceptions."""
        ad_cfg = Ad.model_validate(base_ad_config_with_id)

        # Create temporary YAML file
        ad_file = tmp_path / "test_ad.yaml"
        dicts.save_dict(str(ad_file), base_ad_config_with_id)

        with patch.object(test_bot, "web_find", new_callable = AsyncMock) as mock_find:
            # Unexpected browser errors must propagate.
            mock_find.side_effect = Exception("Unexpected error")

            with pytest.raises(Exception, match = "Unexpected error"):
                await extend_flow._extend_ad(
                    web = test_bot, page_num = 1,
                    ad_file = str(ad_file), ad_cfg = ad_cfg,
                    ad_cfg_orig = base_ad_config_with_id,
                )


class TestExtendChoiceDialog:
    """Tests for the choice dialog introduced with the Astro redesign (free vs. paid extension)."""

    @pytest.mark.asyncio
    async def test_extend_ad_confirms_free_option(self, test_bot:KleinanzeigenBot, base_ad_config_with_id:dict[str, Any], tmp_path:Path) -> None:
        """The free option is confirmed; the dialog is only closed after the success message."""
        page = FakeExtendPage()
        result, saved = await _run_extend(test_bot, page, base_ad_config_with_id, tmp_path)

        assert result is True
        assert page.confirmed is True
        assert page.close_states == ["success"]  # never dismissed via "Schließen" before confirming
        assert saved["updated_on"] != _UNCHANGED

    @pytest.mark.asyncio
    async def test_extend_ad_handles_delayed_choice_dialog(self, test_bot:KleinanzeigenBot, base_ad_config_with_id:dict[str, Any], tmp_path:Path) -> None:
        """A choice dialog that appears late is still confirmed, not closed as a legacy dialog."""
        page = FakeExtendPage(delay = 2)
        result, _saved = await _run_extend(test_bot, page, base_ad_config_with_id, tmp_path)

        assert result is True
        assert page.confirmed is True
        assert page.close_states == ["success"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("select_result", "confirm_result"), [
        ("free-option-not-unique:0/1", "clicked"),
        ("free-option-not-unique:0/2", "clicked"),
        ("paid-option-selected", "clicked"),
        ("selected", "confirm-button-disabled"),
        ("selected", "selection-changed"),
    ])
    async def test_extend_ad_fails_when_free_option_cannot_be_confirmed(
        self, test_bot:KleinanzeigenBot, base_ad_config_with_id:dict[str, Any], tmp_path:Path, select_result:str, confirm_result:str,
    ) -> None:
        """Any doubt about the selected option aborts without confirming and reports failure."""
        page = FakeExtendPage(select_result = select_result, confirm_result = confirm_result)
        result, saved = await _run_extend(test_bot, page, base_ad_config_with_id, tmp_path)

        assert result is False
        assert page.confirmed is False
        assert page.close_states == ["choice"]  # dialog closed after giving up
        assert saved["updated_on"] == _UNCHANGED

    @pytest.mark.asyncio
    @pytest.mark.parametrize("after_confirm", ["none", "choice"], ids = ["dialog-closed-without-message", "dialog-stays-open"])
    async def test_extend_ad_requires_success_message(
        self, test_bot:KleinanzeigenBot, base_ad_config_with_id:dict[str, Any], tmp_path:Path, after_confirm:str,
    ) -> None:
        """After confirming, only the success message counts; a closed or open dialog is a failure."""
        page = FakeExtendPage(after_confirm = after_confirm)
        result, saved = await _run_extend(test_bot, page, base_ad_config_with_id, tmp_path)

        assert result is False
        assert page.confirmed is True
        assert saved["updated_on"] == _UNCHANGED

    def test_choice_dialog_script_receives_labels_as_arguments(self) -> None:
        """Labels are passed as JSON arguments, never spliced into the script text."""
        script = extend_flow._choice_dialog_script("select")
        assert script.endswith(
            '("select", "Nur verl\\u00e4ngern", "Anzeige verl\\u00e4ngern", "erfolgreich verl\\u00e4ngert", "Vielen Dank")'
        )


class TestExtendEdgeCases:
    """Tests for edge cases and boundary conditions."""

    @pytest.fixture(autouse = True)
    def overview(self, test_bot:KleinanzeigenBot) -> Any:
        async def navigate(callback:Callable[[int], Awaitable[bool]], page_url:str) -> bool:  # noqa: ARG001
            return await callback(1)

        rows = [MagicMock(attrs = {"data-adid": str(ad_id)}) for ad_id in (12345, 67890)]
        with (
            patch.object(test_bot, "navigate_paginated_ad_overview", side_effect = navigate),
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, return_value = rows),
        ):
            yield

    @pytest.mark.asyncio
    async def test_extend_ads_exactly_8_days(self, test_bot:KleinanzeigenBot, base_ad_config_with_id:dict[str, Any]) -> None:
        """Test that ads expiring exactly in 8 days are eligible for extension."""
        ad_cfg = Ad.model_validate(base_ad_config_with_id)

        # Set end date to exactly 8 days from now (boundary case)
        future_date = misc.now() + timedelta(days = 8)
        end_date_str = future_date.strftime("%d.%m.%Y")

        published_ads_json = {"ads": [{"id": 12345, "title": "Test Ad Title", "state": "active", "endDate": end_date_str}]}

        with (
            patch.object(test_bot, "web_request", new_callable = AsyncMock) as mock_request,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.extend_flow._extend_ad", new_callable = AsyncMock) as mock_extend_ad,
        ):
            mock_request.return_value = {"content": json.dumps(published_ads_json)}
            mock_extend_ad.return_value = True

            await extend_flow.extend_ads(web = test_bot, root_url = test_bot.root_url, ad_cfgs = [("test.yaml", ad_cfg, base_ad_config_with_id)])

            # Verify extend_ad was called (8 days is within the window)
            mock_extend_ad.assert_called_once()

    @pytest.mark.asyncio
    async def test_extend_ads_exactly_9_days(self, test_bot:KleinanzeigenBot, base_ad_config_with_id:dict[str, Any]) -> None:
        """Test that ads expiring in exactly 9 days are not eligible for extension."""
        ad_cfg = Ad.model_validate(base_ad_config_with_id)

        # Set end date to exactly 9 days from now (just outside window)
        future_date = misc.now() + timedelta(days = 9)
        end_date_str = future_date.strftime("%d.%m.%Y")

        published_ads_json = {"ads": [{"id": 12345, "title": "Test Ad Title", "state": "active", "endDate": end_date_str}]}

        with (
            patch.object(test_bot, "web_request", new_callable = AsyncMock) as mock_request,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.extend_flow._extend_ad", new_callable = AsyncMock) as mock_extend_ad,
        ):
            mock_request.return_value = {"content": json.dumps(published_ads_json)}

            await extend_flow.extend_ads(web = test_bot, root_url = test_bot.root_url, ad_cfgs = [("test.yaml", ad_cfg, base_ad_config_with_id)])

            # Verify extend_ad was not called (9 days is outside the window)
            mock_extend_ad.assert_not_called()

    @pytest.mark.asyncio
    async def test_extend_ads_date_parsing_german_format(self, test_bot:KleinanzeigenBot, base_ad_config_with_id:dict[str, Any]) -> None:
        """Test that extend_ads correctly parses German date format (DD.MM.YYYY)."""
        ad_cfg = Ad.model_validate(base_ad_config_with_id)

        # Use a specific German date format
        published_ads_json = {
            "ads": [
                {
                    "id": 12345,
                    "title": "Test Ad Title",
                    "state": "active",
                    "endDate": "05.02.2026",  # German format: DD.MM.YYYY
                }
            ]
        }

        with (
            patch.object(test_bot, "web_request", new_callable = AsyncMock) as mock_request,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.extend_flow._extend_ad", new_callable = AsyncMock) as mock_extend_ad,
            patch("kleinanzeigen_bot.utils.misc.now") as mock_now,
        ):
            # Mock now() to return a date where 05.02.2026 would be within 8 days
            # Test mock datetime - timezone not relevant for date comparison test
            mock_now.return_value = datetime(2026, 1, 28)  # noqa: DTZ001
            mock_request.return_value = {"content": json.dumps(published_ads_json)}
            mock_extend_ad.return_value = True

            await extend_flow.extend_ads(web = test_bot, root_url = test_bot.root_url, ad_cfgs = [("test.yaml", ad_cfg, base_ad_config_with_id)])

            # Verify extend_ad was called (date was parsed correctly)
            mock_extend_ad.assert_called_once()
