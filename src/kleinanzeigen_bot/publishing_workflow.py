# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Orchestration for publishing and updating ads.

This module owns publish/update orchestration prelude and sequencing,
retry/uncertainty policy, delete-before/after-publish wiring, and the
post-submit publishing result check.

Does **not** own: form section details (:mod:`publishing_form`),
captcha mechanics (:mod:`captcha_flow`), submit/confirm/ad-id recovery
(:mod:`publishing_submission`), or YAML mutation/local path renaming
(:mod:`publishing_persistence`).
"""

import asyncio
import sys
from collections.abc import Awaitable, Callable
from typing import Any, Final

from nodriver.core.connection import ProtocolException
from ruamel.yaml import YAML

from . import ad_state as _ad_state
from . import delete_flow, published_ads
from . import price_reduction as _price_reduction
from . import publishing_form as _publishing_form
from . import publishing_persistence as _publishing_persistence
from . import publishing_submission as _publishing_submission
from .model.ad_model import Ad, AdUpdateStrategy
from .model.config_model import Config
from .published_ads import PublishedAd, PublishedAdsFetchIncompleteError, ad_matches_id
from .utils import loggers as _loggers
from .utils.exceptions import CategoryResolutionError, PublishSubmissionUncertainError
from .utils.i18n import pluralize
from .utils.web_scraping_mixin import By, Is, WebScrapingMixin

LOG = _loggers.get_logger(__name__)

SUBMISSION_MAX_RETRIES:Final[int] = 3


class PostPublishPersistenceError(RuntimeError):
    """Raised when local persistence fails after successful remote publish/update."""

    def __init__(self, *, ad_id:int | None, ad_title:str, original:Exception) -> None:
        self.ad_id = ad_id
        self.ad_title = ad_title
        super().__init__(f"Post-publish persistence failed for '{ad_title}' (ad ID {ad_id})")
        self.original = original


async def check_publishing_result(web:WebScrapingMixin) -> bool:
    """Check for publishing success messages (checking-done or not-completed)."""
    return await web.web_check(By.ID, "checking-done", Is.DISPLAYED) or await web.web_check(By.ID, "not-completed", Is.DISPLAYED)


async def delete_old_ad_if_needed(  # noqa: SLF001 — accessed by bot seam via publishing_workflow.delete_old_ad_if_needed
    web:WebScrapingMixin,
    ad_cfg:Ad,
    published_ads_list:list[PublishedAd],
    *,
    timing:str,
    keep_old_ads:bool,
    config:Config,
    root_url:str,
) -> None:
    """Delete an old ad before or after (re-)publishing, depending on config.

    Skips deletion when *keep_old_ads* is True or when the configured
    ``delete_old_ads`` timing does not match *timing*.

    In ``AFTER_PUBLISH`` mode, title-based deletion is always disabled to
    avoid accidentally removing the newly published ad.
    """
    if keep_old_ads:
        return
    if config.publishing.delete_old_ads != timing:
        return
    delete_old_ads_by_title = (
        config.publishing.delete_old_ads_by_title
        if timing == "BEFORE_PUBLISH" else False
    )
    await delete_flow.delete_ad(
        web = web, root_url = root_url,
        ad_cfg = ad_cfg,
        published_ads_list = published_ads_list,
        delete_old_ads_by_title = delete_old_ads_by_title,
    )


async def publish_ad(
    web:WebScrapingMixin,
    ad_file:str,
    ad_cfg:Ad,
    ad_cfg_orig:dict[str, Any],
    published_ads_list:list[PublishedAd],
    mode:AdUpdateStrategy = AdUpdateStrategy.REPLACE,
    *,
    root_url:str,
    config:Config,
    keep_old_ads:bool,
    config_file_path:str,
) -> None:
    """Publish or update an ad on Kleinanzeigen.

    Args:
        web: A WebScrapingMixin instance for browser interactions.
        ad_file: Path to the ad configuration YAML file.
        ad_cfg: The effective ad configuration with default values applied.
        ad_cfg_orig: The original ad config as present in the YAML file.
        published_ads_list: List of published ads from the API, used for
            deduplication and old ad deletion.
        mode: The ad editing strategy. REPLACE creates a new ad (full
            republish), MODIFY updates an existing ad in-place.
        root_url: Base Kleinanzeigen URL.
        config: Full application config.
        keep_old_ads: If True, skip old-ad deletion.
        config_file_path: Path to the config file (for relative path
            resolution).
    """
    old_ad_id = ad_cfg.id

    if mode == AdUpdateStrategy.REPLACE:
        await delete_old_ad_if_needed(
            web, ad_cfg, published_ads_list,
            timing = "BEFORE_PUBLISH",
            keep_old_ads = keep_old_ads,
            config = config,
            root_url = root_url,
        )

        # Apply auto price reduction in REPLACE mode (republish flow)
        _price_reduction.apply_auto_price_reduction(
            ad_cfg, ad_cfg_orig,
            _ad_state.relative_ad_path(ad_file, config_file_path),
            mode = AdUpdateStrategy.REPLACE,
        )

        LOG.info("Publishing ad '%s'...", ad_cfg.title)
        await web.web_open(f"{root_url}/p-anzeige-aufgeben-schritt2.html", reload_if_already_open = True)
    else:
        # Always run restore-first when enabled so previously applied reductions
        # are restored even when on_update is false.  The evaluator handles
        # the on_update guard internally (returns early without advancing).
        if ad_cfg.auto_price_reduction and ad_cfg.auto_price_reduction.enabled:
            _price_reduction.apply_auto_price_reduction(
                ad_cfg, ad_cfg_orig,
                _ad_state.relative_ad_path(ad_file, config_file_path),
                mode = AdUpdateStrategy.MODIFY,
            )

        LOG.info("Updating ad '%s'...", ad_cfg.title)
        await web.web_open(f"{root_url}/p-anzeige-bearbeiten.html?adId={ad_cfg.id}", reload_if_already_open = True)

    await web.dismiss_consent_banner()

    if _loggers.is_debug(LOG):
        LOG.debug(" -> effective ad meta:")
        YAML().dump(ad_cfg.model_dump(), sys.stdout)

    await _publishing_form.fill_ad_form(
        web, ad_file, ad_cfg, mode,
        root_url = root_url, ad_defaults = config.ad_defaults,
    )

    ad_id = await _publishing_submission.submit_and_confirm_ad(
        web, ad_file, ad_cfg, mode,
        captcha_config = config.captcha,
    )

    try:
        _publishing_persistence.persist_published_ad(
            ad_file, ad_cfg, ad_cfg_orig, old_ad_id, ad_id, mode,
            config = config,
        )
    except Exception as ex:
        LOG.error(  # noqa: G201 — must use .error(exc_info=True) for translation lookup
            "Post-publish persistence failed for '%s' (ad ID %s - ad is live on "
            "Kleinanzeigen but local YAML may be out of sync)",
            ad_cfg.title, ad_id, exc_info = True,
        )
        raise PostPublishPersistenceError(ad_id = ad_id, ad_title = ad_cfg.title, original = ex) from ex


async def _fetch_published_ads_for_publish(
    web:WebScrapingMixin,
    root_url:str,
    config:Config,
    ad_cfgs:list[tuple[str, Ad, dict[str, Any]]],
) -> tuple[list[PublishedAd], list[PublishedAd] | None, bool]:
    """Fetch published ads for publish flow, strictly when title cleanup needs it."""
    require_strict_fetch = (
        config.publishing.delete_old_ads == "BEFORE_PUBLISH"
        and config.publishing.delete_old_ads_by_title
        and any(ad_cfg.id is None for _ad_file, ad_cfg, _ad_cfg_orig in ad_cfgs)
    )
    published_ads_list = await published_ads.fetch_published_ads(web, root_url)
    strict_published_ads_list:list[PublishedAd] | None = None

    if require_strict_fetch:
        try:
            strict_published_ads_list = await published_ads.fetch_published_ads(
                web,
                root_url,
                strict = True,
            )
        except PublishedAdsFetchIncompleteError as ex:
            LOG.error(
                "Skipping title-based publishes because full published-ad list could not "
                "be fetched before publish: %s",
                ex,
            )

    return published_ads_list, strict_published_ads_list, require_strict_fetch


async def publish_ads(
    web:WebScrapingMixin,
    ad_cfgs:list[tuple[str, Ad, dict[str, Any]]],
    *,
    root_url:str,
    config:Config,
    keep_old_ads:bool,
    capture_diagnostics:Callable[..., Awaitable[None]] | None = None,
    config_file_path:str,
) -> None:
    """Publish multiple ads with retry and uncertainty handling.

    Args:
        web: A WebScrapingMixin instance for browser interactions.
        ad_cfgs: List of (ad_file, ad_cfg, ad_cfg_orig) tuples.
        root_url: Base Kleinanzeigen URL.
        config: Full application config.
        keep_old_ads: If True, skip old-ad deletion.
        capture_diagnostics: Optional async callable that captures publish
            error diagnostics. Expected signature:
            ``(ad_cfg, ad_cfg_orig, ad_file, attempt, exc)``.
        config_file_path: Path to the config file (for relative path
            resolution).
    """
    count = 0
    failed_count = 0
    max_retries = SUBMISSION_MAX_RETRIES
    published_ads_list, strict_published_ads_list, require_strict_fetch = await _fetch_published_ads_for_publish(
        web,
        root_url,
        config,
        ad_cfgs,
    )

    for idx, (ad_file, ad_cfg, ad_cfg_orig) in enumerate(ad_cfgs, start = 1):
        LOG.info("Processing %s/%s: '%s' from [%s]...", idx, len(ad_cfgs), ad_cfg.title, ad_file)

        published_ads_for_matching = (
            strict_published_ads_list
            if ad_cfg.id is None and strict_published_ads_list is not None
            else published_ads_list
        )

        if ad_cfg.id is None and strict_published_ads_list is None and require_strict_fetch:
            LOG.warning(
                "Skipping '%s' because strict published-ad fetch failed before publish. "
                "Retry by re-running the publish after transient API issues have resolved.",
                ad_cfg.title,
            )
            count += 1
            failed_count += 1
            continue

        if any(ad_matches_id(x, ad_cfg.id) and x.get("state") == "paused" for x in published_ads_for_matching):
            LOG.info("Skipping because ad is reserved")
            continue

        count += 1
        success = False
        baseline_price = ad_cfg.price
        baseline_price_reduction_count = ad_cfg.price_reduction_count

        for attempt in range(1, max_retries + 1):
            try:
                # publish_ad mutates pricing fields before submit; reset them
                # so retries remain idempotent for a single eligible reduction cycle.
                ad_cfg.price = baseline_price
                ad_cfg.price_reduction_count = baseline_price_reduction_count
                await publish_ad(
                    web, ad_file, ad_cfg, ad_cfg_orig,
                    published_ads_for_matching, AdUpdateStrategy.REPLACE,
                    root_url = root_url, config = config,
                    keep_old_ads = keep_old_ads,
                    config_file_path = config_file_path,
                )
                success = True
                break  # Publish succeeded, exit retry loop
            except asyncio.CancelledError:
                raise  # Respect task cancellation
            except CategoryResolutionError as ex:
                if capture_diagnostics:
                    await capture_diagnostics(ad_cfg, ad_cfg_orig, ad_file, attempt, ex)
                LOG.error(
                    "Category resolution failed for '%s': %s. Skipping ad (configuration error, no retry).",
                    ad_cfg.title, ex,
                )
                failed_count += 1
                break
            except PublishSubmissionUncertainError as ex:
                if capture_diagnostics:
                    await capture_diagnostics(ad_cfg, ad_cfg_orig, ad_file, attempt, ex)
                LOG.warning(
                    "Attempt %s/%s for '%s' reached submit boundary but failed: %s. "
                    "Not retrying to prevent duplicate listings.",
                    attempt, max_retries, ad_cfg.title, ex,
                )
                LOG.warning(
                    "Manual recovery required for '%s'. Check 'Meine Anzeigen' to "
                    "confirm whether the ad was posted.",
                    ad_cfg.title,
                )
                LOG.warning(
                    "If posted, sync local state with 'kleinanzeigen-bot download "
                    "--ads=new' or 'kleinanzeigen-bot download --ads=<id>'; "
                    "otherwise rerun publish for this ad.",
                )
                failed_count += 1
                break
            except PostPublishPersistenceError as ex:
                if capture_diagnostics:
                    await capture_diagnostics(ad_cfg, ad_cfg_orig, ad_file, attempt, ex)
                LOG.warning(
                    "Persistence failed for '%s' after ad submission. Ad ID: %s. "
                    "No retry performed. If the ad is online, sync local state manually.",
                    ad_cfg.title, ex.ad_id,
                )
                failed_count += 1
                break
            except (TimeoutError, ProtocolException) as ex:
                if capture_diagnostics:
                    await capture_diagnostics(ad_cfg, ad_cfg_orig, ad_file, attempt, ex)
                if attempt >= max_retries:
                    LOG.error(
                        "All %s attempts failed for '%s': %s. Skipping ad.",
                        max_retries, ad_cfg.title, ex,
                    )
                    failed_count += 1
                    break

                LOG.warning(
                    "Attempt %s/%s failed for '%s': %s. Retrying...",
                    attempt, max_retries, ad_cfg.title, ex,
                )
                await web.web_sleep(2_000)  # Wait before retry

        # Check publishing result separately (no retry - ad is already submitted)
        if success:
            try:
                publish_timeout = web.timeout("publishing_result")
                await web.web_await(
                    lambda: check_publishing_result(web),
                    timeout = publish_timeout,
                )
            except TimeoutError:
                LOG.warning(
                    " -> Could not confirm publishing for '%s', but ad may be online",
                    ad_cfg.title,
                )

            await delete_old_ad_if_needed(
                web, ad_cfg, published_ads_for_matching,
                timing = "AFTER_PUBLISH",
                keep_old_ads = keep_old_ads,
                config = config,
                root_url = root_url,
            )

    LOG.info("############################################")
    if failed_count > 0:
        LOG.info(
            "DONE: (Re-)published %s (%s failed after retries)",
            pluralize("ad", count - failed_count), failed_count,
        )
    else:
        LOG.info("DONE: (Re-)published %s", pluralize("ad", count))
    LOG.info("############################################")


async def update_ads(
    web:WebScrapingMixin,
    ad_cfgs:list[tuple[str, Ad, dict[str, Any]]],
    *,
    root_url:str,
    config:Config,
    keep_old_ads:bool,
    capture_diagnostics:Callable[..., Awaitable[None]] | None = None,
    config_file_path:str,
) -> None:
    """Update multiple published ads with retry and uncertainty handling.

    Filters to only already-published ads. Calls :func:`publish_ad` in
    MODIFY mode.

    Args:
        web: A WebScrapingMixin instance for browser interactions.
        ad_cfgs: List of (ad_file, ad_cfg, ad_cfg_orig) tuples.
        root_url: Base Kleinanzeigen URL.
        config: Full application config.
        keep_old_ads: If True, skip old-ad deletion.
        capture_diagnostics: Optional async callable that captures publish
            error diagnostics. Expected signature:
            ``(ad_cfg, ad_cfg_orig, ad_file, attempt, exc)``.
        config_file_path: Path to the config file (for relative path
            resolution).
    """
    count = 0
    failed_count = 0
    max_retries = SUBMISSION_MAX_RETRIES

    published_ads_list = await published_ads.fetch_published_ads(web, root_url)

    for idx, (ad_file, ad_cfg, ad_cfg_orig) in enumerate(ad_cfgs, start = 1):
        LOG.info("Processing %s/%s: '%s' from [%s]...", idx, len(ad_cfgs), ad_cfg.title, ad_file)

        ad = next((published_ad for published_ad in published_ads_list if ad_matches_id(published_ad, ad_cfg.id)), None)

        if not ad:
            LOG.warning(
                " -> SKIPPED: ad '%s' (ID: %s) not found in published ads",
                ad_cfg.title, ad_cfg.id,
            )
            continue

        if ad["state"] == "paused":
            LOG.info("Skipping because ad is reserved")
            continue

        count += 1
        success = False
        baseline_price = ad_cfg.price
        baseline_price_reduction_count = ad_cfg.price_reduction_count

        for attempt in range(1, max_retries + 1):
            try:
                ad_cfg.price = baseline_price
                ad_cfg.price_reduction_count = baseline_price_reduction_count
                await publish_ad(
                    web, ad_file, ad_cfg, ad_cfg_orig,
                    published_ads_list, AdUpdateStrategy.MODIFY,
                    root_url = root_url, config = config,
                    keep_old_ads = keep_old_ads,
                    config_file_path = config_file_path,
                )
                success = True
                break
            except asyncio.CancelledError:
                raise
            except PublishSubmissionUncertainError as ex:
                if capture_diagnostics:
                    await capture_diagnostics(ad_cfg, ad_cfg_orig, ad_file, attempt, ex)
                LOG.warning(
                    "Attempt %s/%s for '%s' reached submit boundary but failed: %s. "
                    "Not retrying to prevent duplicate modifications.",
                    attempt, max_retries, ad_cfg.title, ex,
                )
                LOG.warning(
                    "Manual recovery required for '%s'. Check 'Meine Anzeigen' to "
                    "confirm whether the update was applied.",
                    ad_cfg.title,
                )
                failed_count += 1
                break
            except PostPublishPersistenceError as ex:
                if capture_diagnostics:
                    await capture_diagnostics(ad_cfg, ad_cfg_orig, ad_file, attempt, ex)
                LOG.warning(
                    "Persistence failed for '%s' after ad update submission. Ad ID: %s. "
                    "No retry performed. If the ad is online, sync local state manually.",
                    ad_cfg.title, ex.ad_id,
                )
                failed_count += 1
                break
            except CategoryResolutionError as ex:
                if capture_diagnostics:
                    await capture_diagnostics(ad_cfg, ad_cfg_orig, ad_file, attempt, ex)
                LOG.error(
                    "Category resolution failed for '%s': %s. Skipping ad (configuration error, no retry).",
                    ad_cfg.title, ex,
                )
                failed_count += 1
                break
            except (TimeoutError, ProtocolException) as ex:
                if capture_diagnostics:
                    await capture_diagnostics(ad_cfg, ad_cfg_orig, ad_file, attempt, ex)
                if attempt >= max_retries:
                    LOG.error(
                        "All %s attempts failed for '%s': %s. Skipping ad.",
                        max_retries, ad_cfg.title, ex,
                    )
                    failed_count += 1
                    break

                LOG.warning(
                    "Attempt %s/%s failed for '%s': %s. Retrying...",
                    attempt, max_retries, ad_cfg.title, ex,
                )
                await web.web_sleep(2_000)

        if success:
            try:
                publish_timeout = web.timeout("publishing_result")
                await web.web_await(
                    lambda: check_publishing_result(web),
                    timeout = publish_timeout,
                )
            except TimeoutError:
                LOG.warning(
                    " -> Could not confirm update for '%s', but changes may be online",
                    ad_cfg.title,
                )

    LOG.info("############################################")
    if failed_count > 0:
        LOG.info(
            "DONE: updated %s (%s failed after retries)",
            pluralize("ad", count - failed_count), failed_count,
        )
    else:
        LOG.info("DONE: updated %s", pluralize("ad", count))
    LOG.info("############################################")
