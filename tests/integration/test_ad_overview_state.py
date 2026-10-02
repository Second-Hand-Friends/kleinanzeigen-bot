# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Run the ad overview state reset against real sessionStorage on offline HTML."""

import json
from pathlib import Path
from typing import Any

import pytest

from kleinanzeigen_bot.utils.web_scraping_mixin import WebScrapingMixin

# the browser is shared across the session, see conftest.py
pytestmark = [pytest.mark.itest, pytest.mark.slow, pytest.mark.asyncio(loop_scope = "session")]

# any offline page works; about:blank has no origin and therefore no sessionStorage
_OFFLINE_PAGE = Path(__file__).resolve().parents[1] / "fixtures" / "astro_start_page_logged_in_header.html"


async def test_reset_ad_overview_state_drops_only_stored_page_and_search(web:WebScrapingMixin) -> None:
    """Stored page and search are removed, other parameters stay, and odd entries are left alone (#1302)."""
    await web.web_open(_OFFLINE_PAGE.as_uri())

    async def stored() -> Any:
        return await web.web_execute('sessionStorage.getItem("queryParams")')

    async def store(value:str) -> None:
        await web.web_execute(f'sessionStorage.setItem("queryParams", {json.dumps(value)})')

    try:
        await store(json.dumps({"sort": "DEFAULT", "pageSize": "1", "pageNumber": 2, "keyword": "lamp"}))
        await web.reset_ad_overview_state()
        assert json.loads(await stored()) == {"sort": "DEFAULT", "pageSize": "1"}

        await web.web_execute('sessionStorage.removeItem("queryParams")')
        await web.reset_ad_overview_state()
        assert await stored() is None

        await store("not json")
        await web.reset_ad_overview_state()
        assert await stored() == "not json"
    finally:
        await web.web_execute("sessionStorage.clear()")
