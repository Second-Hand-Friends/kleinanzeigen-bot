# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Ad extension browser workflow."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Final

from . import bulk_ad_actions, published_ads
from .published_ads import ad_matches_id
from .utils import dicts as _dicts

if TYPE_CHECKING:
    from .model.ad_model import Ad
    from .published_ads import PublishedAd
from .utils import loggers as _loggers
from .utils import misc as _misc
from .utils import page_scripts
from .utils.i18n import pluralize
from .utils.web_scraping_mixin import By, WebScrapingMixin

LOG:_loggers.Logger = _loggers.get_logger(__name__)


async def extend_ads(
    web:WebScrapingMixin,
    root_url:str,
    ad_cfgs:list[tuple[str, Ad, dict[str, Any]]],
) -> None:
    """Extends ads that are close to expiry."""
    # Fetch currently published ads from API
    published_ads_list = await published_ads.fetch_published_ads(web, root_url)

    # Filter ads that need extension
    ads_to_extend:list[tuple[str, Ad, dict[str, Any]]] = []
    for ad_file, ad_cfg, ad_cfg_orig in ad_cfgs:
        # Skip unpublished ads (no ID)
        if ad_cfg.id is None:
            LOG.info(" -> SKIPPED: ad '%s' is not published yet", ad_cfg.title)
            continue

        published_ad:PublishedAd | None = next(
            (ad for ad in published_ads_list if ad_matches_id(ad, ad_cfg.id)), None
        )
        if not published_ad:
            LOG.warning(" -> SKIPPED: ad '%s' (ID: %s) not found in published ads", ad_cfg.title, ad_cfg.id)
            continue

        # Skip non-active ads
        if published_ad.get("state") != "active":
            LOG.info(" -> SKIPPED: ad '%s' is not active (state: %s)", ad_cfg.title, published_ad.get("state"))
            continue

        # Check if ad is within 8-day extension window using API's endDate
        end_date_str = published_ad.get("endDate")
        if not end_date_str:
            LOG.warning(" -> SKIPPED: ad '%s' has no endDate in API response", ad_cfg.title)
            continue

        # Intentionally parsing naive datetime from kleinanzeigen API's German date format, timezone not relevant for date-only comparison
        try:
            end_date = datetime.strptime(end_date_str, "%d.%m.%Y")  # noqa: DTZ007
        except (ValueError, TypeError):
            LOG.warning(" -> SKIPPED: ad '%s' has invalid endDate format: %s", ad_cfg.title, end_date_str)
            continue
        days_until_expiry = (end_date.date() - _misc.now().date()).days

        # Magic value 8 is kleinanzeigen.de's platform policy: extensions only possible within 8 days of expiry
        if days_until_expiry <= 8:  # noqa: PLR2004
            LOG.info(" -> ad '%s' expires in %d days, will extend", ad_cfg.title, days_until_expiry)
            ads_to_extend.append((ad_file, ad_cfg, ad_cfg_orig))
        else:
            LOG.info(" -> SKIPPED: ad '%s' expires in %d days (can only extend within 8 days)", ad_cfg.title, days_until_expiry)

    if not ads_to_extend:
        LOG.info("No ads need extension at this time.")
        LOG.info("############################################")
        LOG.info("DONE: No ads extended.")
        LOG.info("############################################")
        return

    async def process_ad(entry:bulk_ad_actions.AdEntry, page_num:int) -> bool:
        ad_file, ad_cfg, ad_cfg_orig = entry
        return await _extend_ad(web, ad_file, ad_cfg, ad_cfg_orig, page_num = page_num)

    def report_missing(ad_cfg:Ad) -> None:
        LOG.error(" -> FAILED: Could not find extend button for ad ID %s", ad_cfg.id)

    success_count = await bulk_ad_actions.process_ads(
        web, root_url, ads_to_extend, process_ad = process_ad, report_missing = report_missing
    )

    LOG.info("############################################")
    LOG.info("DONE: Extended %s", pluralize("ad", success_count))
    LOG.info("############################################")


