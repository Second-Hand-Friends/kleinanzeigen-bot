# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import asyncio, enum, importlib, json, os, re, sys  # isort: skip
import urllib.parse as urllib_parse
from dataclasses import dataclass, replace
from gettext import gettext as _
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Sequence, cast

import certifi, colorama  # isort: skip
from nodriver.core.connection import ProtocolException
from ruamel.yaml import YAML

from . import ad_form_helpers as _ad_form_helpers
from . import ad_loading, delete_flow, download_flow, extend_flow, published_ads
from . import ad_state as _ad_state
from . import local_path_renaming as _local_path_renaming
from . import price_reduction as _price_reduction
from . import runtime_config as _runtime_config
from ._version import __version__
from .ad_description import get_ad_description
from .model.ad_model import (
    CARRIER_CODE_BY_OPTION,
    CARRIER_CODES_BY_SIZE,
    SIZE_INFO_BY_CARRIER_CODE,
    Ad,
    AdPartial,
    Contact,
)
from .model.ad_model import (
    AdUpdateStrategy as AdUpdateStrategy,
)
from .model.config_model import Config  # noqa: TC001 — used at runtime, config injection
from .update_checker import UpdateChecker
from .utils import diagnostics as _diagnostics
from .utils import dicts as _dicts
from .utils import loggers as _loggers
from .utils import misc as _misc
from .utils import xdg_paths as _xdg_paths
from .utils.exceptions import CaptchaEncountered, CategoryResolutionError, PublishSubmissionUncertainError
from .utils.files import abspath
from .utils.i18n import pluralize
from .utils.misc import ainput, ensure, is_frozen
from .utils.web_scraping_mixin import By, Element, Is, WebScrapingMixin

if TYPE_CHECKING:
    from .utils.timing_collector import TimingCollector

# W0406: possibly a bug, see https://github.com/PyCQA/pylint/issues/3933

LOG:Final[_loggers.Logger] = _loggers.get_logger(__name__)
LOG.setLevel(_loggers.INFO)

SUBMISSION_MAX_RETRIES:Final[int] = 3
_LOGIN_DETECTION_SELECTORS:Final[list[tuple["By", str]]] = [
    (By.CLASS_NAME, "mr-medium"),
    (By.ID, "user-email"),
]
_LOGGED_OUT_CTA_SELECTORS:Final[list[tuple["By", str]]] = [
    (By.CSS_SELECTOR, 'a[href*="einloggen"]'),
    (By.CSS_SELECTOR, 'a[href*="/m-einloggen"]'),
]

colorama.just_fix_windows_console()


def _format_login_detection_selectors(selectors:Sequence[tuple["By", str]]) -> str:
    return ", ".join(f"{selector_type.name}={selector_value}" for selector_type, selector_value in selectors)


class LoginDetectionReason(enum.Enum):
    USER_INFO_MATCH = enum.auto()
    CTA_MATCH = enum.auto()
    SELECTOR_TIMEOUT = enum.auto()


@dataclass(frozen = True)
class LoginDetectionResult:
    """Login detection result.

    Invariants:
    - is_logged_in=True only with USER_INFO_MATCH
    - is_logged_in=False with CTA_MATCH or SELECTOR_TIMEOUT
    """

    is_logged_in:bool
    reason:LoginDetectionReason

    def __post_init__(self) -> None:
        if not isinstance(self.is_logged_in, bool):
            raise TypeError("is_logged_in must be a bool")
        if not isinstance(self.reason, LoginDetectionReason):
            raise TypeError("reason must be a LoginDetectionReason")
        if self.is_logged_in and self.reason != LoginDetectionReason.USER_INFO_MATCH:
            raise ValueError("is_logged_in=True requires reason=USER_INFO_MATCH")
        if not self.is_logged_in and self.reason == LoginDetectionReason.USER_INFO_MATCH:
            raise ValueError("is_logged_in=False requires reason=CTA_MATCH or SELECTOR_TIMEOUT")


