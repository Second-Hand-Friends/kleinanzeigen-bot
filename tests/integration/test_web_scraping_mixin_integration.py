# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import os
import platform
from typing import cast

import nodriver
import pytest

from kleinanzeigen_bot.utils import loggers
from kleinanzeigen_bot.utils.misc import ensure
from kleinanzeigen_bot.utils.web_scraping_mixin import WebScrapingMixin

if os.environ.get("CI"):
    loggers.get_logger("kleinanzeigen_bot").setLevel(loggers.DEBUG)
    loggers.get_logger("nodriver").setLevel(loggers.DEBUG)


async def atest_init() -> None:
    web_scraping_mixin = WebScrapingMixin()
    if platform.system() == "Linux":
        # required for Ubuntu 24.04 or newer
        cast(list[str], web_scraping_mixin.browser_config.arguments).append("--no-sandbox")

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
