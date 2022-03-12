"""
Copyright (C) 2022 Sebastian Thomschke and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
"""
import logging, os, shutil, sys
from collections.abc import Callable, Iterable
from typing import Any, Final, TypeVar

from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service as ChromeService, DEFAULT_EXECUTABLE_PATH as DEFAULT_CHROMEDRIVER_PATH
from selenium.webdriver.chromium.options import ChromiumOptions
from selenium.webdriver.chromium.webdriver import ChromiumDriver
from selenium.webdriver.edge.service import Service as EdgeService, DEFAULT_EXECUTABLE_PATH as DEFAULT_EDGEDRIVER_PATH
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait
import selenium_stealth
import webdriver_manager.utils as ChromeDriverManagerUtils
from webdriver_manager.chrome import ChromeDriverManager
from webdriver_manager.microsoft import EdgeChromiumDriverManager
from webdriver_manager.utils import ChromeType

from .utils import ensure, pause

LOG:Final[logging.Logger] = logging.getLogger("kleinanzeigen_bot.selenium_mixin")

T:Final[TypeVar] = TypeVar('T')


class BrowserConfig:

    def __init__(self):
        self.arguments:Iterable[str] = []
        self.binary_location:str = None
        self.extensions:Iterable[str] = []
        self.use_private_window:bool = True
        self.user_data_dir:str = ""
        self.profile_name:str = ""


