# SPDX-FileCopyrightText: © Jens Bergman and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import hashlib, json, os  # isort: skip
from typing import Any, Final

from .utils import dicts

MAX_DESCRIPTION_LENGTH:Final[int] = 4000


def calculate_content_hash(ad_cfg: dict[str, Any]) -> str:
    """Calculate a hash for user-modifiable fields of the ad."""

    # Relevant fields for the hash
    content = {
        "active": bool(ad_cfg.get("active", True)),  # Explicitly convert to bool
        "type": str(ad_cfg.get("type", "")),  # Explicitly convert to string
        "title": str(ad_cfg.get("title", "")),
        "description": str(ad_cfg.get("description", "")),
        "category": str(ad_cfg.get("category", "")),
        "price": str(ad_cfg.get("price", "")),  # Price always as string
        "price_type": str(ad_cfg.get("price_type", "")),
        "special_attributes": dict(ad_cfg.get("special_attributes") or {}),  # Handle None case
        "shipping_type": str(ad_cfg.get("shipping_type", "")),
        "shipping_costs": str(ad_cfg.get("shipping_costs", "")),
        "shipping_options": sorted([str(x) for x in (ad_cfg.get("shipping_options") or [])]),  # Handle None case
        "sell_directly": bool(ad_cfg.get("sell_directly", False)),  # Explicitly convert to bool
        "images": sorted([os.path.basename(str(img)) if img is not None else "" for img in (ad_cfg.get("images") or [])]),  # Handle None values in images
        "contact": {
            "name": str(ad_cfg.get("contact", {}).get("name", "")),
            "street": str(ad_cfg.get("contact", {}).get("street", "")),  # Changed from "None" to empty string for consistency
            "zipcode": str(ad_cfg.get("contact", {}).get("zipcode", "")),
            "phone": str(ad_cfg.get("contact", {}).get("phone", ""))
        }
    }

    # Create sorted JSON string for consistent hashes
    content_str = json.dumps(content, sort_keys = True)
    return hashlib.sha256(content_str.encode()).hexdigest()


def get_description_affixes(config: dict[str, Any], *, prefix: bool = True) -> str:
    """Get prefix or suffix for description with proper precedence.

    This function handles both the new flattened format and legacy nested format:

    New format (flattened):
        ad_defaults:
            description_prefix: "Global Prefix"
            description_suffix: "Global Suffix"

    Legacy format (nested):
        ad_defaults:
            description:
                prefix: "Legacy Prefix"
                suffix: "Legacy Suffix"

    Args:
        config: Configuration dictionary containing ad_defaults
        prefix: If True, get prefix, otherwise get suffix

    Returns:
        The appropriate affix string, empty string if none found

    Example:
        >>> config = {"ad_defaults": {"description_prefix": "Hello", "description": {"prefix": "Hi"}}}
        >>> get_description_affixes(config, prefix=True)
        'Hello'
    """
    # Handle edge cases
    if not isinstance(config, dict):
        return ""

    affix_type = "prefix" if prefix else "suffix"

    # First try new flattened format (description_prefix/description_suffix)
    flattened_key = f"description_{affix_type}"
    flattened_value = dicts.safe_get(config, "ad_defaults", flattened_key)
    if isinstance(flattened_value, str):
        return flattened_value

    # Then try legacy nested format (description.prefix/description.suffix)
    nested_value = dicts.safe_get(config, "ad_defaults", "description", affix_type)
    if isinstance(nested_value, str):
        return nested_value

    return ""
