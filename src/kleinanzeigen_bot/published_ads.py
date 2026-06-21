# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Published ads fetching with API pagination."""

import json
from gettext import gettext as _
from typing import Any, Final, TypeAlias

from .utils import loggers as _loggers
from .utils import misc as _misc
from .utils.exceptions import KleinanzeigenBotError
from .utils.web_scraping_mixin import WebScrapingMixin

PublishedAd:TypeAlias = dict[str, Any]
"""A raw published ad entry from the Kleinanzeigen manage-ads JSON API."""


def ad_matches_id(ad:PublishedAd, target_id:int | None) -> bool:
    """Check if a published ad matches the given target ID.

    Normalizes API IDs (which may be ``str`` or ``int``) for safe comparison.
    Returns ``False`` when ``target_id`` is ``None`` or the ad's ``id`` key
    is missing, unparseable, or of an unexpected type.
    """
    if target_id is None:
        return False
    raw_id = ad.get("id")
    if raw_id is None:
        return False
    try:
        return int(raw_id) == target_id
    except (TypeError, ValueError):
        return False


LOG:Final = _loggers.get_logger(__name__)


class PublishedAdsFetchIncompleteError(KleinanzeigenBotError):
    """Raised when published ads cannot be fetched completely for ownership-critical operations."""


def _parse_published_ads_page(
    response:dict[str, Any],
    page:int,
    *,
    strict:bool,
) -> tuple[list[PublishedAd], Any, int] | None:
    """Decode and extract ads from one raw API response dict."""
    _SNIPPET_LIMIT:Final[int] = 500

    def _truncate_preview(text:str, limit:int = _SNIPPET_LIMIT) -> str:
        return text[:limit] + ("..." if len(text) > limit else "")

    content = response.get("content", "")
    if isinstance(content, bytearray):
        content = bytes(content)
    if isinstance(content, bytes):
        content = content.decode("utf-8", errors = "replace")
    if not isinstance(content, str):
        LOG.warning("Unexpected response content type on page %s: %s", page, type(content).__name__)
        if strict:
            raise PublishedAdsFetchIncompleteError(_("Unexpected response content type on page %s: %s") % (page, type(content).__name__))
        return None

    try:
        json_data = json.loads(content)
    except (json.JSONDecodeError, TypeError) as ex:
        if not content:
            LOG.warning("Empty JSON response content on page %s", page)
            if strict:
                raise PublishedAdsFetchIncompleteError(_("Empty JSON response content on page %s") % page) from ex
            return None
        snippet = _truncate_preview(content)
        LOG.warning("Failed to parse JSON response on page %s: %s (content: %s)", page, ex, snippet)
        if strict:
            raise PublishedAdsFetchIncompleteError(_("Failed to parse JSON response on page %s: %s (content: %s)") % (page, ex, snippet)) from ex
        return None

    if not isinstance(json_data, dict):
        snippet = _truncate_preview(content)
        LOG.warning("Unexpected JSON payload on page %s (content: %s)", page, snippet)
        if strict:
            raise PublishedAdsFetchIncompleteError(_("Unexpected JSON payload on page %s (content: %s)") % (page, snippet))
        return None

    page_ads = json_data.get("ads", [])
    if not isinstance(page_ads, list):
        preview = _truncate_preview(str(page_ads))
        LOG.warning("Unexpected 'ads' type on page %s: %s value: %s", page, type(page_ads).__name__, preview)
        if strict:
            raise PublishedAdsFetchIncompleteError(_("Unexpected 'ads' type on page %s: %s value: %s") % (page, type(page_ads).__name__, preview))
        return None

    filtered_page_ads:list[PublishedAd] = []
    rejected_count = 0
    rejected_preview:str | None = None
    for entry in page_ads:
        if isinstance(entry, dict) and "id" in entry and "state" in entry:
            filtered_page_ads.append(entry)
            continue
        rejected_count += 1
        if rejected_preview is None:
            rejected_preview = repr(entry)

    if rejected_count > 0:
        preview = _truncate_preview(rejected_preview or "<none>")
        LOG.warning("Filtered %s malformed ad entries on page %s (sample: %s)", rejected_count, page, preview)
        if strict:
            raise PublishedAdsFetchIncompleteError(_("Filtered %s malformed ad entries on page %s (sample: %s)") % (rejected_count, page, preview))

    paging = json_data.get("paging")
    return (filtered_page_ads, paging, len(page_ads))


