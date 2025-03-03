"""
SPDX-FileCopyrightText: © Jens Bergmann and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

This module contains tests for image extraction without a browser driver.
"""
import tempfile
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kleinanzeigen_bot.extract import AdExtractor


@pytest.mark.asyncio
async def test_download_images_no_driver() -> None:
    """Test downloading images without a browser driver."""
    browser_mock = MagicMock()
    config_mock: Dict[str, Any] = {}
    extractor = AdExtractor(browser_mock, config_mock)

    with tempfile.TemporaryDirectory() as temp_dir:
        # Mock web_find to raise a TimeoutError to simulate no image area found
        with patch.object(extractor, "web_find", new_callable=AsyncMock) as mock_web_find:
            # Set up the mock to raise a TimeoutError
            mock_web_find.side_effect = TimeoutError("No image area found")

            # The method should handle the TimeoutError and return an empty list
            result = await extractor._download_images_from_ad_page(temp_dir, 12345)

            # Verify the result
            assert result == []
