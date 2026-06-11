# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Ad download browser workflow."""

from pathlib import Path
from typing import Any, Protocol

from . import download_selection as _download_selection
from . import extract, published_ads
from .model.ad_model import Ad
from .model.config_model import DEFAULT_DOWNLOAD_DIR, Config
from .published_ads import PublishedAd
from .utils import loggers as _loggers
from .utils import xdg_paths as _xdg_paths
from .utils.files import abspath
from .utils.i18n import pluralize
from .utils.web_scraping_mixin import WebScrapingMixin


class LoadAdsFunc(Protocol):
    """Protocol for callable that loads ads, matching ad_loading.load_ads signature."""

    def __call__(self, *, ignore_inactive:bool = True, exclude_ads_with_id:bool = True) -> list[tuple[str, Ad, dict[str, Any]]]: ...


LOG:_loggers.Logger = _loggers.get_logger(__name__)


def resolve_download_dir(
    config:Config,
    config_file_path:str,
    workspace:_xdg_paths.Workspace,
) -> Path:
    """Resolve the download directory from config and workspace.

    Returns workspace.download_dir when config.download.dir is the literal
    default; otherwise resolves the configured path (relative to config file
    or absolute).
    """
    trimmed_dir = config.download.dir.strip()
    if trimmed_dir == DEFAULT_DOWNLOAD_DIR:
        return workspace.download_dir
    return Path(abspath(trimmed_dir, relative_to = str(Path(config_file_path).parent))).resolve()


async def _download_ad_with_resolved_state(
    ad_extractor:extract.AdExtractor,
    ad_id:int,
    published_ads_by_id:dict[int, PublishedAd],
) -> None:
    """Download an ad with proper active state resolution and logging.

    Resolves the ad's activity state from the published profile, logs appropriately
    based on the resolution result, and initiates the download with the resolved state.

    This function centralizes the resolution + logging + download logic used by
    the "all" and "new" selectors.

    Args:
        ad_extractor: The AdExtractor instance to use for downloading.
        ad_id: The ad ID to download.
        published_ads_by_id: Dict mapping ad IDs to published ad data from API.

    Note:
        The numeric selector does NOT use this helper because it has different
        warning message semantics (foreign ads are expected, not anomalies).
    """
    resolved = _download_selection.resolve_download_ad_activity(ad_id, published_ads_by_id)

    if not resolved.owned:
        # Ad not in user's published profile - unexpected for "all"/"new" selectors
        # since these only list the user's own ads from the overview page
        LOG.warning("Ad %d found in overview but not in published profile. Saving as inactive.", ad_id)
    elif not resolved.active:
        # Ad is in published profile but not in active state (paused, inactive, etc.)
        published_ad = published_ads_by_id.get(ad_id, {})
        LOG.debug("Ad %d has state '%s'. Saving as inactive.", ad_id, published_ad.get("state", "unknown"))

    await ad_extractor.download_ad(ad_id, active = resolved.active)


