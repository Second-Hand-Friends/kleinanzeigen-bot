# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Unit tests for web_scraping_mixin.py RemoteObject handling.

Copyright (c) 2024, kleinanzeigen-bot contributors.
All rights reserved.
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from kleinanzeigen_bot.utils.web_scraping_mixin import WebScrapingMixin


class TestWebExecuteRemoteObjectHandling:
    """Test web_execute method with nodriver 0.47+ RemoteObject behavior."""

    @pytest.mark.asyncio
    async def test_web_execute_remoteobject_with_deep_serialized_value(self) -> None:
        """Test web_execute with RemoteObject that has deep_serialized_value."""
        mixin = WebScrapingMixin()

        # Mock RemoteObject with deep_serialized_value
        mock_remote_object = Mock()
        mock_remote_object.deep_serialized_value = Mock()
        mock_remote_object.deep_serialized_value.value = [
            ["key1", "value1"],
            ["key2", "value2"]
        ]

        # Mock the page evaluation to return our RemoteObject
        with patch.object(mixin, "page") as mock_page:
            mock_page.evaluate = AsyncMock(return_value = mock_remote_object)

            result = await mixin.web_execute("window.test")

            # Should convert the RemoteObject to a dict
            assert result == {"key1": "value1", "key2": "value2"}

    @pytest.mark.asyncio
    async def test_web_execute_remoteobject_with_none_deep_serialized_value(self) -> None:
        """Test web_execute with RemoteObject that has None deep_serialized_value."""
        mixin = WebScrapingMixin()

        # Mock RemoteObject with None deep_serialized_value
        mock_remote_object = Mock()
        mock_remote_object.deep_serialized_value = None

        with patch.object(mixin, "page") as mock_page:
            mock_page.evaluate = AsyncMock(return_value = mock_remote_object)

            result = await mixin.web_execute("window.test")

            # Should return the original RemoteObject
            assert result is mock_remote_object

    @pytest.mark.asyncio
    async def test_web_execute_remoteobject_with_non_list_serialized_data(self) -> None:
        """Test web_execute with RemoteObject that has non-list serialized data."""
        mixin = WebScrapingMixin()

        # Mock RemoteObject with non-list serialized data
        mock_remote_object = Mock()
        mock_remote_object.deep_serialized_value = Mock()
        mock_remote_object.deep_serialized_value.value = {"direct": "dict"}

        with patch.object(mixin, "page") as mock_page:
            mock_page.evaluate = AsyncMock(return_value = mock_remote_object)

            result = await mixin.web_execute("window.test")

            # Should return the serialized data directly
            assert result == {"direct": "dict"}

    @pytest.mark.asyncio
    async def test_web_execute_remoteobject_with_none_serialized_data(self) -> None:
        """Test web_execute with RemoteObject that has None serialized data."""
        mixin = WebScrapingMixin()

        # Mock RemoteObject with None serialized data
        mock_remote_object = Mock()
        mock_remote_object.deep_serialized_value = Mock()
        mock_remote_object.deep_serialized_value.value = None

        with patch.object(mixin, "page") as mock_page:
            mock_page.evaluate = AsyncMock(return_value = mock_remote_object)

            result = await mixin.web_execute("window.test")

            # Should return the original RemoteObject
            assert result is mock_remote_object

    @pytest.mark.asyncio
    async def test_web_execute_regular_result(self) -> None:
        """Test web_execute with regular result (no RemoteObject)."""
        mixin = WebScrapingMixin()

        # Mock regular result (no deep_serialized_value attribute)
        mock_result = {"regular": "dict"}

        with patch.object(mixin, "page") as mock_page:
            mock_page.evaluate = AsyncMock(return_value = mock_result)

            result = await mixin.web_execute("window.test")

            # Should return the result unchanged
            assert result == mock_result

    @pytest.mark.asyncio
    async def test_web_execute_remoteobject_conversion_exception(self) -> None:
        """Test web_execute with RemoteObject that raises exception during conversion."""
        mixin = WebScrapingMixin()

        # Mock RemoteObject that will raise an exception when trying to convert to dict
        mock_remote_object = Mock()
        mock_deep_serialized = Mock()

        # Create a list-like object that raises an exception when dict() is called on it
        class ExceptionRaisingList(list[str]):
            def __iter__(self) -> None:  # type: ignore[override]
                raise ValueError("Simulated conversion error")

        mock_deep_serialized.value = ExceptionRaisingList([["key", "value"]])  # type: ignore[list-item]
        mock_remote_object.deep_serialized_value = mock_deep_serialized

        with patch.object(mixin, "page") as mock_page:
            mock_page.evaluate = AsyncMock(return_value = mock_remote_object)

            result = await mixin.web_execute("window.test")

            # Should return the original RemoteObject when conversion fails
            assert result is mock_remote_object