def _determine_next_page(
    paging:Any,
    requested_page:int,
    raw_ads_count:int,
    *,
    strict:bool,
) -> int | None:
    """Determine the next page number from paging metadata."""
    if not isinstance(paging, dict):
        LOG.debug("No paging dict found on page %s, assuming single page", requested_page)
        if strict:
            raise PublishedAdsFetchIncompleteError(_("No paging dict found on page %s") % requested_page)
        return None

    current_page_num = _misc.coerce_page_number(paging.get("pageNum"))
    if current_page_num is None:
        LOG.warning("Invalid 'pageNum' in paging info: %s, stopping pagination", paging.get("pageNum"))
        if strict:
            raise PublishedAdsFetchIncompleteError(_("Invalid 'pageNum' in paging info: %s, stopping pagination") % paging.get("pageNum"))
        return None

    total_pages = _misc.coerce_page_number(paging.get("last"))

    if total_pages is not None and current_page_num >= total_pages:
        LOG.info("Reached last page %s of %s, stopping pagination", current_page_num, total_pages)
        return None

    if raw_ads_count == 0:
        LOG.info("No ads found on page %s, stopping pagination", requested_page)
        return None

    LOG.debug("Page %s: fetched %s ads (numFound=%s)", requested_page, raw_ads_count, paging.get("numFound"))

    next_page = _misc.coerce_page_number(paging.get("next"))
    if next_page is None:
        if total_pages is not None:
            LOG.warning("Invalid 'next' page value in paging info: %s, stopping pagination", paging.get("next"))
            if strict:
                raise PublishedAdsFetchIncompleteError(_("Invalid 'next' page value in paging info: %s, stopping pagination") % paging.get("next"))
        else:
            LOG.debug("No 'next' in paging on page %s, assuming last page", requested_page)
        return None

    return next_page


async def fetch_published_ads(
    web:WebScrapingMixin,
    root_url:str,
    *,
    strict:bool = False,
) -> list[PublishedAd]:
    """Fetch all published ads, handling API pagination.

    Args:
        web: A WebScrapingMixin instance for making web requests.
        root_url: The base URL of the Kleinanzeigen site.
        strict: If True, raise PublishedAdsFetchIncompleteError when pagination data is incomplete.

    Returns:
        List of all published ads across all pages.
    """
    ads:list[PublishedAd] = []
    page = 1
    MAX_PAGE_LIMIT:Final[int] = 100
    while True:
        # Safety check: don't paginate beyond reasonable limit
        if page > MAX_PAGE_LIMIT:
            LOG.warning("Stopping pagination after %s pages to avoid infinite loop", MAX_PAGE_LIMIT)
            if strict:
                raise PublishedAdsFetchIncompleteError(_("Stopping pagination after %s pages to avoid infinite loop") % MAX_PAGE_LIMIT)
            break

        try:
            response = await web.web_request(f"{root_url}/m-meine-anzeigen-verwalten.json?sort=DEFAULT&pageNum={page}")
        except TimeoutError as ex:
            LOG.warning("Pagination request failed on page %s: %s", page, ex)
            if strict:
                raise PublishedAdsFetchIncompleteError(_("Pagination request failed on page %s: %s") % (page, ex)) from ex
            break

        if not isinstance(response, dict):
            LOG.warning("Unexpected pagination response type on page %s: %s", page, type(response).__name__)
            if strict:
                raise PublishedAdsFetchIncompleteError(_("Unexpected pagination response type on page %s: %s") % (page, type(response).__name__))
            break

        result = _parse_published_ads_page(response, page, strict = strict)
        if result is None:
            break
        filtered_ads, paging, raw_ads_count = result
        ads.extend(filtered_ads)

        next_page = _determine_next_page(paging, page, raw_ads_count, strict = strict)
        if next_page is None:
            break
        page = next_page

    return ads
