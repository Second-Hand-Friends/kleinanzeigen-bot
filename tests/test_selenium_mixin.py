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

    browser_info = selenium_mixin.find_compatible_browser()
    utils.ensure(browser_info is not None, "Chrome type not auto-detected")

    chrome_path, chrome_type, chrome_version = browser_info
    utils.ensure(chrome_path is not None, "Chrome type not auto-detected")
    utils.ensure(chrome_type is not None, "Chrome type not auto-detected")
    utils.ensure(chrome_version is not None, "Chrome version not auto-detected")

    utils.ensure(selenium_mixin.webdriver is None, "Web driver must not be set before create_webdriver_session()")
    selenium_mixin.create_webdriver_session(use_preinstalled_webdriver = True)
    utils.ensure(selenium_mixin.webdriver is not None, "Web driver must be set after create_webdriver_session()")
    selenium_mixin.webdriver.quit()

    selenium_mixin.webdriver = None
    selenium_mixin.create_webdriver_session(use_preinstalled_webdriver = False)
    selenium_mixin.webdriver.quit()
