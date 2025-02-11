"""
SPDX-FileCopyrightText: © Jens Bergmann and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

Utilities for handling ad description text processing.

This module provides functionality for handling description prefixes and suffixes
in ad configurations, supporting both legacy nested and new flattened formats.

Example:
    >>> config = {
    ...     "ad_defaults": {
    ...         "description_prefix": "Global Prefix",
    ...         "description": {"prefix": "Legacy Prefix"}
    ...     }
    ... }
    >>> get_description_affixes(config, prefix=True)
    'Global Prefix'
"""
from typing import Any
from . import dicts


def get_description_affixes(config: dict[str, Any], prefix: bool = True) -> str:
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
