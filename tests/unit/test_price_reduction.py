# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import logging
from datetime import datetime, timedelta, timezone
from gettext import gettext as _
from types import SimpleNamespace
from typing import Any, Protocol, runtime_checkable

import pytest

import kleinanzeigen_bot
from kleinanzeigen_bot.model.ad_model import calculate_auto_price
from kleinanzeigen_bot.model.config_model import AutoPriceReductionConfig
from kleinanzeigen_bot.utils.pydantics import ContextualValidationError


@runtime_checkable
class _ApplyAutoPriceReduction(Protocol):
    def __call__(self, ad_cfg:SimpleNamespace, ad_cfg_orig:dict[str, Any], ad_file_relative:str) -> None:
        pass


@pytest.fixture
def apply_auto_price_reduction() -> _ApplyAutoPriceReduction:
    # Return the module-level function directly (no more name-mangling!)
    return kleinanzeigen_bot.apply_auto_price_reduction  # type: ignore[return-value]


@pytest.mark.unit
def test_initial_posting_uses_base_price() -> None:
    config = AutoPriceReductionConfig(enabled = True, strategy = "PERCENTAGE", amount = 10, min_price = 50)
    assert calculate_auto_price(base_price = 100, auto_price_reduction = config, target_reduction_cycle = 0) == 100


@pytest.mark.unit
def test_auto_price_returns_none_without_base_price() -> None:
    config = AutoPriceReductionConfig(enabled = True, strategy = "PERCENTAGE", amount = 10, min_price = 10)
    assert calculate_auto_price(base_price = None, auto_price_reduction = config, target_reduction_cycle = 3) is None


@pytest.mark.unit
def test_negative_price_reduction_count_is_treated_like_zero() -> None:
    config = AutoPriceReductionConfig(enabled = True, strategy = "PERCENTAGE", amount = 25, min_price = 50)
    assert calculate_auto_price(base_price = 100, auto_price_reduction = config, target_reduction_cycle = -3) == 100


@pytest.mark.unit
def test_missing_price_reduction_returns_base_price() -> None:
    assert calculate_auto_price(base_price = 150, auto_price_reduction = None, target_reduction_cycle = 4) == 150


@pytest.mark.unit
def test_percentage_reduction_on_float_rounds_half_up() -> None:
    config = AutoPriceReductionConfig(enabled = True, strategy = "PERCENTAGE", amount = 12.5, min_price = 50)
    assert calculate_auto_price(base_price = 99.99, auto_price_reduction = config, target_reduction_cycle = 1) == 87


@pytest.mark.unit
def test_fixed_reduction_on_float_rounds_half_up() -> None:
    config = AutoPriceReductionConfig(enabled = True, strategy = "FIXED", amount = 12.4, min_price = 50)
    assert calculate_auto_price(base_price = 80.51, auto_price_reduction = config, target_reduction_cycle = 1) == 68


@pytest.mark.unit
def test_percentage_price_reduction_over_time() -> None:
    config = AutoPriceReductionConfig(enabled = True, strategy = "PERCENTAGE", amount = 10, min_price = 50)
    assert calculate_auto_price(base_price = 100, auto_price_reduction = config, target_reduction_cycle = 1) == 90
    assert calculate_auto_price(base_price = 100, auto_price_reduction = config, target_reduction_cycle = 2) == 81
    assert calculate_auto_price(base_price = 100, auto_price_reduction = config, target_reduction_cycle = 3) == 73


@pytest.mark.unit
def test_fixed_price_reduction_over_time() -> None:
    config = AutoPriceReductionConfig(enabled = True, strategy = "FIXED", amount = 15, min_price = 50)
    assert calculate_auto_price(base_price = 100, auto_price_reduction = config, target_reduction_cycle = 1) == 85
    assert calculate_auto_price(base_price = 100, auto_price_reduction = config, target_reduction_cycle = 2) == 70
    assert calculate_auto_price(base_price = 100, auto_price_reduction = config, target_reduction_cycle = 3) == 55


