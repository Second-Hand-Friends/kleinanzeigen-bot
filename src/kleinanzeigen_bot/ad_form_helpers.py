# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

"""Browser-independent form helpers: condition, XPath, marker, shipping labels."""

import re
from collections.abc import Mapping
from typing import Any, Final

from kleinanzeigen_bot.model.ad_model import validate_condition_api_mapping

__all__ = [
    "CONDITION_GERMAN_TO_API",
    "SPECIAL_ATTRIBUTE_TOKEN_RE",
    "VERSAND_COMBOBOX_SELECTOR",
    "WANTED_SHIPPING_LABELS",
    "get_marker_value",
    "get_marker_value_from_attrs",
    "location_matches_target",
    "normalize_condition",
    "xpath_literal",
]

SPECIAL_ATTRIBUTE_TOKEN_RE:Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_]+$")

VERSAND_COMBOBOX_SELECTOR:Final[str] = (
    'button[role="combobox"][id="versand"], '
    'button[role="combobox"][id$=".versand"], '
    'button[role="combobox"][aria-labelledby$="versand-selected-option"]'
)

WANTED_SHIPPING_LABELS:Final[dict[str, str]] = {
    "SHIPPING": "Versand möglich",
    "PICKUP": "Nur Abholung",
}

CONDITION_GERMAN_TO_API:Final[dict[str, str]] = {
    "neu": "new",
    "wie_neu": "like_new",
    "sehr_gut": "like_new",  # legacy "very good" tier collapses to like_new
    "gut": "ok",
    "in_ordnung": "alright",
    "defekt": "defect",
}
validate_condition_api_mapping("CONDITION_GERMAN_TO_API", CONDITION_GERMAN_TO_API)


def normalize_condition(condition_value:str) -> tuple[str, str | None]:
    """Return the normalized condition value and legacy input.

    Returns ``(canonical_value, legacy_value)`` where ``canonical_value`` is the
    API form from ``CONDITION_GERMAN_TO_API`` and ``legacy_value`` is the original
    input only when a mapping was applied, otherwise ``None``. Unmapped inputs are
    returned unchanged. Example: ``neu`` -> ``("new", "neu")``; ``new`` ->
    ``("new", None)``.
    """
    canonical_value = CONDITION_GERMAN_TO_API.get(condition_value, condition_value)
    if canonical_value != condition_value:
        return canonical_value, condition_value
    return condition_value, None


def location_matches_target(target:str, candidate:str | None) -> bool:
    """Check if a city candidate matches the target location.

    Returns ``True`` if the candidate (as displayed in a city combobox) matches
    the given target location.  Handles zip-code prefixes (``"10115 - Berlin"``),
    whitespace normalization, and case folding.

    Args:
        target: The expected location string (e.g. ``"Berlin"`` or ``"10115 - Berlin"``).
        candidate: The candidate string from a city combobox option, or ``None``.

    Returns:
        ``True`` if the candidate matches the target.
    """
    if not candidate:
        return False

    normalized_target = " ".join(target.split()).casefold()
    normalized_candidate = " ".join(candidate.split()).casefold()
    if not normalized_target or not normalized_candidate:
        return False

    if normalized_target == normalized_candidate:
        return True

    if " - " in normalized_target:
        return False

    if normalized_candidate.startswith(f"{normalized_target} - "):
        return True

    candidate_city = normalized_candidate.rsplit(" - ", maxsplit = 1)[-1]
    return normalized_target == candidate_city


def xpath_literal(value:str) -> str:
    """Return an XPath-safe string literal for *value*.

    Strategy:
    - no single quotes -> wrap in single quotes
    - no double quotes -> wrap in double quotes
    - contains both -> use concat('part1', "'", 'part2', ...)

    Example:
    - value = Bob's "Bike" -> concat('Bob', "'", 's "Bike"')

    This avoids quote-escaping issues in dynamic XPath expressions.
    """
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    return "concat(" + ', "\'", '.join(f"'{part}'" for part in value.split("'")) + ")"


def get_marker_value(marker:object) -> str:
    """Extract and normalize a hidden image marker value from an object with an ``attrs`` attribute.

    The *marker* can be any object with ``.attrs`` (a ``Mapping`` or an object
    with a ``value`` attribute).  This avoids depending on browser element types.
    """
    attrs = getattr(marker, "attrs", None)
    return get_marker_value_from_attrs(attrs)


def get_marker_value_from_attrs(attrs:Mapping[str, Any] | object | None) -> str:
    """Extract and normalize a hidden image marker value from an ``attrs`` object.

    Handles dict-like ``Mapping`` (via ``.get``) and object-like ``attrs``
    (via ``.value`` attribute).
    """
    if attrs is None:
        return ""
    raw_value = attrs.get("value", "") if isinstance(attrs, Mapping) else getattr(attrs, "value", "")
    return str(raw_value or "").strip()
