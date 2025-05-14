# SPDX-FileCopyrightText: © Jens Bergman and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import hashlib, json, os  # isort: skip
from typing import Any

from .model.config_model import Config
from .utils.misc import get_attr


def calculate_content_hash(ad_cfg:dict[str, Any]) -> str:
    """Calculate a hash for user-modifiable fields of the ad."""

    # Relevant fields for the hash
    content = {
        "active": bool(get_attr(ad_cfg, "active", default = True)),  # Explicitly convert to bool
        "type": str(get_attr(ad_cfg, "type", "")),  # Explicitly convert to string
        "title": str(get_attr(ad_cfg, "title", "")),
        "description": str(get_attr(ad_cfg, "description", "")),
        "category": str(get_attr(ad_cfg, "category", "")),
        "price": str(get_attr(ad_cfg, "price", "")),  # Price always as string
        "price_type": str(get_attr(ad_cfg, "price_type", "")),
        "special_attributes": dict(get_attr(ad_cfg, "special_attributes", {})),  # Handle None case
        "shipping_type": str(get_attr(ad_cfg, "shipping_type", "")),
        "shipping_costs": str(get_attr(ad_cfg, "shipping_costs", "")),
        "shipping_options": sorted([str(x) for x in get_attr(ad_cfg, "shipping_options", [])]),  # Handle None case
        "sell_directly": bool(get_attr(ad_cfg, "sell_directly", default = False)),  # Explicitly convert to bool
        "images": sorted([os.path.basename(str(img)) if img is not None else "" for img in get_attr(ad_cfg, "images", [])]),  # Handle None values in images
        "contact": {
            "name": str(get_attr(ad_cfg, "contact.name", "")),
            "street": str(get_attr(ad_cfg, "contact.street", "")),  # Changed from "None" to empty string for consistency
            "zipcode": str(get_attr(ad_cfg, "contact.zipcode", "")),
            "phone": str(get_attr(ad_cfg, "contact.phone", ""))
        }
    }

    # Create sorted JSON string for consistent hashes
    content_str = json.dumps(content, sort_keys = True)
    return hashlib.sha256(content_str.encode()).hexdigest()


def get_description_affixes(config:Config, *, prefix:bool = True) -> str:
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
        >>> get_description_affixes(Config.model_validate(config), prefix=True)
        'Hello'
    """
    affix_type = "prefix" if prefix else "suffix"

    # First try new flattened format (description_prefix/description_suffix)
    flattened_key = f"description_{affix_type}"
    flattened_value = getattr(config.ad_defaults, flattened_key)
    if isinstance(flattened_value, str):
        return flattened_value

    # Then try legacy nested format (description.prefix/description.suffix)
    if config.ad_defaults.description:
        nested_value = getattr(config.ad_defaults.description, affix_type)
        if isinstance(nested_value, str):
            return nested_value

    return ""
