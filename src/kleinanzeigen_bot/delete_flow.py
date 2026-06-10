# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Ad deletion browser workflow."""

from typing import Any, Final, Literal

from . import ad_state as _ad_state
from . import published_ads
from .model.ad_model import Ad
from .utils import dicts as _dicts
from .utils import loggers as _loggers
from .utils.i18n import pluralize
from .utils.misc import ensure
from .utils.web_scraping_mixin import By, WebScrapingMixin

LOG:_loggers.Logger = _loggers.get_logger(__name__)


async def delete_ads(
    web:WebScrapingMixin,
    root_url:str,
    after_delete:Literal["NONE", "RESET", "DISABLE"],
    *,
    delete_old_ads_by_title:bool,
    ad_cfgs:list[tuple[str, Ad, dict[str, Any]]],
) -> None:
    count = 0
    deleted_count = 0

    published_ads_list = await published_ads.fetch_published_ads(web, root_url)

    for ad_file, ad_cfg, ad_cfg_orig in ad_cfgs:
        count += 1
        LOG.info("Processing %s/%s: '%s' from [%s]...", count, len(ad_cfgs), ad_cfg.title, ad_file)

        # Record pre-delete id to detect whether delete_ad attempted a deletion.
        # delete_ad clears ad_cfg.id when targets were found (Phase B ran),
        # and preserves it on no-match early return.
        id_before = ad_cfg.id
        deleted = await delete_ad(web, root_url, ad_cfg, published_ads_list, delete_old_ads_by_title = delete_old_ads_by_title)
        if deleted:
            deleted_count += 1

        # Apply after_delete policy only when a delete was actually attempted.
        # Detection: True return (some 200), or id changed from non-None to None (all 404).
        # When id was already None before the call, only True return is reliable;
        # a False return could be no-match or title-match all-404, both treated as no cleanup.
        delete_attempted = deleted or (id_before is not None and ad_cfg.id is None)

        if delete_attempted and after_delete != "NONE":
            if _ad_state.apply_after_delete_policy(ad_cfg, ad_cfg_orig, mode = after_delete):
                _dicts.save_dict(ad_file, ad_cfg_orig)
        await web.web_sleep()

    LOG.info("############################################")
    LOG.info("DONE: Deleted %s of %s", deleted_count, pluralize("ad", count))
    LOG.info("############################################")


async def delete_ad(
    web:WebScrapingMixin,
    root_url:str,
    ad_cfg:Ad,
    published_ads_list:list[dict[str, Any]],
    *,
    delete_old_ads_by_title:bool,
) -> bool:
    """Delete an ad from the server.

    Returns:
        True if at least one delete request returned 200 (confirmed deleted).
        False if no matching ads were found or all returned 404 (already gone).

    Side effects:
        Clears ``ad_cfg.id`` whenever a delete was attempted, regardless of
        the server response (200 or 404). The old ID is stale in both cases.
        Preserves ``ad_cfg.id`` when no deletion was attempted (early return).
    """
    LOG.info("Deleting ad '%s' if already present...", ad_cfg.title)

    # Phase A: Build set of IDs to delete, unified across both modes
    ids_to_delete:set[int] = set()

    if delete_old_ads_by_title:
        for published_ad in published_ads_list:
            raw_id = published_ad.get("id")
            if raw_id is None:
                LOG.debug("Skipping published ad with missing id: %r", published_ad.get("title"))
                continue
            try:
                published_ad_id = int(raw_id)
            except (ValueError, TypeError):
                LOG.debug("Skipping published ad with invalid id: %r", raw_id)
                continue
            published_ad_title = published_ad.get("title", "")
            if ad_cfg.id == published_ad_id or ad_cfg.title == published_ad_title:
                LOG.debug(" -> matched ad %s '%s' for deletion", published_ad_id, published_ad_title)
                ids_to_delete.add(published_ad_id)
    elif ad_cfg.id is not None:
        ids_to_delete.add(ad_cfg.id)

    # Early return if nothing to delete — skip page open, CSRF fetch, and sleep
    if not ids_to_delete:
        LOG.info(" -> SKIPPED: no published ad matched '%s' for deletion", ad_cfg.title)
        return False

    # Phase B: Open manage-ads page, fetch CSRF token, execute deletions
    await web.web_open(f"{root_url}/m-meine-anzeigen.html")
    csrf_token_elem = await web.web_find(By.CSS_SELECTOR, "meta[name=_csrf]")
    csrf_token = csrf_token_elem.attrs.get("content")
    ensure(csrf_token is not None, "Expected CSRF Token not found in HTML content!")

    HTTP_OK:Final = 200
    deleted = False
    for target_id in ids_to_delete:
        LOG.debug(" -> deleting ad %s...", target_id)
        response = await web.web_request(
            url = f"{root_url}/m-anzeigen-loeschen.json?ids={target_id}",
            method = "POST",
            headers = {"x-csrf-token": str(csrf_token)},
            valid_response_codes = [200, 404],
        )
        if response["statusCode"] == HTTP_OK:
            deleted = True
            LOG.info(" -> SUCCESS: deleted ad '%s' (ID: %s)", ad_cfg.title, target_id)
        else:
            LOG.warning(" -> ad %s not found (status %s), may have been removed already", target_id, response["statusCode"])

    await web.web_sleep()
    # Clear ad_cfg.id whenever a delete was attempted — the old ID is stale
    # regardless of whether the server returned 200 (deleted) or 404 (already gone).
    ad_cfg.id = None
    return deleted
