# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
from __future__ import annotations

import math

import pytest

from kleinanzeigen_bot.model.ad_model import (
    MAX_DESCRIPTION_LENGTH,
    MAX_TITLE_LENGTH,
    MIN_TITLE_LENGTH,
    AdPartial,
    ShippingOption,
    validate_condition_api_mapping,
)
from kleinanzeigen_bot.model.config_model import AdDefaults, AutoPriceReductionConfig
from kleinanzeigen_bot.utils.pydantics import ContextualModel, ContextualValidationError


@pytest.mark.unit
def test_shipping_costs_deprecated_in_schema() -> None:
    """shipping_costs field is still present and parseable, but marked deprecated in JSON schema."""
    # Field still works for parsing (deprecation is schema-level only)
    minimal_cfg = {"title": "Test Title", "category": "160", "description": "Test"}
    assert AdPartial.model_validate(minimal_cfg | {"shipping_costs": 4.95}).shipping_costs == 4.95

    # Verify deprecation marker is present in the generated JSON schema
    schema = AdPartial.model_json_schema()
    shipping_costs_props = schema["properties"]["shipping_costs"]
    assert shipping_costs_props.get("deprecated") is True


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

    assert (
        AdPartial.model_validate(
            minimal_ad_cfg
            | {
                "id": "123456789",
                "created_on": "2025-05-08T09:34:03",
                "updated_on": "2025-05-14T20:43:16",
                "content_hash": "5753ead7cf42b0ace5fe658ecb930b3a8f57ef49bd52b7ea2d64b91b2c75517e",
            }
        )
        .update_content_hash()
        .content_hash
        == minimal_ad_cfg_hash
    )

    assert (
        AdPartial.model_validate(
            minimal_ad_cfg
            | {
                "active": None,
                "images": None,
                "shipping_options": None,
                "special_attributes": None,
                "contact": None,
            }
        )
        .update_content_hash()
        .content_hash
        == minimal_ad_cfg_hash
    )

    assert (
        AdPartial.model_validate(
            minimal_ad_cfg
            | {
                "active": True,
                "images": [],
                "shipping_options": [],
                "special_attributes": {},
                "contact": {},
            }
        )
        .update_content_hash()
        .content_hash
        != minimal_ad_cfg_hash
    )


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

    with pytest.raises(ContextualValidationError, match = "shipping_costs expects a numeric value"):
        AdPartial.model_validate(minimal_ad_cfg | {"shipping_costs": ["DHL_10"]})

    with pytest.raises(ContextualValidationError, match = "Did you mean shipping_options"):
        AdPartial.model_validate(minimal_ad_cfg | {"shipping_costs": "DHL_10"})

    # multi-item sequence → generic list/sequence error (not the shipping_options hint)
    with pytest.raises(ContextualValidationError, match = "not a list/sequence"):
        AdPartial.model_validate(minimal_ad_cfg | {"shipping_costs": ["DHL_10", "HERMES_5"]})

    # single non-string item in sequence → generic list/sequence error
    with pytest.raises(ContextualValidationError, match = "not a list/sequence"):
        AdPartial.model_validate(minimal_ad_cfg | {"shipping_costs": [4.95]})


class ShippingOptionWrapper(ContextualModel):
    option:ShippingOption


@pytest.mark.unit
def test_shipping_option_must_not_be_blank() -> None:
    with pytest.raises(ContextualValidationError, match = "must be non-empty and non-blank"):
        ShippingOptionWrapper.model_validate({"option": " "})


@pytest.mark.unit
def test_validate_condition_api_mapping_rejects_unknown_values() -> None:
    with pytest.raises(ValueError, match = "contains unsupported condition API values: broken"):
        validate_condition_api_mapping("mapping_name", {"known": "new", "bad": "broken"})


@pytest.mark.unit
def test_description_length_limit() -> None:
    cfg = {"title": "Description Length", "category": "160", "description": "x" * (MAX_DESCRIPTION_LENGTH + 1)}

    with pytest.raises(ContextualValidationError, match = f"description length exceeds {MAX_DESCRIPTION_LENGTH} characters"):
        AdPartial.model_validate(cfg)


