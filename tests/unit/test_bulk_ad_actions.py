# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Bulk commands traverse simulated overview pages without a browser or network."""

import re
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kleinanzeigen_bot import extend_flow, reserve_flow
from kleinanzeigen_bot.app import KleinanzeigenBot
from kleinanzeigen_bot.model.ad_model import Ad
from kleinanzeigen_bot.utils import dicts, misc
from kleinanzeigen_bot.utils.web_scraping_mixin import By, Element


class Overview:
    """Only the DOM boundary is simulated; commands use the real pagination loop."""

    def __init__(self, pages:list[list[int]], failed_id:int | None, action:str) -> None:
        self.pages = pages
        self.failed_id = failed_id
        self.action = action
        self.page = 0
        self.visited:list[int] = []
        self.attempted:list[int] = []
        self.succeeded:list[int] = []

    async def open(self, url:str) -> None:
        assert url == "https://example.invalid/m-meine-anzeigen.html"
        self.page = 0
        self.visited.append(self.page)

    async def next_page(self) -> None:
        self.page += 1
        self.visited.append(self.page)

    async def find_all(self, selector_type:By, selector_value:str, **_kwargs:Any) -> list[Element]:
        assert selector_type == By.CSS_SELECTOR
        if selector_value == 'button[aria-label="Nächste"]':
            button = MagicMock()
            button.attrs = {} if self.page + 1 < len(self.pages) else {"disabled": ""}
            button.click = self.next_page
            return [button]
        assert selector_value == "#my-manageitems-adlist li[data-adid]"
        return [MagicMock(attrs = {"data-adid": str(ad_id)}) for ad_id in self.pages[self.page]]

    async def find(self, selector_type:By, selector_value:str, **_kwargs:Any) -> Element:
        if selector_type == By.ID:
            assert selector_value == "my-manageitems-adlist"
            return MagicMock()
        assert selector_type == By.XPATH
        match = re.search(r'data-adid="(\d+)"', selector_value)
        assert match is not None
        ad_id = int(match.group(1))
        if ad_id not in self.pages[self.page]:
            raise TimeoutError("Ad is on another page")

        async def click() -> None:
            self.attempted.append(ad_id)
            if ad_id == self.failed_id:
                raise TimeoutError("Simulated click timeout")
            self.succeeded.append(ad_id)

        return MagicMock(click = click)

    def published(self, ad_ids:list[int]) -> list[dict[str, Any]]:
        initial_state = "paused" if self.action == "activate" else "active"
        resulting_state = "paused" if self.action == "reserve" else "active"
        return [{
            "id": ad_id,
            "state": resulting_state if ad_id in self.succeeded else initial_state,
            "endDate": (misc.now().date() + timedelta(days = 2)).strftime("%d.%m.%Y"),
        } for ad_id in ad_ids]


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["extend", "reserve", "activate"])
@pytest.mark.parametrize(("requested", "failed_id", "fail_open", "expected_pages", "expected_attempts"), [
    ([30, 20, 10], None, False, [0, 1], [20, 10, 30]),
    ([20, 10], None, False, [0], [20, 10]),
    ([30, 20, 10], 20, False, [0, 1], [20, 10, 30]),
    ([99, 30, 10], None, False, [0, 1, 2], [10, 30]),
    ([20, 20, 10], None, False, [0], [20, 20, 10]),
    ([20, 10], None, True, [], []),
    ([], None, False, [], []),
])
async def test_bulk_command_visits_each_needed_page_once(
    *,
    test_bot:KleinanzeigenBot, tmp_path:Path, base_ad_config:dict[str, Any],
    action:Literal["extend", "reserve", "activate"], requested:list[int], failed_id:int | None,
    fail_open:bool, expected_pages:list[int], expected_attempts:list[int],
) -> None:
    overview = Overview([[20, 10, 777], [30], [40]], failed_id, action)
    ads:list[tuple[str, Ad, dict[str, Any]]] = []
    for index, ad_id in enumerate(requested):
        raw = {**base_ad_config, "id": ad_id, "title": f"Test listing {ad_id}"}
        path = str(tmp_path / f"{index}-{ad_id}.yaml")
        dicts.save_dict(path, raw)
        ads.append((path, Ad.model_validate(raw), raw))
    before = {path: Path(path).read_bytes() for path, _, _ in ads}  # noqa: ASYNC240 Small temporary test files

    async def fetch(*_args:Any, **_kwargs:Any) -> list[dict[str, Any]]:
        return overview.published(requested)

    with (
        patch.object(test_bot, "web_open", side_effect = TimeoutError("Overview unavailable") if fail_open else overview.open) as opened,
        patch.object(test_bot, "web_find", side_effect = overview.find),
        patch.object(test_bot, "web_find_all", side_effect = overview.find_all),
        patch.object(test_bot, "web_scroll_page_down", new_callable = AsyncMock),
        patch.object(test_bot, "web_click", new_callable = AsyncMock),
        patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as sleep,
        patch("kleinanzeigen_bot.published_ads.fetch_published_ads", side_effect = fetch),
    ):
        if action == "extend":
            await extend_flow.extend_ads(test_bot, "https://example.invalid", ads)
        else:
            await reserve_flow.set_reservation_state(test_bot, "https://example.invalid", ads, action = action)

    assert opened.await_count == bool(requested)
    assert overview.visited == expected_pages
    assert overview.attempted == expected_attempts
    assert overview.succeeded == [ad_id for ad_id in expected_attempts if ad_id != failed_id]
    assert sum(not call.args and not call.kwargs for call in sleep.await_args_list) == len(requested)
    for path, ad, raw in ads:
        if action == "extend" and ad.id in overview.succeeded:
            assert raw["updated_on"]
            assert Path(path).read_bytes() != before[path]  # noqa: ASYNC240 Small temporary test file
        else:
            assert Path(path).read_bytes() == before[path]  # noqa: ASYNC240 Small temporary test file
