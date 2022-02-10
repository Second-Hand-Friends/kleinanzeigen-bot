"""
Copyright (C) 2022 Sebastian Thomschke and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
"""
from kleinanzeigen_bot.selenium_mixin import SeleniumMixin
from kleinanzeigen_bot import utils


def test_webdriver_auto_init():
    selenium_mixin = SeleniumMixin()

    chrome_type, chrome_version = selenium_mixin.get_browser_version_from_os()
    utils.ensure(chrome_type is not None, "Chrome type not auto-detected")
    utils.ensure(chrome_version is not None, "Chrome version not auto-detected")

    utils.ensure(selenium_mixin.webdriver is None, "Web driver must not be set before create_webdriver_session()")
    selenium_mixin.create_webdriver_session()
    utils.ensure(selenium_mixin.webdriver is not None, "Web driver must be set after create_webdriver_session()")
    selenium_mixin.webdriver.quit()
