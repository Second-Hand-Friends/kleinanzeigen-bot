# SPDX-FileCopyrightText: © Ollornog and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Write the online state and expiry date of published ads back to the local ad files (``sync-status``).

The online data comes from the same manage-ads JSON API that ``list`` uses
(:func:`published_ads.fetch_published_ads`). Only the auto-managed fields
``online_state`` and ``expires_on`` are written. Both are excluded from the
content hash, so a sync never makes an ad count as "changed". Nothing is changed
online, and no local file is created, deleted or otherwise modified.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from gettext import gettext as _
from typing import TYPE_CHECKING, Any, Final

from . import download_selection as _download_selection
from .utils import dicts as _dicts
from .utils import loggers as _loggers
from .utils.exceptions import AdBatchError
from .utils.i18n import pluralize

if TYPE_CHECKING:
    from .model.ad_model import Ad
    from .published_ads import PublishedAd

LOG:Final[_loggers.Logger] = _loggers.get_logger(__name__)

type LoadedAd = tuple[str, str, Ad, dict[str, Any]]
"""``(absolute path, relative path, validated Ad, raw YAML dict)`` as returned by :func:`ad_loading.load_ad_configs`."""

# Date format of ``endDate`` in the manage-ads JSON API, e.g. "05.10.2026".
_API_DATE_FORMAT:Final[str] = "%d.%m.%Y"


@dataclass(slots = True)
class SyncStatusResult:
    """Counts of one ``sync-status`` run."""

    updated:int = 0
    unchanged:int = 0
    not_found_online:int = 0
    failed:int = 0


