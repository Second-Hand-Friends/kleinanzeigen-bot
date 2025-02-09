"""
SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""
import json, os
from typing import Any, TypedDict
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from kleinanzeigen_bot.extract import AdExtractor
from kleinanzeigen_bot.web_scraping_mixin import Browser, By, Element


class _DimensionsDict(TypedDict):
    dimension108: str


class _UniversalAnalyticsOptsDict(TypedDict):
    dimensions: _DimensionsDict


class _BelenConfDict(TypedDict):
    universalAnalyticsOpts: _UniversalAnalyticsOptsDict


class _SpecialAttributesDict(TypedDict, total=False):
    art_s: str
    condition_s: str


class _TestCaseDict(TypedDict):
    belen_conf: _BelenConfDict
    expected: _SpecialAttributesDict


class TestAdExtractorBasics:
    """Basic synchronous tests for AdExtractor."""

    def test_constructor(self, browser_mock: MagicMock, sample_config: dict[str, Any]) -> None:
        """Test the constructor of AdExtractor"""
        extractor = AdExtractor(browser_mock, sample_config)
        assert extractor.browser == browser_mock
        assert extractor.config == sample_config

    @pytest.mark.parametrize(
        "url,expected_id",
        [
            ("https://www.kleinanzeigen.de/s-anzeige/test-title/12345678", 12345678),
            ("https://www.kleinanzeigen.de/s-anzeige/another-test/98765432", 98765432),
            ("https://www.kleinanzeigen.de/s-anzeige/invalid-id/abc", -1),
            ("https://www.kleinanzeigen.de/invalid-url", -1),
        ],
    )
    def test_extract_ad_id_from_ad_url(self, test_extractor: AdExtractor, url: str, expected_id: int) -> None:
        """Test extraction of ad ID from different URL formats."""
        assert test_extractor.extract_ad_id_from_ad_url(url) == expected_id


class TestAdExtractorPricing:
    """Tests for pricing related functionality."""

    @pytest.mark.parametrize(
        "price_text,expected_price,expected_type",
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
        self, test_extractor: AdExtractor, price_text: str, expected_price: int | None, expected_type: str
    ) -> None:
        """Test price extraction with different formats"""
        with patch.object(test_extractor, 'web_text', new_callable=AsyncMock, return_value=price_text):
            price, price_type = await test_extractor._extract_pricing_info_from_ad_page()
            assert price == expected_price
            assert price_type == expected_type

    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_extract_pricing_info_timeout(self, test_extractor: AdExtractor) -> None:
        """Test price extraction when element is not found"""
        with patch.object(test_extractor, 'web_text', new_callable=AsyncMock, side_effect=TimeoutError):
            price, price_type = await test_extractor._extract_pricing_info_from_ad_page()
            assert price is None
            assert price_type == "NOT_APPLICABLE"


class TestAdExtractorShipping:
    """Tests for shipping related functionality."""

    @pytest.mark.parametrize(
        "shipping_text,expected_type,expected_cost",
        [
            ("+ Versand ab 2,99 €", "SHIPPING", 2.99),
            ("Nur Abholung", "PICKUP", None),
            ("Versand möglich", "SHIPPING", None),
        ],
    )
    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_extract_shipping_info(
        self, test_extractor: AdExtractor, shipping_text: str, expected_type: str, expected_cost: float | None
    ) -> None:
        """Test shipping info extraction with different text formats."""
        with patch.object(test_extractor, 'page', MagicMock()), \
                patch.object(test_extractor, 'web_text', new_callable=AsyncMock, return_value=shipping_text), \
                patch.object(test_extractor, 'web_request', new_callable=AsyncMock) as mock_web_request:

            if expected_cost:
                shipping_response: dict[str, Any] = {
                    "data": {
                        "shippingOptionsResponse": {
                            "options": [
                                {"id": "DHL_001", "priceInEuroCent": int(expected_cost * 100)}
                            ]
                        }
                    }
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
    async def test_extract_shipping_info_with_options(self, test_extractor: AdExtractor) -> None:
        """Test shipping info extraction with shipping options."""
        shipping_response = {
            "content": json.dumps({
                "data": {
                    "shippingOptionsResponse": {
                        "options": [
                            {"id": "DHL_001", "priceInEuroCent": 549}
                        ]
                    }
                }
            })
        }

        with patch.object(test_extractor, 'page', MagicMock()), \
                patch.object(test_extractor, 'web_text', new_callable=AsyncMock, return_value="+ Versand ab 5,49 €"), \
                patch.object(test_extractor, 'web_request', new_callable=AsyncMock, return_value=shipping_response):

            shipping_type, costs, options = await test_extractor._extract_shipping_info_from_ad_page()

            assert shipping_type == "SHIPPING"
            assert costs == 5.49
            assert options == ["DHL_2"]


class TestAdExtractorNavigation:
    """Tests for navigation related functionality."""

    @pytest.mark.asyncio
    async def test_navigate_to_ad_page_with_url(self, test_extractor: AdExtractor) -> None:
        """Test navigation to ad page using a URL."""
        page_mock = AsyncMock()
        page_mock.url = "https://www.kleinanzeigen.de/s-anzeige/test/12345"

        with patch.object(test_extractor, 'page', page_mock), \
                patch.object(test_extractor, 'web_open', new_callable=AsyncMock) as mock_web_open, \
                patch.object(test_extractor, 'web_find', new_callable=AsyncMock, side_effect=TimeoutError):

            result = await test_extractor.naviagte_to_ad_page("https://www.kleinanzeigen.de/s-anzeige/test/12345")
            assert result is True
            mock_web_open.assert_called_with("https://www.kleinanzeigen.de/s-anzeige/test/12345")

    @pytest.mark.asyncio
    async def test_navigate_to_ad_page_with_id(self, test_extractor: AdExtractor) -> None:
        """Test navigation to ad page using an ID."""
        page_mock = AsyncMock()
        page_mock.url = "https://www.kleinanzeigen.de/s-anzeige/test/12345"

        submit_button_mock = AsyncMock()
        submit_button_mock.click = AsyncMock()
        submit_button_mock.apply = AsyncMock(return_value=True)

        input_mock = AsyncMock()
        input_mock.clear_input = AsyncMock()
        input_mock.send_keys = AsyncMock()
        input_mock.apply = AsyncMock(return_value=True)

        popup_close_mock = AsyncMock()
        popup_close_mock.click = AsyncMock()
        popup_close_mock.apply = AsyncMock(return_value=True)

        def find_mock(selector_type: By, selector_value: str, **_: Any) -> Element | None:
            if selector_type == By.ID and selector_value == "site-search-query":
                return input_mock
            if selector_type == By.ID and selector_value == "site-search-submit":
                return submit_button_mock
            if selector_type == By.CLASS_NAME and selector_value == "mfp-close":
                return popup_close_mock
            return None

        with patch.object(test_extractor, 'page', page_mock), \
                patch.object(test_extractor, 'web_open', new_callable=AsyncMock) as mock_web_open, \
                patch.object(test_extractor, 'web_input', new_callable=AsyncMock), \
                patch.object(test_extractor, 'web_check', new_callable=AsyncMock, return_value=True), \
                patch.object(test_extractor, 'web_find', new_callable=AsyncMock, side_effect=find_mock):

            result = await test_extractor.naviagte_to_ad_page(12345)
            assert result is True
            mock_web_open.assert_called_with('https://www.kleinanzeigen.de/')
            submit_button_mock.click.assert_awaited_once()
            popup_close_mock.click.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_navigate_to_ad_page_with_popup(self, test_extractor: AdExtractor) -> None:
        """Test navigation to ad page with popup handling."""
        page_mock = AsyncMock()
        page_mock.url = "https://www.kleinanzeigen.de/s-anzeige/test/12345"

        input_mock = AsyncMock()
        input_mock.clear_input = AsyncMock()
        input_mock.send_keys = AsyncMock()
        input_mock.apply = AsyncMock(return_value=True)

        with patch.object(test_extractor, 'page', page_mock), \
                patch.object(test_extractor, 'web_open', new_callable=AsyncMock), \
                patch.object(test_extractor, 'web_find', new_callable=AsyncMock, return_value=input_mock), \
                patch.object(test_extractor, 'web_click', new_callable=AsyncMock) as mock_web_click, \
                patch.object(test_extractor, 'web_check', new_callable=AsyncMock, return_value=True):

            result = await test_extractor.naviagte_to_ad_page(12345)
            assert result is True
            mock_web_click.assert_called_with(By.CLASS_NAME, 'mfp-close')

    @pytest.mark.asyncio
    async def test_navigate_to_ad_page_invalid_id(self, test_extractor: AdExtractor) -> None:
        """Test navigation to ad page with invalid ID."""
        page_mock = AsyncMock()
        page_mock.url = "https://www.kleinanzeigen.de/s-suchen.html?k0"

        input_mock = AsyncMock()
        input_mock.clear_input = AsyncMock()
        input_mock.send_keys = AsyncMock()
        input_mock.apply = AsyncMock(return_value=True)
        input_mock.attrs = {}

        with patch.object(test_extractor, 'page', page_mock), \
                patch.object(test_extractor, 'web_open', new_callable=AsyncMock), \
                patch.object(test_extractor, 'web_find', new_callable=AsyncMock, return_value=input_mock):

            result = await test_extractor.naviagte_to_ad_page(99999)
            assert result is False

    @pytest.mark.asyncio
    async def test_extract_own_ads_urls(self, test_extractor: AdExtractor) -> None:
        """Test extraction of own ads URLs - basic test."""
        with patch.object(test_extractor, 'web_open', new_callable=AsyncMock), \
                patch.object(test_extractor, 'web_sleep', new_callable=AsyncMock), \
                patch.object(test_extractor, 'web_find', new_callable=AsyncMock) as mock_web_find, \
                patch.object(test_extractor, 'web_find_all', new_callable=AsyncMock) as mock_web_find_all, \
                patch.object(test_extractor, 'web_scroll_page_down', new_callable=AsyncMock), \
                patch.object(test_extractor, 'web_execute', new_callable=AsyncMock):

            # Setup mock objects for DOM elements
            splitpage = MagicMock()
            pagination_section = MagicMock()
            pagination = MagicMock()
            pagination_div = MagicMock()
            ad_list = MagicMock()
            cardbox = MagicMock()
            link = MagicMock()
            link.attrs = {'href': '/s-anzeige/test/12345'}

            # Setup mock responses for web_find
            mock_web_find.side_effect = [
                splitpage,                # .l-splitpage
                pagination_section,       # section:nth-of-type(4)
                pagination,              # div > div:nth-of-type(2) > div:nth-of-type(2) > div
                pagination_div,          # div:nth-of-type(1)
                ad_list,                 # my-manageitems-adlist
                link                     # article > section > section:nth-of-type(2) > h2 > div > a
            ]

            # Setup mock responses for web_find_all
            mock_web_find_all.side_effect = [
                [MagicMock()],           # buttons in pagination
                [cardbox]                # cardbox elements
            ]

            # Execute test and verify results
            refs = await test_extractor.extract_own_ads_urls()
            assert refs == ['/s-anzeige/test/12345']


class TestAdExtractorContent:
    """Tests for content extraction functionality."""

    @pytest.fixture
    def extractor(self) -> AdExtractor:
        browser_mock = MagicMock(spec=Browser)
        config_mock = {
            "ad_defaults": {
                "description": {
                    "prefix": "Test Prefix",
                    "suffix": "Test Suffix"
                }
            }
        }
        return AdExtractor(browser_mock, config_mock)

    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_extract_title_and_description(self, extractor: AdExtractor) -> None:
        """Test basic extraction of title and description."""
        page_mock = AsyncMock()
        page_mock.url = "https://www.kleinanzeigen.de/s-anzeige/test/12345"

        category_mock = AsyncMock()
        category_mock.attrs = {'href': '/s-kategorie/c123'}

        with patch.object(extractor, 'page', page_mock), \
                patch.object(extractor, 'web_text', new_callable=AsyncMock) as mock_web_text, \
                patch.object(extractor, 'web_find', new_callable=AsyncMock, return_value=category_mock), \
                patch.object(extractor, '_extract_category_from_ad_page', new_callable=AsyncMock, return_value="17/23"), \
                patch.object(extractor, '_extract_special_attributes_from_ad_page', new_callable=AsyncMock, return_value={}), \
                patch.object(extractor, '_extract_pricing_info_from_ad_page', new_callable=AsyncMock, return_value=(None, "NOT_APPLICABLE")), \
                patch.object(extractor, '_extract_shipping_info_from_ad_page', new_callable=AsyncMock, return_value=("NOT_APPLICABLE", None, None)), \
                patch.object(extractor, '_extract_sell_directly_from_ad_page', new_callable=AsyncMock, return_value=False), \
                patch.object(extractor, '_download_images_from_ad_page', new_callable=AsyncMock, return_value=[]), \
                patch.object(extractor, '_extract_contact_from_ad_page', new_callable=AsyncMock, return_value={}):

            mock_web_text.side_effect = [
                "Test Title",
                "Test Prefix Original Description Test Suffix",
                "03.02.2025"
            ]

            info = await extractor._extract_ad_page_info("/some/dir", 12345)
            assert isinstance(info, dict)
            assert info["title"] == "Test Title"
            assert info["description"].strip() == "Original Description"
            assert info["created_on"] == "2025-02-03T00:00:00"

    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_extract_sell_directly(self, extractor: AdExtractor) -> None:
        """Test extraction of sell directly option."""
        test_cases = [
            ("Direkt kaufen", True),
            ("Other text", False),
        ]

        for text, expected in test_cases:
            with patch.object(extractor, 'web_text', new_callable=AsyncMock, return_value=text):
                result = await extractor._extract_sell_directly_from_ad_page()
                assert result is expected

        with patch.object(extractor, 'web_text', new_callable=AsyncMock, side_effect=TimeoutError):
            result = await extractor._extract_sell_directly_from_ad_page()
            assert result is None


class TestAdExtractorCategory:
    """Tests for category extraction functionality."""

    @pytest.fixture
    def extractor(self) -> AdExtractor:
        browser_mock = MagicMock(spec=Browser)
        config_mock = {
            "ad_defaults": {
                "description": {
                    "prefix": "Test Prefix",
                    "suffix": "Test Suffix"
                }
            }
        }
        return AdExtractor(browser_mock, config_mock)

    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_extract_category(self, extractor: AdExtractor) -> None:
        """Test category extraction from breadcrumb."""
        category_line = MagicMock()
        first_part = MagicMock()
        first_part.attrs = {'href': '/s-familie-kind-baby/c17'}
        second_part = MagicMock()
        second_part.attrs = {'href': '/s-spielzeug/c23'}

        with patch.object(extractor, 'web_find', new_callable=AsyncMock) as mock_web_find:
            mock_web_find.side_effect = [
                category_line,
                first_part,
                second_part
            ]

            result = await extractor._extract_category_from_ad_page()
            assert result == "17/23"

            mock_web_find.assert_any_call(By.ID, 'vap-brdcrmb')
            mock_web_find.assert_any_call(By.CSS_SELECTOR, 'a:nth-of-type(2)', parent=category_line)
            mock_web_find.assert_any_call(By.CSS_SELECTOR, 'a:nth-of-type(3)', parent=category_line)

    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_extract_special_attributes_empty(self, extractor: AdExtractor) -> None:
        """Test extraction of special attributes when empty."""
        with patch.object(extractor, 'web_execute', new_callable=AsyncMock) as mock_web_execute:
            mock_web_execute.return_value = {
                "universalAnalyticsOpts": {
                    "dimensions": {
                        "dimension108": ""
                    }
                }
            }
            result = await extractor._extract_special_attributes_from_ad_page()
            assert result == {}


class TestAdExtractorContact:
    """Tests for contact information extraction."""

    @pytest.fixture
    def extractor(self) -> AdExtractor:
        browser_mock = MagicMock(spec=Browser)
        config_mock = {
            "ad_defaults": {
                "description": {
                    "prefix": "Test Prefix",
                    "suffix": "Test Suffix"
                }
            }
        }
        return AdExtractor(browser_mock, config_mock)

    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_extract_contact_info(self, extractor: AdExtractor) -> None:
        """Test extraction of contact information."""
        with patch.object(extractor, 'page', MagicMock()), \
                patch.object(extractor, 'web_text', new_callable=AsyncMock) as mock_web_text, \
                patch.object(extractor, 'web_find', new_callable=AsyncMock) as mock_web_find:

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
            assert isinstance(contact_info, dict)
            assert contact_info["street"] == "Example Street 123"
            assert contact_info["zipcode"] == "12345"
            assert contact_info["location"] == "Berlin - Mitte"
            assert contact_info["name"] == "Test User"
            assert contact_info["phone"] is None

    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_extract_contact_info_timeout(self, extractor: AdExtractor) -> None:
        """Test contact info extraction when elements are not found."""
        with patch.object(extractor, 'page', MagicMock()), \
                patch.object(extractor, 'web_text', new_callable=AsyncMock, side_effect=TimeoutError()), \
                patch.object(extractor, 'web_find', new_callable=AsyncMock, side_effect=TimeoutError()):

            with pytest.raises(TimeoutError):
                await extractor._extract_contact_from_ad_page()

    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_extract_contact_info_with_phone(self, extractor: AdExtractor) -> None:
        """Test extraction of contact information including phone number."""
        with patch.object(extractor, 'page', MagicMock()), \
                patch.object(extractor, 'web_text', new_callable=AsyncMock) as mock_web_text, \
                patch.object(extractor, 'web_find', new_callable=AsyncMock) as mock_web_find:

            mock_web_text.side_effect = [
                "12345 Berlin - Mitte",
                "Example Street 123,",
                "Test User",
                "+49(0)1234 567890"
            ]

            phone_element = MagicMock()
            mock_web_find.side_effect = [
                MagicMock(),  # contact person element
                MagicMock(),  # name element
                phone_element,  # phone element
            ]

            contact_info = await extractor._extract_contact_from_ad_page()
            assert isinstance(contact_info, dict)
            assert contact_info["phone"] == "01234567890"  # Normalized phone number


class TestAdExtractorDownload:
    """Tests for download functionality."""

    @pytest.fixture
    def extractor(self) -> AdExtractor:
        browser_mock = MagicMock(spec=Browser)
        config_mock = {
            "ad_defaults": {
                "description": {
                    "prefix": "Test Prefix",
                    "suffix": "Test Suffix"
                }
            }
        }
        return AdExtractor(browser_mock, config_mock)

    @pytest.mark.asyncio
    async def test_download_ad_existing_directory(self, extractor: AdExtractor) -> None:
        """Test downloading an ad when the directory already exists."""
        with patch('os.path.exists') as mock_exists, \
                patch('os.path.isdir') as mock_isdir, \
                patch('os.makedirs') as mock_makedirs, \
                patch('os.mkdir') as mock_mkdir, \
                patch('shutil.rmtree') as mock_rmtree, \
                patch('kleinanzeigen_bot.extract.save_dict', autospec=True) as mock_save_dict, \
                patch.object(extractor, '_extract_ad_page_info', new_callable=AsyncMock) as mock_extract:

            base_dir = 'downloaded-ads'
            ad_dir = os.path.join(base_dir, 'ad_12345')
            yaml_path = os.path.join(ad_dir, 'ad_12345.yaml')

            # Configure mocks for directory checks
            existing_paths = {base_dir, ad_dir}
            mock_exists.side_effect = lambda path: path in existing_paths
            mock_isdir.side_effect = lambda path: path == base_dir

            mock_extract.return_value = {
                "title": "Test Advertisement Title",
                "description": "Test Description",
                "price": 100,
                "images": [],
                "contact": {
                    "name": "Test User",
                    "street": "Test Street 123",
                    "zipcode": "12345",
                    "location": "Test City"
                }
            }

            await extractor.download_ad(12345)

            # Verify the correct functions were called
            mock_extract.assert_called_once()
            mock_rmtree.assert_called_once_with(ad_dir)
            mock_mkdir.assert_called_once_with(ad_dir)
            mock_makedirs.assert_not_called()  # Directory already exists

            # Get the actual call arguments
            # Workaround for hard-coded path in download_ad
            actual_call = mock_save_dict.call_args
            assert actual_call is not None
            actual_path = actual_call[0][0].replace('/', os.path.sep)
            assert actual_path == yaml_path
            assert actual_call[0][1] == mock_extract.return_value

    @pytest.mark.asyncio
    # pylint: disable=protected-access
    async def test_download_images_no_images(self, extractor: AdExtractor) -> None:
        """Test image download when no images are found."""
        with patch.object(extractor, 'web_find', new_callable=AsyncMock, side_effect=TimeoutError):
            image_paths = await extractor._download_images_from_ad_page("/some/dir", 12345)
            assert len(image_paths) == 0

    @pytest.mark.asyncio
    async def test_download_ad(self, extractor: AdExtractor) -> None:
        """Test downloading an entire ad."""
        with patch('os.path.exists') as mock_exists, \
                patch('os.path.isdir') as mock_isdir, \
                patch('os.makedirs') as mock_makedirs, \
                patch('os.mkdir') as mock_mkdir, \
                patch('shutil.rmtree') as mock_rmtree, \
                patch('kleinanzeigen_bot.extract.save_dict', autospec=True) as mock_save_dict, \
                patch.object(extractor, '_extract_ad_page_info', new_callable=AsyncMock) as mock_extract:

            base_dir = 'downloaded-ads'
            ad_dir = os.path.join(base_dir, 'ad_12345')
            yaml_path = os.path.join(ad_dir, 'ad_12345.yaml')

            # Configure mocks for directory checks
            mock_exists.return_value = False
            mock_isdir.return_value = False

            mock_extract.return_value = {
                "title": "Test Advertisement Title",
                "description": "Test Description",
                "price": 100,
                "images": [],
                "contact": {
                    "name": "Test User",
                    "street": "Test Street 123",
                    "zipcode": "12345",
                    "location": "Test City"
                }
            }

            await extractor.download_ad(12345)

            # Verify the correct functions were called
            mock_extract.assert_called_once()
            mock_rmtree.assert_not_called()  # No directory to remove
            mock_mkdir.assert_has_calls([
                call(base_dir),
                call(ad_dir)
            ])
            mock_makedirs.assert_not_called()  # Using mkdir instead

            # Get the actual call arguments
            actual_call = mock_save_dict.call_args
            assert actual_call is not None
            actual_path = actual_call[0][0].replace('/', os.path.sep)
            assert actual_path == yaml_path
            assert actual_call[0][1] == mock_extract.return_value
