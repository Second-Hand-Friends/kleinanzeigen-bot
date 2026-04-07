# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import json  # isort: skip
import asyncio
import shutil
from pathlib import Path
from typing import Any, Final, TypedDict
from unittest.mock import AsyncMock, MagicMock, call, patch
from urllib.error import URLError

import pytest
from jsonschema import Draft202012Validator
from ruamel.yaml import YAML

import kleinanzeigen_bot.extract as extract_module
from kleinanzeigen_bot.model.ad_model import AdPartial, ContactPartial
from kleinanzeigen_bot.model.config_model import Config, DownloadConfig
from kleinanzeigen_bot.utils.web_scraping_mixin import Browser, By, Element

SCHEMA_PATH:Final[Path] = Path(__file__).resolve().parents[2] / "schemas" / "ad.schema.json"


def _read_text_file(path:Path) -> str:
    return path.read_text(encoding = "utf-8")


def _create_test_ad_partial(**overrides:Any) -> AdPartial:
    """Create a valid AdPartial payload for extract staging/rollback tests."""
    payload:dict[str, Any] = {
        "title": "Test Advertisement Title",
        "description": "Test Description",
        "category": "Dienstleistungen",
        "price": 100,
        "images": [],
        "contact": {
            "name": "Test User",
            "street": "Test Street 123",
            "zipcode": "12345",
            "location": "Test City",
        },
    }
    payload.update(overrides)
    return AdPartial.model_validate(payload)


class _DimensionsDict(TypedDict):
    ad_attributes:str


class _UniversalAnalyticsOptsDict(TypedDict):
    dimensions:_DimensionsDict


class _BelenConfDict(TypedDict):
    universalAnalyticsOpts:_UniversalAnalyticsOptsDict


class _SpecialAttributesDict(TypedDict, total=False):
    art_s:str
    condition_s:str


class _TestCaseDict(TypedDict):  # noqa: PYI049 Private TypedDict `...` is never used
    belen_conf:_BelenConfDict
    expected:_SpecialAttributesDict


@pytest.fixture
def test_extractor(browser_mock:MagicMock, test_bot_config:Config) -> extract_module.AdExtractor:
    """Provides a fresh extract_module.AdExtractor instance for testing.

    Dependencies:
        - browser_mock: Used to mock browser interactions
        - test_bot_config: Used to initialize the extractor with a valid configuration
    """
    return extract_module.AdExtractor(browser_mock, test_bot_config, Path("downloaded-ads"))


class TestAdExtractorBasics:
    """Basic synchronous tests for extract_module.AdExtractor."""

    def test_constructor(self, browser_mock:MagicMock, test_bot_config:Config) -> None:
        """Test the constructor of extract_module.AdExtractor"""
        extractor = extract_module.AdExtractor(browser_mock, test_bot_config, Path("downloaded-ads"))
        assert extractor.browser == browser_mock
        assert extractor.config == test_bot_config
        assert extractor.download_dir == Path("downloaded-ads")

    @pytest.mark.parametrize(
        ("url", "expected_id"),
        [
            ("https://www.kleinanzeigen.de/s-anzeige/test-title/12345678", 12345678),
            ("https://www.kleinanzeigen.de/s-anzeige/another-test/98765432", 98765432),
            ("https://www.kleinanzeigen.de/s-anzeige/invalid-id/abc", -1),
            ("https://www.kleinanzeigen.de/invalid-url", -1),
        ],
    )
    def test_extract_ad_id_from_ad_url(self, test_extractor:extract_module.AdExtractor, url:str, expected_id:int) -> None:
        """Test extraction of ad ID from different URL formats."""
        assert test_extractor.extract_ad_id_from_ad_url(url) == expected_id

    @pytest.mark.asyncio
    async def test_path_exists_helper(self, tmp_path:Path) -> None:
        """Test files.exists helper function."""

        from kleinanzeigen_bot.utils import files  # noqa: PLC0415

        # Test with existing path
        existing_file = tmp_path / "test.txt"
        existing_file.write_text("test")
        assert await files.exists(existing_file) is True
        assert await files.exists(str(existing_file)) is True

        # Test with non-existing path
        non_existing = tmp_path / "nonexistent.txt"
        assert await files.exists(non_existing) is False
        assert await files.exists(str(non_existing)) is False

    @pytest.mark.asyncio
    async def test_path_is_dir_helper(self, tmp_path:Path) -> None:
        """Test files.is_dir helper function."""

        from kleinanzeigen_bot.utils import files  # noqa: PLC0415

        # Test with directory
        test_dir = tmp_path / "testdir"
        test_dir.mkdir()
        assert await files.is_dir(test_dir) is True
        assert await files.is_dir(str(test_dir)) is True

        # Test with file
        test_file = tmp_path / "test.txt"
        test_file.write_text("test")
        assert await files.is_dir(test_file) is False
        assert await files.is_dir(str(test_file)) is False

        # Test with non-existing path
        non_existing = tmp_path / "nonexistent"
        assert await files.is_dir(non_existing) is False
        assert await files.is_dir(str(non_existing)) is False

    @pytest.mark.asyncio
    async def test_exists_async_helper(self, tmp_path:Path) -> None:
        """Test files.exists async helper function."""
        from kleinanzeigen_bot.utils import files  # noqa: PLC0415

        # Test with existing path
        existing_file = tmp_path / "test.txt"
        existing_file.write_text("test")
        assert await files.exists(existing_file) is True
        assert await files.exists(str(existing_file)) is True

        # Test with non-existing path
        non_existing = tmp_path / "nonexistent.txt"
        assert await files.exists(non_existing) is False
        assert await files.exists(str(non_existing)) is False

    @pytest.mark.asyncio
    async def test_isdir_async_helper(self, tmp_path:Path) -> None:
        """Test files.is_dir async helper function."""
        from kleinanzeigen_bot.utils import files  # noqa: PLC0415

        # Test with directory
        test_dir = tmp_path / "testdir"
        test_dir.mkdir()
        assert await files.is_dir(test_dir) is True
        assert await files.is_dir(str(test_dir)) is True

        # Test with file
        test_file = tmp_path / "test.txt"
        test_file.write_text("test")
        assert await files.is_dir(test_file) is False
        assert await files.is_dir(str(test_file)) is False

        # Test with non-existing path
        non_existing = tmp_path / "nonexistent"
        assert await files.is_dir(non_existing) is False
        assert await files.is_dir(str(non_existing)) is False

    def test_download_and_save_image_sync_success(self, tmp_path:Path) -> None:
        """Test _download_and_save_image_sync with successful download."""
        from unittest.mock import MagicMock, mock_open  # noqa: PLC0415

        test_dir = tmp_path / "images"
        test_dir.mkdir()

        # Mock urllib response
        mock_response = MagicMock()
        mock_response.info().get_content_type.return_value = "image/jpeg"
        mock_response.__enter__ = MagicMock(return_value = mock_response)
        mock_response.__exit__ = MagicMock(return_value = False)

        with (
            patch("kleinanzeigen_bot.extract.urllib_request.urlopen", return_value = mock_response),
            patch("kleinanzeigen_bot.extract.open", mock_open()),
            patch("kleinanzeigen_bot.extract.shutil.copyfileobj"),
        ):
            result = extract_module.AdExtractor._download_and_save_image_sync("http://example.com/image.jpg", str(test_dir), "test_", 1)

            assert result is not None
            assert result.endswith((".jpe", ".jpeg", ".jpg"))
            assert "test_1" in result

    def test_download_and_save_image_sync_failure(self, tmp_path:Path) -> None:
        """Test _download_and_save_image_sync with download failure."""
        with patch("kleinanzeigen_bot.extract.urllib_request.urlopen", side_effect = URLError("Network error")):
            result = extract_module.AdExtractor._download_and_save_image_sync("http://example.com/image.jpg", str(tmp_path), "test_", 1)

            assert result is None


class TestAdExtractorPricing:
    """Tests for pricing related functionality."""

    @pytest.mark.parametrize(
        ("price_text", "expected_price", "expected_type"),
        [
            ("50 €", 50, "FIXED"),
            ("1.234 €", 1234, "FIXED"),
            ("50 € VB", 50, "NEGOTIABLE"),
            ("VB", None, "NEGOTIABLE"),
            ("Zu verschenken", None, "GIVE_AWAY"),
        ],
    )
    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_extract_pricing_info(
        self, test_extractor:extract_module.AdExtractor, price_text:str, expected_price:int | None, expected_type:str
    ) -> None:
        """Test price extraction with different formats"""
        with patch.object(test_extractor, "web_text", new_callable = AsyncMock, return_value = price_text):
            price, price_type = await test_extractor._extract_pricing_info_from_ad_page()
            assert price == expected_price
            assert price_type == expected_type

    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_extract_pricing_info_timeout(self, test_extractor:extract_module.AdExtractor) -> None:
        """Test price extraction when element is not found"""
        with patch.object(test_extractor, "web_text", new_callable = AsyncMock, side_effect = TimeoutError):
            price, price_type = await test_extractor._extract_pricing_info_from_ad_page()
            assert price is None
            assert price_type == "NOT_APPLICABLE"


class TestAdExtractorShipping:
    """Tests for shipping related functionality."""

    @pytest.mark.parametrize(
        ("shipping_text", "expected_type", "expected_cost"),
        [
            ("+ Versand ab 2,99 €", "SHIPPING", 2.99),
            ("Nur Abholung", "PICKUP", None),
            ("Versand möglich", "SHIPPING", None),
        ],
    )
    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_extract_shipping_info(
        self, test_extractor:extract_module.AdExtractor, shipping_text:str, expected_type:str, expected_cost:float | None
    ) -> None:
        """Test shipping info extraction with different text formats."""
        with (
            patch.object(test_extractor, "page", MagicMock()),
            patch.object(test_extractor, "web_text", new_callable = AsyncMock, return_value = shipping_text),
            patch.object(test_extractor, "web_request", new_callable = AsyncMock) as mock_web_request,
        ):
            if expected_cost:
                shipping_response:dict[str, Any] = {
                    "data": {"shippingOptionsResponse": {"options": [{"id": "DHL_001", "priceInEuroCent": int(expected_cost * 100), "packageSize": "SMALL"}]}}
                }
                mock_web_request.return_value = {"content": json.dumps(shipping_response)}

            shipping_type, costs, options = await test_extractor._extract_shipping_info_from_ad_page()

            assert shipping_type == expected_type
            assert costs == expected_cost
            if expected_cost:
                assert options == ["DHL_2"]
            else:
                assert options is None

    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_extract_shipping_info_with_options(self, test_extractor:extract_module.AdExtractor) -> None:
        """Test shipping info extraction with shipping options."""
        shipping_response = {
            "content": json.dumps({"data": {"shippingOptionsResponse": {"options": [{"id": "DHL_001", "priceInEuroCent": 549, "packageSize": "SMALL"}]}}})
        }

        with (
            patch.object(test_extractor, "page", MagicMock()),
            patch.object(test_extractor, "web_text", new_callable = AsyncMock, return_value = "+ Versand ab 5,49 €"),
            patch.object(test_extractor, "web_request", new_callable = AsyncMock, return_value = shipping_response),
        ):
            shipping_type, costs, options = await test_extractor._extract_shipping_info_from_ad_page()

            assert shipping_type == "SHIPPING"
            assert costs == 5.49
            assert options == ["DHL_2"]

    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_extract_shipping_info_with_all_matching_options(self, test_extractor:extract_module.AdExtractor) -> None:
        """Test shipping info extraction with all matching options enabled."""
        shipping_response = {
            "content": json.dumps(
                {
                    "data": {
                        "shippingOptionsResponse": {
                            "options": [
                                {"id": "HERMES_001", "priceInEuroCent": 489, "packageSize": "SMALL"},
                                {"id": "HERMES_002", "priceInEuroCent": 549, "packageSize": "SMALL"},
                                {"id": "DHL_001", "priceInEuroCent": 619, "packageSize": "SMALL"},
                            ]
                        }
                    }
                }
            )
        }

        # Enable all matching options in config
        test_extractor.config.download = DownloadConfig.model_validate({"include_all_matching_shipping_options": True})

        with (
            patch.object(test_extractor, "page", MagicMock()),
            patch.object(test_extractor, "web_text", new_callable = AsyncMock, return_value = "+ Versand ab 4,89 €"),
            patch.object(test_extractor, "web_request", new_callable = AsyncMock, return_value = shipping_response),
        ):
            shipping_type, costs, options = await test_extractor._extract_shipping_info_from_ad_page()

            assert shipping_type == "SHIPPING"
            assert costs == 4.89
            if options is not None:
                assert sorted(options) == ["DHL_2", "Hermes_Päckchen", "Hermes_S"]
            else:
                assert options is None

    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_extract_shipping_info_with_all_matching_options_no_match(self, test_extractor:extract_module.AdExtractor) -> None:
        """Test shipping extraction when include-all is enabled but no option matches the price."""
        shipping_response = {
            "content": json.dumps(
                {
                    "data": {
                        "shippingOptionsResponse": {
                            "options": [
                                {"id": "DHL_001", "priceInEuroCent": 500, "packageSize": "SMALL"},
                                {"id": "HERMES_001", "priceInEuroCent": 600, "packageSize": "SMALL"},
                            ]
                        }
                    }
                }
            )
        }

        test_extractor.config.download = DownloadConfig.model_validate({"include_all_matching_shipping_options": True})

        with (
            patch.object(test_extractor, "page", MagicMock()),
            patch.object(test_extractor, "web_text", new_callable = AsyncMock, return_value = "+ Versand ab 4,89 €"),
            patch.object(test_extractor, "web_request", new_callable = AsyncMock, return_value = shipping_response),
        ):
            shipping_type, costs, options = await test_extractor._extract_shipping_info_from_ad_page()

            assert shipping_type == "SHIPPING"
            assert costs == 4.89
            assert options is None

    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_extract_shipping_info_with_excluded_options(self, test_extractor:extract_module.AdExtractor) -> None:
        """Test shipping info extraction with excluded options."""
        shipping_response = {
            "content": json.dumps(
                {
                    "data": {
                        "shippingOptionsResponse": {
                            "options": [
                                {"id": "HERMES_001", "priceInEuroCent": 489, "packageSize": "SMALL"},
                                {"id": "HERMES_002", "priceInEuroCent": 549, "packageSize": "SMALL"},
                                {"id": "DHL_001", "priceInEuroCent": 619, "packageSize": "SMALL"},
                            ]
                        }
                    }
                }
            )
        }

        # Enable all matching options and exclude DHL in config
        test_extractor.config.download = DownloadConfig.model_validate({"include_all_matching_shipping_options": True, "excluded_shipping_options": ["DHL_2"]})

        with (
            patch.object(test_extractor, "page", MagicMock()),
            patch.object(test_extractor, "web_text", new_callable = AsyncMock, return_value = "+ Versand ab 4,89 €"),
            patch.object(test_extractor, "web_request", new_callable = AsyncMock, return_value = shipping_response),
        ):
            shipping_type, costs, options = await test_extractor._extract_shipping_info_from_ad_page()

            assert shipping_type == "SHIPPING"
            assert costs == 4.89
            if options is not None:
                assert sorted(options) == ["Hermes_Päckchen", "Hermes_S"]
            else:
                assert options is None

    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_extract_shipping_info_with_excluded_matching_option(self, test_extractor:extract_module.AdExtractor) -> None:
        """Test shipping info extraction when the matching option is excluded."""
        shipping_response = {
            "content": json.dumps(
                {
                    "data": {
                        "shippingOptionsResponse": {
                            "options": [
                                {"id": "HERMES_001", "priceInEuroCent": 489, "packageSize": "SMALL"},
                                {"id": "HERMES_002", "priceInEuroCent": 549, "packageSize": "SMALL"},
                            ]
                        }
                    }
                }
            )
        }

        # Exclude the matching option
        test_extractor.config.download = DownloadConfig.model_validate({"excluded_shipping_options": ["Hermes_Päckchen"]})

        with (
            patch.object(test_extractor, "page", MagicMock()),
            patch.object(test_extractor, "web_text", new_callable = AsyncMock, return_value = "+ Versand ab 4,89 €"),
            patch.object(test_extractor, "web_request", new_callable = AsyncMock, return_value = shipping_response),
        ):
            shipping_type, costs, options = await test_extractor._extract_shipping_info_from_ad_page()

            assert shipping_type == "SHIPPING"
            assert costs == 4.89
            assert options is None

    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_extract_shipping_info_with_no_matching_option(self, test_extractor:extract_module.AdExtractor) -> None:
        """Test shipping info extraction when price exists but NO matching option in API response."""
        shipping_response = {
            "content": json.dumps(
                {
                    "data": {
                        "shippingOptionsResponse": {
                            "options": [
                                {"id": "DHL_001", "priceInEuroCent": 500, "packageSize": "SMALL"},
                                {"id": "HERMES_001", "priceInEuroCent": 600, "packageSize": "SMALL"},
                            ]
                        }
                    }
                }
            )
        }

        with (
            patch.object(test_extractor, "page", MagicMock()),
            patch.object(test_extractor, "web_text", new_callable = AsyncMock, return_value = "+ Versand ab 7,00 €"),
            patch.object(test_extractor, "web_request", new_callable = AsyncMock, return_value = shipping_response),
        ):
            shipping_type, costs, options = await test_extractor._extract_shipping_info_from_ad_page()

            assert shipping_type == "SHIPPING"
            assert costs == 7.0
            assert options is None

    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_extract_shipping_info_timeout(self, test_extractor:extract_module.AdExtractor) -> None:
        """Test shipping info extraction when shipping element is missing (TimeoutError)."""
        with (
            patch.object(test_extractor, "page", MagicMock()),
            patch.object(test_extractor, "web_text", new_callable = AsyncMock, side_effect = TimeoutError),
        ):
            shipping_type, costs, options = await test_extractor._extract_shipping_info_from_ad_page()

            assert shipping_type == "NOT_APPLICABLE"
            assert costs is None
            assert options is None