async def _extend_ad(
    web:WebScrapingMixin,
    ad_file:str,
    ad_cfg:Ad,
    ad_cfg_orig:dict[str, Any],
    *,
    page_num:int,
) -> bool:
    """Extends a single ad listing."""
    LOG.info("Extending ad '%s' (ID: %s)...", ad_cfg.title, ad_cfg.id)

    try:
        extend_button_xpath = f'//li[@data-adid="{ad_cfg.id}"]//button[contains(., "Verlängern")]'
        try:
            extend_button = await web.web_find(By.XPATH, extend_button_xpath, timeout = web.timeout("quick_dom"))
        except TimeoutError:
            LOG.error(" -> FAILED: Could not find extend button for ad ID %s", ad_cfg.id)
            return False
        LOG.info("Found extend button on page %s", page_num)
        await extend_button.click()

        if await _has_extension_choice_dialog(web):
            # Since the Astro redesign, "Verlängern" opens a choice dialog
            # ("Wie möchtest du deine Anzeige verlängern?") offering the free
            # "Nur verlängern" option and a paid bump-up option. The extension only
            # happens after selecting an option and confirming with "Anzeige verlängern".
            if not await _confirm_free_extension(web):
                await _close_open_dialog(web)
                LOG.error(" -> FAILED: Could not confirm the free extension option for ad ID %s", ad_cfg.id)
                return False
        else:
            # Legacy flow: the click extends directly and shows a "Vielen Dank!" dialog.
            try:
                dialog_close_timeout = web.timeout("quick_dom")
                await web.web_click(By.CSS_SELECTOR, 'button[aria-label="Schließen"]', timeout = dialog_close_timeout)
                LOG.debug(" -> Closed confirmation dialog")
            except TimeoutError:
                LOG.warning(" -> No confirmation dialog found, extension may have completed directly")

        # Update metadata in YAML file
        # Update updated_on to track when ad was extended
        ad_cfg_orig["updated_on"] = _misc.now().isoformat(timespec = "seconds")
        _dicts.save_dict(ad_file, ad_cfg_orig)

        LOG.info(" -> SUCCESS: ad extended with ID %s", ad_cfg.id)
        return True

    except TimeoutError as ex:
        LOG.error(" -> FAILED: Timeout while extending ad '%s': %s", ad_cfg.title, ex)
        return False
    except OSError as ex:
        LOG.error(" -> FAILED: Could not persist extension for ad '%s': %s", ad_cfg.title, ex)
        return False


# Text of the free option in the extension choice dialog. The paid option
# ("Verlängern & Hochschieben für 2,99 €", value "extend-bumpup") must never be
# selected; resources/js/extend_choice_dialog.js matches the free option by its
# value ("extend-only") or its own label text and rejects any priced option.
_FREE_OPTION_TEXT:Final[str] = "Nur verlängern"
_CONFIRM_BUTTON_TEXT:Final[str] = "Anzeige verlängern"


def _choice_dialog_script(action:str) -> str:
    return page_scripts.call("extend_choice_dialog", action, _FREE_OPTION_TEXT, _CONFIRM_BUTTON_TEXT)


async def _choice_dialog_state(web:WebScrapingMixin) -> str:
    return str(await web.web_execute(_choice_dialog_script("state")))


async def _has_extension_choice_dialog(web:WebScrapingMixin) -> bool:
    """Return True when the choice dialog with the free extension option is open."""
    try:
        await web.web_await(
            lambda: _dialog_is_open(web),
            timeout = web.timeout("quick_dom"),
            timeout_error_message = "Extension choice dialog did not appear",
        )
    except TimeoutError:
        return False
    return True


async def _dialog_is_open(web:WebScrapingMixin) -> bool:
    return await _choice_dialog_state(web) == "pending"


async def _confirm_free_extension(web:WebScrapingMixin) -> bool:
    """Select the free option, confirm, and wait for the dialog to finish.

    Never selects the paid option: every step re-checks that exactly one free option
    is selected and nothing else, otherwise it gives up without confirming.
    """
    selection = str(await web.web_execute(_choice_dialog_script("select")))
    if selection != "selected":
        LOG.warning(" -> Extension choice dialog not handled (%s)", selection)
        return False
    LOG.debug(" -> Selected free extension option")
    await web.web_sleep()

    confirm = str(await web.web_execute(_choice_dialog_script("confirm")))
    if confirm != "clicked":
        LOG.warning(" -> Extension choice dialog not handled (%s)", confirm)
        return False
    LOG.debug(" -> Confirmed free extension")

    try:
        result = await web.web_await(
            lambda: _extension_result(web),
            timeout = web.timeout("publishing_confirmation"),
            timeout_error_message = "Extension was not confirmed",
        )
    except TimeoutError:
        LOG.warning(" -> Extension choice dialog not handled (%s)", "no-confirmation")
        return False
    if result == "success-dialog":
        await _close_open_dialog(web)
    return True


async def _extension_result(web:WebScrapingMixin) -> str | None:
    state = await _choice_dialog_state(web)
    return None if state == "pending" else state


async def _close_open_dialog(web:WebScrapingMixin) -> None:
    try:
        await web.web_click(By.CSS_SELECTOR, 'button[aria-label="Schließen"]', timeout = web.timeout("quick_dom"))
        LOG.debug(" -> Closed confirmation dialog")
    except TimeoutError:
        LOG.debug(" -> No dialog to close")
