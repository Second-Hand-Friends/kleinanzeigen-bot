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


async def atest_belen_conf_evaluation() -> None:
    """Test that window.BelenConf can be evaluated correctly with nodriver."""
    web_scraping_mixin = WebScrapingMixin()
    if platform.system() == "Linux":
        # required for Ubuntu 24.04 or newer
        cast(list[str], web_scraping_mixin.browser_config.arguments).append("--no-sandbox")

    browser_path = web_scraping_mixin.get_compatible_browser()
    ensure(browser_path is not None, "Browser not auto-detected")

    web_scraping_mixin.close_browser_session()
    try:
        await web_scraping_mixin.create_browser_session()

        # Navigate to a simple page that can execute JavaScript
        await web_scraping_mixin.web_open("data:text/html,<html><body><script>window.BelenConf = {test: 'data', universalAnalyticsOpts: {dimensions: {dimension92: 'test', dimension108: 'art_s:test'}}};</script></body></html>")
        await web_scraping_mixin.web_sleep(1000, 2000)  # Wait for page to load

        # Test JavaScript evaluation - this is the critical test for nodriver 0.40-0.44 issues
        belen_conf = await web_scraping_mixin.web_execute("window.BelenConf")

        # Verify the evaluation worked
        assert belen_conf is not None, "window.BelenConf evaluation returned None"

        # In nodriver 0.47+, JavaScript objects are returned as RemoteObject instances
        # We need to check if it's either a dict (old behavior) or RemoteObject (new behavior)
        is_dict = isinstance(belen_conf, dict)
        is_remote_object = hasattr(belen_conf, 'deep_serialized_value') and belen_conf.deep_serialized_value is not None

        assert is_dict or is_remote_object, f"window.BelenConf should be a dict or RemoteObject, got {type(belen_conf)}"

        if is_dict:
            # Old behavior - direct dict access
            assert "test" in belen_conf, "window.BelenConf should contain test data"
            assert "universalAnalyticsOpts" in belen_conf, "window.BelenConf should contain universalAnalyticsOpts"
        else:
            # New behavior - RemoteObject with deep_serialized_value
            assert hasattr(belen_conf, 'deep_serialized_value'), "RemoteObject should have deep_serialized_value"
            assert belen_conf.deep_serialized_value is not None, "deep_serialized_value should not be None"

        if is_dict:
            print(f"✅ BelenConf evaluation successful: {list(belen_conf.keys())}")
        else:
            print(f"✅ BelenConf evaluation successful: RemoteObject with deep_serialized_value")

    finally:
        web_scraping_mixin.close_browser_session()


@pytest.mark.flaky(reruns = 4, reruns_delay = 5)
@pytest.mark.itest
def test_belen_conf_evaluation() -> None:
    """Test that window.BelenConf JavaScript evaluation works correctly.

    This test specifically validates the issue that affected nodriver 0.40-0.44
    where window.BelenConf evaluation would fail.
    """
    nodriver.loop().run_until_complete(atest_belen_conf_evaluation())