class TestAdExtractorNavigation:
    """Tests for navigation related functionality."""

    @pytest.mark.asyncio
    async def test_navigate_to_ad_page_with_url(self, test_extractor:extract_module.AdExtractor) -> None:
        """Test navigation to ad page using a URL."""
        page_mock = AsyncMock()
        page_mock.url = "https://www.kleinanzeigen.de/s-anzeige/test/12345"

        with (
            patch.object(test_extractor, "page", page_mock),
            patch.object(test_extractor, "web_open", new_callable = AsyncMock) as mock_web_open,
            patch.object(test_extractor, "web_find", new_callable = AsyncMock, side_effect = TimeoutError),
        ):
            result = await test_extractor.navigate_to_ad_page("https://www.kleinanzeigen.de/s-anzeige/test/12345")
            assert result is True
            mock_web_open.assert_called_with("https://www.kleinanzeigen.de/s-anzeige/test/12345")

    @pytest.mark.asyncio
    async def test_navigate_to_ad_page_with_id(self, test_extractor:extract_module.AdExtractor) -> None:
        """Test navigation to ad page using an ID."""
        ad_id = 12345
        page_mock = AsyncMock()
        page_mock.url = "https://www.kleinanzeigen.de/s-anzeige/test/{0}".format(ad_id)

        popup_close_mock = AsyncMock()
        popup_close_mock.click = AsyncMock()
        popup_close_mock.apply = AsyncMock(return_value = True)

        def find_mock(selector_type:By, selector_value:str, **_:Any) -> Element | None:
            if selector_type == By.CLASS_NAME and selector_value == "mfp-close":
                return popup_close_mock
            return None

        with (
            patch.object(test_extractor, "page", page_mock),
            patch.object(test_extractor, "web_open", new_callable = AsyncMock) as mock_web_open,
            patch.object(test_extractor, "web_find", new_callable = AsyncMock, side_effect = find_mock),
        ):
            result = await test_extractor.navigate_to_ad_page(ad_id)
            assert result is True
            mock_web_open.assert_called_with("https://www.kleinanzeigen.de/s-suchanfrage.html?keywords={0}".format(ad_id))
            popup_close_mock.click.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_navigate_to_ad_page_with_popup(self, test_extractor:extract_module.AdExtractor) -> None:
        """Test navigation to ad page with popup handling."""
        page_mock = AsyncMock()
        page_mock.url = "https://www.kleinanzeigen.de/s-anzeige/test/12345"

        input_mock = AsyncMock()
        input_mock.clear_input = AsyncMock()
        input_mock.send_keys = AsyncMock()
        input_mock.apply = AsyncMock(return_value = True)

        with (
            patch.object(test_extractor, "page", page_mock),
            patch.object(test_extractor, "web_open", new_callable = AsyncMock),
            patch.object(test_extractor, "web_find", new_callable = AsyncMock, return_value = input_mock),
            patch.object(test_extractor, "web_click", new_callable = AsyncMock) as mock_web_click,
            patch.object(test_extractor, "web_check", new_callable = AsyncMock, return_value = True),
        ):
            result = await test_extractor.navigate_to_ad_page(12345)
            assert result is True
            mock_web_click.assert_called_with(By.CLASS_NAME, "mfp-close")

    @pytest.mark.asyncio
    async def test_navigate_to_ad_page_invalid_id(self, test_extractor:extract_module.AdExtractor) -> None:
        """Test navigation to ad page with invalid ID."""
        page_mock = AsyncMock()
        page_mock.url = "https://www.kleinanzeigen.de/s-suchen.html?k0"

        input_mock = AsyncMock()
        input_mock.clear_input = AsyncMock()
        input_mock.send_keys = AsyncMock()
        input_mock.apply = AsyncMock(return_value = True)
        input_mock.attrs = {}

        with (
            patch.object(test_extractor, "page", page_mock),
            patch.object(test_extractor, "web_open", new_callable = AsyncMock),
            patch.object(test_extractor, "web_find", new_callable = AsyncMock, return_value = input_mock),
        ):
            result = await test_extractor.navigate_to_ad_page(99999)
            assert result is False

    @pytest.mark.asyncio
    async def test_extract_own_ads_urls(self, test_extractor:extract_module.AdExtractor) -> None:
        """Test extraction of own ads URLs - basic test."""
        with (
            patch.object(test_extractor, "web_open", new_callable = AsyncMock),
            patch.object(test_extractor, "web_sleep", new_callable = AsyncMock),
            patch.object(test_extractor, "web_find", new_callable = AsyncMock) as mock_web_find,
            patch.object(test_extractor, "web_find_all", new_callable = AsyncMock) as mock_web_find_all,
            patch.object(test_extractor, "web_scroll_page_down", new_callable = AsyncMock),
            patch.object(test_extractor, "web_execute", new_callable = AsyncMock),
        ):
            # --- Setup mock objects for DOM elements ---
            # Mocks needed for the actual execution flow
            ad_list_container_mock = MagicMock()
            cardbox_mock = MagicMock()  # Represents the <li> element
            link_mock = MagicMock()  # Represents the <a> element
            link_mock.attrs = {"href": "/s-anzeige/test/12345"}  # Configure the desired output

            # Mocks for elements potentially checked but maybe not strictly needed for output
            # (depending on how robust the mocking is)
            # next_button_mock = MagicMock() # If needed for multi_page logic

            # --- Setup mock responses for web_find and web_find_all in CORRECT ORDER ---

            # 1. Initial find for ad list container (before loop)
            # 2. Find for ad list container (inside loop)
            # 3. Find for the link (inside list comprehension)
            mock_web_find.side_effect = [
                ad_list_container_mock,  # Call 1: find #my-manageitems-adlist (before loop)
                ad_list_container_mock,  # Call 2: find #my-manageitems-adlist (inside loop)
                link_mock,  # Call 3: find 'div.manageitems-item-ad h3 a.text-onSurface'
                # Add more mocks here if the pagination navigation logic calls web_find again
            ]

            # 1. Find all next buttons (pagination check) - Raise timeout for single page
            # 2. Find all '.cardbox' elements (inside loop)
            mock_web_find_all.side_effect = [
                TimeoutError("No pagination"),  # Call 1: find 'button[aria-label="Nächste"]' -> single page
                [cardbox_mock],  # Call 2: find .cardbox -> One ad item
            ]

            # --- Execute test and verify results ---
            refs = await test_extractor.extract_own_ads_urls()

            # --- Assertions ---
            assert refs == ["/s-anzeige/test/12345"]  # Now it should match

            # Optional: Verify calls were made as expected
            mock_web_find.assert_has_calls(
                [
                    call(By.ID, "my-manageitems-adlist"),
                    call(By.ID, "my-manageitems-adlist"),
                    call(By.CSS_SELECTOR, "div h3 a.text-onSurface", parent = cardbox_mock),
                ],
                any_order = False,
            )  # Check order if important

            mock_web_find_all.assert_has_calls(
                [
                    call(By.CSS_SELECTOR, 'button[aria-label="Nächste"]', timeout = 10),
                    call(By.CLASS_NAME, "cardbox", parent = ad_list_container_mock),
                ],
                any_order = False,
            )

    @pytest.mark.asyncio
    async def test_extract_own_ads_urls_paginates_with_enabled_next_button(self, test_extractor:extract_module.AdExtractor) -> None:
        """Ensure the paginator clicks the first enabled next button and advances."""
        ad_list_container_mock = MagicMock()
        cardbox_page_one = MagicMock()
        cardbox_page_two = MagicMock()
        link_page_one = MagicMock(attrs = {"href": "/s-anzeige/page-one/111"})
        link_page_two = MagicMock(attrs = {"href": "/s-anzeige/page-two/222"})

        next_button_enabled = AsyncMock()
        next_button_enabled.attrs = {}
        disabled_button = MagicMock()
        disabled_button.attrs = {"disabled": True}

        link_queue = [link_page_one, link_page_two]
        next_button_call = {"count": 0}
        cardbox_call = {"count": 0}

        async def fake_web_find(selector_type:By, selector_value:str, *, parent:Element | None = None, timeout:int | float | None = None) -> Element:
            if selector_type == By.ID and selector_value == "my-manageitems-adlist":
                return ad_list_container_mock
            if selector_type == By.CSS_SELECTOR and selector_value == "div h3 a.text-onSurface":
                return link_queue.pop(0)
            raise AssertionError(f"Unexpected selector {selector_type} {selector_value}")

        async def fake_web_find_all(
            selector_type:By, selector_value:str, *, parent:Element | None = None, timeout:int | float | None = None
        ) -> list[Element]:
            if selector_type == By.CSS_SELECTOR and selector_value == 'button[aria-label="Nächste"]':
                next_button_call["count"] += 1
                if next_button_call["count"] == 1:
                    return [next_button_enabled]  # initial detection -> multi page
                if next_button_call["count"] == 2:
                    return [disabled_button, next_button_enabled]  # navigation on page 1
                return [disabled_button]  # after navigating, stop
            if selector_type == By.CLASS_NAME and selector_value == "cardbox":
                cardbox_call["count"] += 1
                return [cardbox_page_one] if cardbox_call["count"] == 1 else [cardbox_page_two]
            raise AssertionError(f"Unexpected find_all selector {selector_type} {selector_value}")

        with (
            patch.object(test_extractor, "web_open", new_callable = AsyncMock),
            patch.object(test_extractor, "web_scroll_page_down", new_callable = AsyncMock),
            patch.object(test_extractor, "web_sleep", new_callable = AsyncMock),
            patch.object(test_extractor, "web_find", new_callable = AsyncMock, side_effect = fake_web_find),
            patch.object(test_extractor, "web_find_all", new_callable = AsyncMock, side_effect = fake_web_find_all),
        ):
            refs = await test_extractor.extract_own_ads_urls()

        assert refs == ["/s-anzeige/page-one/111", "/s-anzeige/page-two/222"]
        next_button_enabled.click.assert_awaited()  # triggered once during navigation

    @pytest.mark.asyncio
    async def test_extract_own_ads_urls_deduplicates_duplicate_refs_on_same_page(self, test_extractor:extract_module.AdExtractor) -> None:
        """Duplicate refs on one page should be deduplicated while still paginating."""
        ad_list_container_mock = MagicMock()
        cardbox_page_one_a = MagicMock()
        cardbox_page_one_b = MagicMock()
        cardbox_page_two = MagicMock()

        link_page_one_a = MagicMock(attrs = {"href": "/s-anzeige/duplicate/111"})
        link_page_one_b = MagicMock(attrs = {"href": "/s-anzeige/duplicate/111"})
        link_page_two = MagicMock(attrs = {"href": "/s-anzeige/page-two/222"})

        next_button_enabled = AsyncMock()
        next_button_enabled.attrs = {}
        disabled_button = MagicMock()
        disabled_button.attrs = {"disabled": True}

        link_queue = [link_page_one_a, link_page_one_b, link_page_two]
        next_button_call = {"count": 0}
        cardbox_call = {"count": 0}

        async def fake_web_find(selector_type:By, selector_value:str, *, parent:Element | None = None, timeout:int | float | None = None) -> Element:
            if selector_type == By.ID and selector_value == "my-manageitems-adlist":
                return ad_list_container_mock
            if selector_type == By.CSS_SELECTOR and selector_value == "div h3 a.text-onSurface":
                return link_queue.pop(0)
            raise AssertionError(f"Unexpected selector {selector_type} {selector_value}")

        async def fake_web_find_all(
            selector_type:By, selector_value:str, *, parent:Element | None = None, timeout:int | float | None = None
        ) -> list[Element]:
            if selector_type == By.CSS_SELECTOR and selector_value == 'button[aria-label="Nächste"]':
                next_button_call["count"] += 1
                if next_button_call["count"] == 1:
                    return [next_button_enabled]  # initial detection -> multi page
                if next_button_call["count"] == 2:
                    return [disabled_button, next_button_enabled]  # navigation on page 1
                return [disabled_button]  # after navigating, stop
            if selector_type == By.CLASS_NAME and selector_value == "cardbox":
                cardbox_call["count"] += 1
                if cardbox_call["count"] == 1:
                    return [cardbox_page_one_a, cardbox_page_one_b]
                return [cardbox_page_two]
            raise AssertionError(f"Unexpected find_all selector {selector_type} {selector_value}")

        with (
            patch.object(test_extractor, "web_open", new_callable = AsyncMock),
            patch.object(test_extractor, "web_scroll_page_down", new_callable = AsyncMock),
            patch.object(test_extractor, "web_sleep", new_callable = AsyncMock),
            patch.object(test_extractor, "web_find", new_callable = AsyncMock, side_effect = fake_web_find),
            patch.object(test_extractor, "web_find_all", new_callable = AsyncMock, side_effect = fake_web_find_all),
        ):
            refs = await test_extractor.extract_own_ads_urls()

        assert refs == ["/s-anzeige/duplicate/111", "/s-anzeige/page-two/222"]
        next_button_enabled.click.assert_awaited()  # triggered once during navigation

    @pytest.mark.asyncio
    async def test_extract_own_ads_urls_stops_when_second_page_contains_only_seen_refs(self, test_extractor:extract_module.AdExtractor) -> None:
        """Pagination should stop when page 2 only contains refs already seen on page 1."""
        ad_list_container_mock = MagicMock()
        cardbox_page_one = MagicMock()
        cardbox_page_two = MagicMock()

        link_page_one = MagicMock(attrs = {"href": "/s-anzeige/repeat/111"})
        link_page_two = MagicMock(attrs = {"href": "/s-anzeige/repeat/111"})

        next_button_enabled = AsyncMock()
        next_button_enabled.attrs = {}
        disabled_button = MagicMock()
        disabled_button.attrs = {"disabled": True}

        link_queue = [link_page_one, link_page_two]
        next_button_call = {"count": 0}
        cardbox_call = {"count": 0}

        async def fake_web_find(selector_type:By, selector_value:str, *, parent:Element | None = None, timeout:int | float | None = None) -> Element:
            if selector_type == By.ID and selector_value == "my-manageitems-adlist":
                return ad_list_container_mock
            if selector_type == By.CSS_SELECTOR and selector_value == "div h3 a.text-onSurface":
                return link_queue.pop(0)
            raise AssertionError(f"Unexpected selector {selector_type} {selector_value}")

        async def fake_web_find_all(
            selector_type:By, selector_value:str, *, parent:Element | None = None, timeout:int | float | None = None
        ) -> list[Element]:
            if selector_type == By.CSS_SELECTOR and selector_value == 'button[aria-label="Nächste"]':
                next_button_call["count"] += 1
                if next_button_call["count"] == 1:
                    return [next_button_enabled]  # initial detection -> multi page
                if next_button_call["count"] == 2:
                    return [disabled_button, next_button_enabled]  # navigation on page 1
                return [disabled_button]  # after navigating, stop
            if selector_type == By.CLASS_NAME and selector_value == "cardbox":
                cardbox_call["count"] += 1
                return [cardbox_page_one] if cardbox_call["count"] == 1 else [cardbox_page_two]
            raise AssertionError(f"Unexpected find_all selector {selector_type} {selector_value}")

        with (
            patch.object(test_extractor, "web_open", new_callable = AsyncMock),
            patch.object(test_extractor, "web_scroll_page_down", new_callable = AsyncMock),
            patch.object(test_extractor, "web_sleep", new_callable = AsyncMock),
            patch.object(test_extractor, "web_find", new_callable = AsyncMock, side_effect = fake_web_find),
            patch.object(test_extractor, "web_find_all", new_callable = AsyncMock, side_effect = fake_web_find_all),
        ):
            refs = await test_extractor.extract_own_ads_urls()

        assert refs == ["/s-anzeige/repeat/111"]
        next_button_enabled.click.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_extract_own_ads_urls_timeout_in_callback(self, test_extractor:extract_module.AdExtractor) -> None:
        """Test that TimeoutError in extract_page_refs callback stops pagination."""
        with (
            patch.object(test_extractor, "web_open", new_callable = AsyncMock),
            patch.object(test_extractor, "web_sleep", new_callable = AsyncMock),
            patch.object(test_extractor, "web_find", new_callable = AsyncMock) as mock_web_find,
            patch.object(test_extractor, "web_find_all", new_callable = AsyncMock, return_value = []),
            patch.object(test_extractor, "web_scroll_page_down", new_callable = AsyncMock),
            patch.object(test_extractor, "web_execute", new_callable = AsyncMock),
        ):
            # Setup: ad list container exists, but web_find_all for cardbox raises TimeoutError
            ad_list_container_mock = MagicMock()

            call_count = {"count": 0}

            def mock_find_side_effect(*args:Any, **kwargs:Any) -> Element:
                call_count["count"] += 1
                if call_count["count"] == 1:
                    # First call: ad list container (before pagination loop)
                    return ad_list_container_mock
                # Second call: ad list container (inside callback)
                return ad_list_container_mock

            mock_web_find.side_effect = mock_find_side_effect

            # Make web_find_all for cardbox raise TimeoutError (simulating missing ad items)
            async def mock_find_all_side_effect(*args:Any, **kwargs:Any) -> list[Element]:
                raise TimeoutError("Ad items not found")

            with patch.object(test_extractor, "web_find_all", new_callable = AsyncMock, side_effect = mock_find_all_side_effect):
                refs = await test_extractor.extract_own_ads_urls()

            # Pagination should stop (TimeoutError in callback returns True)
            assert refs == []

    @pytest.mark.asyncio
    async def test_extract_own_ads_urls_skips_single_item_timeout(self, test_extractor:extract_module.AdExtractor) -> None:
        """Timeout on one ad item should skip that item but keep extracting others."""
        ad_list_container_mock = MagicMock()
        first_item = MagicMock()
        second_item = MagicMock()
        valid_link = MagicMock()
        valid_link.attrs = {"href": "/s-anzeige/ok/999"}

        with (
            patch.object(test_extractor, "web_open", new_callable = AsyncMock),
            patch.object(test_extractor, "web_sleep", new_callable = AsyncMock),
            patch.object(test_extractor, "web_scroll_page_down", new_callable = AsyncMock),
            patch.object(
                test_extractor,
                "web_find_all",
                new_callable = AsyncMock,
                side_effect = [TimeoutError("No pagination"), [first_item, second_item]],
            ),
            patch.object(
                test_extractor,
                "web_find",
                new_callable = AsyncMock,
                side_effect = [ad_list_container_mock, ad_list_container_mock, TimeoutError(), valid_link],
            ),
        ):
            refs = await test_extractor.extract_own_ads_urls()

        assert refs == ["/s-anzeige/ok/999"]

    @pytest.mark.asyncio
    async def test_extract_own_ads_urls_skips_single_item_without_href(self, test_extractor:extract_module.AdExtractor) -> None:
        """Anchor without href should be skipped instead of adding a 'None' entry."""
        ad_list_container_mock = MagicMock()
        first_item = MagicMock()
        second_item = MagicMock()
        missing_href_link = MagicMock()
        missing_href_link.attrs = {}
        valid_link = MagicMock()
        valid_link.attrs = {"href": "/s-anzeige/ok/999"}

        with (
            patch.object(test_extractor, "web_open", new_callable = AsyncMock),
            patch.object(test_extractor, "web_sleep", new_callable = AsyncMock),
            patch.object(test_extractor, "web_scroll_page_down", new_callable = AsyncMock),
            patch.object(
                test_extractor,
                "web_find_all",
                new_callable = AsyncMock,
                side_effect = [TimeoutError("No pagination"), [first_item, second_item]],
            ),
            patch.object(
                test_extractor,
                "web_find",
                new_callable = AsyncMock,
                side_effect = [ad_list_container_mock, ad_list_container_mock, missing_href_link, valid_link],
            ),
        ):
            refs = await test_extractor.extract_own_ads_urls()

        assert refs == ["/s-anzeige/ok/999"]

    @pytest.mark.asyncio
    async def test_extract_own_ads_urls_generic_exception_in_callback(self, test_extractor:extract_module.AdExtractor) -> None:
        """Test that generic Exception in extract_page_refs callback continues pagination."""
        with (
            patch.object(test_extractor, "web_open", new_callable = AsyncMock),
            patch.object(test_extractor, "web_sleep", new_callable = AsyncMock),
            patch.object(test_extractor, "web_find", new_callable = AsyncMock) as mock_web_find,
            patch.object(test_extractor, "web_scroll_page_down", new_callable = AsyncMock),
        ):
            # Setup: ad list container exists, but web_find_all raises generic Exception
            ad_list_container_mock = MagicMock()

            call_count = {"count": 0}

            def mock_find_side_effect(*args:Any, **kwargs:Any) -> Element:
                call_count["count"] += 1
                if call_count["count"] == 1:
                    # First call: ad list container (before pagination loop)
                    return ad_list_container_mock
                # Second call: ad list container (inside callback)
                return ad_list_container_mock

            mock_web_find.side_effect = mock_find_side_effect

            with patch.object(
                test_extractor,
                "web_find_all",
                new_callable = AsyncMock,
                side_effect = [TimeoutError("No pagination"), AttributeError("Unexpected error")],
            ):
                refs = await test_extractor.extract_own_ads_urls()

            # Pagination should continue despite exception (callback returns False)
            # Since it's a single page (no pagination), refs should be empty
            assert refs == []


