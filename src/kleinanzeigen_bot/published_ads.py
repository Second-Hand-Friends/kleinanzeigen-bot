# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Published ads fetching with API pagination."""

import json
import logging
from gettext import gettext as _
from typing import Any, Final

from .utils import misc as _misc
from .utils.exceptions import KleinanzeigenBotError
from .utils.loggers import get_logger
from .utils.web_scraping_mixin import WebScrapingMixin

LOG:Final = get_logger(__name__)
LOG.setLevel(logging.INFO)


class PublishedAdsFetchIncompleteError(KleinanzeigenBotError):
    """Raised when published ads cannot be fetched completely for ownership-critical operations."""


async def fetch_published_ads(
    web:WebScrapingMixin,
    root_url:str,
    *,
    strict:bool = False,
) -> list[dict[str, Any]]:
    """Fetch all published ads, handling API pagination.

    Args:
        web: A WebScrapingMixin instance for making web requests.
        root_url: The base URL of the Kleinanzeigen site.
        strict: If True, raise PublishedAdsFetchIncompleteError when pagination data is incomplete.

    Returns:
        List of all published ads across all pages.
    """
    ads:list[dict[str, Any]] = []
    page = 1
    MAX_PAGE_LIMIT:Final[int] = 100
    SNIPPET_LIMIT:Final[int] = 500

    def _handle_incomplete_fetch(template:str, *args:Any, cause:Exception | None = None) -> None:
        if strict:
            raise PublishedAdsFetchIncompleteError(_(template) % args) from cause

    while True:
        # Safety check: don't paginate beyond reasonable limit
        if page > MAX_PAGE_LIMIT:
            LOG.warning("Stopping pagination after %s pages to avoid infinite loop", MAX_PAGE_LIMIT)
            _handle_incomplete_fetch("Stopping pagination after %s pages to avoid infinite loop", MAX_PAGE_LIMIT)
            break

        try:
            response = await web.web_request(f"{root_url}/m-meine-anzeigen-verwalten.json?sort=DEFAULT&pageNum={page}")
        except TimeoutError as ex:
            LOG.warning("Pagination request failed on page %s: %s", page, ex)
            _handle_incomplete_fetch("Pagination request failed on page %s: %s", page, ex, cause = ex)
            break

        if not isinstance(response, dict):
            LOG.warning("Unexpected pagination response type on page %s: %s", page, type(response).__name__)
            _handle_incomplete_fetch("Unexpected pagination response type on page %s: %s", page, type(response).__name__)
            break

        content = response.get("content", "")
        if isinstance(content, bytearray):
            content = bytes(content)
        if isinstance(content, bytes):
            content = content.decode("utf-8", errors = "replace")
        if not isinstance(content, str):
            LOG.warning("Unexpected response content type on page %s: %s", page, type(content).__name__)
            _handle_incomplete_fetch("Unexpected response content type on page %s: %s", page, type(content).__name__)
            break

        try:
            json_data = json.loads(content)
        except (json.JSONDecodeError, TypeError) as ex:
            if not content:
                LOG.warning("Empty JSON response content on page %s", page)
                _handle_incomplete_fetch("Empty JSON response content on page %s", page, cause = ex)
                break
            snippet = content[:SNIPPET_LIMIT] + ("..." if len(content) > SNIPPET_LIMIT else "")
            LOG.warning("Failed to parse JSON response on page %s: %s (content: %s)", page, ex, snippet)
            _handle_incomplete_fetch("Failed to parse JSON response on page %s: %s (content: %s)", page, ex, snippet, cause = ex)
            break

        if not isinstance(json_data, dict):
            snippet = content[:SNIPPET_LIMIT] + ("..." if len(content) > SNIPPET_LIMIT else "")
            LOG.warning("Unexpected JSON payload on page %s (content: %s)", page, snippet)
            _handle_incomplete_fetch("Unexpected JSON payload on page %s (content: %s)", page, snippet)
            break

        page_ads = json_data.get("ads", [])
        if not isinstance(page_ads, list):
            preview = str(page_ads)
            if len(preview) > SNIPPET_LIMIT:
                preview = preview[:SNIPPET_LIMIT] + "..."
            LOG.warning("Unexpected 'ads' type on page %s: %s value: %s", page, type(page_ads).__name__, preview)
            _handle_incomplete_fetch("Unexpected 'ads' type on page %s: %s value: %s", page, type(page_ads).__name__, preview)
            break

        filtered_page_ads:list[dict[str, Any]] = []
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
            preview = rejected_preview or "<none>"
            if len(preview) > SNIPPET_LIMIT:
                preview = preview[:SNIPPET_LIMIT] + "..."
            LOG.warning("Filtered %s malformed ad entries on page %s (sample: %s)", rejected_count, page, preview)
            _handle_incomplete_fetch("Filtered %s malformed ad entries on page %s (sample: %s)", rejected_count, page, preview)

        ads.extend(filtered_page_ads)

        paging = json_data.get("paging")
        if not isinstance(paging, dict):
            LOG.debug("No paging dict found on page %s, assuming single page", page)
            break

        # Use only real API fields (confirmed from production data)
        current_page_num = _misc.coerce_page_number(paging.get("pageNum"))
        total_pages = _misc.coerce_page_number(paging.get("last"))

        if current_page_num is None:
            LOG.warning("Invalid 'pageNum' in paging info: %s, stopping pagination", paging.get("pageNum"))
            _handle_incomplete_fetch("Invalid 'pageNum' in paging info: %s, stopping pagination", paging.get("pageNum"))
            break

        # Stop if reached last page (only when API provides 'last')
        if total_pages is not None and current_page_num >= total_pages:
            LOG.info("Reached last page %s of %s, stopping pagination", current_page_num, total_pages)
            break

        # Safety: stop if no ads returned
        if len(page_ads) == 0:
            LOG.info("No ads found on page %s, stopping pagination", page)
            break

        LOG.debug("Page %s: fetched %s ads (numFound=%s)", page, len(page_ads), paging.get("numFound"))

        # Use API's next field for navigation (more robust than our counter)
        next_page = _misc.coerce_page_number(paging.get("next"))
        if next_page is None:
            if total_pages is not None:
                LOG.warning("Invalid 'next' page value in paging info: %s, stopping pagination", paging.get("next"))
                _handle_incomplete_fetch("Invalid 'next' page value in paging info: %s, stopping pagination", paging.get("next"))
            else:
                LOG.debug("No 'next' in paging on page %s, assuming last page", page)
            break
        page = next_page

    return ads
