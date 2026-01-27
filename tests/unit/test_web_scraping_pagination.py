# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Tests for the _navigate_paginated_ad_overview helper method."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kleinanzeigen_bot.utils.web_scraping_mixin import By, Element, WebScrapingMixin


class TestNavigatePaginatedAdOverview:
    """Tests for _navigate_paginated_ad_overview method."""

    @pytest.mark.asyncio
    async def test_single_page_action_succeeds(self) -> None:
        """Test pagination on single page where action succeeds."""
        mixin = WebScrapingMixin()

        # Mock callback that succeeds
        callback = AsyncMock(return_value=True)

        with (
            patch.object(mixin, "web_open", new_callable=AsyncMock),
            patch.object(mixin, "web_sleep", new_callable=AsyncMock),
            patch.object(mixin, "web_find", new_callable=AsyncMock) as mock_find,
            patch.object(mixin, "web_find_all", new_callable=AsyncMock, return_value=[]),
            patch.object(mixin, "web_scroll_page_down", new_callable=AsyncMock),
            patch.object(mixin, "_timeout", return_value=10),
        ):
            # Ad list container exists
            mock_find.return_value = MagicMock()

            result = await mixin._navigate_paginated_ad_overview(callback)

            assert result is True
            callback.assert_awaited_once_with(1)

    @pytest.mark.asyncio
    async def test_single_page_action_returns_false(self) -> None:
        """Test pagination on single page where action returns False."""
        mixin = WebScrapingMixin()

        # Mock callback that returns False (doesn't find what it's looking for)
        callback = AsyncMock(return_value=False)

        with (
            patch.object(mixin, "web_open", new_callable=AsyncMock),
            patch.object(mixin, "web_sleep", new_callable=AsyncMock),
            patch.object(mixin, "web_find", new_callable=AsyncMock) as mock_find,
            patch.object(mixin, "web_find_all", new_callable=AsyncMock, return_value=[]),
            patch.object(mixin, "web_scroll_page_down", new_callable=AsyncMock),
            patch.object(mixin, "_timeout", return_value=10),
        ):
            # Ad list container exists
            mock_find.return_value = MagicMock()

            result = await mixin._navigate_paginated_ad_overview(callback)

            assert result is False
            callback.assert_awaited_once_with(1)

    @pytest.mark.asyncio
    async def test_multi_page_action_succeeds_on_page_2(self) -> None:
        """Test pagination across multiple pages where action succeeds on page 2."""
        mixin = WebScrapingMixin()

        # Mock callback that returns False on page 1, True on page 2
        callback_results = [False, True]
        callback = AsyncMock(side_effect=callback_results)

        pagination_section = MagicMock()
        next_button_enabled = MagicMock()
        next_button_enabled.attrs = {}  # No "disabled" attribute = enabled
        next_button_enabled.click = AsyncMock()

        find_call_count = {"count": 0}

        async def mock_find_side_effect(selector_type: By, selector_value: str, **kwargs: Any) -> Element:
            find_call_count["count"] += 1
            if selector_type == By.ID and selector_value == "my-manageitems-adlist":
                return MagicMock()  # Ad list container
            if selector_type == By.CSS_SELECTOR and selector_value == ".Pagination":
                return pagination_section
            raise TimeoutError("Unexpected find")

        find_all_call_count = {"count": 0}

        async def mock_find_all_side_effect(selector_type: By, selector_value: str, **kwargs: Any) -> list[Element]:
            find_all_call_count["count"] += 1
            if selector_type == By.CSS_SELECTOR and 'aria-label="Nächste"' in selector_value:
                # Return enabled next button on both calls (initial detection and navigation)
                return [next_button_enabled]
            return []

        with (
            patch.object(mixin, "web_open", new_callable=AsyncMock),
            patch.object(mixin, "web_sleep", new_callable=AsyncMock),
            patch.object(mixin, "web_find", new_callable=AsyncMock, side_effect=mock_find_side_effect),
            patch.object(mixin, "web_find_all", new_callable=AsyncMock, side_effect=mock_find_all_side_effect),
            patch.object(mixin, "web_scroll_page_down", new_callable=AsyncMock),
            patch.object(mixin, "_timeout", return_value=10),
        ):
            result = await mixin._navigate_paginated_ad_overview(callback)

            assert result is True
            assert callback.await_count == 2
            next_button_enabled.click.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_web_open_raises_timeout(self) -> None:
        """Test that TimeoutError on web_open is caught and returns False."""
        mixin = WebScrapingMixin()

        callback = AsyncMock()

        with patch.object(mixin, "web_open", new_callable=AsyncMock, side_effect=TimeoutError("Page load timeout")):
            result = await mixin._navigate_paginated_ad_overview(callback)

            assert result is False
            callback.assert_not_awaited()  # Callback should not be called

    @pytest.mark.asyncio
    async def test_ad_list_container_not_found(self) -> None:
        """Test that missing ad list container returns False."""
        mixin = WebScrapingMixin()

        callback = AsyncMock()

        with (
            patch.object(mixin, "web_open", new_callable=AsyncMock),
            patch.object(mixin, "web_sleep", new_callable=AsyncMock),
            patch.object(mixin, "web_find", new_callable=AsyncMock, side_effect=TimeoutError("Container not found")),
        ):
            result = await mixin._navigate_paginated_ad_overview(callback)

            assert result is False
            callback.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_web_scroll_timeout_continues(self) -> None:
        """Test that TimeoutError on web_scroll_page_down is non-fatal and pagination continues."""
        mixin = WebScrapingMixin()

        callback = AsyncMock(return_value=True)

        with (
            patch.object(mixin, "web_open", new_callable=AsyncMock),
            patch.object(mixin, "web_sleep", new_callable=AsyncMock),
            patch.object(mixin, "web_find", new_callable=AsyncMock, return_value=MagicMock()),
            patch.object(mixin, "web_find_all", new_callable=AsyncMock, return_value=[]),
            patch.object(mixin, "web_scroll_page_down", new_callable=AsyncMock, side_effect=TimeoutError("Scroll timeout")),
            patch.object(mixin, "_timeout", return_value=10),
        ):
            result = await mixin._navigate_paginated_ad_overview(callback)

            # Should continue and call callback despite scroll timeout
            assert result is True
            callback.assert_awaited_once_with(1)

    @pytest.mark.asyncio
    async def test_page_action_raises_timeout(self) -> None:
        """Test that TimeoutError from page_action is caught and returns False."""
        mixin = WebScrapingMixin()

        callback = AsyncMock(side_effect=TimeoutError("Action timeout"))

        with (
            patch.object(mixin, "web_open", new_callable=AsyncMock),
            patch.object(mixin, "web_sleep", new_callable=AsyncMock),
            patch.object(mixin, "web_find", new_callable=AsyncMock, return_value=MagicMock()),
            patch.object(mixin, "web_find_all", new_callable=AsyncMock, return_value=[]),
            patch.object(mixin, "web_scroll_page_down", new_callable=AsyncMock),
            patch.object(mixin, "_timeout", return_value=10),
        ):
            result = await mixin._navigate_paginated_ad_overview(callback)

            assert result is False
            callback.assert_awaited_once_with(1)
