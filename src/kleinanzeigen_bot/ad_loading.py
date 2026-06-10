# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Browser-free ad discovery, validation, selector filtering, and content-hash helpers.

All functions take explicit dependencies — no implicit ``self`` plumbing, no
browser imports.  The module owns:

- file discovery from glob patterns
- selector validation and filtering (``new``, ``changed``, ``due``, ``all``,
  numeric IDs)
- ad model validation and default application
- category alias resolution
- image globbing and validation
- content-hash comparison and persistence

Orchestration entry point: :func:`load_ads`.
"""
from __future__ import annotations

import os
from datetime import datetime  # noqa: TC003 — used in runtime type narrowing via _misc.now()
from gettext import gettext as _
from typing import Any, Final

from wcmatch import glob

from . import download_selection as _download_selection
from . import price_reduction as _price_reduction
from .ad_description import get_ad_description
from .model.ad_model import Ad, AdPartial
from .utils import dicts as _dicts
from .utils import loggers as _loggers
from .utils import misc as _misc
from .utils.files import abspath
from .utils.i18n import pluralize
from .utils.misc import ensure

LOG:Final[_loggers.Logger] = _loggers.get_logger(__name__)
LOG.setLevel(_loggers.INFO)


# --------------------------------------------------------------------------- #
# File discovery
# --------------------------------------------------------------------------- #


def discover_ad_files(
    config_file_path:str,
    ad_file_patterns:list[str],
) -> dict[str, str]:
    """Glob for ad config files matching *ad_file_patterns*.

    Returns a ``{abspath: relative_path}`` dict, excluding any file whose
    basename is ``ad_fields.yaml``.
    """
    ad_files:dict[str, str] = {}
    data_root_dir = os.path.dirname(config_file_path)
    for file_pattern in ad_file_patterns:
        for ad_file in glob.glob(
            file_pattern,
            root_dir = data_root_dir,
            flags = glob.GLOBSTAR | glob.BRACE | glob.EXTGLOB,
        ):
            if not str(ad_file).endswith("ad_fields.yaml"):
                ad_files[abspath(ad_file, relative_to = data_root_dir)] = ad_file
    return ad_files


# --------------------------------------------------------------------------- #
# Ad model loading
# --------------------------------------------------------------------------- #


def load_ad(ad_cfg_orig:dict[str, Any], ad_defaults:Any) -> Ad:
    """Validate a raw YAML dict into an :class:`Ad` with *ad_defaults* applied."""
    return AdPartial.model_validate(ad_cfg_orig).to_ad(ad_defaults)


# --------------------------------------------------------------------------- #
# Selector helpers
# --------------------------------------------------------------------------- #


def is_valid_ads_selector(ads_selector:str, valid_keywords:set[str]) -> bool:
    """Check whether *ads_selector* is valid for *valid_keywords*.

    Accepts a single keyword, a comma-separated list of keywords, or a
    comma-separated list of numeric IDs.  Mixed keyword+numeric selectors
    are rejected.
    """
    return (
        ads_selector in valid_keywords
        or all(s.strip() in valid_keywords for s in ads_selector.split(","))
        or _download_selection.is_numeric_ids_selector(ads_selector)
    )


# --------------------------------------------------------------------------- #
# Republication / change detection
# --------------------------------------------------------------------------- #


def check_ad_republication(
    ad_cfg:Ad,
    ad_file_relative:str,
    *,
    now:datetime | None = None,
) -> bool:
    """Return ``True`` when *ad_cfg* is due for republication.

    The interval is measured against :func:`~kleinanzeigen_bot.utils.misc.now`
    by default; pass *now* to make the function deterministic in tests.
    """
    if ad_cfg.updated_on:
        last_updated_on = ad_cfg.updated_on
    elif ad_cfg.created_on:
        last_updated_on = ad_cfg.created_on
    else:
        return True

    if not last_updated_on:
        return True

    if now is None:
        now = _misc.now()

    ad_age = now - last_updated_on
    if ad_age.days <= ad_cfg.republication_interval:
        LOG.info(
            " -> SKIPPED: ad [%s] was last published %d days ago. republication is only required every %s days",
            ad_file_relative,
            ad_age.days,
            ad_cfg.republication_interval,
        )
        return False

    return True


def check_ad_changed(
    ad_cfg:Ad,
    ad_cfg_orig:dict[str, Any],
    ad_file_relative:str,
) -> bool:
    """Return ``True`` when the ad's content hash differs from its stored hash.

    .. important::

        As a deliberate side effect, this function **mutates**
        ``ad_cfg_orig["content_hash"]`` with the freshly computed hash when a
        change is detected.  Callers must be aware of this mutation.
    """
    if not ad_cfg.id:
        # New ads are not considered "changed"
        return False

    # Calculate hash on original config to match what was stored
    current_hash = AdPartial.model_validate(ad_cfg_orig).update_content_hash().content_hash
    stored_hash = ad_cfg_orig.get("content_hash")

    LOG.debug("Hash comparison for [%s]:", ad_file_relative)
    LOG.debug("    Stored hash: %s", stored_hash)
    LOG.debug("    Current hash: %s", current_hash)

    if stored_hash and current_hash != stored_hash:
        LOG.info("Changes detected in ad [%s], will republish", ad_file_relative)
        # Update hash in original configuration
        ad_cfg_orig["content_hash"] = current_hash
        return True

    return False


# --------------------------------------------------------------------------- #
# Category & image resolution
# --------------------------------------------------------------------------- #


def resolve_ad_category(ad_cfg:Ad, categories:dict[str, str]) -> None:
    """Resolve *ad_cfg.category* alias to a numeric category ID in-place.

    When the exact alias is unknown but contains ``>``, falls back to the
    parent category if that parent is a known alias.
    """
    if not ad_cfg.category:
        return

    resolved_category_id = categories.get(ad_cfg.category)
    if not resolved_category_id and ">" in ad_cfg.category:
        # this maps actually to the sonstiges/weiteres sub-category
        parent_category = ad_cfg.category.rpartition(">")[0].strip()
        resolved_category_id = categories.get(parent_category)
        if resolved_category_id:
            LOG.warning(
                "Category [%s] unknown. Using category [%s] with ID [%s] instead.",
                ad_cfg.category,
                parent_category,
                resolved_category_id,
            )

    if resolved_category_id:
        ad_cfg.category = resolved_category_id


def resolve_ad_images(ad_file:str, image_patterns:list[str]) -> list[str]:
    """Glob and validate image files matching *image_patterns*.

    Images are resolved relative to *ad_file*'s directory.  Supported
    extensions: ``.gif``, ``.jpg``, ``.jpeg``, ``.png``.

    Returns a deduplicated, ordered list of absolute paths.
    """
    if not image_patterns:
        return []

    images:list[str] = []
    ad_dir = os.path.dirname(ad_file)
    for image_pattern in image_patterns:
        pattern_images = set()
        for image_file in glob.glob(
            image_pattern,
            root_dir = ad_dir,
            flags = glob.GLOBSTAR | glob.BRACE | glob.EXTGLOB,
        ):
            image_file_ext = os.path.splitext(image_file)[1]
            ensure(
                image_file_ext.lower() in {".gif", ".jpg", ".jpeg", ".png"},
                f"Unsupported image file type [{image_file}]",
            )
            if os.path.isabs(image_file):
                pattern_images.add(image_file)
            else:
                pattern_images.add(abspath(image_file, relative_to = ad_file))
        images.extend(sorted(pattern_images))

    ensure(
        images or not image_patterns,
        f"No images found for given file patterns {image_patterns} at {ad_dir}",
    )
    return list(dict.fromkeys(images))


# --------------------------------------------------------------------------- #
# Main ad loading orchestration
# --------------------------------------------------------------------------- #


def load_ads(  # noqa: PLR0915
    *,
    config_file_path:str,
    ad_file_patterns:list[str],
    ad_defaults:Any,
    categories:dict[str, str],
    ads_selector:str,
    command:str,
    ignore_inactive:bool = True,
    exclude_ads_with_id:bool = True,
) -> list[tuple[str, Ad, dict[str, Any]]]:
    """Load and validate all ad config files, optionally filtering inactive or already-published ads.

    This is the main orchestration function — it wires together file
    discovery, model validation, selector filtering, category resolution,
    and image globbing.  All inputs are explicit.

    Returns:
        list[tuple[str, Ad, dict[str, Any]]]:
        Tuples of ``(file_path, validated Ad model, original raw data)``.
    """
    LOG.info("Searching for ad config files...")
    ad_files = discover_ad_files(config_file_path, ad_file_patterns)
    LOG.info(" -> found %s", pluralize("ad config file", ad_files))
    if not ad_files:
        return []

    ids = []
    use_specific_ads = False
    # Preserve exact tokenization: split without stripping whitespace.
    selectors = ads_selector.split(",")

    if _download_selection.is_numeric_ids_selector(ads_selector):
        ids = [int(n) for n in ads_selector.split(",")]
        use_specific_ads = True
        LOG.info("Start fetch task for the ad(s) with id(s):")
        LOG.info(" | ".join([str(id_) for id_ in ids]))

    ads:list[tuple[str, Ad, dict[str, Any]]] = []
    for ad_file, ad_file_relative in sorted(ad_files.items()):
        ad_cfg_orig:dict[str, Any] = _dicts.load_dict(ad_file, "ad")
        ad_cfg:Ad = load_ad(ad_cfg_orig, ad_defaults)

        # Inactive check runs before numeric ID filtering — an inactive ad
        # with a matching numeric ID will still be skipped.
        if ignore_inactive and not ad_cfg.active:
            LOG.info(" -> SKIPPED: inactive ad [%s]", ad_file_relative)
            continue

        if use_specific_ads:
            if ad_cfg.id not in ids:
                LOG.info(" -> SKIPPED: ad [%s] is not in list of given ids.", ad_file_relative)
                continue
        else:
            # Check if ad should be included based on selectors
            should_include = False

            # Check for 'changed' selector
            if "changed" in selectors and check_ad_changed(ad_cfg, ad_cfg_orig, ad_file_relative):
                should_include = True
            elif "changed" in selectors and command == "update" and _price_reduction.is_auto_price_reduction_due(ad_cfg, ad_file_relative):
                # Only the "update" command considers pending price reductions
                # as a reason to include a "changed" ad.
                should_include = True

            # Check for 'new' selector
            if "new" in selectors and (not ad_cfg.id or not exclude_ads_with_id):
                should_include = True
            elif "new" in selectors and ad_cfg.id and exclude_ads_with_id:
                LOG.info(" -> SKIPPED: ad [%s] is not new. already has an id assigned.", ad_file_relative)

            # Check for 'due' selector
            if "due" in selectors:
                if check_ad_republication(ad_cfg, ad_file_relative):
                    should_include = True

            # Check for 'all' selector (always include)
            if "all" in selectors:
                should_include = True

            if not should_include:
                continue

        ensure(
            get_ad_description(ad_cfg, ad_defaults, with_affixes = False),
            _("-> property [description] not specified @ [%s]") % ad_file,
        )
        get_ad_description(ad_cfg, ad_defaults, with_affixes = True)  # validates complete description

        resolve_ad_category(ad_cfg, categories)

        if ad_cfg.images:
            ad_cfg.images = resolve_ad_images(ad_file, ad_cfg.images)

        LOG.info(" -> LOADED: ad [%s]", ad_file_relative)
        ads.append((ad_file, ad_cfg, ad_cfg_orig))

    LOG.info("Loaded %s", pluralize("ad", ads))
    return ads


# --------------------------------------------------------------------------- #
# Content hash updates
# --------------------------------------------------------------------------- #


def update_content_hashes(ads:list[tuple[str, Ad, dict[str, Any]]]) -> int:
    """Recompute and persist content hashes for every loaded ad.

    Returns the count of ads whose hash actually changed.
    """
    changed = 0

    for idx, (ad_file, ad_cfg, ad_cfg_orig) in enumerate(ads, start = 1):
        LOG.info("Processing %s/%s: '%s' from [%s]...", idx, len(ads), ad_cfg.title, ad_file)
        ad_cfg.update_content_hash()
        if ad_cfg.content_hash != ad_cfg_orig["content_hash"]:
            changed += 1
            ad_cfg_orig["content_hash"] = ad_cfg.content_hash
            _dicts.save_dict(ad_file, ad_cfg_orig)

    LOG.info("############################################")
    LOG.info("DONE: Updated [content_hash] in %s", pluralize("ad", changed))
    LOG.info("############################################")
    return changed
