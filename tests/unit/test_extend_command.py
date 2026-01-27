# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import json  # isort: skip
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kleinanzeigen_bot import KleinanzeigenBot, misc
from kleinanzeigen_bot.model.ad_model import Ad
from kleinanzeigen_bot.utils import dicts
from kleinanzeigen_bot.utils.web_scraping_mixin import By, Element


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
    async def test_run_extend_command_no_ads(self, test_bot: KleinanzeigenBot) -> None:
        """Test running extend command with no ads."""
        with patch.object(test_bot, "load_config"), patch.object(test_bot, "load_ads", return_value=[]), patch("kleinanzeigen_bot.UpdateChecker"):
            await test_bot.run(["script.py", "extend"])
            assert test_bot.command == "extend"
            assert test_bot.ads_selector == "all"

    @pytest.mark.asyncio
    async def test_run_extend_command_with_specific_ids(self, test_bot: KleinanzeigenBot) -> None:
        """Test running extend command with specific ad IDs."""
        with (
            patch.object(test_bot, "load_config"),
            patch.object(test_bot, "load_ads", return_value=[]),
            patch.object(test_bot, "create_browser_session", new_callable=AsyncMock),
            patch.object(test_bot, "login", new_callable=AsyncMock),
            patch("kleinanzeigen_bot.UpdateChecker"),
        ):
            await test_bot.run(["script.py", "extend", "--ads=12345,67890"])
            assert test_bot.command == "extend"
            assert test_bot.ads_selector == "12345,67890"


