# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
from __future__ import annotations

import math
from typing import Any

import pytest

from kleinanzeigen_bot.model.ad_model import MAX_DESCRIPTION_LENGTH, Ad, AdPartial, Contact, ShippingOption
from kleinanzeigen_bot.model.config_model import AdDefaults, AutoPriceReductionConfig
from kleinanzeigen_bot.utils.pydantics import ContextualModel, ContextualValidationError


@pytest.mark.unit
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


@pytest.mark.unit
def test_price_reduction_count_does_not_influence_content_hash() -> None:
    base_ad_cfg = {
        "id": "123456789",
        "title": "Test Ad Title",
        "category": "160",
        "description": "Test Description",
        "price_type": "NEGOTIABLE",
    }

    hash_without_reposts = AdPartial.model_validate(base_ad_cfg | {"price_reduction_count": 0}).update_content_hash().content_hash
    hash_with_reposts = AdPartial.model_validate(base_ad_cfg | {"price_reduction_count": 5}).update_content_hash().content_hash
    assert hash_without_reposts == hash_with_reposts


@pytest.mark.unit
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


@pytest.mark.unit
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


class ShippingOptionWrapper(ContextualModel):
    option:ShippingOption


@pytest.mark.unit
def test_shipping_option_must_not_be_blank() -> None:
    with pytest.raises(ValueError, match = "must be non-empty and non-blank"):
        ShippingOptionWrapper.model_validate({"option": " "})


@pytest.mark.unit
def test_description_length_limit() -> None:
    cfg = {
        "title": "Description Length",
        "category": "160",
        "description": "x" * (MAX_DESCRIPTION_LENGTH + 1)
    }

    with pytest.raises(ValueError, match = f"description length exceeds {MAX_DESCRIPTION_LENGTH} characters"):
        AdPartial.model_validate(cfg)


@pytest.fixture
def base_ad_cfg() -> dict[str, object]:
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


@pytest.fixture
def complete_ad_cfg(base_ad_cfg:dict[str, object]) -> dict[str, object]:
    return base_ad_cfg | {
        "republication_interval": 7,
        "price": 100,
        "auto_price_reduction": {
            "enabled": True,
            "strategy": "FIXED",
            "amount": 5,
            "min_price": 50,
            "delay_reposts": 0,
            "delay_days": 0
        }
    }


class SparseDumpAdPartial(AdPartial):
    def model_dump(self, *args:Any, **kwargs:Any) -> dict[str, object]:  # noqa: ANN401
        data = super().model_dump(*args, **kwargs)
        data.pop("price_reduction_count", None)
        data.pop("repost_count", None)
        return data


@pytest.mark.unit
def test_auto_reduce_requires_price(base_ad_cfg:dict[str, object]) -> None:
    cfg = base_ad_cfg.copy() | {
        "auto_price_reduction": {
            "enabled": True,
            "strategy": "FIXED",
            "amount": 5,
            "min_price": 50
        }
    }
    with pytest.raises(ContextualValidationError, match = "price must be specified"):
        AdPartial.model_validate(cfg).to_ad(AdDefaults())


@pytest.mark.unit
def test_auto_reduce_requires_strategy(base_ad_cfg:dict[str, object]) -> None:
    cfg = base_ad_cfg.copy() | {
        "price": 100,
        "auto_price_reduction": {
            "enabled": True,
            "min_price": 50
        }
    }
    with pytest.raises(ContextualValidationError, match = "strategy must be specified"):
        AdPartial.model_validate(cfg).to_ad(AdDefaults())


@pytest.mark.unit
def test_prepare_ad_model_fills_missing_counters(base_ad_cfg:dict[str, object]) -> None:
    cfg = base_ad_cfg.copy() | {
        "price": 120,
        "shipping_type": "SHIPPING",
        "sell_directly": False
    }
    ad = AdPartial.model_validate(cfg).to_ad(AdDefaults())

    assert ad.auto_price_reduction.delay_reposts == 0
    assert ad.auto_price_reduction.delay_days == 0
    assert ad.price_reduction_count == 0
    assert ad.repost_count == 0


@pytest.mark.unit
def test_min_price_must_not_exceed_price(base_ad_cfg:dict[str, object]) -> None:
    cfg = base_ad_cfg.copy() | {
        "price": 100,
        "auto_price_reduction": {
            "enabled": True,
            "strategy": "FIXED",
            "amount": 5,
            "min_price": 120
        }
    }
    with pytest.raises(ContextualValidationError, match = "min_price must not exceed price"):
        AdPartial.model_validate(cfg)


@pytest.mark.unit
def test_auto_reduce_requires_min_price(base_ad_cfg:dict[str, object]) -> None:
    cfg = base_ad_cfg.copy() | {
        "price": 100,
        "auto_price_reduction": {
            "enabled": True,
            "strategy": "FIXED",
            "amount": 5
        }
    }
    with pytest.raises(ContextualValidationError, match = "min_price must be specified"):
        AdPartial.model_validate(cfg).to_ad(AdDefaults())


