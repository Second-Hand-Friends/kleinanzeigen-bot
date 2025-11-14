# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
from gettext import gettext as _
from types import SimpleNamespace
from typing import Any, Protocol, cast, runtime_checkable

import pytest

from kleinanzeigen_bot import KleinanzeigenBot
from kleinanzeigen_bot.model.ad_model import calculate_auto_price
from kleinanzeigen_bot.model.config_model import PriceReductionConfig


@runtime_checkable
class _ApplyAutoPriceReduction(Protocol):
    def __call__(self, ad_cfg:SimpleNamespace, ad_cfg_orig:dict[str, Any], ad_file_relative:str) -> None:
        ...


@pytest.fixture
def apply_auto_price_reduction() -> _ApplyAutoPriceReduction:
    bot:Any = KleinanzeigenBot()
    return cast(_ApplyAutoPriceReduction, bot._KleinanzeigenBot__apply_auto_price_reduction)


@pytest.mark.unit
def test_initial_posting_uses_base_price() -> None:
    reduction = PriceReductionConfig(type = "PERCENTAGE", value = 10)
    assert calculate_auto_price(
        base_price = 100,
        auto_reduce = True,
        price_reduction = reduction,
        repost_count = 0,
        min_price = 50
    ) == 100


@pytest.mark.unit
def test_auto_price_returns_none_without_base_price() -> None:
    reduction = PriceReductionConfig(type = "PERCENTAGE", value = 10)
    assert calculate_auto_price(
        base_price = None,
        auto_reduce = True,
        price_reduction = reduction,
        repost_count = 3,
        min_price = 10
    ) is None


@pytest.mark.unit
def test_negative_price_reduction_count_is_treated_like_zero() -> None:
    reduction = PriceReductionConfig(type = "PERCENTAGE", value = 25)
    assert calculate_auto_price(
        base_price = 100,
        auto_reduce = True,
        price_reduction = reduction,
        repost_count = -3,
        min_price = 50
    ) == 100


@pytest.mark.unit
def test_missing_price_reduction_returns_base_price() -> None:
    assert calculate_auto_price(
        base_price = 150,
        auto_reduce = True,
        price_reduction = None,
        repost_count = 4,
        min_price = 50
    ) == 150


@pytest.mark.unit
def test_percentage_reduction_on_float_rounds_half_up() -> None:
    reduction = PriceReductionConfig(type = "PERCENTAGE", value = 12.5)
    assert calculate_auto_price(
        base_price = 99.99,
        auto_reduce = True,
        price_reduction = reduction,
        repost_count = 1,
        min_price = 50
    ) == 87


@pytest.mark.unit
def test_fixed_reduction_on_float_rounds_half_up() -> None:
    reduction = PriceReductionConfig(type = "FIXED", value = 12.4)
    assert calculate_auto_price(
        base_price = 80.51,
        auto_reduce = True,
        price_reduction = reduction,
        repost_count = 1,
        min_price = 50
    ) == 68


@pytest.mark.unit
def test_percentage_price_reduction_over_time() -> None:
    reduction = PriceReductionConfig(type = "PERCENTAGE", value = 10)
    assert calculate_auto_price(
        base_price = 100,
        auto_reduce = True,
        price_reduction = reduction,
        repost_count = 1,
        min_price = 50
    ) == 90
    assert calculate_auto_price(
        base_price = 100,
        auto_reduce = True,
        price_reduction = reduction,
        repost_count = 2,
        min_price = 50
    ) == 81
    assert calculate_auto_price(
        base_price = 100,
        auto_reduce = True,
        price_reduction = reduction,
        repost_count = 3,
        min_price = 50
    ) == 73


@pytest.mark.unit
def test_fixed_price_reduction_over_time() -> None:
    reduction = PriceReductionConfig(type = "FIXED", value = 15)
    assert calculate_auto_price(
        base_price = 100,
        auto_reduce = True,
        price_reduction = reduction,
        repost_count = 1,
        min_price = 40
    ) == 85
    assert calculate_auto_price(
        base_price = 100,
        auto_reduce = True,
        price_reduction = reduction,
        repost_count = 2,
        min_price = 40
    ) == 70
    assert calculate_auto_price(
        base_price = 100,
        auto_reduce = True,
        price_reduction = reduction,
        repost_count = 3,
        min_price = 40
    ) == 55


@pytest.mark.unit
def test_min_price_boundary_is_respected() -> None:
    reduction = PriceReductionConfig(type = "FIXED", value = 20)
    assert calculate_auto_price(
        base_price = 100,
        auto_reduce = True,
        price_reduction = reduction,
        repost_count = 5,
        min_price = 50
    ) == 50


@pytest.mark.unit
def test_min_price_zero_is_allowed() -> None:
    reduction = PriceReductionConfig(type = "FIXED", value = 5)
    assert calculate_auto_price(
        base_price = 20,
        auto_reduce = True,
        price_reduction = reduction,
        repost_count = 5,
        min_price = 0
    ) == 0


