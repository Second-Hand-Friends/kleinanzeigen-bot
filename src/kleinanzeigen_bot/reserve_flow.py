# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Ad reservation browser workflow.

Reserving takes an ad out of circulation without ending the sale: it keeps the
ad ID, its age, its view count and every watcher/saver, and it is undone with a
single "activate". Deleting and re-publishing loses all of that, which is why
this is a separate command rather than a delete/publish round trip.

The reserved state is what the API reports as ``paused`` -- the same value
:mod:`publishing_workflow` already checks to avoid re-publishing reserved ads.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final, Literal

from . import published_ads
from .published_ads import ad_matches_id

if TYPE_CHECKING:
    from .model.ad_model import Ad
    from .published_ads import PublishedAd
from .utils import loggers as _loggers
from .utils.i18n import pluralize
from .utils.web_scraping_mixin import By, WebScrapingMixin

LOG:_loggers.Logger = _loggers.get_logger(__name__)

type ReserveAction = Literal["reserve", "activate"]

# The API's ad state that corresponds to a reserved ad in the web UI.
STATE_RESERVED:Final[str] = "paused"
STATE_ACTIVE:Final[str] = "active"

# Button labels in the manage-ads overview, per action. The buttons sit in the
# same <li data-adid="..."> row that the extend flow already targets.
_BUTTON_LABELS:Final[dict[ReserveAction, str]] = {
    "reserve": "Reservieren",
    "activate": "Aktivieren",
}

# The state an ad must currently be in for the action to be applicable.
_REQUIRED_STATE:Final[dict[ReserveAction, str]] = {
    "reserve": STATE_ACTIVE,
    "activate": STATE_RESERVED,
}

# The state the ad ends up in once the action succeeded.
_RESULTING_STATE:Final[dict[ReserveAction, str]] = {
    "reserve": STATE_RESERVED,
    "activate": STATE_ACTIVE,
}


async def set_reservation_state(
    web:WebScrapingMixin,
    root_url:str,
    ad_cfgs:list[tuple[str, Ad, dict[str, Any]]],
    *,
    action:ReserveAction,
) -> None:
    """Reserves or activates the given ads.

    Ads that are already in the target state are skipped rather than treated as
    failures -- running the command twice must not report an error.
    """
    published_ads_list = await published_ads.fetch_published_ads(web, root_url)

    ads_to_change:list[tuple[str, Ad, dict[str, Any]]] = []
    for ad_file, ad_cfg, ad_cfg_orig in ad_cfgs:
        if _is_applicable(ad_cfg, published_ads_list, action = action):
            ads_to_change.append((ad_file, ad_cfg, ad_cfg_orig))

    if not ads_to_change:
        LOG.info("############################################")
        if action == "reserve":
            LOG.info("DONE: No ads to reserve.")
        else:
            LOG.info("DONE: No ads to activate.")
        LOG.info("############################################")
        return

    success_count = 0
    for idx, (ad_file, ad_cfg, _ad_cfg_orig) in enumerate(ads_to_change, start = 1):
        LOG.info("Processing %s/%s: '%s' from [%s]...", idx, len(ads_to_change), ad_cfg.title, ad_file)
        if await _change_ad_state(web, root_url, ad_cfg, action = action):
            success_count += 1
        await web.web_sleep()

    LOG.info("############################################")
    if action == "reserve":
        LOG.info("DONE: Reserved %s", pluralize("ad", success_count))
    else:
        LOG.info("DONE: Activated %s", pluralize("ad", success_count))
    LOG.info("############################################")


def _is_applicable(
    ad_cfg:Ad,
    published_ads_list:list[PublishedAd],
    *,
    action:ReserveAction,
) -> bool:
    """Whether the action can be applied to this ad, logging the reason if not."""
    if ad_cfg.id is None:
        LOG.info(" -> SKIPPED: ad '%s' is not published yet", ad_cfg.title)
        return False

    published_ad:PublishedAd | None = next(
        (ad for ad in published_ads_list if ad_matches_id(ad, ad_cfg.id)), None
    )
    if not published_ad:
        LOG.warning(" -> SKIPPED: ad '%s' (ID: %s) not found in published ads", ad_cfg.title, ad_cfg.id)
        return False

    current_state = published_ad.get("state")
    if current_state == _REQUIRED_STATE[action]:
        return True

    if current_state == _RESULTING_STATE[action]:
        # Already where the caller wants it -- not an error, just nothing to do.
        LOG.info(" -> SKIPPED: ad '%s' is already in the requested state", ad_cfg.title)
    else:
        LOG.info(" -> SKIPPED: ad '%s' cannot be switched (state: %s)", ad_cfg.title, current_state)
    return False


async def _change_ad_state(
    web:WebScrapingMixin,
    root_url:str,
    ad_cfg:Ad,
    *,
    action:ReserveAction,
) -> bool:
    """Clicks the reserve/activate button for a single ad in the manage-ads overview."""
    if action == "reserve":
        LOG.info("Reserving ad '%s' (ID: %s)...", ad_cfg.title, ad_cfg.id)
    else:
        LOG.info("Activating ad '%s' (ID: %s)...", ad_cfg.title, ad_cfg.id)

    label = _BUTTON_LABELS[action]
    button_xpath = f'//li[@data-adid="{ad_cfg.id}"]//button[contains(., "{label}")]'

    async def find_and_click_button(page_num:int) -> bool:
        try:
            button = await web.web_find(By.XPATH, button_xpath, timeout = web.timeout("quick_dom"))
            LOG.info("Found '%s' button on page %s", label, page_num)
            await button.click()
            return True
        except TimeoutError:
            LOG.debug("'%s' button not found on page %s", label, page_num)
            return False

    try:
        success = await web.navigate_paginated_ad_overview(
            find_and_click_button, page_url = f"{root_url}/m-meine-anzeigen.html"
        )
    except TimeoutError as ex:
        LOG.error(" -> FAILED: Timeout while switching ad '%s': %s", ad_cfg.title, ex)
        return False

    if not success:
        LOG.error(" -> FAILED: Could not find '%s' button for ad ID %s", label, ad_cfg.id)
        return False

    await _dismiss_confirmation_dialog(web)

    if action == "reserve":
        LOG.info(" -> SUCCESS: ad '%s' (ID: %s) is now reserved", ad_cfg.title, ad_cfg.id)
    else:
        LOG.info(" -> SUCCESS: ad '%s' (ID: %s) is now active", ad_cfg.title, ad_cfg.id)
    return True


async def _dismiss_confirmation_dialog(web:WebScrapingMixin) -> None:
    """Closes the confirmation dialog if one appeared.

    Its absence is not an error: the action may complete without a dialog, and
    the state change has already been requested at this point either way.
    """
    try:
        await web.web_click(
            By.CSS_SELECTOR, 'button[aria-label="Schließen"]', timeout = web.timeout("quick_dom")
        )
        LOG.debug(" -> Closed confirmation dialog")
    except TimeoutError:
        LOG.debug(" -> No confirmation dialog found, action may have completed directly")
