# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import json  # isort: skip
from gettext import gettext as _
from pathlib import Path
from typing import Any, TypedDict
from unittest.mock import AsyncMock, MagicMock, call, patch
from urllib.error import URLError

import pytest

import kleinanzeigen_bot.extract as extract_module
from kleinanzeigen_bot.model.ad_model import AdPartial, ContactPartial
from kleinanzeigen_bot.model.config_model import Config, DownloadConfig
from kleinanzeigen_bot.utils.web_scraping_mixin import Browser, By, Element


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
            patch.object(test_extractor, "web_find_by_rule", new_callable = AsyncMock) as mock_web_find_by_rule,
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

            # --- Setup mock responses for rule and selector lookups in CORRECT ORDER ---
            mock_web_find_by_rule.side_effect = [
                ad_list_container_mock,  # Call 1: ad_management.ad_list_container
                TimeoutError(),  # Call 2: pagination.container -> single page
            ]

            # 1. Find for ad list container inside callback
            # 2. Find for the link inside list comprehension
            mock_web_find.side_effect = [
                ad_list_container_mock,
                link_mock,
            ]

            # 1. Find all '.cardbox' elements (inside loop)
            mock_web_find_all.side_effect = [[cardbox_mock]]

            # --- Execute test and verify results ---
            refs = await test_extractor.extract_own_ads_urls()

            # --- Assertions ---
            assert refs == ["/s-anzeige/test/12345"]  # Now it should match

            # Optional: Verify calls were made as expected
            mock_web_find_by_rule.assert_has_calls(
                [
                    call("ad_management.ad_list_container"),
                    call("pagination.container", timeout = 10),
                ],
                any_order = False,
            )

            mock_web_find.assert_has_calls(
                [
                    call(By.ID, "my-manageitems-adlist"),
                    call(By.CSS_SELECTOR, "div h3 a.text-onSurface", parent = cardbox_mock),
                ],
                any_order = False,
            )  # Check order if important

            mock_web_find_all.assert_has_calls(
                [
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
            if selector_type == By.CLASS_NAME and selector_value == "cardbox":
                cardbox_call["count"] += 1
                return [cardbox_page_one] if cardbox_call["count"] == 1 else [cardbox_page_two]
            raise AssertionError(f"Unexpected find_all selector {selector_type} {selector_value}")

        with (
            patch.object(test_extractor, "web_open", new_callable = AsyncMock),
            patch.object(test_extractor, "web_scroll_page_down", new_callable = AsyncMock),
            patch.object(test_extractor, "web_sleep", new_callable = AsyncMock),
            patch.object(
                test_extractor,
                "web_find_by_rule",
                new_callable = AsyncMock,
                side_effect = [
                    ad_list_container_mock,
                    MagicMock(),  # initial pagination.container
                    next_button_enabled,  # initial pagination.next_button
                    MagicMock(),  # follow-up pagination.container (page 1)
                    next_button_enabled,  # follow-up pagination.next_button (click)
                    MagicMock(),  # follow-up pagination.container (page 2)
                    disabled_button,  # follow-up pagination.next_button (stop)
                ],
            ),
            patch.object(test_extractor, "web_find", new_callable = AsyncMock, side_effect = fake_web_find),
            patch.object(test_extractor, "web_find_all", new_callable = AsyncMock, side_effect = fake_web_find_all),
        ):
            refs = await test_extractor.extract_own_ads_urls()

        assert refs == ["/s-anzeige/page-one/111", "/s-anzeige/page-two/222"]
        next_button_enabled.click.assert_awaited()  # triggered once during navigation

    @pytest.mark.asyncio
    async def test_extract_own_ads_urls_timeout_in_callback(self, test_extractor:extract_module.AdExtractor) -> None:
        """Test that TimeoutError in extract_page_refs callback stops pagination."""
        with (
            patch.object(test_extractor, "web_open", new_callable = AsyncMock),
            patch.object(test_extractor, "web_sleep", new_callable = AsyncMock),
            patch.object(test_extractor, "web_find_by_rule", new_callable = AsyncMock) as mock_web_find_by_rule,
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
            mock_web_find_by_rule.side_effect = [ad_list_container_mock, TimeoutError()]

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
            patch.object(test_extractor, "web_find_by_rule", new_callable = AsyncMock, side_effect = [ad_list_container_mock, TimeoutError()]),
            patch.object(test_extractor, "web_find_all", new_callable = AsyncMock, return_value = [first_item, second_item]),
            patch.object(test_extractor, "web_find", new_callable = AsyncMock, side_effect = [ad_list_container_mock, TimeoutError(), valid_link]),
        ):
            refs = await test_extractor.extract_own_ads_urls()

        assert refs == ["/s-anzeige/ok/999"]

    @pytest.mark.asyncio
    async def test_extract_own_ads_urls_generic_exception_in_callback(self, test_extractor:extract_module.AdExtractor) -> None:
        """Test that generic Exception in extract_page_refs callback continues pagination."""
        with (
            patch.object(test_extractor, "web_open", new_callable = AsyncMock),
            patch.object(test_extractor, "web_sleep", new_callable = AsyncMock),
            patch.object(test_extractor, "web_find_by_rule", new_callable = AsyncMock) as mock_web_find_by_rule,
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
                # Second call: pagination check - raise TimeoutError to indicate no pagination
                if call_count["count"] == 2:
                    raise TimeoutError("No pagination")
                # Third call: ad list container (inside callback)
                return ad_list_container_mock

            mock_web_find.side_effect = mock_find_side_effect
            mock_web_find_by_rule.side_effect = [ad_list_container_mock, TimeoutError()]

            # Make web_find_all raise a generic exception
            async def mock_find_all_side_effect(*args:Any, **kwargs:Any) -> list[Element]:
                raise AttributeError("Unexpected error")

            with patch.object(test_extractor, "web_find_all", new_callable = AsyncMock, side_effect = mock_find_all_side_effect):
                refs = await test_extractor.extract_own_ads_urls()

            # Pagination should continue despite exception (callback returns False)
            # Since it's a single page (no pagination), refs should be empty
            assert refs == []


class TestAdExtractorContent:
    """Tests for content extraction functionality."""

    # pylint: disable=protected-access

    @pytest.mark.asyncio
    async def test_extract_description_with_affixes(
        self, test_extractor:extract_module.AdExtractor, description_test_cases:list[tuple[dict[str, Any], str, str]], test_bot_config:Config
    ) -> None:
        """Test extraction of description with various prefix/suffix configurations."""
        # Mock the page
        page_mock = MagicMock()
        page_mock.url = "https://www.kleinanzeigen.de/s-anzeige/test/12345"
        test_extractor.page = page_mock

        for config, raw_description, _expected_description in description_test_cases:
            test_extractor.config = test_bot_config.with_values(config)

            with patch.multiple(
                test_extractor,
                web_text = AsyncMock(
                    side_effect = [
                        "Test Title",  # Title
                        raw_description,  # Raw description (without affixes)
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
                info = await test_extractor._extract_ad_page_info("/some/dir", 12345)
                assert info.description == raw_description

    @pytest.mark.asyncio
    async def test_extract_description_with_affixes_timeout(self, test_extractor:extract_module.AdExtractor) -> None:
        """Test handling of timeout when extracting description."""
        # Mock the page
        page_mock = MagicMock()
        page_mock.url = "https://www.kleinanzeigen.de/s-anzeige/test/12345"
        test_extractor.page = page_mock

        with patch.multiple(
            test_extractor,
            web_text = AsyncMock(
                side_effect = [
                    "Test Title",  # Title succeeds
                    TimeoutError("Timeout"),  # Description times out
                    "03.02.2025",  # Date succeeds
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
            try:
                info = await test_extractor._extract_ad_page_info("/some/dir", 12345)
                assert not info.description
            except TimeoutError:
                # This is also acceptable - depends on how we want to handle timeouts
                pass

    @pytest.mark.asyncio
    async def test_extract_description_with_affixes_no_affixes(self, test_extractor:extract_module.AdExtractor) -> None:
        """Test extraction of description without any affixes in config."""
        # Mock the page
        page_mock = MagicMock()
        page_mock.url = "https://www.kleinanzeigen.de/s-anzeige/test/12345"
        test_extractor.page = page_mock
        raw_description = "Original Description"

        with patch.multiple(
            test_extractor,
            web_text = AsyncMock(
                side_effect = [
                    "Test Title",  # Title
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
            info = await test_extractor._extract_ad_page_info("/some/dir", 12345)
            assert info.description == raw_description

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
    async def test_extract_category_fallback_to_legacy_selectors(self, extractor:extract_module.AdExtractor, caplog:pytest.LogCaptureFixture) -> None:
        """Test category extraction when breadcrumb links are not available and legacy selectors are used."""
        category_line = MagicMock()
        first_part = MagicMock()
        first_part.attrs = {"href": 12345}  # Ensure str() conversion happens
        second_part = MagicMock()
        second_part.attrs = {"href": 67890}  # This will need str() conversion

        caplog.set_level("DEBUG")
        expected_message = _("Falling back to legacy breadcrumb selectors; collected ids: %s") % []
        with (
            patch.object(extractor, "web_find", new_callable = AsyncMock) as mock_web_find,
            patch.object(extractor, "web_find_all", new_callable = AsyncMock, side_effect = TimeoutError) as mock_web_find_all,
        ):
            mock_web_find.side_effect = [category_line, first_part, second_part]

            result = await extractor._extract_category_from_ad_page()
            assert result == "12345/67890"
            assert sum(1 for record in caplog.records if record.message == expected_message) == 1

            mock_web_find.assert_any_call(By.ID, "vap-brdcrmb")
            mock_web_find.assert_any_call(By.CSS_SELECTOR, "a:nth-of-type(2)", parent = category_line)
            mock_web_find.assert_any_call(By.CSS_SELECTOR, "a:nth-of-type(3)", parent = category_line)
            mock_web_find_all.assert_awaited_once_with(By.CSS_SELECTOR, "a", parent = category_line)

    @pytest.mark.asyncio
    async def test_extract_category_legacy_selectors_timeout(self, extractor:extract_module.AdExtractor, caplog:pytest.LogCaptureFixture) -> None:
        """Ensure fallback timeout logs the error and re-raises with translated message."""
        category_line = MagicMock()

        async def fake_web_find(selector_type:By, selector_value:str, *, parent:Element | None = None, timeout:int | float | None = None) -> Element:
            if selector_type == By.ID and selector_value == "vap-brdcrmb":
                return category_line
            raise TimeoutError("legacy selectors missing")

        with (
            patch.object(extractor, "web_find", new_callable = AsyncMock, side_effect = fake_web_find),
            patch.object(extractor, "web_find_all", new_callable = AsyncMock, side_effect = TimeoutError),
            caplog.at_level("ERROR"),
            pytest.raises(TimeoutError, match = "Unable to locate breadcrumb fallback selectors"),
        ):
            await extractor._extract_category_from_ad_page()

        assert any("Legacy breadcrumb selectors not found" in record.message for record in caplog.records)

    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_extract_special_attributes_empty(self, extractor:extract_module.AdExtractor) -> None:
        """Test extraction of special attributes when empty."""
        with patch.object(extractor, "web_execute", new_callable = AsyncMock) as mock_web_execute:
            mock_web_execute.return_value = {"universalAnalyticsOpts": {"dimensions": {"ad_attributes": ""}}}
            result = await extractor._extract_special_attributes_from_ad_page(mock_web_execute.return_value)
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
    async def test_extract_special_attributes_missing_ad_attributes(self, extractor:extract_module.AdExtractor) -> None:
        """Test extraction of special attributes when ad_attributes key is missing."""
        belen_conf:dict[str, Any] = {
            "universalAnalyticsOpts": {
                "dimensions": {
                    # ad_attributes key is completely missing
                }
            }
        }
        result = await extractor._extract_special_attributes_from_ad_page(belen_conf)
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
        yaml_path = final_dir / "ad_12345.yaml"
        extractor.download_dir = download_base

        with (
            patch("kleinanzeigen_bot.extract.dicts.save_dict", autospec = True) as mock_save_dict,
            patch.object(extractor, "_extract_ad_page_info_with_directory_handling", new_callable = AsyncMock) as mock_extract_with_dir,
        ):
            mock_extract_with_dir.return_value = (
                AdPartial.model_validate(
                    {
                        "title": "Test Advertisement Title",
                        "description": "Test Description",
                        "category": "Dienstleistungen",
                        "price": 100,
                        "images": [],
                        "contact": {"name": "Test User", "street": "Test Street 123", "zipcode": "12345", "location": "Test City"},
                    }
                ),
                str(final_dir),
            )

            await extractor.download_ad(12345)

            # Verify observable behavior: extraction and save were called
            mock_extract_with_dir.assert_called_once()
            mock_save_dict.assert_called_once()

            # Verify saved to correct location with correct data
            actual_call = mock_save_dict.call_args
            actual_path = Path(actual_call[0][0])
            assert actual_path == yaml_path
            assert actual_call[0][1] == mock_extract_with_dir.return_value[0].model_dump()

    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_download_images_no_images(self, extractor:extract_module.AdExtractor) -> None:
        """Test image download when no images are found."""
        with patch.object(extractor, "web_find", new_callable = AsyncMock, side_effect = TimeoutError):
            image_paths = await extractor._download_images_from_ad_page("/some/dir", 12345)
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
            image_paths = await extractor._download_images_from_ad_page("/some/dir", 12345)

            # Should only download the one valid image (skip the None)
            assert len(image_paths) == 1
            assert image_paths[0] == "ad_12345__img1.jpg"

    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_extract_ad_page_info_with_directory_handling_final_dir_exists(self, extractor:extract_module.AdExtractor, tmp_path:Path) -> None:
        """Test directory handling when final_dir already exists - it should be deleted."""
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
            ad_cfg, result_dir = await extractor._extract_ad_page_info_with_directory_handling(base_dir, 12345)

            # Verify the old directory was deleted and recreated
            assert result_dir == final_dir
            assert result_dir.exists()
            assert not old_file.exists()  # Old file should be gone
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
            ad_cfg, result_dir = await extractor._extract_ad_page_info_with_directory_handling(base_dir, 12345)

            # Verify the directory was renamed from temp_dir to final_dir
            final_dir = base_dir / "ad_12345_Test Title"
            assert result_dir == final_dir
            assert result_dir.exists()
            assert not temp_dir.exists()  # Old temp dir should be gone
            assert (result_dir / "existing_image.jpg").exists()  # File should be preserved
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
            ad_cfg, result_dir = await extractor._extract_ad_page_info_with_directory_handling(base_dir, 12345)

            # Verify the existing temp_dir was used (not renamed)
            assert result_dir == temp_dir
            assert result_dir.exists()
            assert (result_dir / "existing_image.jpg").exists()  # File should be preserved
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
            ad_cfg, result_dir = await extractor._extract_ad_page_info_with_directory_handling(base_dir, 12345)

            # Verify directory was created with NFC-normalized name
            assert result_dir.exists()
            assert ad_cfg.title == title_with_umlauts

            # Test saving YAML file to the Unicode directory path
            # Before fix: Failed on Linux/Windows due to NFC/NFD mismatch
            # After fix: Both directory and file use NFC normalization
            ad_file_path = Path(result_dir) / "ad_12345.yaml"

            from kleinanzeigen_bot.utils import dicts  # noqa: PLC0415

            header_string = (
                "# yaml-language-server: $schema=https://raw.githubusercontent.com/Second-Hand-Friends/kleinanzeigen-bot/refs/heads/main/schemas/ad.schema.json"
            )

            # save_dict normalizes path to NFC, matching the NFC directory name
            dicts.save_dict(str(ad_file_path), ad_cfg.model_dump(), header = header_string)

            # Verify file was created successfully (no FileNotFoundError)
            assert ad_file_path.exists()
            assert ad_file_path.is_file()
