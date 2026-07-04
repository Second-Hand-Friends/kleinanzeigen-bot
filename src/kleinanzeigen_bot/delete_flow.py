# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Ad deletion browser workflow."""

from gettext import gettext as _
from typing import Any, Final, Literal, NamedTuple

from . import ad_state as _ad_state
from . import published_ads
from .model.ad_model import Ad
from .published_ads import PublishedAd
from .utils import dicts as _dicts
from .utils import loggers as _loggers
from .utils.i18n import pluralize
from .utils.misc import ensure
from .utils.web_scraping_mixin import By, WebScrapingMixin


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

    needs_title_matching = delete_old_ads_by_title and any(ad_cfg.id is None for _, ad_cfg, _ in ad_cfgs)
    title_matching_fetch_error:published_ads.PublishedAdsFetchIncompleteError | None = None
    if needs_title_matching:
        try:
            published_ads_list = await published_ads.fetch_published_ads(web, root_url, strict = True)
        except published_ads.PublishedAdsFetchIncompleteError as ex:
            published_ads_list = []
            title_matching_fetch_error = ex
    else:
        published_ads_list = await published_ads.fetch_published_ads(web, root_url, strict = False)

    for ad_file, ad_cfg, ad_cfg_orig in ad_cfgs:
        count += 1
        LOG.info("Processing %s/%s: '%s' from [%s]...", count, len(ad_cfgs), ad_cfg.title, ad_file)

        if ad_cfg.id is None and delete_old_ads_by_title and title_matching_fetch_error is not None:
            LOG.error(
                " -> SKIPPED: title-based deletion requires a complete published ads list: %s",
                title_matching_fetch_error,
            )
            result = DeleteResult(deleted = False, attempted = False)
        else:
            result = await delete_ad(web, root_url, ad_cfg, published_ads_list, delete_old_ads_by_title = delete_old_ads_by_title)
        if result.deleted:
            deleted_count += 1

        if result.attempted and after_delete != "NONE":
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
    await web.web_open(f"{root_url}/m-meine-anzeigen.html")
    csrf_token_elem = await web.web_find(By.CSS_SELECTOR, "meta[name=_csrf]")
    csrf_token = csrf_token_elem.attrs.get("content")
    ensure(csrf_token is not None and isinstance(csrf_token, str) and csrf_token.strip(), _("Expected CSRF Token not found in HTML content!"))

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
    return DeleteResult(deleted = deleted, attempted = True)
