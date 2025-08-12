# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import asyncio, enum, inspect, json, os, platform, secrets, shutil, subprocess, urllib.request  # isort: skip # noqa: S404
from collections.abc import Callable, Coroutine, Iterable
from gettext import gettext as _
from typing import Any, Final, cast

try:
    from typing import Never  # type: ignore[attr-defined,unused-ignore] # mypy
except ImportError:
    from typing import NoReturn as Never  # Python <3.11

import nodriver, psutil  # isort: skip
from nodriver.core.browser import Browser
from nodriver.core.config import Config
from nodriver.core.element import Element
from nodriver.core.tab import Tab as Page

from . import loggers, net
from .chrome_version_detector import (
    detect_chrome_version_from_binary,
    get_chrome_version_diagnostic_info,
    validate_chrome_136_configuration,
)
from .misc import T, ensure

__all__ = [
    "Browser",
    "BrowserConfig",
    "By",
    "Element",
    "Page",
    "Is",
    "WebScrapingMixin",
]

LOG:Final[loggers.Logger] = loggers.get_logger(__name__)

# see https://api.jquery.com/category/selectors/
METACHAR_ESCAPER:Final[dict[int, str]] = str.maketrans({ch: f"\\{ch}" for ch in '!"#$%&\'()*+,./:;<=>?@[\\]^`{|}~'})


def _is_admin() -> bool:
    """Check if the current process is running with admin/root privileges."""
    try:
        if hasattr(os, "geteuid"):
            result = os.geteuid() == 0
            return bool(result)
        return False
    except AttributeError:
        return False


class By(enum.Enum):
    ID = enum.auto()
    CLASS_NAME = enum.auto()
    CSS_SELECTOR = enum.auto()
    TAG_NAME = enum.auto()
    TEXT = enum.auto()
    XPATH = enum.auto()


class Is(enum.Enum):
    CLICKABLE = enum.auto()
    DISPLAYED = enum.auto()
    DISABLED = enum.auto()
    READONLY = enum.auto()
    SELECTED = enum.auto()


class BrowserConfig:

    def __init__(self) -> None:
        self.arguments:Iterable[str] = []
        self.binary_location:str | None = None
        self.extensions:Iterable[str] = []
        self.use_private_window:bool = True
        self.user_data_dir:str | None = None
        self.profile_name:str | None = None


