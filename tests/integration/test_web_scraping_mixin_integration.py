# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import platform

import pytest

from kleinanzeigen_bot.utils.misc import ensure
from kleinanzeigen_bot.utils.web_scraping_mixin import WebScrapingMixin

pytestmark = pytest.mark.slow

# Configure logging for integration tests
# The main bot already handles nodriver logging via silence_nodriver_logs fixture
# and pytest handles verbosity with -v flag automatically


@pytest.mark.flaky(reruns = 5, reruns_delay = 10)
@pytest.mark.itest
@pytest.mark.asyncio
async def test_init() -> None:
    web_scraping_mixin = WebScrapingMixin()
    if platform.system() == "Linux":
        # required for Ubuntu 24.04 or newer
        web_scraping_mixin.browser_config.arguments.append("--no-sandbox")

    browser_path = web_scraping_mixin.get_compatible_browser()
    ensure(browser_path is not None, "Browser not auto-detected")

    try:
        await web_scraping_mixin.create_browser_session()
    finally:
        await web_scraping_mixin.close_browser_session()