@pytest.mark.unit
def test_min_price_boundary_is_respected() -> None:
    config = AutoPriceReductionConfig(enabled = True, strategy = "FIXED", amount = 20, min_price = 50)
    assert calculate_auto_price(base_price = 100, auto_price_reduction = config, target_reduction_cycle = 5) == 50


@pytest.mark.unit
def test_min_price_zero_is_allowed() -> None:
    config = AutoPriceReductionConfig(enabled = True, strategy = "FIXED", amount = 5, min_price = 0)
    assert calculate_auto_price(base_price = 20, auto_price_reduction = config, target_reduction_cycle = 5) == 0


@pytest.mark.unit
def test_missing_min_price_raises_error() -> None:
    # min_price validation happens at config initialization when enabled=True
    with pytest.raises(ContextualValidationError, match = "min_price must be specified"):
        AutoPriceReductionConfig.model_validate({"enabled": True, "strategy": "PERCENTAGE", "amount": 50, "min_price": None})


@pytest.mark.unit
def test_percentage_above_100_raises_error() -> None:
    with pytest.raises(ContextualValidationError, match = "Percentage reduction amount must not exceed 100"):
        AutoPriceReductionConfig.model_validate({"enabled": True, "strategy": "PERCENTAGE", "amount": 150, "min_price": 50})


@pytest.mark.unit
def test_feature_disabled_path_leaves_price_unchanged() -> None:
    config = AutoPriceReductionConfig(enabled = False, strategy = "PERCENTAGE", amount = 25, min_price = 50)
    price = calculate_auto_price(base_price = 100, auto_price_reduction = config, target_reduction_cycle = 4)
    assert price == 100


@pytest.mark.unit
def test_apply_auto_price_reduction_disabled_emits_no_decision_logs(
    caplog:pytest.LogCaptureFixture, apply_auto_price_reduction:_ApplyAutoPriceReduction
) -> None:
    ad_cfg = SimpleNamespace(
        price = 100,
        auto_price_reduction = AutoPriceReductionConfig(
            enabled = False,
            strategy = "PERCENTAGE",
            amount = 10,
            min_price = 50,
            delay_reposts = 0,
            delay_days = 0,
        ),
        price_reduction_count = 0,
        repost_count = 0,
        updated_on = None,
        created_on = None,
    )

    with caplog.at_level(logging.INFO):
        apply_auto_price_reduction(ad_cfg, {}, "ad_disabled.yaml")

    assert not any("Auto price reduction decision for" in message for message in caplog.messages)


@pytest.mark.unit
def test_apply_auto_price_reduction_logs_drop(caplog:pytest.LogCaptureFixture, apply_auto_price_reduction:_ApplyAutoPriceReduction) -> None:
    ad_cfg = SimpleNamespace(
        price = 200,
        auto_price_reduction = AutoPriceReductionConfig(
            enabled = True,
            strategy = "PERCENTAGE",
            amount = 25,
            min_price = 50,
            delay_reposts = 0,
            delay_days = 0,
        ),
        price_reduction_count = 0,
        repost_count = 1,
        updated_on = None,
        created_on = None,
    )

    ad_orig:dict[str, Any] = {}

    with caplog.at_level(logging.INFO):
        apply_auto_price_reduction(ad_cfg, ad_orig, "ad_test.yaml")

    expected = _("Auto price reduction applied: %s -> %s after %s reduction cycles") % (200, 150, 1)
    assert any(expected in message for message in caplog.messages)
    assert ad_cfg.price == 150
    assert ad_cfg.price_reduction_count == 1
    # Note: price_reduction_count is NOT persisted to ad_orig until after successful publish
    assert "price_reduction_count" not in ad_orig