class TestAdExtractorContent:
    """Tests for content extraction functionality."""

    # pylint: disable=protected-access

    @pytest.mark.asyncio
    async def test_extract_description_with_affixes(
        self, test_extractor:extract_module.AdExtractor, description_test_cases:list[tuple[dict[str, Any], str, str]], test_bot_config:Config, tmp_path:Path
    ) -> None:
        """Test extraction of description with various prefix/suffix configurations."""
        base_dir = tmp_path / "downloaded-ads"
        base_dir.mkdir()

        # Mock the page
        page_mock = MagicMock()
        page_mock.url = "https://www.kleinanzeigen.de/s-anzeige/test/12345"
        test_extractor.page = page_mock

        for config, expected_raw, web_description_with_affixes in description_test_cases:
            test_extractor.config = test_bot_config.with_values(config)

            with patch.multiple(
                test_extractor,
                web_text = AsyncMock(
                    side_effect = [
                        "Test Title",  # Title (wrapper's initial extraction)
                        "Test Title",  # Title (core extraction's call)
                        web_description_with_affixes,  # Description with affixes (as it appears on web)
                        "03.02.2025",  # Creation date
                    ]
                ),
                web_execute = AsyncMock(return_value = {"universalAnalyticsOpts": {"dimensions": {"l3_category_id": "", "ad_attributes": ""}}}),
                _extract_category_from_ad_page = AsyncMock(return_value = "160"),
                _extract_special_attributes_from_ad_page = AsyncMock(return_value = {}),
                _extract_pricing_info_from_ad_page = AsyncMock(return_value = (None, "NOT_APPLICABLE")),
                _extract_shipping_info_from_ad_page = AsyncMock(return_value = ("NOT_APPLICABLE", None, None)),
                _extract_sell_directly_from_ad_page = AsyncMock(return_value = False),
                _download_images_from_ad_page = AsyncMock(return_value = []),
                _extract_contact_from_ad_page = AsyncMock(return_value = {}),
            ):
                ad_cfg, _staging_dir, _final_dir, _ad_file_stem = await test_extractor._extract_ad_page_info_with_directory_handling(base_dir, 12345)
                assert ad_cfg.description == expected_raw

    @pytest.mark.asyncio
    async def test_extract_description_with_affixes_timeout(self, test_extractor:extract_module.AdExtractor, tmp_path:Path) -> None:
        """Test handling of timeout when extracting description."""
        base_dir = tmp_path / "downloaded-ads"
        base_dir.mkdir()

        # Mock the page
        page_mock = MagicMock()
        page_mock.url = "https://www.kleinanzeigen.de/s-anzeige/test/12345"
        test_extractor.page = page_mock

        with (
            patch.multiple(
                test_extractor,
                web_text = AsyncMock(
                    side_effect = [
                        "Test Title",  # Title (wrapper's initial extraction)
                        "Test Title",  # Title (core extraction's call)
                        TimeoutError("Timeout"),  # Description times out
                        "03.02.2025",  # Date succeeds (not reached)
                    ]
                ),
                web_execute = AsyncMock(return_value = {"universalAnalyticsOpts": {"dimensions": {"l3_category_id": "", "ad_attributes": ""}}}),
                _extract_category_from_ad_page = AsyncMock(return_value = "160"),
                _extract_special_attributes_from_ad_page = AsyncMock(return_value = {}),
                _extract_pricing_info_from_ad_page = AsyncMock(return_value = (None, "NOT_APPLICABLE")),
                _extract_shipping_info_from_ad_page = AsyncMock(return_value = ("NOT_APPLICABLE", None, None)),
                _extract_sell_directly_from_ad_page = AsyncMock(return_value = False),
                _download_images_from_ad_page = AsyncMock(return_value = []),
                _extract_contact_from_ad_page = AsyncMock(return_value = ContactPartial()),
            ),
            pytest.raises(TimeoutError),
        ):
            await test_extractor._extract_ad_page_info_with_directory_handling(base_dir, 12345)

    @pytest.mark.asyncio
    async def test_extract_description_with_affixes_no_affixes(self, test_extractor:extract_module.AdExtractor, tmp_path:Path) -> None:
        """Test extraction of description without any affixes in config."""
        base_dir = tmp_path / "downloaded-ads"
        base_dir.mkdir()

        # Mock the page
        page_mock = MagicMock()
        page_mock.url = "https://www.kleinanzeigen.de/s-anzeige/test/12345"
        test_extractor.page = page_mock
        raw_description = "Original Description"

        with patch.multiple(
            test_extractor,
            web_text = AsyncMock(
                side_effect = [
                    "Test Title",  # Title (wrapper's initial extraction)
                    "Test Title",  # Title (core extraction's call)
                    raw_description,  # Description without affixes
                    "03.02.2025",  # Creation date
                ]
            ),
            web_execute = AsyncMock(return_value = {"universalAnalyticsOpts": {"dimensions": {"l3_category_id": "", "ad_attributes": ""}}}),
            _extract_category_from_ad_page = AsyncMock(return_value = "160"),
            _extract_special_attributes_from_ad_page = AsyncMock(return_value = {}),
            _extract_pricing_info_from_ad_page = AsyncMock(return_value = (None, "NOT_APPLICABLE")),
            _extract_shipping_info_from_ad_page = AsyncMock(return_value = ("NOT_APPLICABLE", None, None)),
            _extract_sell_directly_from_ad_page = AsyncMock(return_value = False),
            _download_images_from_ad_page = AsyncMock(return_value = []),
            _extract_contact_from_ad_page = AsyncMock(return_value = ContactPartial()),
        ):
            ad_cfg, _staging_dir, _final_dir, _ad_file_stem = await test_extractor._extract_ad_page_info_with_directory_handling(base_dir, 12345)
            assert ad_cfg.description == raw_description

    @pytest.mark.asyncio
    async def test_extract_sell_directly_data_hit_true(self, test_extractor:extract_module.AdExtractor) -> None:
        """Test sell_directly extraction with data hit - buyNowEligible=True."""
        # Setup extractor with published ads data
        test_extractor.published_ads_by_id = {123456789: {"id": 123456789, "buyNowEligible": True}}

        # Setup page URL
        test_extractor.page = MagicMock()
        test_extractor.page.url = "https://www.kleinanzeigen.de/s-anzeige/test-ad/123456789"

        result = await test_extractor._extract_sell_directly_from_ad_page()

        assert result is True

    @pytest.mark.asyncio
    async def test_extract_sell_directly_data_hit_false(self, test_extractor:extract_module.AdExtractor) -> None:
        """Test sell_directly extraction with data hit - buyNowEligible=False."""
        test_extractor.published_ads_by_id = {123456789: {"id": 123456789, "buyNowEligible": False}}

        test_extractor.page = MagicMock()
        test_extractor.page.url = "https://www.kleinanzeigen.de/s-anzeige/test-ad/123456789"

        result = await test_extractor._extract_sell_directly_from_ad_page()

        assert result is False

    @pytest.mark.asyncio
    async def test_extract_sell_directly_data_miss(self, test_extractor:extract_module.AdExtractor) -> None:
        """Test sell_directly extraction with data miss - ad ID not in cache returns None."""
        # Cache has a different ad ID than the one in the URL - true data miss
        test_extractor.published_ads_by_id = {987654321: {"id": 987654321, "buyNowEligible": True}}

        test_extractor.page = MagicMock()
        test_extractor.page.url = "https://www.kleinanzeigen.de/s-anzeige/test-ad/123456789"

        result = await test_extractor._extract_sell_directly_from_ad_page()

        assert result is None

    @pytest.mark.asyncio
    async def test_extract_sell_directly_empty_published_ads(self, test_extractor:extract_module.AdExtractor) -> None:
        """Test sell_directly extraction with empty published_ads_by_id - returns None."""
        test_extractor.published_ads_by_id = {}

        test_extractor.page = MagicMock()
        test_extractor.page.url = "https://www.kleinanzeigen.de/s-anzeige/test-ad/123456789"

        result = await test_extractor._extract_sell_directly_from_ad_page()

        assert result is None

    @pytest.mark.asyncio
    async def test_extract_sell_directly_invalid_url(self, test_extractor:extract_module.AdExtractor) -> None:
        """Test sell_directly extraction with invalid URL - returns None."""
        test_extractor.published_ads_by_id = {123456789: {"id": 123456789, "buyNowEligible": True}}

        test_extractor.page = MagicMock()
        test_extractor.page.url = "https://www.kleinanzeigen.de/invalid-url"

        result = await test_extractor._extract_sell_directly_from_ad_page()

        assert result is None

    @pytest.mark.asyncio
    async def test_extract_sell_directly_non_boolean_value(self, test_extractor:extract_module.AdExtractor) -> None:
        """Test sell_directly extraction when buyNowEligible is not a boolean."""
        test_extractor.published_ads_by_id = {123456789: {"id": 123456789, "buyNowEligible": "true"}}  # String, not bool

        test_extractor.page = MagicMock()
        test_extractor.page.url = "https://www.kleinanzeigen.de/s-anzeige/test-ad/123456789"

        result = await test_extractor._extract_sell_directly_from_ad_page()

        assert result is None

    @pytest.mark.asyncio
    async def test_extract_sell_directly_missing_buy_now_field(self, test_extractor:extract_module.AdExtractor) -> None:
        """Test sell_directly extraction when buyNowEligible field is missing."""
        test_extractor.published_ads_by_id = {123456789: {"id": 123456789, "state": "active"}}  # No buyNowEligible

        test_extractor.page = MagicMock()
        test_extractor.page.url = "https://www.kleinanzeigen.de/s-anzeige/test-ad/123456789"

        result = await test_extractor._extract_sell_directly_from_ad_page()

        assert result is None

    @pytest.mark.asyncio
    async def test_extract_sell_directly_integer_value(self, test_extractor:extract_module.AdExtractor) -> None:
        """Test sell_directly extraction when buyNowEligible is an integer (not bool)."""
        test_extractor.published_ads_by_id = {123456789: {"id": 123456789, "buyNowEligible": 1}}  # Integer, not bool

        test_extractor.page = MagicMock()
        test_extractor.page.url = "https://www.kleinanzeigen.de/s-anzeige/test-ad/123456789"

        result = await test_extractor._extract_sell_directly_from_ad_page()

        assert result is None


