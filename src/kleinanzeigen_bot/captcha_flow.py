# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
from gettext import gettext as _
from typing import Final

from .model.config_model import CaptchaConfig
from .utils import loggers as _loggers
from .utils import misc as _misc
from .utils.exceptions import CaptchaEncountered
from .utils.misc import ainput
from .utils.web_scraping_mixin import By, WebScrapingMixin

# Combined CSS selector covering all Auth0-supported captcha/challenge providers
# that inject a detectable iframe. We do not know which specific provider
# Kleinanzeigen.de uses; this set covers every iframe-based provider Auth0
# supports (reCAPTCHA v2/Enterprise, hCaptcha, Cloudflare Turnstile, Arkose,
# FunCaptcha). Excludes Simple CAPTCHA and Auth Challenge — both render
# inline without an external iframe and cannot be detected by CSS selectors.
# Single probe call avoids multiplying timeout across N individual selectors.
_CAPTCHA_IFRAME_SELECTOR:Final[str] = (
    "iframe[src*='google.com/recaptcha'],"
    "iframe[src*='recaptcha.net'],"
    "iframe[src*='hcaptcha.com'],"
    "iframe[src*='challenges.cloudflare.com'],"
    "iframe[src*='arkoselabs.com'],"
    "iframe[src*='funcaptcha.com']"
)

LOG = _loggers.get_logger(__name__)


async def detect_captcha(
    web:WebScrapingMixin,
    *,
    timeout:float | None = None,
) -> bool:
    """Detect any supported captcha/challenge iframe on the page.

    Uses a single combined CSS selector probe to avoid multiplying timeout
    across multiple individual selectors.
    """
    effective_timeout = web.timeout("captcha_detection") if timeout is None else timeout
    elem = await web.web_probe(
        By.CSS_SELECTOR,
        _CAPTCHA_IFRAME_SELECTOR,
        timeout = effective_timeout,
    )
    return elem is not None


async def check_and_wait_for_captcha(
    web:WebScrapingMixin,
    captcha_config:CaptchaConfig,
    *,
    is_login_page:bool = True,
    page_context:str | None = None,
) -> None:
    captcha_detected = await detect_captcha(web)

    context_label = page_context or ("login page" if is_login_page else "publish operation")
    if not captcha_detected:
        LOG.debug("No captcha detected within timeout (page_context=%s)", context_label)
        return

    if not is_login_page and captcha_config.auto_restart:
        LOG.warning("Captcha recognized - auto-restart enabled, abort run...")
        raise CaptchaEncountered(_misc.parse_duration(captcha_config.restart_delay))

    LOG.warning("############################################")
    LOG.warning("# Captcha present! Please solve the captcha.")
    LOG.warning("############################################")

    if not is_login_page:
        try:
            await web.web_scroll_page_down()
        except TimeoutError as ex:
            LOG.debug("Captcha page scroll skipped after timeout: %s", ex)

    await ainput(_("Press a key to continue..."))
