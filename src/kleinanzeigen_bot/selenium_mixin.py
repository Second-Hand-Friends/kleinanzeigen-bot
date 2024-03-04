"""
SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""
import logging, os, platform, shutil, time
from collections.abc import Callable, Iterable
from typing import Any, Final, TypeVar

from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.chromium.options import ChromiumOptions
from selenium.webdriver.chromium.webdriver import ChromiumDriver
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait
import selenium_stealth
from .utils import ensure, pause, T

LOG:Final[logging.Logger] = logging.getLogger("kleinanzeigen_bot.selenium_mixin")


class BrowserConfig:

    def __init__(self) -> None:
        self.arguments:Iterable[str] = []
        self.binary_location:str | None = None
        self.extensions:Iterable[str] = []
        self.use_private_window:bool = True
        self.user_data_dir:str = ""
        self.profile_name:str = ""


CHROMIUM_OPTIONS = TypeVar('CHROMIUM_OPTIONS', bound = ChromiumOptions)  # pylint: disable=invalid-name


class SeleniumMixin:

    def __init__(self) -> None:
        os.environ["SE_AVOID_STATS"] = "true"  # see https://www.selenium.dev/documentation/selenium_manager/
        self.browser_config:Final[BrowserConfig] = BrowserConfig()
        self.webdriver:WebDriver = None

    def _init_browser_options(self, browser_options:CHROMIUM_OPTIONS) -> CHROMIUM_OPTIONS:
        if self.browser_config.use_private_window:
            if isinstance(browser_options, webdriver.EdgeOptions):
                browser_options.add_argument("-inprivate")
            else:
                browser_options.add_argument("--incognito")

        if self.browser_config.user_data_dir:
            LOG.info(" -> Browser User Data Dir: %s", self.browser_config.user_data_dir)
            browser_options.add_argument(f"--user-data-dir={self.browser_config.user_data_dir}")

        if self.browser_config.profile_name:
            LOG.info(" -> Browser Profile Name: %s", self.browser_config.profile_name)
            browser_options.add_argument(f"--profile-directory={self.browser_config.profile_name}")

        browser_options.add_argument("--disable-crash-reporter")
        browser_options.add_argument("--no-first-run")
        browser_options.add_argument("--no-service-autorun")
        for chrome_option in self.browser_config.arguments:
            LOG.info(" -> Custom chrome argument: %s", chrome_option)
            browser_options.add_argument(chrome_option)
        LOG.debug("Effective browser arguments: %s", browser_options.arguments)

        for crx_extension in self.browser_config.extensions:
            ensure(os.path.exists(crx_extension), f"Configured extension-file [{crx_extension}] does not exist.")
            browser_options.add_extension(crx_extension)
        LOG.debug("Effective browser extensions: %s", browser_options.extensions)

        browser_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        browser_options.add_experimental_option("useAutomationExtension", False)
        browser_options.add_experimental_option("prefs", {
            "credentials_enable_service": False,
            "profile.password_manager_enabled": False,
            "profile.default_content_setting_values.notifications": 2,  # 1 = allow, 2 = block browser notifications
            "devtools.preferences.currentDockState": "\"bottom\""
        })

        if not LOG.isEnabledFor(logging.DEBUG):
            browser_options.add_argument("--log-level=3")  # INFO: 0, WARNING: 1, ERROR: 2, FATAL: 3

        LOG.debug("Effective experimental options: %s", browser_options.experimental_options)

        if self.browser_config.binary_location:
            browser_options.binary_location = self.browser_config.binary_location
            LOG.info(" -> Chrome binary location: %s", self.browser_config.binary_location)
        return browser_options

    def create_webdriver_session(self) -> None:
        LOG.info("Creating WebDriver session...")

        if self.browser_config.binary_location:
            ensure(os.path.exists(self.browser_config.binary_location), f"Specified browser binary [{self.browser_config.binary_location}] does not exist.")
        else:
            self.browser_config.binary_location = self.get_compatible_browser()

        if "edge" in self.browser_config.binary_location.lower():
            os.environ["MSEDGEDRIVER_TELEMETRY_OPTOUT"] = "1"  # https://docs.microsoft.com/en-us/microsoft-edge/privacy-whitepaper/#microsoft-edge-driver
            browser_options = self._init_browser_options(webdriver.EdgeOptions())
            browser_options.binary_location = self.browser_config.binary_location
            self.webdriver = webdriver.Edge(options = browser_options)
        else:
            browser_options = self._init_browser_options(webdriver.ChromeOptions())
            browser_options.binary_location = self.browser_config.binary_location
            self.webdriver = webdriver.Chrome(options = browser_options)

        LOG.info(" -> Chrome driver: %s", self.webdriver.service.path)

        # workaround to support Edge, see https://github.com/diprajpatra/selenium-stealth/pull/25
        selenium_stealth.Driver = ChromiumDriver

        selenium_stealth.stealth(self.webdriver,  # https://github.com/diprajpatra/selenium-stealth#args
            languages = ("de-DE", "de", "en-US", "en"),
            platform = "Win32",
            fix_hairline = True,
        )

        LOG.info("New WebDriver session is: %s %s", self.webdriver.session_id, self.webdriver.command_executor._url)  # pylint: disable=protected-access

    def get_compatible_browser(self) -> str | None:
        match platform.system():
            case "Linux":
                browser_paths = [
                    shutil.which("chromium"),
                    shutil.which("chromium-browser"),
                    shutil.which("google-chrome"),
                    shutil.which("microsoft-edge")
                ]

            case "Darwin":
                browser_paths = [
                    "/Applications/Chromium.app/Contents/MacOS/Chromium",
                    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                ]

            case "Windows":
                browser_paths = [
                    os.environ.get("ProgramFiles", "C:\\Program Files") + r'\Microsoft\Edge\Application\msedge.exe',
                    os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)") + r'\Microsoft\Edge\Application\msedge.exe',

                    os.environ["ProgramFiles"] + r'\Chromium\Application\chrome.exe',
                    os.environ["ProgramFiles(x86)"] + r'\Chromium\Application\chrome.exe',
                    os.environ["LOCALAPPDATA"] + r'\Chromium\Application\chrome.exe',

                    os.environ["ProgramFiles"] + r'\Chrome\Application\chrome.exe',
                    os.environ["ProgramFiles(x86)"] + r'\Chrome\Application\chrome.exe',
                    os.environ["LOCALAPPDATA"] + r'\Chrome\Application\chrome.exe',

                    shutil.which("msedge.exe"),
                    shutil.which("chromium.exe"),
                    shutil.which("chrome.exe")
                ]

            case _ as os_name:
                LOG.warning("Installed browser for OS [%s] could not be detected", os_name)
                return None

        for browser_path in browser_paths:
            if browser_path and os.path.isfile(browser_path):
                return browser_path

        raise AssertionError("Installed browser could not be detected")

    def web_await(self, condition: Callable[[WebDriver], T], timeout:float = 5, exception_on_timeout: Callable[[], Exception] | None = None) -> T:
        """
        Blocks/waits until the given condition is met.

        :param timeout: timeout in seconds
        :raises TimeoutException: if element could not be found within time
        """
        max_attempts = 2
        for attempt in range(max_attempts + 1)[1:]:
            try:
                return WebDriverWait(self.webdriver, timeout).until(condition)  # type: ignore[no-any-return]
            except TimeoutException as ex:
                if exception_on_timeout:
                    raise exception_on_timeout() from ex
                raise ex
            except WebDriverException as ex:
                # temporary workaround for:
                # - https://groups.google.com/g/chromedriver-users/c/Z_CaHJTJnLw
                # - https://bugs.chromium.org/p/chromedriver/issues/detail?id=4048
                if ex.msg == "target frame detached" and attempt < max_attempts:
                    LOG.warning(ex)
                else:
                    raise ex

        raise AssertionError("Should never be reached.")

    def web_click(self, selector_type:By, selector_value:str, timeout:float = 5) -> WebElement:
        """
        :param timeout: timeout in seconds
        :raises NoSuchElementException: if element could not be found within time
        """
        elem = self.web_await(
            EC.element_to_be_clickable((selector_type, selector_value)),
            timeout,
            lambda: NoSuchElementException(f"Element {selector_type}:{selector_value} not found or not clickable")
        )
        elem.click()
        pause()
        return elem

    def web_execute(self, javascript:str) -> Any:
        """
        Executes the given JavaScript code in the context of the current page.

        :return: The command's JSON response
        """
        return self.webdriver.execute_script(javascript)

    def web_find(self, selector_type:By, selector_value:str, timeout:float = 5) -> WebElement:
        """
        Locates an HTML element.

        :param timeout: timeout in seconds
        :raises NoSuchElementException: if element could not be found within time
        """
        return self.web_await(
            EC.presence_of_element_located((selector_type, selector_value)),
            timeout,
            lambda: NoSuchElementException(f"Element {selector_type}='{selector_value}' not found")
        )

    def web_input(self, selector_type:By, selector_value:str, text:str, timeout:float = 5) -> WebElement:
        """
        Enters text into an HTML input field.

        :param timeout: timeout in seconds
        :raises NoSuchElementException: if element could not be found within time
        """
        input_field = self.web_find(selector_type, selector_value, timeout)
        input_field.clear()
        input_field.send_keys(text)
        pause()
        return input_field

    def web_open(self, url:str, timeout:float = 15, reload_if_already_open:bool = False) -> None:
        """
        :param url: url to open in browser
        :param timeout: timespan in seconds within the page needs to be loaded
        :param reload_if_already_open: if False does nothing if the URL is already open in the browser
        :raises TimeoutException: if page did not open within given timespan
        """
        LOG.debug(" -> Opening [%s]...", url)
        if not reload_if_already_open and url == self.webdriver.current_url:
            LOG.debug("  => skipping, [%s] is already open", url)
            return
        self.webdriver.get(url)
        WebDriverWait(self.webdriver, timeout).until(lambda _: self.web_execute("return document.readyState") == "complete")

    # pylint: disable=dangerous-default-value
    def web_request(self, url:str, method:str = "GET", valid_response_codes:Iterable[int] = [200], headers:dict[str, str] | None = None) -> dict[str, Any]:
        method = method.upper()
        LOG.debug(" -> HTTP %s [%s]...", method, url)
        response:dict[str, Any] = self.webdriver.execute_async_script(f"""
            var callback = arguments[arguments.length - 1];
            fetch("{url}", {{
                method: "{method}",
                redirect: "follow",
                headers: {headers or {}}
            }})
            .then(response => response.text().then(responseText => {{
                headers = {{}};
                response.headers.forEach((v, k) => headers[k] = v);
                callback({{
                    "statusCode": response.status,
                    "statusMessage": response.statusText,
                    "headers": headers,
                    "content": responseText
                }})
            }}))
        """)
        ensure(
            response["statusCode"] in valid_response_codes,
            f'Invalid response "{response["statusCode"]} response["statusMessage"]" received for HTTP {method} to {url}'
        )
        return response
    # pylint: enable=dangerous-default-value

    def web_scroll_page_down(self, scroll_length: int = 10, scroll_speed: int = 10000, scroll_back_top: bool = False) -> None:
        """
        Smoothly scrolls the current web page down.

        :param scroll_length: the length of a single scroll iteration, determines smoothness of scrolling, lower is smoother
        :param scroll_speed: the speed of scrolling, higher is faster
        :param scroll_back_top: whether to scroll the page back to the top after scrolling to the bottom
        """
        current_y_pos = 0
        bottom_y_pos: int = self.webdriver.execute_script('return document.body.scrollHeight;')  # get bottom position by JS
        while current_y_pos < bottom_y_pos:  # scroll in steps until bottom reached
            current_y_pos += scroll_length
            self.webdriver.execute_script(f'window.scrollTo(0, {current_y_pos});')  # scroll one step
            time.sleep(scroll_length / scroll_speed)

        if scroll_back_top:  # scroll back to top in same style
            while current_y_pos > 0:
                current_y_pos -= scroll_length
                self.webdriver.execute_script(f'window.scrollTo(0, {current_y_pos});')
                time.sleep(scroll_length / scroll_speed / 2)  # double speed

    def web_select(self, selector_type:By, selector_value:str, selected_value:Any, timeout:float = 5) -> WebElement:
        """
        Selects an <option/> of a <select/> HTML element.

        :param timeout: timeout in seconds
        :raises NoSuchElementException: if element could not be found within time
        :raises UnexpectedTagNameException: if element is not a <select> element
        """
        elem = self.web_await(
            EC.element_to_be_clickable((selector_type, selector_value)),
            timeout,
            lambda: NoSuchElementException(f"Element {selector_type}='{selector_value}' not found or not clickable")
        )
        Select(elem).select_by_value(selected_value)
        pause()
        return elem
