# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Tests for ad_status module — pure computation, no disk I/O in unit tests."""

from __future__ import annotations

import copy
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

import pytest

import kleinanzeigen_bot.price_reduction as _pr_mod  # noqa: PLC0414 — module import for patching in tests
from kleinanzeigen_bot.ad_status import (
    AprDetail,
    StatusRow,
    build_status_rows,
    compute_ad_status,
    render_status_rows,
)
from kleinanzeigen_bot.app import KleinanzeigenBot
from kleinanzeigen_bot.cli import parse_args
from kleinanzeigen_bot.model.ad_model import Ad, AdPartial, AdUpdateStrategy
from kleinanzeigen_bot.model.config_model import Config
from kleinanzeigen_bot.runtime_config import VALID_COMMANDS, RuntimeState
from kleinanzeigen_bot.utils import xdg_paths

pytestmark = pytest.mark.unit

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(value:str) -> str:
    return _ANSI_RE.sub("", value)


_TZ = timezone.utc


def _now() -> datetime:
    """Return a timezone-aware 'now' for reproducible tests."""
    return datetime.now(tz = _TZ)


def days_ago(n:int) -> datetime:
    """Return *n* days before :func:`_now`."""
    return _now() - timedelta(days = n)


def _ad(**overrides:Any) -> Ad:
    """Helper: build an Ad with all required fields."""
    defaults:dict[str, Any] = {
        "active": True,
        "title": "test ad title",
        "type": "OFFER",
        "price": 10,
        "description": "desc",
        "category": "Möbel",
        "shipping_type": "PICKUP",
        "price_type": "FIXED",
        "sell_directly": False,
        "contact": {"name": "Test", "zipcode": "12345"},
        "republication_interval": 7,
    }
    defaults.update(overrides)
    return Ad(**defaults)


def _raw(**overrides:Any) -> dict[str, Any]:
    """Helper: build a raw YAML dict with required fields."""
    d:dict[str, Any] = {
        "title": "test ad title",
        "description": "desc",
        "category": "Möbel",
    }
    d.update(overrides)
    return d


def _content_hash(raw:dict[str, Any]) -> str:
    """Compute the actual content_hash for a raw dict."""
    result = AdPartial.model_validate(raw).update_content_hash().content_hash
    assert result is not None
    return result


# --------------------------------------------------------------------------- #
# compute_ad_status — precedence
# --------------------------------------------------------------------------- #


def test_disabled() -> None:
    """Disabled beats everything, even with id and matching hash."""
    ad = _ad(active = False, id = 123, content_hash = "abc")
    raw = _raw(content_hash = "abc")
    assert compute_ad_status(ad, raw) == "disabled"


def test_draft() -> None:
    """Draft when id is None."""
    ad = _ad(active = True, id = None)
    raw = _raw()
    assert compute_ad_status(ad, raw) == "draft"


def test_changed() -> None:
    """Changed when stored hash exists, non-empty, and differs."""
    ad = _ad(active = True, id = 1, content_hash = "old_hash")
    raw = _raw(content_hash = "old_hash", title = "different title to trigger hash change")
    result = compute_ad_status(ad, raw)
    assert result == "changed"


def test_changed_no_hash_not_changed() -> None:
    """Missing stored hash is NOT changed — falls through."""
    ad = _ad(active = True, id = 1, content_hash = None, created_on = _now())
    raw = _raw()
    result = compute_ad_status(ad, raw, now = _now())
    assert result != "changed"
    assert result == "published-local"


def test_changed_no_id_not_changed() -> None:
    """No id means draft before hash check."""
    ad = _ad(active = True, id = None, content_hash = "some")
    raw = _raw(content_hash = "some")
    assert compute_ad_status(ad, raw) == "draft"


def test_changed_empty_hash_not_changed() -> None:
    """Empty string stored hash is NOT changed (same as missing)."""
    ad = _ad(active = True, id = 1, content_hash = "", created_on = _now())
    raw = _raw(content_hash = "")
    result = compute_ad_status(ad, raw, now = _now())
    assert result == "published-local"


