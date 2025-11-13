# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
from __future__ import annotations

import math

import pytest

from kleinanzeigen_bot.model.ad_model import Ad, AdPartial
from kleinanzeigen_bot.model.config_model import AdDefaults


def test_update_content_hash() -> None:
    minimal_ad_cfg = {
        "id": "123456789",
        "title": "Test Ad Title",
        "category": "160",
        "description": "Test Description",
    }
    minimal_ad_cfg_hash = "ae3defaccd6b41f379eb8de17263caa1bd306e35e74b11aa03a4738621e96ece"

    assert AdPartial.model_validate(minimal_ad_cfg).update_content_hash().content_hash == minimal_ad_cfg_hash

    assert AdPartial.model_validate(minimal_ad_cfg | {
        "id": "123456789",
        "created_on": "2025-05-08T09:34:03",
        "updated_on": "2025-05-14T20:43:16",
        "content_hash": "5753ead7cf42b0ace5fe658ecb930b3a8f57ef49bd52b7ea2d64b91b2c75517e"
    }).update_content_hash().content_hash == minimal_ad_cfg_hash

    assert AdPartial.model_validate(minimal_ad_cfg | {
        "active": None,
        "images": None,
        "shipping_options": None,
        "special_attributes": None,
        "contact": None,
    }).update_content_hash().content_hash == minimal_ad_cfg_hash

    assert AdPartial.model_validate(minimal_ad_cfg | {
        "active": True,
        "images": [],
        "shipping_options": [],
        "special_attributes": {},
        "contact": {},
    }).update_content_hash().content_hash != minimal_ad_cfg_hash


def test_repost_count_does_not_influence_content_hash() -> None:
    base_ad_cfg = {
        "id": "123456789",
        "title": "Test Ad Title",
        "category": "160",
        "description": "Test Description",
        "price_type": "NEGOTIABLE",
    }

    hash_without_reposts = AdPartial.model_validate(base_ad_cfg | {"repost_count": 0}).update_content_hash().content_hash
    hash_with_reposts = AdPartial.model_validate(base_ad_cfg | {"repost_count": 5}).update_content_hash().content_hash
    assert hash_without_reposts == hash_with_reposts


def test_shipping_costs() -> None:
    minimal_ad_cfg = {
        "id": "123456789",
        "title": "Test Ad Title",
        "category": "160",
        "description": "Test Description",
    }

    def is_close(a:float | None, b:float) -> bool:
        return a is not None and math.isclose(a, b, rel_tol = 1e-09, abs_tol = 1e-09)

    assert AdPartial.model_validate(minimal_ad_cfg | {"shipping_costs": 0}).shipping_costs == 0
    assert is_close(AdPartial.model_validate(minimal_ad_cfg | {"shipping_costs": 0.00}).shipping_costs, 0)
    assert is_close(AdPartial.model_validate(minimal_ad_cfg | {"shipping_costs": 0.10}).shipping_costs, 0.10)
    assert is_close(AdPartial.model_validate(minimal_ad_cfg | {"shipping_costs": 1.00}).shipping_costs, 1)
    assert AdPartial.model_validate(minimal_ad_cfg | {"shipping_costs": ""}).shipping_costs is None
    assert AdPartial.model_validate(minimal_ad_cfg | {"shipping_costs": " "}).shipping_costs is None
    assert AdPartial.model_validate(minimal_ad_cfg | {"shipping_costs": None}).shipping_costs is None
    assert AdPartial.model_validate(minimal_ad_cfg).shipping_costs is None


def _base_ad_cfg() -> dict[str, object]:
    return {
        "title": "Test Ad Title",
        "category": "160",
        "description": "Test Description",
        "price_type": "NEGOTIABLE",
        "contact": {"name": "Test User", "zipcode": "12345"},
        "shipping_type": "PICKUP",
        "sell_directly": False,
        "type": "OFFER",
        "active": True
    }


def _complete_ad_cfg() -> dict[str, object]:
    return _base_ad_cfg() | {
        "republication_interval": 7,
        "price": 100,
        "auto_reduce_price": True,
        "price_reduction": {"type": "FIXED", "value": 5},
        "min_price": 50
    }


def test_auto_reduce_requires_price() -> None:
    cfg = _base_ad_cfg() | {
        "auto_reduce_price": True,
        "price_reduction": {"type": "FIXED", "value": 5},
        "min_price": 50
    }
    with pytest.raises(ValueError, match = "price must be specified"):
        AdPartial.model_validate(cfg).to_ad(AdDefaults())


