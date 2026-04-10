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
from kleinanzeigen_bot import AdUpdateStrategy
from kleinanzeigen_bot.model.ad_model import calculate_auto_price
from kleinanzeigen_bot.model.config_model import AutoPriceReductionConfig
from kleinanzeigen_bot.utils.pydantics import ContextualValidationError


@runtime_checkable
class _ApplyAutoPriceReduction(Protocol):
    def __call__(
        self,
        ad_cfg:Any,
        _ad_cfg_orig:dict[str, Any],
        ad_file_relative:str,
        *,
        mode:AdUpdateStrategy = AdUpdateStrategy.REPLACE,
    ) -> None:
        pass


@pytest.fixture
def apply_auto_price_reduction() -> _ApplyAutoPriceReduction:
    # Return the module-level function directly (no more name-mangling!)
    return kleinanzeigen_bot.apply_auto_price_reduction


def _price_cfg(*, on_update:bool = False, **overrides:Any) -> AutoPriceReductionConfig:
    """Create an auto_price_reduction config with optional overrides."""
    defaults:dict[str, Any] = {
        "enabled": True,
        "strategy": "PERCENTAGE",
        "amount": 10,
        "min_price": 50,
        "delay_reposts": 0,
        "delay_days": 0,
        "on_update": on_update,
    }
    defaults.update(overrides)
    return AutoPriceReductionConfig.model_validate(defaults)


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
def test_apply_auto_price_reduction_reduces_price_by_configured_percentage(apply_auto_price_reduction:_ApplyAutoPriceReduction) -> None:
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
    apply_auto_price_reduction(ad_cfg, ad_orig, "ad_test.yaml")

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
@pytest.mark.parametrize(
    ("price", "min_price", "price_reduction_count", "repost_count"),
    [
        (None, 10, 2, 2),
        (100, 100, 0, 1),
    ],
    ids = ["price_missing", "min_price_equals_price"],
)
def test_apply_auto_price_reduction_warns_and_preserves_state_on_invalid_config(
    price:int | None,
    min_price:int,
    price_reduction_count:int,
    repost_count:int,
    caplog:pytest.LogCaptureFixture,
    apply_auto_price_reduction:_ApplyAutoPriceReduction,
) -> None:
    ad_cfg = SimpleNamespace(
        price = price,
        auto_price_reduction = AutoPriceReductionConfig(
            enabled = True,
            strategy = "PERCENTAGE",
            amount = 25,
            min_price = min_price,
            delay_reposts = 0,
            delay_days = 0,
        ),
        price_reduction_count = price_reduction_count,
        repost_count = repost_count,
        updated_on = None,
        created_on = None,
    )

    ad_orig:dict[str, Any] = {}

    with caplog.at_level(logging.WARNING):
        apply_auto_price_reduction(ad_cfg, ad_orig, "ad_invalid.yaml")

    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warning_records) >= 1
    assert ad_cfg.price == price
    assert ad_cfg.price_reduction_count == price_reduction_count


@pytest.mark.unit
def test_apply_auto_price_reduction_respects_repost_delay(apply_auto_price_reduction:_ApplyAutoPriceReduction) -> None:
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
    apply_auto_price_reduction(ad_cfg, ad_orig, "ad_delay.yaml")

    assert ad_cfg.price == 200
    assert ad_cfg.price_reduction_count == 0


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
    assert ad_cfg.price == 73
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
def test_fractional_reduction_increments_counter_when_price_unchanged(
    caplog:pytest.LogCaptureFixture, apply_auto_price_reduction:_ApplyAutoPriceReduction
) -> None:
    # Small reductions that round back to the same euro value still advance the
    # reduction cycle counter so fractional progress can accumulate across runs.
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
    expected = _("Auto price reduction kept price %s after attempting %s reduction cycles") % (100, 1)
    assert any(expected in message for message in caplog.messages)
    assert ad_cfg.price == 100
    assert ad_cfg.price_reduction_count == 1
    assert "price_reduction_count" not in ad_orig