class KleinanzeigenBot(WebScrapingMixin):  # noqa: PLR0904
    def __init__(self) -> None:
        # workaround for https://github.com/Second-Hand-Friends/kleinanzeigen-bot/issues/295
        # see https://github.com/pyinstaller/pyinstaller/issues/7229#issuecomment-1309383026
        os.environ["SSL_CERT_FILE"] = certifi.where()

        super().__init__()

        self.root_url = "https://www.kleinanzeigen.de"

        self.config:Config
        self.config_file_path = abspath("config.yaml")
        self.workspace:_xdg_paths.Workspace | None = None
        self._config_arg:str | None = None
        self._workspace_mode_arg:_xdg_paths.InstallationMode | None = None

        self.categories:dict[str, str] = {}

        self.file_log:_loggers.LogFileHandle | None = None
        self._log_basename = os.path.splitext(os.path.basename(sys.executable))[0] if is_frozen() else self.__module__
        self.log_file_path:str | None = abspath(f"{self._log_basename}.log")
        self._logfile_arg:str | None = None
        self._logfile_explicitly_provided:bool = False

        self.command = "help"
        self.ads_selector = "due"
        self._ads_selector_explicit:bool = False
        self.keep_old_ads = False

        self._login_detection_diagnostics_captured:bool = False
        self._timing_collector:"TimingCollector | None" = None

    def __del__(self) -> None:
        if self.file_log:
            self.file_log.close()
            self.file_log = None
        self.close_browser_session()

    def get_version(self) -> str:
        return __version__

    def _workspace_or_raise(self) -> _xdg_paths.Workspace:
        if self.workspace is None:
            raise AssertionError(_("Workspace must be resolved before command execution"))
        return self.workspace

    @property
    def _update_check_state_path(self) -> Path:
        return self._workspace_or_raise().state_dir / "update_check_state.json"

    async def run(self, args:list[str]) -> None:  # noqa: PLR0915
        _cli = importlib.import_module(".cli", __name__)
        parsed = _cli.parse_args(args)
        self.command = parsed.command
        self.ads_selector = parsed.ads_selector
        self._ads_selector_explicit = parsed.ads_selector_explicit
        self.keep_old_ads = parsed.keep_old_ads
        self._preserve_local_settings = parsed.preserve_local_settings
        self._config_arg = parsed.config_arg
        self._workspace_mode_arg = cast(_xdg_paths.InstallationMode, parsed.workspace_mode) if parsed.workspace_mode else None
        self._logfile_arg = parsed.logfile_arg
        self._logfile_explicitly_provided = parsed.logfile_explicitly_provided
        if parsed.config_file_path is not None:
            self.config_file_path = parsed.config_file_path
        if parsed.logfile_explicitly_provided:
            self.log_file_path = parsed.log_file_path

        self.workspace = _runtime_config.resolve_workspace(
            command = self.command,
            config_file_path = self.config_file_path,
            config_arg = self._config_arg,
            logfile_arg = self._logfile_arg,
            workspace_mode = self._workspace_mode_arg,
            logfile_explicitly_provided = self._logfile_explicitly_provided,
            log_basename = self._log_basename,
        )
        if self.workspace is not None:
            self.config_file_path = str(self.workspace.config_file)
            self.log_file_path = str(self.workspace.log_file) if self.workspace.log_file else None

        def _bootstrap_runtime() -> None:
            self.file_log = _runtime_config.configure_file_logging(
                self.log_file_path,
                self.workspace,
                self.file_log,
                self.get_version(),
            )
            runtime_state = _runtime_config.load_config(self.config_file_path, self.workspace, self.command)
            self.config = runtime_state.config
            self.categories = runtime_state.categories
            self._timing_collector = runtime_state.timing_collector
            _runtime_config.apply_browser_config(self.browser_config, self.config, self.workspace, self.config_file_path)

        try:
            # When adding/removing a case, also update runtime_config.VALID_COMMANDS.
            match self.command:
                case "help":
                    _cli.show_help()
                    return
                case "version":
                    print(self.get_version())
                case "create-config":
                    if self.workspace is None and self._workspace_mode_arg is not None:
                        try:
                            workspace = _xdg_paths.resolve_workspace(
                                config_arg = self._config_arg,
                                logfile_arg = self._logfile_arg,
                                workspace_mode = self._workspace_mode_arg,
                                logfile_explicitly_provided = self._logfile_explicitly_provided,
                                log_basename = self._log_basename,
                            )
                            self.workspace = workspace
                            self.config_file_path = str(workspace.config_file)
                            self.log_file_path = str(workspace.log_file) if workspace.log_file else None
                        except ValueError as exc:
                            LOG.error(str(exc))
                            sys.exit(2)
                    _runtime_config.create_default_config(self.config_file_path, self.workspace)
                    return
                case "diagnose":
                    _bootstrap_runtime()
                    self.diagnose_browser_issues()
                    return
                case "verify":
                    _bootstrap_runtime()
                    # Check for updates on startup
                    checker = UpdateChecker(self.config, self._update_check_state_path)
                    checker.check_for_updates()
                    self.ads_selector = "all"
                    if ads := self.load_ads(exclude_ads_with_id = False):
                        for ad_file, ad_cfg, _ad_cfg_orig in ads:
                            ad_file_relative = _ad_state.relative_ad_path(ad_file, self.config_file_path)
                            publish_decision = _price_reduction.evaluate_auto_price_reduction(ad_cfg, ad_file_relative, mode = AdUpdateStrategy.REPLACE)
                            _price_reduction.log_auto_price_reduction_preview(ad_file_relative, publish_decision)

                            update_decision = _price_reduction.evaluate_auto_price_reduction(ad_cfg, ad_file_relative, mode = AdUpdateStrategy.MODIFY)
                            _price_reduction.log_auto_price_reduction_preview(ad_file_relative, update_decision)
                    LOG.info("############################################")
                    LOG.info("DONE: No configuration errors found.")
                    LOG.info("############################################")
                case "update-check":
                    _bootstrap_runtime()
                    checker = UpdateChecker(self.config, self._update_check_state_path)
                    checker.check_for_updates(skip_interval_check = True)
                case "update-content-hash":
                    _bootstrap_runtime()
                    # Check for updates on startup
                    checker = UpdateChecker(self.config, self._update_check_state_path)
                    checker.check_for_updates()
                    self.ads_selector = "all"
                    if ads := self.load_ads(exclude_ads_with_id = False):
                        ad_loading.update_content_hashes(ads)
                    else:
                        LOG.info("############################################")
                        LOG.info("DONE: No active ads found.")
                        LOG.info("############################################")
                case "publish":
                    _bootstrap_runtime()
                    # Check for updates on startup
                    checker = UpdateChecker(self.config, self._update_check_state_path)
                    checker.check_for_updates()

                    if not ad_loading.is_valid_ads_selector(self.ads_selector, {"all", "new", "due", "changed"}):
                        if self._ads_selector_explicit:
                            LOG.error(
                                'Invalid --ads selector: "%s". Valid values: comma-separated keywords (all, new, due, changed) or numeric IDs.',
                                self.ads_selector,
                            )
                            sys.exit(2)
                        self.ads_selector = "due"

                    if ads := self.load_ads():
                        await self.create_browser_session()
                        await self.login()
                        await self.publish_ads(ads)
                    else:
                        LOG.info("############################################")
                        LOG.info("DONE: No new/outdated ads found.")
                        LOG.info("############################################")
                case "update":
                    _bootstrap_runtime()

                    if not ad_loading.is_valid_ads_selector(self.ads_selector, {"all", "changed"}):
                        if self._ads_selector_explicit:
                            LOG.error('Invalid --ads selector: "%s". Valid values: comma-separated keywords (all, changed) or numeric IDs.', self.ads_selector)
                            sys.exit(2)
                        self.ads_selector = "changed"

                    if ads := self.load_ads():
                        await self.create_browser_session()
                        await self.login()
                        await self.update_ads(ads)
                    else:
                        LOG.info("############################################")
                        LOG.info("DONE: No changed ads found.")
                        LOG.info("############################################")
                case "delete":
                    _bootstrap_runtime()
                    # Check for updates on startup
                    checker = UpdateChecker(self.config, self._update_check_state_path)
                    checker.check_for_updates()
                    if ads := self.load_ads():
                        await self.create_browser_session()
                        await self.login()
                        await delete_flow.delete_ads(
                            web = self, root_url = self.root_url,
                            after_delete = self.config.deleting.after_delete,
                            delete_old_ads_by_title = self.config.publishing.delete_old_ads_by_title,
                            ad_cfgs = ads,
                        )
                    else:
                        LOG.info("############################################")
                        LOG.info("DONE: No ads to delete found.")
                        LOG.info("############################################")
                case "extend":
                    _bootstrap_runtime()
                    # Check for updates on startup
                    checker = UpdateChecker(self.config, self._update_check_state_path)
                    checker.check_for_updates()

                    # Default to all ads if no selector provided, but reject invalid values
                    if not ad_loading.is_valid_ads_selector(self.ads_selector, {"all"}):
                        if self._ads_selector_explicit:
                            LOG.error('Invalid --ads selector: "%s". Valid values: all or comma-separated numeric IDs.', self.ads_selector)
                            sys.exit(2)
                        LOG.info("Extending all ads within 8-day window...")
                        self.ads_selector = "all"

                    if ads := self.load_ads():
                        await self.create_browser_session()
                        await self.login()
                        await extend_flow.extend_ads(
                            web = self, root_url = self.root_url,
                            ad_cfgs = ads,
                        )
                    else:
                        LOG.info("############################################")
                        LOG.info("DONE: No ads found to extend.")
                        LOG.info("############################################")
                case "download":
                    # ad IDs depends on selector
                    if not ad_loading.is_valid_ads_selector(self.ads_selector, {"all", "new"}):
                        if self._ads_selector_explicit:
                            LOG.error('Invalid --ads selector: "%s". Valid values: comma-separated keywords (all, new) or numeric IDs.', self.ads_selector)
                            sys.exit(2)
                        self.ads_selector = "new"
                    _bootstrap_runtime()
                    if self._preserve_local_settings:
                        self.config.download.preserve_local_settings = True
                    # Check for updates on startup
                    checker = UpdateChecker(self.config, self._update_check_state_path)
                    checker.check_for_updates()
                    await self.create_browser_session()
                    await self.login()
                    await download_flow.download_ads(
                        web = self, config = self.config,
                        config_file_path = self.config_file_path,
                        workspace = self._workspace_or_raise(),
                        ads_selector = self.ads_selector,
                        load_ads_func = self.load_ads,
                        root_url = self.root_url,
                    )

                case _:
                    LOG.error("Unknown command: %s", self.command)
                    sys.exit(2)
        finally:
            self.close_browser_session()
            if self._timing_collector is not None:
                try:
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(None, self._timing_collector.flush)
                except Exception as exc:  # noqa: BLE001
                    LOG.warning("Timing collector flush failed: %s", exc)

    def load_ads(self, *, ignore_inactive:bool = True, exclude_ads_with_id:bool = True) -> list[tuple[str, Ad, dict[str, Any]]]:
        """Load and validate all ad config files.

        Temporary thin delegator to :func:`ad_loading.load_ads` — kept to
        avoid churn at 7+ call sites inside :meth:`run` and
        :meth:`download_ads`.  Will be removed when the bot class itself is
        decomposed in steps 9–11.
        """
        return ad_loading.load_ads(
            config_file_path = self.config_file_path,
            ad_file_patterns = self.config.ad_files,
            ad_defaults = self.config.ad_defaults,
            categories = self.categories,
            ads_selector = self.ads_selector,
            command = self.command,
            ignore_inactive = ignore_inactive,
            exclude_ads_with_id = exclude_ads_with_id,
        )

    async def check_and_wait_for_captcha(self, *, is_login_page:bool = True, page_context:str | None = None) -> None:
        captcha_elem = await self.web_probe(
            By.CSS_SELECTOR,
            "iframe[name^='a-'][src^='https://www.google.com/recaptcha/api2/anchor?']",
            timeout = self.timeout("captcha_detection"),
        )

        context_label = page_context or ("login page" if is_login_page else "publish operation")
        if captcha_elem is None:
            LOG.debug("No captcha detected within timeout (page_context=%s)", context_label)
            return

        if not is_login_page and self.config.captcha.auto_restart:
            LOG.warning("Captcha recognized - auto-restart enabled, abort run...")
            raise CaptchaEncountered(_misc.parse_duration(self.config.captcha.restart_delay))

        LOG.warning("############################################")
        LOG.warning("# Captcha present! Please solve the captcha.")
        LOG.warning("############################################")

        if not is_login_page:
            await self.web_scroll_page_down()

        await ainput(_("Press a key to continue..."))

    async def login(self) -> None:
        self._login_detection_diagnostics_captured = False
        sso_navigation_timeout = self.timeout("page_load")
        pre_login_gdpr_timeout = self.timeout("quick_dom")

        LOG.info("Checking if already logged in...")
        await self.web_open(f"{self.root_url}")
        await self._click_gdpr_banner(timeout = pre_login_gdpr_timeout)

        detection_result = await self.get_login_state(capture_diagnostics = False)
        if detection_result.is_logged_in:
            LOG.info("Already logged in. Skipping login.")
            return

        LOG.debug("Navigating to SSO login page (Auth0)...")
        # m-einloggen-sso.html triggers immediate server-side redirect to Auth0
        # This avoids waiting for JS on m-einloggen.html which may not execute in headless mode
        try:
            await self.web_open(f"{self.root_url}/m-einloggen-sso.html", timeout = sso_navigation_timeout)
        except TimeoutError:
            LOG.warning("Timeout navigating to SSO login page after %.1fs", sso_navigation_timeout)
            await self._capture_login_detection_diagnostics_if_enabled(
                base_prefix = "login_detection_sso_navigation_timeout",
                pause_banner_message = "# SSO navigation timed out. Browser is paused for manual inspection.",
            )
            raise

        try:
            await self.fill_login_data_and_send()
            await self.handle_after_login_logic()
        except (AssertionError, TimeoutError):
            # AssertionError is intentionally part of auth-boundary control flow so
            # diagnostics are captured before the original error is re-raised.
            await self._capture_login_detection_diagnostics_if_enabled(
                base_prefix = "login_detection_auth0_flow_failure",
                pause_banner_message = "# Auth0 login flow failed. Browser is paused for manual inspection.",
            )
            raise

        await self._dismiss_consent_banner()

        detection_result = await self.get_login_state(capture_diagnostics = False)
        if detection_result.is_logged_in:
            LOG.info("Login confirmed.")
            return

        current_url = self._current_page_url()
        LOG.debug("Login detection reason after attempt is %s", detection_result.reason.name)
        LOG.warning("Login could not be confirmed after Auth0 flow (url=%s)", current_url)
        await self._capture_login_detection_diagnostics_if_enabled(
            base_prefix = f"login_detection_{detection_result.reason.name.lower()}",
            pause_banner_message = "# Login confirmation failed after Auth0 flow. Browser is paused for manual inspection.",
        )
        raise AssertionError(_("Login could not be confirmed after Auth0 flow (reason=%s, url=%s)") % (detection_result.reason.name, current_url))

    def _current_page_url(self) -> str:
        page = getattr(self, "page", None)
        if page is None:
            return "unknown"
        url = getattr(page, "url", None)
        if not isinstance(url, str) or not url:
            return "unknown"

        parsed = urllib_parse.urlparse(url)
        host = parsed.hostname or parsed.netloc.split("@")[-1]
        netloc = f"{host}:{parsed.port}" if parsed.port is not None and host else host
        sanitized = urllib_parse.urlunparse((parsed.scheme, netloc, parsed.path, "", "", ""))
        return sanitized or "unknown"

    async def _wait_for_auth0_login_context(self) -> None:
        redirect_timeout = self.timeout("login_detection")
        try:
            await self.web_await(
                lambda: "login.kleinanzeigen.de" in self._current_page_url() or "/u/login" in self._current_page_url(),
                timeout = redirect_timeout,
                timeout_error_message = f"Auth0 redirect did not start within {redirect_timeout} seconds",
                apply_multiplier = False,
            )
        except TimeoutError as ex:
            current_url = self._current_page_url()
            raise AssertionError(_("Auth0 redirect not detected (url=%s)") % current_url) from ex

    async def _wait_for_auth0_password_step(self) -> None:
        password_step_timeout = self.timeout("login_detection")
        try:
            await self.web_await(
                lambda: "/u/login/password" in self._current_page_url(),
                timeout = password_step_timeout,
                timeout_error_message = f"Auth0 password page not reached within {password_step_timeout} seconds",
                apply_multiplier = False,
            )
        except TimeoutError as ex:
            current_url = self._current_page_url()
            raise AssertionError(_("Auth0 password step not reached (url=%s)") % current_url) from ex

    async def _wait_for_post_auth0_submit_transition(self) -> None:
        post_submit_timeout = self.timeout("login_detection")
        quick_dom_timeout = self.timeout("quick_dom")
        fallback_max_ms = max(700, int(quick_dom_timeout * 1_000))
        fallback_min_ms = max(300, fallback_max_ms // 2)

        try:
            await self.web_await(
                lambda: self._is_valid_post_auth0_destination(self._current_page_url()),
                timeout = post_submit_timeout,
                timeout_error_message = f"Auth0 post-submit transition did not complete within {post_submit_timeout} seconds",
                apply_multiplier = False,
            )
            return
        except TimeoutError:
            LOG.debug("Post-submit transition not detected via URL, checking logged-in selectors")

        login_confirmed = False
        try:
            login_confirmed = await asyncio.wait_for(self.is_logged_in(), timeout = post_submit_timeout)
        except (TimeoutError, asyncio.TimeoutError):
            LOG.debug("Post-submit login verification did not complete within %.1fs", post_submit_timeout)

        if login_confirmed:
            return

        LOG.debug("Auth0 post-submit verification remained inconclusive; applying bounded fallback pause")
        await self.web_sleep(min_ms = fallback_min_ms, max_ms = fallback_max_ms)

        try:
            if await asyncio.wait_for(self.is_logged_in(), timeout = quick_dom_timeout):
                return
        except (TimeoutError, asyncio.TimeoutError):
            LOG.debug("Final post-submit login confirmation did not complete within %.1fs", quick_dom_timeout)

        current_url = self._current_page_url()
        raise TimeoutError(_("Auth0 post-submit verification remained inconclusive (url=%s)") % current_url)

    def _is_valid_post_auth0_destination(self, url:str) -> bool:
        if not url or url in {"unknown", "about:blank"}:
            return False

        parsed = urllib_parse.urlparse(url)
        host = (parsed.hostname or "").lower()
        path = parsed.path.lower()

        if host != "kleinanzeigen.de" and not host.endswith(".kleinanzeigen.de"):
            return False
        if host == "login.kleinanzeigen.de":
            return False
        if path.startswith("/u/login"):
            return False

        return "error" not in path

    async def fill_login_data_and_send(self) -> None:
        """Auth0 2-step login via m-einloggen-sso.html (server-side redirect, no JS needed).

        Step 1: /u/login/identifier - email
        Step 2: /u/login/password   - password
        """
        LOG.info("Logging in...")

        await self._wait_for_auth0_login_context()

        # Step 1: email identifier
        LOG.debug("Auth0 Step 1: entering email...")
        await self.web_input(By.ID, "username", self.config.login.username)
        await self.web_click(By.CSS_SELECTOR, "button[type='submit']")

        # Step 2: wait for password page then enter password
        LOG.debug("Waiting for Auth0 password page...")
        await self._wait_for_auth0_password_step()

        LOG.debug("Auth0 Step 2: entering password...")
        await self.web_input(By.CSS_SELECTOR, "input[type='password']", self.config.login.password)
        await self.check_and_wait_for_captcha(is_login_page = True)
        await self.web_click(By.CSS_SELECTOR, "button[type='submit']")
        await self._wait_for_post_auth0_submit_transition()
        LOG.debug("Auth0 login submitted.")

    async def handle_after_login_logic(self) -> None:
        await self._check_sms_verification()
        await self._check_email_verification()
        LOG.debug("Handling GDPR disclaimer...")
        await self._click_gdpr_banner()

    async def _check_sms_verification(self) -> None:
        sms_timeout = self.timeout("sms_verification")
        element = await self.web_probe(By.TEXT, "Wir haben dir gerade einen 6-stelligen Code für die Telefonnummer", timeout = sms_timeout)
        if element is None:
            LOG.debug("No SMS verification prompt detected after login")
            return
        LOG.warning("############################################")
        LOG.warning("# Device verification message detected. Please follow the instruction displayed in the Browser.")
        LOG.warning("############################################")
        await ainput(_("Press ENTER when done..."))

    async def _dismiss_consent_banner(self) -> None:
        """Dismiss the GDPR/TCF consent banner if it is present.

        This banner can appear on any page navigation (not just after login) and blocks
        all form interaction until dismissed. Uses a short timeout to avoid slowing down
        the flow when the banner is already gone.
        """
        banner_timeout = self.timeout("quick_dom")
        element = await self.web_probe(By.ID, "gdpr-banner-accept", timeout = banner_timeout)
        if element is not None:
            LOG.debug("Consent banner detected, clicking 'Alle akzeptieren'...")
            await element.click()
            await self.web_sleep()
        else:
            LOG.debug("Consent banner not present; continuing without dismissal")

    async def _check_email_verification(self) -> None:
        email_timeout = self.timeout("email_verification")
        element = await self.web_probe(By.TEXT, "Um dein Konto zu schützen haben wir dir eine E-Mail geschickt", timeout = email_timeout)
        if element is None:
            LOG.debug("No email verification prompt detected after login")
            return
        LOG.warning("############################################")
        LOG.warning("# Device verification message detected. Please follow the instruction displayed in the Browser.")
        LOG.warning("############################################")
        await ainput(_("Press ENTER when done..."))

    async def _click_gdpr_banner(self, *, timeout:float | None = None) -> None:
        gdpr_timeout = self.timeout("quick_dom") if timeout is None else timeout
        element = await self.web_probe(By.ID, "gdpr-banner-accept", timeout = gdpr_timeout)
        if element is not None:
            await element.click()
            await self.web_sleep()
        else:
            LOG.debug("GDPR banner not present; continuing without click")

    async def get_login_state(self, *, capture_diagnostics:bool = True) -> LoginDetectionResult:
        """Determine login status using DOM-first detection and return result with reason.

        Order:
        1) DOM-based logged-in marker check
        2) Logged-out CTA check
        3) If inconclusive, optionally capture diagnostics and return a timeout reason
        """
        # Prefer DOM-based checks first to minimize bot-like behavior and avoid
        # fragile API probing side effects. Server-side auth probing was removed.
        if await self._has_logged_in_marker():
            return LoginDetectionResult(is_logged_in = True, reason = LoginDetectionReason.USER_INFO_MATCH)

        if await self._has_logged_out_cta(log_timeout = False):
            return LoginDetectionResult(is_logged_in = False, reason = LoginDetectionReason.CTA_MATCH)

        if capture_diagnostics:
            await self._capture_login_detection_diagnostics_if_enabled(
                base_prefix = "login_detection_selector_timeout",
                pause_banner_message = "# Login detection remained inconclusive. Browser is paused for manual inspection.",
            )
        return LoginDetectionResult(is_logged_in = False, reason = LoginDetectionReason.SELECTOR_TIMEOUT)

    def _diagnostics_output_dir(self) -> Path:
        diagnostics = getattr(self.config, "diagnostics", None)
        if diagnostics is not None and diagnostics.output_dir and diagnostics.output_dir.strip():
            return Path(abspath(diagnostics.output_dir, relative_to = self.config_file_path)).resolve()

        workspace = self._workspace_or_raise()
        _xdg_paths.ensure_directory(workspace.diagnostics_dir, "diagnostics directory")
        return workspace.diagnostics_dir

    async def _capture_login_detection_diagnostics_if_enabled(
        self,
        *,
        base_prefix:str = "login_detection_inconclusive",
        pause_banner_message:str = "# Login detection remained inconclusive. Browser is paused for manual inspection.",
    ) -> None:
        cfg = getattr(self.config, "diagnostics", None)
        if cfg is None or not cfg.capture_on.login_detection:
            return

        if self._login_detection_diagnostics_captured:
            return

        page = getattr(self, "page", None)

        try:
            output_dir = self._diagnostics_output_dir()
        except Exception as exc:  # noqa: BLE001
            LOG.debug("Login diagnostics capture skipped (base_prefix=%s): %s", base_prefix, exc)
            return

        try:
            await _diagnostics.capture_diagnostics(
                output_dir = output_dir,
                base_prefix = base_prefix,
                page = page,
                log_file_path = self.log_file_path,
                copy_log = cfg.capture_log_copy,
            )
        except Exception as exc:  # noqa: BLE001
            LOG.debug(
                "Login diagnostics capture failed (output_dir=%s, base_prefix=%s): %s",
                output_dir,
                base_prefix,
                exc,
            )
            return

        self._login_detection_diagnostics_captured = True

        if cfg.pause_on_login_detection_failure and getattr(sys.stdin, "isatty", lambda: False)():
            LOG.warning("############################################")
            LOG.warning(pause_banner_message)
            LOG.warning("############################################")
            await ainput(_("Press a key to continue..."))

    async def _capture_publish_error_diagnostics_if_enabled(
        self,
        ad_cfg:Ad,
        ad_cfg_orig:dict[str, Any],
        ad_file:str,
        attempt:int,
        exc:Exception,
    ) -> None:
        """Capture publish failure diagnostics when enabled and a page is available.

        Runs only if cfg.capture_on.publish is enabled and self.page is set.
        Uses the ad configuration and publish attempt details to write screenshot, HTML,
        JSON payload, and optional log copy for debugging.
        """
        cfg = getattr(self.config, "diagnostics", None)
        if cfg is None or not cfg.capture_on.publish:
            return

        page = getattr(self, "page", None)
        if page is None:
            return

        # Use the ad filename (without extension) as identifier
        ad_file_stem = Path(ad_file).stem

        json_payload = {
            "timestamp": _misc.now().isoformat(timespec = "seconds"),
            "attempt": attempt,
            "page_url": getattr(page, "url", None),
            "exception": {
                "type": exc.__class__.__name__,
                "message": str(exc),
                "repr": repr(exc),
            },
            "ad_file": ad_file,
            "ad_title": ad_cfg.title,
            "ad_config_effective": ad_cfg.model_dump(mode = "json"),
            "ad_config_original": ad_cfg_orig,
        }

        try:
            await _diagnostics.capture_diagnostics(
                output_dir = self._diagnostics_output_dir(),
                base_prefix = "publish_error",
                attempt = attempt,
                subject = ad_file_stem,
                page = page,
                json_payload = json_payload,
                log_file_path = self.log_file_path,
                copy_log = cfg.capture_log_copy,
            )
        except Exception as error:  # noqa: BLE001
            LOG.warning("Diagnostics capture failed during publish error handling: %s", error)

    async def _has_logged_in_marker(self) -> bool:
        # Use login_detection timeout (10s default) instead of default (5s)
        # to allow sufficient time for client-side JavaScript rendering after page load.
        # This is especially important for older sessions (20+ days) that require
        # additional server-side validation time.
        login_check_timeout = self.timeout("login_detection")
        effective_timeout = self._effective_timeout("login_detection")
        username = self.config.login.username.lower()
        LOG.debug(
            "Starting login detection (timeout: %.1fs base, %.1fs effective with multiplier/backoff)",
            login_check_timeout,
            effective_timeout,
        )
        quick_dom_timeout = self.timeout("quick_dom")
        tried_login_selectors = _format_login_detection_selectors(_LOGIN_DETECTION_SELECTORS)

        try:
            user_info, matched_selector = await self.web_text_first_available(
                _LOGIN_DETECTION_SELECTORS,
                timeout = quick_dom_timeout,
                key = "quick_dom",
                description = "login_detection(quick_logged_in)",
            )
            if username in user_info.lower():
                matched_selector_display = (
                    f"{_LOGIN_DETECTION_SELECTORS[matched_selector][0].name}={_LOGIN_DETECTION_SELECTORS[matched_selector][1]}"
                    if 0 <= matched_selector < len(_LOGIN_DETECTION_SELECTORS)
                    else f"selector_index_{matched_selector}"
                )
                LOG.debug("Login detected via login detection selector '%s'", matched_selector_display)
                return True
        except TimeoutError:
            LOG.debug("No login detected via configured login detection selectors (%s)", tried_login_selectors)

        try:
            user_info, matched_selector = await self.web_text_first_available(
                _LOGIN_DETECTION_SELECTORS,
                timeout = login_check_timeout,
                key = "login_detection",
                description = "login_detection(selector_group)",
            )
            if username in user_info.lower():
                matched_selector_display = (
                    f"{_LOGIN_DETECTION_SELECTORS[matched_selector][0].name}={_LOGIN_DETECTION_SELECTORS[matched_selector][1]}"
                    if 0 <= matched_selector < len(_LOGIN_DETECTION_SELECTORS)
                    else f"selector_index_{matched_selector}"
                )
                LOG.debug("Login detected via login detection selector '%s'", matched_selector_display)
                return True
        except TimeoutError:
            LOG.debug("Timeout waiting for login detection selector group after %.1fs", effective_timeout)

        return False

    async def is_logged_in(self) -> bool:
        if await self._has_logged_in_marker():
            return True

        tried_login_selectors = _format_login_detection_selectors(_LOGIN_DETECTION_SELECTORS)

        LOG.debug("No login detected via configured login detection selectors (%s)", tried_login_selectors)
        return False

    # NOTE: Treats any matched CTA selector with non-empty text as logged-out evidence.
    # Does NOT verify visibility (hidden/footer/off-canvas links could theoretically match).
    # PR #870 verified these selectors work correctly in practice.
    # If false positives occur, harden by adding web_check(Is.DISPLAYED) on cta_element.
    # See issue #876.
    async def _has_logged_out_cta(self, *, log_timeout:bool = True) -> bool:
        quick_dom_timeout = self.timeout("quick_dom")
        tried_logged_out_selectors = _format_login_detection_selectors(_LOGGED_OUT_CTA_SELECTORS)

        try:
            cta_element, cta_index = await self.web_find_first_available(
                _LOGGED_OUT_CTA_SELECTORS,
                timeout = quick_dom_timeout,
                key = "quick_dom",
                description = "login_detection(logged_out_cta)",
            )
            cta_text = await self._extract_visible_text(cta_element)
            if cta_text.strip():
                matched_selector_display = (
                    f"{_LOGGED_OUT_CTA_SELECTORS[cta_index][0].name}={_LOGGED_OUT_CTA_SELECTORS[cta_index][1]}"
                    if 0 <= cta_index < len(_LOGGED_OUT_CTA_SELECTORS)
                    else f"selector_index_{cta_index}"
                )
                if 0 <= cta_index < len(_LOGGED_OUT_CTA_SELECTORS):
                    LOG.debug("Fast logged-out pre-check matched selector '%s'", matched_selector_display)
                    return True
                LOG.debug("Fast logged-out pre-check got unexpected selector index '%s'; failing closed", cta_index)
                return False
        except TimeoutError:
            if log_timeout:
                LOG.debug(
                    "Fast logged-out pre-check found no login CTA (%s) within %.1fs",
                    tried_logged_out_selectors,
                    quick_dom_timeout,
                )

        return False

    async def _fetch_published_ads(self, *, strict:bool = False) -> list[dict[str, Any]]:
        """Temporary delegator to published_ads.fetch_published_ads."""
        return await published_ads.fetch_published_ads(self, self.root_url, strict = strict)

    async def __check_publishing_result(self) -> bool:
        # Check for success messages
        return await self.web_check(By.ID, "checking-done", Is.DISPLAYED) or await self.web_check(By.ID, "not-completed", Is.DISPLAYED)

    def _log_local_path_rename_result(self, result:_local_path_renaming.LocalPathRenameResult, ad_id:int) -> None:
        """Log a human-readable summary of local path renaming after a republish."""
        path_old_id = result.path_old_id if result.path_old_id is not None else result.yaml_old_id
        id_label = f"ID {path_old_id} -> ID {ad_id}"
        if result.path_old_id is not None and result.yaml_old_id is not None and result.path_old_id != result.yaml_old_id:
            id_label += f" (YAML had ID {result.yaml_old_id})"

        renamed:list[str] = []
        if result.folder_status == _local_path_renaming.RenameStatus.RENAMED:
            renamed.append(_("folder"))
        if result.file_status == _local_path_renaming.RenameStatus.RENAMED:
            renamed.append(_("ad file"))
        if result.renamed_image_count > 0:
            renamed.append(f"{result.renamed_image_count} {_('image(s)')}")

        blocked:list[str] = []
        if result.file_status in {_local_path_renaming.RenameStatus.TARGET_EXISTS, _local_path_renaming.RenameStatus.ERROR}:
            blocked.append(_("ad file"))
        if result.folder_status in {_local_path_renaming.RenameStatus.TARGET_EXISTS, _local_path_renaming.RenameStatus.ERROR}:
            blocked.append(_("ad folder"))
        if result.blocked_image_count > 0:
            blocked.append(f"{result.blocked_image_count} {_('image(s)')}")

        if renamed:
            LOG.info("Local path renaming (%s): %s", id_label, ", ".join(renamed))
            if _local_path_renaming.RenameStatus.RENAMED in {result.file_status, result.folder_status}:
                LOG.info("Updated ad file: %s", result.ad_file)

        if blocked:
            LOG.warning("Local path renaming (%s): could not rename %s (target exists or error)", id_label, ", ".join(blocked))

        if not renamed and not blocked:
            if (
                result.yaml_old_id is not None
                and result.yaml_old_id != ad_id
                and self.config.publishing.local_path_renaming.mode == "TEMPLATE_MATCH"
            ):
                LOG.info("Local path renaming (%s): no local paths needed renaming", id_label)

    async def publish_ads(self, ad_cfgs:list[tuple[str, Ad, dict[str, Any]]]) -> None:
        count = 0
        failed_count = 0
        max_retries = SUBMISSION_MAX_RETRIES

        published_ads = await self._fetch_published_ads()

        for idx, (ad_file, ad_cfg, ad_cfg_orig) in enumerate(ad_cfgs, start = 1):
            LOG.info("Processing %s/%s: '%s' from [%s]...", idx, len(ad_cfgs), ad_cfg.title, ad_file)

            if [x for x in published_ads if x["id"] == ad_cfg.id and x["state"] == "paused"]:
                LOG.info("Skipping because ad is reserved")
                continue

            count += 1
            success = False
            baseline_price = ad_cfg.price
            baseline_price_reduction_count = ad_cfg.price_reduction_count

            for attempt in range(1, max_retries + 1):
                try:
                    # publish_ad mutates pricing fields before submit; reset them so retries
                    # remain idempotent for a single eligible reduction cycle.
                    ad_cfg.price = baseline_price
                    ad_cfg.price_reduction_count = baseline_price_reduction_count
                    await self.publish_ad(ad_file, ad_cfg, ad_cfg_orig, published_ads, AdUpdateStrategy.REPLACE)
                    success = True
                    break  # Publish succeeded, exit retry loop
                except asyncio.CancelledError:
                    raise  # Respect task cancellation
                except CategoryResolutionError as ex:
                    await self._capture_publish_error_diagnostics_if_enabled(ad_cfg, ad_cfg_orig, ad_file, attempt, ex)
                    LOG.error("Category resolution failed for '%s': %s. Skipping ad (configuration error, no retry).", ad_cfg.title, ex)
                    failed_count += 1
                    break
                except PublishSubmissionUncertainError as ex:
                    await self._capture_publish_error_diagnostics_if_enabled(ad_cfg, ad_cfg_orig, ad_file, attempt, ex)
                    LOG.warning(
                        "Attempt %s/%s for '%s' reached submit boundary but failed: %s. Not retrying to prevent duplicate listings.",
                        attempt,
                        max_retries,
                        ad_cfg.title,
                        ex,
                    )
                    LOG.warning("Manual recovery required for '%s'. Check 'Meine Anzeigen' to confirm whether the ad was posted.", ad_cfg.title)
                    LOG.warning(
                        "If posted, sync local state with 'kleinanzeigen-bot download --ads=new' or 'kleinanzeigen-bot download --ads=<id>'; "
                        "otherwise rerun publish for this ad."
                    )
                    failed_count += 1
                    break
                except (TimeoutError, ProtocolException) as ex:
                    await self._capture_publish_error_diagnostics_if_enabled(ad_cfg, ad_cfg_orig, ad_file, attempt, ex)
                    if attempt >= max_retries:
                        LOG.error("All %s attempts failed for '%s': %s. Skipping ad.", max_retries, ad_cfg.title, ex)
                        failed_count += 1
                        break

                    LOG.warning("Attempt %s/%s failed for '%s': %s. Retrying...", attempt, max_retries, ad_cfg.title, ex)
                    await self.web_sleep(2_000)  # Wait before retry

            # Check publishing result separately (no retry - ad is already submitted)
            if success:
                try:
                    publish_timeout = self.timeout("publishing_result")
                    await self.web_await(self.__check_publishing_result, timeout = publish_timeout)
                except TimeoutError:
                    LOG.warning(" -> Could not confirm publishing for '%s', but ad may be online", ad_cfg.title)

            if success and self.config.publishing.delete_old_ads == "AFTER_PUBLISH" and not self.keep_old_ads:
                await delete_flow.delete_ad(
                    web = self, root_url = self.root_url,
                    ad_cfg = ad_cfg,
                    published_ads_list = published_ads,
                    delete_old_ads_by_title = False,
                )

        LOG.info("############################################")
        if failed_count > 0:
            LOG.info("DONE: (Re-)published %s (%s failed after retries)", pluralize("ad", count - failed_count), failed_count)
        else:
            LOG.info("DONE: (Re-)published %s", pluralize("ad", count))
        LOG.info("############################################")

    async def publish_ad(  # noqa: PLR0915 PLR0914 PLR0912
        self, ad_file:str, ad_cfg:Ad, ad_cfg_orig:dict[str, Any], published_ads:list[dict[str, Any]], mode:AdUpdateStrategy = AdUpdateStrategy.REPLACE
    ) -> None:
        """Publish or update an ad on Kleinanzeigen.

        Args:
            ad_file: Path to the ad configuration YAML file.
            ad_cfg: The effective ad configuration with default values applied.
            ad_cfg_orig: The original ad configuration as present in the YAML file.
            published_ads: List of published ads from the API, used for deduplication
                and old ad deletion.
            mode: The ad editing strategy. REPLACE creates a new ad (full republish),
                MODIFY updates an existing ad in-place.

        Returns:
            None
        """
        old_ad_id = ad_cfg.id

        if mode == AdUpdateStrategy.REPLACE:
            if self.config.publishing.delete_old_ads == "BEFORE_PUBLISH" and not self.keep_old_ads:
                await delete_flow.delete_ad(
                    web = self, root_url = self.root_url,
                    ad_cfg = ad_cfg,
                    published_ads_list = published_ads,
                    delete_old_ads_by_title = self.config.publishing.delete_old_ads_by_title,
                )

            # Apply auto price reduction in REPLACE mode (republish flow)
            _price_reduction.apply_auto_price_reduction(
                ad_cfg, ad_cfg_orig, _ad_state.relative_ad_path(
                    ad_file, self.config_file_path), mode = AdUpdateStrategy.REPLACE)

            LOG.info("Publishing ad '%s'...", ad_cfg.title)
            await self.web_open(f"{self.root_url}/p-anzeige-aufgeben-schritt2.html", reload_if_already_open = True)
        else:
            # Always run restore-first when enabled so previously applied reductions
            # are restored even when on_update is false.  The evaluator handles
            # the on_update guard internally (returns early without advancing).
            if ad_cfg.auto_price_reduction and ad_cfg.auto_price_reduction.enabled:
                _price_reduction.apply_auto_price_reduction(
                    ad_cfg, ad_cfg_orig, _ad_state.relative_ad_path(
                        ad_file, self.config_file_path), mode = AdUpdateStrategy.MODIFY)

            LOG.info("Updating ad '%s'...", ad_cfg.title)
            await self.web_open(f"{self.root_url}/p-anzeige-bearbeiten.html?adId={ad_cfg.id}", reload_if_already_open = True)

        await self._dismiss_consent_banner()

        if _loggers.is_debug(LOG):
            LOG.debug(" -> effective ad meta:")
            YAML().dump(ad_cfg.model_dump(), sys.stdout)

        if ad_cfg.type == "WANTED":
            await self.web_click(By.ID, "ad-type-WANTED")

        #############################
        # set category (before title to avoid form reset clearing title)
        #############################
        await self.__set_category(ad_cfg.category, ad_file)
        await self.web_sleep()  # wait for category-dependent fields to render before setting attributes

        #############################
        # set special attributes
        #############################
        await self.__set_special_attributes(ad_cfg)

        #############################
        # set shipping type/options/costs
        #############################
        shipping_type = ad_cfg.shipping_type
        if shipping_type != "NOT_APPLICABLE":
            if ad_cfg.type == "WANTED":
                # WANTED ads render shipping as a special-attribute combobox dropdown,
                # not as radio buttons.  Select by display text using the standard
                # DOM-based web_select_button_combobox (no React fiber internals).
                # See issue #930 for broader React fiber migration.
                display_text = _ad_form_helpers.WANTED_SHIPPING_LABELS.get(shipping_type)
                if display_text:
                    try:
                        shipping_btn = await self.web_find(
                            By.CSS_SELECTOR,
                            '[role="combobox"][id$=".versand"]',
                            timeout = self.timeout("quick_dom"),
                        )
                        btn_id = cast(str, shipping_btn.attrs.get("id"))
                        if not btn_id:
                            raise TimeoutError(_("Shipping combobox button has no id attribute"))
                        await self.web_select_button_combobox(btn_id, display_text)
                    except TimeoutError as ex:
                        LOG.warning("Failed to set shipping attribute for type '%s'!", shipping_type)
                        raise TimeoutError(_("Failed to set shipping attribute for type '%s'!") % shipping_type) from ex
            else:
                await self.__set_shipping(ad_cfg, mode)
        else:
            LOG.debug("Shipping step skipped - reason: NOT_APPLICABLE")

        #############################
        # set price
        #############################
        price_type = ad_cfg.price_type
        if price_type != "NOT_APPLICABLE":
            price_type_options = {"FIXED": 0, "NEGOTIABLE": 1, "GIVE_AWAY": 2}
            option_idx = price_type_options.get(price_type)
            if option_idx is not None:
                try:
                    await self.web_click(By.ID, "ad-price-type")
                    await self.web_click(By.ID, f"ad-price-type-menu-option-{option_idx}")
                except TimeoutError as ex:
                    raise TimeoutError(_("Failed to set price type '%s'") % price_type) from ex
            if ad_cfg.price is not None:
                await self.__set_input_value("ad-price-amount", str(ad_cfg.price))

        #############################
        # set sell_directly
        #############################
        if ad_cfg.type != "WANTED":
            sell_directly = ad_cfg.sell_directly
            quick_dom = self.timeout("quick_dom")
            if ad_cfg.shipping_type == "SHIPPING":
                if sell_directly and price_type in {"FIXED", "NEGOTIABLE"}:
                    buy_now_true = await self.web_probe(By.ID, "ad-buy-now-true", timeout = quick_dom)
                    if buy_now_true is None:
                        LOG.warning("Direct-buy (sell_directly) is not available for the selected category. Skipping.")
                    elif not await self.web_check(By.ID, "ad-buy-now-true", Is.SELECTED, timeout = quick_dom):
                        await self.web_click(By.ID, "ad-buy-now-true", timeout = quick_dom)
                else:
                    buy_now_false = await self.web_probe(By.ID, "ad-buy-now-false", timeout = quick_dom)
                    if buy_now_false and not await self.web_check(By.ID, "ad-buy-now-false", Is.SELECTED, timeout = quick_dom):
                        await self.web_click(By.ID, "ad-buy-now-false", timeout = quick_dom)
            else:
                # For PICKUP/other types: always opt out of buy-now if the radio exists
                buy_now_false = await self.web_probe(By.ID, "ad-buy-now-false", timeout = quick_dom)
                if buy_now_false and not await self.web_check(By.ID, "ad-buy-now-false", Is.SELECTED, timeout = quick_dom):
                    await self.web_click(By.ID, "ad-buy-now-false", timeout = quick_dom)

        #############################
        # set description
        #############################
        description = get_ad_description(ad_cfg, self.config.ad_defaults, with_affixes = True)
        await self.__set_input_value("ad-description", description)

        await self.__set_contact_fields(ad_cfg.contact)

        #############################
        # delete previous images to ensure a clean slate
        # (needed for MODIFY because we don't know which changed,
        #  and as defensive cleanup when the form is pre-populated with thumbnails)
        #############################
        remove_button_selector = "button[aria-label='Bild entfernen']"
        hidden_marker_selector = "input[name^='adImages'][name$='.url']"
        quick_dom = self.timeout("quick_dom")
        removed_count = 0

        try:
            existing_markers = await self._web_find_all_once(By.CSS_SELECTOR, hidden_marker_selector, quick_dom)
            existing_image_count = sum(1 for marker in existing_markers if _ad_form_helpers.get_marker_value(marker))
        except TimeoutError:
            existing_image_count = 0

        if existing_image_count:
            for idx in range(existing_image_count):
                remove_btn = await self.web_probe(By.CSS_SELECTOR, remove_button_selector, timeout = quick_dom)
                if remove_btn is None:
                    raise TimeoutError(
                        _("Image cleanup failed before upload. Removed %(removed)d of %(total)d existing images.")
                        % {"removed": idx, "total": existing_image_count}
                    )
                await remove_btn.click()
                removed_count += 1
                await self.web_sleep(300, 500)

        if removed_count > 0:
            LOG.info(" -> removed %d existing image(s) before upload", removed_count)
            # Let async DOM updates settle before capturing hidden-marker baseline
            await self.web_sleep(200, 350)

        #############################
        # upload images
        #############################
        await self.__upload_images(ad_cfg)

        #############################
        # wait for captcha
        #############################
        operation_label = {
            AdUpdateStrategy.REPLACE: "publish",
            AdUpdateStrategy.MODIFY: "update",
        }.get(mode, mode.name.lower())
        await self.check_and_wait_for_captcha(is_login_page = False, page_context = f"{operation_label} operation")

        #############################
        # set title (right before submit to prevent React re-render clearing it)
        #############################
        LOG.debug("Setting title '%s' (deferred to prevent React re-render clearing it)", ad_cfg.title)
        await self.__set_input_value("ad-title", ad_cfg.title)

        #############################
        # submit
        #############################
        # Click is retryable — no submission can have occurred before this point.
        # Edit page uses 'Änderungen speichern' or 'Anzeige speichern'; publish page uses 'Anzeige aufgeben'
        await self.web_click(By.XPATH, "//button[contains(., 'Anzeige aufgeben') or contains(., 'Änderungen speichern') or contains(., 'Anzeige speichern')]")

        # PostListingForm v2 may show an "Effektiver verkaufen" upsell
        # dialog after clicking submit.  Dismiss it so the actual form
        # POST can proceed.
        quick_dom = self.timeout("quick_dom")
        upsell_dialog = await self.web_probe(
            By.XPATH, "//dialog[@open and contains(., 'Effektiver verkaufen')]", timeout = quick_dom
        )
        if upsell_dialog is not None:
            LOG.info("Dismissing 'Effektiver verkaufen' upsell dialog...")
            await self.web_click(
                By.XPATH, "//dialog[@open]//button[contains(., 'Ohne Hochschieben weiter')]",
                timeout = quick_dom,
            )
            await self.web_sleep(500)  # let the dialog close animation finish

        # Everything after the first click is uncertain: the ad may already have been submitted.
        ad_id:int | None = None
        try:
            quick_dom = self.timeout("quick_dom")

            imprint_btn = await self.web_probe(By.ID, "imprint-guidance-submit", timeout = quick_dom)
            if imprint_btn is not None:
                await imprint_btn.click()

            # check for no image question
            if not ad_cfg.images:
                image_hint_xpath = '//button[contains(., "Ohne Bild veröffentlichen")]'
                image_hint_button = await self.web_probe(By.XPATH, image_hint_xpath, timeout = quick_dom)
                if image_hint_button is not None:
                    await image_hint_button.click()

            #############################
            # wait for payment form if commercial account is used
            #############################
            payment_form = await self.web_probe(By.ID, "myftr-shppngcrt-frm", timeout = quick_dom)
            if payment_form is not None:
                LOG.warning("############################################")
                LOG.warning("# Payment form detected! Please proceed with payment.")
                LOG.warning("############################################")
                await self.web_scroll_page_down()
                await ainput(_("Press a key to continue..."))

            confirmation_timeout = self.timeout("publishing_confirmation")

            async def _check_confirmation_url() -> bool:
                url = str(await self.web_execute("window.location.href"))
                return "p-anzeige-aufgeben-bestaetigung.html?adId=" in url

            await self.web_await(_check_confirmation_url, timeout = confirmation_timeout)

            # extract the ad id from the URL's query parameter (use JS for fresh URL, not stale self.page.url)
            current_url = str(await self.web_execute("window.location.href"))
            current_url_query_params = urllib_parse.parse_qs(urllib_parse.urlparse(current_url).query)
            ad_id = int(current_url_query_params.get("adId", [])[0])

        except (TimeoutError, ProtocolException, IndexError, ValueError, TypeError) as ex:
            # The confirmation page may have auto-redirected before we could poll it,
            # or the URL was redirected between polling and extraction (race condition).
            # Try to recover the ad ID from tracking data on the current page.
            LOG.debug("Confirmation URL polling or extraction failed (%s), attempting tracking data fallback...", type(ex).__name__)
            try:
                ad_id = await self._try_recover_ad_id_from_redirect()
            except Exception as fallback_ex:  # noqa: BLE001
                LOG.debug("Tracking data fallback failed: %s", fallback_ex)

            if ad_id is None:
                raise PublishSubmissionUncertainError("submission may have succeeded before failure") from ex

            LOG.warning(
                "Confirmation page redirected too fast; extracted ad ID %s from page tracking data",
                ad_id,
            )

        # Defensive guard: ad_id must be set by now — either from the confirmation URL
        # (try block) or the tracking fallback (except block). The except block always
        # either sets ad_id or raises PublishSubmissionUncertainError, making this
        # unreachable in the current code. Guards against future regressions.
        if ad_id is None:
            msg = _("ad_id is unexpectedly None after confirmation flow for %s") % ad_file
            raise RuntimeError(msg)

        ad_cfg_orig["id"] = ad_id
        # Rename referenced images before hashing/saving so the YAML content and
        # content_hash reflect only image file renames that actually succeeded.
        image_result = _local_path_renaming.rename_referenced_local_image_files_after_id_change(
            Path(ad_file),
            ad_cfg_orig.get("images"),
            old_id = old_ad_id,
            new_id = ad_id,
            ad_file_name_template = self.config.download.ad_file_name_template,
            enabled = self.config.publishing.local_path_renaming.mode == "TEMPLATE_MATCH",
        )
        if image_result.updated_images is not None:
            ad_cfg_orig["images"] = image_result.updated_images

        # Update content hash after successful publication
        # Calculate hash on original config to ensure consistent comparison on restart
        ad_cfg_orig["content_hash"] = AdPartial.model_validate(ad_cfg_orig).update_content_hash().content_hash
        ad_cfg_orig["updated_on"] = _misc.now().isoformat(timespec = "seconds")
        if not ad_cfg.created_on and not ad_cfg.id:
            ad_cfg_orig["created_on"] = ad_cfg_orig["updated_on"]

        # Increment repost_count only for REPLACE operations (actual reposts)
        if mode == AdUpdateStrategy.REPLACE:
            # Increment repost_count after successful publish
            # Note: This happens AFTER publish, so price reduction logic (which runs before publish)
            # sees the count from the PREVIOUS run. This is intentional: the first publish uses
            # repost_count=0 (no reduction), the second publish uses repost_count=1 (first reduction), etc.
            current_reposts = int(ad_cfg_orig.get("repost_count", ad_cfg.repost_count or 0))
            ad_cfg_orig["repost_count"] = current_reposts + 1
            ad_cfg.repost_count = ad_cfg_orig["repost_count"]

        # Persist price_reduction_count after successful publish/update.
        # This ensures failed submissions don't incorrectly increment the reduction counter.
        if ad_cfg.price_reduction_count is not None and ad_cfg.price_reduction_count > 0:
            ad_cfg_orig["price_reduction_count"] = ad_cfg.price_reduction_count

        if mode == AdUpdateStrategy.REPLACE:
            LOG.info(" -> SUCCESS: ad published with ID %s", ad_id)
        else:
            LOG.info(" -> SUCCESS: ad updated with ID %s", ad_id)

        try:
            _dicts.save_dict(ad_file, ad_cfg_orig)
        except Exception:
            for old_path, new_path in image_result.renamed_paths:
                try:
                    new_path.rename(old_path)
                except OSError:
                    LOG.warning("Failed to rollback image rename: %s -> %s", new_path, old_path)
            raise
        # Rename the YAML file and containing folder after saving, because the
        # saved file itself may move as part of this opt-in local migration.
        file_folder_result = _local_path_renaming.rename_local_ad_file_and_folder_after_id_change(
            Path(ad_file),
            old_id = old_ad_id,
            new_id = ad_id,
            ad_file_name_template = self.config.download.ad_file_name_template,
            folder_name_template = self.config.download.folder_name_template,
            enabled = self.config.publishing.local_path_renaming.mode == "TEMPLATE_MATCH",
        )
        rename_result = replace(
            file_folder_result,
            renamed_image_count = image_result.renamed_count,
            blocked_image_count = image_result.blocked_count,
            yaml_old_id = old_ad_id,
        )
        self._log_local_path_rename_result(rename_result, ad_id)
        # NOTE: ad_file string may differ from rename_result.ad_file after the call above.
        # ad_file is stale at this point (pointing to the pre-rename path), but
        # no code in publish_ad() dereferences it after this line, so the drift
        # has no runtime impact.

    async def _try_recover_ad_id_from_redirect(self) -> int | None:
        """Try to extract the published ad ID from page tracking data.

        Used as a fallback when the confirmation page auto-redirects before
        the URL can be polled. Checks document.referrer first, then scans
        inline script content for the confirmation URL containing adId.

        Returns:
            The extracted ad ID, or None if no ad ID could be found.
        """
        # Layer 1: check document.referrer for the confirmation URL.
        # Note: referrer reflects the most recent navigation, so a stale ID from a
        # previous publish is not a concern — the publish flow navigates to the edit
        # page first, resetting the referrer before the confirmation redirect occurs.
        try:
            referrer = str(await self.web_execute("document.referrer") or "")
        except (TimeoutError, ProtocolException) as ex:
            LOG.debug("document.referrer lookup failed (%s), skipping to script scan", type(ex).__name__)
            referrer = ""

        if "p-anzeige-aufgeben-bestaetigung.html?adId=" in referrer:
            try:
                query = urllib_parse.parse_qs(urllib_parse.urlparse(referrer).query)
                ad_id_str = query.get("adId", [])[0]
                ad_id = int(ad_id_str)
                LOG.debug("Extracted ad ID %s from document.referrer fallback", ad_id)
                return ad_id
            except (IndexError, ValueError, TypeError):
                LOG.debug("Failed to parse ad ID from document.referrer: %s", referrer)

        # Layer 2: scan inline <script> tags for confirmation URL with adId
        try:
            script_content = str(await self.web_execute(
                "[...document.querySelectorAll('script')].map(s => s.textContent).join('\\n')"
            ) or "")
            match = re.search(r"p-anzeige-aufgeben-bestaetigung\.html\?adId=(\d+)", script_content)
            if match:
                ad_id = int(match.group(1))
                LOG.debug("Extracted ad ID %s from inline script fallback", ad_id)
                return ad_id
        except (TimeoutError, ProtocolException, ValueError, TypeError) as ex:
            LOG.debug("Script content scan failed (%s): %s", type(ex).__name__, ex)

        return None

    async def __set_input_value(self, element_id:str, value:str) -> None:
        """Sets a framework-controlled input value using the native DOM setter to trigger onChange."""
        await self.web_find(By.ID, element_id)  # raises TimeoutError if element is absent
        js_element_id = json.dumps(element_id)
        js_value = json.dumps(value)
        await self.web_execute(
            f"(function(id,v){{"
            "var el=document.getElementById(id);"
            "if(!el)return;"
            "var tag=el.tagName.toLowerCase();"
            "var proto=tag==='textarea'?window.HTMLTextAreaElement:window.HTMLInputElement;"
            "var setter=Object.getOwnPropertyDescriptor(proto.prototype,'value').set;"
            "setter.call(el,v);"
            "el.dispatchEvent(new Event('input',{bubbles:true}));"
            "el.dispatchEvent(new Event('change',{bubbles:true}));"
            f"}})({js_element_id},{js_value})"
        )

    @staticmethod
    def __location_matches_target(target:str, candidate:str | None) -> bool:
        if not candidate:
            return False

        normalized_target = " ".join(target.split()).casefold()
        normalized_candidate = " ".join(candidate.split()).casefold()
        if not normalized_target or not normalized_candidate:
            return False

        if normalized_target == normalized_candidate:
            return True

        if " - " in normalized_target:
            return False

        if normalized_candidate.startswith(f"{normalized_target} - "):
            return True

        candidate_city = normalized_candidate.rsplit(" - ", maxsplit = 1)[-1]
        return normalized_target == candidate_city

    async def __city_option_text(self, option:Element) -> str:
        text = str(getattr(option, "text", "") or "").strip()
        if text:
            return text
        try:
            return (await self._extract_visible_text(option)).strip()
        except TimeoutError:
            return ""

    async def __read_city_selection_text(self) -> str | None:
        city_timeout = self.timeout("default")
        quick_dom_timeout = self.timeout("quick_dom")
        try:
            city_element = await self.web_find(By.ID, "ad-city", timeout = city_timeout)
        except TimeoutError:
            return None
        if city_element is None:
            return None

        if city_element.local_name == "input":
            live_value = await city_element.apply("(elem) => (elem.value || '').trim()")
            if isinstance(live_value, str) and live_value.strip():
                return live_value

        try:
            selected_text = await self.web_text(By.ID, "ad-city-selected-option", timeout = quick_dom_timeout)
            if selected_text:
                return selected_text
        except TimeoutError:
            # #ad-city-selected-option may not exist in all DOM states; fall through to textContent
            pass

        live_text = await city_element.apply("(elem) => (elem.textContent || '').trim()")
        if isinstance(live_text, str) and live_text.strip():
            return live_text

        try:
            selected_text = await self.web_text(By.ID, "ad-city", timeout = quick_dom_timeout)
            if selected_text:
                return selected_text
        except TimeoutError:
            return None
        return None

    async def __select_city_combobox_option(self, target:str) -> None:
        quick_dom_timeout = self.timeout("quick_dom")
        city_flow_timeout = self.timeout("default")

        await self.web_click(By.ID, "ad-city", timeout = quick_dom_timeout)
        city_element = await self.web_find(By.ID, "ad-city", timeout = quick_dom_timeout)
        city_attrs = getattr(city_element, "attrs", None)
        listbox_id_raw = None
        if city_attrs is not None:
            listbox_id_raw = city_attrs.get("aria-controls") if hasattr(city_attrs, "get") else getattr(city_attrs, "aria-controls", None)
        listbox_id = next((candidate for candidate in str(listbox_id_raw or "").split() if candidate.strip()), "")
        if not listbox_id:
            listbox_id = "ad-city-menu"

        listbox_id_css = listbox_id.replace("\\", "\\\\").replace('"', '\\"')
        listbox_scope = f'[id="{listbox_id_css}"]'
        option_selector = (
            f"{listbox_scope} [role='option'], "
            f"{listbox_scope} li[aria-selected='true'], {listbox_scope} li[aria-selected='false'], "
            f"{listbox_scope} button[aria-selected='true'], {listbox_scope} button[aria-selected='false']"
        )

        candidates:list[Element] = []

        async def _options_available() -> bool:
            nonlocal candidates
            try:
                candidates = await self.web_find_all(By.CSS_SELECTOR, option_selector, timeout = quick_dom_timeout)
            except TimeoutError:
                candidates = []
            return bool(candidates)

        try:
            await self.web_await(_options_available, timeout = city_flow_timeout)
        except TimeoutError as ex:
            raise TimeoutError(_("City combobox options did not load for location: %s") % target) from ex

        def normalize(value:str) -> str:
            return " ".join(value.split()).casefold()

        target_norm = normalize(target)
        option_entries = [(candidate, normalize(await self.__city_option_text(candidate))) for candidate in candidates]

        exact_match = next((entry[0] for entry in option_entries if entry[1] == target_norm), None)
        city_matches:list[Element] = []
        prefix_matches:list[Element] = []
        if " - " not in target_norm:
            city_matches = [entry[0] for entry in option_entries if entry[1] and entry[1].rsplit(" - ", maxsplit = 1)[-1] == target_norm]
            prefix_matches = [entry[0] for entry in option_entries if entry[1].startswith(f"{target_norm} - ")]

        if exact_match is None and len(city_matches) > 1:
            raise TimeoutError(_("City combobox options are ambiguous for location: %s") % target)

        if exact_match is None and not city_matches and len(prefix_matches) > 1:
            raise TimeoutError(_("City combobox options are ambiguous for location: %s") % target)

        selected_option = exact_match or (city_matches[0] if city_matches else None) or (prefix_matches[0] if len(prefix_matches) == 1 else None)
        if selected_option is None:
            raise TimeoutError(_("No city combobox option matched location: %s") % target)

        await selected_option.click()

        async def _selection_converged() -> bool:
            selected_city = await self.__read_city_selection_text()
            return self.__location_matches_target(target, selected_city)

        try:
            await self.web_await(_selection_converged, timeout = city_flow_timeout)
        except TimeoutError as ex:
            raise TimeoutError(_("City selection did not converge for location: %s") % target) from ex

    async def __set_contact_location(self, location:str) -> None:
        target = location.strip()
        if not target:
            return

        selected_city = await self.__read_city_selection_text()
        if self.__location_matches_target(target, selected_city):
            return

        city_timeout = self.timeout("default")
        city_element = await self.web_find(By.ID, "ad-city", timeout = city_timeout)
        if city_element is None:
            raise TimeoutError(_("Unsupported city element type while setting contact location: <%s>") % "missing")
        city_tag = city_element.local_name
        city_attrs = getattr(city_element, "attrs", {}) or {}
        city_role = str(city_attrs.get("role") or "").casefold()

        # kleinanzeigen.de switched the city field to a read-only <input> whose
        # value is derived from the entered zip code; it is no longer a
        # selectable combobox. When the page already prefilled a non-empty
        # value, accept it instead of trying (and failing) to open a combobox.
        if city_tag == "input" and "readonly" in city_attrs and selected_city:
            LOG.info(
                "ad-city is a <input readonly> with value '%s' (zip-derived) - accepting instead of combobox selection.",
                selected_city,
            )
            return

        if city_tag != "button" or city_role != "combobox":
            raise TimeoutError(_("Unsupported city element type while setting contact location: <%s>") % city_tag)

        await self.__select_city_combobox_option(target)

    async def __set_contact_fields(self, contact:Contact) -> None:
        #############################
        # set contact zipcode + location
        #############################
        if contact.zipcode:
            try:
                await self.web_input(By.ID, "ad-zip-code", str(contact.zipcode))
            except TimeoutError as ex:
                LOG.warning("Could not set contact zipcode: %s", ex)
                raise TimeoutError(_("Failed to set contact zipcode: %s") % contact.zipcode) from ex

            if contact.location:
                await self.__set_contact_location(contact.location)

        #############################
        # set contact street
        #############################
        if contact.street:
            try:
                if await self.web_check(By.ID, "ad-street", Is.DISABLED):
                    await self.web_click(By.ID, "ad-address-visibility")
                    await self.web_sleep()
                await self.__set_input_value("ad-street", contact.street)
            except TimeoutError:
                LOG.warning("Could not set contact street.")

        #############################
        # set contact name
        #############################
        if contact.name:
            try:
                if not await self.web_check(By.ID, "ad-name", Is.READONLY):
                    await self.__set_input_value("ad-name", contact.name)
            except TimeoutError:
                LOG.warning("Could not set contact name.")

        #############################
        # set contact phone
        #############################
        if contact.phone:
            phone_elem = await self.web_probe(By.ID, "ad-phone", timeout = self.timeout("quick_dom"))
            if phone_elem is None:
                LOG.info(
                    "Phone number field not present on page. This is expected for many private accounts; commercial accounts may still support phone numbers."
                )
            else:
                try:
                    if await self.web_check(By.ID, "ad-phone", Is.DISABLED, timeout = self.timeout("quick_dom")):
                        await self.web_click(By.ID, "ad-phone-visibility", timeout = self.timeout("quick_dom"))
                        await self.web_sleep()
                    await self.__set_input_value("ad-phone", contact.phone)
                except TimeoutError as ex:
                    LOG.warning("Could not set contact phone despite visible phone field: %s", ex)

    async def update_ads(self, ad_cfgs:list[tuple[str, Ad, dict[str, Any]]]) -> None:
        """
        Updates a list of ads.
        The list gets filtered, so that only already published ads will be updated.
        Calls publish_ad in MODIFY mode.

        Args:
            ad_cfgs: List of ad configurations

        Returns:
            None
        """
        count = 0
        failed_count = 0
        max_retries = SUBMISSION_MAX_RETRIES

        published_ads = await self._fetch_published_ads()

        for idx, (ad_file, ad_cfg, ad_cfg_orig) in enumerate(ad_cfgs, start = 1):
            LOG.info("Processing %s/%s: '%s' from [%s]...", idx, len(ad_cfgs), ad_cfg.title, ad_file)

            ad = next((ad for ad in published_ads if ad["id"] == ad_cfg.id), None)

            if not ad:
                LOG.warning(" -> SKIPPED: ad '%s' (ID: %s) not found in published ads", ad_cfg.title, ad_cfg.id)
                continue

            if ad["state"] == "paused":
                LOG.info("Skipping because ad is reserved")
                continue

            count += 1
            success = False
            baseline_price = ad_cfg.price
            baseline_price_reduction_count = ad_cfg.price_reduction_count

            for attempt in range(1, max_retries + 1):
                try:
                    ad_cfg.price = baseline_price
                    ad_cfg.price_reduction_count = baseline_price_reduction_count
                    await self.publish_ad(ad_file, ad_cfg, ad_cfg_orig, published_ads, AdUpdateStrategy.MODIFY)
                    success = True
                    break
                except asyncio.CancelledError:
                    raise
                except PublishSubmissionUncertainError as ex:
                    await self._capture_publish_error_diagnostics_if_enabled(ad_cfg, ad_cfg_orig, ad_file, attempt, ex)
                    LOG.warning(
                        "Attempt %s/%s for '%s' reached submit boundary but failed: %s. Not retrying to prevent duplicate modifications.",
                        attempt,
                        max_retries,
                        ad_cfg.title,
                        ex,
                    )
                    LOG.warning("Manual recovery required for '%s'. Check 'Meine Anzeigen' to confirm whether the update was applied.", ad_cfg.title)
                    failed_count += 1
                    break
                except CategoryResolutionError as ex:
                    await self._capture_publish_error_diagnostics_if_enabled(ad_cfg, ad_cfg_orig, ad_file, attempt, ex)
                    LOG.error("Category resolution failed for '%s': %s. Skipping ad (configuration error, no retry).", ad_cfg.title, ex)
                    failed_count += 1
                    break
                except (TimeoutError, ProtocolException) as ex:
                    await self._capture_publish_error_diagnostics_if_enabled(ad_cfg, ad_cfg_orig, ad_file, attempt, ex)
                    if attempt >= max_retries:
                        LOG.error("All %s attempts failed for '%s': %s. Skipping ad.", max_retries, ad_cfg.title, ex)
                        failed_count += 1
                        break

                    LOG.warning("Attempt %s/%s failed for '%s': %s. Retrying...", attempt, max_retries, ad_cfg.title, ex)
                    await self.web_sleep(2_000)

            if success:
                try:
                    publish_timeout = self.timeout("publishing_result")
                    await self.web_await(self.__check_publishing_result, timeout = publish_timeout)
                except TimeoutError:
                    LOG.warning(" -> Could not confirm update for '%s', but changes may be online", ad_cfg.title)

        LOG.info("############################################")
        if failed_count > 0:
            LOG.info("DONE: updated %s (%s failed after retries)", pluralize("ad", count - failed_count), failed_count)
        else:
            LOG.info("DONE: updated %s", pluralize("ad", count))
        LOG.info("############################################")

    async def __set_condition(self, condition_value:str) -> bool:
        """Try to set condition via dialog path.

        Returns True when dialog handling succeeded, otherwise False to indicate
        that caller should use generic special-attribute handling.
        """
        canonical_value, legacy_value = _ad_form_helpers.normalize_condition(condition_value)
        if legacy_value is not None:
            LOG.warning("Condition value [%s] is deprecated; update your config to [%s].", legacy_value, canonical_value)

        short_timeout = self.timeout("quick_dom")
        condition_trigger_xpath = "//label[contains(@for, '.condition')]/following::button[@aria-haspopup='dialog' or @aria-haspopup='true'][1]"

        condition_trigger = await self.web_probe(By.XPATH, condition_trigger_xpath, timeout = short_timeout)
        if condition_trigger is None:
            LOG.debug("Condition dialog trigger not available for [%s]; falling back to generic handler.", condition_value)
            return False

        trigger_id = str(condition_trigger.attrs.get("id") or "")
        trigger_controls = str(condition_trigger.attrs.get("aria-controls") or "")
        LOG.debug("Condition dialog trigger resolved: id='%s', aria-controls='%s'", trigger_id, trigger_controls)

        # Some categories render condition as a combobox and the broad dialog-trigger XPath
        # may accidentally resolve to shipping controls (for example: id='ad-shipping-options').
        # In that case we deliberately skip the dialog path and fall back to generic handling.
        if "shipping" in trigger_id.lower() or "shipping" in trigger_controls.lower():
            LOG.debug(
                "Condition dialog trigger appears to be shipping-related (id='%s', aria-controls='%s'); skipping dialog path for condition_s.",
                trigger_id,
                trigger_controls,
            )
            return False

        # CONDITION_GERMAN_TO_API maps German legacy condition tiers to English API
        # values. Some legacy tiers are intentionally collapsed by the API
        # (e.g. "sehr_gut" / legacy "very good" maps to "like_new").
        # Build candidate_values by probing canonical_value first to avoid quick_dom
        # timeout delays on the current API-valued dialog, then legacy_value as fallback.
        candidate_values:list[str] = [canonical_value]
        if legacy_value is not None:
            candidate_values.append(legacy_value)

        try:
            await condition_trigger.click()
            await self.web_find(By.XPATH, '//*[self::dialog or @role="dialog"]', timeout = short_timeout)
            condition_radio = None
            for candidate in candidate_values:
                condition_radio = await self.web_probe(
                    By.XPATH,
                    f"//*[self::dialog or @role='dialog']//input[@type='radio' and @value={_ad_form_helpers.xpath_literal(candidate)}]",
                    timeout = short_timeout,
                )
                if condition_radio is not None:
                    break
            if condition_radio is None:
                raise TimeoutError(f"No condition radio matched values {candidate_values}")
            condition_radio_id = str(condition_radio.attrs.get("id") or "")
            if condition_radio_id:
                try:
                    await self.web_click(By.XPATH, f"//*[self::dialog or @role='dialog']//label[@for={_ad_form_helpers.xpath_literal(condition_radio_id)}]")
                except TimeoutError:
                    await condition_radio.click()
            else:
                await condition_radio.click()
        except TimeoutError as ex:
            LOG.debug("Unable to select condition [%s]", condition_value, exc_info = True)
            raise TimeoutError(_("Failed to set attribute '%s'") % "condition_s") from ex

        try:
            # Click accept button
            await self.web_click(By.XPATH, '//*[self::dialog or @role="dialog"]//button[.//span[text()="Bestätigen"]]')
        except TimeoutError as ex:
            raise TimeoutError(_("Unable to close condition dialog!")) from ex

        return True

    async def __set_category(self, category:str | None, ad_file:str) -> None:
        # click on something to trigger automatic category detection
        await self.web_click(By.ID, "ad-description")

        is_category_auto_selected = False
        category_path_elem = await self.web_probe(By.ID, "ad-category-path")
        if category_path_elem and await self._extract_visible_text(category_path_elem):
            is_category_auto_selected = True

        if category:
            await self.web_sleep()  # workaround for https://github.com/Second-Hand-Friends/kleinanzeigen-bot/issues/39
            await self.web_click(By.XPATH, "//a[contains(., 'Kategorie')] | //button[contains(., 'Kategorie')]")
            await self.web_find(By.XPATH, "//button[contains(., 'Weiter')]")

            category_url = f"{self.root_url}/p-kategorie-aendern.html#?path={category}"
            await self.web_open(category_url)
            await self.web_click(By.XPATH, "//button[contains(., 'Weiter')]")

            # When the configured path cannot be resolved (e.g. outdated or ambiguous),
            # the site falls back to a React category-suggestion radio picker. Handle it
            # by matching a path segment against one of the offered suggestions.
            await self.__resolve_category_suggestions(category)
        else:
            ensure(is_category_auto_selected, f"No category specified in [{ad_file}] and automatic category detection failed")

    async def __resolve_category_suggestions(self, category:str) -> None:
        """Handle Kleinanzeigen's post-redesign category-suggestion picker.

        If ``fieldset#ad-category-picker`` is rendered after the category change
        flow (because the configured path could not be resolved), try to click
        the suggestion whose radio ``value`` matches one of the segments of
        ``category`` (deepest first). The radio input is ``sr-only``, so clicks
        go on the associated ``<label for="...">``.

        If the picker shell is present but radios have not rendered yet, retry
        once after a short pause and then raise ``TimeoutError`` so the caller
        can treat it as a retryable pre-submit failure. Raises
        ``CategoryResolutionError`` with the list of offered suggestions if none
        of the segments match — surfaces an actionable error instead of letting
        the submit retry loop trip the duplicate-guard.
        """
        picker_timeout = self.timeout("quick_dom")
        picker = await self.web_probe(By.ID, "ad-category-picker", timeout = picker_timeout)
        if picker is None:
            return

        radio_selector = "#ad-category-picker input[type='radio'][name='category-suggestions']"
        radio_by_value:dict[str, Element] = {}
        for attempt in range(2):
            try:
                radios = await self.web_find_all(By.CSS_SELECTOR, radio_selector, timeout = picker_timeout)
            except TimeoutError:
                radios = []

            radio_by_value = {}
            for radio in radios:
                value = str(cast(Any, radio.attrs.get("value")) or "").strip()
                if value and value not in radio_by_value:
                    radio_by_value[value] = radio

            if radio_by_value:
                break

            if attempt == 0:
                await self.web_sleep(200, 350)

        if not radio_by_value:
            raise TimeoutError(_("Category suggestion picker element found but no radio suggestions rendered after waiting."))

        # Try deepest-first segments so "73/76/sachbuecher" first probes the leaf, then 76, then 73.
        for segment in (seg.strip() for seg in reversed(category.split("/")) if seg.strip()):
            radio = radio_by_value.get(segment)
            if radio is None:
                continue
            radio_id = str(cast(Any, radio.attrs.get("id")) or "")
            try:
                if radio_id:
                    await self.web_click(
                        By.XPATH,
                        f"//fieldset[@id='ad-category-picker']//label[@for={_ad_form_helpers.xpath_literal(radio_id)}]",
                        timeout = picker_timeout,
                    )
                else:
                    await radio.click()
            except TimeoutError:
                await radio.click()
            LOG.info("Category suggestion picker: selected value=%s (matched path segment).", segment)
            return

        offered = ", ".join(sorted(radio_by_value.keys())) or "(none)"
        message = _("Category suggestion picker shown, but no segment of configured path '%(category)s' matched the offered suggestions [%(offered)s]. Update the ad's 'category' to an offered ID or a valid full path.")  # noqa: E501
        raise CategoryResolutionError(message % {"category": category, "offered": offered})

    @staticmethod
    def __special_attribute_candidate_priority(elem:Element) -> tuple[int, int]:
        local_name = elem.local_name
        elem_type = str(cast(Any, elem.attrs.get("type")) or "").lower()
        role = str(cast(Any, elem.attrs.get("role")) or "").lower()

        if local_name == "button" and role == "combobox":
            return (0, 0)
        if local_name == "input" and elem_type in {"text", ""} and role == "combobox":
            return (1, 0)
        if local_name == "select":
            return (2, 0)
        if elem_type == "checkbox":
            return (3, 0)
        if local_name in {"input", "textarea"} and elem_type != "hidden":
            return (4, 0)
        if elem_type == "hidden":
            return (9, 1)
        return (8, 0)

    @staticmethod
    def __describe_special_attribute_candidate(elem:Element) -> str:
        elem_id = cast(str | None, elem.attrs.get("id"))
        elem_name = cast(str | None, elem.attrs.get("name"))
        elem_type = cast(str | None, elem.attrs.get("type"))
        elem_role = cast(str | None, elem.attrs.get("role"))
        return f"{elem.local_name}#'{elem_id}' name='{elem_name}' type='{elem_type}' role='{elem_role}'"

    def __pick_special_attribute_candidate(self, candidates:Sequence[Element], special_attribute_key:str) -> Element:
        ensure(candidates, f"No candidates found for special attribute [{special_attribute_key}]")
        ranked_candidates = sorted(
            enumerate(candidates),
            key = lambda entry: (self.__special_attribute_candidate_priority(entry[1]), entry[0]),
        )
        selected_idx, selected = ranked_candidates[0]

        if len(candidates) > 1:
            debug_candidates = ", ".join(f"#{idx}:{self.__describe_special_attribute_candidate(candidate)}" for idx, candidate in enumerate(candidates))
            LOG.debug(
                "Attribute field '%s' matched %s elements. Selected #%s: %s. Candidates: %s",
                special_attribute_key,
                len(candidates),
                selected_idx,
                self.__describe_special_attribute_candidate(selected),
                debug_candidates,
            )

        return selected

    async def __set_special_attributes(self, ad_cfg:Ad) -> None:
        if not ad_cfg.special_attributes:
            return

        LOG.debug("Found %i special attributes", len(ad_cfg.special_attributes))
        for special_attribute_key, special_attribute_value in ad_cfg.special_attributes.items():
            # Ensure special_attribute_value is treated as a string
            special_attribute_value_str = str(special_attribute_value)
            normalized_special_attribute_key = re.sub(r"_[a-z]+$", "", special_attribute_key).rsplit(".", maxsplit = 1)[-1]
            if not _ad_form_helpers.SPECIAL_ATTRIBUTE_TOKEN_RE.fullmatch(normalized_special_attribute_key):
                LOG.debug(
                    "Attribute field '%s' has unsupported normalized key '%s'.",
                    special_attribute_key,
                    normalized_special_attribute_key,
                )
                raise TimeoutError(_("Failed to set attribute '%s'") % special_attribute_key)

            if normalized_special_attribute_key == "condition":
                LOG.debug("Special attribute [%s]: trying dedicated condition dialog path", special_attribute_key)
                if await self.__set_condition(special_attribute_value_str):
                    LOG.debug("Special attribute [%s]: condition dialog path succeeded", special_attribute_key)
                    continue

                LOG.info("Condition dialog not available, falling back to generic attribute handler for [%s]...", special_attribute_key)
                special_attribute_value_str = _ad_form_helpers.normalize_condition(special_attribute_value_str)[0]

            LOG.debug("Setting special attribute [%s] to [%s]...", special_attribute_key, special_attribute_value_str)
            id_suffix_literal = _ad_form_helpers.xpath_literal(f".{normalized_special_attribute_key}")
            name_suffix_literal = _ad_form_helpers.xpath_literal(f".{normalized_special_attribute_key}]")
            name_plus_literal = _ad_form_helpers.xpath_literal(f".{normalized_special_attribute_key}+")
            bare_id_literal = _ad_form_helpers.xpath_literal(normalized_special_attribute_key)
            bare_name_literal = _ad_form_helpers.xpath_literal(f"attributeMap[{normalized_special_attribute_key}]")
            original_key_literal = _ad_form_helpers.xpath_literal(special_attribute_key)
            # Match attribute fields by five patterns:
            # 1) exact id                 -> @id={bare_id_literal}
            # 2) dotted id suffix         -> ... = {id_suffix_literal}
            # 3) exact attributeMap name  -> @name={bare_name_literal}
            # 4) dotted name suffix       -> ... = {name_suffix_literal}
            # 5) compound key marker      -> contains(@name, {name_plus_literal})
            # Literals are derived via _ad_form_helpers.xpath_literal from normalized_special_attribute_key.
            # 6) original config key      -> contains(@name, {original_key_literal}) for compound keys
            special_attr_xpath = (
                "//*["
                f"@id={bare_id_literal}"
                f" or (contains(@id, '.') and substring(@id, string-length(@id) - string-length({id_suffix_literal}) + 1) = {id_suffix_literal})"
                f" or @name={bare_name_literal}"
                f" or (contains(@name, '.') and substring(@name, string-length(@name) - string-length({name_suffix_literal}) + 1) = {name_suffix_literal})"
                f" or contains(@name, {name_plus_literal})"
                f" or contains(@name, {original_key_literal})"
                "]"
            )
            quick_dom = self.timeout("quick_dom")
            try:
                if special_attribute_key == "condition_s":
                    special_attr_probe = await self.web_probe(By.XPATH, special_attr_xpath, timeout = quick_dom)
                    if special_attr_probe is None:
                        LOG.warning("Special attribute '%s' is not available for the selected category. Skipping.", special_attribute_key)
                        continue
                special_attr_candidates = await self.web_find_all(
                    By.XPATH,
                    special_attr_xpath,
                )
                special_attr_elem = self.__pick_special_attribute_candidate(special_attr_candidates, special_attribute_key)
            except AssertionError as ex:
                LOG.debug(
                    "Attribute field '%s' (normalized: '%s') could not be found.",
                    special_attribute_key,
                    normalized_special_attribute_key,
                )
                if special_attribute_key == "condition_s":
                    LOG.warning("Special attribute '%s' is not available for the selected category. Skipping.", special_attribute_key)
                    continue
                raise TimeoutError(_("Failed to set attribute '%s'") % special_attribute_key) from ex

            try:
                elem_id = cast(str | None, special_attr_elem.attrs.get("id"))
                elem_type = str(cast(Any, special_attr_elem.attrs.get("type")) or "").lower()
                elem_role = str(cast(Any, special_attr_elem.attrs.get("role")) or "").lower()
                elem_selector_type = By.ID if elem_id else By.XPATH
                elem_selector_value = elem_id or special_attr_xpath

                # If the only match was a hidden backing input, search for the
                # associated <button role="combobox"> by walking up the DOM tree.
                if elem_type == "hidden":
                    LOG.debug("Attribute field '%s': only matched hidden input, searching for associated button combobox...", special_attribute_key)
                    hidden_input_name = special_attr_elem.attrs.get("name")
                    if not hidden_input_name:
                        raise TimeoutError(_("Failed to set attribute '%s'") % special_attribute_key)
                    associated_button_id = await self._find_associated_button_combobox(
                        hidden_input_name = str(hidden_input_name)
                    )
                    if associated_button_id is None:
                        raise TimeoutError(_("Failed to set attribute '%s'") % special_attribute_key)
                    LOG.debug("Attribute field '%s': found associated button combobox id='%s'", special_attribute_key, associated_button_id)
                    await self.__select_button_combobox(associated_button_id, special_attribute_value_str)
                    LOG.debug("Successfully set attribute field [%s] to [%s]...", special_attribute_key, special_attribute_value_str)
                    continue

                if special_attr_elem.local_name == "select":
                    LOG.debug("Attribute field '%s' seems to be a select...", special_attribute_key)
                    await self.web_select(elem_selector_type, elem_selector_value, special_attribute_value_str)
                elif elem_type == "checkbox":
                    LOG.debug("Attribute field '%s' seems to be a checkbox...", special_attribute_key)
                    truthy_values = {"1", "true", "yes", "on", "ja", "checked"}
                    falsy_values = {"", "0", "false", "no", "off", "nein", "unchecked", "none"}
                    normalized_checkbox_value = special_attribute_value_str.strip().lower()
                    if normalized_checkbox_value in truthy_values:
                        desired_checked = True
                    elif normalized_checkbox_value in falsy_values:
                        desired_checked = False
                    else:
                        LOG.debug(
                            "Attribute field '%s' has unsupported checkbox value '%s'.",
                            special_attribute_key,
                            special_attribute_value_str,
                        )
                        raise TimeoutError(_("Failed to set attribute '%s'") % special_attribute_key)

                    current_checked_attr = special_attr_elem.attrs.get("checked")
                    if isinstance(current_checked_attr, bool):
                        current_checked = current_checked_attr
                    else:
                        normalized_current_checked = str(current_checked_attr).strip().lower() if current_checked_attr is not None else ""
                        current_checked = normalized_current_checked not in falsy_values

                    if desired_checked != current_checked:
                        await self.web_click(elem_selector_type, elem_selector_value)
                elif special_attr_elem.local_name == "button" and elem_role == "combobox":
                    LOG.debug("Attribute field '%s' seems to be a button combobox (click-to-open dropdown)...", special_attribute_key)
                    ensure(elem_id, f"No id available for button combobox special attribute [{special_attribute_key}]")
                    await self.__select_button_combobox(cast(str, elem_id), special_attribute_value_str)
                elif elem_role == "combobox" and elem_type in {"text", ""} and special_attr_elem.local_name == "input":
                    LOG.debug("Attribute field '%s' seems to be a Combobox (i.e. text input with filtering dropdown)...", special_attribute_key)
                    await self.web_select_combobox(elem_selector_type, elem_selector_value, special_attribute_value_str)
                else:
                    LOG.debug("Attribute field '%s' seems to be a text input...", special_attribute_key)
                    await self.web_input(elem_selector_type, elem_selector_value, special_attribute_value_str)
            except TimeoutError as ex:
                LOG.debug("Failed to set attribute field '%s' via known input types.", special_attribute_key)
                raise TimeoutError(_("Failed to set attribute '%s'") % special_attribute_key) from ex
            LOG.debug("Successfully set attribute field [%s] to [%s]...", special_attribute_key, special_attribute_value_str)

    # TODO: Issue #930 — migrate to web_select_button_combobox (display-text-based, no React fiber)
    async def __select_button_combobox(self, elem_id:str, value:str) -> None:
        """Select an option from a <button role="combobox"> dropdown by its API value.

        Clicks the button to open the listbox, reads the options data from the React fiber
        (which maps API values to display labels), and clicks the matching option.
        """
        await self.web_click(By.ID, elem_id)
        listbox_id = f"{elem_id}-menu"
        await self.web_find(By.ID, listbox_id)
        js_btn_id = json.dumps(elem_id)
        js_listbox_id = json.dumps(listbox_id)
        js_value = json.dumps(value)
        ok = await self.web_execute(f"""(function() {{
            const listbox = document.getElementById({js_listbox_id});
            if (!listbox) return false;
            const liOptions = Array.from(listbox.querySelectorAll('[role="option"]'));
            const btnEl = document.getElementById({js_btn_id});
            if (!btnEl) return false;
            const fiberKey = Object.keys(btnEl).find(k => k.startsWith('__reactFiber'));
            let fiber = fiberKey ? btnEl[fiberKey] : null;
            for (let i = 0; i < 20 && fiber; i++, fiber = fiber.return) {{
                if (fiber.memoizedProps && fiber.memoizedProps.options) {{
                    const optionsData = fiber.memoizedProps.options;
                    for (let j = 0; j < optionsData.length; j++) {{
                        if (optionsData[j].value === {js_value} && liOptions[j]) {{
                            liOptions[j].click();
                            return true;
                        }}
                    }}
                    return false;
                }}
            }}
            return false;
        }})()""")
        if not ok:
            raise TimeoutError(_("Option '%(value)s' not found in button combobox '%(id)s'") % {"value": value, "id": elem_id})

    async def __set_shipping(self, ad_cfg:Ad, mode:AdUpdateStrategy = AdUpdateStrategy.REPLACE) -> None:
        short_timeout = self.timeout("quick_dom")
        if ad_cfg.shipping_type == "PICKUP":
            pickup_radio = await self.web_probe(By.ID, "ad-shipping-enabled-no", timeout = short_timeout)
            if pickup_radio is None:
                shipping_fieldset = await self.web_probe(By.ID, "ad-shipping-enabled", timeout = short_timeout)
                if shipping_fieldset is not None:
                    raise TimeoutError(
                        _("Shipping fieldset is rendered, but the pickup radio is missing; page may not be fully loaded.")
                    )
                # Some categories (notably books 76/77 and comics 76/77/15156) render no
                # shipping fieldset at all — those ads are PICKUP-only by site convention.
                LOG.debug("PICKUP: no shipping fieldset for this category; treating as already PICKUP.")
                return
            try:
                if not await self.web_check(By.ID, "ad-shipping-enabled-no", Is.SELECTED, timeout = short_timeout):
                    await self.web_click(By.ID, "ad-shipping-enabled-no", timeout = short_timeout)
            except TimeoutError as ex:
                LOG.debug(ex, exc_info = True)
                raise TimeoutError(_("Failed to set shipping attribute for type '%s'!") % ad_cfg.shipping_type) from ex
        elif ad_cfg.shipping_options:
            # Ensure shipping is enabled before opening the dialog (may already be selected)
            try:
                await self.web_click(By.ID, "ad-shipping-enabled-yes", timeout = short_timeout)
                await self.web_sleep(500, 800)
            except TimeoutError as ex:
                LOG.debug("Shipping enabled toggle not found before options dialog: %s", ex)
            await self.web_click(By.ID, "ad-shipping-options")

            if mode == AdUpdateStrategy.MODIFY:
                try:
                    # when "Andere Versandmethoden" is not available, go back and start over new
                    await self.web_find(By.XPATH, '//button[contains(., "Andere Versandmethoden")]', timeout = short_timeout)
                except TimeoutError:
                    await self.web_click(By.XPATH, '//button[contains(., "Zurück")]')

                    # in some categories we need to go another dialog back
                    try:
                        await self.web_find(By.XPATH, '//button[contains(., "Andere Versandmethoden")]', timeout = short_timeout)
                    except TimeoutError:
                        await self.web_click(By.XPATH, '//button[contains(., "Zurück")]')

            await self.web_click(By.XPATH, '//button[contains(., "Andere Versandmethoden")]')
            await self.__set_shipping_options(ad_cfg, mode)
        else:
            # Ensure shipping is enabled before opening the dialog (may already be selected)
            try:
                await self.web_click(By.ID, "ad-shipping-enabled-yes", timeout = short_timeout)
                await self.web_sleep(500, 800)
            except TimeoutError as ex:
                LOG.debug("Shipping enabled toggle not found before options dialog: %s", ex)

            # no options. only costs. Set custom shipping cost
            try:
                await self.web_click(By.ID, "ad-shipping-options")
            except TimeoutError as ex:
                LOG.debug(ex, exc_info = True)
                LOG.warning("Shipping options dialog entry not found. Legacy '.versand_s' select UI is no longer supported and requires dedicated rebuild.")
                raise TimeoutError(_("Unable to open shipping options dialog!")) from ex

            try:
                # when "Andere Versandmethoden" is not available, then we are already on the individual page
                await self.web_click(By.XPATH, '//button[contains(., "Andere Versandmethoden")]')
            except TimeoutError:
                # Dialog option not present; already on the individual shipping page.
                pass

            # only click on "Individueller Versand" when the price input is not available, otherwise it's already checked
            # (important for mode = UPDATE)
            individual_price_elem = await self.web_probe(By.ID, "ad-individual-shipping-price", timeout = short_timeout)
            if individual_price_elem is None:
                # Input not visible yet; click the individual shipping option.
                try:
                    await self.web_click(By.ID, "ad-individual-shipping-checkbox-control")
                except TimeoutError as ex:
                    LOG.debug(ex, exc_info = True)
                    raise TimeoutError(_("Unable to select individual shipping option!")) from ex

            if ad_cfg.shipping_costs is not None:
                price_str = str(ad_cfg.shipping_costs).replace(".", ",")
                # Native DOM setter + React-aware events: send_keys gets wiped by
                # React re-render after the ad-individual-shipping-checkbox-control click.
                # A re-render between web_find and web_execute inside __set_input_value can
                # also leave the write as a silent no-op, so verify and retry before "Fertig".
                max_attempts = 3
                for attempt in range(1, max_attempts + 1):
                    try:
                        await self.__set_input_value("ad-individual-shipping-price", price_str)
                        actual = await self.web_execute("document.getElementById('ad-individual-shipping-price')?.value")
                    except TimeoutError as ex:
                        # A re-render landing on web_find inside __set_input_value or on the
                        # readback web_execute can raise here; treat either as a transient
                        # failure so the outer loop can retry instead of bailing.
                        LOG.debug(ex, exc_info = True)
                        if attempt >= max_attempts:
                            raise TimeoutError(_("Unable to set shipping price!")) from ex
                        await self.web_sleep(300, 500)
                        continue
                    if actual == price_str:
                        break
                    if attempt >= max_attempts:
                        raise TimeoutError(_("Unable to set shipping price!"))
                    LOG.debug("shipping price not persisted (attempt %d/%d): got %r, expected %r", attempt, max_attempts, actual, price_str)
                    await self.web_sleep(300, 500)
            else:
                LOG.debug(
                    "Shipping option 'ad-individual-shipping-checkbox-control' selected but no shipping_costs provided; "
                    "leaving field 'ad-individual-shipping-price' unchanged."
                )

            try:
                await self.web_click(By.XPATH, '//button[contains(., "Fertig")]')
            except TimeoutError as ex:
                LOG.debug(ex, exc_info = True)
                raise TimeoutError(_("Unable to close shipping dialog!")) from ex

    async def __set_shipping_options(self, ad_cfg:Ad, mode:AdUpdateStrategy = AdUpdateStrategy.REPLACE) -> None:
        if not ad_cfg.shipping_options:
            raise ValueError(_("shipping_options must be provided"))

        # Resolve user-facing config names to carrier codes
        try:
            wanted_carrier_codes = [CARRIER_CODE_BY_OPTION[opt] for opt in set(ad_cfg.shipping_options)]
        except KeyError as ex:
            raise KeyError(_("Unknown shipping option(s), please refer to the documentation/README: %s") % ad_cfg.shipping_options) from ex

        # Determine the size group — all options must belong to the same group
        size_info = {SIZE_INFO_BY_CARRIER_CODE[code] for code in wanted_carrier_codes}
        if len(size_info) != 1:
            raise ValueError(_("You can only specify shipping options for one package size!"))
        ((shipping_size, shipping_radio_value),) = size_info
        wanted_codes = set(wanted_carrier_codes)
        all_codes_for_size = CARRIER_CODES_BY_SIZE[shipping_size]

        short_timeout = self.timeout("quick_dom")
        dialog = '//*[self::dialog or @role="dialog"]'

        try:
            # Select the size group via radio button value (e.g. "SMALL", "MEDIUM", "LARGE")
            size_radio_xpath = f'{dialog}//input[@type="radio" and @value="{shipping_radio_value}"]'
            shipping_size_radio = await self.web_find(By.XPATH, size_radio_xpath, timeout = short_timeout)
            shipping_size_radio_is_checked = shipping_size_radio.attrs.get("checked") is not None

            if not shipping_size_radio_is_checked:
                LOG.debug("Selecting size '%s' (radio value=%s)", shipping_size, shipping_radio_value)
                await self.web_click(By.XPATH, size_radio_xpath, timeout = short_timeout)

            await self.web_sleep(300, 500)
            await self.web_click(By.XPATH, f'{dialog}//button[contains(., "Weiter")]', timeout = short_timeout)
            await self.web_sleep(500, 800)

            # Toggle package checkboxes by carrier code value attribute.
            # IMPORTANT: REPLACE intentionally uses the same state-based sync as MODIFY.
            # Live DOM defaults after "Weiter" are not stable across size/category (issue #956),
            # so we must read current checkbox state and reconcile with desired state.
            LOG.debug("Using state-based shipping option sync for mode '%s'", mode)
            LOG.debug("Processing %d packages for size '%s'", len(all_codes_for_size), shipping_size)

            for carrier_code in all_codes_for_size:
                checkbox_xpath = f'{dialog}//input[@type="checkbox" and @value="{carrier_code}"]'
                checkbox = await self.web_find(By.XPATH, checkbox_xpath, timeout = short_timeout)
                is_checked = checkbox.attrs.get("checked") is not None
                should_be_checked = carrier_code in wanted_codes

                LOG.debug("Carrier '%s': checked=%s, wanted=%s", carrier_code, is_checked, should_be_checked)

                if is_checked != should_be_checked:
                    LOG.debug("Toggling carrier '%s'", carrier_code)
                    await self.web_click(By.XPATH, checkbox_xpath, timeout = short_timeout)
        except TimeoutError as ex:
            LOG.debug(ex, exc_info = True)
            raise TimeoutError(_("Failed to configure shipping options in dialog!")) from ex

        try:
            # Click apply button
            await self.web_click(By.XPATH, f'{dialog}//button[contains(., "Fertig")]', timeout = short_timeout)
        except TimeoutError as ex:
            raise TimeoutError(_("Unable to close shipping dialog!")) from ex

    async def __upload_images(self, ad_cfg:Ad) -> None:
        if not ad_cfg.images:
            return

        LOG.info(" -> found %s", pluralize("image", ad_cfg.images))
        hidden_marker_selector = "input[name^='adImages'][name$='.url']"
        quick_dom_timeout = self.timeout("quick_dom")

        # Capture marker baseline before this upload attempt to avoid counting stale values
        baseline_marker_count = 0
        try:
            baseline_markers = await self._web_find_all_once(By.CSS_SELECTOR, hidden_marker_selector, quick_dom_timeout)
            baseline_marker_count = sum(1 for marker in baseline_markers if _ad_form_helpers.get_marker_value(marker))
        except TimeoutError:
            baseline_marker_count = 0

        if baseline_marker_count:
            LOG.debug(" -> detected %d pre-existing image marker(s) before upload", baseline_marker_count)

        total_images = len(ad_cfg.images)
        for index, image in enumerate(ad_cfg.images, start = 1):
            image_upload:Element = await self.web_find(By.CSS_SELECTOR, "input[type=file]")
            LOG.info(" -> uploading image %s/%s [%s]", index, total_images, image)
            await image_upload.send_file(image)
            await self.web_sleep()

        # Wait for all images to be processed
        expected_count = len(ad_cfg.images)
        LOG.info(" -> waiting for %s to be processed...", pluralize("image", ad_cfg.images))

        async def count_processed_images() -> int:
            try:
                markers = await self._web_find_all_once(By.CSS_SELECTOR, hidden_marker_selector, quick_dom_timeout)
                marker_count = sum(1 for marker in markers if _ad_form_helpers.get_marker_value(marker))
            except TimeoutError:
                marker_count = 0

            return max(0, marker_count - baseline_marker_count)

        async def check_thumbnails_uploaded() -> bool:
            current_count = await count_processed_images()
            if current_count < expected_count:
                LOG.debug(" -> %d of %d images processed", current_count, expected_count)
            return current_count >= expected_count

        try:
            await self.web_await(check_thumbnails_uploaded, timeout = self.timeout("image_upload"), timeout_error_message = _("Image upload timeout exceeded"))
        except TimeoutError as ex:
            # Get current count for better error message
            current_count = await count_processed_images()
            raise TimeoutError(
                _("Not all images were uploaded within timeout. Expected %(expected)d, found %(found)d processed images.")
                % {"expected": expected_count, "found": current_count}
            ) from ex

        LOG.info(" -> all images uploaded successfully")

#############################
# main entry point
#############################


def main(args:list[str]) -> None:
    _cli = importlib.import_module(".cli", __name__)
    _cli.main(args)


if __name__ == "__main__":
    _loggers.configure_console_logging()
    LOG.error("Direct execution not supported. Use 'pdm run app'")
    sys.exit(1)
