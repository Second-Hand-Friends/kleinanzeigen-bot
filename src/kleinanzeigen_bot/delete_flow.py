# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Ad deletion browser workflow."""

from gettext import gettext as _
from typing import Any, Final, Literal, NamedTuple

from nodriver.core.connection import ProtocolException

from . import ad_state as _ad_state
from . import published_ads
from .model.ad_model import Ad
from .published_ads import PublishedAd
from .utils import dicts as _dicts
from .utils import loggers as _loggers
from .utils import page_scripts
from .utils.exceptions import AdBatchError
from .utils.i18n import pluralize
from .utils.misc import ensure
from .utils.web_scraping_mixin import WebScrapingMixin


class DeleteResult(NamedTuple):
    """Outcome of a delete_ad call.

    :param deleted: True if at least one server response was 200.
    :param attempted: True if HTTP DELETE requests were actually sent
        (Phase B ran), regardless of server response codes.
        Always True when ``deleted`` is True.
    """

    deleted:bool
    attempted:bool


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
    failed_count = 0
    skipped_ids_not_in_account:list[int] = []

    # The account's own ads are needed to match ID-less ads by title and to check explicit IDs:
    # deleting an ID that belongs to another account (e.g. after moving ads to a new account) makes
    # the server answer with an error that would abort the whole batch.
    needs_title_matching = delete_old_ads_by_title and any(ad_cfg.id is None for _, ad_cfg, _ in ad_cfgs)
    needs_published_ads = needs_title_matching or any(ad_cfg.id is not None for _, ad_cfg, _ in ad_cfgs)
    published_ads_fetch_error:published_ads.PublishedAdsFetchIncompleteError | None = None
    published_ads_list:list[PublishedAd] = []
    if needs_published_ads:
        try:
            published_ads_list = await published_ads.fetch_published_ads(web, root_url, strict = True)
        except (published_ads.PublishedAdsFetchIncompleteError, AssertionError, ProtocolException) as ex:
            # Besides timeouts and malformed pages, web_request raises AssertionError for an unexpected HTTP status
            # and ProtocolException for a missing response; all mean the list is incomplete.
            published_ads_fetch_error = (
                ex if isinstance(ex, published_ads.PublishedAdsFetchIncompleteError) else published_ads.PublishedAdsFetchIncompleteError(str(ex))
            )
            if any(ad_cfg.id is not None for _, ad_cfg, _ in ad_cfgs):
                # Keep deleting by ID as before rather than blocking the batch on an incomplete list.
                LOG.warning("Could not check ad IDs against your account, deleting by ID without the check: %s", ex)
    published_ids = _published_ad_ids(published_ads_list)

    for ad_file, ad_cfg, ad_cfg_orig in ad_cfgs:
        count += 1
        LOG.info("Processing %s/%s: '%s' from [%s]...", count, len(ad_cfgs), ad_cfg.title, ad_file)

        if ad_cfg.id is None and delete_old_ads_by_title and published_ads_fetch_error is not None:
            LOG.error(
                " -> SKIPPED: title-based deletion requires a complete published ads list: %s",
                published_ads_fetch_error,
            )
            result = DeleteResult(deleted = False, attempted = False)
        elif ad_cfg.id is not None and published_ads_fetch_error is None and ad_cfg.id not in published_ids:
            LOG.warning(" -> SKIPPED: ad '%s' (ID: %s) is not listed in your account", ad_cfg.title, ad_cfg.id)
            skipped_ids_not_in_account.append(ad_cfg.id)
            result = DeleteResult(deleted = False, attempted = False)
        else:
            try:
                result = await delete_ad(web, root_url, ad_cfg, published_ads_list, delete_old_ads_by_title = delete_old_ads_by_title)
            except (AssertionError, ProtocolException) as ex:
                # A rejected delete request (e.g. HTTP 500) must not abort the remaining ads.
                LOG.error(" -> FAILED: could not delete ad '%s' (ID: %s): %s", ad_cfg.title, ad_cfg.id, ex)
                failed_count += 1
                result = DeleteResult(deleted = False, attempted = False)
        if result.deleted:
            deleted_count += 1

        if result.attempted and after_delete != "NONE":
            if _ad_state.apply_after_delete_policy(ad_cfg, ad_cfg_orig, mode = after_delete):
                _dicts.save_dict(ad_file, ad_cfg_orig)
        await web.web_sleep()

    LOG.info("############################################")
    LOG.info("DONE: Deleted %s of %s", deleted_count, pluralize("ad", count))
    if skipped_ids_not_in_account:
        LOG.warning(
            "Skipped %s not listed in your account (IDs: %s); their local files were left unchanged",
            pluralize("ad", skipped_ids_not_in_account),
            ", ".join(str(ad_id) for ad_id in skipped_ids_not_in_account),
        )
    LOG.info("############################################")
    if failed_count:
        raise AdBatchError(_("Failed to delete %s") % pluralize("ad", failed_count))