def test_due_interval_elapsed() -> None:
    """Due when republication_interval elapsed since created_on."""
    raw = _raw()
    ch = _content_hash(raw)
    ad = _ad(
        active = True,
        id = 1,
        content_hash = ch,
        created_on = days_ago(30),
        republication_interval = 7,
    )
    result = compute_ad_status(ad, raw, now = _now())
    assert result == "due"


def test_due_no_dates() -> None:
    """Due when both updated_on and created_on are None."""
    raw = _raw()
    ch = _content_hash(raw)
    ad = _ad(
        active = True,
        id = 1,
        content_hash = ch,
        updated_on = None,
        created_on = None,
        republication_interval = 7,
    )
    result = compute_ad_status(ad, raw, now = _now())
    assert result == "due"


def test_due_not_due() -> None:
    """Not due when republication_interval has not elapsed."""
    raw = _raw()
    ch = _content_hash(raw)
    ad = _ad(
        active = True,
        id = 1,
        content_hash = ch,
        created_on = days_ago(1),
        republication_interval = 7,
    )
    result = compute_ad_status(ad, raw, now = _now())
    assert result == "published-local"


def test_published_local() -> None:
    """published-local when id exists and nothing else applies."""
    raw = _raw()
    ch = _content_hash(raw)
    ad = _ad(
        active = True,
        id = 1,
        content_hash = ch,
        created_on = _now(),
        republication_interval = 7,
    )
    result = compute_ad_status(ad, raw, now = _now())
    assert result == "published-local"


# --------------------------------------------------------------------------- #
# build_status_rows
# --------------------------------------------------------------------------- #


def test_build_status_rows() -> None:
    """build_status_rows produces a StatusRow per ad."""
    ads:list[tuple[str, Ad, dict[str, Any]]] = [
        ("ads/one.yaml", _ad(active = False, id = 123), _raw()),
        ("ads/two.yaml", _ad(active = True, id = None), _raw()),
    ]
    rows = build_status_rows(ads, now = _now())
    assert len(rows) == 2
    assert rows[0].filename == "ads/one.yaml"
    assert rows[1].filename == "ads/two.yaml"
    assert rows[0].status == "disabled"
    assert rows[1].status == "draft"


# --------------------------------------------------------------------------- #
# render_status_rows
# --------------------------------------------------------------------------- #


