# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

"""Browser login, auth detection, GDPR banners, Captcha handling."""

import asyncio
import enum
import sys
import urllib.parse as urllib_parse
from dataclasses import dataclass
from gettext import gettext as _
from typing import TYPE_CHECKING, Final, Sequence

from kleinanzeigen_bot.utils import diagnostics
from kleinanzeigen_bot.utils.exceptions import CaptchaEncountered
from kleinanzeigen_bot.utils.loggers import get_logger
from kleinanzeigen_bot.utils.misc import ainput, parse_duration
from kleinanzeigen_bot.utils.web_scraping_mixin import By

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from typing import Protocol

    from nodriver.core.element import Element
    from nodriver.core.tab import Tab as Page

    from kleinanzeigen_bot.model.config_model import Config

    class _LoginMixinHost(Protocol):
        config:Config
        root_url:str
        page:Page
        log_file_path:str | None
        _login_detection_diagnostics_captured:bool

        def _diagnostics_output_dir(self) -> Path:
            raise NotImplementedError

        def _timeout(self, key:str = "default", override:float | None = None) -> float:
            raise NotImplementedError

        def _effective_timeout(self, key:str = "default", override:float | None = None, *, attempt:int = 0) -> float:
            raise NotImplementedError

        async def _extract_visible_text(self, element:Element) -> str:
            raise NotImplementedError

        async def web_await(
            self,
            condition:Callable[[], object],
            *,
            timeout:int | float | None = None,
            timeout_error_message:str = "",
            apply_multiplier:bool = True,
        ) -> object:
            raise NotImplementedError

        async def web_click(self, selector_type:By, selector_value:str, *, timeout:int | float | None = None) -> Element:
            raise NotImplementedError

        async def web_find_first_available(
            self,
            selectors:Sequence[tuple[By, str]],
            *,
            parent:Element | None = None,
            timeout:int | float | None = None,
            key:str = "default",
            description:str | None = None,
        ) -> tuple[Element, int]:
            raise NotImplementedError

        async def web_input(self, selector_type:By, selector_value:str, text:str | int, *, timeout:int | float | None = None) -> Element:
            raise NotImplementedError

        async def web_open(self, url:str, *, timeout:int | float | None = None, reload_if_already_open:bool = False) -> None:
            raise NotImplementedError

        async def web_probe(
            self,
            selector_type:By,
            selector_value:str,
            *,
            parent:Element | None = None,
            timeout:int | float | None = None,
        ) -> Element | None:
            raise NotImplementedError

        async def web_scroll_page_down(self, scroll_length:int = 10, scroll_speed:int = 10_000, *, scroll_back_top:bool = False) -> None:
            raise NotImplementedError

        async def web_sleep(self, min_ms:int = 1_000, max_ms:int = 2_500) -> None:
            raise NotImplementedError

        async def web_text_first_available(
            self,
            selectors:Sequence[tuple[By, str]],
            *,
            parent:Element | None = None,
            timeout:int | float | None = None,
            key:str = "default",
            description:str | None = None,
        ) -> tuple[str, int]:
            raise NotImplementedError

else:

    class _LoginMixinHost:
        pass

LOG:Final = get_logger(__name__)

_LOGIN_DETECTION_SELECTORS:Final[list[tuple["By", str]]] = [
    (By.CLASS_NAME, "mr-medium"),
    (By.ID, "user-email"),
]
_LOGGED_OUT_CTA_SELECTORS:Final[list[tuple["By", str]]] = [
    (By.CSS_SELECTOR, 'a[href*="einloggen"]'),
    (By.CSS_SELECTOR, 'a[href*="/m-einloggen"]'),
]


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


class LoginMixin(_LoginMixinHost):
    """Browser login, auth detection, GDPR banners, Captcha handling."""

    async def check_and_wait_for_captcha(self, *, is_login_page:bool = True, page_context:str | None = None) -> None:
        captcha_elem = await self.web_probe(
            By.CSS_SELECTOR,
            "iframe[name^='a-'][src^='https://www.google.com/recaptcha/api2/anchor?']",
            timeout = self._timeout("captcha_detection"),
        )

        context_label = page_context or ("login page" if is_login_page else "publish operation")
        if captcha_elem is None:
            LOG.debug("No captcha detected within timeout (page_context=%s)", context_label)
            return

        if not is_login_page and self.config.captcha.auto_restart:
            LOG.warning("Captcha recognized - auto-restart enabled, abort run...")
            raise CaptchaEncountered(parse_duration(self.config.captcha.restart_delay))

        LOG.warning("############################################")
        LOG.warning("# Captcha present! Please solve the captcha.")
        LOG.warning("############################################")

        if not is_login_page:
            await self.web_scroll_page_down()

        await ainput(_("Press a key to continue..."))

    async def login(self) -> None:
        self._login_detection_diagnostics_captured = False
        sso_navigation_timeout = self._timeout("page_load")
        pre_login_gdpr_timeout = self._timeout("quick_dom")

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
        redirect_timeout = self._timeout("login_detection")
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
        password_step_timeout = self._timeout("login_detection")
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
        post_submit_timeout = self._timeout("login_detection")
        quick_dom_timeout = self._timeout("quick_dom")
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
        sms_timeout = self._timeout("sms_verification")
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
        banner_timeout = self._timeout("quick_dom")
        element = await self.web_probe(By.ID, "gdpr-banner-accept", timeout = banner_timeout)
        if element is not None:
            LOG.debug("Consent banner detected, clicking 'Alle akzeptieren'...")
            await element.click()
            await self.web_sleep()
        else:
            LOG.debug("Consent banner not present; continuing without dismissal")

    async def _check_email_verification(self) -> None:
        email_timeout = self._timeout("email_verification")
        element = await self.web_probe(By.TEXT, "Um dein Konto zu schützen haben wir dir eine E-Mail geschickt", timeout = email_timeout)
        if element is None:
            LOG.debug("No email verification prompt detected after login")
            return
        LOG.warning("############################################")
        LOG.warning("# Email verification message detected. Please check your email for the verification link/code and follow the instructions.")
        LOG.warning("############################################")
        await ainput(_("Press ENTER when done..."))

    async def _click_gdpr_banner(self, *, timeout:float | None = None) -> None:
        gdpr_timeout = self._timeout("quick_dom") if timeout is None else timeout
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
            await diagnostics.capture_diagnostics(
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

    async def _has_logged_in_marker(self) -> bool:
        # Use login_detection timeout (10s default) instead of default (5s)
        # to allow sufficient time for client-side JavaScript rendering after page load.
        # This is especially important for older sessions (20+ days) that require
        # additional server-side validation time.
        login_check_timeout = self._timeout("login_detection")
        effective_timeout = self._effective_timeout("login_detection")
        username = self.config.login.username.lower()
        LOG.debug(
            "Starting login detection (timeout: %.1fs base, %.1fs effective with multiplier/backoff)",
            login_check_timeout,
            effective_timeout,
        )
        quick_dom_timeout = self._timeout("quick_dom")
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
        quick_dom_timeout = self._timeout("quick_dom")
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