class TestExtendAdsMethod:
    """Tests for the extend_ads() method."""

    @pytest.mark.asyncio
    async def test_extend_ads_skips_unpublished_ad(self, test_bot: KleinanzeigenBot, base_ad_config_with_id: dict[str, Any]) -> None:
        """Test that extend_ads skips ads without an ID (unpublished)."""
        # Create ad without ID
        ad_config = base_ad_config_with_id.copy()
        ad_config["id"] = None
        ad_cfg = Ad.model_validate(ad_config)

        with patch.object(test_bot, "web_request", new_callable=AsyncMock) as mock_request, patch.object(test_bot, "web_sleep", new_callable=AsyncMock):
            mock_request.return_value = {"content": '{"ads": []}'}

            await test_bot.extend_ads([("test.yaml", ad_cfg, ad_config)])

            # Verify no extension was attempted
            mock_request.assert_called_once()  # Only the API call to get published ads

    @pytest.mark.asyncio
    async def test_extend_ads_skips_ad_not_in_published_list(self, test_bot: KleinanzeigenBot, base_ad_config_with_id: dict[str, Any]) -> None:
        """Test that extend_ads skips ads not found in the published ads API response."""
        ad_cfg = Ad.model_validate(base_ad_config_with_id)

        with patch.object(test_bot, "web_request", new_callable=AsyncMock) as mock_request, patch.object(test_bot, "web_sleep", new_callable=AsyncMock):
            # Return empty published ads list
            mock_request.return_value = {"content": '{"ads": []}'}

            await test_bot.extend_ads([("test.yaml", ad_cfg, base_ad_config_with_id)])

            # Verify no extension was attempted
            mock_request.assert_called_once()

    @pytest.mark.asyncio
    async def test_extend_ads_skips_inactive_ad(self, test_bot: KleinanzeigenBot, base_ad_config_with_id: dict[str, Any]) -> None:
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
            patch.object(test_bot, "web_request", new_callable=AsyncMock) as mock_request,
            patch.object(test_bot, "web_sleep", new_callable=AsyncMock),
            patch.object(test_bot, "extend_ad", new_callable=AsyncMock) as mock_extend_ad,
        ):
            mock_request.return_value = {"content": json.dumps(published_ads_json)}

            await test_bot.extend_ads([("test.yaml", ad_cfg, base_ad_config_with_id)])

            # Verify extend_ad was not called
            mock_extend_ad.assert_not_called()

    @pytest.mark.asyncio
    async def test_extend_ads_skips_ad_without_enddate(self, test_bot: KleinanzeigenBot, base_ad_config_with_id: dict[str, Any]) -> None:
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
            patch.object(test_bot, "web_request", new_callable=AsyncMock) as mock_request,
            patch.object(test_bot, "web_sleep", new_callable=AsyncMock),
            patch.object(test_bot, "extend_ad", new_callable=AsyncMock) as mock_extend_ad,
        ):
            mock_request.return_value = {"content": json.dumps(published_ads_json)}

            await test_bot.extend_ads([("test.yaml", ad_cfg, base_ad_config_with_id)])

            # Verify extend_ad was not called
            mock_extend_ad.assert_not_called()

    @pytest.mark.asyncio
    async def test_extend_ads_skips_ad_outside_window(self, test_bot: KleinanzeigenBot, base_ad_config_with_id: dict[str, Any]) -> None:
        """Test that extend_ads skips ads expiring more than 8 days in the future."""
        ad_cfg = Ad.model_validate(base_ad_config_with_id)

        # Set end date to 30 days from now (outside 8-day window)
        future_date = misc.now() + timedelta(days=30)
        end_date_str = future_date.strftime("%d.%m.%Y")

        published_ads_json = {"ads": [{"id": 12345, "title": "Test Ad Title", "state": "active", "endDate": end_date_str}]}

        with (
            patch.object(test_bot, "web_request", new_callable=AsyncMock) as mock_request,
            patch.object(test_bot, "web_sleep", new_callable=AsyncMock),
            patch.object(test_bot, "extend_ad", new_callable=AsyncMock) as mock_extend_ad,
        ):
            mock_request.return_value = {"content": json.dumps(published_ads_json)}

            await test_bot.extend_ads([("test.yaml", ad_cfg, base_ad_config_with_id)])

            # Verify extend_ad was not called
            mock_extend_ad.assert_not_called()

    @pytest.mark.asyncio
    async def test_extend_ads_extends_ad_within_window(self, test_bot: KleinanzeigenBot, base_ad_config_with_id: dict[str, Any]) -> None:
        """Test that extend_ads extends ads within the 8-day window."""
        ad_cfg = Ad.model_validate(base_ad_config_with_id)

        # Set end date to 5 days from now (within 8-day window)
        future_date = misc.now() + timedelta(days=5)
        end_date_str = future_date.strftime("%d.%m.%Y")

        published_ads_json = {"ads": [{"id": 12345, "title": "Test Ad Title", "state": "active", "endDate": end_date_str}]}

        with (
            patch.object(test_bot, "web_request", new_callable=AsyncMock) as mock_request,
            patch.object(test_bot, "web_sleep", new_callable=AsyncMock),
            patch.object(test_bot, "extend_ad", new_callable=AsyncMock) as mock_extend_ad,
        ):
            mock_request.return_value = {"content": json.dumps(published_ads_json)}
            mock_extend_ad.return_value = True

            await test_bot.extend_ads([("test.yaml", ad_cfg, base_ad_config_with_id)])

            # Verify extend_ad was called
            mock_extend_ad.assert_called_once()

    @pytest.mark.asyncio
    async def test_extend_ads_no_eligible_ads(self, test_bot: KleinanzeigenBot, base_ad_config_with_id: dict[str, Any]) -> None:
        """Test extend_ads when no ads are eligible for extension."""
        ad_cfg = Ad.model_validate(base_ad_config_with_id)

        # Set end date to 30 days from now (outside window)
        future_date = misc.now() + timedelta(days=30)
        end_date_str = future_date.strftime("%d.%m.%Y")

        published_ads_json = {"ads": [{"id": 12345, "title": "Test Ad Title", "state": "active", "endDate": end_date_str}]}

        with (
            patch.object(test_bot, "web_request", new_callable=AsyncMock) as mock_request,
            patch.object(test_bot, "web_sleep", new_callable=AsyncMock),
            patch.object(test_bot, "extend_ad", new_callable=AsyncMock) as mock_extend_ad,
        ):
            mock_request.return_value = {"content": json.dumps(published_ads_json)}

            await test_bot.extend_ads([("test.yaml", ad_cfg, base_ad_config_with_id)])

            # Verify extend_ad was not called
            mock_extend_ad.assert_not_called()

    @pytest.mark.asyncio
    async def test_extend_ads_handles_multiple_ads(self, test_bot: KleinanzeigenBot, base_ad_config_with_id: dict[str, Any]) -> None:
        """Test that extend_ads processes multiple ads correctly."""
        ad_cfg1 = Ad.model_validate(base_ad_config_with_id)

        # Create second ad
        ad_config2 = base_ad_config_with_id.copy()
        ad_config2["id"] = 67890
        ad_config2["title"] = "Second Test Ad"
        ad_cfg2 = Ad.model_validate(ad_config2)

        # Set end dates - one within window, one outside
        within_window = misc.now() + timedelta(days=5)
        outside_window = misc.now() + timedelta(days=30)

        published_ads_json = {
            "ads": [
                {"id": 12345, "title": "Test Ad Title", "state": "active", "endDate": within_window.strftime("%d.%m.%Y")},
                {"id": 67890, "title": "Second Test Ad", "state": "active", "endDate": outside_window.strftime("%d.%m.%Y")},
            ]
        }

        with (
            patch.object(test_bot, "web_request", new_callable=AsyncMock) as mock_request,
            patch.object(test_bot, "web_sleep", new_callable=AsyncMock),
            patch.object(test_bot, "extend_ad", new_callable=AsyncMock) as mock_extend_ad,
        ):
            mock_request.return_value = {"content": json.dumps(published_ads_json)}
            mock_extend_ad.return_value = True

            await test_bot.extend_ads([("test1.yaml", ad_cfg1, base_ad_config_with_id), ("test2.yaml", ad_cfg2, ad_config2)])

            # Verify extend_ad was called only once (for the ad within window)
            assert mock_extend_ad.call_count == 1