@pytest.mark.unit
def test_missing_min_price_raises_error() -> None:
    reduction = PriceReductionConfig(type = "PERCENTAGE", value = 50)
    with pytest.raises(ValueError, match = "min_price must be specified"):
        calculate_auto_price(base_price = 200, auto_reduce = True, price_reduction = reduction, repost_count = 3, min_price = None)


@pytest.mark.unit
def test_feature_disabled_path_leaves_price_unchanged() -> None:
    reduction = PriceReductionConfig(type = "PERCENTAGE", value = 25)
    price = calculate_auto_price(base_price = 100, auto_reduce = False, price_reduction = reduction, repost_count = 4, min_price = 40)
    assert price == 100


@pytest.mark.unit
def test_apply_auto_price_reduction_logs_drop(
    caplog:pytest.LogCaptureFixture,
    apply_auto_price_reduction:_ApplyAutoPriceReduction
) -> None:
    ad_cfg = SimpleNamespace(
        auto_reduce_price = True,
        price = 200,
        price_reduction = PriceReductionConfig(type = "PERCENTAGE", value = 25),
        repost_count = 1,
        min_price = 50
    )

    ad_orig:dict[str, Any] = {}

    with caplog.at_level("INFO"):
        apply_auto_price_reduction(ad_cfg, ad_orig, "ad_test.yaml")

    expected = _("Auto price reduction applied: %s -> %s after %s reposts") % (200, 150, 1)
    assert any(expected in message for message in caplog.messages)
    assert ad_cfg.price == 150


@pytest.mark.unit
def test_apply_auto_price_reduction_logs_unchanged_price(
    caplog:pytest.LogCaptureFixture,
    apply_auto_price_reduction:_ApplyAutoPriceReduction
) -> None:
    ad_cfg = SimpleNamespace(
        auto_reduce_price = True,
        price = 120,
        price_reduction = PriceReductionConfig(type = "PERCENTAGE", value = 25),
        repost_count = 0,
        min_price = 120
    )

    ad_orig:dict[str, Any] = {}

    with caplog.at_level("INFO"):
        apply_auto_price_reduction(ad_cfg, ad_orig, "ad_test.yaml")

    expected = _("Auto price reduction using unchanged price %s after %s reposts") % (120, 0)
    assert any(expected in message for message in caplog.messages)
    assert ad_cfg.price == 120


@pytest.mark.unit
def test_apply_auto_price_reduction_warns_when_price_missing(
    caplog:pytest.LogCaptureFixture,
    apply_auto_price_reduction:_ApplyAutoPriceReduction
) -> None:
    ad_cfg = SimpleNamespace(
        auto_reduce_price = True,
        price = None,
        price_reduction = PriceReductionConfig(type = "PERCENTAGE", value = 25),
        repost_count = 2,
        min_price = 10
    )

    ad_orig:dict[str, Any] = {}

    with caplog.at_level("WARNING"):
        apply_auto_price_reduction(ad_cfg, ad_orig, "ad_warning.yaml")

    expected = _("Auto price reduction is enabled for [%s] but no price is configured.") % ("ad_warning.yaml",)
    assert any(expected in message for message in caplog.messages)


@pytest.mark.unit
def test_apply_auto_price_reduction_respects_repost_delay(
    caplog:pytest.LogCaptureFixture,
    apply_auto_price_reduction:_ApplyAutoPriceReduction
) -> None:
    ad_cfg = SimpleNamespace(
        auto_reduce_price = True,
        price = 200,
        price_reduction = PriceReductionConfig(type = "PERCENTAGE", value = 25),
        price_reduction_count = 0,
        repost_count = 2,
        min_price = 50,
        price_reduction_delay_reposts = 3,
        price_reduction_delay_days = 0
    )

    ad_orig:dict[str, Any] = {}

    with caplog.at_level("INFO"):
        apply_auto_price_reduction(ad_cfg, ad_orig, "ad_delay.yaml")

    assert ad_cfg.price == 200
    delayed_message = _("Auto price reduction delayed for [%s]: waiting %s more reposts (completed %s, applied %s reductions)") % ("ad_delay.yaml", 2, 2, 0)
    assert any(delayed_message in message for message in caplog.messages)


@pytest.mark.unit
def test_apply_auto_price_reduction_after_repost_delay_reduces_once(
    apply_auto_price_reduction:_ApplyAutoPriceReduction
) -> None:
    ad_cfg = SimpleNamespace(
        auto_reduce_price = True,
        price = 100,
        price_reduction = PriceReductionConfig(type = "PERCENTAGE", value = 10),
        price_reduction_count = 0,
        repost_count = 3,
        min_price = 50,
        price_reduction_delay_reposts = 2,
        price_reduction_delay_days = 0
    )

    ad_cfg_orig:dict[str, Any] = {}
    apply_auto_price_reduction(ad_cfg, ad_cfg_orig, "ad_after_delay.yaml")

    assert ad_cfg.price == 90
    assert ad_cfg.price_reduction_count == 1
    assert ad_cfg_orig["price_reduction_count"] == 1


