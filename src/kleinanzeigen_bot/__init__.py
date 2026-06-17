# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import asyncio, enum, importlib, os, sys  # isort: skip
import urllib.parse as urllib_parse
from dataclasses import dataclass
from gettext import gettext as _
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal, Sequence, cast

import certifi, colorama  # isort: skip
from nodriver.core.connection import ProtocolException
from ruamel.yaml import YAML

from . import ad_loading, captcha_flow, delete_flow, download_flow, extend_flow, published_ads
from . import ad_state as _ad_state
from . import price_reduction as _price_reduction
from . import publishing_form as _publishing_form
from . import publishing_persistence as _publishing_persistence
from . import publishing_submission as _publishing_submission
from . import runtime_config as _runtime_config
from ._version import __version__
from .model.ad_model import Ad
from .model.ad_model import (
    AdUpdateStrategy as AdUpdateStrategy,
)
from .model.config_model import Config  # noqa: TC001 — used at runtime, config injection
from .published_ads import PublishedAd, ad_matches_id
from .update_checker import UpdateChecker
from .utils import diagnostics as _diagnostics
from .utils import loggers as _loggers
from .utils import misc as _misc
from .utils import xdg_paths as _xdg_paths
from .utils.exceptions import CategoryResolutionError, PublishSubmissionUncertainError
from .utils.files import abspath
from .utils.i18n import pluralize
from .utils.misc import ainput, is_frozen
from .utils.web_scraping_mixin import By, Is, WebScrapingMixin

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
                    # update-check uses sensible defaults and needs no config files,
                    # no file logging, and no browser setup — skip bootstrap entirely.
                    self.config = Config()
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

    async def _handle_identifier_captcha_state(self) -> None:
        """Handle captcha on the Auth0 identifier page and click Weiter if present.

        After submitting the email, a reCAPTCHA may be displayed on the
        identifier page. If so, this waits for the user to solve it and then
        probes for a visible Weiter button (not assumed to exist). If present,
        it is clicked to continue to the password page.

        No-op when the password page is already reached or no captcha is
        detected, so the normal fast path is unaffected.
        """
        if "/u/login/password" in self._current_page_url():
            return

        captcha_elem = await self.web_probe(
            By.CSS_SELECTOR,
            "iframe[name^='a-'][src^='https://www.google.com/recaptcha/api2/anchor?']",
            timeout = self.timeout("captcha_detection"),
        )
        if captcha_elem is None:
            return

        LOG.warning("############################################")
        LOG.warning("# Captcha detected on Auth0 login page. Please solve it in the browser.")
        LOG.warning("############################################")
        await ainput(_("Press a key to continue..."))

        # After captcha solving, probe for a visible Weiter button
        quick_dom = self.timeout("quick_dom")
        weiter_xpath = "//button[contains(., 'Weiter')]"
        weiter = await self.web_probe(
            By.XPATH, weiter_xpath,
            timeout = quick_dom,
        )
        if weiter is not None and await self.web_check(By.XPATH, weiter_xpath, Is.DISPLAYED, timeout = quick_dom):
            LOG.info("Auth0 Weiter button present after captcha, clicking it...")
            await self.web_click(By.XPATH, weiter_xpath, timeout = quick_dom)
            await self.web_sleep()
        else:
            LOG.debug("No Weiter button after captcha — continuing to wait for password page")

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

        # Captcha-solving branch: captcha can appear on the identifier page
        # after email submit. After solving, a visible Weiter button may need
        # clicking to reach the password page.
        await self._handle_identifier_captcha_state()

        # Step 2: wait for password page then enter password
        LOG.debug("Waiting for Auth0 password page...")
        await self._wait_for_auth0_password_step()

        LOG.debug("Auth0 Step 2: entering password...")
        await self.web_input(By.CSS_SELECTOR, "input[type='password']", self.config.login.password)
        await captcha_flow.check_and_wait_for_captcha(self, self.config.captcha, is_login_page = True)
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

    async def _check_publishing_result(self) -> bool:
        # Check for success messages
        return await self.web_check(By.ID, "checking-done", Is.DISPLAYED) or await self.web_check(By.ID, "not-completed", Is.DISPLAYED)

    async def _delete_old_ad_if_needed(
        self, ad_cfg:Ad, published_ads_list:list[PublishedAd],
        *, timing:Literal["BEFORE_PUBLISH", "AFTER_PUBLISH"],
    ) -> None:
        """Delete an old ad before or after (re-)publishing, depending on config.

        Skips deletion when keep_old_ads is True or when the configured
        ``delete_old_ads`` timing does not match *timing*.

        In ``AFTER_PUBLISH`` mode, title-based deletion is always disabled to
        avoid accidentally removing the newly published ad.
        """
        if self.keep_old_ads:
            return
        if self.config.publishing.delete_old_ads != timing:
            return
        delete_old_ads_by_title = (
            self.config.publishing.delete_old_ads_by_title
            if timing == "BEFORE_PUBLISH" else False
        )
        await delete_flow.delete_ad(
            web = self, root_url = self.root_url,
            ad_cfg = ad_cfg,
            published_ads_list = published_ads_list,
            delete_old_ads_by_title = delete_old_ads_by_title,
        )

    async def publish_ads(self, ad_cfgs:list[tuple[str, Ad, dict[str, Any]]]) -> None:
        count = 0
        failed_count = 0
        max_retries = SUBMISSION_MAX_RETRIES

        published_ads_list = await published_ads.fetch_published_ads(self, self.root_url)

        for idx, (ad_file, ad_cfg, ad_cfg_orig) in enumerate(ad_cfgs, start = 1):
            LOG.info("Processing %s/%s: '%s' from [%s]...", idx, len(ad_cfgs), ad_cfg.title, ad_file)

            if any(ad_matches_id(x, ad_cfg.id) and x.get("state") == "paused" for x in published_ads_list):
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
                    await self.publish_ad(ad_file, ad_cfg, ad_cfg_orig, published_ads_list, AdUpdateStrategy.REPLACE)
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
                    await self.web_await(self._check_publishing_result, timeout = publish_timeout)
                except TimeoutError:
                    LOG.warning(" -> Could not confirm publishing for '%s', but ad may be online", ad_cfg.title)

                await self._delete_old_ad_if_needed(ad_cfg, published_ads_list, timing = "AFTER_PUBLISH")

        LOG.info("############################################")
        if failed_count > 0:
            LOG.info("DONE: (Re-)published %s (%s failed after retries)", pluralize("ad", count - failed_count), failed_count)
        else:
            LOG.info("DONE: (Re-)published %s", pluralize("ad", count))
        LOG.info("############################################")

    async def _fill_ad_form(
        self, ad_file:str, ad_cfg:Ad, mode:AdUpdateStrategy,
    ) -> None:
        """Fill the ad creation/edit form — category, attributes, shipping, price,
        sell-directly, description, contact, and images."""

        #############################
        # set category (before title to avoid form reset clearing title)
        #############################
        await _publishing_form.set_category(self, root_url = self.root_url, category = ad_cfg.category, ad_file = ad_file)
        await self.web_sleep()  # wait for category-dependent fields to render before setting attributes

        #############################
        # set special attributes
        #############################
        await _publishing_form.set_special_attributes(self, ad_cfg)

        #############################
        # set shipping type/options/costs
        #############################
        await _publishing_form.set_shipping_form(self, ad_cfg, mode)

        await _publishing_form.set_pricing_fields(self, ad_cfg, self.config.ad_defaults)

        await _publishing_form.set_contact_fields(self, ad_cfg.contact)

        await _publishing_form.fill_image_section(self, ad_cfg)

    async def publish_ad(
        self, ad_file:str, ad_cfg:Ad, ad_cfg_orig:dict[str, Any], published_ads_list:list[PublishedAd], mode:AdUpdateStrategy = AdUpdateStrategy.REPLACE
    ) -> None:
        """Publish or update an ad on Kleinanzeigen.

        Args:
            ad_file: Path to the ad configuration YAML file.
            ad_cfg: The effective ad configuration with default values applied.
            ad_cfg_orig: The original ad configuration as present in the YAML file.
            published_ads_list: List of published ads from the API, used for deduplication
                and old ad deletion.
            mode: The ad editing strategy. REPLACE creates a new ad (full republish),
                MODIFY updates an existing ad in-place.

        Returns:
            None
        """
        old_ad_id = ad_cfg.id

        if mode == AdUpdateStrategy.REPLACE:
            await self._delete_old_ad_if_needed(ad_cfg, published_ads_list, timing = "BEFORE_PUBLISH")

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

        await self._fill_ad_form(ad_file, ad_cfg, mode)

        ad_id = await _publishing_submission.submit_and_confirm_ad(self, ad_file, ad_cfg, mode, captcha_config = self.config.captcha)

        try:
            _publishing_persistence.persist_published_ad(ad_file, ad_cfg, ad_cfg_orig, old_ad_id, ad_id, mode, config = self.config)
        except Exception:
            LOG.error(  # noqa: G201 — must use .error(exc_info=True) for translation lookup to resolve publish_ad
                "Post-publish persistence failed for '%s' (ad ID %s - ad is live on Kleinanzeigen but local YAML may be out of sync)",
                ad_cfg.title, ad_id, exc_info = True,
            )

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

        published_ads_list = await published_ads.fetch_published_ads(self, self.root_url)

        for idx, (ad_file, ad_cfg, ad_cfg_orig) in enumerate(ad_cfgs, start = 1):
            LOG.info("Processing %s/%s: '%s' from [%s]...", idx, len(ad_cfgs), ad_cfg.title, ad_file)

            ad = next((published_ad for published_ad in published_ads_list if ad_matches_id(published_ad, ad_cfg.id)), None)

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
                    await self.publish_ad(ad_file, ad_cfg, ad_cfg_orig, published_ads_list, AdUpdateStrategy.MODIFY)
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
                    await self.web_await(self._check_publishing_result, timeout = publish_timeout)
                except TimeoutError:
                    LOG.warning(" -> Could not confirm update for '%s', but changes may be online", ad_cfg.title)

        LOG.info("############################################")
        if failed_count > 0:
            LOG.info("DONE: updated %s (%s failed after retries)", pluralize("ad", count - failed_count), failed_count)
        else:
            LOG.info("DONE: updated %s", pluralize("ad", count))
        LOG.info("############################################")

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
