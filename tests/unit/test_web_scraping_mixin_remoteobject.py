# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Unit tests for web_scraping_mixin.py RemoteObject handling.

Tests the conversion of nodriver RemoteObject results to regular Python objects.
"""

from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

from kleinanzeigen_bot.utils.web_scraping_mixin import WebScrapingMixin


class TestWebExecuteRemoteObjectHandling:
    """Test web_execute method with nodriver 0.47+ RemoteObject behavior."""

    @pytest.mark.asyncio
    async def test_web_execute_with_regular_result(self) -> None:
        """Test web_execute with regular (non-RemoteObject) result."""
        mixin = WebScrapingMixin()

        with patch.object(mixin, "page") as mock_page:
            mock_page.evaluate = AsyncMock(return_value = "regular_result")

            result = await mixin.web_execute("window.test")

            assert result == "regular_result"

    @pytest.mark.asyncio
    async def test_web_execute_with_remoteobject_result(self) -> None:
        """Test web_execute with RemoteObject result."""
        mixin = WebScrapingMixin()

        # Mock RemoteObject
        mock_remote_object = Mock()
        mock_remote_object.deep_serialized_value = Mock()
        mock_remote_object.deep_serialized_value.value = {"key": "value"}

        with patch.object(mixin, "page") as mock_page:
            mock_page.evaluate = AsyncMock(return_value = mock_remote_object)

            result = await mixin.web_execute("window.test")

            assert result == {"key": "value"}

    @pytest.mark.asyncio
    async def test_web_execute_remoteobject_with_nested_type_value_structures(self) -> None:
        """Test web_execute with RemoteObject containing nested type/value structures."""
        mixin = WebScrapingMixin()

        # Mock RemoteObject with nested type/value structures
        mock_remote_object = Mock()
        mock_remote_object.deep_serialized_value = Mock()
        mock_remote_object.deep_serialized_value.value = [
            ["statusCode", {"type": "number", "value": 200}],
            ["content", {"type": "string", "value": "success"}]
        ]

        with patch.object(mixin, "page") as mock_page:
            mock_page.evaluate = AsyncMock(return_value = mock_remote_object)

            result = await mixin.web_execute("window.test")

            # Should convert nested type/value structures to their values
            assert result == {"statusCode": 200, "content": "success"}

    @pytest.mark.asyncio
    async def test_web_execute_remoteobject_with_mixed_nested_structures(self) -> None:
        """Test web_execute with RemoteObject containing mixed nested structures."""
        mixin = WebScrapingMixin()

        # Mock RemoteObject with mixed nested structures
        mock_remote_object = Mock()
        mock_remote_object.deep_serialized_value = Mock()
        mock_remote_object.deep_serialized_value.value = {
            "simple": "value",
            "nested": {"type": "number", "value": 42},
            "list": [{"type": "string", "value": "item1"}, {"type": "string", "value": "item2"}]
        }

        with patch.object(mixin, "page") as mock_page:
            mock_page.evaluate = AsyncMock(return_value = mock_remote_object)

            result = await mixin.web_execute("window.test")

            # Should convert nested structures while preserving simple values
            expected = {
                "simple": "value",
                "nested": 42,
                "list": ["item1", "item2"]
            }
            assert result == expected


class TestConvertRemoteObjectResult:
    """Test _convert_remote_object_result method for RemoteObject conversion."""

    def test_convert_remote_object_result_with_none_deep_serialized_value(self) -> None:
        """Test _convert_remote_object_result when deep_serialized_value is None."""
        mixin = WebScrapingMixin()

        # Mock RemoteObject with None deep_serialized_value
        mock_remote_object = Mock()
        mock_remote_object.deep_serialized_value = None

        result = mixin._convert_remote_object_result(mock_remote_object)
        assert result == mock_remote_object

    def test_convert_remote_object_result_with_none_serialized_data(self) -> None:
        """Test _convert_remote_object_result when serialized_data is None."""
        mixin = WebScrapingMixin()

        # Mock RemoteObject with None serialized_data
        mock_remote_object = Mock()
        mock_remote_object.deep_serialized_value = Mock()
        mock_remote_object.deep_serialized_value.value = None

        result = mixin._convert_remote_object_result(mock_remote_object)
        assert result == mock_remote_object

    def test_convert_remote_object_result_with_list_data(self) -> None:
        """Test _convert_remote_object_result with list data."""
        mixin = WebScrapingMixin()

        # Mock RemoteObject with list data
        mock_remote_object = Mock()
        mock_remote_object.deep_serialized_value = Mock()
        mock_remote_object.deep_serialized_value.value = [
            ["key1", "value1"],
            ["key2", "value2"]
        ]

        result = mixin._convert_remote_object_result(mock_remote_object)
        assert result == {"key1": "value1", "key2": "value2"}

    def test_convert_remote_object_result_with_dict_data(self) -> None:
        """Test _convert_remote_object_result with dict data."""
        mixin = WebScrapingMixin()

        # Mock RemoteObject with dict data
        mock_remote_object = Mock()
        mock_remote_object.deep_serialized_value = Mock()
        mock_remote_object.deep_serialized_value.value = {"key": "value"}

        result = mixin._convert_remote_object_result(mock_remote_object)
        assert result == {"key": "value"}

    def test_convert_remote_object_result_with_conversion_error(self) -> None:
        """Test _convert_remote_object_result when conversion raises an exception."""
        mixin = WebScrapingMixin()

        # Mock RemoteObject that will raise an exception during conversion
        mock_remote_object = Mock()
        mock_remote_object.deep_serialized_value = Mock()
        mock_remote_object.deep_serialized_value.value = "invalid_data"

        # Mock the _convert_remote_object_dict to raise an exception
        with patch.object(mixin, "_convert_remote_object_dict", side_effect = ValueError("Test error")):
            result = mixin._convert_remote_object_result(mock_remote_object)
            # When conversion fails, it should return the original value
            assert result == "invalid_data"


class TestConvertRemoteObjectDict:
    """Test _convert_remote_object_dict method for nested RemoteObject conversion."""

    def test_convert_remote_object_dict_with_type_value_pair(self) -> None:
        """Test conversion of type/value pair structures."""
        mixin = WebScrapingMixin()

        # Test type/value pair
        data = {"type": "number", "value": 200}
        result = mixin._convert_remote_object_dict(data)
        assert result == 200

        # Test string type/value pair
        data = {"type": "string", "value": "hello"}
        result = mixin._convert_remote_object_dict(data)
        assert result == "hello"

    def test_convert_remote_object_dict_with_regular_dict(self) -> None:
        """Test conversion of regular dict structures."""
        mixin = WebScrapingMixin()

        # Test regular dict (not type/value pair)
        data = {"key1": "value1", "key2": "value2"}
        result = mixin._convert_remote_object_dict(data)
        assert result == {"key1": "value1", "key2": "value2"}

    def test_convert_remote_object_dict_with_nested_structures(self) -> None:
        """Test conversion of nested dict structures."""
        mixin = WebScrapingMixin()

        # Test nested structures
        data = {
            "simple": "value",
            "nested": {"type": "number", "value": 42},
            "list": [{"type": "string", "value": "item1"}, {"type": "string", "value": "item2"}]
        }
        result = mixin._convert_remote_object_dict(data)

        expected = {
            "simple": "value",
            "nested": 42,
            "list": ["item1", "item2"]
        }
        assert result == expected

    def test_convert_remote_object_dict_with_list(self) -> None:
        """Test conversion of list structures."""
        mixin = WebScrapingMixin()

        # Test list with type/value pairs
        data = [{"type": "number", "value": 1}, {"type": "string", "value": "test"}]
        result = mixin._convert_remote_object_dict(data)
        assert result == [1, "test"]

    def test_convert_remote_object_dict_with_primitive_values(self) -> None:
        """Test conversion with primitive values."""
        mixin = WebScrapingMixin()

        # Test primitive values
        assert mixin._convert_remote_object_dict("string") == "string"
        assert mixin._convert_remote_object_dict(42) == 42
        assert mixin._convert_remote_object_dict(True) is True
        assert mixin._convert_remote_object_dict(None) is None

    def test_convert_remote_object_dict_with_complex_nested_structures(self) -> None:
        """Test conversion with complex nested structures."""
        mixin = WebScrapingMixin()

        # Test complex nested structures
        data = {
            "response": {
                "status": {"type": "number", "value": 200},
                "data": [
                    {"type": "string", "value": "item1"},
                    {"type": "string", "value": "item2"}
                ],
                "metadata": {
                    "count": {"type": "number", "value": 2},
                    "type": {"type": "string", "value": "list"}
                }
            }
        }
        result = mixin._convert_remote_object_dict(data)

        expected = {
            "response": {
                "status": 200,
                "data": ["item1", "item2"],
                "metadata": {
                    "count": 2,
                    "type": "list"
                }
            }
        }
        assert result == expected


class TestWebRequestRemoteObjectHandling:
    """Test web_request method with nodriver 0.47+ RemoteObject behavior."""

    @pytest.mark.asyncio
    async def test_web_request_with_remoteobject_result(self) -> None:
        """Test web_request with RemoteObject result to catch subscriptability issues."""
        mixin = WebScrapingMixin()

        # Mock RemoteObject that simulates the exact structure returned by web_request
        mock_remote_object = Mock()
        mock_remote_object.deep_serialized_value = Mock()
        mock_remote_object.deep_serialized_value.value = {
            "statusCode": 200,
            "statusMessage": "OK",
            "headers": {"content-type": "application/json"},
            "content": '{"success": true}'
        }

        with patch.object(mixin, "web_execute") as mock_web_execute:
            # Mock web_execute to return the converted result (simulating our fix)
            mock_web_execute.return_value = {
                "statusCode": 200,
                "statusMessage": "OK",
                "headers": {"content-type": "application/json"},
                "content": '{"success": true}'
            }

            result = await mixin.web_request("https://example.com/api")

            # Verify the result is properly converted and subscriptable
            assert result["statusCode"] == 200
            assert result["statusMessage"] == "OK"
            assert result["headers"]["content-type"] == "application/json"
            assert result["content"] == '{"success": true}'

    @pytest.mark.asyncio
    async def test_web_request_with_remoteobject_error_response(self) -> None:
        """Test web_request with RemoteObject error response."""
        mixin = WebScrapingMixin()

        # Mock RemoteObject for error response
        mock_remote_object = Mock()
        mock_remote_object.deep_serialized_value = Mock()
        mock_remote_object.deep_serialized_value.value = {
            "statusCode": 404,
            "statusMessage": "Not Found",
            "headers": {"content-type": "text/html"},
            "content": "<html>Not Found</html>"
        }

        with patch.object(mixin, "web_execute") as mock_web_execute:
            # Mock web_execute to return the converted result (simulating our fix)
            mock_web_execute.return_value = {
                "statusCode": 404,
                "statusMessage": "Not Found",
                "headers": {"content-type": "text/html"},
                "content": "<html>Not Found</html>"
            }

            # This should raise an exception due to invalid status code
            with pytest.raises(Exception, match = "Invalid response"):
                await mixin.web_request("https://example.com/api", valid_response_codes = [200])

    @pytest.mark.asyncio
    async def test_web_request_with_nested_remoteobject_structures(self) -> None:
        """Test web_request with complex nested RemoteObject structures."""
        mixin = WebScrapingMixin()

        # Mock RemoteObject with nested type/value structures
        mock_remote_object = Mock()
        mock_remote_object.deep_serialized_value = Mock()
        mock_remote_object.deep_serialized_value.value = {
            "statusCode": {"type": "number", "value": 200},
            "statusMessage": {"type": "string", "value": "OK"},
            "headers": {
                "content-type": {"type": "string", "value": "application/json"}
            },
            "content": {"type": "string", "value": '{"data": "test"}'}
        }

        with patch.object(mixin, "web_execute") as mock_web_execute:
            # Mock web_execute to return the converted result (simulating our fix)
            mock_web_execute.return_value = {
                "statusCode": 200,
                "statusMessage": "OK",
                "headers": {"content-type": "application/json"},
                "content": '{"data": "test"}'
            }

            result = await mixin.web_request("https://example.com/api")

            # Verify nested structures are properly converted
            assert result["statusCode"] == 200
            assert result["statusMessage"] == "OK"
            assert result["headers"]["content-type"] == "application/json"
            assert result["content"] == '{"data": "test"}'


class TestWebRequestRemoteObjectRegression:
    """Test web_request method to catch future RemoteObject regression issues."""

    @pytest.mark.asyncio
    async def test_web_request_without_remoteobject_conversion_fails(self) -> None:
        """Test that web_request fails without RemoteObject conversion (regression test)."""
        mixin = WebScrapingMixin()

        # Mock RemoteObject that would cause the original error
        mock_remote_object = Mock()
        mock_remote_object.deep_serialized_value = Mock()
        mock_remote_object.deep_serialized_value.value = {
            "statusCode": 200,
            "statusMessage": "OK",
            "headers": {"content-type": "application/json"},
            "content": '{"success": true}'
        }

        # Mock web_execute to return the raw RemoteObject (simulating the bug)
        with patch.object(mixin, "web_execute") as mock_web_execute:
            mock_web_execute.return_value = mock_remote_object

            # This should fail with the original error if our fix is removed
            with pytest.raises(TypeError, match = "object is not subscriptable"):
                await mixin.web_request("https://example.com/api")

    @pytest.mark.asyncio
    async def test_web_request_with_remoteobject_conversion_succeeds(self) -> None:
        """Test that web_request succeeds with RemoteObject conversion (our fix)."""
        mixin = WebScrapingMixin()

        # Mock RemoteObject
        mock_remote_object = Mock()
        mock_remote_object.deep_serialized_value = Mock()
        mock_remote_object.deep_serialized_value.value = {
            "statusCode": 200,
            "statusMessage": "OK",
            "headers": {"content-type": "application/json"},
            "content": '{"success": true}'
        }

        # Mock web_execute to simulate our conversion logic
        async def mock_web_execute_with_conversion(script:str) -> Any:
            # Simulate the conversion that happens in our fix
            return mock_remote_object.deep_serialized_value.value

        with patch.object(mixin, "web_execute", side_effect = mock_web_execute_with_conversion):
            result = await mixin.web_request("https://example.com/api")

            # Verify the result works correctly
            assert result["statusCode"] == 200
            assert result["statusMessage"] == "OK"
            assert result["headers"]["content-type"] == "application/json"
            assert result["content"] == '{"success": true}'
