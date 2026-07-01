# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Login, authentication, and session management for KleinanzeigenBot.

This module owns browser login form interaction, credential handling,
logged-in detection.

Does **not** own: application shell, publishing orchestration, or
generic consent-banner dismissal (that stays on WebScrapingMixin).
"""

import asyncio
import enum
import sys
import urllib.parse as urllib_parse
from collections.abc import Callable
from dataclasses import dataclass
from gettext import gettext as _
from pathlib import Path
from typing import Final, Sequence

from . import captcha_flow
from .model.config_model import CaptchaConfig, DiagnosticsConfig
from .utils import diagnostics as _diagnostics
from .utils import loggers as _loggers
from .utils.misc import ainput
from .utils.web_scraping_mixin import By, WebScrapingMixin

LOG:Final[_loggers.Logger] = _loggers.get_logger(__name__)

_LOGIN_DETECTION_SELECTORS:Final[list[tuple["By", str]]] = [
    (By.CLASS_NAME, "mr-medium"),
    (By.ID, "user-email"),
]
_LOGGED_OUT_CTA_SELECTORS:Final[list[tuple["By", str]]] = [
    (By.CSS_SELECTOR, 'a[href*="einloggen"]'),
    (By.CSS_SELECTOR, 'a[href*="/m-einloggen"]'),
]

# Auth0-specific container selectors for captcha detection on the identifier page.
# Includes container-based providers that don't use external iframes (e.g., Friendly
# Captcha, built-in image CAPTCHA). We don't know which provider Kleinanzeigen.de
# uses; this covers every Auth0-supported captcha container class. Used only in
# handle_identifier_captcha_state, NOT in the shared captcha_flow to avoid false
# positives during publishing flow.
_AUTH0_CAPTCHA_CONTAINER_SELECTOR:Final[str] = (
    # [data-captcha-provider] catches ALL Auth0 captcha providers (most reliable)
    "[data-captcha-provider],"
    # Container classes for known providers (substring matching where needed)
    ".recaptcha, .hcaptcha, .captcha-challenge,"
    "[class*='auth0-v2'], [class*='auth0_v2'],"
    ".friendly-captcha, .frc-captcha, .arkose, .cf-turnstile,"
    ".g-recaptcha"
)

# Post-submit diagnostic selectors — Auth0 inline error indicators on the
# password page. Probed by _classify_post_submit_state() when the password
# submit does not produce a valid post-Auth0 destination.
_AUTH0_POST_SUBMIT_ERROR_SELECTORS:Final[list[tuple[By, str]]] = [
    (By.CSS_SELECTOR, "[role='alert']"),
    (By.CSS_SELECTOR, ".ulp-input-error-message"),
    (By.CSS_SELECTOR, ".ulp-error-info"),
    (By.CSS_SELECTOR, "#error-element-password"),
]


async def _click_auth0_submit(web:WebScrapingMixin, *, timeout:float | None = None) -> None:
    """Click the visible Auth0 submit button, avoiding the hidden form-submit button."""
    effective_timeout = web.timeout("quick_dom") if timeout is None else timeout
    await web.web_click(
        By.CSS_SELECTOR,
        "button[type='submit'][data-action-button-primary='true']:not([disabled]):not([aria-disabled='true'])",
        timeout = effective_timeout,
    )


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


# ---------------------------------------------------------------------------
# Login entry point
# ---------------------------------------------------------------------------


async def login(
    web:WebScrapingMixin,
    *,
    username:str,
    password:str,
    captcha_config:CaptchaConfig,
    root_url:str,
    log_file_path:str | None,
    diagnostics_config:DiagnosticsConfig | None,
    diagnostics_output_dir_fn:Callable[[], Path],
) -> None:
    """Perform the full login flow: pre-check, Auth0 SSO, form fill, post-verify.

    Args:
        web: Browser/web interaction mixin.
        username: Login username/email.
        password: Login password.
        captcha_config: Captcha configuration.
        root_url: Base kleinanzeigen.de URL.
        log_file_path: Path to the bot's log file.
        diagnostics_config: Diagnostics configuration (or None if disabled).
        diagnostics_output_dir_fn: Callable returning the diagnostics output directory.
    """
    # Reset the diagnostics guard at the start of each login attempt so that
    # subsequent login-detection diagnostics/pause runs again, even if a
    # previous attempt on the same bot instance already captured diagnostics.
    setattr(web, "_login_detection_diagnostics_captured", False)  # noqa: B010
    sso_navigation_timeout = web.timeout("page_load")
    pre_login_gdpr_timeout = web.timeout("quick_dom")

    LOG.info("Checking if already logged in...")
    await web.web_open(root_url)
    await click_gdpr_banner(web, timeout = pre_login_gdpr_timeout)

    detection_result = await get_login_state(
        web,
        username = username,
        capture_diagnostics = False,
        diagnostics_config = diagnostics_config,
        diagnostics_output_dir_fn = diagnostics_output_dir_fn,
        log_file_path = log_file_path,
    )
    if detection_result.is_logged_in:
        LOG.info("Already logged in. Skipping login.")
        return

    LOG.debug("Navigating to SSO login page (Auth0)...")
    # m-einloggen-sso.html triggers immediate server-side redirect to Auth0
    # This avoids waiting for JS on m-einloggen.html which may not execute in headless mode
    try:
        await web.web_open(f"{root_url}/m-einloggen-sso.html", timeout = sso_navigation_timeout)
    except TimeoutError:
        LOG.warning("Timeout navigating to SSO login page after %.1fs", sso_navigation_timeout)
        await capture_login_detection_diagnostics_if_enabled(
            web,
            base_prefix = "login_detection_sso_navigation_timeout",
            pause_banner_message = "# SSO navigation timed out. Browser is paused for manual inspection.",
            diagnostics_config = diagnostics_config,
            diagnostics_output_dir_fn = diagnostics_output_dir_fn,
            log_file_path = log_file_path,
        )
        raise

    try:
        await fill_login_data_and_send(
            web,
            username = username,
            password = password,
            captcha_config = captcha_config,
            diagnostics_config = diagnostics_config,
            diagnostics_output_dir_fn = diagnostics_output_dir_fn,
            log_file_path = log_file_path,
        )
        await handle_after_login_logic(web)
    except (AssertionError, TimeoutError):
        # AssertionError is intentionally part of auth-boundary control flow so
        # diagnostics are captured before the original error is re-raised.
        await capture_login_detection_diagnostics_if_enabled(
            web,
            base_prefix = "login_detection_auth0_flow_failure",
            pause_banner_message = "# Auth0 login flow failed. Browser is paused for manual inspection.",
            diagnostics_config = diagnostics_config,
            diagnostics_output_dir_fn = diagnostics_output_dir_fn,
            log_file_path = log_file_path,
        )
        raise

    await web.dismiss_consent_banner()

    detection_result = await get_login_state(
        web,
        username = username,
        capture_diagnostics = False,
        diagnostics_config = diagnostics_config,
        diagnostics_output_dir_fn = diagnostics_output_dir_fn,
        log_file_path = log_file_path,
    )
    if detection_result.is_logged_in:
        LOG.info("Login confirmed.")
        return

    current_url = current_page_url(web)
    LOG.debug("Login detection reason after attempt is %s", detection_result.reason.name)
    LOG.warning("Login could not be confirmed after Auth0 flow (url=%s)", current_url)
    await capture_login_detection_diagnostics_if_enabled(
        web,
        base_prefix = f"login_detection_{detection_result.reason.name.lower()}",
        pause_banner_message = "# Login confirmation failed after Auth0 flow. Browser is paused for manual inspection.",
        diagnostics_config = diagnostics_config,
        diagnostics_output_dir_fn = diagnostics_output_dir_fn,
        log_file_path = log_file_path,
    )
    raise AssertionError(_("Login could not be confirmed after Auth0 flow (reason=%s, url=%s)") % (detection_result.reason.name, current_url))


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


def current_page_url(web:WebScrapingMixin) -> str:
    page = getattr(web, "page", None)
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


def is_valid_post_auth0_destination(url:str) -> bool:
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


# ---------------------------------------------------------------------------
# Auth0 wait helpers
# ---------------------------------------------------------------------------


async def wait_for_auth0_login_context(web:WebScrapingMixin) -> None:
    redirect_timeout = web.timeout("login_detection")
    try:
        await web.web_await(
            lambda: "login.kleinanzeigen.de" in current_page_url(web) or "/u/login" in current_page_url(web),
            timeout = redirect_timeout,
            timeout_error_message = f"Auth0 redirect did not start within {redirect_timeout} seconds",
            apply_multiplier = False,
        )
    except TimeoutError as ex:
        current_url = current_page_url(web)
        raise AssertionError(_("Auth0 redirect not detected (url=%s)") % current_url) from ex


async def wait_for_auth0_password_step(web:WebScrapingMixin) -> None:
    password_step_timeout = web.timeout("login_detection")
    try:
        await web.web_await(
            lambda: "/u/login/password" in current_page_url(web),
            timeout = password_step_timeout,
            timeout_error_message = f"Auth0 password page not reached within {password_step_timeout} seconds",
            apply_multiplier = False,
        )
    except TimeoutError as ex:
        current_url = current_page_url(web)
        raise AssertionError(_("Auth0 password step not reached (url=%s)") % current_url) from ex


def _diagnostic_url(web:WebScrapingMixin) -> str:
    """Best-effort URL diagnostic; returns ``'unknown'`` on any exception.

    Relies on ``current_page_url()`` which already strips query, fragment,
    and userinfo from the raw browser URL.
    """
    try:
        return current_page_url(web)
    except Exception:  # noqa: BLE001
        return "unknown"


async def _classify_post_submit_state(web:WebScrapingMixin) -> str:
    """Best-effort classification of page state after Auth0 password submit.

    Probes URL, Auth0 inline error selectors, and high-confidence MFA/
    verification signals (gated to non-destination URLs). Never raises —
    all callers must treat the result as non-fatal diagnostic context.

    Each probe/text-extraction failure uses a local ``try/except`` so the
    signal is treated as absent without discarding already-detected facts.

    Returns coarse labels only (no raw page text):
        "STILL_ON_PASSWORD_PAGE"
        "STILL_ON_PASSWORD_PAGE + AUTH0_INLINE_ERROR"
        "STILL_ON_PASSWORD_PAGE + IP_RANGE_BLOCKED"
        "MFA_DETECTED (ONE_TIME_CODE_INPUT)"
        "MFA_DETECTED (SMS_VERIFICATION)"
        "UNKNOWN (url=https://kleinanzeigen.de/u/login/password)"
    """
    url = _diagnostic_url(web)
    if url == "unknown":
        return "UNKNOWN (classification_error)"

    facts:list[str] = []

    # 1) Password-page classification
    is_password_page = "/u/login/password" in url
    if is_password_page:
        facts.append("STILL_ON_PASSWORD_PAGE")

    try:
        quick_dom = web.timeout("quick_dom")
    except Exception:  # noqa: BLE001
        quick_dom = 5.0

    # 2) Auth0 inline error selectors — gated to password page only because
    #    ``[role='alert']`` is a broad selector that could match unrelated UI.
    #    Appears as the coarse label ``AUTH0_INLINE_ERROR`` (no raw text).
    if is_password_page:
        for sel_type, sel_value in _AUTH0_POST_SUBMIT_ERROR_SELECTORS:
            try:
                element = await web.web_probe(sel_type, sel_value, timeout = quick_dom)
            except Exception:  # noqa: S112, BLE001
                continue
            if element is not None:
                try:
                    text = await web.extract_visible_text(element)
                except Exception:  # noqa: BLE001
                    text = None
                if text and text.strip():
                    facts.append("AUTH0_INLINE_ERROR")
                    break

    # 2b) IP range block detection — gated to password page only.
    #      When Kleinanzeigen returns its IP-range block page (div#error with
    #      German text) instead of proceeding after password submit, the URL
    #      stays on /u/login/password but the DOM is the block page. Probe
    #      for the distinctive heading text.
    if is_password_page:
        try:
            ip_block_element = await web.web_probe(
                By.TEXT,
                "IP-Bereich vorübergehend gesperrt",
                timeout = quick_dom,
            )
            if ip_block_element is not None:
                facts.append("IP_RANGE_BLOCKED")
        except Exception:  # noqa: S110, BLE001
            pass

    # 3) High-confidence MFA/verification probes.
    #    Gate: only run when URL is not a known valid Kleinanzeigen destination
    #    (i.e. still on login/knotenpunkt or an Auth0 challenge page).
    if not is_valid_post_auth0_destination(url):
        mfa_facts:list[str] = []

        # Locale-independent one-time code input field
        try:
            otc_element = await web.web_probe(
                By.CSS_SELECTOR, "input[autocomplete='one-time-code']",
                timeout = quick_dom,
            )
            if otc_element is not None:
                mfa_facts.append("ONE_TIME_CODE_INPUT")
        except Exception:  # noqa: S110, BLE001
            pass

        # German SMS verification prompt (same text as check_sms_verification)
        try:
            sms_element = await web.web_probe(
                By.TEXT,
                "Wir haben dir gerade einen 6-stelligen Code für die Telefonnummer",
                timeout = quick_dom,
            )
            if sms_element is not None:
                mfa_facts.append("SMS_VERIFICATION")
        except Exception:  # noqa: S110, BLE001
            pass

        # German email verification prompt (same text as check_email_verification)
        try:
            email_element = await web.web_probe(
                By.TEXT,
                "Um dein Konto zu schützen haben wir dir eine E-Mail geschickt",
                timeout = quick_dom,
            )
            if email_element is not None:
                mfa_facts.append("EMAIL_VERIFICATION")
        except Exception:  # noqa: S110, BLE001
            pass

        if mfa_facts:
            facts.append(f"MFA_DETECTED ({', '.join(mfa_facts)})")

    if facts:
        return " + ".join(facts)
    return f"UNKNOWN (url={url})"


async def wait_for_post_auth0_submit_transition(
    web:WebScrapingMixin,
    *,
    username:str,
    diagnostics_config:DiagnosticsConfig | None = None,
    diagnostics_output_dir_fn:Callable[[], Path] | None = None,
    log_file_path:str | None = None,
) -> None:
    post_submit_timeout = web.timeout("login_detection")
    quick_dom_timeout = web.timeout("quick_dom")
    fallback_max_ms = max(700, int(quick_dom_timeout * 1_000))
    fallback_min_ms = max(300, fallback_max_ms // 2)

    try:
        await web.web_await(
            lambda: is_valid_post_auth0_destination(_diagnostic_url(web)),
            timeout = post_submit_timeout,
            timeout_error_message = f"Auth0 post-submit transition did not complete within {post_submit_timeout} seconds",
            apply_multiplier = False,
        )
        return
    except TimeoutError:
        LOG.debug("Post-submit transition not detected via URL, checking logged-in selectors")

    login_confirmed = False
    try:
        login_confirmed = await asyncio.wait_for(is_logged_in(web, username = username), timeout = post_submit_timeout)
    except (TimeoutError, asyncio.TimeoutError):
        LOG.debug("Post-submit login verification did not complete within %.1fs", post_submit_timeout)

    if login_confirmed:
        return

    LOG.debug("Auth0 post-submit verification remained inconclusive; applying bounded fallback pause")
    LOG.debug("Post-submit state before fallback sleep: url=%s", _diagnostic_url(web))
    await web.web_sleep(min_ms = fallback_min_ms, max_ms = fallback_max_ms)

    try:
        if await asyncio.wait_for(is_logged_in(web, username = username), timeout = quick_dom_timeout):
            return
    except (TimeoutError, asyncio.TimeoutError):
        LOG.debug("Final post-submit login confirmation did not complete within %.1fs", quick_dom_timeout)

    classification = await _classify_post_submit_state(web)
    sanitized_url = _diagnostic_url(web)
    LOG.warning("Auth0 post-submit verification remained inconclusive")

    # Best-effort diagnostics capture — never mask the original TimeoutError.
    try:
        await capture_login_detection_diagnostics_if_enabled(
            web,
            base_prefix = "login_detection_auth0_post_submit_inconclusive",
            pause_banner_message = "# Auth0 post-submit verification remained inconclusive. Browser is paused for manual inspection.",
            diagnostics_config = diagnostics_config,
            diagnostics_output_dir_fn = diagnostics_output_dir_fn,
            log_file_path = log_file_path,
            json_payload = {
                "event": "auth0_post_submit_inconclusive",
                "classification": classification,
                "page_url": sanitized_url,
            },
        )
    except Exception:  # noqa: S110, BLE001
        pass

    raise TimeoutError(
        _("Auth0 post-submit verification remained inconclusive: %s (url=%s)")
        % (classification, sanitized_url)
    )


# ---------------------------------------------------------------------------
# Captcha handling on identifier page
# ---------------------------------------------------------------------------


async def _detect_auth0_identifier_captcha(web:WebScrapingMixin) -> bool:
    """Detect captcha on the Auth0 identifier page.

    Checks both iframe-based captchas (shared mechanism) and Auth0-specific
    container-based captcha elements that don't use iframes.
    """
    # Shared iframe-based captcha detection (reCAPTCHA, hCaptcha, Turnstile, etc.)
    if await captcha_flow.detect_captcha(web):
        return True

    # Auth0-specific container-based captchas without iframes
    quick_dom = web.timeout("quick_dom")
    container = await web.web_probe(
        By.CSS_SELECTOR,
        _AUTH0_CAPTCHA_CONTAINER_SELECTOR,
        timeout = quick_dom,
    )
    return container is not None


async def handle_identifier_captcha_state(web:WebScrapingMixin) -> None:
    """Handle captcha/challenge on the Auth0 identifier page.

    After submitting the email, a non-interactive captcha (Cloudflare Turnstile
    via Auth0 v2) may appear. This waits for its token, clicks the visible
    submit button, and falls back to a user prompt if still stuck.
    """
    # Already on password page — nothing to do
    if "/u/login/password" in current_page_url(web):
        return

    # Brief grace period for normal navigation
    await web.web_sleep(1000, 2000)
    if "/u/login/password" in current_page_url(web):
        return

    # Detect captcha on the identifier page
    captcha_detected = await _detect_auth0_identifier_captcha(web)
    quick_dom = web.timeout("quick_dom")

    # Re-check URL: detection may have taken long enough for navigation to complete
    if "/u/login/password" in current_page_url(web):
        return

    if captcha_detected:
        LOG.info("Auth0 captcha detected, waiting for token...")
        # Wait for Turnstile/other provider to generate a token before clicking
        try:
            await web.web_await(
                lambda: web.web_execute(
                    "document.querySelector(\"input[name='captcha']\")?.value?.length > 0"
                ),
                timeout = quick_dom,
                timeout_error_message = "Captcha token did not appear",
                apply_multiplier = False,
            )
        except TimeoutError:
            LOG.debug("No captcha token appeared within quick_dom timeout")
        else:
            # Re-check URL: token wait may have taken long enough for navigation
            if "/u/login/password" in current_page_url(web):
                return
            LOG.info("Captcha token ready, clicking submit...")
            try:
                await _click_auth0_submit(web)
                await web.web_sleep()
                if "/u/login/password" in current_page_url(web):
                    return
            except TimeoutError:
                LOG.debug("Visible submit button not clickable after token; falling through to prompt")
        # If token never arrived or click didn't advance, fall through to prompt

    # No captcha or token didn't arrive — try clicking visible submit once
    if not captcha_detected:
        if "/u/login/password" in current_page_url(web):
            return
        try:
            await _click_auth0_submit(web, timeout = quick_dom)
            await web.web_sleep()
            if "/u/login/password" in current_page_url(web):
                return
        except TimeoutError:
            LOG.debug("Visible submit button not found — falling through to user prompt")

    # Still stuck — prompt user for any undetected challenge
    LOG.warning("############################################")
    LOG.warning("# Auth0 identifier page is still waiting.")
    LOG.warning("# If a security challenge is visible, please solve it.")
    LOG.warning("############################################")
    await ainput(_("Press a key after solving the challenge..."))

    if "/u/login/password" in current_page_url(web):
        return

    # Final attempt
    try:
        await _click_auth0_submit(web, timeout = quick_dom)
        await web.web_sleep()
    except TimeoutError:
        LOG.debug("Final submit button not found — giving up")


# ---------------------------------------------------------------------------
# Form filling
# ---------------------------------------------------------------------------


async def fill_login_data_and_send(
    web:WebScrapingMixin,
    *,
    username:str,
    password:str,
    captcha_config:CaptchaConfig,
    diagnostics_config:DiagnosticsConfig | None = None,
    diagnostics_output_dir_fn:Callable[[], Path] | None = None,
    log_file_path:str | None = None,
) -> None:
    """Auth0 2-step login via m-einloggen-sso.html (server-side redirect, no JS needed).

    Step 1: /u/login/identifier - email
    Step 2: /u/login/password   - password
    """
    LOG.info("Logging in...")
    await wait_for_auth0_login_context(web)

    # Step 1: email identifier
    LOG.debug("Auth0 Step 1: entering email...")
    await web.web_input(By.ID, "username", username)
    await _click_auth0_submit(web)

    # Captcha-solving branch: captcha can appear on the identifier page
    # after email submit. After solving, a visible Weiter button may need
    # clicking to reach the password page.
    await handle_identifier_captcha_state(web)

    # Step 2: wait for password page then enter password
    LOG.debug("Waiting for Auth0 password page...")
    await wait_for_auth0_password_step(web)

    LOG.debug("Auth0 Step 2: entering password...")
    await web.web_input(By.CSS_SELECTOR, "input[type='password']", password)
    await captcha_flow.check_and_wait_for_captcha(web, captcha_config, is_login_page = True)
    await _click_auth0_submit(web)
    await wait_for_post_auth0_submit_transition(
        web,
        username = username,
        diagnostics_config = diagnostics_config,
        diagnostics_output_dir_fn = diagnostics_output_dir_fn,
        log_file_path = log_file_path,
    )
    LOG.debug("Auth0 login submitted.")


# ---------------------------------------------------------------------------
# Post-login checks
# ---------------------------------------------------------------------------


async def handle_after_login_logic(web:WebScrapingMixin) -> None:
    await check_sms_verification(web)
    await check_email_verification(web)
    LOG.debug("Handling GDPR disclaimer...")
    await click_gdpr_banner(web)


async def check_sms_verification(web:WebScrapingMixin) -> None:
    sms_timeout = web.timeout("sms_verification")
    element = await web.web_probe(By.TEXT, "Wir haben dir gerade einen 6-stelligen Code für die Telefonnummer", timeout = sms_timeout)
    if element is None:
        LOG.debug("No SMS verification prompt detected after login")
        return
    LOG.warning("############################################")
    LOG.warning("# Device verification message detected. Please follow the instruction displayed in the Browser.")
    LOG.warning("############################################")
    await ainput(_("Press ENTER when done..."))


async def check_email_verification(web:WebScrapingMixin) -> None:
    email_timeout = web.timeout("email_verification")
    element = await web.web_probe(By.TEXT, "Um dein Konto zu schützen haben wir dir eine E-Mail geschickt", timeout = email_timeout)
    if element is None:
        LOG.debug("No email verification prompt detected after login")
        return
    LOG.warning("############################################")
    LOG.warning("# Device verification message detected. Please follow the instruction displayed in the Browser.")
    LOG.warning("############################################")
    await ainput(_("Press ENTER when done..."))


async def click_gdpr_banner(web:WebScrapingMixin, *, timeout:float | None = None) -> None:
    gdpr_timeout = web.timeout("quick_dom") if timeout is None else timeout
    element = await web.web_probe(By.ID, "gdpr-banner-accept", timeout = gdpr_timeout)
    if element is not None:
        await element.click()
        await web.web_sleep()
    else:
        LOG.debug("GDPR banner not present; continuing without click")


# ---------------------------------------------------------------------------
# Login state detection
# ---------------------------------------------------------------------------


async def get_login_state(
    web:WebScrapingMixin,
    *,
    username:str,
    capture_diagnostics:bool = True,
    diagnostics_config:DiagnosticsConfig | None = None,
    diagnostics_output_dir_fn:Callable[[], Path] | None = None,
    log_file_path:str | None = None,
) -> LoginDetectionResult:
    """Determine login status using DOM-first detection and return result with reason.

    Order:
    1) DOM-based logged-in marker check
    2) Logged-out CTA check
    3) If inconclusive, optionally capture diagnostics and return a timeout reason
    """
    # Prefer DOM-based checks first to minimize bot-like behavior and avoid
    # fragile API probing side effects. Server-side auth probing was removed.
    if await has_logged_in_marker(web, username = username):
        return LoginDetectionResult(is_logged_in = True, reason = LoginDetectionReason.USER_INFO_MATCH)

    if await has_logged_out_cta(web, log_timeout = False):
        return LoginDetectionResult(is_logged_in = False, reason = LoginDetectionReason.CTA_MATCH)

    if capture_diagnostics:
        await capture_login_detection_diagnostics_if_enabled(
            web,
            base_prefix = "login_detection_selector_timeout",
            pause_banner_message = "# Login detection remained inconclusive. Browser is paused for manual inspection.",
            diagnostics_config = diagnostics_config,
            diagnostics_output_dir_fn = diagnostics_output_dir_fn,
            log_file_path = log_file_path,
        )
    return LoginDetectionResult(is_logged_in = False, reason = LoginDetectionReason.SELECTOR_TIMEOUT)


async def capture_login_detection_diagnostics_if_enabled(
    web:WebScrapingMixin,
    *,
    base_prefix:str = "login_detection_inconclusive",
    pause_banner_message:str = "# Login detection remained inconclusive. Browser is paused for manual inspection.",
    diagnostics_config:DiagnosticsConfig | None,
    diagnostics_output_dir_fn:Callable[[], Path] | None,
    log_file_path:str | None,
    json_payload:dict[str, str] | None = None,
) -> None:
    cfg = diagnostics_config
    if cfg is None or not getattr(getattr(cfg, "capture_on", None), "login_detection", False):
        return

    if getattr(web, "_login_detection_diagnostics_captured", False):
        return

    page = getattr(web, "page", None)

    try:
        if diagnostics_output_dir_fn is None:
            LOG.debug("Login diagnostics capture skipped (base_prefix=%s): no output dir callable", base_prefix)
            return
        output_dir = diagnostics_output_dir_fn()
    except Exception as exc:  # noqa: BLE001
        LOG.debug("Login diagnostics capture skipped (base_prefix=%s): %s", base_prefix, exc)
        return

    try:
        await _diagnostics.capture_diagnostics(
            output_dir = output_dir,
            base_prefix = base_prefix,
            page = page,
            json_payload = json_payload,
            log_file_path = log_file_path,
            copy_log = getattr(cfg, "capture_log_copy", False),
        )
        setattr(web, "_login_detection_diagnostics_captured", True)  # noqa: B010
    except Exception as exc:  # noqa: BLE001
        LOG.debug(
            "Login diagnostics capture failed (output_dir=%s, base_prefix=%s): %s",
            output_dir,
            base_prefix,
            exc,
        )
        return

    if getattr(cfg, "pause_on_login_detection_failure", False) and getattr(sys.stdin, "isatty", lambda: False)():
        LOG.warning("############################################")
        LOG.warning(pause_banner_message)
        LOG.warning("############################################")
        await ainput(_("Press a key to continue..."))


# ---------------------------------------------------------------------------
# Logged-in / logged-out detection
# ---------------------------------------------------------------------------


async def has_logged_in_marker(web:WebScrapingMixin, *, username:str) -> bool:
    # Use login_detection timeout (10s default) instead of default (5s)
    # to allow sufficient time for client-side JavaScript rendering after page load.
    # This is especially important for older sessions (20+ days) that require
    # additional server-side validation time.
    login_check_timeout = web.timeout("login_detection")
    effective_timeout = web.effective_timeout("login_detection")
    username_lower = username.lower()
    LOG.debug(
        "Starting login detection (timeout: %.1fs base, %.1fs effective with multiplier/backoff)",
        login_check_timeout,
        effective_timeout,
    )
    quick_dom_timeout = web.timeout("quick_dom")
    tried_login_selectors = _format_login_detection_selectors(_LOGIN_DETECTION_SELECTORS)

    try:
        user_info, matched_selector = await web.web_text_first_available(
            _LOGIN_DETECTION_SELECTORS,
            timeout = quick_dom_timeout,
            key = "quick_dom",
            description = "login_detection(quick_logged_in)",
        )
        if username_lower in user_info.lower():
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
        user_info, matched_selector = await web.web_text_first_available(
            _LOGIN_DETECTION_SELECTORS,
            timeout = login_check_timeout,
            key = "login_detection",
            description = "login_detection(selector_group)",
        )
        if username_lower in user_info.lower():
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


async def is_logged_in(web:WebScrapingMixin, *, username:str) -> bool:
    """Check if the browser session is logged in."""
    if await has_logged_in_marker(web, username = username):
        return True

    tried_login_selectors = _format_login_detection_selectors(_LOGIN_DETECTION_SELECTORS)

    LOG.debug("No login detected via configured login detection selectors (%s)", tried_login_selectors)
    return False


# NOTE: Treats any matched CTA selector with non-empty text as logged-out evidence.
# Does NOT verify visibility (hidden/footer/off-canvas links could theoretically match).
# PR #870 verified these selectors work correctly in practice.
# If false positives occur, harden by adding web_check(Is.DISPLAYED) on cta_element.
# See issue #876.
async def has_logged_out_cta(web:WebScrapingMixin, *, log_timeout:bool = True) -> bool:
    quick_dom_timeout = web.timeout("quick_dom")
    tried_logged_out_selectors = _format_login_detection_selectors(_LOGGED_OUT_CTA_SELECTORS)

    try:
        cta_element, cta_index = await web.web_find_first_available(
            _LOGGED_OUT_CTA_SELECTORS,
            timeout = quick_dom_timeout,
            key = "quick_dom",
            description = "login_detection(logged_out_cta)",
        )
        cta_text = await web.extract_visible_text(cta_element)
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