@pytest.mark.unit
def test_apply_auto_price_reduction_logs_unchanged_price_at_floor(
    caplog:pytest.LogCaptureFixture, apply_auto_price_reduction:_ApplyAutoPriceReduction
) -> None:
    # Test scenario: price has been reduced to just above min_price,
    # and the next reduction would drop it below, so it gets clamped
    ad_cfg = SimpleNamespace(
        price = 95,
        auto_price_reduction = AutoPriceReductionConfig(enabled = True, strategy = "FIXED", amount = 10, min_price = 90, delay_reposts = 0, delay_days = 0),
        price_reduction_count = 0,
        repost_count = 1,
        updated_on = None,
        created_on = None,
    )

    ad_orig:dict[str, Any] = {}

    with caplog.at_level(logging.INFO):
        apply_auto_price_reduction(ad_cfg, ad_orig, "ad_test.yaml")

    # Price: 95 - 10 = 85, clamped to 90 (floor)
    # So the effective price is 90, not 95, meaning reduction was applied
    expected = _("Auto price reduction applied: %s -> %s after %s reduction cycles") % (95, 90, 1)
    assert any(expected in message for message in caplog.messages)
    assert ad_cfg.price == 90
    assert ad_cfg.price_reduction_count == 1
    assert "price_reduction_count" not in ad_orig


@pytest.mark.unit
def test_apply_auto_price_reduction_warns_when_price_missing(caplog:pytest.LogCaptureFixture, apply_auto_price_reduction:_ApplyAutoPriceReduction) -> None:
    ad_cfg = SimpleNamespace(
        price = None,
        auto_price_reduction = AutoPriceReductionConfig(
            enabled = True,
            strategy = "PERCENTAGE",
            amount = 25,
            min_price = 10,
            delay_reposts = 0,
            delay_days = 0,
        ),
        price_reduction_count = 2,
        repost_count = 2,
        updated_on = None,
        created_on = None,
    )

    ad_orig:dict[str, Any] = {}

    with caplog.at_level(logging.WARNING):
        apply_auto_price_reduction(ad_cfg, ad_orig, "ad_warning.yaml")

    expected = _("Auto price reduction is enabled for [%s] but no price is configured.") % ("ad_warning.yaml",)
    assert any(expected in message for message in caplog.messages)
    assert ad_cfg.price is None


@pytest.mark.unit
def test_apply_auto_price_reduction_warns_when_min_price_equals_price(
    caplog:pytest.LogCaptureFixture, apply_auto_price_reduction:_ApplyAutoPriceReduction
) -> None:
    ad_cfg = SimpleNamespace(
        price = 100,
        auto_price_reduction = AutoPriceReductionConfig(
            enabled = True,
            strategy = "PERCENTAGE",
            amount = 25,
            min_price = 100,
            delay_reposts = 0,
            delay_days = 0,
        ),
        price_reduction_count = 0,
        repost_count = 1,
        updated_on = None,
        created_on = None,
    )

    ad_orig:dict[str, Any] = {}

    with caplog.at_level(logging.WARNING):
        apply_auto_price_reduction(ad_cfg, ad_orig, "ad_equal_prices.yaml")

    expected = _("Auto price reduction is enabled for [%s] but min_price equals price (%s) - no reductions will occur.") % ("ad_equal_prices.yaml", 100)
    assert any(expected in message for message in caplog.messages)
    assert ad_cfg.price == 100
    assert ad_cfg.price_reduction_count == 0


@pytest.mark.unit
def test_apply_auto_price_reduction_respects_repost_delay(caplog:pytest.LogCaptureFixture, apply_auto_price_reduction:_ApplyAutoPriceReduction) -> None:
    ad_cfg = SimpleNamespace(
        price = 200,
        auto_price_reduction = AutoPriceReductionConfig(
            enabled = True,
            strategy = "PERCENTAGE",
            amount = 25,
            min_price = 50,
            delay_reposts = 3,
            delay_days = 0,
        ),
        price_reduction_count = 0,
        repost_count = 2,
        updated_on = None,
        created_on = None,
    )

    ad_orig:dict[str, Any] = {}

    with caplog.at_level(logging.DEBUG):
        apply_auto_price_reduction(ad_cfg, ad_orig, "ad_delay.yaml")

    assert ad_cfg.price == 200
    delayed_message = _("Auto price reduction delayed for [%s]: waiting %s more reposts (completed %s, applied %s reductions)") % ("ad_delay.yaml", 2, 2, 0)
    assert any(delayed_message in message for message in caplog.messages)
    decision_message = (
        "Auto price reduction decision for [ad_delay.yaml]: skipped (repost delay). "
        "next reduction earliest at repost >= 4 and day delay 0/0 days. repost_count=2 eligible_cycles=0 applied_cycles=0"
    )
    assert any(message.startswith(decision_message) for message in caplog.messages)