class WebScrapingMixin:

    def __init__(self) -> None:
        self.browser_config:Final[BrowserConfig] = BrowserConfig()
        self.browser:Browser = None  # pyright: ignore[reportAttributeAccessIssue]
        self.page:Page = None  # pyright: ignore[reportAttributeAccessIssue]

    async def create_browser_session(self) -> None:
        LOG.info("Creating Browser session...")

        if self.browser_config.binary_location:
            ensure(os.path.exists(self.browser_config.binary_location), f"Specified browser binary [{self.browser_config.binary_location}] does not exist.")
        else:
            self.browser_config.binary_location = self.get_compatible_browser()
        LOG.info(" -> Browser binary location: %s", self.browser_config.binary_location)

        # Chrome version detection and validation
        await self._validate_chrome_version_configuration()

        ########################################################
        # check if an existing browser instance shall be used...
        ########################################################
        remote_host = "127.0.0.1"
        remote_port = 0
        for arg in self.browser_config.arguments:
            if arg.startswith("--remote-debugging-host="):
                remote_host = arg.split("=", maxsplit = 1)[1]
            if arg.startswith("--remote-debugging-port="):
                remote_port = int(arg.split("=", maxsplit = 1)[1])

        if remote_port > 0:
            LOG.info("Using existing browser process at %s:%s", remote_host, remote_port)

            # Enhanced port checking with retry logic
            port_available = await self._check_port_with_retry(remote_host, remote_port)
            ensure(port_available,
                f"Browser process not reachable at {remote_host}:{remote_port}. "
                f"Start the browser with --remote-debugging-port={remote_port} or remove this port from your config.yaml. "
                f"Make sure the browser is running and the port is not blocked by firewall.")

            try:
                cfg = Config(
                    browser_executable_path = self.browser_config.binary_location  # actually not necessary but nodriver fails without
                )
                cfg.host = remote_host
                cfg.port = remote_port
                self.browser = await nodriver.start(cfg)
                LOG.info("New Browser session is %s", self.browser.websocket_url)
                return
            except Exception as e:
                error_msg = str(e)
                if "root" in error_msg.lower():
                    LOG.error("Failed to connect to browser. This error often occurs when:")
                    LOG.error("1. Running as root user (try running as regular user)")
                    LOG.error("2. Browser profile is locked or in use by another process")
                    LOG.error("3. Insufficient permissions to access the browser profile")
                    LOG.error("4. Browser is not properly started with remote debugging enabled")
                    LOG.error("")
                    LOG.error("Troubleshooting steps:")
                    LOG.error("1. Close all browser instances and try again")
                    LOG.error("2. Remove the user_data_dir configuration temporarily")
                    LOG.error("3. Start browser manually with: %s --remote-debugging-port=%d",
                             self.browser_config.binary_location, remote_port)
                    LOG.error("4. Check if any antivirus or security software is blocking the connection")
                raise

        ########################################################
        # configure and initialize new browser instance...
        ########################################################

        # default_browser_args: @ https://github.com/ultrafunkamsterdam/nodriver/blob/main/nodriver/core/config.py
        # https://peter.sh/experiments/chromium-command-line-switches/
        # https://github.com/GoogleChrome/chrome-launcher/blob/main/docs/chrome-flags-for-tools.md
        browser_args = [
            # "--disable-dev-shm-usage", # https://stackoverflow.com/a/50725918/5116073
            "--disable-crash-reporter",
            "--disable-domain-reliability",
            "--disable-sync",
            "--no-experiments",
            "--disable-search-engine-choice-screen",

            "--disable-features=MediaRouter",
            "--use-mock-keychain",

            "--test-type",  # https://stackoverflow.com/a/36746675/5116073
            # https://chromium.googlesource.com/chromium/src/+/master/net/dns/README.md#request-remapping
            '--host-resolver-rules="MAP connect.facebook.net 127.0.0.1, MAP securepubads.g.doubleclick.net 127.0.0.1, MAP www.googletagmanager.com 127.0.0.1"'
        ]

        is_edge = "edge" in self.browser_config.binary_location.lower()

        if is_edge:
            os.environ["MSEDGEDRIVER_TELEMETRY_OPTOUT"] = "1"  # https://docs.microsoft.com/en-us/microsoft-edge/privacy-whitepaper/#microsoft-edge-driver

        if self.browser_config.use_private_window:
            browser_args.append("-inprivate" if is_edge else "--incognito")

        if self.browser_config.profile_name:
            LOG.info(" -> Browser profile name: %s", self.browser_config.profile_name)
            browser_args.append(f"--profile-directory={self.browser_config.profile_name}")

        for browser_arg in self.browser_config.arguments:
            LOG.info(" -> Custom Browser argument: %s", browser_arg)
            browser_args.append(browser_arg)

        if not loggers.is_debug(LOG):
            browser_args.append("--log-level=3")  # INFO: 0, WARNING: 1, ERROR: 2, FATAL: 3

        if self.browser_config.user_data_dir:
            LOG.info(" -> Browser user data dir: %s", self.browser_config.user_data_dir)

        cfg = Config(
            headless = False,
            browser_executable_path = self.browser_config.binary_location,
            browser_args = browser_args,
            user_data_dir = self.browser_config.user_data_dir
        )

        # already logged by nodriver:
        # LOG.debug("-> Effective browser arguments: \n\t\t%s", "\n\t\t".join(cfg.browser_args))

        # Enhanced profile directory handling
        if cfg.user_data_dir:
            profile_dir = os.path.join(cfg.user_data_dir, self.browser_config.profile_name or "Default")
            os.makedirs(profile_dir, exist_ok = True)
            prefs_file = os.path.join(profile_dir, "Preferences")
            if not os.path.exists(prefs_file):
                LOG.info(" -> Setting chrome prefs [%s]...", prefs_file)
                with open(prefs_file, "w", encoding = "UTF-8") as fd:
                    json.dump({
                        "credentials_enable_service": False,
                        "enable_do_not_track": True,
                        "google": {
                            "services": {
                                "consented_to_sync": False
                            }
                        },
                        "profile": {
                            "default_content_setting_values": {
                                "popups": 0,
                                "notifications": 2  # 1 = allow, 2 = block browser notifications
                            },
                            "password_manager_enabled": False
                        },
                        "signin": {
                            "allowed": False
                        },
                        "translate_site_blacklist": [
                            "www.kleinanzeigen.de"
                        ],
                        "devtools": {
                            "preferences": {
                                "currentDockState": '"bottom"'
                            }
                        }
                    }, fd)

        # load extensions
        for crx_extension in self.browser_config.extensions:
            LOG.info(" -> Adding Browser extension: [%s]", crx_extension)
            ensure(os.path.exists(crx_extension), f"Configured extension-file [{crx_extension}] does not exist.")
            cfg.add_extension(crx_extension)

        try:
            self.browser = await nodriver.start(cfg)
            LOG.info("New Browser session is %s", self.browser.websocket_url)
        except Exception as e:
            error_msg = str(e)
            if "root" in error_msg.lower():
                LOG.error("Failed to start browser. This error often occurs when:")
                LOG.error("1. Running as root user (try running as regular user)")
                LOG.error("2. Browser profile is locked or in use by another process")
                LOG.error("3. Insufficient permissions to access the browser profile")
                LOG.error("4. Browser binary is not executable or missing")
                LOG.error("")
                LOG.error("Troubleshooting steps:")
                LOG.error("1. Close all browser instances and try again")
                LOG.error("2. Remove the user_data_dir configuration temporarily")
                LOG.error("3. Try running without profile configuration")
                LOG.error("4. Check browser binary permissions: %s", self.browser_config.binary_location)
                LOG.error("5. Check if any antivirus or security software is blocking the browser")
            raise

    async def _check_port_with_retry(self, host:str, port:int, max_retries:int = 3, retry_delay:float = 1.0) -> bool:
        """
        Check if a port is open with retry logic.

        Args:
            host: Host to check
            port: Port to check
            max_retries: Maximum number of retry attempts
            retry_delay: Delay between retries in seconds

        Returns:
            True if port is open, False otherwise
        """
        for attempt in range(max_retries):
            if net.is_port_open(host, port):
                return True

            if attempt < max_retries - 1:
                LOG.debug("Port %s:%s not available, retrying in %.1f seconds (attempt %d/%d)",
                         host, port, retry_delay, attempt + 1, max_retries)
                await asyncio.sleep(retry_delay)

        return False

    def diagnose_browser_issues(self) -> None:
        """
        Diagnose common browser connection issues and provide troubleshooting information.
        """
        LOG.info("=== Browser Connection Diagnostics ===")

        # Check browser binary
        if self.browser_config.binary_location:
            if os.path.exists(self.browser_config.binary_location):
                LOG.info("(ok) Browser binary exists: %s", self.browser_config.binary_location)
                if os.access(self.browser_config.binary_location, os.X_OK):
                    LOG.info("(ok) Browser binary is executable")
                else:
                    LOG.error("(fail) Browser binary is not executable")
            else:
                LOG.error("(fail) Browser binary not found: %s", self.browser_config.binary_location)
        else:
            browser_path = self.get_compatible_browser()
            if browser_path:
                LOG.info("(ok) Auto-detected browser: %s", browser_path)
                # Set the binary location for Chrome version detection
                self.browser_config.binary_location = browser_path
            else:
                LOG.error("(fail) No compatible browser found")

        # Check user data directory
        if self.browser_config.user_data_dir:
            if os.path.exists(self.browser_config.user_data_dir):
                LOG.info("(ok) User data directory exists: %s", self.browser_config.user_data_dir)
                if os.access(self.browser_config.user_data_dir, os.R_OK | os.W_OK):
                    LOG.info("(ok) User data directory is readable and writable")
                else:
                    LOG.error("(fail) User data directory permissions issue")
            else:
                LOG.info("(info) User data directory does not exist (will be created): %s", self.browser_config.user_data_dir)

        # Check for remote debugging port
        remote_port = 0
        for arg in self.browser_config.arguments:
            if arg.startswith("--remote-debugging-port="):
                remote_port = int(arg.split("=", maxsplit = 1)[1])
                break

        if remote_port > 0:
            LOG.info("(info) Remote debugging port configured: %d", remote_port)
            if net.is_port_open("127.0.0.1", remote_port):
                LOG.info("(ok) Remote debugging port is open")
                # Try to get more information about the debugging endpoint
                try:
                    response = urllib.request.urlopen(f"http://127.0.0.1:{remote_port}/json/version", timeout = 2)
                    version_info = json.loads(response.read().decode())
                    LOG.info("(ok) Remote debugging API accessible - Browser: %s", version_info.get("Browser", "Unknown"))
                except Exception as e:
                    LOG.warning("(fail) Remote debugging port is open but API not accessible: %s", str(e))
                    LOG.info("  This might indicate a browser update issue or configuration problem")
            else:
                LOG.error("(fail) Remote debugging port is not open")
                LOG.info("  Make sure browser is started with: --remote-debugging-port=%d", remote_port)

        # Check for running browser processes
        browser_processes = []
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                if proc.info["name"] and any(browser in proc.info["name"].lower() for browser in ["chrome", "chromium", "edge"]):
                    browser_processes.append(proc.info)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        if browser_processes:
            LOG.info("(info) Found %d browser processes running", len(browser_processes))
            for proc in browser_processes[:3]:  # Show first 3
                LOG.info("  - PID %d: %s", proc["pid"], proc["name"])
        else:
            LOG.info("(info) No browser processes currently running")

        # Platform-specific checks
        if platform.system() == "Windows":
            LOG.info("(info) Windows detected - check Windows Defender and antivirus software")
        elif platform.system() == "Darwin":
            LOG.info("(info) macOS detected - check Gatekeeper and security settings")
        elif platform.system() == "Linux":
            LOG.info("(info) Linux detected - check if running as root (not recommended)")
            if _is_admin():
                LOG.error("(fail) Running as root - this can cause browser connection issues")

        # Chrome version detection and validation
        self._diagnose_chrome_version_issues(remote_port)

        LOG.info("=== End Diagnostics ===")

    def close_browser_session(self) -> None:
        if self.browser:
            LOG.debug("Closing Browser session...")
            self.page = None  # pyright: ignore[reportAttributeAccessIssue]
            browser_process = psutil.Process(self.browser._process_pid)  # noqa: SLF001 Private member accessed
            browser_children:list[psutil.Process] = browser_process.children()
            self.browser.stop()
            for p in browser_children:
                if p.is_running():
                    p.kill()  # terminate orphaned browser processes
            self.browser = None  # pyright: ignore[reportAttributeAccessIssue]

    def get_compatible_browser(self) -> str:
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
                    os.environ.get("PROGRAMFILES", "C:\\Program Files") + r"\Microsoft\Edge\Application\msedge.exe",
                    os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)") + r"\Microsoft\Edge\Application\msedge.exe",

                    os.environ["PROGRAMFILES"] + r"\Chromium\Application\chrome.exe",
                    os.environ["PROGRAMFILES(X86)"] + r"\Chromium\Application\chrome.exe",
                    os.environ["LOCALAPPDATA"] + r"\Chromium\Application\chrome.exe",

                    os.environ["PROGRAMFILES"] + r"\Chrome\Application\chrome.exe",
                    os.environ["PROGRAMFILES(X86)"] + r"\Chrome\Application\chrome.exe",
                    os.environ["LOCALAPPDATA"] + r"\Chrome\Application\chrome.exe",

                    shutil.which("msedge.exe"),
                    shutil.which("chromium.exe"),
                    shutil.which("chrome.exe")
                ]

            case _ as os_name:
                raise AssertionError(_("Installed browser for OS %s could not be detected") % os_name)

        for browser_path in browser_paths:
            if browser_path and os.path.isfile(browser_path):
                return browser_path

        raise AssertionError(_("Installed browser could not be detected"))

    async def web_await(self, condition:Callable[[], T | Never | Coroutine[Any, Any, T | Never]], *,
            timeout:int | float = 5, timeout_error_message:str = "") -> T:
        """
        Blocks/waits until the given condition is met.

        :param timeout: timeout in seconds
        :raises TimeoutError: if element could not be found within time
        """
        loop = asyncio.get_running_loop()
        start_at = loop.time()

        while True:
            await self.page
            ex:Exception | None = None
            try:
                result_raw = condition()
                result:T = cast(T, await result_raw if inspect.isawaitable(result_raw) else result_raw)
                if result:
                    return result
            except Exception as ex1:
                ex = ex1
            if loop.time() - start_at > timeout:
                if ex:
                    raise ex
                raise TimeoutError(timeout_error_message or f"Condition not met within {timeout} seconds")
            await self.page.sleep(0.5)

    async def web_check(self, selector_type:By, selector_value:str, attr:Is, *, timeout:int | float = 5) -> bool:
        """
        Locates an HTML element and returns a state.

        :param timeout: timeout in seconds
        :raises TimeoutError: if element could not be found within time
        """

        def is_disabled(elem:Element) -> bool:
            return elem.attrs.get("disabled") is not None

        async def is_displayed(elem:Element) -> bool:
            return cast(bool, await elem.apply("""
                function (element) {
                    var style = window.getComputedStyle(element);
                    return style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && style.opacity !== '0'
                        && element.offsetWidth > 0
                        && element.offsetHeight > 0
                }
            """))

        elem:Element = await self.web_find(selector_type, selector_value, timeout = timeout)

        match attr:
            case Is.CLICKABLE:
                return not is_disabled(elem) or await is_displayed(elem)
            case Is.DISPLAYED:
                return await is_displayed(elem)
            case Is.DISABLED:
                return is_disabled(elem)
            case Is.READONLY:
                return elem.attrs.get("readonly") is not None
            case Is.SELECTED:
                return cast(bool, await elem.apply("""
                    function (element) {
                        if (element.tagName.toLowerCase() === 'input') {
                            if (element.type === 'checkbox' || element.type === 'radio') {
                                return element.checked
                            }
                        }
                        return false
                    }
                """))
        raise AssertionError(_("Unsupported attribute: %s") % attr)

    async def web_click(self, selector_type:By, selector_value:str, *, timeout:int | float = 5) -> Element:
        """
        Locates an HTML element by ID.

        :param timeout: timeout in seconds
        :raises TimeoutError: if element could not be found within time
        """
        elem = await self.web_find(selector_type, selector_value, timeout = timeout)
        await elem.click()
        await self.web_sleep()
        return elem

    async def web_execute(self, jscode:str) -> Any:
        """
        Executes the given JavaScript code in the context of the current page.

        :return: The javascript's return value
        """
        result = await self.page.evaluate(jscode, await_promise = True, return_by_value = True)

        # debug log the jscode but avoid excessive debug logging of window.scrollTo calls
        _prev_jscode:str = getattr(self.__class__.web_execute, "_prev_jscode", "")
        if not (jscode == _prev_jscode or (jscode.startswith("window.scrollTo") and _prev_jscode.startswith("window.scrollTo"))):
            LOG.debug("web_execute(`%s`) = `%s`", jscode, result)
        self.__class__.web_execute._prev_jscode = jscode  # type: ignore[attr-defined]  # noqa: SLF001 Private member accessed

        return result

    async def web_find(self, selector_type:By, selector_value:str, *, parent:Element | None = None, timeout:int | float = 5) -> Element:
        """
        Locates an HTML element by the given selector type and value.

        :param timeout: timeout in seconds
        :raises TimeoutError: if element could not be found within time
        """
        match selector_type:
            case By.ID:
                escaped_id = selector_value.translate(METACHAR_ESCAPER)
                return await self.web_await(
                    lambda: self.page.query_selector(f"#{escaped_id}", parent),
                    timeout = timeout,
                    timeout_error_message = f"No HTML element found with ID '{selector_value}' within {timeout} seconds.")
            case By.CLASS_NAME:
                escaped_classname = selector_value.translate(METACHAR_ESCAPER)
                return await self.web_await(
                    lambda: self.page.query_selector(f".{escaped_classname}", parent),
                    timeout = timeout,
                    timeout_error_message = f"No HTML element found with CSS class '{selector_value}' within {timeout} seconds.")
            case By.TAG_NAME:
                return await self.web_await(
                    lambda: self.page.query_selector(selector_value, parent),
                    timeout = timeout,
                    timeout_error_message = f"No HTML element found of tag <{selector_value}> within {timeout} seconds.")
            case By.CSS_SELECTOR:
                return await self.web_await(
                    lambda: self.page.query_selector(selector_value, parent),
                    timeout = timeout,
                    timeout_error_message = f"No HTML element found using CSS selector '{selector_value}' within {timeout} seconds.")
            case By.TEXT:
                ensure(not parent, f"Specifying a parent element currently not supported with selector type: {selector_type}")
                return await self.web_await(
                    lambda: self.page.find_element_by_text(selector_value, best_match = True),
                    timeout = timeout,
                    timeout_error_message = f"No HTML element found containing text '{selector_value}' within {timeout} seconds.")
            case By.XPATH:
                ensure(not parent, f"Specifying a parent element currently not supported with selector type: {selector_type}")
                return await self.web_await(
                    lambda: self.page.find_element_by_text(selector_value, best_match = True),
                    timeout = timeout,
                    timeout_error_message = f"No HTML element found using XPath '{selector_value}' within {timeout} seconds.")

        raise AssertionError(_("Unsupported selector type: %s") % selector_type)

    async def web_find_all(self, selector_type:By, selector_value:str, *, parent:Element | None = None, timeout:int | float = 5) -> list[Element]:
        """
        Locates an HTML element by ID.

        :param timeout: timeout in seconds
        :raises TimeoutError: if element could not be found within time
        """
        match selector_type:
            case By.CLASS_NAME:
                escaped_classname = selector_value.translate(METACHAR_ESCAPER)
                return await self.web_await(
                    lambda: self.page.query_selector_all(f".{escaped_classname}", parent),
                    timeout = timeout,
                    timeout_error_message = f"No HTML elements found with CSS class '{selector_value}' within {timeout} seconds.")
            case By.CSS_SELECTOR:
                return await self.web_await(
                    lambda: self.page.query_selector_all(selector_value, parent),
                    timeout = timeout,
                    timeout_error_message = f"No HTML elements found using CSS selector '{selector_value}' within {timeout} seconds.")
            case By.TAG_NAME:
                return await self.web_await(
                    lambda: self.page.query_selector_all(selector_value, parent),
                    timeout = timeout,
                    timeout_error_message = f"No HTML elements found of tag <{selector_value}> within {timeout} seconds.")
            case By.TEXT:
                ensure(not parent, f"Specifying a parent element currently not supported with selector type: {selector_type}")
                return await self.web_await(
                    lambda: self.page.find_elements_by_text(selector_value),
                    timeout = timeout,
                    timeout_error_message = f"No HTML elements found containing text '{selector_value}' within {timeout} seconds.")
            case By.XPATH:
                ensure(not parent, f"Specifying a parent element currently not supported with selector type: {selector_type}")
                return await self.web_await(
                    lambda: self.page.find_elements_by_text(selector_value),
                    timeout = timeout,
                    timeout_error_message = f"No HTML elements found using XPath '{selector_value}' within {timeout} seconds.")

        raise AssertionError(_("Unsupported selector type: %s") % selector_type)

    async def web_input(self, selector_type:By, selector_value:str, text:str | int, *, timeout:int | float = 5) -> Element:
        """
        Enters text into an HTML input field.

        :param timeout: timeout in seconds
        :raises TimeoutError: if element could not be found within time
        """
        input_field = await self.web_find(selector_type, selector_value, timeout = timeout)
        await input_field.clear_input()
        await input_field.send_keys(str(text))
        await self.web_sleep()
        return input_field

    async def web_open(self, url:str, *, timeout:int | float = 15_000, reload_if_already_open:bool = False) -> None:
        """
        :param url: url to open in browser
        :param timeout: timespan in seconds within the page needs to be loaded
        :param reload_if_already_open: if False does nothing if the URL is already open in the browser
        :raises TimeoutException: if page did not open within given timespan
        """
        LOG.debug(" -> Opening [%s]...", url)
        if not reload_if_already_open and self.page and url == self.page.url:
            LOG.debug("  => skipping, [%s] is already open", url)
            return
        self.page = await self.browser.get(url = url, new_tab = False, new_window = False)
        await self.web_await(lambda: self.web_execute("document.readyState == 'complete'"), timeout = timeout,
                timeout_error_message = f"Page did not finish loading within {timeout} seconds.")

    async def web_text(self, selector_type:By, selector_value:str, *, parent:Element | None = None, timeout:int | float = 5) -> str:
        return str(await (await self.web_find(selector_type, selector_value, parent = parent, timeout = timeout)).apply("""
            function (elem) {
                let sel = window.getSelection()
                sel.removeAllRanges()
                let range = document.createRange()
                range.selectNode(elem)
                sel.addRange(range)
                let visibleText = sel.toString().trim()
                sel.removeAllRanges()
                return visibleText
            }
        """))

    async def web_sleep(self, min_ms:int = 1_000, max_ms:int = 2_500) -> None:
        duration = max_ms <= min_ms and min_ms or secrets.randbelow(max_ms - min_ms) + min_ms
        LOG.log(loggers.INFO if duration > 1_500 else loggers.DEBUG,  # noqa: PLR2004 Magic value used in comparison
                " ... pausing for %d ms ...", duration)
        await self.page.sleep(duration / 1_000)

    async def web_request(self, url:str, method:str = "GET", valid_response_codes:int | Iterable[int] = 200,
            headers:dict[str, str] | None = None) -> dict[str, Any]:
        method = method.upper()
        LOG.debug(" -> HTTP %s [%s]...", method, url)
        response = cast(dict[str, Any], await self.page.evaluate(f"""
            fetch("{url}", {{
                method: "{method}",
                redirect: "follow",
                headers: {headers or {}}
            }})
            .then(response => response.text().then(responseText => {{
                headers = {{}};
                response.headers.forEach((v, k) => headers[k] = v);
                return {{
                    statusCode: response.status,
                    statusMessage: response.statusText,
                    headers: headers,
                    content: responseText
                }}
            }}))
        """, await_promise = True, return_by_value = True))
        if isinstance(valid_response_codes, int):
            valid_response_codes = [valid_response_codes]
        ensure(
            response["statusCode"] in valid_response_codes,
            f'Invalid response "{response["statusCode"]} response["statusMessage"]" received for HTTP {method} to {url}'
        )
        return response
    # pylint: enable=dangerous-default-value

    async def web_scroll_page_down(self, scroll_length:int = 10, scroll_speed:int = 10_000, *, scroll_back_top:bool = False) -> None:
        """
        Smoothly scrolls the current web page down.

        :param scroll_length: the length of a single scroll iteration, determines smoothness of scrolling, lower is smoother
        :param scroll_speed: the speed of scrolling, higher is faster
        :param scroll_back_top: whether to scroll the page back to the top after scrolling to the bottom
        """
        current_y_pos = 0
        bottom_y_pos:int = await self.web_execute("document.body.scrollHeight")  # get bottom position
        while current_y_pos < bottom_y_pos:  # scroll in steps until bottom reached
            current_y_pos += scroll_length
            await self.web_execute(f"window.scrollTo(0, {current_y_pos})")  # scroll one step
            await asyncio.sleep(scroll_length / scroll_speed)

        if scroll_back_top:  # scroll back to top in same style
            while current_y_pos > 0:
                current_y_pos -= scroll_length
                await self.web_execute(f"window.scrollTo(0, {current_y_pos})")
                await asyncio.sleep(scroll_length / scroll_speed / 2)  # double speed

    async def web_select(self, selector_type:By, selector_value:str, selected_value:Any, timeout:int | float = 5) -> Element:
        """
        Selects an <option/> of a <select/> HTML element.

        :param timeout: timeout in seconds
        :raises TimeoutError: if element could not be found within time
        :raises UnexpectedTagNameException: if element is not a <select> element
        """
        await self.web_await(
            lambda: self.web_check(selector_type, selector_value, Is.CLICKABLE), timeout = timeout,
            timeout_error_message = f"No clickable HTML element with selector: {selector_type}='{selector_value}' found"
        )
        elem = await self.web_find(selector_type, selector_value)
        await elem.apply(f"""
            function (element) {{
              for(let i=0; i < element.options.length; i++)
                {{
                  if(element.options[i].value == "{selected_value}") {{
                    element.selectedIndex = i;
                    element.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    break;
                }}
              }}
              throw new Error("Option with value {selected_value} not found.");
            }}
        """)
        await self.web_sleep()
        return elem

    async def _validate_chrome_version_configuration(self) -> None:
        """
        Validate Chrome version configuration for Chrome 136+ security requirements.

        This method checks if the browser is Chrome 136+ and validates that the configuration
        meets the security requirements for remote debugging.
        """
        # Skip validation in test environments to avoid subprocess calls
        if os.environ.get("PYTEST_CURRENT_TEST"):
            LOG.debug(" -> Skipping browser version validation in test environment")
            return

        try:
            # Detect Chrome version from binary
            binary_path = self.browser_config.binary_location
            version_info = detect_chrome_version_from_binary(binary_path) if binary_path else None

            if version_info and version_info.is_chrome_136_plus:
                LOG.info(" -> %s 136+ detected: %s", version_info.browser_name, version_info)

                # Validate configuration for Chrome/Edge 136+
                is_valid, error_message = validate_chrome_136_configuration(
                    list(self.browser_config.arguments),
                    self.browser_config.user_data_dir
                )

                if not is_valid:
                    LOG.error(" -> %s 136+ configuration validation failed: %s", version_info.browser_name, error_message)
                    LOG.error(" -> Please update your configuration to include --user-data-dir for remote debugging")
                    raise AssertionError(error_message)
                LOG.info(" -> %s 136+ configuration validation passed", version_info.browser_name)
            elif version_info:
                LOG.info(" -> %s version detected: %s (pre-136, no special validation required)", version_info.browser_name, version_info)
            else:
                LOG.debug(" -> Could not detect browser version, skipping validation")
        except (subprocess.SubprocessError, OSError, FileNotFoundError) as e:
            LOG.warning(" -> Browser version detection failed, skipping validation: %s", e)
            # Continue without validation rather than failing
        except Exception as e:
            LOG.warning(" -> Unexpected error during browser version validation, skipping: %s", e)
            # Continue without validation rather than failing

    def _diagnose_chrome_version_issues(self, remote_port:int) -> None:
        """
        Diagnose Chrome version issues and provide specific recommendations.

        Args:
            remote_port: Remote debugging port (0 if not configured)
        """
        # Skip diagnostics in test environments to avoid subprocess calls
        if os.environ.get("PYTEST_CURRENT_TEST"):
            LOG.debug(" -> Skipping browser version diagnostics in test environment")
            return

        try:
            # Get diagnostic information
            binary_path = self.browser_config.binary_location
            diagnostic_info = get_chrome_version_diagnostic_info(
                binary_path = binary_path,
                remote_port = remote_port if remote_port > 0 else None
            )

            # Report binary detection results
            if diagnostic_info["binary_detection"]:
                binary_info = diagnostic_info["binary_detection"]
                LOG.info("(info) %s version from binary: %s %s (major: %d)",
                        binary_info["browser_name"], binary_info["browser_name"], binary_info["version_string"], binary_info["major_version"])

                if binary_info["is_chrome_136_plus"]:
                    LOG.info("(info) %s 136+ detected - security validation required", binary_info["browser_name"])
                else:
                    LOG.info("(info) %s pre-136 detected - no special security requirements", binary_info["browser_name"])

            # Report remote detection results
            if diagnostic_info["remote_detection"]:
                remote_info = diagnostic_info["remote_detection"]
                LOG.info("(info) %s version from remote debugging: %s %s (major: %d)",
                        remote_info["browser_name"], remote_info["browser_name"], remote_info["version_string"], remote_info["major_version"])

                if remote_info["is_chrome_136_plus"]:
                    LOG.info("(info) Remote %s 136+ detected - validating configuration", remote_info["browser_name"])

                    # Validate configuration for Chrome/Edge 136+
                    is_valid, error_message = validate_chrome_136_configuration(
                        list(self.browser_config.arguments),
                        self.browser_config.user_data_dir
                    )

                    if not is_valid:
                        LOG.error("(fail) %s 136+ configuration validation failed: %s", remote_info["browser_name"], error_message)
                        LOG.info("  Solution: Add --user-data-dir=/path/to/directory to browser arguments")
                        LOG.info('  And user_data_dir: "/path/to/directory" to your configuration')
                    else:
                        LOG.info("(ok) %s 136+ configuration validation passed", remote_info["browser_name"])

            # Add general recommendations
            if diagnostic_info["chrome_136_plus_detected"]:
                LOG.info("(info) Chrome/Edge 136+ security changes require --user-data-dir for remote debugging")
                LOG.info("  See: https://developer.chrome.com/blog/remote-debugging-port")
        except (subprocess.SubprocessError, OSError, FileNotFoundError) as e:
            LOG.warning(" -> Browser version diagnostics failed: %s", e)
            # Continue without diagnostics rather than failing
        except Exception as e:
            LOG.warning(" -> Unexpected error during browser version diagnostics: %s", e)
            # Continue without diagnostics rather than failing
