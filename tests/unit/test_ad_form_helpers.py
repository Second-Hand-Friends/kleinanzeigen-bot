# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

"""Tests for ad_form_helpers — condition, XPath, marker, and constant helpers."""

from __future__ import annotations

import pytest

from kleinanzeigen_bot.ad_form_helpers import (
    SPECIAL_ATTRIBUTE_TOKEN_RE,
    WANTED_SHIPPING_LABELS,
    get_marker_value,
    get_marker_value_from_attrs,
    normalize_condition,
    xpath_literal,
)

# ---------------------------------------------------------------------------
# normalize_condition
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("input_value", "expected_canonical", "expected_legacy"),
    [
        ("neu", "new", "neu"),
        ("wie_neu", "like_new", "wie_neu"),
        ("sehr_gut", "like_new", "sehr_gut"),
        ("gut", "ok", "gut"),
        ("in_ordnung", "alright", "in_ordnung"),
        ("defekt", "defect", "defekt"),
        ("unknown_value", "unknown_value", None),
    ],
)
def test_normalize_condition(input_value:str, expected_canonical:str, expected_legacy:str | None) -> None:
    canonical, legacy = normalize_condition(input_value)
    assert canonical == expected_canonical
    assert legacy == expected_legacy


# ---------------------------------------------------------------------------
# xpath_literal
# ---------------------------------------------------------------------------

def test_xpath_literal_no_quotes() -> None:
    assert xpath_literal("hello") == "'hello'"


def test_xpath_literal_single_quote() -> None:
    assert xpath_literal("it's") == '"it\'s"'


def test_xpath_literal_double_quote() -> None:
    assert xpath_literal('say "hi"') == "'say \"hi\"'"


def test_xpath_literal_both_quotes() -> None:
    result = xpath_literal("Bob's \"Bike\"")
    # concat('Bob', "'", 's "Bike"')
    assert result == "concat('Bob', \"'\", 's \"Bike\"')"


# ---------------------------------------------------------------------------
# get_marker_value / get_marker_value_from_attrs
# ---------------------------------------------------------------------------

class _FakeMarker:
    def __init__(self, attrs:object) -> None:
        self.attrs = attrs


class _FakeAttrs:
    def __init__(self, value:str) -> None:
        self.value = value


def test_get_marker_value_from_dict_attrs() -> None:
    assert get_marker_value_from_attrs({"value": "  abc "}) == "abc"


def test_get_marker_value_from_dict_attrs_missing() -> None:
    assert not get_marker_value_from_attrs({"other": 1})


def test_get_marker_value_from_object_attrs() -> None:
    assert get_marker_value_from_attrs(_FakeAttrs("  xyz  ")) == "xyz"


def test_get_marker_value_from_attrs_none() -> None:
    assert not get_marker_value_from_attrs(None)


def test_get_marker_value_via_object() -> None:
    marker = _FakeMarker({"value": "  foo  "})
    assert get_marker_value(marker) == "foo"


# ---------------------------------------------------------------------------
# SPECIAL_ATTRIBUTE_TOKEN_RE
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "token",
    [
        "condition",
        "abc123",
        "ABC",
        "key1_name",
    ],
)
def test_special_attribute_token_valid(token:str) -> None:
    assert SPECIAL_ATTRIBUTE_TOKEN_RE.fullmatch(token) is not None


@pytest.mark.parametrize(
    "token",
    [
        "my-key",
        "has space",
        "key.name",
        "über",
        "",
        "with/slash",
    ],
)
def test_special_attribute_token_invalid(token:str) -> None:
    assert SPECIAL_ATTRIBUTE_TOKEN_RE.fullmatch(token) is None


# ---------------------------------------------------------------------------
# WANTED_SHIPPING_LABELS
# ---------------------------------------------------------------------------

def test_wanted_shipping_labels_exact_dict() -> None:
    assert WANTED_SHIPPING_LABELS == {
        "SHIPPING": "Versand möglich",
        "PICKUP": "Nur Abholung",
    }


# ---------------------------------------------------------------------------
# CONDITION_GERMAN_TO_API
# ---------------------------------------------------------------------------
