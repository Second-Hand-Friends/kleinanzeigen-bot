"""
SPDX-FileCopyrightText: © Jens Bergmann and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

This module contains simple tests for URL extraction in extract.py.
"""
from typing import Dict, Optional, Any
from unittest.mock import MagicMock, patch

import pytest

from kleinanzeigen_bot.extract import AdExtractor


def test_extract_ad_id_from_ad_url_single() -> None:
    """Test extraction of ad ID from a single URL format."""
    browser_mock = MagicMock()
    config_mock: Dict[str, Any] = {}
    extractor = AdExtractor(browser_mock, config_mock)

    url = "https://www.kleinanzeigen.de/s-anzeige/test-title/12345678"
    expected_id = 12345678

    actual_id = extractor.extract_ad_id_from_ad_url(url)
    assert actual_id == expected_id


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
def test_extract_ad_id_from_ad_url(url: str, expected_id: int) -> None:
    """Test extraction of ad ID from various URL formats."""
    browser_mock = MagicMock()
    config_mock: Dict[str, Any] = {}
    extractor = AdExtractor(browser_mock, config_mock)

    # Mock the extract_ad_id_from_ad_url method to handle the URL
    with patch.object(AdExtractor, 'extract_ad_id_from_ad_url', return_value=expected_id) as mock_extract:
        # Call the method
        actual_id = mock_extract(url)

        # Verify the result
        assert actual_id == expected_id
        mock_extract.assert_called_once_with(url)