@pytest.mark.unit
def test_apply_auto_price_reduction_after_repost_delay_reduces_once(apply_auto_price_reduction:_ApplyAutoPriceReduction) -> None:
    ad_cfg = SimpleNamespace(
        price = 100,
        auto_price_reduction = AutoPriceReductionConfig(
            enabled = True,
            strategy = "PERCENTAGE",
            amount = 10,
            min_price = 50,
            delay_reposts = 2,
            delay_days = 0,
        ),
        price_reduction_count = 0,
        repost_count = 3,
        updated_on = None,
        created_on = None,
    )

    ad_cfg_orig:dict[str, Any] = {}
    apply_auto_price_reduction(ad_cfg, ad_cfg_orig, "ad_after_delay.yaml")

    assert ad_cfg.price == 90
    assert ad_cfg.price_reduction_count == 1
    # Note: price_reduction_count is NOT persisted to ad_orig until after successful publish
    assert "price_reduction_count" not in ad_cfg_orig


@pytest.mark.unit
def test_apply_auto_price_reduction_waits_when_reduction_already_applied(
    caplog:pytest.LogCaptureFixture, apply_auto_price_reduction:_ApplyAutoPriceReduction
) -> None:
    ad_cfg = SimpleNamespace(
        price = 100,
        auto_price_reduction = AutoPriceReductionConfig(
            enabled = True,
            strategy = "PERCENTAGE",
            amount = 10,
            min_price = 50,
            delay_reposts = 0,
            delay_days = 0,
        ),
        price_reduction_count = 3,
        repost_count = 3,
        updated_on = None,
        created_on = None,
    )

    ad_orig:dict[str, Any] = {}

    with caplog.at_level(logging.DEBUG, logger = "kleinanzeigen_bot"):
        apply_auto_price_reduction(ad_cfg, ad_orig, "ad_already.yaml")

    expected = _("Auto price reduction already applied for [%s]: %s reductions match %s eligible reposts") % ("ad_already.yaml", 3, 3)
    assert any(expected in message for message in caplog.messages)
    decision_message = (
        "Auto price reduction decision for [ad_already.yaml]: skipped (repost delay). "
        "next reduction earliest at repost >= 4 and day delay 0/0 days. repost_count=3 eligible_cycles=3 applied_cycles=3"
    )
    assert any(message.startswith(decision_message) for message in caplog.messages)
    assert ad_cfg.price == 100
    assert ad_cfg.price_reduction_count == 3
    assert "price_reduction_count" not in ad_orig


@pytest.mark.unit
def test_apply_auto_price_reduction_respects_day_delay(
    monkeypatch:pytest.MonkeyPatch, caplog:pytest.LogCaptureFixture, apply_auto_price_reduction:_ApplyAutoPriceReduction
) -> None:
    reference = datetime(2025, 1, 1, tzinfo = timezone.utc)
    ad_cfg = SimpleNamespace(
        price = 150,
        auto_price_reduction = AutoPriceReductionConfig(
            enabled = True,
            strategy = "PERCENTAGE",
            amount = 25,
            min_price = 50,
            delay_reposts = 0,
            delay_days = 3,
        ),
        price_reduction_count = 0,
        repost_count = 1,
        updated_on = reference,
        created_on = reference,
    )

    monkeypatch.setattr("kleinanzeigen_bot.misc.now", lambda: reference + timedelta(days = 1))

    ad_orig:dict[str, Any] = {}

    with caplog.at_level("INFO"):
        apply_auto_price_reduction(ad_cfg, ad_orig, "ad_delay_days.yaml")

    assert ad_cfg.price == 150
    delayed_message = _("Auto price reduction delayed for [%s]: waiting %s days (elapsed %s)") % ("ad_delay_days.yaml", 3, 1)
    assert any(delayed_message in message for message in caplog.messages)