async def delete_all_online_ads(web:WebScrapingMixin, root_url:str) -> None:
    """Delete every ad the account lists online, independent of local ad files.

    Uses the same strict, paginated list as the ``list`` command. If the list is incomplete nothing is deleted.
    A failing delete request for one ad is logged and the remaining ads are still processed.
    """
    online_ads = await published_ads.fetch_published_ads(web, root_url, strict = True)
    targets:list[tuple[int, str]] = []
    for published_ad in online_ads:
        try:
            targets.append((int(published_ad["id"]), str(published_ad.get("title") or "-")))
        except (KeyError, TypeError, ValueError):
            LOG.warning("Skipping online ad with invalid id: %r", published_ad.get("id"))

    LOG.info("Found %s online to delete", pluralize("ad", len(targets)))
    if not targets:
        LOG.info("############################################")
        LOG.info("DONE: No ads to delete found.")
        LOG.info("############################################")
        return

    csrf_token = await _fetch_csrf_token(web, root_url)
    deleted_count = 0
    failed_count = 0
    for index, (ad_id, title) in enumerate(targets, start = 1):
        LOG.info("Processing %s/%s: '%s' (ID: %s)...", index, len(targets), title, ad_id)
        try:
            if await _request_delete(web, root_url, csrf_token, ad_id, title):
                deleted_count += 1
        except (AssertionError, ProtocolException) as ex:
            LOG.error(" -> FAILED: could not delete ad '%s' (ID: %s): %s", title, ad_id, ex)
            failed_count += 1
        await web.web_sleep()

    LOG.info("############################################")
    LOG.info("DONE: Deleted %s of %s", deleted_count, pluralize("ad", len(targets)))
    LOG.info("############################################")
    if failed_count:
        raise AdBatchError(_("Failed to delete %s") % pluralize("ad", failed_count))


async def _fetch_csrf_token(web:WebScrapingMixin, root_url:str) -> str:
    await web.web_open(f"{root_url}/m-meine-anzeigen.html")
    csrf_token = await web.web_execute(page_scripts.call("csrf_token"))
    ensure(isinstance(csrf_token, str) and csrf_token.strip(), _("Expected CSRF Token not found in HTML content!"))
    return str(csrf_token)


async def _request_delete(web:WebScrapingMixin, root_url:str, csrf_token:str, target_id:int, title:str) -> bool:
    """Send one delete request; return True on HTTP 200, False on 404 (already gone)."""
    HTTP_OK:Final = 200
    LOG.debug(" -> deleting ad %s...", target_id)
    response = await web.web_request(
        url = f"{root_url}/m-anzeigen-loeschen.json?ids={target_id}",
        method = "POST",
        headers = {"x-csrf-token": csrf_token},
        valid_response_codes = [200, 404],
    )
    if response["statusCode"] == HTTP_OK:
        LOG.info(" -> SUCCESS: deleted ad '%s' (ID: %s)", title, target_id)
        return True
    LOG.warning(" -> ad %s not found (status %s), may have been removed already", target_id, response["statusCode"])
    return False


def _published_ad_ids(published_ads_list:list[PublishedAd]) -> set[int]:
    """Return the numeric IDs of the account's published ads, ignoring entries without a usable ID."""
    ids:set[int] = set()
    for published_ad in published_ads_list:
        try:
            ids.add(int(published_ad["id"]))
        except (KeyError, TypeError, ValueError):
            LOG.debug("Skipping published ad with invalid id: %r", published_ad.get("id"))
    return ids


async def delete_ad(
    web:WebScrapingMixin,
    root_url:str,
    ad_cfg:Ad,
    published_ads_list:list[PublishedAd],
    *,
    delete_old_ads_by_title:bool,
) -> DeleteResult:
    """Delete an ad from the server.

    Returns:
        :class:`DeleteResult` with ``deleted`` (at least one 200) and
        ``attempted`` (HTTP DELETE requests were actually sent).

    Side effects:
        Clears ``ad_cfg.id`` whenever a delete was attempted, regardless of
        the server response (200 or 404). The old ID is stale in both cases.
        Preserves ``ad_cfg.id`` when no deletion was attempted (early return).
    """
    LOG.info("Deleting ad '%s' if already present...", ad_cfg.title)

    # Phase A: Build set of IDs to delete. Explicit IDs are exact-ID only;
    # title matching is only used for ID-less ads and must fail closed when ambiguous.
    ids_to_delete:set[int] = set()

    if ad_cfg.id is not None:
        ids_to_delete.add(ad_cfg.id)
    elif delete_old_ads_by_title:
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
            if ad_cfg.title == published_ad_title:
                LOG.debug(" -> matched ad %s '%s' for deletion", published_ad_id, published_ad_title)
                ids_to_delete.add(published_ad_id)

        if len(ids_to_delete) > 1:
            LOG.error(
                " -> SKIPPED: title '%s' matched multiple published ads (%s); delete by ID instead",
                ad_cfg.title,
                ", ".join(str(ad_id) for ad_id in sorted(ids_to_delete)),
            )
            return DeleteResult(deleted = False, attempted = False)

    # Early return if nothing to delete — skip page open, CSRF fetch, and sleep
    if not ids_to_delete:
        LOG.info(" -> SKIPPED: no published ad matched '%s' for deletion", ad_cfg.title)
        return DeleteResult(deleted = False, attempted = False)

    # Phase B: Open manage-ads page, fetch CSRF token, execute deletions
    csrf_token = await _fetch_csrf_token(web, root_url)

    deleted = False
    for target_id in ids_to_delete:
        if await _request_delete(web, root_url, csrf_token, target_id, ad_cfg.title):
            deleted = True

    await web.web_sleep()
    # Clear ad_cfg.id whenever a delete was attempted — the old ID is stale
    # regardless of whether the server returned 200 (deleted) or 404 (already gone).
    ad_cfg.id = None
    return DeleteResult(deleted = deleted, attempted = True)
