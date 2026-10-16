# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Run the extraction page scripts in the real browser against inline HTML."""

import html
import json
from collections.abc import Awaitable, Callable

import pytest

from kleinanzeigen_bot.utils import page_scripts
from kleinanzeigen_bot.utils.web_scraping_mixin import WebScrapingMixin

pytestmark = [pytest.mark.itest, pytest.mark.slow, pytest.mark.asyncio(loop_scope = "session")]

ShowHtml = Callable[..., Awaitable[None]]


def _island(props:object) -> str:
    return f'<astro-island props="{html.escape(json.dumps(props))}"></astro-island>'


async def test_ad_island_props_returns_the_island_with_ad_data(web:WebScrapingMixin, show_html:ShowHtml) -> None:
    """Of several islands, the one carrying ad data is returned, HTML-decoded."""
    ad = {"data": [0, {"userDetails": [0, {"name": [0, "Max & Co"]}]}]}
    await show_html(_island({"nav": [0, "x"]}) + _island(ad))
    assert json.loads(await web.web_execute(page_scripts.call("ad_island_props"))) == ad


async def test_ad_island_props_without_ad_data_is_null(web:WebScrapingMixin, show_html:ShowHtml) -> None:
    """Islands without ad data are ignored."""
    await show_html(_island({"nav": [0, "x"]}) + "<astro-island></astro-island>")
    assert await web.web_execute(page_scripts.call("ad_island_props")) is None