class TestRenderStatusRows:
    def test_empty(self) -> None:
        assert not render_status_rows([])

    def test_per_ad_layout_keeps_status_apr_and_path_together(self) -> None:
        rows = [
            StatusRow(
                title = "Ad A",
                ad_id = "-",
                filename = "data/my_ads/ad_000001_Test/ad_000001.yaml",
                status = "draft",
            ),
            StatusRow(
                title = "Ad B",
                ad_id = "123",
                filename = "b.yaml",
                status = "published-local",
                apr_update_detail = AprDetail(
                    result_key = "no_new_reduction",
                    result = "no new reduction",
                    effective_price = 10,
                    reason_key = "day_delay_waiting",
                    reason = "waiting for day delay",
                ),
                apr_repost_detail = AprDetail(
                    result_key = "price_reduction",
                    result = "price reduction",
                    effective_price = 9,
                    reason_key = "eligible",
                    reason = "eligible",
                    price_before = 10,
                    price_after = 9,
                    cycle = 1,
                ),
            ),
        ]
        output = render_status_rows(rows)

        assert "Ad status" not in output
        assert "APR details" not in output
        assert "Ad A" in output
        assert "Ad B" in output
        assert "data/my_ads/ad_000001_Test/ad_000001.yaml" in output
        assert "b.yaml" in output
        assert "title:" in output
        assert "id:" in output
        assert "status:" in output
        assert "APR update" in output
        assert "APR publish" in output

        ad_b_start = output.index("Ad B")
        assert output.index(
            "  status:",
            ad_b_start) < output.index(
            "  APR update:",
            ad_b_start) < output.index(
            "  APR publish:",
            ad_b_start) < output.index(
                "Summary:",
        )

        assert "draft" in output.casefold()
        assert "published-local" in output.casefold()
        assert "total" in output.casefold() or "gesamt" in output.casefold()

    # ------------------------------------------------------------------ #
    # Colour rendering
    # ------------------------------------------------------------------ #

    def test_render_uncoloured_unchanged(self) -> None:
        """Uncoloured output matches the default (color=False)."""
        rows = [
            StatusRow(title = "Item", ad_id = "1", filename = "a.yaml", status = "draft"),
            StatusRow(title = "Thing", ad_id = "2", filename = "b.yaml", status = "published-local"),
        ]
        assert render_status_rows(rows) == render_status_rows(rows, color = False)

    def test_render_coloured_contains_ansi(self) -> None:
        """Coloured output includes ANSI escape sequences."""
        rows = [StatusRow(title = "Test", ad_id = "42", filename = "t.yaml", status = "changed")]
        output = render_status_rows(rows, color = True)
        assert "\x1b[" in output

    def test_render_coloured_stripped_equals_uncoloured(self) -> None:
        """Coloured and uncoloured output contain the same status data."""
        rows = [
            StatusRow(title = "Sofa", ad_id = "-", filename = "sofa.yaml", status = "draft"),
            StatusRow(title = "Chair", ad_id = "99", filename = "chair.yaml", status = "published-local"),
            StatusRow(title = "Table", ad_id = "55", filename = "table.yaml", status = "changed"),
            StatusRow(title = "Lamp", ad_id = "33", filename = "lamp.yaml", status = "due"),
            StatusRow(title = "Rug", ad_id = "11", filename = "rug.yaml", status = "disabled"),
        ]

        plain = render_status_rows(rows, color = False)
        coloured = render_status_rows(rows, color = True)
        stripped = _strip_ansi(coloured)

        for expected in ("Sofa", "Chair", "changed", "due", "disabled"):
            assert expected in plain
            assert expected in stripped

    def test_render_coloured_contains_status_style(self) -> None:
        """Status colour styling is present when enabled."""
        rows = [StatusRow(title = "Desk", ad_id = "7", filename = "desk.yaml", status = "due")]
        output = render_status_rows(rows, color = True)
        assert "\x1b[" in output
        assert "due" in _strip_ansi(output)

    def test_render_colour_only_mapped_statuses(self) -> None:
        """Only known status values get colour; unmapped statuses are unchanged."""
        rows = [StatusRow(title = "?iss", ad_id = "0", filename = "q.yaml", status = "unknown")]
        plain = render_status_rows(rows, color = False)
        coloured = render_status_rows(rows, color = True)
        stripped = _strip_ansi(coloured)
        assert "unknown" in plain
        assert "unknown" in stripped


# --------------------------------------------------------------------------- #
# APR column rendering
# --------------------------------------------------------------------------- #