class TestExtendAdMethod:
    """Tests for the extend_ad() method.

    Note: These tests mock `_navigate_paginated_ad_overview` rather than individual browser methods
    (web_find, web_click, etc.) because the pagination helper involves complex multi-step browser
    interactions that would require extensive, brittle mock choreography. Mocking at this level
    keeps tests focused on extend_ad's own logic (dialog handling, YAML persistence, error paths).
    """

    @pytest.mark.asyncio
    async def test_extend_ad_success(self, test_bot: KleinanzeigenBot, base_ad_config_with_id: dict[str, Any], tmp_path: Path) -> None:
        """Test successful ad extension."""
        ad_cfg = Ad.model_validate(base_ad_config_with_id)

        # Create temporary YAML file
        ad_file = tmp_path / "test_ad.yaml"
        dicts.save_dict(str(ad_file), base_ad_config_with_id)

        with (
            patch.object(test_bot, "_navigate_paginated_ad_overview", new_callable=AsyncMock) as mock_paginate,
            patch.object(test_bot, "web_click", new_callable=AsyncMock),
            patch("kleinanzeigen_bot.misc.now") as mock_now,
        ):
            # Test mock datetime - timezone not relevant for timestamp formatting test
            mock_now.return_value = datetime(2025, 1, 28, 14, 30, 0)  # noqa: DTZ001

            mock_paginate.return_value = True

            result = await test_bot.extend_ad(str(ad_file), ad_cfg, base_ad_config_with_id)

            assert result is True
            assert mock_paginate.call_count == 1

            # Verify updated_on was updated in the YAML file
            updated_config = dicts.load_dict(str(ad_file))
            assert updated_config["updated_on"] == "2025-01-28T14:30:00"

    @pytest.mark.asyncio
    async def test_extend_ad_button_not_found(self, test_bot: KleinanzeigenBot, base_ad_config_with_id: dict[str, Any], tmp_path: Path) -> None:
        """Test extend_ad when the Verlängern button is not found."""
        ad_cfg = Ad.model_validate(base_ad_config_with_id)

        # Create temporary YAML file
        ad_file = tmp_path / "test_ad.yaml"
        dicts.save_dict(str(ad_file), base_ad_config_with_id)

        with patch.object(test_bot, "_navigate_paginated_ad_overview", new_callable=AsyncMock) as mock_paginate:
            # Simulate button not found by having pagination return False (not found on any page)
            mock_paginate.return_value = False

            result = await test_bot.extend_ad(str(ad_file), ad_cfg, base_ad_config_with_id)

            assert result is False
            assert mock_paginate.call_count == 1

    @pytest.mark.asyncio
    async def test_extend_ad_dialog_timeout(self, test_bot: KleinanzeigenBot, base_ad_config_with_id: dict[str, Any], tmp_path: Path) -> None:
        """Test extend_ad when the confirmation dialog times out (no dialog appears)."""
        ad_cfg = Ad.model_validate(base_ad_config_with_id)

        # Create temporary YAML file
        ad_file = tmp_path / "test_ad.yaml"
        dicts.save_dict(str(ad_file), base_ad_config_with_id)

        with (
            patch.object(test_bot, "_navigate_paginated_ad_overview", new_callable=AsyncMock) as mock_paginate,
            patch.object(test_bot, "web_click", new_callable=AsyncMock) as mock_click,
            patch("kleinanzeigen_bot.misc.now") as mock_now,
        ):
            # Test mock datetime - timezone not relevant for timestamp formatting test
            mock_now.return_value = datetime(2025, 1, 28, 14, 30, 0)  # noqa: DTZ001

            # Pagination succeeds (button found and clicked)
            mock_paginate.return_value = True
            # Dialog close button times out
            mock_click.side_effect = TimeoutError("Dialog not found")

            result = await test_bot.extend_ad(str(ad_file), ad_cfg, base_ad_config_with_id)

            # Should still succeed (dialog might not appear)
            assert result is True

    @pytest.mark.asyncio
    async def test_extend_ad_exception_handling(self, test_bot: KleinanzeigenBot, base_ad_config_with_id: dict[str, Any], tmp_path: Path) -> None:
        """Test extend_ad propagates unexpected exceptions."""
        ad_cfg = Ad.model_validate(base_ad_config_with_id)

        # Create temporary YAML file
        ad_file = tmp_path / "test_ad.yaml"
        dicts.save_dict(str(ad_file), base_ad_config_with_id)

        with patch.object(test_bot, "_navigate_paginated_ad_overview", new_callable=AsyncMock) as mock_paginate:
            # Simulate unexpected exception during pagination
            mock_paginate.side_effect = Exception("Unexpected error")

            with pytest.raises(Exception, match="Unexpected error"):
                await test_bot.extend_ad(str(ad_file), ad_cfg, base_ad_config_with_id)

    @pytest.mark.asyncio
    async def test_extend_ad_updates_yaml_file(self, test_bot: KleinanzeigenBot, base_ad_config_with_id: dict[str, Any], tmp_path: Path) -> None:
        """Test that extend_ad correctly updates the YAML file with new timestamp."""
        ad_cfg = Ad.model_validate(base_ad_config_with_id)

        # Create temporary YAML file
        ad_file = tmp_path / "test_ad.yaml"
        original_updated_on = base_ad_config_with_id["updated_on"]
        dicts.save_dict(str(ad_file), base_ad_config_with_id)

        with (
            patch.object(test_bot, "_navigate_paginated_ad_overview", new_callable=AsyncMock) as mock_paginate,
            patch.object(test_bot, "web_click", new_callable=AsyncMock),
            patch("kleinanzeigen_bot.misc.now") as mock_now,
        ):
            # Test mock datetime - timezone not relevant for timestamp formatting test
            mock_now.return_value = datetime(2025, 1, 28, 14, 30, 0)  # noqa: DTZ001

            # Pagination succeeds (button found and clicked)
            mock_paginate.return_value = True

            await test_bot.extend_ad(str(ad_file), ad_cfg, base_ad_config_with_id)

            # Load the updated file and verify the timestamp changed
            updated_config = dicts.load_dict(str(ad_file))
            assert updated_config["updated_on"] != original_updated_on
            assert updated_config["updated_on"] == "2025-01-28T14:30:00"

    @pytest.mark.asyncio
    async def test_extend_ad_with_web_mocks(self, test_bot: KleinanzeigenBot, base_ad_config_with_id: dict[str, Any], tmp_path: Path) -> None:
        """Test extend_ad with web-level mocks to exercise the find_and_click_extend_button callback."""
        ad_cfg = Ad.model_validate(base_ad_config_with_id)

        # Create temporary YAML file
        ad_file = tmp_path / "test_ad.yaml"
        dicts.save_dict(str(ad_file), base_ad_config_with_id)

        extend_button_mock = AsyncMock()
        extend_button_mock.click = AsyncMock()

        pagination_section = MagicMock()

        find_call_count = {"count": 0}

        async def mock_web_find(selector_type: By, selector_value: str, **kwargs: Any) -> Element:
            find_call_count["count"] += 1
            # Ad list container (called by pagination helper)
            if selector_type.name == "ID" and selector_value == "my-manageitems-adlist":
                return MagicMock()
            # Pagination section (called by pagination helper)
            if selector_type.name == "CSS_SELECTOR" and selector_value == ".Pagination":
                # Raise TimeoutError on first call (pagination detection) to indicate single page
                if find_call_count["count"] == 2:
                    raise TimeoutError("No pagination")
                return pagination_section
            # Extend button (called by find_and_click_extend_button callback)
            if selector_type.name == "XPATH" and "Verlängern" in selector_value:
                return extend_button_mock
            raise TimeoutError(f"Unexpected find: {selector_type} {selector_value}")

        with (
            patch.object(test_bot, "web_open", new_callable=AsyncMock),
            patch.object(test_bot, "web_sleep", new_callable=AsyncMock),
            patch.object(test_bot, "web_find", new_callable=AsyncMock, side_effect=mock_web_find),
            patch.object(test_bot, "web_find_all", new_callable=AsyncMock, return_value=[]),
            patch.object(test_bot, "web_scroll_page_down", new_callable=AsyncMock),
            patch.object(test_bot, "web_click", new_callable=AsyncMock),
            patch.object(test_bot, "_timeout", return_value=10),
            patch("kleinanzeigen_bot.misc.now") as mock_now,
        ):
            # Test mock datetime - timezone not relevant for timestamp formatting test
            mock_now.return_value = datetime(2025, 1, 28, 15, 0, 0)  # noqa: DTZ001

            result = await test_bot.extend_ad(str(ad_file), ad_cfg, base_ad_config_with_id)

            assert result is True
            # Verify the extend button was found and clicked
            extend_button_mock.click.assert_awaited_once()

            # Verify updated_on was updated
            updated_config = dicts.load_dict(str(ad_file))
            assert updated_config["updated_on"] == "2025-01-28T15:00:00"