@pytest.mark.unit
def test_apply_auto_price_reduction_waits_when_reduction_already_applied(
    caplog:pytest.LogCaptureFixture,
    apply_auto_price_reduction:_ApplyAutoPriceReduction
) -> None:
    ad_cfg = SimpleNamespace(
        auto_reduce_price = True,
        price = 100,
        price_reduction = PriceReductionConfig(type = "PERCENTAGE", value = 10),
        price_reduction_count = 3,
        repost_count = 3,
        min_price = 50,
        price_reduction_delay_reposts = 0,
        price_reduction_delay_days = 0
    )

    ad_orig:dict[str, Any] = {}

    with caplog.at_level("INFO"):
        apply_auto_price_reduction(ad_cfg, ad_orig, "ad_already.yaml")

    expected = _("Auto price reduction already applied for [%s]: %s reductions match %s eligible reposts") % ("ad_already.yaml", 3, 3)
    assert any(expected in message for message in caplog.messages)
    assert ad_cfg.price == 100
    assert ad_cfg.price_reduction_count == 3
    assert "price_reduction_count" not in ad_orig


@pytest.mark.unit
def test_apply_auto_price_reduction_respects_day_delay(
    monkeypatch:pytest.MonkeyPatch,
    caplog:pytest.LogCaptureFixture,
    apply_auto_price_reduction:_ApplyAutoPriceReduction
) -> None:
    reference = datetime(2025, 1, 1, tzinfo = timezone.utc)
    ad_cfg = SimpleNamespace(
        auto_reduce_price = True,
        price = 150,
        price_reduction = PriceReductionConfig(type = "PERCENTAGE", value = 25),
        price_reduction_count = 0,
        repost_count = 1,
        min_price = 50,
        price_reduction_delay_reposts = 0,
        price_reduction_delay_days = 3,
        updated_on = reference,
        created_on = reference
    )

    monkeypatch.setattr("kleinanzeigen_bot.misc.now", lambda: reference + timedelta(days = 1))

    ad_orig:dict[str, Any] = {}

    with caplog.at_level("INFO"):
        apply_auto_price_reduction(ad_cfg, ad_orig, "ad_delay_days.yaml")

    assert ad_cfg.price == 150
    delayed_message = _("Auto price reduction delayed for [%s]: waiting %s days (elapsed %s)") % ("ad_delay_days.yaml", 3, 1)
    assert any(delayed_message in message for message in caplog.messages)


@pytest.mark.unit
def test_apply_auto_price_reduction_runs_after_delays(
    monkeypatch:pytest.MonkeyPatch,
    apply_auto_price_reduction:_ApplyAutoPriceReduction
) -> None:
    reference = datetime(2025, 1, 1, tzinfo = timezone.utc)
    ad_cfg = SimpleNamespace(
        auto_reduce_price = True,
        price = 120,
        price_reduction = PriceReductionConfig(type = "PERCENTAGE", value = 25),
        price_reduction_count = 0,
        repost_count = 3,
        min_price = 60,
        price_reduction_delay_reposts = 2,
        price_reduction_delay_days = 3,
        updated_on = reference - timedelta(days = 5),
        created_on = reference - timedelta(days = 10)
    )

    monkeypatch.setattr("kleinanzeigen_bot.misc.now", lambda: reference)

    ad_orig:dict[str, Any] = {}
    apply_auto_price_reduction(ad_cfg, ad_orig, "ad_ready.yaml")

    assert ad_cfg.price == 90


@pytest.mark.unit
def test_apply_auto_price_reduction_delayed_when_timestamp_missing(
    caplog:pytest.LogCaptureFixture,
    apply_auto_price_reduction:_ApplyAutoPriceReduction
) -> None:
    ad_cfg = SimpleNamespace(
        auto_reduce_price = True,
        price = 200,
        price_reduction = PriceReductionConfig(type = "FIXED", value = 20),
        price_reduction_count = 0,
        repost_count = 1,
        min_price = 50,
        price_reduction_delay_reposts = 0,
        price_reduction_delay_days = 2,
        updated_on = None,
        created_on = None
    )

    ad_orig:dict[str, Any] = {}

    with caplog.at_level("INFO"):
        apply_auto_price_reduction(ad_cfg, ad_orig, "ad_missing_time.yaml")

    expected = _("Auto price reduction delayed for [%s]: waiting %s days but publish timestamp missing") % ("ad_missing_time.yaml", 2)
    assert any(expected in message for message in caplog.messages)
>>>>>>> 764f76a (test: simplify auto price reduction fixture)