@pytest.mark.parametrize(
    ("title_length", "should_pass", "error_match"),
    [
        (MIN_TITLE_LENGTH - 1, False, f"title length must be at least {MIN_TITLE_LENGTH} characters"),
        (MIN_TITLE_LENGTH, True, None),
        (MAX_TITLE_LENGTH + 1, False, f"title length exceeds {MAX_TITLE_LENGTH} characters"),
        (MAX_TITLE_LENGTH, True, None),
    ],
)
@pytest.mark.unit
def test_title_length_validation(title_length:int, should_pass:bool, error_match:str | None) -> None:
    assert MAX_TITLE_LENGTH == 65
    cfg = {"title": "x" * title_length, "category": "160", "description": "Test Description"}
    if should_pass:
        validated = AdPartial.model_validate(cfg)
        assert validated.title == "x" * title_length
    else:
        assert error_match is not None
        with pytest.raises(ContextualValidationError, match = error_match):
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
        "active": True,
    }


@pytest.fixture
def complete_ad_cfg(base_ad_cfg:dict[str, object]) -> dict[str, object]:
    return base_ad_cfg | {
        "republication_interval": 7,
        "price": 100,
        "auto_price_reduction": {"enabled": True, "strategy": "FIXED", "amount": 5, "min_price": 50, "delay_reposts": 0, "delay_days": 0},
    }


class SparseDumpAdPartial(AdPartial):
    def model_dump(self, *args:object, **kwargs:object) -> dict[str, object]:
        data = super().model_dump(*args, **kwargs)  # type: ignore[arg-type]
        data.pop("price_reduction_count", None)
        data.pop("repost_count", None)
        return data


@pytest.mark.unit
def test_auto_reduce_requires_price(base_ad_cfg:dict[str, object]) -> None:
    cfg = base_ad_cfg.copy() | {"auto_price_reduction": {"enabled": True, "strategy": "FIXED", "amount": 5, "min_price": 50}}
    with pytest.raises(ContextualValidationError, match = "price must be specified"):
        AdPartial.model_validate(cfg).to_ad(AdDefaults())


@pytest.mark.unit
def test_auto_reduce_requires_strategy(base_ad_cfg:dict[str, object]) -> None:
    cfg = base_ad_cfg.copy() | {"price": 100, "auto_price_reduction": {"enabled": True, "min_price": 50}}
    with pytest.raises(ContextualValidationError, match = "strategy must be specified"):
        AdPartial.model_validate(cfg).to_ad(AdDefaults())


@pytest.mark.unit
def test_prepare_ad_model_fills_missing_counters(base_ad_cfg:dict[str, object]) -> None:
    cfg = base_ad_cfg.copy() | {"price": 120, "shipping_type": "SHIPPING", "sell_directly": False}
    ad = AdPartial.model_validate(cfg).to_ad(AdDefaults())

    assert ad.auto_price_reduction.delay_reposts == 0
    assert ad.auto_price_reduction.delay_days == 0
    assert ad.price_reduction_count == 0
    assert ad.repost_count == 0


@pytest.mark.unit
def test_min_price_must_not_exceed_price(base_ad_cfg:dict[str, object]) -> None:
    cfg = base_ad_cfg.copy() | {"price": 100, "auto_price_reduction": {"enabled": True, "strategy": "FIXED", "amount": 5, "min_price": 120}}
    with pytest.raises(ContextualValidationError, match = "min_price must not exceed price"):
        AdPartial.model_validate(cfg)


