# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Unit tests for web_scraping_mixin.py JavaScript serialization handling.

Tests the JSON serialization approach to ensure regular Python objects are returned.
"""

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from kleinanzeigen_bot.utils.web_scraping_mixin import WebScrapingMixin


class TestWebExecuteJavaScriptSerialization:
    """Test web_execute method with JSON serialization approach."""

    @pytest.mark.asyncio
    async def test_web_execute_with_regular_result(self) -> None:
        """Test web_execute with regular result."""
        mixin = WebScrapingMixin()

        with patch.object(mixin, "page") as mock_page:
            mock_page.evaluate = AsyncMock(return_value = "regular_result")

            result = await mixin.web_execute("window.test")

            assert result == "regular_result"

    @pytest.mark.asyncio
    async def test_web_execute_with_dict_result(self) -> None:
        """Test web_execute with dict result."""
        mixin = WebScrapingMixin()

        with patch.object(mixin, "page") as mock_page:
            mock_page.evaluate = AsyncMock(return_value = {"key": "value"})

            result = await mixin.web_execute("window.test")

            assert result == {"key": "value"}

    @pytest.mark.asyncio
    async def test_web_execute_with_complex_dict_result(self) -> None:
        """Test web_execute with complex dict result."""
        mixin = WebScrapingMixin()

        with patch.object(mixin, "page") as mock_page:
            mock_page.evaluate = AsyncMock(return_value = {
                "statusCode": 200,
                "content": "success",
                "nested": {"key": "value"}
            })

            result = await mixin.web_execute("window.test")

            expected = {
                "statusCode": 200,
                "content": "success",
                "nested": {"key": "value"}
            }
            assert result == expected

    @pytest.mark.asyncio
    async def test_web_execute_with_remoteobject_conversion(self) -> None:
        """Test web_execute with RemoteObject conversion."""
        mixin = WebScrapingMixin()

        # Test the _convert_remote_object_value method directly
        test_data = [["key1", "value1"], ["key2", "value2"]]
        result = mixin._convert_remote_object_value(test_data)

        # Should convert key/value list to dict
        assert result == {"key1": "value1", "key2": "value2"}

    def test_convert_remote_object_value_key_value_list(self) -> None:
        """Test _convert_remote_object_value with key/value list format."""
        mixin = WebScrapingMixin()

        # Test key/value list format
        test_data = [["key1", "value1"], ["key2", "value2"]]
        result = mixin._convert_remote_object_value(test_data)
        assert result == {"key1": "value1", "key2": "value2"}

    def test_convert_remote_object_value_with_nested_type_value(self) -> None:
        """Test _convert_remote_object_value with nested type/value structures."""
        mixin = WebScrapingMixin()

        # Test with nested type/value structures
        test_data = [["key1", {"type": "string", "value": "nested_value"}]]
        result = mixin._convert_remote_object_value(test_data)
        assert result == {"key1": "nested_value"}

    def test_convert_remote_object_value_regular_list(self) -> None:
        """Test _convert_remote_object_value with regular list."""
        mixin = WebScrapingMixin()

        # Test regular list (not key/value format)
        test_data = ["item1", "item2", "item3"]
        result = mixin._convert_remote_object_value(test_data)
        assert result == ["item1", "item2", "item3"]

    def test_convert_remote_object_value_nested_list(self) -> None:
        """Test _convert_remote_object_value with nested list."""
        mixin = WebScrapingMixin()

        # Test nested list that looks like key/value pairs (gets converted to dict)
        test_data = [["nested", "list"], ["another", "item"]]
        result = mixin._convert_remote_object_value(test_data)
        assert result == {"nested": "list", "another": "item"}

    def test_convert_remote_object_value_type_value_dict(self) -> None:
        """Test _convert_remote_object_value with type/value dict."""
        mixin = WebScrapingMixin()

        # Test type/value dict
        test_data = {"type": "string", "value": "actual_value"}
        result = mixin._convert_remote_object_value(test_data)
        assert result == "actual_value"

    def test_convert_remote_object_value_regular_dict(self) -> None:
        """Test _convert_remote_object_value with regular dict."""
        mixin = WebScrapingMixin()

        # Test regular dict
        test_data = {"key1": "value1", "key2": "value2"}
        result = mixin._convert_remote_object_value(test_data)
        assert result == {"key1": "value1", "key2": "value2"}

    def test_convert_remote_object_value_nested_dict(self) -> None:
        """Test _convert_remote_object_value with nested dict."""
        mixin = WebScrapingMixin()

        # Test nested dict
        test_data = {"key1": {"nested": "value"}, "key2": "value2"}
        result = mixin._convert_remote_object_value(test_data)
        assert result == {"key1": {"nested": "value"}, "key2": "value2"}

    def test_convert_remote_object_value_primitive(self) -> None:
        """Test _convert_remote_object_value with primitive values."""
        mixin = WebScrapingMixin()

        # Test primitive values
        assert mixin._convert_remote_object_value("string") == "string"
        assert mixin._convert_remote_object_value(123) == 123
        assert mixin._convert_remote_object_value(True) is True
        assert mixin._convert_remote_object_value(None) is None

    def test_convert_remote_object_value_malformed_key_value_pair(self) -> None:
        """Test _convert_remote_object_value with malformed key/value pairs."""
        mixin = WebScrapingMixin()

        # Test with malformed key/value pairs (wrong length)
        test_data = [["key1", "value1"], ["key2"]]  # Second item has wrong length
        result = mixin._convert_remote_object_value(test_data)
        # Should still convert the valid pairs and skip malformed ones
        assert result == {"key1": "value1"}

    def test_convert_remote_object_value_empty_list(self) -> None:
        """Test _convert_remote_object_value with empty list."""
        mixin = WebScrapingMixin()

        # Test empty list
        test_data:list[Any] = []
        result = mixin._convert_remote_object_value(test_data)
        assert result == []

    def test_convert_remote_object_value_complex_nested_structure(self) -> None:
        """Test _convert_remote_object_value with complex nested structure."""
        mixin = WebScrapingMixin()

        # Test complex nested structure
        test_data = [
            ["key1", "value1"],
            ["key2", {"type": "object", "value": {"nested": "value"}}],
            ["key3", [["inner_key", "inner_value"]]]
        ]
        result = mixin._convert_remote_object_value(test_data)
        expected = {
            "key1": "value1",
            "key2": {"nested": "value"},
            "key3": {"inner_key": "inner_value"}  # The inner list gets converted to dict too
        }
        assert result == expected

    @pytest.mark.asyncio
    async def test_web_execute_remoteobject_exception_handling(self) -> None:
        """Test web_execute with RemoteObject exception handling."""
        mixin = WebScrapingMixin()

        # Create a mock RemoteObject that will raise an exception
        mock_remote_object = type("MockRemoteObject", (), {
            "__class__": type("MockClass", (), {"__name__": "RemoteObject"}),
            "value": None,
            "deep_serialized_value": None
        })()

        with patch.object(mixin, "page") as mock_page:
            mock_page.evaluate = AsyncMock(return_value = mock_remote_object)

            # Mock the _convert_remote_object_value to raise an exception
            with patch.object(mixin, "_convert_remote_object_value", side_effect = Exception("Test exception")):
                result = await mixin.web_execute("window.test")

                # Should return the original RemoteObject when exception occurs
                assert result == mock_remote_object

    @pytest.mark.asyncio
    async def test_web_execute_remoteobject_with_value(self) -> None:
        """Test web_execute with RemoteObject that has a value."""
        mixin = WebScrapingMixin()

        # Create a mock RemoteObject with a value
        mock_remote_object = type("MockRemoteObject", (), {
            "__class__": type("MockClass", (), {"__name__": "RemoteObject"}),
            "value": "test_value",
            "deep_serialized_value": None
        })()

        with patch.object(mixin, "page") as mock_page:
            mock_page.evaluate = AsyncMock(return_value = mock_remote_object)

            result = await mixin.web_execute("window.test")

            # Should return the value directly
            assert result == "test_value"

    @pytest.mark.asyncio
    async def test_web_execute_remoteobject_with_deep_serialized_value(self) -> None:
        """Test web_execute with RemoteObject that has deep_serialized_value."""
        mixin = WebScrapingMixin()

        # Create a mock RemoteObject with deep_serialized_value
        mock_remote_object = type("MockRemoteObject", (), {
            "__class__": type("MockClass", (), {"__name__": "RemoteObject"}),
            "value": None,
            "deep_serialized_value": type("MockDeepSerialized", (), {
                "value": [["key1", "value1"], ["key2", "value2"]]
            })()
        })()

        with patch.object(mixin, "page") as mock_page:
            mock_page.evaluate = AsyncMock(return_value = mock_remote_object)

            result = await mixin.web_execute("window.test")

            # Should convert the deep_serialized_value
            assert result == {"key1": "value1", "key2": "value2"}

    @pytest.mark.asyncio
    async def test_web_execute_remoteobject_fallback(self) -> None:
        """Test web_execute with RemoteObject fallback when no value or deep_serialized_value."""
        mixin = WebScrapingMixin()

        # Create a mock RemoteObject with no value or deep_serialized_value
        mock_remote_object = type("MockRemoteObject", (), {
            "__class__": type("MockClass", (), {"__name__": "RemoteObject"}),
            "value": None,
            "deep_serialized_value": None
        })()

        with patch.object(mixin, "page") as mock_page:
            mock_page.evaluate = AsyncMock(return_value = mock_remote_object)

            result = await mixin.web_execute("window.test")

            # Should return the original RemoteObject as fallback
            assert result == mock_remote_object
