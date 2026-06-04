# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

"""Tests for ad_description: description composition with prefix/suffix affixes."""

from __future__ import annotations

import pytest

from kleinanzeigen_bot.ad_description import get_ad_description
from kleinanzeigen_bot.model.ad_model import Ad
from kleinanzeigen_bot.model.config_model import AdDefaults, Config


def _make_ad(**kwargs:object) -> Ad:
    defaults = {
        "title": "0123456789",
        "category": "whatever",
        "active": True,
        "type": "OFFER",
        "price_type": "FIXED",
        "price": 100,
        "shipping_type": "SHIPPING",
        "sell_directly": False,
        "contact": {"name": "Test", "zipcode": "12345"},
        "republication_interval": 7,
    }
    return Ad.model_validate(defaults | kwargs)


def _make_defaults(config_overrides:dict[str, object] | None = None) -> AdDefaults:
    cfg = Config.model_validate(config_overrides or {})
    return cfg.ad_defaults


def test_without_affixes() -> None:
    """@ is not replaced when ``with_affixes=False``."""
    ad = _make_ad(description = "Hello @world")
    result = get_ad_description(ad, _make_defaults(), with_affixes = False)
    assert result == "Hello @world"


def test_with_config_prefix_suffix() -> None:
    defaults = _make_defaults(
        {"ad_defaults": {"description_prefix": "P_", "description_suffix": "_S"}}
    )
    ad = _make_ad(description = "desc")
    result = get_ad_description(ad, defaults, with_affixes = True)
    assert result == "P_desc_S"


def test_ad_level_affixes_take_precedence() -> None:
    defaults = _make_defaults(
        {"ad_defaults": {"description_prefix": "ConfigP_", "description_suffix": "_ConfigS"}}
    )
    ad = _make_ad(
        description = "desc",
        description_prefix = "AdP_",
        description_suffix = "_AdS",
    )
    result = get_ad_description(ad, defaults, with_affixes = True)
    assert result == "AdP_desc_AdS"


def test_none_affixes_fallback_to_empty() -> None:
    defaults = _make_defaults(
        {"ad_defaults": {"description_prefix": None, "description_suffix": None}}
    )
    ad = _make_ad(description = "desc")
    result = get_ad_description(ad, defaults, with_affixes = True)
    assert result == "desc"


def test_ad_level_none_falls_back_to_config() -> None:
    defaults = _make_defaults(
        {"ad_defaults": {"description_prefix": "CP_", "description_suffix": "_CS"}}
    )
    ad = _make_ad(
        description = "desc",
        description_prefix = None,
        description_suffix = None,
    )
    result = get_ad_description(ad, defaults, with_affixes = True)
    assert result == "CP_desc_CS"


def test_at_sign_replacement() -> None:
    defaults = _make_defaults()
    ad = _make_ad(description = "test@example.com")
    result = get_ad_description(ad, defaults, with_affixes = True)
    assert result == "test(at)example.com"


def test_at_sign_replaced_in_prefix_and_suffix_too() -> None:
    defaults = _make_defaults(
        {"ad_defaults": {"description_prefix": "pre@", "description_suffix": "@suf"}}
    )
    ad = _make_ad(description = "desc")
    result = get_ad_description(ad, defaults, with_affixes = True)
    assert result == "pre(at)desc(at)suf"


def test_length_validation_raises() -> None:
    defaults = _make_defaults(
        {"ad_defaults": {"description_prefix": "P" * 1000, "description_suffix": "S" * 1000}}
    )
    ad = _make_ad(description = "D" * 2001)
    with pytest.raises(AssertionError, match = r"Length of ad description .* exceeds 4000 chars"):
        get_ad_description(ad, defaults, with_affixes = True)