@pytest.mark.unit
def test_min_price_validation_defers_to_pydantic_for_invalid_types(base_ad_cfg:dict[str, object]) -> None:
    # Test that invalid price/min_price types are handled gracefully
    # The safe Decimal comparison should catch conversion errors and defer to Pydantic
    cfg = base_ad_cfg.copy() | {"price": "not_a_number", "auto_price_reduction": {"enabled": True, "strategy": "FIXED", "amount": 5, "min_price": 100}}
    # Should raise Pydantic validation error for invalid price type, not our custom validation error
    with pytest.raises(ContextualValidationError):
        AdPartial.model_validate(cfg)

    # Test with invalid min_price type
    cfg2 = base_ad_cfg.copy() | {"price": 100, "auto_price_reduction": {"enabled": True, "strategy": "FIXED", "amount": 5, "min_price": "invalid"}}
    # Should raise Pydantic validation error for invalid min_price type
    with pytest.raises(ContextualValidationError):
        AdPartial.model_validate(cfg2)


@pytest.mark.unit
def test_auto_reduce_requires_min_price(base_ad_cfg:dict[str, object]) -> None:
    cfg = base_ad_cfg.copy() | {"price": 100, "auto_price_reduction": {"enabled": True, "strategy": "FIXED", "amount": 5}}
    with pytest.raises(ContextualValidationError, match = "min_price must be specified"):
        AdPartial.model_validate(cfg).to_ad(AdDefaults())


@pytest.mark.unit
def test_to_ad_stabilizes_counters_when_defaults_omit(base_ad_cfg:dict[str, object]) -> None:
    cfg = base_ad_cfg.copy() | {"republication_interval": 7, "price": 120}
    ad = AdPartial.model_validate(cfg).to_ad(AdDefaults())

    assert ad.auto_price_reduction.delay_reposts == 0
    assert ad.auto_price_reduction.delay_days == 0
    assert ad.price_reduction_count == 0
    assert ad.repost_count == 0


@pytest.mark.unit
def test_to_ad_sets_zero_when_counts_missing_from_dump(base_ad_cfg:dict[str, object]) -> None:
    cfg = base_ad_cfg.copy() | {"republication_interval": 7, "price": 130}
    ad = SparseDumpAdPartial.model_validate(cfg).to_ad(AdDefaults())

    assert ad.price_reduction_count == 0
    assert ad.repost_count == 0


@pytest.mark.unit
def test_price_reduction_delay_inherited_from_defaults(complete_ad_cfg:dict[str, object]) -> None:
    # When auto_price_reduction is not specified in ad config, it inherits from defaults
    cfg = complete_ad_cfg.copy()
    cfg.pop("auto_price_reduction", None)  # Remove to inherit from defaults
    apr = AutoPriceReductionConfig(enabled = True, strategy = "FIXED", amount = 5, min_price = 50, delay_reposts = 4, delay_days = 0)
    defaults = AdDefaults(auto_price_reduction = apr)
    ad = AdPartial.model_validate(cfg).to_ad(defaults)
    assert ad.auto_price_reduction.delay_reposts == 4


@pytest.mark.unit
def test_price_reduction_delay_override_zero(complete_ad_cfg:dict[str, object]) -> None:
    cfg = complete_ad_cfg.copy()
    # Type-safe way to modify nested dict
    cfg["auto_price_reduction"] = {"enabled": True, "strategy": "FIXED", "amount": 5, "min_price": 50, "delay_reposts": 0, "delay_days": 0}
    apr = AutoPriceReductionConfig(enabled = True, strategy = "FIXED", amount = 5, min_price = 50, delay_reposts = 4, delay_days = 0)
    defaults = AdDefaults(auto_price_reduction = apr)
    ad = AdPartial.model_validate(cfg).to_ad(defaults)
    assert ad.auto_price_reduction.delay_reposts == 0


@pytest.mark.unit
def test_auto_price_reduction_config_requires_amount_when_enabled() -> None:
    """Test AutoPriceReductionConfig validator requires amount when enabled"""
    with pytest.raises(ValueError, match = "amount must be specified when auto_price_reduction is enabled"):
        AutoPriceReductionConfig(enabled = True, strategy = "FIXED", amount = None, min_price = 50)