class TestAdExtractorCategory:
    """Tests for category extraction functionality."""

    @pytest.fixture
    def extractor(self, test_bot_config:Config) -> extract_module.AdExtractor:
        browser_mock = MagicMock(spec = Browser)
        config = test_bot_config.with_values({"ad_defaults": {"description": {"prefix": "Test Prefix", "suffix": "Test Suffix"}}})
        return extract_module.AdExtractor(browser_mock, config, Path("downloaded-ads"))

    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_extract_category(self, extractor:extract_module.AdExtractor) -> None:
        """Test category extraction from breadcrumb."""
        category_line = MagicMock()
        first_part = MagicMock()
        first_part.attrs = {"href": "/s-familie-kind-baby/c17"}
        second_part = MagicMock()
        second_part.attrs = {"href": "/s-spielzeug/c23"}

        with (
            patch.object(extractor, "web_find", new_callable = AsyncMock, side_effect = [category_line]) as mock_web_find,
            patch.object(extractor, "web_find_all", new_callable = AsyncMock, return_value = [first_part, second_part]) as mock_web_find_all,
        ):
            result = await extractor._extract_category_from_ad_page()
            assert result == "17/23"

            mock_web_find.assert_awaited_once_with(By.ID, "vap-brdcrmb")
            mock_web_find_all.assert_awaited_once_with(By.CSS_SELECTOR, "a", parent = category_line)

    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_extract_category_single_identifier(self, extractor:extract_module.AdExtractor) -> None:
        """Test category extraction when only a single breadcrumb code exists."""
        category_line = MagicMock()
        first_part = MagicMock()
        first_part.attrs = {"href": "/s-kleidung/c42"}

        with (
            patch.object(extractor, "web_find", new_callable = AsyncMock, side_effect = [category_line]) as mock_web_find,
            patch.object(extractor, "web_find_all", new_callable = AsyncMock, return_value = [first_part]) as mock_web_find_all,
        ):
            result = await extractor._extract_category_from_ad_page()
            assert result == "42/42"

            mock_web_find.assert_awaited_once_with(By.ID, "vap-brdcrmb")
            mock_web_find_all.assert_awaited_once_with(By.CSS_SELECTOR, "a", parent = category_line)

    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_extract_category_fallback_to_legacy_selectors(self, extractor:extract_module.AdExtractor) -> None:
        """Test category extraction when breadcrumb links are not available and legacy selectors are used."""
        category_line = MagicMock()
        first_part = MagicMock()
        first_part.attrs = {"href": 12345}  # Ensure str() conversion happens
        second_part = MagicMock()
        second_part.attrs = {"href": 67890}  # This will need str() conversion

        with (
            patch.object(extractor, "web_find", new_callable = AsyncMock) as mock_web_find,
            patch.object(extractor, "web_find_all", new_callable = AsyncMock, side_effect = TimeoutError) as mock_web_find_all,
        ):
            mock_web_find.side_effect = [category_line, first_part, second_part]

            result = await extractor._extract_category_from_ad_page()
            assert result == "12345/67890"

            mock_web_find.assert_any_call(By.ID, "vap-brdcrmb")
            mock_web_find.assert_any_call(By.CSS_SELECTOR, "a:nth-of-type(2)", parent = category_line)
            mock_web_find.assert_any_call(By.CSS_SELECTOR, "a:nth-of-type(3)", parent = category_line)
            mock_web_find_all.assert_awaited_once_with(By.CSS_SELECTOR, "a", parent = category_line)

    @pytest.mark.asyncio
    async def test_extract_category_legacy_selectors_timeout(self, extractor:extract_module.AdExtractor) -> None:
        """Ensure fallback timeout re-raises with translated message."""
        category_line = MagicMock()

        async def fake_web_find(selector_type:By, selector_value:str, *, parent:Element | None = None, timeout:int | float | None = None) -> Element:
            if selector_type == By.ID and selector_value == "vap-brdcrmb":
                return category_line
            raise TimeoutError("legacy selectors missing")

        with (
            patch.object(extractor, "web_find", new_callable = AsyncMock, side_effect = fake_web_find),
            patch.object(extractor, "web_find_all", new_callable = AsyncMock, side_effect = TimeoutError),
            pytest.raises(TimeoutError, match = "Unable to locate breadcrumb fallback selectors"),
        ):
            await extractor._extract_category_from_ad_page()

    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_extract_special_attributes_empty(self, extractor:extract_module.AdExtractor) -> None:
        """Test extraction of special attributes when empty."""
        belen_conf:dict[str, Any] = {"universalAnalyticsOpts": {"dimensions": {"ad_attributes": ""}}}
        with patch.object(extractor, "_extract_special_attributes_from_dom", new_callable = AsyncMock, return_value = {}):
            result = await extractor._extract_special_attributes_from_ad_page(belen_conf)
            assert result == {}

    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_extract_special_attributes_not_empty(self, extractor:extract_module.AdExtractor) -> None:
        """Test extraction of special attributes when not empty."""

        special_atts = {
            "universalAnalyticsOpts": {
                "dimensions": {"ad_attributes": "versand_s:t|color_s:creme|groesse_s:68|condition_s:alright|type_s:accessoires|art_s:maedchen"}
            }
        }
        result = await extractor._extract_special_attributes_from_ad_page(special_atts)
        assert len(result) == 5
        assert "versand_s" not in result
        assert "color_s" in result
        assert result["color_s"] == "creme"
        assert "groesse_s" in result
        assert result["groesse_s"] == "68"
        assert "condition_s" in result
        assert result["condition_s"] == "alright"
        assert "type_s" in result
        assert result["type_s"] == "accessoires"
        assert "art_s" in result
        assert result["art_s"] == "maedchen"

    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_extract_special_attributes_dom_fallback_when_missing(self, extractor:extract_module.AdExtractor) -> None:
        """When ad_attributes is missing, special attributes should be extracted via DOM fallback."""
        belen_conf:dict[str, Any] = {"universalAnalyticsOpts": {"dimensions": {}}}
        with patch.object(
            extractor,
            "_extract_special_attributes_from_dom",
            new_callable = AsyncMock,
            return_value = {"condition_s": "new"},
        ):
            result = await extractor._extract_special_attributes_from_ad_page(belen_conf)

        assert result == {"condition_s": "new"}

    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_extract_special_attributes_dom_fallback_not_called_when_present(self, extractor:extract_module.AdExtractor) -> None:
        """When ad_attributes is present, special attributes should be extracted from it directly."""
        belen_conf:dict[str, Any] = {"universalAnalyticsOpts": {"dimensions": {"ad_attributes": "condition_s:ok|versand_s:t"}}}
        with patch.object(
            extractor,
            "_extract_special_attributes_from_dom",
            new_callable = AsyncMock,
            return_value = {},
        ):
            result = await extractor._extract_special_attributes_from_ad_page(belen_conf)

        assert result == {"condition_s": "ok"}

    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_extract_special_attributes_from_dom_extracts_condition(self, extractor:extract_module.AdExtractor) -> None:
        """DOM fallback should extract condition_s from #viewad-details section."""
        detail_item = MagicMock()
        detail_item.text = "Zustand Neu"

        async def text_side_effect(by:Any, selector:str, *, parent:Any = None, **__:Any) -> str:
            if parent is detail_item:
                return "Neu"
            return ""

        async def visible_text_side_effect(element:Any) -> str:
            if element is detail_item:
                return "Zustand Neu"
            return ""

        with (
            patch.object(extractor, "web_find_all", new_callable = AsyncMock, return_value = [detail_item]),
            patch.object(extractor, "web_text", new_callable = AsyncMock, side_effect = text_side_effect),
            patch.object(extractor, "_extract_visible_text", new_callable = AsyncMock, side_effect = visible_text_side_effect),
        ):
            result = await extractor._extract_special_attributes_from_dom()

        assert result == {"condition_s": "new"}

    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_extract_special_attributes_from_dom_skips_malformed_row(self, extractor:extract_module.AdExtractor) -> None:
        """DOM fallback should skip rows where web_text raises TimeoutError and still extract valid rows."""
        good_item = MagicMock()
        good_item.text = "Zustand Neu"

        async def text_side_effect(by:Any, selector:str, *, parent:Any = None, **__:Any) -> str:
            if parent is good_item:
                return "Neu"
            raise TimeoutError("value span not found")

        async def visible_text_side_effect(element:Any) -> str:
            if element is good_item:
                return "Zustand Neu"
            return ""

        malformed_item = MagicMock()

        with (
            patch.object(
                extractor,
                "web_find_all",
                new_callable = AsyncMock,
                return_value = [malformed_item, good_item],
            ),
            patch.object(extractor, "web_text", new_callable = AsyncMock, side_effect = text_side_effect),
            patch.object(extractor, "_extract_visible_text", new_callable = AsyncMock, side_effect = visible_text_side_effect),
        ):
            result = await extractor._extract_special_attributes_from_dom()

        assert result == {"condition_s": "new"}

    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_extract_special_attributes_from_dom_returns_empty_when_no_details_section(self, extractor:extract_module.AdExtractor) -> None:
        """DOM fallback should return empty dict when the details section is not found."""
        with patch.object(
            extractor,
            "web_find_all",
            new_callable = AsyncMock,
            side_effect = TimeoutError,
        ):
            result = await extractor._extract_special_attributes_from_dom()

        assert result == {}

    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_extract_special_attributes_from_dom_skips_unrecognized_label(self, extractor:extract_module.AdExtractor) -> None:
        """DOM fallback should skip rows whose label is not in the lookup map."""
        detail_item = MagicMock()

        async def text_side_effect(by:Any, selector:str, *, parent:Any = None, **__:Any) -> str:
            if parent is detail_item:
                return "SomeValue"
            return ""

        async def visible_text_side_effect(element:Any) -> str:
            if element is detail_item:
                return "UnrecognizedLabel SomeValue"
            return ""

        with (
            patch.object(extractor, "web_find_all", new_callable = AsyncMock, return_value = [detail_item]),
            patch.object(extractor, "web_text", new_callable = AsyncMock, side_effect = text_side_effect),
            patch.object(extractor, "_extract_visible_text", new_callable = AsyncMock, side_effect = visible_text_side_effect),
        ):
            result = await extractor._extract_special_attributes_from_dom()

        assert result == {}

    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_extract_special_attributes_from_dom_skips_unmapped_condition_value(self, extractor:extract_module.AdExtractor) -> None:
        """DOM fallback should skip condition rows whose display value is not in the API mapping."""
        detail_item = MagicMock()

        async def text_side_effect(by:Any, selector:str, *, parent:Any = None, **__:Any) -> str:
            if parent is detail_item:
                return "Unbekannt"
            return ""

        async def visible_text_side_effect(element:Any) -> str:
            if element is detail_item:
                return "Zustand Unbekannt"
            return ""

        with (
            patch.object(extractor, "web_find_all", new_callable = AsyncMock, return_value = [detail_item]),
            patch.object(extractor, "web_text", new_callable = AsyncMock, side_effect = text_side_effect),
            patch.object(extractor, "_extract_visible_text", new_callable = AsyncMock, side_effect = visible_text_side_effect),
        ):
            result = await extractor._extract_special_attributes_from_dom()

        assert result == {}