@pytest.mark.unit
def test_apply_auto_price_reduction_runs_after_delays(monkeypatch:pytest.MonkeyPatch, apply_auto_price_reduction:_ApplyAutoPriceReduction) -> None:
    reference = datetime(2025, 1, 1, tzinfo = timezone.utc)
    ad_cfg = SimpleNamespace(
        price = 120,
        auto_price_reduction = AutoPriceReductionConfig(
            enabled = True,
            strategy = "PERCENTAGE",
            amount = 25,
            min_price = 60,
            delay_reposts = 2,
            delay_days = 3,
        ),
        price_reduction_count = 0,
        repost_count = 3,
        updated_on = reference - timedelta(days = 5),
        created_on = reference - timedelta(days = 10),
    )

    monkeypatch.setattr("kleinanzeigen_bot.misc.now", lambda: reference)

    ad_orig:dict[str, Any] = {}
    apply_auto_price_reduction(ad_cfg, ad_orig, "ad_ready.yaml")

    assert ad_cfg.price == 90


@pytest.mark.unit
def test_apply_auto_price_reduction_delayed_when_timestamp_missing(
    caplog:pytest.LogCaptureFixture, apply_auto_price_reduction:_ApplyAutoPriceReduction
) -> None:
    ad_cfg = SimpleNamespace(
        price = 200,
        auto_price_reduction = AutoPriceReductionConfig(enabled = True, strategy = "FIXED", amount = 20, min_price = 50, delay_reposts = 0, delay_days = 2),
        price_reduction_count = 0,
        repost_count = 1,
        updated_on = None,
        created_on = None,
    )

    ad_orig:dict[str, Any] = {}

    with caplog.at_level("INFO"):
        apply_auto_price_reduction(ad_cfg, ad_orig, "ad_missing_time.yaml")

    expected = _("Auto price reduction delayed for [%s]: waiting %s days but publish timestamp missing") % ("ad_missing_time.yaml", 2)
    assert any(expected in message for message in caplog.messages)


@pytest.mark.unit
def test_fractional_reduction_increments_counter_even_when_price_unchanged(
    caplog:pytest.LogCaptureFixture, apply_auto_price_reduction:_ApplyAutoPriceReduction
) -> None:
    # Test that small fractional reductions increment the counter even when rounded price doesn't change
    # This allows cumulative reductions to eventually show visible effect
    ad_cfg = SimpleNamespace(
        price = 100,
        auto_price_reduction = AutoPriceReductionConfig(enabled = True, strategy = "FIXED", amount = 0.3, min_price = 50, delay_reposts = 0, delay_days = 0),
        price_reduction_count = 0,
        repost_count = 1,
        updated_on = None,
        created_on = None,
    )

    ad_orig:dict[str, Any] = {}

    with caplog.at_level(logging.INFO):
        apply_auto_price_reduction(ad_cfg, ad_orig, "ad_fractional.yaml")

    # Price: 100 - 0.3 = 99.7, rounds to 100 (no visible change)
    # But counter should still increment for future cumulative reductions
    expected = _("Auto price reduction kept price %s after attempting %s reduction cycles") % (100, 1)
    assert any(expected in message for message in caplog.messages)
    assert ad_cfg.price == 100
    assert ad_cfg.price_reduction_count == 1  # Counter incremented despite no visible price change
    assert "price_reduction_count" not in ad_orig


@pytest.mark.unit
def test_apply_auto_price_reduction_verbose_logs_trace(caplog:pytest.LogCaptureFixture, apply_auto_price_reduction:_ApplyAutoPriceReduction) -> None:
    ad_cfg = SimpleNamespace(
        price = 200,
        auto_price_reduction = AutoPriceReductionConfig(
            enabled = True,
            strategy = "PERCENTAGE",
            amount = 25,
            min_price = 50,
            delay_reposts = 0,
            delay_days = 0,
        ),
        price_reduction_count = 0,
        repost_count = 1,
        updated_on = None,
        created_on = None,
    )

    with caplog.at_level(logging.DEBUG, logger = "kleinanzeigen_bot"):
        apply_auto_price_reduction(ad_cfg, {}, "ad_trace.yaml")

    assert any("Auto price reduction trace for [ad_trace.yaml]" in message for message in caplog.messages)
    assert any(" -> cycle=1 before=200 reduction=50.0 after_rounding=150 floor_applied=False" in message for message in caplog.messages)


