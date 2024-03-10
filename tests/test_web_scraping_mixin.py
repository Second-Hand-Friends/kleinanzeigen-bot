"""
SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""
import logging, os

import nodriver, pytest

from kleinanzeigen_bot.web_scraping_mixin import WebScrapingMixin
from kleinanzeigen_bot.utils import ensure

if os.environ.get("CI"):
    logging.getLogger("kleinanzeigen_bot").setLevel(logging.DEBUG)
    logging.getLogger("nodriver").setLevel(logging.DEBUG)


async def atest_init() -> None:
    web_scraping_mixin = WebScrapingMixin()

    browser_path = web_scraping_mixin.get_compatible_browser()
    ensure(browser_path is not None, "Browser not auto-detected")

    web_scraping_mixin.close_browser_session()
    try:
        await web_scraping_mixin.create_browser_session()
    finally:
        web_scraping_mixin.close_browser_session()


@pytest.mark.flaky(reruns = 4, reruns_delay = 5)
@pytest.mark.itest
def test_init() -> None:
    nodriver.loop().run_until_complete(atest_init())