class TestAdExtractorContact:
    """Tests for contact information extraction."""

    @pytest.fixture
    def extractor(self, test_bot_config:Config) -> extract_module.AdExtractor:
        browser_mock = MagicMock(spec = Browser)
        config = test_bot_config.with_values({"ad_defaults": {"description": {"prefix": "Test Prefix", "suffix": "Test Suffix"}}})
        return extract_module.AdExtractor(browser_mock, config, Path("downloaded-ads"))

    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_extract_contact_info(self, extractor:extract_module.AdExtractor) -> None:
        """Test extraction of contact information."""
        with (
            patch.object(extractor, "page", MagicMock()),
            patch.object(extractor, "web_text", new_callable = AsyncMock) as mock_web_text,
            patch.object(extractor, "web_find", new_callable = AsyncMock) as mock_web_find,
        ):
            mock_web_text.side_effect = [
                "12345 Berlin - Mitte",
                "Example Street 123,",
                "Test User",
            ]

            mock_web_find.side_effect = [
                MagicMock(),  # contact person element
                MagicMock(),  # name element
                TimeoutError(),  # phone element (simulating no phone)
            ]

            contact_info = await extractor._extract_contact_from_ad_page()
            assert contact_info.street == "Example Street 123"
            assert contact_info.zipcode == "12345"
            assert contact_info.location == "Berlin - Mitte"
            assert contact_info.name == "Test User"
            assert contact_info.phone is None

    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_extract_contact_info_timeout(self, extractor:extract_module.AdExtractor) -> None:
        """Test contact info extraction when elements are not found."""
        with (
            patch.object(extractor, "page", MagicMock()),
            patch.object(extractor, "web_text", new_callable = AsyncMock, side_effect = TimeoutError()),
            patch.object(extractor, "web_find", new_callable = AsyncMock, side_effect = TimeoutError()),
            pytest.raises(TimeoutError),
        ):
            await extractor._extract_contact_from_ad_page()

    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_extract_contact_info_with_phone(self, extractor:extract_module.AdExtractor) -> None:
        """Test extraction of contact information including phone number."""
        with (
            patch.object(extractor, "page", MagicMock()),
            patch.object(extractor, "web_text", new_callable = AsyncMock) as mock_web_text,
            patch.object(extractor, "web_find", new_callable = AsyncMock) as mock_web_find,
        ):
            mock_web_text.side_effect = ["12345 Berlin - Mitte", "Example Street 123,", "Test User", "+49(0)1234 567890"]

            phone_element = MagicMock()
            mock_web_find.side_effect = [
                MagicMock(),  # contact person element
                MagicMock(),  # name element
                phone_element,  # phone element
            ]

            contact_info = await extractor._extract_contact_from_ad_page()
            assert contact_info.phone == "01234567890"  # Normalized phone number