@pytest.mark.unit
def test_no_visible_change_at_floor_advances_counter(apply_auto_price_reduction:_ApplyAutoPriceReduction) -> None:
    """When price is already floor-clamped, no visible change still advances cycle counter."""
    ad_cfg = SimpleNamespace(
        price = 100,
        auto_price_reduction = AutoPriceReductionConfig(
            enabled = True,
            strategy = "PERCENTAGE",
            amount = 10,
            min_price = 90,
            delay_reposts = 0,
            delay_days = 0,
        ),
        price_reduction_count = 3,
        repost_count = 5,
        updated_on = None,
        created_on = None,
    )

    apply_auto_price_reduction(ad_cfg, {}, "ad_floor.yaml")

    assert ad_cfg.price == 90
    assert ad_cfg.price_reduction_count == 4


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


# ---------------------------------------------------------------------------
# MODIFY-mode price reduction tests (on_update conditional behavior)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_apply_modify_mode_on_update_false_leaves_base_price_when_no_prior_reductions(
    apply_auto_price_reduction:_ApplyAutoPriceReduction,
) -> None:
    """With no prior reductions, MODIFY + on_update=false must not start a new cycle."""
    ad_cfg = SimpleNamespace(
        price = 200,
        auto_price_reduction = _price_cfg(on_update = False, amount = 25),
        price_reduction_count = 0,
        repost_count = 1,
        updated_on = None,
        created_on = None,
    )

    apply_auto_price_reduction(ad_cfg, {}, "ad_no_update.yaml", mode = AdUpdateStrategy.MODIFY)

    assert ad_cfg.price == 200
    assert ad_cfg.price_reduction_count == 0


@pytest.mark.unit
def test_apply_modify_mode_applies_reduction_when_on_update_true_and_day_delay_satisfied(
    monkeypatch:pytest.MonkeyPatch,
    apply_auto_price_reduction:_ApplyAutoPriceReduction,
) -> None:
    """MODIFY mode with on_update=true reduces price when day delay is met.

    delay_reposts must be ignored in MODIFY mode (repost count does not change).
    """
    reference = datetime(2025, 1, 1, tzinfo = timezone.utc)
    ad_cfg = SimpleNamespace(
        price = 200,
        # delay_reposts=5 would normally block reduction, but MODIFY mode ignores it
        auto_price_reduction = _price_cfg(on_update = True, amount = 25, delay_reposts = 5, delay_days = 3),
        price_reduction_count = 0,
        repost_count = 1,
        updated_on = reference - timedelta(days = 5),
        created_on = reference - timedelta(days = 10),
    )

    monkeypatch.setattr("kleinanzeigen_bot.misc.now", lambda: reference)

    apply_auto_price_reduction(ad_cfg, {}, "ad_modify.yaml", mode = AdUpdateStrategy.MODIFY)

    assert ad_cfg.price == 150  # 200 * 0.75
    assert ad_cfg.price_reduction_count == 1


@pytest.mark.unit
def test_apply_modify_mode_skips_new_cycle_when_day_delay_not_satisfied(
    monkeypatch:pytest.MonkeyPatch,
    apply_auto_price_reduction:_ApplyAutoPriceReduction,
) -> None:
    """MODIFY mode with on_update=true does NOT apply a new cycle when day delay is not met."""
    reference = datetime(2025, 1, 1, tzinfo = timezone.utc)
    ad_cfg = SimpleNamespace(
        price = 200,
        auto_price_reduction = _price_cfg(on_update = True, amount = 25, delay_days = 3),
        price_reduction_count = 0,
        repost_count = 1,
        updated_on = reference - timedelta(days = 1),  # only 1 day elapsed, need 3
        created_on = reference - timedelta(days = 10),
    )

    monkeypatch.setattr("kleinanzeigen_bot.misc.now", lambda: reference)

    apply_auto_price_reduction(ad_cfg, {}, "ad_delay_not_met.yaml", mode = AdUpdateStrategy.MODIFY)

    assert ad_cfg.price == 200
    assert ad_cfg.price_reduction_count == 0