# ── sell_directly validator tests ──────────────────────────────────────────────


@pytest.mark.unit
def test_sell_directly_accepted_with_valid_options(base_ad_cfg:dict[str, object]) -> None:
    """OFFER ad with sell_directly: true + SHIPPING + predefined options + FIXED passes."""
    cfg = base_ad_cfg.copy() | {
        "price_type": "FIXED",
        "shipping_type": "SHIPPING",
        "shipping_options": ["DHL_2"],
        "sell_directly": True,
        "price": 50,
    }
    ad = AdPartial.model_validate(cfg).to_ad(AdDefaults())
    assert ad.sell_directly is True
    assert ad.shipping_options == ["DHL_2"]


@pytest.mark.unit
def test_sell_directly_rejected_without_options(base_ad_cfg:dict[str, object]) -> None:
    """OFFER ad with sell_directly: true but no shipping_options fails."""
    cfg = base_ad_cfg.copy() | {
        "price_type": "FIXED",
        "shipping_type": "SHIPPING",
        "sell_directly": True,
        "price": 50,
    }
    with pytest.raises(ContextualValidationError, match = "shipping_option"):
        AdPartial.model_validate(cfg).to_ad(AdDefaults())


@pytest.mark.unit
def test_sell_directly_rejected_with_shipping_costs_only(base_ad_cfg:dict[str, object]) -> None:
    """OFFER ad with sell_directly: true + shipping_costs but no shipping_options fails."""
    cfg = base_ad_cfg.copy() | {
        "price_type": "FIXED",
        "shipping_type": "SHIPPING",
        "shipping_costs": 4.95,
        "sell_directly": True,
        "price": 50,
    }
    with pytest.raises(ContextualValidationError, match = "shipping_option"):
        AdPartial.model_validate(cfg).to_ad(AdDefaults())


@pytest.mark.unit
def test_sell_directly_wanted_not_rejected(base_ad_cfg:dict[str, object]) -> None:
    """WANTED ad with sell_directly: true must NOT be rejected by the validator."""
    cfg = base_ad_cfg.copy() | {
        "type": "WANTED",
        "sell_directly": True,
    }
    # Should pass without error even with minimal shipping config
    ad = AdPartial.model_validate(cfg).to_ad(AdDefaults())
    assert ad.type == "WANTED"
    assert ad.sell_directly is True


@pytest.mark.unit
def test_sell_directly_rejected_with_pickup(base_ad_cfg:dict[str, object]) -> None:
    """OFFER ad with sell_directly: true + PICKUP shipping_type fails."""
    cfg = base_ad_cfg.copy() | {
        "shipping_type": "PICKUP",
        "sell_directly": True,
    }
    with pytest.raises(ContextualValidationError, match = "shipping_type"):
        AdPartial.model_validate(cfg).to_ad(AdDefaults())


@pytest.mark.unit
def test_sell_directly_rejected_with_give_away(base_ad_cfg:dict[str, object]) -> None:
    """OFFER ad with sell_directly + GIVE_AWAY price_type fails."""
    cfg = base_ad_cfg.copy() | {
        "shipping_type": "SHIPPING",
        "shipping_options": ["DHL_2"],
        "price_type": "GIVE_AWAY",
        "sell_directly": True,
    }
    with pytest.raises(ContextualValidationError, match = "price_type"):
        AdPartial.model_validate(cfg).to_ad(AdDefaults())


@pytest.mark.unit
def test_shipping_costs_in_content_hash_during_deprecation() -> None:
    """Verify shipping_costs still affects the content hash during deprecation phase."""
    base = {
        "title": "Test Ad Title",
        "category": "160",
        "description": "Test Description",
    }
    hash_without_costs = AdPartial.model_validate(base).update_content_hash().content_hash
    hash_with_costs = AdPartial.model_validate(base | {"shipping_costs": 4.95}).update_content_hash().content_hash
    assert hash_with_costs != hash_without_costs, \
        "shipping_costs must still affect the content hash during deprecation"