class TestAprRendering:
    """APR details: presence, formatting, and block placement."""

    # -- render: columns absent when no active APR ------------------------- #

    def test_no_apr_no_columns(self) -> None:
        """APR details absent when no effective APR is configured."""
        rows = [
            StatusRow(title = "A", ad_id = "1", filename = "a.yaml", status = "published-local"),
            StatusRow(title = "B", ad_id = "2", filename = "b.yaml", status = "draft"),
        ]
        output = render_status_rows(rows)
        assert "APR update" not in output
        assert "APR publish" not in output

    def test_apr_details_stay_under_the_relevant_ad(self) -> None:
        """APR belongs under the relevant ad block, not in a separate table."""
        rows = [
            StatusRow(
                title = "A",
                ad_id = "1",
                filename = "ads/a.yaml",
                status = "published-local",
                apr_repost_detail = AprDetail(
                    result_key = "no_new_reduction",
                    result = "no new reduction",
                    effective_price = 10,
                    reason_key = "day_delay_waiting",
                    reason = "waiting for day delay",
                )),
        ]

        output = render_status_rows(rows)

        assert "APR details" not in output
        assert "APR update" not in output
        assert "APR publish" in output
        assert "10" in output
        assert "no new" in output
        assert "no new reduction" in output
        assert "waiting for" in output
        assert "ads/a.yaml" in output

    # -- render: APR unaffected by colour ---------------------------------- #

    def test_apr_details_coloured_output_remains_readable(self) -> None:
        """APR detail values remain readable when color is enabled."""
        rows = [
            StatusRow(
                title = "A",
                ad_id = "1",
                filename = "ads/a.yaml",
                status = "published-local",
                apr_repost_detail = AprDetail(
                    result_key = "price_reduction",
                    result = "price reduction",
                    effective_price = 9,
                    reason_key = "eligible",
                    reason = "eligible",
                    price_before = 10,
                    price_after = 9,
                    cycle = 1,
                ),
            ),
        ]
        output = render_status_rows(rows, color = True)
        stripped = _strip_ansi(output)
        assert "price reduction" in stripped
        assert "10 -> 9" in stripped
        assert "cycle: 1" in stripped

    # -- render: coloured APR output -------------------------------------- #

    def test_apr_coloured_stripped_equals_plain(self) -> None:
        """With APR details, coloured output preserves APR detail text."""
        rows = [
            StatusRow(
                title = "A", ad_id = "1", filename = "ads/a.yaml",
                status = "published-local",
                apr_repost_detail = AprDetail(
                    result_key = "price_reduction",
                    result = "price reduction",
                    effective_price = 9,
                    reason_key = "eligible",
                    reason = "eligible",
                    price_before = 10,
                    price_after = 9,
                    cycle = 1,
                ),
            ),
        ]
        plain = render_status_rows(rows, color = False)
        coloured = render_status_rows(rows, color = True)
        stripped = _strip_ansi(coloured)
        for expected in ("price reduction", "10 -> 9", "cycle: 1", "published-local"):
            assert expected in plain
            assert expected in stripped

    def test_apr_due_transition_shows_restored_to_result_price(self) -> None:
        """Due APR details show the old→new price transition."""
        decision = _pr_mod.PriceReductionDecision(
            mode = AdUpdateStrategy.REPLACE,
            enabled = True,
            on_update = False,
            base_price = 20,
            restored_price = 15,
            result_price = 10,
            applied_cycles = 1,
            next_cycle = 2,
            cycle_advanced = True,
            reason = "cycle_advanced",
            total_reposts = 0,
            delay_reposts = 0,
            eligible_cycles = 1,
            delay_days = 0,
            elapsed_days = None,
            reference = None,
            delay_reposts_ignored = False,
        )

        with patch.object(_pr_mod, "evaluate_auto_price_reduction", return_value = decision):
            rows = build_status_rows([("ads/a.yaml", _ad(id = 1, price = 20), _raw())], now = _now())

        output = render_status_rows(rows)
        assert "price reduction" in output
        assert "15 -> 10" in output

    def test_apr_detail_uses_structured_result_price_and_reason(self) -> None:
        """APR details keep result, price, and reason visible in one line."""
        decision = _pr_mod.PriceReductionDecision(
            mode = AdUpdateStrategy.REPLACE,
            enabled = True,
            on_update = False,
            base_price = 20,
            restored_price = 20,
            result_price = 20,
            applied_cycles = 0,
            next_cycle = None,
            cycle_advanced = False,
            reason = "day_delay_waiting",
            total_reposts = 0,
            delay_reposts = 0,
            eligible_cycles = 0,
            delay_days = 7,
            elapsed_days = 3,
            reference = None,
            delay_reposts_ignored = False,
        )

        with patch.object(_pr_mod, "evaluate_auto_price_reduction", return_value = decision):
            rows = build_status_rows([("ads/a.yaml", _ad(id = 1, price = 20), _raw())], now = _now())

        assert rows[0].apr_repost_detail == AprDetail(
            result_key = "no_new_reduction",
            result = "no new reduction",
            effective_price = 20,
            reason_key = "day_delay_waiting",
            reason = "waiting for day delay",
        )

    def test_apr_detail_suppresses_unconfigured_apr(self) -> None:
        """Rows without configured APR do not create noisy APR detail tables."""
        decision = _pr_mod.PriceReductionDecision(
            mode = AdUpdateStrategy.REPLACE,
            enabled = False,
            on_update = False,
            base_price = 20,
            restored_price = None,
            result_price = None,
            applied_cycles = 0,
            next_cycle = None,
            cycle_advanced = False,
            reason = "not_configured",
            total_reposts = 0,
            delay_reposts = 0,
            eligible_cycles = 0,
            delay_days = 0,
            elapsed_days = None,
            reference = None,
            delay_reposts_ignored = False,
        )

        with patch.object(_pr_mod, "evaluate_auto_price_reduction", return_value = decision):
            rows = build_status_rows([("ads/a.yaml", _ad(id = 1, price = 20), _raw())], now = _now())

        output = render_status_rows(rows)
        assert "APR update" not in output
        assert "APR publish" not in output

    # -- build_status_rows: APR evaluation integration --------------------- #
    # These test that evaluate_auto_price_reduction is called correctly
    # by patching it and asserting call arguments.

    def test_apr_eval_called_for_active_published(self) -> None:
        """Active published row calls evaluator with REPLACE and MODIFY."""
        ad = _ad(active = True, id = 1)
        raw = _raw()
        with patch.object(_pr_mod, "evaluate_auto_price_reduction") as mock_eval:
            mock_eval.return_value = _pr_mod.PriceReductionDecision(
                mode = AdUpdateStrategy.REPLACE,
                enabled = False, on_update = False,
                base_price = None, restored_price = None, result_price = None,
                applied_cycles = 0, next_cycle = None, cycle_advanced = False,
                reason = "not_configured",
                total_reposts = 0, delay_reposts = 0, eligible_cycles = 0,
                delay_days = 0, elapsed_days = None, reference = None,
                delay_reposts_ignored = False,
            )
            build_status_rows([("ads/test.yaml", ad, raw)], now = _now())

        assert mock_eval.call_count == 2
        replace_call, modify_call = mock_eval.call_args_list
        # REPLACE call
        assert replace_call.kwargs["mode"] == AdUpdateStrategy.REPLACE
        assert replace_call.args[1] == "ads/test.yaml"
        # MODIFY call
        assert modify_call.kwargs["mode"] == AdUpdateStrategy.MODIFY
        assert modify_call.args[1] == "ads/test.yaml"

    def test_apr_eval_not_called_for_disabled(self) -> None:
        """Inactive/disabled row calls evaluator neither REPLACE nor MODIFY."""
        ad = _ad(active = False, id = 1)
        raw = _raw()
        with patch.object(_pr_mod, "evaluate_auto_price_reduction") as mock_eval:
            build_status_rows([("ads/test.yaml", ad, raw)], now = _now())
        mock_eval.assert_not_called()

    def test_apr_eval_draft_calls_replace_only(self) -> None:
        """Active draft (no id) calls REPLACE only, not MODIFY."""
        ad = _ad(active = True, id = None)
        raw = _raw()
        with patch.object(_pr_mod, "evaluate_auto_price_reduction") as mock_eval:
            mock_eval.return_value = _pr_mod.PriceReductionDecision(
                mode = AdUpdateStrategy.REPLACE,
                enabled = False, on_update = False,
                base_price = None, restored_price = None, result_price = None,
                applied_cycles = 0, next_cycle = None, cycle_advanced = False,
                reason = "not_configured",
                total_reposts = 0, delay_reposts = 0, eligible_cycles = 0,
                delay_days = 0, elapsed_days = None, reference = None,
                delay_reposts_ignored = False,
            )
            build_status_rows([("ads/test.yaml", ad, raw)], now = _now())

        assert mock_eval.call_count == 1
        only_call = mock_eval.call_args_list[0]
        assert only_call.kwargs["mode"] == AdUpdateStrategy.REPLACE

    def test_apr_eval_apply_not_called(self) -> None:
        """apply_auto_price_reduction is never called during build_status_rows."""
        ad = _ad(active = True, id = 1)
        raw = _raw()
        with patch.object(_pr_mod, "apply_auto_price_reduction") as mock_apply:
            build_status_rows([("ads/test.yaml", ad, raw)], now = _now())
            mock_apply.assert_not_called()

    def test_apr_eval_does_not_mutate_models(self) -> None:
        """Ad model and raw dict unchanged after build_status_rows."""
        ad = _ad(
            active = True,
            id = 1,
            price = 1000,
            repost_count = 1,
            auto_price_reduction = {
                "enabled": True,
                "strategy": "PERCENTAGE",
                "amount": 10,
                "min_price": 1,
                "delay_reposts": 0,
                "delay_days": 0,
            },
        )
        raw = _raw(price = 1000)
        ad_before = ad.model_dump(mode = "json")
        raw_before = copy.deepcopy(raw)
        build_status_rows([("ads/test.yaml", ad, raw)], now = _now())
        assert ad.model_dump(mode = "json") == ad_before, "Ad model should not be mutated"
        assert raw == raw_before, "Raw dict should not be mutated"


