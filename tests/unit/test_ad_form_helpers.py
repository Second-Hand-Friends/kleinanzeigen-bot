# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

"""Tests for ad_form_helpers — condition, XPath, marker, and constant helpers."""

from __future__ import annotations

import pytest

from kleinanzeigen_bot.ad_form_helpers import (
    CONDITION_GERMAN_TO_API,
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
        ("new", "new", None),
        ("like_new", "like_new", None),
        ("ok", "ok", None),
        ("alright", "alright", None),
        ("defect", "defect", None),
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


def test_xpath_literal_multiple_single_quotes() -> None:
    result = xpath_literal("a'b'c")
    # No double quotes, so double-quote wrapping works fine
    assert result == '"a\'b\'c"'


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


def test_get_marker_value_none_attrs() -> None:
    marker = _FakeMarker(None)
    assert not get_marker_value(marker)


def test_get_marker_value_object_attrs() -> None:
    marker = _FakeMarker(_FakeAttrs("bar"))
    assert get_marker_value(marker) == "bar"


# ---------------------------------------------------------------------------
# SPECIAL_ATTRIBUTE_TOKEN_RE
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "token",
    [
        "condition",
        "shipping_options",
        "abc123",
        "ABC",
        "a",
        "Z",
        "0",
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


def test_wanted_shipping_labels() -> None:
    assert WANTED_SHIPPING_LABELS["SHIPPING"] == "Versand möglich"
    assert WANTED_SHIPPING_LABELS["PICKUP"] == "Nur Abholung"


# ---------------------------------------------------------------------------
# CONDITION_GERMAN_TO_API
# ---------------------------------------------------------------------------

def test_condition_german_to_api_exact_dict() -> None:
    assert CONDITION_GERMAN_TO_API == {
        "neu": "new",
        "wie_neu": "like_new",
        "sehr_gut": "like_new",
        "gut": "ok",
        "in_ordnung": "alright",
        "defekt": "defect",
    }


def test_condition_german_to_api_contains_expected_keys() -> None:
    assert "neu" in CONDITION_GERMAN_TO_API
    assert "wie_neu" in CONDITION_GERMAN_TO_API
    assert "sehr_gut" in CONDITION_GERMAN_TO_API
    assert "gut" in CONDITION_GERMAN_TO_API
    assert "in_ordnung" in CONDITION_GERMAN_TO_API
    assert "defekt" in CONDITION_GERMAN_TO_API


def test_condition_german_to_api_all_values_valid() -> None:
    """All mapped API values must be in the known set."""
    valid_api = {"new", "like_new", "ok", "alright", "defect"}
    assert set(CONDITION_GERMAN_TO_API.values()).issubset(valid_api)
