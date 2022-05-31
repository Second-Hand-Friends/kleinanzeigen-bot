"""
Copyright (C) 2022 Sebastian Thomschke and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
"""
import logging, os, shutil
from collections.abc import Callable, Iterable
from typing import Any, Final

from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, TimeoutException, WebDriverException
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
import webdriver_manager.core
from webdriver_manager.chrome import ChromeDriverManager
from webdriver_manager.microsoft import EdgeChromiumDriverManager
from webdriver_manager.core.utils import ChromeType, OSType

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


class SeleniumMixin:

    def __init__(self) -> None:
        self.browser_config:Final[BrowserConfig] = BrowserConfig()
        self.webdriver:WebDriver = None

    def _init_browser_options(self, browser_options:ChromiumOptions) -> ChromiumOptions:
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

    def create_webdriver_session(self, *, use_preinstalled_webdriver:bool = True) -> None:
        LOG.info("Creating WebDriver session...")

        if not LOG.isEnabledFor(logging.DEBUG):
            os.environ['WDM_LOG_LEVEL'] = '0'  # silence the web driver manager

        # check if a chrome driver is present already
        if use_preinstalled_webdriver and shutil.which(DEFAULT_CHROMEDRIVER_PATH):
            LOG.info("Using pre-installed Chrome Driver [%s]", shutil.which(DEFAULT_CHROMEDRIVER_PATH))
            self.webdriver = webdriver.Chrome(options = self._init_browser_options(webdriver.ChromeOptions()))
        elif use_preinstalled_webdriver and shutil.which(DEFAULT_EDGEDRIVER_PATH):
            LOG.info("Using pre-installed Edge Driver [%s]", shutil.which(DEFAULT_EDGEDRIVER_PATH))
            self.webdriver = webdriver.ChromiumEdge(options = self._init_browser_options(webdriver.EdgeOptions()))
        else:
            # determine browser major version
            if self.browser_config.binary_location:
                ensure(os.path.exists(self.browser_config.binary_location), f"Specified browser binary [{self.browser_config.binary_location}] does not exist.")
                chrome_type, chrome_version = self.get_browser_version(self.browser_config.binary_location)
            else:
                browser_info = self.find_compatible_browser()
                if browser_info is None:
                    raise AssertionError("No supported browser found!")
                chrome_path, chrome_type, chrome_version = browser_info
                self.browser_config.binary_location = chrome_path
            LOG.info("Using Browser: %s %s [%s]", chrome_type.upper(), chrome_version, self.browser_config.binary_location)
            chrome_major_version = chrome_version.split(".", 1)[0]

            # hack to specify the concrete browser version for which the driver shall be downloaded
            webdriver_manager.core.driver.get_browser_version_from_os = lambda _: chrome_major_version

            # download and install matching chrome driver
            if chrome_type == ChromeType.MSEDGE:
                webdriver_mgr = EdgeChromiumDriverManager(cache_valid_range = 14)
                webdriver_path = webdriver_mgr.install()
                env = os.environ.copy()
                env["MSEDGEDRIVER_TELEMETRY_OPTOUT"] = "1"  # https://docs.microsoft.com/en-us/microsoft-edge/privacy-whitepaper/#microsoft-edge-driver
                self.webdriver = webdriver.ChromiumEdge(
                    service = EdgeService(webdriver_path, env = env),
                    options = self._init_browser_options(webdriver.EdgeOptions())
                )
            else:
                webdriver_mgr = ChromeDriverManager(chrome_type = chrome_type, cache_valid_range = 14)
                webdriver_path = webdriver_mgr.install()
                self.webdriver = webdriver.Chrome(service = ChromeService(webdriver_path), options = self._init_browser_options(webdriver.ChromeOptions()))

        # workaround to support Edge, see https://github.com/diprajpatra/selenium-stealth/pull/25
        selenium_stealth.Driver = ChromiumDriver

        selenium_stealth.stealth(self.webdriver,  # https://github.com/diprajpatra/selenium-stealth#args
            languages = ("de-DE", "de", "en-US", "en"),
            platform = "Win32",
            fix_hairline = True,
        )

        LOG.info("New WebDriver session is: %s %s", self.webdriver.session_id, self.webdriver.command_executor._url)  # pylint: disable=protected-access

    def get_browser_version(self, executable_path: str) -> tuple[ChromeType, str]:
        match webdriver_manager.core.utils.os_name():
            case OSType.WIN:
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

            case OSType.LINUX:
                version_cmd = webdriver_manager.core.utils.linux_browser_apps_to_cmd(f'"{executable_path}"')

            case _:
                version_cmd = f'"{executable_path}" --version'

        filename = os.path.basename(executable_path).lower()
        if "chromium" in filename:
            return (
                ChromeType.CHROMIUM,
                webdriver_manager.core.utils.read_version_from_cmd(version_cmd, webdriver_manager.core.utils.PATTERN[ChromeType.CHROMIUM])
            )
        if "edge" in filename:
            return (
                ChromeType.MSEDGE,
                webdriver_manager.core.utils.read_version_from_cmd(version_cmd, webdriver_manager.core.utils.PATTERN[ChromeType.MSEDGE])
            )
        return (
            ChromeType.GOOGLE,
            webdriver_manager.core.utils.read_version_from_cmd(version_cmd, webdriver_manager.core.utils.PATTERN[ChromeType.GOOGLE])
        )

    def find_compatible_browser(self) -> tuple[str, ChromeType, str] | None:
        match webdriver_manager.core.utils.os_name():
            case OSType.LINUX:
                browser_paths = [
                    shutil.which("chromium"),
                    shutil.which("chromium-browser"),
                    shutil.which("google-chome"),
                    shutil.which("microsoft-edge")
                ]

            case OSType.MAC:
                browser_paths = [
                    "/Applications/Chromium.app/Contents/MacOS/Chromium",
                    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                ]

            case OSType.WIN:
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
                return (browser_path, *self.get_browser_version(browser_path))

        LOG.warning("Installed browser could not be detected")
        return None

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