@pytest.mark.unit
def test_apply_modify_mode_restores_reduced_price_with_prior_reductions(
    apply_auto_price_reduction:_ApplyAutoPriceReduction,
) -> None:
    """Restore-first invariant: effective reduced price is recalculated from base price
    and existing reduction_count even when no new cycle is eligible.

    This prevents the YAML base price from overwriting an already-reduced effective price
    during MODIFY operations.
    """
    ad_cfg = SimpleNamespace(
        price = 100,  # YAML base price (original)
        auto_price_reduction = _price_cfg(on_update = True, amount = 10, delay_days = 5),
        price_reduction_count = 2,  # 2 prior reductions were applied
        repost_count = 5,
        updated_on = None,  # missing timestamp → day delay not satisfied
        created_on = None,
    )

    apply_auto_price_reduction(ad_cfg, {}, "ad_restore.yaml", mode = AdUpdateStrategy.MODIFY)

    # base=100, 2 cycles of 10%: 100*0.9=90 → 90*0.9=81
    assert ad_cfg.price == 81  # restored to reduced price, not left at base 100
    assert ad_cfg.price_reduction_count == 2  # no increment (no new cycle)


@pytest.mark.unit
def test_cross_mode_update_then_publish_preserves_reduced_price(
    apply_auto_price_reduction:_ApplyAutoPriceReduction,
) -> None:
    """After a MODIFY-mode reduction, a subsequent REPLACE with no newly eligible cycle
    must preserve the reduced price.

    Simulates:
      1. MODIFY applies one reduction cycle (100 → 90).
      2. Price and counters are persisted (simulated by resetting price to base and
         restoring counters as if re-loaded from YAML after one applied cycle).
      3. REPLACE runs but no new repost cycle is eligible → reduced price restored.
    """
    cfg = _price_cfg(on_update = True, amount = 10, delay_reposts = 1, delay_days = 0)

    # --- Step 1: MODIFY applies first reduction ---
    ad_cfg = SimpleNamespace(
        price = 100,
        auto_price_reduction = cfg,
        price_reduction_count = 0,
        repost_count = 1,
        updated_on = None,
        created_on = None,
    )
    apply_auto_price_reduction(ad_cfg, {}, "ad_cross.yaml", mode = AdUpdateStrategy.MODIFY)
    assert ad_cfg.price == 90
    assert ad_cfg.price_reduction_count == 1

    # --- Step 2: Simulate re-load from YAML (price resets to base) ---
    # repost_count and price_reduction_count reflect post-persist state after one cycle
    ad_cfg.price = 100  # YAML always stores base price
    ad_cfg.repost_count = 2
    ad_cfg.price_reduction_count = 1

    # --- Step 3: REPLACE mode, no new repost cycle eligible ---
    apply_auto_price_reduction(ad_cfg, {}, "ad_cross.yaml", mode = AdUpdateStrategy.REPLACE)

    # Restore-first: keep the single previously applied cycle from base 100 → 90
    assert ad_cfg.price == 90
    assert ad_cfg.price_reduction_count == 1


def test_modify_on_update_false_restores_price(
    apply_auto_price_reduction:_ApplyAutoPriceReduction,
) -> None:
    """MODIFY with on_update=false must still restore previously reduced prices.

    Regression test: when on_update is false, the evaluator must still compute
    and restore the effective price from price_reduction_count.  Without this,
    an ad that previously received reductions via REPLACE would have its base
    YAML price submitted during an update, silently reverting the reduction.

    Given:
      - base price 200, one 10% reduction already applied (→ 180)
      - price_reduction_count = 1, on_update = false
    Expected:
      - price is restored to 180 (not base 200)
      - price_reduction_count unchanged (no new cycle)
    """
    cfg = _price_cfg(on_update = False, amount = 10, delay_reposts = 0, delay_days = 0)

    ad_cfg = SimpleNamespace(
        price = 200,  # YAML base price (not yet restored)
        auto_price_reduction = cfg,
        price_reduction_count = 1,  # one reduction already applied
        repost_count = 1,
        updated_on = None,
        created_on = None,
    )

    apply_auto_price_reduction(ad_cfg, {}, "ad_restore.yaml", mode = AdUpdateStrategy.MODIFY)

    # Price must be restored from base 200 with one 10% reduction → 180
    assert ad_cfg.price == 180
    # Counter must NOT advance (on_update is false)
    assert ad_cfg.price_reduction_count == 1
