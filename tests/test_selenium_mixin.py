"""
SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""
import pytest

from kleinanzeigen_bot.selenium_mixin import SeleniumMixin
from kleinanzeigen_bot import utils


@pytest.mark.itest
def test_webdriver_auto_init():
    selenium_mixin = SeleniumMixin()
    selenium_mixin.browser_config.arguments = ["--no-sandbox"]

    browser_path = selenium_mixin.get_compatible_browser()
    utils.ensure(browser_path is not None, "Browser not auto-detected")

    selenium_mixin.webdriver = None
    selenium_mixin.create_webdriver_session()
    selenium_mixin.webdriver.quit()
