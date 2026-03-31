# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Tests for JSON API pagination helper methods."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from kleinanzeigen_bot import KleinanzeigenBot
from kleinanzeigen_bot.utils import misc
from kleinanzeigen_bot.utils.exceptions import PublishedAdsFetchIncompleteError


@pytest.mark.unit
class TestJSONPagination:
    """Tests for _coerce_page_number and _fetch_published_ads methods."""

    @pytest.fixture
    def bot(self) -> KleinanzeigenBot:
        return KleinanzeigenBot()

    def test_coerce_page_number_with_valid_int(self) -> None:
        """Test that valid integers are returned as-is."""
        result = misc.coerce_page_number(1)
        if result != 1:
            pytest.fail(f"_coerce_page_number(1) expected 1, got {result}")

        result = misc.coerce_page_number(0)
        if result != 0:
            pytest.fail(f"_coerce_page_number(0) expected 0, got {result}")

        result = misc.coerce_page_number(42)
        if result != 42:
            pytest.fail(f"_coerce_page_number(42) expected 42, got {result}")

    def test_coerce_page_number_with_string_int(self) -> None:
        """Test that string integers are converted to int."""
        result = misc.coerce_page_number("1")
        if result != 1:
            pytest.fail(f"_coerce_page_number('1') expected 1, got {result}")

        result = misc.coerce_page_number("0")
        if result != 0:
            pytest.fail(f"_coerce_page_number('0') expected 0, got {result}")

        result = misc.coerce_page_number("42")
        if result != 42:
            pytest.fail(f"_coerce_page_number('42') expected 42, got {result}")

    def test_coerce_page_number_with_none(self) -> None:
        """Test that None returns None."""
        result = misc.coerce_page_number(None)
        if result is not None:
            pytest.fail(f"_coerce_page_number(None) expected None, got {result}")

    def test_coerce_page_number_with_invalid_types(self) -> None:
        """Test that invalid types return None."""
        result = misc.coerce_page_number("invalid")
        if result is not None:
            pytest.fail(f'_coerce_page_number("invalid") expected None, got {result}')

        result = misc.coerce_page_number("")
        if result is not None:
            pytest.fail(f'_coerce_page_number("") expected None, got {result}')

        result = misc.coerce_page_number([])
        if result is not None:
            pytest.fail(f"_coerce_page_number([]) expected None, got {result}")

        result = misc.coerce_page_number({})
        if result is not None:
            pytest.fail(f"_coerce_page_number({{}}) expected None, got {result}")

        result = misc.coerce_page_number(3.14)
        if result is not None:
            pytest.fail(f"_coerce_page_number(3.14) expected None, got {result}")

    def test_coerce_page_number_with_whole_number_float(self) -> None:
        """Test that whole-number floats are accepted and converted to int."""
        result = misc.coerce_page_number(2.0)
        if result != 2:
            pytest.fail(f"_coerce_page_number(2.0) expected 2, got {result}")

        result = misc.coerce_page_number(0.0)
        if result != 0:
            pytest.fail(f"_coerce_page_number(0.0) expected 0, got {result}")

        result = misc.coerce_page_number(42.0)
        if result != 42:
            pytest.fail(f"_coerce_page_number(42.0) expected 42, got {result}")

    @pytest.mark.asyncio
    async def test_fetch_published_ads_single_page_no_paging(self, bot:KleinanzeigenBot) -> None:
        """Test fetching ads from single page with no paging info."""
        with patch.object(bot, "web_request", new_callable = AsyncMock) as mock_request:
            mock_request.return_value = {"content": '{"ads": [{"id": 1, "state": "active", "title": "Ad 1"}, {"id": 2, "state": "active", "title": "Ad 2"}]}'}

            result = await bot._fetch_published_ads()

            if len(result) != 2:
                pytest.fail(f"Expected 2 results, got {len(result)}")
            if result[0]["id"] != 1:
                pytest.fail(f"Expected result[0]['id'] == 1, got {result[0]['id']}")
            if result[1]["id"] != 2:
                pytest.fail(f"Expected result[1]['id'] == 2, got {result[1]['id']}")
            mock_request.assert_awaited_once_with(f"{bot.root_url}/m-meine-anzeigen-verwalten.json?sort=DEFAULT&pageNum=1")

    @pytest.mark.asyncio
    async def test_fetch_published_ads_single_page_with_paging(self, bot:KleinanzeigenBot) -> None:
        """Test fetching ads from single page with paging info showing 1/1."""
        response_data = {"ads": [{"id": 1, "state": "active", "title": "Ad 1"}], "paging": {"pageNum": 1, "last": 1}}

        with patch.object(bot, "web_request", new_callable = AsyncMock) as mock_request:
            mock_request.return_value = {"content": json.dumps(response_data)}

            result = await bot._fetch_published_ads()

            if len(result) != 1:
                pytest.fail(f"Expected 1 ad, got {len(result)}")
            if result[0].get("id") != 1:
                pytest.fail(f"Expected ad id 1, got {result[0].get('id')}")
            mock_request.assert_awaited_once_with(f"{bot.root_url}/m-meine-anzeigen-verwalten.json?sort=DEFAULT&pageNum=1")

    @pytest.mark.asyncio
    async def test_fetch_published_ads_multi_page(self, bot:KleinanzeigenBot) -> None:
        """Test fetching ads from multiple pages (3 pages, 2 ads each)."""
        page1_data = {"ads": [{"id": 1, "state": "active"}, {"id": 2, "state": "active"}], "paging": {"pageNum": 1, "last": 3, "next": 2}}
        page2_data = {"ads": [{"id": 3, "state": "active"}, {"id": 4, "state": "active"}], "paging": {"pageNum": 2, "last": 3, "next": 3}}
        page3_data = {"ads": [{"id": 5, "state": "active"}, {"id": 6, "state": "active"}], "paging": {"pageNum": 3, "last": 3}}

        with patch.object(bot, "web_request", new_callable = AsyncMock) as mock_request:
            mock_request.side_effect = [
                {"content": json.dumps(page1_data)},
                {"content": json.dumps(page2_data)},
                {"content": json.dumps(page3_data)},
            ]

            result = await bot._fetch_published_ads()

            if len(result) != 6:
                pytest.fail(f"Expected 6 ads but got {len(result)}")
            if [ad["id"] for ad in result] != [1, 2, 3, 4, 5, 6]:
                pytest.fail(f"Expected ids [1, 2, 3, 4, 5, 6] but got {[ad['id'] for ad in result]}")
            if mock_request.call_count != 3:
                pytest.fail(f"Expected 3 web_request calls but got {mock_request.call_count}")
            mock_request.assert_any_await(f"{bot.root_url}/m-meine-anzeigen-verwalten.json?sort=DEFAULT&pageNum=1")
            mock_request.assert_any_await(f"{bot.root_url}/m-meine-anzeigen-verwalten.json?sort=DEFAULT&pageNum=2")
            mock_request.assert_any_await(f"{bot.root_url}/m-meine-anzeigen-verwalten.json?sort=DEFAULT&pageNum=3")

    @pytest.mark.asyncio
    async def test_fetch_published_ads_empty_list(self, bot:KleinanzeigenBot) -> None:
        """Test handling of empty ads list."""
        response_data = {"ads": [], "paging": {"pageNum": 1, "last": 1}}

        with patch.object(bot, "web_request", new_callable = AsyncMock) as mock_request:
            mock_request.return_value = {"content": json.dumps(response_data)}

            result = await bot._fetch_published_ads()

            if not isinstance(result, list):
                pytest.fail(f"expected result to be list, got {type(result).__name__}")
            if len(result) != 0:
                pytest.fail(f"expected empty list from _fetch_published_ads, got {len(result)} items")

    @pytest.mark.asyncio
    async def test_fetch_published_ads_invalid_json(self, bot:KleinanzeigenBot) -> None:
        """Test handling of invalid JSON response."""
        with patch.object(bot, "web_request", new_callable = AsyncMock) as mock_request:
            mock_request.return_value = {"content": "invalid json"}

            result = await bot._fetch_published_ads()
            if result != []:
                pytest.fail(f"Expected empty list on invalid JSON, got {result}")

    @pytest.mark.asyncio
    async def test_fetch_published_ads_missing_paging_dict(self, bot:KleinanzeigenBot) -> None:
        """Test handling of missing paging dict."""
        response_data = {"ads": [{"id": 1, "state": "active"}, {"id": 2, "state": "active"}]}

        with patch.object(bot, "web_request", new_callable = AsyncMock) as mock_request:
            mock_request.return_value = {"content": json.dumps(response_data)}

            result = await bot._fetch_published_ads()

            if len(result) != 2:
                pytest.fail(f"expected 2 ads, got {len(result)}")
            mock_request.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fetch_published_ads_non_integer_paging_values(self, bot:KleinanzeigenBot) -> None:
        """Test handling of non-integer paging values."""
        response_data = {"ads": [{"id": 1, "state": "active"}], "paging": {"pageNum": "invalid", "last": "also-invalid"}}

        with patch.object(bot, "web_request", new_callable = AsyncMock) as mock_request:
            mock_request.return_value = {"content": json.dumps(response_data)}

            result = await bot._fetch_published_ads()

            # Should return ads from first page and stop due to invalid paging
            if len(result) != 1:
                pytest.fail(f"Expected 1 ad, got {len(result)}")
            if result[0].get("id") != 1:
                pytest.fail(f"Expected ad id 1, got {result[0].get('id')}")

    @pytest.mark.asyncio
    async def test_fetch_published_ads_non_list_ads(self, bot:KleinanzeigenBot) -> None:
        """Test handling of non-list ads field."""
        response_data = {"ads": "not a list", "paging": {"pageNum": 1, "last": 1}}

        with patch.object(bot, "web_request", new_callable = AsyncMock) as mock_request:
            mock_request.return_value = {"content": json.dumps(response_data)}

            result = await bot._fetch_published_ads()

            # Should return empty list when ads is not a list
            if not isinstance(result, list):
                pytest.fail(f"expected empty list when 'ads' is not a list, got: {result}")
            if len(result) != 0:
                pytest.fail(f"expected empty list when 'ads' is not a list, got: {result}")

    @pytest.mark.asyncio
    async def test_fetch_published_ads_filters_non_dict_entries(self, bot:KleinanzeigenBot, caplog:pytest.LogCaptureFixture) -> None:
        """Malformed entries should be filtered and logged."""
        response_data = {"ads": [42, {"id": 1, "state": "active"}, "broken"], "paging": {"pageNum": 1, "last": 1}}

        with patch.object(bot, "web_request", new_callable = AsyncMock) as mock_request:
            mock_request.return_value = {"content": json.dumps(response_data)}

            with caplog.at_level("WARNING"):
                result = await bot._fetch_published_ads()

            if result != [{"id": 1, "state": "active"}]:
                pytest.fail(f"expected malformed entries to be filtered out, got: {result}")
            if "Filtered 2 malformed ad entries on page 1" not in caplog.text:
                pytest.fail(f"expected malformed-entry warning in logs, got: {caplog.text}")

    @pytest.mark.asyncio
    async def test_fetch_published_ads_filters_dict_entries_missing_required_keys(
        self,
        bot:KleinanzeigenBot,
        caplog:pytest.LogCaptureFixture,
    ) -> None:
        """Dict entries without required id/state keys should be rejected."""
        response_data = {
            "ads": [
                {"id": 1},
                {"state": "active"},
                {"title": "missing both"},
                {"id": 2, "state": "paused"},
            ],
            "paging": {"pageNum": 1, "last": 1},
        }

        with patch.object(bot, "web_request", new_callable = AsyncMock) as mock_request:
            mock_request.return_value = {"content": json.dumps(response_data)}

            with caplog.at_level("WARNING"):
                result = await bot._fetch_published_ads()

            if result != [{"id": 2, "state": "paused"}]:
                pytest.fail(f"expected only entries with id and state to remain, got: {result}")
            if "Filtered 3 malformed ad entries on page 1" not in caplog.text:
                pytest.fail(f"expected malformed-entry warning in logs, got: {caplog.text}")

    @pytest.mark.asyncio
    async def test_fetch_published_ads_strict_raises_on_malformed_entries(self, bot:KleinanzeigenBot) -> None:
        """Strict fetch should raise when malformed entries are detected."""
        response_data = {"ads": [42, {"id": 1, "state": "active"}, "broken"], "paging": {"pageNum": 1, "last": 1}}
        mock_request = AsyncMock(return_value = {"content": json.dumps(response_data)})

        with (
            patch.object(bot, "web_request", mock_request),
            pytest.raises(PublishedAdsFetchIncompleteError, match = "Filtered 2 malformed ad entries on page 1"),
        ):
            await bot._fetch_published_ads(strict = True)

    @pytest.mark.asyncio
    async def test_fetch_published_ads_timeout(self, bot:KleinanzeigenBot) -> None:
        """Test handling of timeout during pagination."""
        with patch.object(bot, "web_request", new_callable = AsyncMock) as mock_request:
            mock_request.side_effect = TimeoutError("timeout")

            result = await bot._fetch_published_ads()

            if result != []:
                pytest.fail(f"Expected empty list on timeout, got {result}")

    @pytest.mark.asyncio
    async def test_fetch_published_ads_strict_raises_on_timeout(self, bot:KleinanzeigenBot) -> None:
        """Strict fetch should raise when pagination cannot be completed."""
        with (
            patch.object(bot, "web_request", new_callable = AsyncMock, side_effect = TimeoutError("timeout")),
            pytest.raises(PublishedAdsFetchIncompleteError, match = "Pagination request failed on page 1"),
        ):
            await bot._fetch_published_ads(strict = True)

    @pytest.mark.asyncio
    async def test_fetch_published_ads_handles_non_string_content_type(self, bot:KleinanzeigenBot, caplog:pytest.LogCaptureFixture) -> None:
        """Unexpected non-string content types should stop pagination with warning."""
        with patch.object(bot, "web_request", new_callable = AsyncMock) as mock_request:
            mock_request.return_value = {"content": None}

            with caplog.at_level("WARNING"):
                result = await bot._fetch_published_ads()

            if result != []:
                pytest.fail(f"expected empty result on non-string content, got: {result}")
            if "Unexpected response content type on page 1: NoneType" not in caplog.text:
                pytest.fail(f"expected non-string content warning in logs, got: {caplog.text}")

    @pytest.mark.asyncio
    async def test_fetch_published_ads_multi_page_without_last_field(self, bot:KleinanzeigenBot) -> None:
        """Pagination should continue using 'next' when 'last' is absent (issue #917)."""
        page1 = {"ads": [{"id": 1, "state": "active"}, {"id": 2, "state": "active"}], "paging": {"pageNum": 1, "pageSize": 25, "numFound": 3, "next": 2}}
        page2 = {"ads": [{"id": 3, "state": "active"}], "paging": {"pageNum": 2, "pageSize": 25, "numFound": 3}}

        with patch.object(bot, "web_request", new_callable = AsyncMock) as mock_request:
            mock_request.side_effect = [
                {"content": json.dumps(page1)},
                {"content": json.dumps(page2)},
            ]

            result = await bot._fetch_published_ads()

            if [ad["id"] for ad in result] != [1, 2, 3]:
                pytest.fail(f"Expected ids [1, 2, 3] but got {[ad['id'] for ad in result]}")
            if mock_request.call_count != 2:
                pytest.fail(f"Expected 2 web_request calls but got {mock_request.call_count}")
            requested_pages = [int(call.args[0].rsplit("pageNum=", maxsplit = 1)[1]) for call in mock_request.await_args_list]
            if requested_pages != [1, 2]:
                pytest.fail(f"Expected page requests [1, 2] but got {requested_pages}")

    @pytest.mark.asyncio
    async def test_fetch_published_ads_single_page_no_last_no_next(self, bot:KleinanzeigenBot) -> None:
        """Paging dict with pageNum=1 but no 'last' and no 'next' should return ads and stop cleanly."""
        response_data = {
            "ads": [{"id": 10, "state": "active"}, {"id": 20, "state": "active"}],
            "paging": {"pageNum": 1},
        }

        with patch.object(bot, "web_request", new_callable = AsyncMock) as mock_request:
            mock_request.return_value = {"content": json.dumps(response_data)}

            result = await bot._fetch_published_ads()

            if len(result) != 2:
                pytest.fail(f"Expected 2 ads, got {len(result)}")
            if [ad["id"] for ad in result] != [10, 20]:
                pytest.fail(f"Expected ids [10, 20] but got {[ad['id'] for ad in result]}")
            mock_request.assert_awaited_once_with(
                f"{bot.root_url}/m-meine-anzeigen-verwalten.json?sort=DEFAULT&pageNum=1",
            )

    @pytest.mark.asyncio
    async def test_fetch_published_ads_single_page_no_last_no_next_strict_raises(self, bot:KleinanzeigenBot) -> None:
        """Strict mode should fail when paging omits both 'last' and 'next'."""
        response_data = {
            "ads": [{"id": 10, "state": "active"}],
            "paging": {"pageNum": 1},
        }

        with (
            patch.object(bot, "web_request", new_callable = AsyncMock, return_value = {"content": json.dumps(response_data)}),
            pytest.raises(PublishedAdsFetchIncompleteError, match = r"No 'next' in paging on page 1"),
        ):
            await bot._fetch_published_ads(strict = True)