@pytest.mark.unit
def test_reduction_value_zero_raises_error() -> None:
    with pytest.raises(ContextualValidationError, match = "Input should be greater than 0"):
        AutoPriceReductionConfig.model_validate({"enabled": True, "strategy": "PERCENTAGE", "amount": 0, "min_price": 50})


@pytest.mark.unit
def test_reduction_value_negative_raises_error() -> None:
    with pytest.raises(ContextualValidationError, match = "Input should be greater than 0"):
        AutoPriceReductionConfig.model_validate({"enabled": True, "strategy": "FIXED", "amount": -5, "min_price": 50})


@pytest.mark.unit
def test_percentage_reduction_100_percent() -> None:
    config = AutoPriceReductionConfig(enabled = True, strategy = "PERCENTAGE", amount = 100, min_price = 0)
    assert calculate_auto_price(base_price = 150, auto_price_reduction = config, target_reduction_cycle = 1) == 0


@pytest.mark.unit
def test_extreme_reduction_cycles() -> None:
    # Test that extreme cycle counts don't cause performance issues or errors
    config = AutoPriceReductionConfig(enabled = True, strategy = "PERCENTAGE", amount = 10, min_price = 0)
    result = calculate_auto_price(base_price = 1000, auto_price_reduction = config, target_reduction_cycle = 100)
    # With commercial rounding (round after each step), price stabilizes at 5
    # because 5 * 0.9 = 4.5 rounds back to 5 with ROUND_HALF_UP
    assert result == 5


@pytest.mark.unit
def test_commercial_rounding_each_step() -> None:
    """Test that commercial rounding is applied after each reduction step, not just at the end."""
    config = AutoPriceReductionConfig(enabled = True, strategy = "PERCENTAGE", amount = 10, min_price = 0)
    # With 135 EUR and 2x 10% reduction:
    # Step 1: 135 * 0.9 = 121.5 → rounds to 122 EUR
    # Step 2: 122 * 0.9 = 109.8 → rounds to 110 EUR
    # (Without intermediate rounding, it would be: 135 * 0.9^2 = 109.35 → 109 EUR)
    result = calculate_auto_price(base_price = 135, auto_price_reduction = config, target_reduction_cycle = 2)
    assert result == 110  # Commercial rounding result


@pytest.mark.unit
def test_extreme_reduction_cycles_with_floor() -> None:
    # Test that extreme cycles stop at min_price and don't cause issues
    config = AutoPriceReductionConfig(enabled = True, strategy = "PERCENTAGE", amount = 10, min_price = 50)
    result = calculate_auto_price(base_price = 1000, auto_price_reduction = config, target_reduction_cycle = 1000)
    # Should stop at min_price, not go to 0, regardless of cycle count
    assert result == 50


@pytest.mark.unit
def test_fractional_min_price_is_rounded_up_with_ceiling() -> None:
    # Test that fractional min_price is rounded UP using ROUND_CEILING
    # This prevents the price from going below min_price due to int() conversion
    # Example: min_price=90.5 should become floor of 91, not 90
    config = AutoPriceReductionConfig(enabled = True, strategy = "FIXED", amount = 10, min_price = 90.5)

    # Start at 100, reduce by 10 = 90
    # But min_price=90.5 rounds UP to 91 with ROUND_CEILING
    # So the result should be 91, not 90
    result = calculate_auto_price(base_price = 100, auto_price_reduction = config, target_reduction_cycle = 1)
    assert result == 91  # Rounded up from 90.5 floor

    # Verify with another fractional value
    config2 = AutoPriceReductionConfig(enabled = True, strategy = "FIXED", amount = 5, min_price = 49.1)
    result2 = calculate_auto_price(
        base_price = 60,
        auto_price_reduction = config2,
        target_reduction_cycle = 3,  # 60 - 5 - 5 - 5 = 45, clamped to ceil(49.1) = 50
    )
    assert result2 == 50  # Rounded up from 49.1 floor