@pytest.mark.unit
def test_to_ad_stabilizes_counters_when_defaults_omit(base_ad_cfg:dict[str, object]) -> None:
    cfg = base_ad_cfg.copy() | {
        "republication_interval": 7,
        "price": 120
    }
    ad = AdPartial.model_validate(cfg).to_ad(AdDefaults())

    assert ad.auto_price_reduction.delay_reposts == 0
    assert ad.auto_price_reduction.delay_days == 0
    assert ad.price_reduction_count == 0
    assert ad.repost_count == 0


@pytest.mark.unit
def test_to_ad_sets_zero_when_counts_missing_from_dump(base_ad_cfg:dict[str, object]) -> None:
    cfg = base_ad_cfg.copy() | {
        "republication_interval": 7,
        "price": 130
    }
    ad = SparseDumpAdPartial.model_validate(cfg).to_ad(AdDefaults())

    assert ad.price_reduction_count == 0
    assert ad.repost_count == 0


@pytest.mark.unit
def test_ad_model_auto_reduce_requires_price(complete_ad_cfg:dict[str, object]) -> None:
    cfg = complete_ad_cfg.copy() | {"price": None}
    with pytest.raises(ContextualValidationError, match = "price must be specified"):
        Ad.model_validate(cfg)


@pytest.mark.unit
def test_ad_model_auto_reduce_requires_strategy(complete_ad_cfg:dict[str, object]) -> None:
    cfg_copy = complete_ad_cfg.copy()
    cfg_copy["auto_price_reduction"] = {
        "enabled": True,
        "min_price": 50
    }
    with pytest.raises(ContextualValidationError, match = "strategy must be specified"):
        Ad.model_validate(cfg_copy)


@pytest.mark.unit
def test_price_reduction_delay_inherited_from_defaults(complete_ad_cfg:dict[str, object]) -> None:
    # When auto_price_reduction is not specified in ad config, it inherits from defaults
    cfg = complete_ad_cfg.copy()
    cfg.pop("auto_price_reduction", None)  # Remove to inherit from defaults
    defaults = AdDefaults(
        auto_price_reduction = AutoPriceReductionConfig(
            enabled = True,
            strategy = "FIXED",
            amount = 5,
            min_price = 50,
            delay_reposts = 4,
            delay_days = 0
        )
    )
    ad = AdPartial.model_validate(cfg).to_ad(defaults)
    assert ad.auto_price_reduction.delay_reposts == 4


@pytest.mark.unit
def test_price_reduction_delay_override_zero(complete_ad_cfg:dict[str, object]) -> None:
    cfg = complete_ad_cfg.copy()
    # Type-safe way to modify nested dict
    cfg["auto_price_reduction"] = {
        "enabled": True,
        "strategy": "FIXED",
        "amount": 5,
        "min_price": 50,
        "delay_reposts": 0,
        "delay_days": 0
    }
    defaults = AdDefaults(
        auto_price_reduction = AutoPriceReductionConfig(
            enabled = True,
            strategy = "FIXED",
            amount = 5,
            min_price = 50,
            delay_reposts = 4,
            delay_days = 0
        )
    )
    ad = AdPartial.model_validate(cfg).to_ad(defaults)
    assert ad.auto_price_reduction.delay_reposts == 0


@pytest.mark.unit
def test_ad_model_auto_reduce_requires_min_price(complete_ad_cfg:dict[str, object]) -> None:
    cfg_copy = complete_ad_cfg.copy()
    cfg_copy["auto_price_reduction"] = {
        "enabled": True,
        "strategy": "FIXED",
        "amount": 5
    }
    with pytest.raises(ContextualValidationError, match = "min_price must be specified"):
        Ad.model_validate(cfg_copy)


@pytest.mark.unit
def test_ad_model_min_price_must_not_exceed_price(complete_ad_cfg:dict[str, object]) -> None:
    cfg_copy = complete_ad_cfg.copy()
    cfg_copy["price"] = 100
    cfg_copy["auto_price_reduction"] = {
        "enabled": True,
        "strategy": "FIXED",
        "amount": 5,
        "min_price": 150,
        "delay_reposts": 0,
        "delay_days": 0
    }
    with pytest.raises(ContextualValidationError, match = "min_price must not exceed price"):
        Ad.model_validate(cfg_copy)


@pytest.mark.unit
def test_calculate_auto_price_with_missing_strategy() -> None:
    """Test calculate_auto_price when strategy is None but enabled is True (defensive check)"""
    from kleinanzeigen_bot.model.ad_model import calculate_auto_price
    from kleinanzeigen_bot.model.config_model import AutoPriceReductionConfig

    # Use model_construct to bypass validation and reach defensive lines 234-235
    config = AutoPriceReductionConfig.model_construct(
        enabled = True, strategy = None, amount = None, min_price = 50
    )
    result = calculate_auto_price(
        base_price = 100,
        auto_price_reduction = config,
        target_reduction_cycle = 1
    )
    assert result == 100  # Should return base price when strategy is None


