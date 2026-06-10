# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Ad extension browser workflow."""

from datetime import datetime
from typing import Any

from . import published_ads
from .model.ad_model import Ad
from .utils import dicts as _dicts
from .utils import loggers as _loggers
from .utils import misc as _misc
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
        if not ad_cfg.id:
            LOG.info(" -> SKIPPED: ad '%s' is not published yet", ad_cfg.title)
            continue

        # Find ad in published list
        published_ad = next((ad for ad in published_ads_list if ad["id"] == ad_cfg.id), None)
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
        end_date = datetime.strptime(end_date_str, "%d.%m.%Y")  # noqa: DTZ007
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

    # Process extensions
    success_count = 0
    for idx, (ad_file, ad_cfg, ad_cfg_orig) in enumerate(ads_to_extend, start = 1):
        LOG.info("Processing %s/%s: '%s' from [%s]...", idx, len(ads_to_extend), ad_cfg.title, ad_file)
        if await _extend_ad(web, root_url, ad_file, ad_cfg, ad_cfg_orig):
            success_count += 1
        await web.web_sleep()

    LOG.info("############################################")
    LOG.info("DONE: Extended %s", pluralize("ad", success_count))
    LOG.info("############################################")


async def _extend_ad(
    web:WebScrapingMixin,
    root_url:str,
    ad_file:str,
    ad_cfg:Ad,
    ad_cfg_orig:dict[str, Any],
) -> bool:
    """Extends a single ad listing."""
    LOG.info("Extending ad '%s' (ID: %s)...", ad_cfg.title, ad_cfg.id)

    try:
        # Navigate to ad management page and find extend button across all pages
        extend_button_xpath = f'//li[@data-adid="{ad_cfg.id}"]//button[contains(., "Verlängern")]'

        async def find_and_click_extend_button(page_num:int) -> bool:
            """Try to find and click extend button on current page."""
            try:
                extend_button = await web.web_find(By.XPATH, extend_button_xpath, timeout = web.timeout("quick_dom"))
                LOG.info("Found extend button on page %s", page_num)
                await extend_button.click()
                return True  # Success - stop pagination
            except TimeoutError:
                LOG.debug("Extend button not found on page %s", page_num)
                return False  # Continue to next page

        success = await web.navigate_paginated_ad_overview(find_and_click_extend_button, page_url = f"{root_url}/m-meine-anzeigen.html")

        if not success:
            LOG.error(" -> FAILED: Could not find extend button for ad ID %s", ad_cfg.id)
            return False

        # Handle confirmation dialog
        # After clicking "Verlängern", a dialog appears with:
        # - Title: "Vielen Dank!"
        # - Message: "Deine Anzeige ... wurde erfolgreich verlängert."
        # - Paid bump-up option (skipped by closing dialog)
        # Simply close the dialog with the X button (aria-label="Schließen")
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