def parse_expiry_date(value:Any) -> date | None:
    """Parse the API's ``endDate`` ("dd.mm.yyyy"); missing or empty values mean "no expiry date".

    >>> parse_expiry_date("05.10.2026")
    datetime.date(2026, 10, 5)
    >>> parse_expiry_date(None) is None
    True

    :raises ValueError: if the value is present but not a valid date
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str):
        raise ValueError(value)
    # The API returns a plain calendar date; a timezone is irrelevant for it.
    return datetime.strptime(value.strip(), _API_DATE_FORMAT).date()  # noqa: DTZ007


def select_ads_to_sync(loaded_ads:list[LoadedAd], ads_selector:str) -> list[LoadedAd]:
    """Return the local ads that have an ID and match ``ads_selector`` (``all`` or numeric IDs).

    Ads without an ID were never published, so there is no online state to sync.
    """
    requested_ids:set[int] | None = None
    if _download_selection.is_numeric_ids_selector(ads_selector):
        requested_ids = {int(ad_id) for ad_id in ads_selector.split(",")}

    selected:list[LoadedAd] = []
    for entry in loaded_ads:
        _ad_file, ad_file_relative, ad_cfg, _ad_cfg_orig = entry
        if ad_cfg.id is None:
            LOG.debug(" -> SKIPPED: ad [%s] has no ID (not published yet)", ad_file_relative)
            continue
        if requested_ids is not None and ad_cfg.id not in requested_ids:
            continue
        selected.append(entry)

    if requested_ids is not None:
        found_ids = {entry[2].id for entry in selected}
        for ad_id in sorted(requested_ids - found_ids):
            LOG.warning("No local ad file found for ID %s", ad_id)
    return selected


def _set_or_remove(ad_cfg_orig:dict[str, Any], key:str, value:str | None) -> None:
    """Set ``key`` to ``value``, or remove it when the value is unknown, keeping the rest of the file intact."""
    if value is None:
        ad_cfg_orig.pop(key, None)
    else:
        ad_cfg_orig[key] = value


def _sync_ad(entry:LoadedAd, online_ad:PublishedAd, result:SyncStatusResult) -> None:
    """Write the online state and expiry date of one ad to its file if they differ from the local values."""
    ad_file, ad_file_relative, ad_cfg, ad_cfg_orig = entry

    state = online_ad.get("state")
    new_state = str(state) if state is not None and str(state).strip() else None  # missing or empty means unknown
    expiry_valid = True
    try:
        new_expires_on = parse_expiry_date(online_ad.get("endDate"))
    except ValueError:
        LOG.warning(
            " -> ad [%s] (ID: %s): invalid expiry date from kleinanzeigen.de: %s; keeping the local value",
            ad_file_relative, ad_cfg.id, online_ad.get("endDate"),
        )
        expiry_valid = False
        new_expires_on = ad_cfg.expires_on

    if new_state == ad_cfg.online_state and new_expires_on == ad_cfg.expires_on:
        LOG.info(" -> UNCHANGED: ad [%s] (ID: %s)", ad_file_relative, ad_cfg.id)
        result.unchanged += 1
        return

    # Only the two auto-managed fields are touched; the round-trip dict keeps comments, order and all other values.
    # The date is written as an ISO string, which also works for JSON ad files.
    _set_or_remove(ad_cfg_orig, "online_state", new_state)
    if expiry_valid:
        _set_or_remove(ad_cfg_orig, "expires_on", new_expires_on.isoformat() if new_expires_on else None)
    try:
        _dicts.save_dict(ad_file, ad_cfg_orig)
    except OSError as ex:
        LOG.error(" -> FAILED: could not save ad [%s]: %s", ad_file_relative, ex)
        result.failed += 1
        return

    LOG.info(
        " -> UPDATED: ad [%s] (ID: %s): state %s -> %s, expires %s -> %s",
        ad_file_relative, ad_cfg.id,
        ad_cfg.online_state or "-", new_state or "-",
        ad_cfg.expires_on.isoformat() if ad_cfg.expires_on else "-",
        new_expires_on.isoformat() if new_expires_on else "-",
    )
    result.updated += 1


def sync_ad_status(
    ads:list[LoadedAd],
    online_ads:list[PublishedAd],
    *,
    report_online_ads_without_local_file:bool = True,
) -> SyncStatusResult:
    """Match ``ads`` to ``online_ads`` by ID and write each ad's online state and expiry date to its file.

    - Local ads not listed online are reported and left unchanged.
    - Online ads without a local file are reported when ``report_online_ads_without_local_file`` is set.

    :raises AdBatchError: if at least one ad file could not be saved (after all other ads were processed)
    """
    online_by_id:dict[int, PublishedAd] = {}
    for published_ad in online_ads:
        try:
            online_by_id[int(published_ad["id"])] = published_ad
        except (KeyError, TypeError, ValueError):
            LOG.warning("Skipping online ad with non-numeric id: %s", published_ad.get("id"))

    result = SyncStatusResult()
    for entry in ads:
        _ad_file, ad_file_relative, ad_cfg, _ad_cfg_orig = entry
        matching_ad = online_by_id.get(ad_cfg.id) if ad_cfg.id is not None else None
        if matching_ad is None:
            LOG.warning(" -> NOT FOUND ONLINE: ad [%s] (ID: %s) is not listed in your account; local file left unchanged", ad_file_relative, ad_cfg.id)
            result.not_found_online += 1
            continue
        _sync_ad(entry, matching_ad, result)

    if report_online_ads_without_local_file:
        local_ids = {entry[2].id for entry in ads}
        missing_locally = [(ad_id, online_ad) for ad_id, online_ad in online_by_id.items() if ad_id not in local_ids]
        for ad_id, online_ad in missing_locally:
            LOG.info("Online ad %s ('%s') has no local ad file.", ad_id, online_ad.get("title") or "-")
        if missing_locally:
            LOG.info("Use 'download --ads=new' to download online ads that have no local ad file.")

    LOG.info("############################################")
    LOG.info(
        "DONE: Checked %s: %s updated, %s unchanged, %s not found online, %s failed",
        pluralize("ad", len(ads)), result.updated, result.unchanged, result.not_found_online, result.failed,
    )
    LOG.info("############################################")

    if result.failed:
        raise AdBatchError(_("Failed to save %s") % pluralize("ad", result.failed))
    return result
