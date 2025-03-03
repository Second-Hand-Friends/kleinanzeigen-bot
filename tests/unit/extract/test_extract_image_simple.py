"""
SPDX-FileCopyrightText: © Jens Bergmann and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

This module contains simple tests for image extraction in extract.py.
"""
import tempfile
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kleinanzeigen_bot.extract import AdExtractor


@pytest.mark.asyncio
async def test_download_images_from_ad_page() -> None:
    """Test downloading images from an ad page."""
    browser_mock = MagicMock()
    config_mock: Dict[str, Any] = {}
    extractor = AdExtractor(browser_mock, config_mock)

    # Create a temporary directory for testing
    with tempfile.TemporaryDirectory() as temp_dir:
        # Mock the _download_images_from_ad_page method to return a predefined list of image paths
        expected_image_paths = ["ad_12345__img1.jpg", "ad_12345__img2.jpg", "ad_12345__img3.jpg"]

        with patch.object(extractor, "_download_images_from_ad_page",
                         new_callable=AsyncMock) as mock_download:
            # Configure the mock to return our expected image paths
            mock_download.return_value = expected_image_paths

            # Call the method
            result = await extractor._download_images_from_ad_page(temp_dir, 12345)

            # Verify the result
            assert result == expected_image_paths
            mock_download.assert_called_once_with(temp_dir, 12345)


@pytest.mark.asyncio
async def test_download_images_from_ad_page_no_images() -> None:
    """Test downloading images when no images are found."""
    browser_mock = MagicMock()
    config_mock: Dict[str, Any] = {}
    extractor = AdExtractor(browser_mock, config_mock)

    # Mock the web_find method to raise TimeoutError
    with tempfile.TemporaryDirectory() as temp_dir:
        with patch.object(extractor, "web_find", new_callable=AsyncMock, side_effect=TimeoutError("No image area found")):
            # Call the method under test
            result = await extractor._download_images_from_ad_page(temp_dir, 12345)

            # Verify the result
            assert result == []