class TestExtendEdgeCases:
    """Tests for edge cases and boundary conditions."""

    @pytest.mark.asyncio
    async def test_extend_ads_exactly_8_days(self, test_bot: KleinanzeigenBot, base_ad_config_with_id: dict[str, Any]) -> None:
        """Test that ads expiring exactly in 8 days are eligible for extension."""
        ad_cfg = Ad.model_validate(base_ad_config_with_id)

        # Set end date to exactly 8 days from now (boundary case)
        future_date = misc.now() + timedelta(days=8)
        end_date_str = future_date.strftime("%d.%m.%Y")

        published_ads_json = {"ads": [{"id": 12345, "title": "Test Ad Title", "state": "active", "endDate": end_date_str}]}

        with (
            patch.object(test_bot, "web_request", new_callable=AsyncMock) as mock_request,
            patch.object(test_bot, "web_sleep", new_callable=AsyncMock),
            patch.object(test_bot, "extend_ad", new_callable=AsyncMock) as mock_extend_ad,
        ):
            mock_request.return_value = {"content": json.dumps(published_ads_json)}
            mock_extend_ad.return_value = True

            await test_bot.extend_ads([("test.yaml", ad_cfg, base_ad_config_with_id)])

            # Verify extend_ad was called (8 days is within the window)
            mock_extend_ad.assert_called_once()

    @pytest.mark.asyncio
    async def test_extend_ads_exactly_9_days(self, test_bot: KleinanzeigenBot, base_ad_config_with_id: dict[str, Any]) -> None:
        """Test that ads expiring in exactly 9 days are not eligible for extension."""
        ad_cfg = Ad.model_validate(base_ad_config_with_id)

        # Set end date to exactly 9 days from now (just outside window)
        future_date = misc.now() + timedelta(days=9)
        end_date_str = future_date.strftime("%d.%m.%Y")

        published_ads_json = {"ads": [{"id": 12345, "title": "Test Ad Title", "state": "active", "endDate": end_date_str}]}

        with (
            patch.object(test_bot, "web_request", new_callable=AsyncMock) as mock_request,
            patch.object(test_bot, "web_sleep", new_callable=AsyncMock),
            patch.object(test_bot, "extend_ad", new_callable=AsyncMock) as mock_extend_ad,
        ):
            mock_request.return_value = {"content": json.dumps(published_ads_json)}

            await test_bot.extend_ads([("test.yaml", ad_cfg, base_ad_config_with_id)])

            # Verify extend_ad was not called (9 days is outside the window)
            mock_extend_ad.assert_not_called()

    @pytest.mark.asyncio
    async def test_extend_ads_date_parsing_german_format(self, test_bot: KleinanzeigenBot, base_ad_config_with_id: dict[str, Any]) -> None:
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
            patch.object(test_bot, "web_request", new_callable=AsyncMock) as mock_request,
            patch.object(test_bot, "web_sleep", new_callable=AsyncMock),
            patch.object(test_bot, "extend_ad", new_callable=AsyncMock) as mock_extend_ad,
            patch("kleinanzeigen_bot.misc.now") as mock_now,
        ):
            # Mock now() to return a date where 05.02.2026 would be within 8 days
            # Test mock datetime - timezone not relevant for date comparison test
            mock_now.return_value = datetime(2026, 1, 28)  # noqa: DTZ001
            mock_request.return_value = {"content": json.dumps(published_ads_json)}
            mock_extend_ad.return_value = True

            await test_bot.extend_ads([("test.yaml", ad_cfg, base_ad_config_with_id)])

            # Verify extend_ad was called (date was parsed correctly)
            mock_extend_ad.assert_called_once()
