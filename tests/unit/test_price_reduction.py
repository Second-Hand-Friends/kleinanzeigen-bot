# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
from gettext import gettext as _
from types import SimpleNamespace
from typing import Any, Protocol, cast

import pytest

from kleinanzeigen_bot import KleinanzeigenBot
from kleinanzeigen_bot.model.ad_model import calculate_auto_price
from kleinanzeigen_bot.model.config_model import PriceReductionConfig


class _ApplyAutoPriceReduction(Protocol):
    def __call__(self, ad_cfg:Any, ad_file_relative:str) -> None: ...


def test_initial_posting_uses_base_price() -> None:
    reduction = PriceReductionConfig(type = "percentage", value = 10)
    assert calculate_auto_price(
        base_price = 100,
        auto_reduce = True,
        price_reduction = reduction,
        repost_count = 0,
        min_price = 50
    ) == 100


def test_percentage_price_reduction_over_time() -> None:
    reduction = PriceReductionConfig(type = "percentage", value = 10)
    assert calculate_auto_price(base_price = 100, auto_reduce = True, price_reduction = reduction, repost_count = 1, min_price = 50) == 90
    assert calculate_auto_price(base_price = 100, auto_reduce = True, price_reduction = reduction, repost_count = 2, min_price = 50) == 81
    assert calculate_auto_price(base_price = 100, auto_reduce = True, price_reduction = reduction, repost_count = 3, min_price = 50) == 73


def test_fixed_price_reduction_over_time() -> None:
    reduction = PriceReductionConfig(type = "fixed", value = 15)
    assert calculate_auto_price(base_price = 100, auto_reduce = True, price_reduction = reduction, repost_count = 1, min_price = 40) == 85
    assert calculate_auto_price(base_price = 100, auto_reduce = True, price_reduction = reduction, repost_count = 2, min_price = 40) == 70
    assert calculate_auto_price(base_price = 100, auto_reduce = True, price_reduction = reduction, repost_count = 3, min_price = 40) == 55


def test_min_price_boundary_is_respected() -> None:
    reduction = PriceReductionConfig(type = "fixed", value = 20)
    assert calculate_auto_price(base_price = 100, auto_reduce = True, price_reduction = reduction, repost_count = 5, min_price = 50) == 50


def test_missing_min_price_defaults_to_base_price_floor() -> None:
    reduction = PriceReductionConfig(type = "percentage", value = 50)
    assert calculate_auto_price(base_price = 200, auto_reduce = True, price_reduction = reduction, repost_count = 3, min_price = None) == 200


def test_feature_disabled_path_leaves_price_unchanged() -> None:
    reduction = PriceReductionConfig(type = "percentage", value = 25)
    price = calculate_auto_price(base_price = 100, auto_reduce = False, price_reduction = reduction, repost_count = 4, min_price = 40)
    assert price == 100


def test_apply_auto_price_reduction_logs_drop(caplog:pytest.LogCaptureFixture) -> None:
    bot = KleinanzeigenBot()
    ad_cfg = SimpleNamespace(
        auto_reduce_price = True,
        price = 200,
        price_reduction = PriceReductionConfig(type = "percentage", value = 25),
        repost_count = 1,
        min_price = 50
    )

    apply_method = cast(_ApplyAutoPriceReduction, getattr(bot, "_KleinanzeigenBot__apply_auto_price_reduction"))

    with caplog.at_level("INFO"):
        apply_method(ad_cfg, "ad_test.yaml")

    expected = _("Auto price reduction applied: %s -> %s after %s reposts") % (200, 150, 1)
    assert any(expected in message for message in caplog.messages)
    assert ad_cfg.price == 150


def test_apply_auto_price_reduction_logs_unchanged_price(caplog:pytest.LogCaptureFixture) -> None:
    bot = KleinanzeigenBot()
    ad_cfg = SimpleNamespace(
        auto_reduce_price = True,
        price = 120,
        price_reduction = PriceReductionConfig(type = "percentage", value = 25),
        repost_count = 0,
        min_price = None
    )

    apply_method = cast(_ApplyAutoPriceReduction, getattr(bot, "_KleinanzeigenBot__apply_auto_price_reduction"))

    with caplog.at_level("INFO"):
        apply_method(ad_cfg, "ad_test.yaml")

    expected = _("Auto price reduction using unchanged price %s after %s reposts") % (120, 0)
    assert any(expected in message for message in caplog.messages)
    assert ad_cfg.price == 120