class SeleniumMixin:

    def __init__(self):
        self.browser_config:Final[BrowserConfig] = BrowserConfig()
        self.webdriver:WebDriver = None

    def create_webdriver_session(self) -> None:
        LOG.info("Creating WebDriver session...")

        def init_browser_options(browser_options:ChromiumOptions):
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

        if not LOG.isEnabledFor(logging.DEBUG):
            os.environ['WDM_LOG_LEVEL'] = '0'  # silence the web driver manager

        # check if a chrome driver is present already
        if shutil.which(DEFAULT_CHROMEDRIVER_PATH):
            self.webdriver = webdriver.Chrome(options = init_browser_options(webdriver.ChromeOptions()))
        elif shutil.which(DEFAULT_EDGEDRIVER_PATH):
            self.webdriver = webdriver.ChromiumEdge(options = init_browser_options(webdriver.EdgeOptions()))
        else:
            # determine browser major version
            if self.browser_config.binary_location:
                chrome_type, chrome_version = self.get_browser_version(self.browser_config.binary_location)
            else:
                chrome_type, chrome_version = self.get_browser_version_from_os()
            chrome_major_version = chrome_version.split(".", 1)[0]

            # download and install matching chrome driver
            if chrome_type == ChromeType.MSEDGE:
                webdriver_mgr = EdgeChromiumDriverManager(cache_valid_range = 14)
                webdriver_mgr.driver.browser_version = chrome_major_version
                webdriver_path = webdriver_mgr.install()
                env = os.environ.copy()
                env["MSEDGEDRIVER_TELEMETRY_OPTOUT"] = "1"  # https://docs.microsoft.com/en-us/microsoft-edge/privacy-whitepaper/#microsoft-edge-driver
                self.webdriver = webdriver.ChromiumEdge(
                    service = EdgeService(webdriver_path, env = env),
                    options = init_browser_options(webdriver.EdgeOptions())
                )
            else:
                webdriver_mgr = ChromeDriverManager(chrome_type = chrome_type, cache_valid_range = 14)
                webdriver_mgr.driver.browser_version = chrome_major_version
                webdriver_path = webdriver_mgr.install()
                self.webdriver = webdriver.Chrome(service = ChromeService(webdriver_path), options = init_browser_options(webdriver.ChromeOptions()))

        # workaround to support Edge, see https://github.com/diprajpatra/selenium-stealth/pull/25
        selenium_stealth.Driver = ChromiumDriver

        selenium_stealth.stealth(self.webdriver,  # https://github.com/diprajpatra/selenium-stealth#args
            languages = ("de-DE", "de", "en-US", "en"),
            platform = "Win32",
            fix_hairline = True,
        )

        LOG.info("New WebDriver session is: %s %s", self.webdriver.session_id, self.webdriver.command_executor._url)  # pylint: disable=protected-access

    def get_browser_version(self, executable_path: str) -> tuple[ChromeType, str]:
        if sys.platform == "win32":
            import win32api  # pylint: disable=import-outside-toplevel,import-error
            # pylint: disable=no-member
            lang, codepage = win32api.GetFileVersionInfo(executable_path, "\\VarFileInfo\\Translation")[0]
            product_name = win32api.GetFileVersionInfo(executable_path, f"\\StringFileInfo\\{lang:04X}{codepage:04X}\\ProductName")
            product_version = win32api.GetFileVersionInfo(executable_path, f"\\StringFileInfo\\{lang:04X}{codepage:04X}\\ProductVersion")
            # pylint: enable=no-member
            match product_name:
                case "Chromium":
                    return (ChromeType.CHROMIUM, product_version)
                case "Microsoft Edge":
                    return (ChromeType.MSEDGE, product_version)
                case _:  # "Google Chrome"
                    return (ChromeType.GOOGLE, product_version)

        if sys.platform.startswith("linux"):
            cmd = ChromeDriverManagerUtils.linux_browser_apps_to_cmd(executable_path)
        else:
            cmd = executable_path + " --version"

        version = ChromeDriverManagerUtils.read_version_from_cmd(cmd, r'\d+\.\d+\.\d+')
        filename = os.path.basename(executable_path).lower()
        if "chromium" in filename:
            return (ChromeType.CHROMIUM, version)
        if "edge" in filename:
            return (ChromeType.MSEDGE, version)
        return (ChromeType.GOOGLE, version)

    def get_browser_version_from_os(self) -> tuple[ChromeType, str]:
        version = ChromeDriverManagerUtils.get_browser_version_from_os(ChromeType.CHROMIUM)
        if version != "UNKNOWN":
            return (ChromeType.CHROMIUM, version)
        LOG.debug("Chromium not found")

        version = ChromeDriverManagerUtils.get_browser_version_from_os(ChromeType.GOOGLE)
        if version != "UNKNOWN":
            return (ChromeType.GOOGLE, version)
        LOG.debug("Google Chrome not found")

        version = ChromeDriverManagerUtils.get_browser_version_from_os(ChromeType.MSEDGE)
        if version != "UNKNOWN":
            return (ChromeType.MSEDGE, version)
        LOG.debug("Microsoft Edge not found")

        return (None, None)

    def web_await(self, condition: Callable[[WebDriver], T], timeout:float = 5, timeout_exception_type: type[Exception] = TimeoutException) -> T:
        """
        Blocks/waits until the given condition is met.

        :param timeout: timeout in seconds
        :raises TimeoutException: if element could not be found within time
        """
        try:
            return WebDriverWait(self.webdriver, timeout).until(condition)
        except TimeoutException as ex:
            if isinstance(ex, timeout_exception_type):
                raise ex
            raise timeout_exception_type from ex

    def web_click(self, selector_type:By, selector_value:str, timeout:float = 5) -> WebElement:
        """
        :param timeout: timeout in seconds
        :raises NoSuchElementException: if element could not be found within time
        """
        elem = self.web_await(EC.element_to_be_clickable((selector_type, selector_value)), timeout, NoSuchElementException)
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
        return self.web_await(EC.presence_of_element_located((selector_type, selector_value)), timeout, NoSuchElementException)

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

    def web_open(self, url:str, timeout:float = 15, reload_if_already_open = False) -> None:
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
    def web_request(self, url:str, method:str = "GET", valid_response_codes:Iterable[int] = [200], headers:dict[str, str] = None) -> dict[str, Any]:
        method = method.upper()
        LOG.debug(" -> HTTP %s [%s]...", method, url)
        response = self.webdriver.execute_async_script(f"""
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

    def web_select(self, selector_type:By, selector_value:str, selected_value:Any, timeout:float = 5) -> WebElement:
        """
        Selects an <option/> of a <select/> HTML element.

        :param timeout: timeout in seconds
        :raises NoSuchElementException: if element could not be found within time
        :raises UnexpectedTagNameException: if element is not a <select> element
        """
        elem = self.web_await(EC.element_to_be_clickable((selector_type, selector_value)), timeout, NoSuchElementException)
        Select(elem).select_by_value(selected_value)
        pause()
        return elem