class TestAdExtractorDownload:
    """Tests for download functionality."""

    @pytest.fixture
    def extractor(self, test_bot_config:Config) -> extract_module.AdExtractor:
        browser_mock = MagicMock(spec = Browser)
        config = test_bot_config.with_values({"ad_defaults": {"description": {"prefix": "Test Prefix", "suffix": "Test Suffix"}}})
        return extract_module.AdExtractor(browser_mock, config, Path("downloaded-ads"))

    @pytest.mark.asyncio
    async def test_download_ad(self, extractor:extract_module.AdExtractor, tmp_path:Path) -> None:
        """Test downloading an ad - directory creation and saving ad data."""
        # Use tmp_path for OS-agnostic path handling
        download_base = tmp_path / "downloaded-ads"
        final_dir = download_base / "ad_12345_Test Advertisement Title"
        staging_dir = download_base / ".tmp-ad_12345"
        staging_yaml_path = staging_dir / "ad_12345.yaml"
        extractor.download_dir = download_base
        staging_dir.mkdir(parents = True)

        with (
            patch("kleinanzeigen_bot.extract.dicts.save_dict", autospec = True) as mock_save_dict,
            patch.object(extractor, "_extract_ad_page_info_with_directory_handling", new_callable = AsyncMock) as mock_extract_with_dir,
        ):
            mock_extract_with_dir.return_value = (
                _create_test_ad_partial(),
                staging_dir,
                final_dir,
                "ad_12345",
            )

            await extractor.download_ad(12345)

            # Verify observable behavior: extraction and save were called
            mock_extract_with_dir.assert_called_once()
            mock_save_dict.assert_called_once()

            # Verify saved to correct location with correct data
            actual_call = mock_save_dict.call_args
            actual_path = Path(actual_call[0][0])
            assert actual_path == staging_yaml_path
            assert actual_call[0][1] == mock_extract_with_dir.return_value[0].model_dump(mode = "json")
            assert final_dir.exists()
            assert not staging_dir.exists()

    @pytest.mark.asyncio
    async def test_download_ad_passes_active_override(self, extractor:extract_module.AdExtractor, tmp_path:Path) -> None:
        """Test that download_ad forwards the active override to extraction."""
        download_base = tmp_path / "downloaded-ads"
        final_dir = download_base / "ad_12345_Test Advertisement Title"
        staging_dir = download_base / ".tmp-ad_12345"
        extractor.download_dir = download_base
        staging_dir.mkdir(parents = True)

        with (
            patch("kleinanzeigen_bot.extract.dicts.save_dict", autospec = True),
            patch.object(extractor, "_extract_ad_page_info_with_directory_handling", new_callable = AsyncMock) as mock_extract_with_dir,
        ):
            mock_extract_with_dir.return_value = (
                _create_test_ad_partial(active = False),
                staging_dir,
                final_dir,
                "ad_12345",
            )

            await extractor.download_ad(12345, active = False)

            mock_extract_with_dir.assert_awaited_once_with(download_base, 12345, active_override = False)

    @pytest.mark.asyncio
    async def test_download_ad_writes_schema_compliant_yaml(self, extractor:extract_module.AdExtractor, tmp_path:Path) -> None:
        """Test that downloaded ad YAML validates against ad.schema.json."""
        download_base = tmp_path / "downloaded-ads"
        final_dir = download_base / "ad_12345_Test Advertisement Title"
        staging_dir = download_base / ".tmp-ad_12345"
        yaml_path = final_dir / "ad_12345.yaml"
        extractor.download_dir = download_base
        staging_dir.mkdir(parents = True)

        with patch.object(extractor, "_extract_ad_page_info_with_directory_handling", new_callable = AsyncMock) as mock_extract_with_dir:
            mock_extract_with_dir.return_value = (
                _create_test_ad_partial(created_on = "2026-03-08T00:00:00+01:00", updated_on = "2026-03-09T01:02:03+01:00"),
                staging_dir,
                final_dir,
                "ad_12345",
            )

            await extractor.download_ad(12345)

        loaded_ad = YAML(typ = "safe").load(await asyncio.to_thread(_read_text_file, yaml_path))
        schema = json.loads(await asyncio.to_thread(_read_text_file, SCHEMA_PATH))

        Draft202012Validator(schema).validate(loaded_ad)
        assert isinstance(loaded_ad["created_on"], str)
        assert isinstance(loaded_ad["updated_on"], str)

    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_download_images_no_images(self, extractor:extract_module.AdExtractor) -> None:
        """Test image download when no images are found."""
        with patch.object(extractor, "web_find", new_callable = AsyncMock, side_effect = TimeoutError):
            image_paths = await extractor._download_images_from_ad_page("/some/dir", "ad_12345")
            assert len(image_paths) == 0

    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_download_images_with_none_url(self, extractor:extract_module.AdExtractor) -> None:
        """Test image download when some images have None as src attribute."""
        image_box_mock = MagicMock()

        # Create image elements - one with valid src, one with None src
        img_with_url = MagicMock()
        img_with_url.attrs = {"src": "http://example.com/valid_image.jpg"}

        img_without_url = MagicMock()
        img_without_url.attrs = {"src": None}

        with (
            patch.object(extractor, "web_find", new_callable = AsyncMock, return_value = image_box_mock),
            patch.object(extractor, "web_find_all", new_callable = AsyncMock, return_value = [img_with_url, img_without_url]),
            patch.object(extract_module.AdExtractor, "_download_and_save_image_sync", return_value = "/some/dir/ad_12345__img1.jpg"),
        ):
            image_paths = await extractor._download_images_from_ad_page("/some/dir", "ad_12345")

            # Should only download the one valid image (skip the None)
            assert len(image_paths) == 1
            assert image_paths[0] == "ad_12345__img1.jpg"

    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_extract_ad_page_info_with_directory_handling_final_dir_exists(self, extractor:extract_module.AdExtractor, tmp_path:Path) -> None:
        """Test directory handling when final_dir already exists - extraction should stage."""
        base_dir = tmp_path / "downloaded-ads"
        base_dir.mkdir()

        # Create the final directory that should be deleted
        final_dir = base_dir / "ad_12345_Test Title"
        final_dir.mkdir()
        old_file = final_dir / "old_file.txt"
        old_file.write_text("old content")

        # Mock the page
        page_mock = MagicMock()
        page_mock.url = "https://www.kleinanzeigen.de/s-anzeige/test/12345"
        extractor.page = page_mock

        with (
            patch.object(
                extractor,
                "web_text",
                new_callable = AsyncMock,
                side_effect = [
                    "Test Title",  # Title extraction
                    "Test Title",  # Second title call for full extraction
                    "Description text",  # Description
                    "03.02.2025",  # Creation date
                ],
            ),
            patch.object(
                extractor,
                "web_execute",
                new_callable = AsyncMock,
                return_value = {"universalAnalyticsOpts": {"dimensions": {"l3_category_id": "", "ad_attributes": ""}}},
            ),
            patch.object(extractor, "_extract_category_from_ad_page", new_callable = AsyncMock, return_value = "160"),
            patch.object(extractor, "_extract_special_attributes_from_ad_page", new_callable = AsyncMock, return_value = {}),
            patch.object(extractor, "_extract_pricing_info_from_ad_page", new_callable = AsyncMock, return_value = (None, "NOT_APPLICABLE")),
            patch.object(extractor, "_extract_shipping_info_from_ad_page", new_callable = AsyncMock, return_value = ("NOT_APPLICABLE", None, None)),
            patch.object(extractor, "_extract_sell_directly_from_ad_page", new_callable = AsyncMock, return_value = False),
            patch.object(extractor, "_download_images_from_ad_page", new_callable = AsyncMock, return_value = []),
            patch.object(
                extractor,
                "_extract_contact_from_ad_page",
                new_callable = AsyncMock,
                return_value = ContactPartial(
                    name = "Test",
                    zipcode = "12345",
                    location = "Berlin",
                ),
            ),
        ):
            ad_cfg, staging_dir, final_dir_result, _ad_file_stem = await extractor._extract_ad_page_info_with_directory_handling(base_dir, 12345)

            assert final_dir_result == final_dir
            assert final_dir_result.exists()
            assert old_file.exists()
            assert staging_dir.exists()
            assert ad_cfg.title == "Test Title"

    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_extract_ad_page_info_with_directory_handling_rename_enabled(self, extractor:extract_module.AdExtractor, tmp_path:Path) -> None:
        """Test directory handling when temp_dir exists and rename_existing_folders is True."""
        base_dir = tmp_path / "downloaded-ads"
        base_dir.mkdir()

        # Create the temp directory (without title)
        temp_dir = base_dir / "ad_12345"
        temp_dir.mkdir()
        existing_file = temp_dir / "existing_image.jpg"
        existing_file.write_text("existing image data")

        # Enable rename_existing_folders in config
        extractor.config.download.rename_existing_folders = True

        # Mock the page
        page_mock = MagicMock()
        page_mock.url = "https://www.kleinanzeigen.de/s-anzeige/test/12345"
        extractor.page = page_mock

        with (
            patch.object(
                extractor,
                "web_text",
                new_callable = AsyncMock,
                side_effect = [
                    "Test Title",  # Title extraction
                    "Test Title",  # Second title call for full extraction
                    "Description text",  # Description
                    "03.02.2025",  # Creation date
                ],
            ),
            patch.object(
                extractor,
                "web_execute",
                new_callable = AsyncMock,
                return_value = {"universalAnalyticsOpts": {"dimensions": {"l3_category_id": "", "ad_attributes": ""}}},
            ),
            patch.object(extractor, "_extract_category_from_ad_page", new_callable = AsyncMock, return_value = "160"),
            patch.object(extractor, "_extract_special_attributes_from_ad_page", new_callable = AsyncMock, return_value = {}),
            patch.object(extractor, "_extract_pricing_info_from_ad_page", new_callable = AsyncMock, return_value = (None, "NOT_APPLICABLE")),
            patch.object(extractor, "_extract_shipping_info_from_ad_page", new_callable = AsyncMock, return_value = ("NOT_APPLICABLE", None, None)),
            patch.object(extractor, "_extract_sell_directly_from_ad_page", new_callable = AsyncMock, return_value = False),
            patch.object(extractor, "_download_images_from_ad_page", new_callable = AsyncMock, return_value = []),
            patch.object(
                extractor,
                "_extract_contact_from_ad_page",
                new_callable = AsyncMock,
                return_value = ContactPartial(
                    name = "Test",
                    zipcode = "12345",
                    location = "Berlin",
                ),
            ),
        ):
            ad_cfg, staging_dir, result_dir, _ad_file_stem = await extractor._extract_ad_page_info_with_directory_handling(base_dir, 12345)

            # Verify the directory was renamed from temp_dir to final_dir
            final_dir = base_dir / "ad_12345_Test Title"
            assert result_dir == final_dir
            assert result_dir.exists()
            assert not temp_dir.exists()  # Old temp dir should be gone
            assert (result_dir / "existing_image.jpg").exists()  # File should be preserved
            assert staging_dir.exists()
            assert ad_cfg.title == "Test Title"

    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_extract_ad_page_info_with_directory_handling_use_existing(self, extractor:extract_module.AdExtractor, tmp_path:Path) -> None:
        """Test directory handling when temp_dir exists and rename_existing_folders is False (default)."""
        base_dir = tmp_path / "downloaded-ads"
        base_dir.mkdir()

        # Create the temp directory (without title)
        temp_dir = base_dir / "ad_12345"
        temp_dir.mkdir()
        existing_file = temp_dir / "existing_image.jpg"
        existing_file.write_text("existing image data")

        # Ensure rename_existing_folders is False (default)
        extractor.config.download.rename_existing_folders = False

        # Mock the page
        page_mock = MagicMock()
        page_mock.url = "https://www.kleinanzeigen.de/s-anzeige/test/12345"
        extractor.page = page_mock

        with (
            patch.object(
                extractor,
                "web_text",
                new_callable = AsyncMock,
                side_effect = [
                    "Test Title",  # Title extraction
                    "Test Title",  # Second title call for full extraction
                    "Description text",  # Description
                    "03.02.2025",  # Creation date
                ],
            ),
            patch.object(
                extractor,
                "web_execute",
                new_callable = AsyncMock,
                return_value = {"universalAnalyticsOpts": {"dimensions": {"l3_category_id": "", "ad_attributes": ""}}},
            ),
            patch.object(extractor, "_extract_category_from_ad_page", new_callable = AsyncMock, return_value = "160"),
            patch.object(extractor, "_extract_special_attributes_from_ad_page", new_callable = AsyncMock, return_value = {}),
            patch.object(extractor, "_extract_pricing_info_from_ad_page", new_callable = AsyncMock, return_value = (None, "NOT_APPLICABLE")),
            patch.object(extractor, "_extract_shipping_info_from_ad_page", new_callable = AsyncMock, return_value = ("NOT_APPLICABLE", None, None)),
            patch.object(extractor, "_extract_sell_directly_from_ad_page", new_callable = AsyncMock, return_value = False),
            patch.object(extractor, "_download_images_from_ad_page", new_callable = AsyncMock, return_value = []),
            patch.object(
                extractor,
                "_extract_contact_from_ad_page",
                new_callable = AsyncMock,
                return_value = ContactPartial(
                    name = "Test",
                    zipcode = "12345",
                    location = "Berlin",
                ),
            ),
        ):
            ad_cfg, staging_dir, result_dir, _ad_file_stem = await extractor._extract_ad_page_info_with_directory_handling(base_dir, 12345)

            # Verify the existing temp_dir was used (not renamed)
            assert result_dir == temp_dir
            assert result_dir.exists()
            assert (result_dir / "existing_image.jpg").exists()  # File should be preserved
            assert staging_dir.exists()
            assert ad_cfg.title == "Test Title"

    @pytest.mark.asyncio
    async def test_download_ad_with_umlauts_in_title(self, extractor:extract_module.AdExtractor, tmp_path:Path) -> None:
        """Test cross-platform Unicode handling for ad titles with umlauts (issue #728).

        Verifies that:
        1. Directories are created with NFC-normalized names (via sanitize_folder_name)
        2. Files can be saved to those directories (via save_dict's NFC normalization)
        3. No FileNotFoundError occurs due to NFC/NFD mismatch on Linux/Windows
        """
        # Title with German umlauts (ä) - common in real ads
        title_with_umlauts = "KitchenAid Zuhälter - nie benutzt"

        # Mock the page
        page_mock = MagicMock()
        page_mock.url = "https://www.kleinanzeigen.de/s-anzeige/test/12345"
        extractor.page = page_mock

        base_dir = tmp_path / "downloaded-ads"
        base_dir.mkdir()

        with (
            patch.object(
                extractor,
                "web_text",
                new_callable = AsyncMock,
                side_effect = [
                    title_with_umlauts,  # Title extraction
                    title_with_umlauts,  # Second title call for full extraction
                    "Description text",  # Description
                    "03.02.2025",  # Creation date
                ],
            ),
            patch.object(
                extractor,
                "web_execute",
                new_callable = AsyncMock,
                return_value = {"universalAnalyticsOpts": {"dimensions": {"l3_category_id": "", "ad_attributes": ""}}},
            ),
            patch.object(extractor, "_extract_category_from_ad_page", new_callable = AsyncMock, return_value = "160"),
            patch.object(extractor, "_extract_special_attributes_from_ad_page", new_callable = AsyncMock, return_value = {}),
            patch.object(extractor, "_extract_pricing_info_from_ad_page", new_callable = AsyncMock, return_value = (None, "NOT_APPLICABLE")),
            patch.object(extractor, "_extract_shipping_info_from_ad_page", new_callable = AsyncMock, return_value = ("NOT_APPLICABLE", None, None)),
            patch.object(extractor, "_extract_sell_directly_from_ad_page", new_callable = AsyncMock, return_value = False),
            patch.object(extractor, "_download_images_from_ad_page", new_callable = AsyncMock, return_value = []),
            patch.object(
                extractor,
                "_extract_contact_from_ad_page",
                new_callable = AsyncMock,
                return_value = ContactPartial(
                    name = "Test",
                    zipcode = "12345",
                    location = "Berlin",
                ),
            ),
        ):
            ad_cfg, staging_dir, _result_dir, _ad_file_stem = await extractor._extract_ad_page_info_with_directory_handling(base_dir, 12345)

            # Verify directory was created with NFC-normalized name
            assert staging_dir.exists()
            assert ad_cfg.title == title_with_umlauts

            # Test saving YAML file to the Unicode directory path
            # Before fix: Failed on Linux/Windows due to NFC/NFD mismatch
            # After fix: Both directory and file use NFC normalization
            ad_file_path = Path(staging_dir) / "ad_12345.yaml"

            from kleinanzeigen_bot.utils import dicts  # noqa: PLC0415

            header_string = (
                "# yaml-language-server: $schema=https://raw.githubusercontent.com/Second-Hand-Friends/kleinanzeigen-bot/refs/heads/main/schemas/ad.schema.json"
            )

            # save_dict normalizes path to NFC, matching the NFC directory name
            dicts.save_dict(str(ad_file_path), ad_cfg.model_dump(), header = header_string)

            # Verify file was created successfully (no FileNotFoundError)
            assert ad_file_path.exists()
            assert ad_file_path.is_file()

    @pytest.mark.asyncio
    async def test_download_ad_uses_custom_folder_and_file_templates(self, extractor:extract_module.AdExtractor, tmp_path:Path) -> None:
        download_base = tmp_path / "downloaded-ads"
        final_dir = download_base / "listing_12345_Test Advertisement Title"
        ad_file_stem = "listing_12345_Test Advertisement Title"
        staging_dir = download_base / f".tmp-{ad_file_stem}"
        yaml_path = staging_dir / f"{ad_file_stem}.yaml"
        extractor.download_dir = download_base
        extractor.config.download.folder_name_template = "listing_{id}_{title}"
        extractor.config.download.ad_file_name_template = "listing_{id}_{title}"
        staging_dir.mkdir(parents = True)

        with (
            patch("kleinanzeigen_bot.extract.dicts.save_dict", autospec = True) as mock_save_dict,
            patch.object(extractor, "_extract_ad_page_info_with_directory_handling", new_callable = AsyncMock) as mock_extract_with_dir,
        ):
            mock_extract_with_dir.return_value = (
                _create_test_ad_partial(),
                staging_dir,
                final_dir,
                ad_file_stem,
            )

            await extractor.download_ad(12345)

            mock_extract_with_dir.assert_called_once()
            mock_save_dict.assert_called_once()
            assert Path(mock_save_dict.call_args[0][0]) == yaml_path

    @pytest.mark.asyncio
    async def test_download_ad_replaces_final_dir_after_staging_success(self, extractor:extract_module.AdExtractor, tmp_path:Path) -> None:
        download_base = tmp_path / "downloaded-ads"
        final_dir = download_base / "ad_12345_Test Advertisement Title"
        staging_dir = download_base / ".tmp-ad_12345"
        backup_dir = download_base / ".bak-ad_12345"
        old_file = final_dir / "old_file.txt"
        final_yaml = final_dir / "ad_12345.yaml"

        extractor.download_dir = download_base
        final_dir.mkdir(parents = True)
        old_file.write_text("old content")
        staging_dir.mkdir(parents = True)
        original_rename = Path.rename

        with (
            patch.object(extractor, "_extract_ad_page_info_with_directory_handling", new_callable = AsyncMock) as mock_extract_with_dir,
            patch.object(Path, "rename", autospec = True, side_effect = original_rename) as mock_rename,
        ):
            mock_extract_with_dir.return_value = (
                _create_test_ad_partial(),
                staging_dir,
                final_dir,
                "ad_12345",
            )

            await extractor.download_ad(12345)

        assert final_dir.exists()
        assert final_yaml.exists()
        assert not old_file.exists()
        assert not backup_dir.exists()
        assert not staging_dir.exists()
        assert mock_rename.call_args_list == [
            call(final_dir, backup_dir),
            call(staging_dir, final_dir),
        ]

    @pytest.mark.asyncio
    async def test_download_ad_preserves_final_dir_when_yaml_write_fails(self, extractor:extract_module.AdExtractor, tmp_path:Path) -> None:
        download_base = tmp_path / "downloaded-ads"
        final_dir = download_base / "ad_12345_Test Advertisement Title"
        staging_dir = download_base / ".tmp-ad_12345"
        backup_dir = download_base / ".bak-ad_12345"
        old_file = final_dir / "old_file.txt"

        extractor.download_dir = download_base
        final_dir.mkdir(parents = True)
        old_file.write_text("old content")
        staging_dir.mkdir(parents = True)

        with (
            patch.object(extractor, "_extract_ad_page_info_with_directory_handling", new_callable = AsyncMock) as mock_extract_with_dir,
            patch("kleinanzeigen_bot.extract.dicts.save_dict", autospec = True, side_effect = OSError("write failed")),
            patch.object(Path, "rename", autospec = True) as mock_rename,
        ):
            mock_extract_with_dir.return_value = (
                _create_test_ad_partial(),
                staging_dir,
                final_dir,
                "ad_12345",
            )

            with pytest.raises(OSError, match = "write failed"):
                await extractor.download_ad(12345)

        assert final_dir.exists()
        assert old_file.exists()
        assert not backup_dir.exists()
        assert not staging_dir.exists()
        mock_rename.assert_not_called()

    @pytest.mark.asyncio
    async def test_download_ad_cleans_staging_when_yaml_write_fails_without_existing_final_dir(
        self,
        extractor:extract_module.AdExtractor,
        tmp_path:Path,
    ) -> None:
        download_base = tmp_path / "downloaded-ads"
        final_dir = download_base / "ad_12345_Test Advertisement Title"
        staging_dir = download_base / ".tmp-ad_12345"
        backup_dir = download_base / ".bak-ad_12345"

        extractor.download_dir = download_base
        staging_dir.mkdir(parents = True)

        with (
            patch.object(extractor, "_extract_ad_page_info_with_directory_handling", new_callable = AsyncMock) as mock_extract_with_dir,
            patch("kleinanzeigen_bot.extract.dicts.save_dict", autospec = True, side_effect = OSError("write failed")),
            patch.object(Path, "rename", autospec = True) as mock_rename,
        ):
            mock_extract_with_dir.return_value = (
                _create_test_ad_partial(),
                staging_dir,
                final_dir,
                "ad_12345",
            )

            with pytest.raises(OSError, match = "write failed"):
                await extractor.download_ad(12345)

        assert not final_dir.exists()
        assert not backup_dir.exists()
        assert not staging_dir.exists()
        mock_rename.assert_not_called()

    @pytest.mark.asyncio
    async def test_download_ad_skips_staging_cleanup_when_staging_dir_is_missing(
        self,
        extractor:extract_module.AdExtractor,
        tmp_path:Path,
    ) -> None:
        download_base = tmp_path / "downloaded-ads"
        final_dir = download_base / "ad_12345_Test Advertisement Title"
        staging_dir = download_base / ".tmp-ad_12345"

        extractor.download_dir = download_base

        with (
            patch.object(extractor, "_extract_ad_page_info_with_directory_handling", new_callable = AsyncMock) as mock_extract_with_dir,
            patch("kleinanzeigen_bot.extract.dicts.save_dict", autospec = True, side_effect = OSError("write failed")),
            patch("kleinanzeigen_bot.extract.shutil.rmtree", autospec = True) as mock_rmtree,
        ):
            mock_extract_with_dir.return_value = (
                _create_test_ad_partial(),
                staging_dir,
                final_dir,
                "ad_12345",
            )

            with pytest.raises(OSError, match = "write failed"):
                await extractor.download_ad(12345)

        mock_rmtree.assert_not_called()

    @pytest.mark.parametrize(
        ("cleanup_target", "expected_warning"),
        [
            pytest.param("staging", "Could not remove staging directory", id = "staging"),
            pytest.param("backup", "Could not remove backup directory", id = "backup"),
        ],
    )
    @pytest.mark.asyncio
    async def test_download_ad_logs_warning_when_cleanup_fails(
        self,
        extractor:extract_module.AdExtractor,
        tmp_path:Path,
        caplog:pytest.LogCaptureFixture,
        cleanup_target:str,
        expected_warning:str,
    ) -> None:
        download_base = tmp_path / "downloaded-ads"
        final_dir = download_base / "ad_12345_Test Advertisement Title"
        staging_dir = download_base / ".tmp-ad_12345"
        backup_dir = download_base / ".bak-ad_12345"
        final_yaml = final_dir / "ad_12345.yaml"

        extractor.download_dir = download_base
        final_dir.mkdir(parents = True)
        staging_dir.mkdir(parents = True)

        def rmtree_side_effect(path:str | Path, *_args:object, **_kwargs:object) -> None:
            normalized_path = Path(path)
            if cleanup_target == "backup" and normalized_path == backup_dir:
                raise OSError("busy")
            if cleanup_target == "staging" and normalized_path == staging_dir:
                raise OSError("busy")
            # Any other cleanup target would be unexpected for this scenario.
            raise AssertionError(f"Unexpected rmtree path: {path}")

        if cleanup_target == "staging":
            with (
                patch.object(extractor, "_extract_ad_page_info_with_directory_handling", new_callable = AsyncMock) as mock_extract_with_dir,
                patch("kleinanzeigen_bot.extract.dicts.save_dict", autospec = True, side_effect = OSError("write failed")),
                patch("kleinanzeigen_bot.extract.shutil.rmtree", autospec = True, side_effect = rmtree_side_effect),
                caplog.at_level("WARNING"),
            ):
                mock_extract_with_dir.return_value = (
                    _create_test_ad_partial(),
                    staging_dir,
                    final_dir,
                    "ad_12345",
                )
                with pytest.raises(OSError, match = "write failed"):
                    await extractor.download_ad(12345)
        else:
            with (
                patch.object(extractor, "_extract_ad_page_info_with_directory_handling", new_callable = AsyncMock) as mock_extract_with_dir,
                patch("kleinanzeigen_bot.extract.shutil.rmtree", autospec = True, side_effect = rmtree_side_effect),
                caplog.at_level("WARNING"),
            ):
                mock_extract_with_dir.return_value = (
                    _create_test_ad_partial(),
                    staging_dir,
                    final_dir,
                    "ad_12345",
                )
                await extractor.download_ad(12345)

        if cleanup_target == "backup":
            assert final_dir.exists()
            assert final_yaml.exists()
            assert backup_dir.exists()
            assert not staging_dir.exists()
        else:
            assert staging_dir.exists()

        assert any(expected_warning in message for message in caplog.messages)

    @pytest.mark.asyncio
    async def test_download_ad_restores_final_dir_when_swap_rename_fails(self, extractor:extract_module.AdExtractor, tmp_path:Path) -> None:
        download_base = tmp_path / "downloaded-ads"
        final_dir = download_base / "ad_12345_Test Advertisement Title"
        staging_dir = download_base / ".tmp-ad_12345"
        backup_dir = download_base / ".bak-ad_12345"
        old_file = final_dir / "old_file.txt"

        extractor.download_dir = download_base
        final_dir.mkdir(parents = True)
        old_file.write_text("old content")
        staging_dir.mkdir(parents = True)

        original_rename = Path.rename
        rename_calls:list[tuple[Path, Path]] = []

        def rename_side_effect(path_obj:Path, target:Path) -> Path:
            rename_calls.append((path_obj, target))
            if path_obj == staging_dir and target == final_dir:
                raise OSError("rename failed")
            return original_rename(path_obj, target)

        with (
            patch.object(extractor, "_extract_ad_page_info_with_directory_handling", new_callable = AsyncMock) as mock_extract_with_dir,
            patch("kleinanzeigen_bot.extract.dicts.save_dict", autospec = True),
            patch.object(Path, "rename", autospec = True, side_effect = rename_side_effect),
        ):
            mock_extract_with_dir.return_value = (
                _create_test_ad_partial(),
                staging_dir,
                final_dir,
                "ad_12345",
            )

            with pytest.raises(OSError, match = "rename failed"):
                await extractor.download_ad(12345)

        assert final_dir.exists()
        assert old_file.exists()
        assert not staging_dir.exists()
        assert not backup_dir.exists()
        assert rename_calls == [
            (final_dir, backup_dir),
            (staging_dir, final_dir),
            (backup_dir, final_dir),
        ]

    @pytest.mark.asyncio
    async def test_download_ad_fails_when_backup_dir_already_exists(self, extractor:extract_module.AdExtractor, tmp_path:Path) -> None:
        download_base = tmp_path / "downloaded-ads"
        final_dir = download_base / "ad_12345_Test Advertisement Title"
        staging_dir = download_base / ".tmp-ad_12345"
        backup_dir = download_base / ".bak-ad_12345"
        old_file = final_dir / "old_file.txt"

        extractor.download_dir = download_base
        final_dir.mkdir(parents = True)
        old_file.write_text("old content")
        staging_dir.mkdir(parents = True)
        backup_dir.mkdir(parents = True)

        with patch.object(extractor, "_extract_ad_page_info_with_directory_handling", new_callable = AsyncMock) as mock_extract_with_dir:
            mock_extract_with_dir.return_value = (
                _create_test_ad_partial(),
                staging_dir,
                final_dir,
                "ad_12345",
            )

            with pytest.raises(FileExistsError):
                await extractor.download_ad(12345)

        assert final_dir.exists()
        assert old_file.exists()
        assert backup_dir.exists()
        assert not staging_dir.exists()

    @pytest.mark.asyncio
    async def test_download_ad_does_not_restore_preexisting_backup_when_final_dir_missing(
        self,
        extractor:extract_module.AdExtractor,
        tmp_path:Path,
    ) -> None:
        download_base = tmp_path / "downloaded-ads"
        final_dir = download_base / "ad_12345_Test Advertisement Title"
        staging_dir = download_base / ".tmp-ad_12345"
        backup_dir = download_base / ".bak-ad_12345"
        backup_file = backup_dir / "old_file.txt"

        extractor.download_dir = download_base
        staging_dir.mkdir(parents = True)
        backup_dir.mkdir(parents = True)
        backup_file.write_text("old content")

        with patch.object(extractor, "_extract_ad_page_info_with_directory_handling", new_callable = AsyncMock) as mock_extract_with_dir:
            mock_extract_with_dir.return_value = (
                _create_test_ad_partial(),
                staging_dir,
                final_dir,
                "ad_12345",
            )

            with pytest.raises(FileExistsError):
                await extractor.download_ad(12345)

        assert not final_dir.exists()
        assert backup_dir.exists()
        assert backup_file.exists()
        assert not staging_dir.exists()

    @pytest.mark.asyncio
    async def test_download_ad_logs_restore_error_when_backup_restore_fails(
        self,
        extractor:extract_module.AdExtractor,
        tmp_path:Path,
        caplog:pytest.LogCaptureFixture,
    ) -> None:
        download_base = tmp_path / "downloaded-ads"
        final_dir = download_base / "ad_12345_Test Advertisement Title"
        staging_dir = download_base / ".tmp-ad_12345"
        backup_dir = download_base / ".bak-ad_12345"
        old_file = final_dir / "old_file.txt"

        extractor.download_dir = download_base
        final_dir.mkdir(parents = True)
        old_file.write_text("old content")
        staging_dir.mkdir(parents = True)

        original_rename = Path.rename

        def rename_side_effect(path_obj:Path, target:Path) -> Path:
            if path_obj == staging_dir and target == final_dir:
                raise OSError("staging rename failed")
            if path_obj == backup_dir and target == final_dir:
                raise OSError("backup restore failed")
            return original_rename(path_obj, target)

        with (
            patch.object(extractor, "_extract_ad_page_info_with_directory_handling", new_callable = AsyncMock) as mock_extract_with_dir,
            patch("kleinanzeigen_bot.extract.dicts.save_dict", autospec = True),
            patch.object(Path, "rename", autospec = True, side_effect = rename_side_effect),
        ):
            mock_extract_with_dir.return_value = (
                _create_test_ad_partial(),
                staging_dir,
                final_dir,
                "ad_12345",
            )

            with caplog.at_level("ERROR"), pytest.raises(OSError, match = "staging rename failed"):
                await extractor.download_ad(12345)

        assert not final_dir.exists()
        assert backup_dir.exists()
        assert not staging_dir.exists()
        assert any("Failed to restore backup directory" in message for message in caplog.messages)

    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_extract_ad_page_info_with_directory_handling_cleans_staging_on_failure(
        self,
        extractor:extract_module.AdExtractor,
        tmp_path:Path,
    ) -> None:
        base_dir = tmp_path / "downloaded-ads"
        base_dir.mkdir()
        expected_staging_dir = base_dir / ".tmp-ad_12345"

        with (
            patch.object(extractor, "_extract_title_from_ad_page", new_callable = AsyncMock, return_value = "Test Title"),
            patch.object(extractor, "_extract_ad_page_info", new_callable = AsyncMock, side_effect = RuntimeError("extract failed")),
            pytest.raises(RuntimeError, match = "extract failed"),
        ):
            await extractor._extract_ad_page_info_with_directory_handling(base_dir, 12345)

        assert not expected_staging_dir.exists()

    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_extract_ad_page_info_with_directory_handling_removes_stale_staging_dir(
        self,
        extractor:extract_module.AdExtractor,
        tmp_path:Path,
    ) -> None:
        base_dir = tmp_path / "downloaded-ads"
        base_dir.mkdir()
        stale_staging_dir = base_dir / ".tmp-ad_12345"
        stale_staging_dir.mkdir()
        stale_file = stale_staging_dir / "stale.txt"
        stale_file.write_text("stale")

        ad_cfg = _create_test_ad_partial(title = "Test Title")

        with (
            patch.object(extractor, "_extract_title_from_ad_page", new_callable = AsyncMock, return_value = "Test Title"),
            patch.object(extractor, "_extract_ad_page_info", new_callable = AsyncMock, return_value = ad_cfg),
        ):
            _cfg, staging_dir, _final_dir, _ad_file_stem = await extractor._extract_ad_page_info_with_directory_handling(base_dir, 12345)

        assert staging_dir.exists()
        assert not stale_file.exists()

    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_extract_ad_page_info_with_directory_handling_continues_when_stale_cleanup_raises_but_dir_is_gone(
        self,
        extractor:extract_module.AdExtractor,
        tmp_path:Path,
    ) -> None:
        base_dir = tmp_path / "downloaded-ads"
        base_dir.mkdir()
        stale_staging_dir = base_dir / ".tmp-ad_12345"
        stale_staging_dir.mkdir()

        ad_cfg = _create_test_ad_partial(title = "Test Title")

        original_rmtree = shutil.rmtree

        def rmtree_side_effect(path:str, *_args:object, **_kwargs:object) -> None:
            original_rmtree(path)
            raise OSError("cleanup race")

        with (
            patch.object(extractor, "_extract_title_from_ad_page", new_callable = AsyncMock, return_value = "Test Title"),
            patch("kleinanzeigen_bot.extract.shutil.rmtree", autospec = True, side_effect = rmtree_side_effect),
            patch.object(extractor, "_extract_ad_page_info", new_callable = AsyncMock, return_value = ad_cfg),
        ):
            _cfg, staging_dir, _final_dir, _ad_file_stem = await extractor._extract_ad_page_info_with_directory_handling(base_dir, 12345)

        assert staging_dir.exists()

    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_extract_ad_page_info_with_directory_handling_skips_legacy_rename_when_final_dir_exists(
        self,
        extractor:extract_module.AdExtractor,
        tmp_path:Path,
    ) -> None:
        base_dir = tmp_path / "downloaded-ads"
        base_dir.mkdir()
        legacy_dir = base_dir / "ad_12345"
        final_dir = base_dir / "ad_12345_Test Title"
        legacy_dir.mkdir()
        final_dir.mkdir()

        ad_cfg = _create_test_ad_partial(title = "Test Title")

        extractor.config.download.rename_existing_folders = True

        with (
            patch.object(extractor, "_extract_title_from_ad_page", new_callable = AsyncMock, return_value = "Test Title"),
            patch.object(extractor, "_extract_ad_page_info", new_callable = AsyncMock, return_value = ad_cfg),
        ):
            _cfg, staging_dir, result_dir, _ad_file_stem = await extractor._extract_ad_page_info_with_directory_handling(base_dir, 12345)

        assert result_dir == final_dir
        assert legacy_dir.exists()
        assert final_dir.exists()
        assert staging_dir.exists()

    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_extract_ad_page_info_with_directory_handling_aborts_when_stale_staging_cleanup_fails(
        self,
        extractor:extract_module.AdExtractor,
        tmp_path:Path,
    ) -> None:
        base_dir = tmp_path / "downloaded-ads"
        base_dir.mkdir()
        stale_staging_dir = base_dir / ".tmp-ad_12345"
        stale_staging_dir.mkdir()

        with (
            patch.object(extractor, "_extract_title_from_ad_page", new_callable = AsyncMock, return_value = "Test Title"),
            patch("kleinanzeigen_bot.extract.shutil.rmtree", autospec = True, side_effect = OSError("busy")),
            patch.object(extractor, "_extract_ad_page_info", new_callable = AsyncMock) as mock_extract,
            pytest.raises(OSError, match = "Could not remove stale staging directory"),
        ):
            await extractor._extract_ad_page_info_with_directory_handling(base_dir, 12345)

        mock_extract.assert_not_called()
        assert stale_staging_dir.exists()

    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_extract_ad_page_info_with_directory_handling_logs_warning_when_failure_cleanup_fails(
        self,
        extractor:extract_module.AdExtractor,
        tmp_path:Path,
        caplog:pytest.LogCaptureFixture,
    ) -> None:
        base_dir = tmp_path / "downloaded-ads"
        base_dir.mkdir()

        with (
            patch.object(extractor, "_extract_title_from_ad_page", new_callable = AsyncMock, return_value = "Test Title"),
            patch.object(extractor, "_extract_ad_page_info", new_callable = AsyncMock, side_effect = RuntimeError("extract failed")),
            patch("kleinanzeigen_bot.extract.shutil.rmtree", autospec = True, side_effect = OSError("busy")),
            caplog.at_level("WARNING"),
            pytest.raises(RuntimeError, match = "extract failed"),
        ):
            await extractor._extract_ad_page_info_with_directory_handling(base_dir, 12345)

        assert any("Could not remove staging directory" in message for message in caplog.messages)

    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_extract_ad_page_info_with_directory_handling_skips_failure_cleanup_when_staging_is_missing(
        self,
        extractor:extract_module.AdExtractor,
        tmp_path:Path,
    ) -> None:
        base_dir = tmp_path / "downloaded-ads"
        base_dir.mkdir()
        expected_staging_dir = base_dir / ".tmp-ad_12345"

        async def failing_extract(*_args:object, **_kwargs:object) -> None:
            if expected_staging_dir.exists():
                expected_staging_dir.rmdir()
            raise RuntimeError("extract failed")

        with (
            patch.object(extractor, "_extract_title_from_ad_page", new_callable = AsyncMock, return_value = "Test Title"),
            patch.object(extractor, "_extract_ad_page_info", new_callable = AsyncMock, side_effect = failing_extract),
            patch("kleinanzeigen_bot.extract.shutil.rmtree", autospec = True) as mock_rmtree,
            pytest.raises(RuntimeError, match = "extract failed"),
        ):
            await extractor._extract_ad_page_info_with_directory_handling(base_dir, 12345)

        # only stale cleanup (if any) should be attempted; failure cleanup branch skips when staging is already gone
        assert all(Path(call.args[0]) != expected_staging_dir for call in mock_rmtree.call_args_list)

    @pytest.mark.asyncio
    async def test_download_images_use_provided_ad_file_stem(self, extractor:extract_module.AdExtractor) -> None:
        image_box_mock = MagicMock()

        img_with_url = MagicMock()
        img_with_url.attrs = {"src": "http://example.com/valid_image.jpg"}

        with (
            patch.object(extractor, "web_find", new_callable = AsyncMock, return_value = image_box_mock),
            patch.object(extractor, "web_find_all", new_callable = AsyncMock, return_value = [img_with_url]),
            patch.object(extract_module.AdExtractor, "_download_and_save_image_sync", return_value = "/some/dir/listing_12345__img1.jpg"),
        ):
            image_paths = await extractor._download_images_from_ad_page("/some/dir", "listing_12345")

        assert image_paths == ["listing_12345__img1.jpg"]

    def test_render_download_ad_file_stem_reserves_suffix_budget(self, extractor:extract_module.AdExtractor) -> None:
        long_title = "x" * 600
        stem = extractor._render_download_ad_file_stem(12345, long_title)

        assert "12345" in stem
        assert len(stem) <= 255 - len("__img9999.jpeg")

    def test_render_download_folder_name_applies_folder_name_max_length(self, extractor:extract_module.AdExtractor) -> None:
        extractor.config.download.folder_name_max_length = 24
        folder_name = extractor._render_download_folder_name(12345, "title " * 20)

        assert len(folder_name) <= 24
        assert "12345" in folder_name

    def test_render_download_name_with_budget_handles_literal_only_template(self, extractor:extract_module.AdExtractor) -> None:
        rendered = extractor._render_download_name_with_budget("prefix", 12345, "Any Title", 6)

        assert rendered == "prefix"

    def test_render_download_name_with_budget_handles_zero_title_budget(self, extractor:extract_module.AdExtractor) -> None:
        rendered = extractor._render_download_name_with_budget("{id}_{title}", 12345, "Any Title", 5)

        assert rendered == "12345"

    def test_render_download_name_with_budget_handles_title_before_id(self, extractor:extract_module.AdExtractor) -> None:
        rendered = extractor._render_download_name_with_budget("{title}_{id}", 12345, "Any Title", 20)

        assert "12345" in rendered
        assert rendered.endswith("_12345")
        assert len(rendered) <= 20


