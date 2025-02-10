"""
SPDX-FileCopyrightText: © Jens Bergman and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""
import json, os, hashlib
from typing import Any


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
