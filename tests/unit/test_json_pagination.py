# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Tests for JSON API pagination helper methods."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from kleinanzeigen_bot import KleinanzeigenBot


class TestJSONPagination:
    """Tests for _coerce_page_number and _fetch_published_ads methods."""

    @pytest.fixture
    def bot(self) -> KleinanzeigenBot:
        return KleinanzeigenBot()

    def test_coerce_page_number_with_valid_int(self, bot:KleinanzeigenBot) -> None:
        """Test that valid integers are returned as-is."""
        assert bot._coerce_page_number(1) == 1
        assert bot._coerce_page_number(0) == 0
        assert bot._coerce_page_number(42) == 42

    def test_coerce_page_number_with_string_int(self, bot:KleinanzeigenBot) -> None:
        """Test that string integers are converted to int."""
        assert bot._coerce_page_number("1") == 1
        assert bot._coerce_page_number("0") == 0
        assert bot._coerce_page_number("42") == 42

    def test_coerce_page_number_with_none(self, bot:KleinanzeigenBot) -> None:
        """Test that None returns None."""
        assert bot._coerce_page_number(None) is None

    def test_coerce_page_number_with_invalid_types(self, bot:KleinanzeigenBot) -> None:
        """Test that invalid types return None."""
        assert bot._coerce_page_number("invalid") is None
        assert bot._coerce_page_number("") is None
        assert bot._coerce_page_number([]) is None
        assert bot._coerce_page_number({}) is None
        assert bot._coerce_page_number(3.14) is None

    @pytest.mark.asyncio
    async def test_fetch_published_ads_single_page_no_paging(self, bot:KleinanzeigenBot) -> None:
        """Test fetching ads from single page with no paging info."""
        with patch.object(bot, "web_request", new_callable = AsyncMock) as mock_request:
            mock_request.return_value = {"content": '{"ads": [{"id": 1, "title": "Ad 1"}, {"id": 2, "title": "Ad 2"}]}'}

            result = await bot._fetch_published_ads()

            assert len(result) == 2
            assert result[0]["id"] == 1
            assert result[1]["id"] == 2
            mock_request.assert_awaited_once_with(f"{bot.root_url}/m-meine-anzeigen-verwalten.json?sort=DEFAULT&page=1")

    @pytest.mark.asyncio
    async def test_fetch_published_ads_single_page_with_paging(self, bot:KleinanzeigenBot) -> None:
        """Test fetching ads from single page with paging info showing 1/1."""
        response_data = {"ads": [{"id": 1, "title": "Ad 1"}], "paging": {"pageNum": 1, "last": 1}}

        with patch.object(bot, "web_request", new_callable = AsyncMock) as mock_request:
            mock_request.return_value = {"content": json.dumps(response_data)}

            result = await bot._fetch_published_ads()

            assert len(result) == 1
            assert result[0]["id"] == 1
            mock_request.assert_awaited_once_with(f"{bot.root_url}/m-meine-anzeigen-verwalten.json?sort=DEFAULT&page=1")

    @pytest.mark.asyncio
    async def test_fetch_published_ads_multi_page(self, bot:KleinanzeigenBot) -> None:
        """Test fetching ads from multiple pages (3 pages, 2 ads each)."""
        page1_data = {"ads": [{"id": 1}, {"id": 2}], "paging": {"pageNum": 1, "last": 3}}
        page2_data = {"ads": [{"id": 3}, {"id": 4}], "paging": {"pageNum": 2, "last": 3}}
        page3_data = {"ads": [{"id": 5}, {"id": 6}], "paging": {"pageNum": 3, "last": 3}}

        with patch.object(bot, "web_request", new_callable = AsyncMock) as mock_request:
            mock_request.side_effect = [
                {"content": json.dumps(page1_data)},
                {"content": json.dumps(page2_data)},
                {"content": json.dumps(page3_data)},
            ]

            result = await bot._fetch_published_ads()

            assert len(result) == 6
            assert [ad["id"] for ad in result] == [1, 2, 3, 4, 5, 6]
            assert mock_request.call_count == 3
            mock_request.assert_any_await(f"{bot.root_url}/m-meine-anzeigen-verwalten.json?sort=DEFAULT&page=1")
            mock_request.assert_any_await(f"{bot.root_url}/m-meine-anzeigen-verwalten.json?sort=DEFAULT&page=2")
            mock_request.assert_any_await(f"{bot.root_url}/m-meine-anzeigen-verwalten.json?sort=DEFAULT&page=3")

    @pytest.mark.asyncio
    async def test_fetch_published_ads_empty_list(self, bot:KleinanzeigenBot) -> None:
        """Test handling of empty ads list."""
        response_data = {"ads": [], "paging": {"pageNum": 1, "last": 1}}

        with patch.object(bot, "web_request", new_callable = AsyncMock) as mock_request:
            mock_request.return_value = {"content": json.dumps(response_data)}

            result = await bot._fetch_published_ads()

            assert len(result) == 0
            assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_fetch_published_ads_invalid_json(self, bot:KleinanzeigenBot) -> None:
        """Test handling of invalid JSON response."""
        with patch.object(bot, "web_request", new_callable = AsyncMock) as mock_request:
            mock_request.return_value = {"content": "invalid json"}

            with pytest.raises(json.JSONDecodeError):
                await bot._fetch_published_ads()

    @pytest.mark.asyncio
    async def test_fetch_published_ads_zero_indexed_pages(self, bot:KleinanzeigenBot) -> None:
        """Test handling of zero-indexed page numbers (pageNum: 0, last: 2)."""
        page1_data = {"ads": [{"id": 1}], "paging": {"pageNum": 0, "last": 2}}
        page2_data = {"ads": [{"id": 2}], "paging": {"pageNum": 1, "last": 2}}

        with patch.object(bot, "web_request", new_callable = AsyncMock) as mock_request:
            mock_request.side_effect = [
                {"content": json.dumps(page1_data)},
                {"content": json.dumps(page2_data)},
            ]

            result = await bot._fetch_published_ads()

            assert len(result) == 2
            assert [ad["id"] for ad in result] == [1, 2]
            # Should request page 1 first, then page 2
            assert mock_request.call_count == 2

    @pytest.mark.asyncio
    async def test_fetch_published_ads_mixed_field_names(self, bot:KleinanzeigenBot) -> None:
        """Test handling of different field name variations."""
        page1_data = {"ads": [{"id": 1}], "paging": {"page": 1, "totalPages": 3}}
        page2_data = {"ads": [{"id": 2}], "paging": {"currentPage": 2, "maxPages": 3}}

        with patch.object(bot, "web_request", new_callable = AsyncMock) as mock_request:
            mock_request.side_effect = [
                {"content": json.dumps(page1_data)},
                {"content": json.dumps(page2_data)},
                {"content": json.dumps({"ads": [], "paging": {"page": 3, "totalPages": 3}})},
            ]

            result = await bot._fetch_published_ads()

            assert len(result) == 2
            assert [ad["id"] for ad in result] == [1, 2]

    @pytest.mark.asyncio
    async def test_fetch_published_ads_missing_paging_dict(self, bot:KleinanzeigenBot) -> None:
        """Test handling of missing paging dict."""
        response_data = {"ads": [{"id": 1}, {"id": 2}]}

        with patch.object(bot, "web_request", new_callable = AsyncMock) as mock_request:
            mock_request.return_value = {"content": json.dumps(response_data)}

            result = await bot._fetch_published_ads()

            assert len(result) == 2
            mock_request.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fetch_published_ads_non_integer_paging_values(self, bot:KleinanzeigenBot) -> None:
        """Test handling of non-integer paging values."""
        response_data = {"ads": [{"id": 1}], "paging": {"pageNum": "invalid", "last": "also-invalid"}}

        with patch.object(bot, "web_request", new_callable = AsyncMock) as mock_request:
            mock_request.return_value = {"content": json.dumps(response_data)}

            result = await bot._fetch_published_ads()

            # Should return ads from first page and stop due to invalid paging
            assert len(result) == 1
            assert result[0]["id"] == 1

    @pytest.mark.asyncio
    async def test_fetch_published_ads_non_list_ads(self, bot:KleinanzeigenBot) -> None:
        """Test handling of non-list ads field."""
        response_data = {"ads": "not a list", "paging": {"pageNum": 1, "last": 1}}

        with patch.object(bot, "web_request", new_callable = AsyncMock) as mock_request:
            mock_request.return_value = {"content": json.dumps(response_data)}

            result = await bot._fetch_published_ads()

            # Should return empty list when ads is not a list
            assert len(result) == 0
            assert isinstance(result, list)