async def download_ads(
    web:WebScrapingMixin,
    config:Config,
    config_file_path:str,
    workspace:_xdg_paths.Workspace,
    ads_selector:str,
    *,
    load_ads_func:LoadAdsFunc,
    root_url:str,
) -> None:
    """
    Determines which download mode was chosen with the arguments, and calls the specified download routine.
    This downloads either all, only unsaved(new), or specific ads given by ID.
    """
    # Normalize comma-separated keyword selectors; set deduplication collapses "new,new" → {"new"}
    selector_tokens = {s.strip() for s in ads_selector.split(",")}
    if "all" in selector_tokens:
        effective_selector = "all"
    elif len(selector_tokens) == 1:
        effective_selector = next(iter(selector_tokens))  # e.g. "new,new" → "new"
    else:
        effective_selector = ads_selector  # numeric IDs: "123,456" — unchanged

    # Fetch published ads once from manage-ads JSON to avoid repetitive API calls during extraction
    # Build lookup dict inline and pass directly to extractor (no cache abstraction needed)
    LOG.info("Fetching ad metadata (status, expiry dates)...")
    published_ads_list = await published_ads.fetch_published_ads(web, root_url, strict = _download_selection.is_numeric_ids_selector(effective_selector))
    published_ads_by_id:dict[int, PublishedAd] = {}
    for published_ad in published_ads_list:
        try:
            ad_id = published_ad.get("id")
            if ad_id is not None:
                published_ads_by_id[int(ad_id)] = published_ad
        except (ValueError, TypeError):
            LOG.warning("Skipping ad with non-numeric id: %s", published_ad.get("id"))
    LOG.info("Loaded metadata for %s published ads.", len(published_ads_by_id))

    download_dir = resolve_download_dir(config, config_file_path, workspace)
    _xdg_paths.ensure_directory(download_dir, "downloaded ads directory")
    LOG.info("Ads download directory: %s", download_dir)
    ad_extractor = extract.AdExtractor(web.browser, config, download_dir, published_ads_by_id = published_ads_by_id)

    if effective_selector in {"all", "new"}:  # explore ads overview for these two modes
        LOG.info("Scanning ad overview for navigation URLs...")
        own_ad_urls = await ad_extractor.extract_own_ads_urls()
        LOG.info("Found %s.", pluralize("ad URL", len(own_ad_urls)))

        if effective_selector == "all":  # download all of your ads
            LOG.info("Starting download of all ads...")

            valid_ad_refs:list[tuple[str, int]] = []
            for ad_url in own_ad_urls:
                ad_id = ad_extractor.extract_ad_id_from_ad_url(ad_url)
                if ad_id == -1:
                    # Skip ads with invalid URLs (warning already logged by extract_ad_id_from_ad_url)
                    continue
                valid_ad_refs.append((ad_url, ad_id))

            success_count = 0
            # call download function for each ad page
            for idx, (ad_url, ad_id) in enumerate(valid_ad_refs, start = 1):
                LOG.info("Downloading %d/%d ads...", idx, len(valid_ad_refs))

                if await ad_extractor.navigate_to_ad_page(ad_url):
                    await _download_ad_with_resolved_state(ad_extractor, ad_id, published_ads_by_id)
                    success_count += 1
            LOG.info("%d of %d ads were downloaded from your profile.", success_count, len(valid_ad_refs))

        elif effective_selector == "new":  # download only unsaved ads
            # check which ads already saved
            saved_ad_ids:list[int] = []
            ads = load_ads_func(ignore_inactive = False, exclude_ads_with_id = False)  # do not skip because of existing IDs
            for ad in ads:
                saved_ad_id = ad[1].id
                if saved_ad_id is None:
                    LOG.debug("Skipping saved ad without id (likely unpublished or manually created): %s", ad[0])
                    continue
                saved_ad_ids.append(int(saved_ad_id))

            # determine ad IDs from links
            ad_id_by_url = {url: ad_extractor.extract_ad_id_from_ad_url(url) for url in own_ad_urls}

            LOG.info("Starting download of not yet downloaded ads...")
            ads_to_download:list[tuple[str, int]] = []
            for ad_url, ad_id in ad_id_by_url.items():
                # Skip ads with invalid URLs (warning already logged by extract_ad_id_from_ad_url)
                if ad_id == -1:
                    continue

                # check if ad with ID already saved
                if ad_id in saved_ad_ids:
                    LOG.info("The ad with id %d has already been saved.", ad_id)
                    continue
                ads_to_download.append((ad_url, ad_id))

            new_count = 0
            for idx, (ad_url, ad_id) in enumerate(ads_to_download, start = 1):
                LOG.info("Downloading %d/%d ads...", idx, len(ads_to_download))

                if await ad_extractor.navigate_to_ad_page(ad_url):
                    await _download_ad_with_resolved_state(ad_extractor, ad_id, published_ads_by_id)
                    new_count += 1
            LOG.info("%s were downloaded from your profile.", pluralize("new ad", new_count))

    elif _download_selection.is_numeric_ids_selector(effective_selector):  # download ad(s) with specific id(s)
        ids = [int(n) for n in effective_selector.split(",")]
        LOG.info("Starting download of ad(s) with the id(s):")
        LOG.info(" | ".join([str(ad_id) for ad_id in ids]))

        for idx, ad_id in enumerate(ids, start = 1):  # call download routine for every id
            LOG.info("Downloading %d/%d ads...", idx, len(ids))
            exists = await ad_extractor.navigate_to_ad_page(ad_id)
            if exists:
                resolved = _download_selection.resolve_download_ad_activity(ad_id, published_ads_by_id)
                if not resolved.owned:
                    # Foreign ad - expected for numeric IDs (can download any public ad)
                    LOG.warning("Ad id %d is not in your published profile ads. Saving downloaded ad as inactive.", ad_id)

                await ad_extractor.download_ad(ad_id, active = resolved.active)
                LOG.info("Downloaded ad with id %d", ad_id)
            else:
                LOG.error("The page with the id %d does not exist!", ad_id)