# --------------------------------------------------------------------------- #
# Guardrail: status does not call load_ads or open browser
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_status_guardrails(
    monkeypatch:pytest.MonkeyPatch,
    tmp_path:Any,
) -> None:
    """Status calls _check_for_updates but NOT load_ads / browser login."""
    bot = KleinanzeigenBot()
    bot.config_file_path = str(tmp_path / "config.yaml")
    workspace = xdg_paths.Workspace.for_config(tmp_path / "config.yaml", "kleinanzeigen-bot")
    dummy_config = Config()

    update_called:list[bool] = []
    load_ads_called:list[bool] = []
    browser_called:list[bool] = []

    def _track_update(*_args:Any, **_kwargs:Any) -> None:
        update_called.append(True)

    def _track_load_ads(*_args:Any, **_kwargs:Any) -> list[Any]:
        load_ads_called.append(True)
        return []

    def _track_browser(*_args:Any, **_kwargs:Any) -> None:
        browser_called.append(True)

    with (
        patch("kleinanzeigen_bot.runtime_config.resolve_workspace", return_value = workspace),
        patch(
            "kleinanzeigen_bot.runtime_config.load_config",
            return_value = RuntimeState(
                config = dummy_config, categories = {}, timing_collector = None,
            ),
        ),
        patch("kleinanzeigen_bot.runtime_config.configure_file_logging", return_value = None),
        patch("kleinanzeigen_bot.runtime_config.apply_browser_config"),
        patch("kleinanzeigen_bot.update_checker.UpdateChecker"),
        patch.object(bot, "_check_for_updates", side_effect = _track_update),
        patch.object(bot, "load_ads", side_effect = _track_load_ads),
        patch.object(bot, "create_browser_session", side_effect = _track_browser),
        patch.object(bot, "login", side_effect = _track_browser),
        patch.object(bot, "close_browser_session"),
        patch(
            "kleinanzeigen_bot.ad_loading.load_ad_configs",
            return_value = [("/abs/a.yaml", "ads/a.yaml", _ad(id = 1), _raw())],
        ),
        patch(
            "kleinanzeigen_bot.ad_status.build_status_rows",
            return_value = [StatusRow(title = "A", ad_id = "1", filename = "ads/a.yaml", status = "published-local")],
        ),
        patch("kleinanzeigen_bot.ad_status.render_status_rows", return_value = "") as render_status_rows_mock,
        patch("kleinanzeigen_bot.utils.color.should_use_color", return_value = False),
    ):
        await bot.run(["app", "status"])

    assert update_called, "status must call _check_for_updates()"
    assert not load_ads_called, "status must not call load_ads()"
    assert not browser_called, "status must not open browser or login"
    assert render_status_rows_mock.call_args.kwargs == {"color": False}


# --------------------------------------------------------------------------- #
# Parser / dispatch
# --------------------------------------------------------------------------- #


def test_status_is_valid_command() -> None:
    """Status is recognised by the parser."""
    parsed = parse_args(["app", "status"])
    assert parsed.command == "status"


def test_status_in_runtime_config() -> None:
    """Status is listed in VALID_COMMANDS."""
    assert "status" in VALID_COMMANDS
