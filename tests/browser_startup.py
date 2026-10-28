# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Retry transient test browser startup failures only after reaping the owned process."""

import asyncio
from pathlib import Path

from nodriver.core.browser import Browser
from nodriver.core.util import get_registered_instances

from kleinanzeigen_bot.utils.web_scraping_mixin import WebScrapingMixin

_BROWSER_START_ATTEMPTS = 5
_BROWSER_START_RETRY_DELAY = 10  # seconds, as the former shared-fixture retries


async def _close_failed_browser(web:WebScrapingMixin, instances_before:set[Browser]) -> None:
    # Integration browser startups are serial within each pytest process.
    new_instances = set(get_registered_instances()) - instances_before
    if len(new_instances) != 1:
        raise RuntimeError("Cannot identify exactly one browser from the failed startup")
    browser = new_instances.pop()
    process = getattr(browser, "_process", None)
    if not isinstance(browser, Browser) or not isinstance(process, asyncio.subprocess.Process):
        raise RuntimeError("Failed startup has no owned browser subprocess")
    if browser._process_pid != process.pid or (web.browser is not None and web.browser is not browser):
        raise RuntimeError("Failed startup browser ownership does not match")
    profile = web.browser_config.user_data_dir
    if profile:
        browser_profile = await asyncio.to_thread(Path(browser.config.user_data_dir).resolve)
        configured_profile = await asyncio.to_thread(Path(profile).resolve)
        if browser_profile != configured_profile:
            raise RuntimeError("Failed startup browser profile does not match")
    elif browser.config.uses_custom_data_dir:
        raise RuntimeError("Failed startup unexpectedly uses a custom browser profile")
    web.browser = browser
    await web.close_browser_session()
    if process.returncode is None:
        raise RuntimeError("Failed startup browser subprocess was not reaped")


async def start_browser_session(web:WebScrapingMixin) -> None:
    """Keep the existing bounded startup retry, with owned-process cleanup before every retry."""
    for attempt in range(_BROWSER_START_ATTEMPTS):
        instances_before = set(get_registered_instances())
        try:
            await web.create_browser_session()
            return
        except Exception as exc:  # noqa: BLE001 - nodriver raises a bare Exception
            if "Failed to connect to browser" not in str(exc):
                raise
            try:
                await _close_failed_browser(web, instances_before)
            except Exception as cleanup_exc:  # noqa: BLE001 - preserve the original startup error
                exc.add_note(f"Browser startup cleanup failed; retry stopped: {cleanup_exc}")
                raise exc from cleanup_exc
            if attempt == _BROWSER_START_ATTEMPTS - 1:
                raise
            await asyncio.sleep(_BROWSER_START_RETRY_DELAY)