def test_auto_reduce_requires_price_reduction() -> None:
    cfg = _base_ad_cfg() | {
        "auto_reduce_price": True,
        "price": 100,
        "min_price": 50
    }
    with pytest.raises(ValueError, match = "price_reduction must be specified"):
        AdPartial.model_validate(cfg).to_ad(AdDefaults())


def test_prepare_ad_model_fills_missing_counters() -> None:
    cfg = _base_ad_cfg() | {
        "price": 120,
        "shipping_type": "SHIPPING",
        "sell_directly": False
    }
    ad = AdPartial.model_validate(cfg).to_ad(AdDefaults())

    assert ad.price_reduction_delay_reposts == 0
    assert ad.price_reduction_delay_days == 0
    assert ad.price_reduction_count == 0
    assert ad.repost_count == 0


def test_ad_model_auto_reduce_validator_rejects_missing_price() -> None:
    cfg = _complete_ad_cfg() | {"price": None}
    with pytest.raises(ValueError, match = "price must be specified"):
        Ad.model_validate(cfg)


def test_ad_model_auto_reduce_validator_rejects_missing_price_reduction() -> None:
    cfg = _complete_ad_cfg() | {"price_reduction": None}
    with pytest.raises(ValueError, match = "price_reduction must be specified"):
        Ad.model_validate(cfg)


def test_ad_model_auto_reduce_validator_rejects_missing_min_price() -> None:
    cfg = _complete_ad_cfg() | {"min_price": None}
    with pytest.raises(ValueError, match = "min_price must be specified"):
        Ad.model_validate(cfg)


def test_ad_model_auto_reduce_validator_rejects_min_price_above_price() -> None:
    cfg = _complete_ad_cfg() | {"min_price": 150, "price": 100}
    with pytest.raises(ValueError, match = "min_price must not exceed price"):
        Ad.model_validate(cfg)


def test_auto_reduce_rejects_null_price_reduction() -> None:
    cfg = _base_ad_cfg() | {
        "auto_reduce_price": True,
        "price": 100,
        "price_reduction": None,
        "min_price": 50
    }
    with pytest.raises(ValueError, match = "price_reduction must be specified"):
        AdPartial.model_validate(cfg).to_ad(AdDefaults())


def test_min_price_must_not_exceed_price() -> None:
    cfg = _base_ad_cfg() | {
        "auto_reduce_price": True,
        "price": 100,
        "min_price": 120,
        "price_reduction": {"type": "FIXED", "value": 5}
    }
    with pytest.raises(ValueError, match = "min_price must not exceed price"):
        AdPartial.model_validate(cfg)


def test_auto_reduce_requires_min_price() -> None:
    cfg = _base_ad_cfg() | {
        "auto_reduce_price": True,
        "price": 100,
        "price_reduction": {"type": "FIXED", "value": 5}
    }
    with pytest.raises(ValueError, match = "min_price must be specified"):
        AdPartial.model_validate(cfg).to_ad(AdDefaults())


def test_min_price_without_auto_reduce_must_not_exceed_price() -> None:
    cfg = _base_ad_cfg() | {
        "price": 100,
        "min_price": 150,
        "auto_reduce_price": False
    }
    with pytest.raises(ValueError, match = "min_price must not exceed price"):
        AdPartial.model_validate(cfg)


def test_ad_model_auto_reduce_requires_price() -> None:
    cfg = _complete_ad_cfg() | {"price": None}
    with pytest.raises(ValueError, match = "price must be specified"):
        Ad.model_validate(cfg)


def test_ad_model_auto_reduce_requires_price_reduction() -> None:
    cfg = _complete_ad_cfg() | {"price_reduction": None}
    with pytest.raises(ValueError, match = "price_reduction must be specified"):
        Ad.model_validate(cfg)


def test_ad_model_auto_reduce_requires_min_price() -> None:
    cfg = _complete_ad_cfg() | {"min_price": None}
    with pytest.raises(ValueError, match = "min_price must be specified"):
        Ad.model_validate(cfg)


def test_ad_model_min_price_must_not_exceed_price() -> None:
    cfg = _complete_ad_cfg() | {"min_price": 150, "price": 100}
    with pytest.raises(ValueError, match = "min_price must not exceed price"):
        Ad.model_validate(cfg)