class TestRenderDownloadNameWithBudgetWarnings:
    """Tests for truncation warnings in download name rendering."""

    def test_truncate_log_snippet_returns_value_when_within_limit(self) -> None:
        """Values within max_length are returned unchanged."""
        result = extract_module.AdExtractor._truncate_log_snippet("short", max_length = 10)

        assert result == "short"

    def test_truncate_log_snippet_truncates_and_respects_limit(self) -> None:
        """Values over max_length are truncated and final length stays within the cap."""
        result = extract_module.AdExtractor._truncate_log_snippet("x" * 150, max_length = 20)

        assert result == ("x" * 17) + "..."
        assert len(result) == 20

    def test_truncate_log_snippet_handles_small_limits(self) -> None:
        """Small limits return a shortened ellipsis-only preview."""
        result = extract_module.AdExtractor._truncate_log_snippet("abcdef", max_length = 2)

        assert result == ".."

    def test_truncate_log_snippet_returns_empty_for_zero_max_length(self) -> None:
        """Zero max_length returns empty string."""
        result = extract_module.AdExtractor._truncate_log_snippet("any value", max_length = 0)

        assert not result

    def test_truncate_log_snippet_returns_empty_for_negative_max_length(self) -> None:
        """Negative max_length returns empty string."""
        result = extract_module.AdExtractor._truncate_log_snippet("any value", max_length = -1)

        assert not result

    def test_truncate_log_snippet_returns_exact_ellipsis_at_limit_three(self) -> None:
        """When max_length equals ellipsis length, return exact ellipsis."""
        result = extract_module.AdExtractor._truncate_log_snippet("long value here", max_length = 3)

        assert result == "..."

    def test_render_download_name_with_title_only_template(self, test_extractor:extract_module.AdExtractor) -> None:
        """Template with only {title} placeholder (no {id}) renders correctly."""
        rendered = test_extractor._render_download_name_with_budget("{title}", 12345, "My Item Title", 50)

        assert rendered == "My Item Title"
        assert "12345" not in rendered

    def test_render_download_name_with_title_only_truncated(self, test_extractor:extract_module.AdExtractor) -> None:
        """Template with only {title} truncates when budget is tight."""
        rendered = test_extractor._render_download_name_with_budget("{title}", 12345, "Very Long Title Here", 10)

        assert len(rendered) <= 10
        # Title is truncated to fit budget via sanitize_folder_name
        assert rendered == "Very Long"

    def test_render_download_name_ignores_unknown_placeholder(self, test_extractor:extract_module.AdExtractor) -> None:
        """Unknown placeholders are ignored (defensive fallback)."""
        rendered = test_extractor._render_download_name_with_budget("prefix_{unknown}_suffix", 12345, "Title", 50)

        # Unknown placeholder is skipped, only literals remain
        assert rendered == "prefix__suffix"

    def test_no_warning_when_everything_fits(self, test_extractor:extract_module.AdExtractor, caplog:pytest.LogCaptureFixture) -> None:
        """No warning when all placeholders fit within budget."""
        with caplog.at_level("WARNING"):
            rendered = test_extractor._render_download_name_with_budget("{id}_{title}", 12345, "Short", 50)

        assert "12345" in rendered
        assert "Short" in rendered
        assert "truncated" not in caplog.text.lower()

    def test_warns_when_id_truncated(self, test_extractor:extract_module.AdExtractor, caplog:pytest.LogCaptureFixture) -> None:
        """Warning emitted when {id} is truncated."""
        with caplog.at_level("WARNING"):
            rendered = test_extractor._render_download_name_with_budget("{id}", 12345678901234567890, "", 5)

        assert rendered == "12345"
        assert "12345678901234567890" not in rendered
        assert len(rendered) <= 5
        assert "truncated {id}" in caplog.text

    def test_warns_when_title_truncated(self, test_extractor:extract_module.AdExtractor, caplog:pytest.LogCaptureFixture) -> None:
        """Warning emitted when {title} is truncated."""
        with caplog.at_level("WARNING"):
            rendered = test_extractor._render_download_name_with_budget("{id}_{title}", 12345, "Very Long Title Here", 12)

        assert "12345" in rendered
        assert "truncated {title}" in caplog.text

    def test_id_protected_over_literals(self, test_extractor:extract_module.AdExtractor) -> None:
        """{id} is protected over literal text with tight budget."""
        rendered = test_extractor._render_download_name_with_budget("LONGPREFIX_{id}", 12345, "", 10)

        # Exact output: "LONGPREFIX_" (12 chars) truncated to 5, id=12345 (5 chars) preserved
        # Priority: {id} > literals → result = "LONGP" + "12345" = 10 chars
        assert rendered == "LONGP12345"
        assert rendered.endswith("12345")
        assert "12345" in rendered
        assert len(rendered) <= 10

    def test_title_truncated_to_reserve_id_space(self, test_extractor:extract_module.AdExtractor) -> None:
        """{title} is truncated to reserve space for {id} and literals."""
        rendered = test_extractor._render_download_name_with_budget("{title}_{id}", 12345, "Very Long Title", 18)

        assert rendered.endswith("_12345")
        assert "12345" in rendered
        assert "Very Long Title" not in rendered
        assert len(rendered) <= 18

    def test_title_before_id_with_tight_budget_preserves_full_id(self, test_extractor:extract_module.AdExtractor) -> None:
        """When budget is tight, {title} is truncated before {id} and separators."""
        rendered = test_extractor._render_download_name_with_budget("{title}_{id}", 12345, "Any Title", 14)

        assert rendered.endswith("_12345")
        assert "12345" in rendered
        assert len(rendered) <= 14

    def test_literals_preserved_before_title(self, test_extractor:extract_module.AdExtractor) -> None:
        """Literal text is preserved before {title} under budget pressure."""
        # Budget calculation: PREFIX_ (7) + 12345 (5) + _ (1) = 13 chars reserved for id+literals
        # Remaining budget for title: 15 - 13 = 2 chars → "Hello" truncated to "He"
        rendered = test_extractor._render_download_name_with_budget("PREFIX_{id}_{title}", 12345, "Hello", 15)

        assert rendered.startswith("PREFIX_12345_")
        assert rendered == "PREFIX_12345_He"
        assert len(rendered) <= 15

    def test_suffix_literal_preserved_with_tight_budget(self, test_extractor:extract_module.AdExtractor) -> None:
        """Suffix literal survives when {title} truncates first under tight budget."""
        # Priority: {id} > literals > {title}
        # Budget 18: id=12345 (5) + _ (1) + _SUFFIX (7) = 13 chars for id+suffix literals
        # Remaining for title: 18 - 13 = 5 chars → "Any Title" truncated to "Any T"
        rendered = test_extractor._render_download_name_with_budget("{id}_{title}_SUFFIX", 12345, "Any Title", 18)

        assert "12345" in rendered
        assert rendered.endswith("_SUFFIX")
        assert len(rendered) <= 18

    def test_title_truncates_before_suffix_and_id(self, test_extractor:extract_module.AdExtractor) -> None:
        """When {title} precedes suffix literal and {id}, title truncates to preserve both."""
        # Priority: {id} > literals > {title}
        # Budget 20: id=12345 (5) + _SUFFIX_ (8) = 13 chars for id+suffix literals
        # Remaining for title: 20 - 13 = 7 chars → "Very Long Title" truncated to "Very Lo"
        rendered = test_extractor._render_download_name_with_budget("{title}_SUFFIX_{id}", 12345, "Very Long Title", 20)

        assert "12345" in rendered
        assert "_SUFFIX_" in rendered
        assert rendered.endswith("_12345")
        assert len(rendered) <= 20

    def test_warns_when_both_id_and_title_truncated(self, test_extractor:extract_module.AdExtractor, caplog:pytest.LogCaptureFixture) -> None:
        """Warnings are emitted when both placeholders are truncated."""
        with caplog.at_level("WARNING"):
            rendered = test_extractor._render_download_name_with_budget("{title}_{id}", 12345678901234567890, "Very Long Title Here", 15)

        assert len(rendered) <= 15
        assert "truncated {id}" in caplog.text
        assert "truncated {title}" in caplog.text
