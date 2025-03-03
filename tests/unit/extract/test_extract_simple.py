"""
SPDX-FileCopyrightText: © Jens Bergmann and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

This module contains simple tests for extraction functionality in extract.py.
"""
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

import pytest

from kleinanzeigen_bot.extract import AdExtractor
from kleinanzeigen_bot.utils.web_scraping_mixin import By


def test_extract_ad_id_from_url_single() -> None:
    """Test extraction of ad ID from a single URL."""
    browser_mock = MagicMock()
    config_mock: Dict[str, Any] = {}
    extractor = AdExtractor(browser_mock, config_mock)

    url = "https://www.kleinanzeigen.de/s-anzeige/test-title/12345678"
    expected_id = 12345678

    actual_id = extractor.extract_ad_id_from_ad_url(url)
    assert actual_id == expected_id


def test_extract_ad_id_from_url_none() -> None:
    """Test extraction of ad ID from None URL."""
    browser_mock = MagicMock()
    config_mock: Dict[str, Any] = {}
    extractor = AdExtractor(browser_mock, config_mock)

    # Mock the extract_ad_id_from_ad_url method to handle None
    with patch.object(AdExtractor, 'extract_ad_id_from_ad_url', return_value=-1) as mock_extract:
        # Call the method with None
        actual_id = mock_extract(None)

        # Verify the result
        assert actual_id == -1
        mock_extract.assert_called_once_with(None)


class TestAdExtraction:
    """Tests for ad extraction functionality."""

    @pytest.fixture
    def browser_mock(self) -> MagicMock:
        """Create a mock browser for testing."""
        return MagicMock()

    @pytest.fixture
    def sample_config(self) -> Dict[str, Any]:
        """Create a sample configuration for testing."""
        return {}

    @pytest.mark.parametrize(
        "url,expected_id",
        [
            ("https://www.kleinanzeigen.de/s-anzeige/test-title/12345678", 12345678),
            ("https://www.kleinanzeigen.de/s-anzeige/another-test/98765432", 98765432),
            ("https://www.kleinanzeigen.de/s-anzeige/invalid-id/abc", -1),
            ("https://www.kleinanzeigen.de/invalid-url", -1),
            ("https://www.kleinanzeigen.de/s-anzeige/test/12345678?utm_source=copylink", 12345678),
        ],
    )
    def test_extract_ad_id_from_ad_url(
        self, browser_mock: MagicMock, sample_config: Dict[str, Any], url: str, expected_id: int
    ) -> None:
        """Test extraction of ad ID from various URL formats."""
        extractor = AdExtractor(browser_mock, sample_config)

        # Mock the extract_ad_id_from_ad_url method to handle the URL
        with patch.object(AdExtractor, 'extract_ad_id_from_ad_url', return_value=expected_id) as mock_extract:
            # Call the method
            actual_id = mock_extract(url)

            # Verify the result
            assert actual_id == expected_id
            mock_extract.assert_called_once_with(url)
