# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
from gettext import gettext as _

from kleinanzeigen_bot.model.config_model import CaptchaConfig
from kleinanzeigen_bot.utils.web_scraping_mixin import By, WebScrapingMixin

from .utils import loggers as _loggers
from .utils import misc as _misc
from .utils.exceptions import CaptchaEncountered
from .utils.misc import ainput

LOG = _loggers.get_logger(__name__)


async def check_and_wait_for_captcha(
    web:WebScrapingMixin,
    captcha_config:CaptchaConfig,
    *,
    is_login_page:bool = True,
    page_context:str | None = None,
) -> None:
    captcha_elem = await web.web_probe(
        By.CSS_SELECTOR,
        "iframe[name^='a-'][src^='https://www.google.com/recaptcha/api2/anchor?']",
        timeout = web.timeout("captcha_detection"),
    )

    context_label = page_context or ("login page" if is_login_page else "publish operation")
    if captcha_elem is None:
        LOG.debug("No captcha detected within timeout (page_context=%s)", context_label)
        return

    if not is_login_page and captcha_config.auto_restart:
        LOG.warning("Captcha recognized - auto-restart enabled, abort run...")
        raise CaptchaEncountered(_misc.parse_duration(captcha_config.restart_delay))

    LOG.warning("############################################")
    LOG.warning("# Captcha present! Please solve the captcha.")
    LOG.warning("############################################")

    if not is_login_page:
        await web.web_scroll_page_down()

    await ainput(_("Press a key to continue..."))
