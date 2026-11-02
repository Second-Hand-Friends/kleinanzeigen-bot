# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Import of online ad drafts (e.g. saved in the mobile app) as local draft files.

Drafts are often incomplete (no price, no title, photos only), so they are saved as
``*.yaml.draft`` files that the default ``ad_files`` patterns do not match. Once a draft
is complete, the user renames it to ``*.yaml`` and sets ``active: true`` to publish it.
"""

import asyncio
import json
import mimetypes
import shutil
import urllib.error as urllib_error
import urllib.request as urllib_request
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit

from . import resources
from .model.ad_model import OPTION_NAME_BY_CARRIER_CODE
from .utils import dicts, files, loggers, misc
from .utils.i18n import pluralize
from .utils.web_scraping_mixin import WebScrapingMixin

LOG:Final[loggers.Logger] = loggers.get_logger(__name__)

DRAFTS_DIR_NAME:Final[str] = "drafts"
DRAFT_FILE_SUFFIX:Final[str] = ".yaml.draft"
_STAGING_DIR_PREFIX:Final[str] = ".tmp-"
_AD_TYPES:Final[frozenset[str]] = frozenset({"OFFER", "WANTED"})
_PRICE_TYPES:Final[frozenset[str]] = frozenset({"FIXED", "NEGOTIABLE", "GIVE_AWAY", "NOT_APPLICABLE"})
# Same exclusions as for downloaded ads: shipping is modelled separately, seller badges are tracking data.
_IGNORED_ATTRIBUTES:Final[frozenset[str]] = frozenset({"versand_s", "seller_badges_s"})
_DRAFT_FILE_HEADER:Final[str] = (
    "# yaml-language-server: $schema=https://raw.githubusercontent.com/Second-Hand-Friends/kleinanzeigen-bot/refs/heads/main/schemas/ad.schema.json\n"
    "# Imported online draft. Complete it, rename the file to *.yaml and set 'active: true' to publish it."
)


def _parse_json_response(response:Any) -> Any:
    """Return the decoded JSON body of a ``web_request`` response, or None if it is not JSON."""
    content = response.get("content", "") if isinstance(response, dict) else None
    if isinstance(content, (bytes, bytearray)):
        content = bytes(content).decode("utf-8", errors = "replace")
    if not isinstance(content, str):
        return None
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return None


async def _fetch_user_id(web:WebScrapingMixin, root_url:str) -> int | None:
    """Read the numeric account id, which the drafts endpoint needs, from the manage-ads API."""
    payload = _parse_json_response(await web.web_request(f"{root_url}/m-meine-anzeigen-verwalten.json?sort=DEFAULT&pageNum=1"))
    user_id = dicts.safe_get(payload, "bizJsonModel", "quotaUsageAccounts", "userAccount", "userId") if isinstance(payload, dict) else None
    if str(user_id).isdigit():
        return int(str(user_id))
    return None


async def fetch_ad_drafts(web:WebScrapingMixin, root_url:str) -> list[dict[str, Any]] | None:
    """Fetch the online drafts of the logged-in account, or None if they could not be loaded."""
    user_id = await _fetch_user_id(web, root_url)
    if user_id is None:
        LOG.error("Could not determine the account id required to load online drafts.")
        return None

    payload = _parse_json_response(await web.web_request(f"{root_url}/users/{user_id}/ad-drafts.json"))
    drafts = payload.get("listOfAdDrafts") if isinstance(payload, dict) else None
    if not isinstance(drafts, list):
        LOG.error("Unexpected response when loading online drafts.")
        return None
    return [draft for draft in drafts if isinstance(draft, dict) and isinstance(draft.get("id"), str) and draft["id"].strip()]


def load_category_paths() -> set[str]:
    """Return all known numeric category paths (e.g. ``161/176/kaffee_espressomaschinen``)."""
    categories = dicts.load_dict_from_module(resources, "categories.yaml", "")
    return {str(path) for path in categories.values()}


def resolve_draft_category(category_id:Any, special_attributes:dict[str, str], category_paths:set[str]) -> str:
    """Turn the draft's leaf category id into the ``parent/child[/art]`` path used in ad files.

    Returns the bare id if no parent is known, and an empty string if the draft has no category.
    """
    leaf = str(category_id).strip() if category_id is not None else ""
    if not leaf.isdigit():
        return ""

    parent = next((segments[0] for segments in (path.split("/") for path in sorted(category_paths)) if len(segments) > 1 and segments[1] == leaf), None)
    if parent is None:
        return leaf

    category = f"{parent}/{leaf}"
    # Like downloaded ads, keep the sub category when it is a known path, so the matching attribute gets preselected.
    art = next((value for key, value in special_attributes.items() if key.rsplit(".", 1)[-1] == "art_s"), None)
    if art and f"{category}/{art}" in category_paths:
        return f"{category}/{art}"
    return category


def draft_to_ad_config(draft:dict[str, Any], category_paths:set[str], images:list[str]) -> dict[str, Any]:
    """Map an online draft to an (inactive and possibly incomplete) ad configuration."""
    attribute_map = draft.get("attributeMap")
    special_attributes = {
        str(key): str(value)
        for key, value in (attribute_map.items() if isinstance(attribute_map, dict) else [])
        if isinstance(value, (str, int, float)) and str(value) and str(key).rsplit(".", 1)[-1] not in _IGNORED_ATTRIBUTES
    }

    shipping_options:list[str] = []
    raw_shipping_options = draft.get("shippingOptions")
    for option in raw_shipping_options if isinstance(raw_shipping_options, list) else []:
        carrier_code = option.get("shippingOptionId") if isinstance(option, dict) else None
        if isinstance(carrier_code, str) and carrier_code in OPTION_NAME_BY_CARRIER_CODE:
            shipping_options.append(OPTION_NAME_BY_CARRIER_CODE[carrier_code])
        else:
            LOG.warning("Draft %s: skipping unknown shipping option %s.", draft["id"], carrier_code)

    ad_cfg:dict[str, Any] = {"active": False}
    if isinstance(draft.get("adType"), str) and draft["adType"] in _AD_TYPES:
        ad_cfg["type"] = draft["adType"]
    ad_cfg["title"] = str(draft.get("title") or "")
    ad_cfg["description"] = str(draft.get("description") or "")
    ad_cfg["category"] = resolve_draft_category(draft.get("categoryId"), special_attributes, category_paths)
    if special_attributes:
        ad_cfg["special_attributes"] = special_attributes
    if isinstance(draft.get("priceType"), str) and draft["priceType"] in _PRICE_TYPES:
        ad_cfg["price_type"] = draft["priceType"]
    if shipping_options:
        ad_cfg["shipping_type"] = "SHIPPING"
        ad_cfg["shipping_options"] = shipping_options
    # Direct purchase needs a predefined shipping option (see Ad._validate_sell_directly).
    ad_cfg["sell_directly"] = bool(draft.get("buyNow")) and bool(shipping_options)
    ad_cfg["images"] = images
    return ad_cfg


def _draft_image_urls(draft:dict[str, Any]) -> list[str]:
    images = draft.get("adImages")
    urls = (image.get("canonicalUrl") for image in images if isinstance(image, dict)) if isinstance(images, list) else ()
    return [url for url in urls if isinstance(url, str) and url.startswith("https://")]


class _ImageDownloadError(Exception):
    """A draft image could not be downloaded; the draft is not saved so the next run retries it."""


def _download_image_sync(url:str, directory:Path, filename_prefix:str, img_nr:int, timeout:float) -> str | None:
    """Download one draft image and return its file name, or None on failure."""
    try:
        with urllib_request.urlopen(url, timeout = timeout) as response:  # noqa: S310 only https URLs from the drafts API reach this
            file_ending = mimetypes.guess_extension(response.info().get_content_type()) or ""
            img_path = directory / f"{filename_prefix}{img_nr}{file_ending}"
            with open(img_path, "wb") as file:
                shutil.copyfileobj(response, file)
            return img_path.name
    except (urllib_error.URLError, ValueError, OSError) as ex:
        # Image URLs carry an access token in the query string: keep it out of the log.
        safe_url = urlsplit(url)._replace(query = "", fragment = "").geturl()
        LOG.warning("Failed to download image %s: %s", safe_url, str(ex).replace(url, safe_url))
        return None


async def _save_draft(draft:dict[str, Any], final_dir:Path, file_stem:str, category_paths:set[str], image_timeout:float) -> None:
    """Download the draft images and write the draft file via a staging directory."""
    staging_dir = final_dir.with_name(f"{_STAGING_DIR_PREFIX}{final_dir.name}")
    await asyncio.to_thread(lambda: shutil.rmtree(staging_dir, ignore_errors = True))
    await asyncio.to_thread(lambda: staging_dir.mkdir(parents = True))
    try:
        images:list[str] = []
        for img_nr, url in enumerate(_draft_image_urls(draft), start = 1):
            image = await asyncio.to_thread(_download_image_sync, url, staging_dir, f"{file_stem}__img", img_nr, image_timeout)
            if not image:
                # Saving the draft without the photo would make later runs skip it for good.
                raise _ImageDownloadError
            images.append(image)

        ad_cfg = draft_to_ad_config(draft, category_paths, images)
        await asyncio.to_thread(lambda: dicts.save_dict(staging_dir / f"{file_stem}{DRAFT_FILE_SUFFIX}", ad_cfg, header = _DRAFT_FILE_HEADER))
        await asyncio.to_thread(staging_dir.rename, final_dir)
        LOG.info("Saved draft '%s' with %s to: %s", ad_cfg["title"], pluralize("image", images), final_dir)
    except Exception:  # noqa: BLE001
        # Broad catch is intentional: remove the half-written staging directory, then re-raise.
        await asyncio.to_thread(lambda: shutil.rmtree(staging_dir, ignore_errors = True))
        raise


async def download_ad_drafts(web:WebScrapingMixin, drafts_dir:Path, root_url:str) -> None:
    """Save all online drafts that were not imported yet below *drafts_dir*."""
    LOG.info("Loading online drafts...")
    try:
        drafts = await fetch_ad_drafts(web, root_url)
    except (AssertionError, TimeoutError) as ex:  # web_request raises AssertionError for unexpected HTTP status codes
        LOG.error("Loading online drafts failed: %s", ex)
        return
    if drafts is None:
        return
    LOG.info("Online drafts found: %d", len(drafts))

    category_paths = load_category_paths()
    image_timeout = web.timeout("page_load")
    saved_count = 0
    for draft in drafts:
        draft_id = misc.sanitize_folder_name(draft["id"])
        final_dir = drafts_dir / draft_id
        if await files.exists(final_dir):
            # Never overwrite: the user may already be completing this draft locally.
            LOG.info("Draft %s was already imported to %s. Skipping it to keep local changes.", draft_id, final_dir)
            continue
        try:
            await _save_draft(draft, final_dir, f"ad_{draft_id}", category_paths, image_timeout)
        except _ImageDownloadError:
            LOG.warning("Draft %s was not saved because an image could not be downloaded. It will be retried on the next run.", draft_id)
            continue
        except Exception:  # noqa: BLE001 - one bad draft must not abort the batch
            LOG.exception("Failed to save draft %s. Skipping it.", draft_id)
            continue
        saved_count += 1

    LOG.info("DONE: saved %d new drafts. Complete them, rename them to *.yaml and set 'active: true' to publish them.", saved_count)