@pytest.mark.unit
def test_calculate_auto_price_with_missing_amount() -> None:
    """Test calculate_auto_price when amount is None but enabled is True (defensive check)"""
    from kleinanzeigen_bot.model.ad_model import calculate_auto_price
    from kleinanzeigen_bot.model.config_model import AutoPriceReductionConfig

    # Use model_construct to bypass validation and reach defensive lines 234-235
    config = AutoPriceReductionConfig.model_construct(
        enabled = True, strategy = "FIXED", amount = None, min_price = 50
    )
    result = calculate_auto_price(
        base_price = 100,
        auto_price_reduction = config,
        target_reduction_cycle = 1
    )
    assert result == 100  # Should return base price when amount is None


@pytest.mark.unit
def test_calculate_auto_price_raises_when_min_price_none_and_enabled() -> None:
    """Test that calculate_auto_price raises ValueError when min_price is None during calculation (defensive check)"""
    from kleinanzeigen_bot.model.ad_model import calculate_auto_price
    from kleinanzeigen_bot.model.config_model import AutoPriceReductionConfig

    # Use model_construct to bypass validation and reach defensive line 237-238
    config = AutoPriceReductionConfig.model_construct(
        enabled = True, strategy = "FIXED", amount = 10, min_price = None
    )

    with pytest.raises(ValueError, match = "min_price must be specified when auto_price_reduction is enabled"):
        calculate_auto_price(
            base_price = 100,
            auto_price_reduction = config,
            target_reduction_cycle = 1
        )


@pytest.mark.unit
def test_ad_validator_requires_price_when_enabled() -> None:
    """Test Ad model validator requires price when auto_price_reduction is enabled (defensive check on Ad)"""
    from kleinanzeigen_bot.model.config_model import AutoPriceReductionConfig

    # Use model_construct to bypass AdPartial validation and reach the Ad._validate_auto_price_config validator
    ad = Ad.model_construct(
        title = "Test Ad",
        category = "160",
        description = "Test description",
        price_type = "NEGOTIABLE",
        shipping_type = "PICKUP",
        type = "OFFER",
        active = True,
        sell_directly = False,
        republication_interval = 7,
        contact = Contact(name = "Test", zipcode = "12345"),
        auto_price_reduction = AutoPriceReductionConfig(enabled = True, strategy = "FIXED", amount = 5, min_price = 50),
        price = None
    )

    # Call the validator directly to reach line 283
    with pytest.raises(ValueError, match = "price must be specified when auto_price_reduction is enabled"):
        ad._validate_auto_price_config()


@pytest.mark.unit
def test_ad_validator_min_price_exceeds_price() -> None:
    """Test Ad model validator when min_price > price (defensive check on Ad)"""
    from kleinanzeigen_bot.model.config_model import AutoPriceReductionConfig

    # Use model_construct to bypass AdPartial validation and reach the Ad._validate_auto_price_config validator
    ad = Ad.model_construct(
        title = "Test Ad",
        category = "160",
        description = "Test description",
        price_type = "NEGOTIABLE",
        shipping_type = "PICKUP",
        type = "OFFER",
        active = True,
        sell_directly = False,
        republication_interval = 7,
        contact = Contact(name = "Test", zipcode = "12345"),
        price = 50,
        auto_price_reduction = AutoPriceReductionConfig(enabled = True, strategy = "FIXED", amount = 5, min_price = 100)
    )

    # Call the validator directly to reach line 286
    with pytest.raises(ValueError, match = "min_price must not exceed price"):
        ad._validate_auto_price_config()


@pytest.mark.unit
def test_auto_price_reduction_config_requires_amount_when_enabled() -> None:
    """Test AutoPriceReductionConfig validator requires amount when enabled"""
    from kleinanzeigen_bot.model.config_model import AutoPriceReductionConfig

    with pytest.raises(ValueError, match = "amount must be specified when auto_price_reduction is enabled"):
        AutoPriceReductionConfig(enabled = True, strategy = "FIXED", amount = None, min_price = 50)


@pytest.mark.unit
def test_ad_validator_when_min_price_is_none() -> None:
    """Test Ad model validator when auto_price_reduction is enabled but min_price is None (covers line 284->287 branch)"""
    from kleinanzeigen_bot.model.config_model import AutoPriceReductionConfig

    # Use model_construct to bypass validation and create an Ad with enabled=True but min_price=None
    ad = Ad.model_construct(
        title = "Test Ad",
        category = "160",
        description = "Test description",
        price_type = "NEGOTIABLE",
        shipping_type = "PICKUP",
        type = "OFFER",
        active = True,
        sell_directly = False,
        republication_interval = 7,
        contact = Contact(name = "Test", zipcode = "12345"),
        price = 100,
        auto_price_reduction = AutoPriceReductionConfig.model_construct(enabled = True, strategy = "FIXED", amount = 5, min_price = None)
    )

    # This should pass validation (line 284 condition is False, goes to line 287)
    result = ad._validate_auto_price_config()
    assert result == ad
