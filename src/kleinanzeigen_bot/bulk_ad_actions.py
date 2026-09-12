# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Process selected ads during a single traversal of the management overview."""

from collections.abc import Awaitable, Callable
from typing import Any

from .model.ad_model import Ad
from .utils import loggers
from .utils.web_scraping_mixin import By, WebScrapingMixin

LOG:loggers.Logger = loggers.get_logger(__name__)
type AdEntry = tuple[str, Ad, dict[str, Any]]


async def process_ads(
    web:WebScrapingMixin,
    root_url:str,
    ad_cfgs:list[AdEntry],
    *,
    process_ad:Callable[[AdEntry, int], Awaitable[bool]],
    report_missing:Callable[[Ad], None],
) -> int:
    """Act only on selected rows, retaining IDs rather than stale DOM elements."""
    if not ad_cfgs:
        return 0

    pending:dict[str, list[AdEntry]] = {}
    for entry in ad_cfgs:
        pending.setdefault(str(entry[1].id), []).append(entry)
    total = len(ad_cfgs)
    processed_count = 0
    success_count = 0

    async def process_page(page_num:int) -> bool:
        nonlocal success_count, processed_count
        rows = await web.web_find_all(
            By.CSS_SELECTOR, "#my-manageitems-adlist li[data-adid]", timeout = web.timeout("quick_dom")
        )
        # Snapshot IDs before clicks update the DOM; each action resolves its own button.
        page_ids = [str(row.attrs.get("data-adid")) for row in rows]
        for ad_id in page_ids:
            for entry in pending.pop(ad_id, []):
                ad_file, ad_cfg, _ = entry
                processed_count += 1
                LOG.info("Processing %s/%s: '%s' from [%s]...", processed_count, total, ad_cfg.title, ad_file)
                if await process_ad(entry, page_num):
                    success_count += 1
                await web.web_sleep()
        return not pending

    await web.navigate_paginated_ad_overview(process_page, page_url = f"{root_url}/m-meine-anzeigen.html")
    for entries in pending.values():
        for ad_file, ad_cfg, _ in entries:
            processed_count += 1
            LOG.info("Processing %s/%s: '%s' from [%s]...", processed_count, total, ad_cfg.title, ad_file)
            report_missing(ad_cfg)
            await web.web_sleep()
    return success_count
