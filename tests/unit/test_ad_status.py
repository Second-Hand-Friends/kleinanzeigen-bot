# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Tests for ad_status module — pure computation, no disk I/O in unit tests."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

import pytest

from kleinanzeigen_bot.ad_status import (
    StatusRow,
    build_status_rows,
    compute_ad_status,
    render_status_rows,
)
from kleinanzeigen_bot.app import KleinanzeigenBot
from kleinanzeigen_bot.cli import parse_args
from kleinanzeigen_bot.model.ad_model import Ad, AdPartial
from kleinanzeigen_bot.model.config_model import Config
from kleinanzeigen_bot.runtime_config import VALID_COMMANDS, RuntimeState
from kleinanzeigen_bot.utils import xdg_paths

pytestmark = pytest.mark.unit

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

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
    assert rows[0].status == "disabled"
    assert rows[1].status == "draft"


# --------------------------------------------------------------------------- #
# render_status_rows
# --------------------------------------------------------------------------- #


class TestRenderStatusRows:
    def test_empty(self) -> None:
        assert not render_status_rows([])

    def test_headers_and_rows(self) -> None:
        rows = [
            StatusRow(title = "Ad A", ad_id = "-", status = "draft"),
            StatusRow(title = "Ad B", ad_id = "123", status = "published-local"),
        ]
        output = render_status_rows(rows)

        # Contains ASCII table borders
        assert "+" in output
        assert "-" in output
        assert "|" in output

        # Contains headers
        assert "Ad ID" in output
        assert "Title" in output
        assert "Status" in output

        # Contains row data
        assert "-" in output
        assert "123" in output
        assert "Ad A" in output
        assert "Ad B" in output

        # Contains summary line
        assert "draft:" in output.casefold()
        assert "published-local:" in output.casefold()

        # Summary ends with total count
        assert "total" in output.casefold() or "gesamt" in output.casefold()

    # ------------------------------------------------------------------ #
    # Colour rendering
    # ------------------------------------------------------------------ #

    def test_render_uncoloured_unchanged(self) -> None:
        """Uncoloured output matches the default (color=False)."""
        rows = [
            StatusRow(title = "Item", ad_id = "1", status = "draft"),
            StatusRow(title = "Thing", ad_id = "2", status = "published-local"),
        ]
        assert render_status_rows(rows) == render_status_rows(rows, color = False)

    def test_render_coloured_contains_ansi(self) -> None:
        """Coloured output includes ANSI escape sequences."""
        rows = [StatusRow(title = "Test", ad_id = "42", status = "changed")]
        output = render_status_rows(rows, color = True)
        assert "\x1b[" in output

    def test_render_coloured_stripped_equals_uncoloured(self) -> None:
        """Stripping ANSI from coloured output produces identical text to uncoloured."""
        rows = [
            StatusRow(title = "Sofa", ad_id = "-", status = "draft"),
            StatusRow(title = "Chair", ad_id = "99", status = "published-local"),
            StatusRow(title = "Table", ad_id = "55", status = "changed"),
            StatusRow(title = "Lamp", ad_id = "33", status = "due"),
            StatusRow(title = "Rug", ad_id = "11", status = "disabled"),
        ]

        plain = render_status_rows(rows, color = False)
        coloured = render_status_rows(rows, color = True)
        stripped = _ANSI_RE.sub("", coloured)

        assert stripped == plain, (
            "Stripped coloured output must exactly match uncoloured output"
        )

    def test_render_coloured_only_status_column(self) -> None:
        """Only the status column contains ANSI codes; headers and separators are plain."""
        rows = [StatusRow(title = "Desk", ad_id = "7", status = "due")]
        output = render_status_rows(rows, color = True)

        # Header row should not contain ANSI
        header_line = output.splitlines()[1]
        assert "\x1b[" not in header_line, "Header must not be coloured"

        # Separator lines should not contain ANSI
        sep_lines = [line for line in output.splitlines() if line.startswith("+")]
        for sep in sep_lines:
            assert "\x1b[" not in sep, "Separators must not be coloured"

        # The status column cell (last pipe segment) should contain ANSI
        data_line = output.splitlines()[3]
        cells = [c.strip() for c in data_line.split("|") if c.strip()]
        assert len(cells) >= 3, "Expected at least 3 cells (id, title, status)"
        status_cell = cells[-1]
        assert "\x1b[" in status_cell, "Status cell should be coloured"

    def test_render_colour_only_mapped_statuses(self) -> None:
        """Only known status values get colour; unmapped statuses are unchanged."""
        rows = [StatusRow(title = "?iss", ad_id = "0", status = "unknown")]
        plain = render_status_rows(rows, color = False)
        coloured = render_status_rows(rows, color = True)
        assert plain == coloured, "Unmapped status should not be coloured"


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
        patch("kleinanzeigen_bot.ad_loading.load_ad_configs", return_value = []),
        patch("kleinanzeigen_bot.ad_status.render_status_rows", return_value = ""),
        patch("kleinanzeigen_bot.utils.color.should_use_color", return_value = False),
    ):
        await bot.run(["app", "status"])

    assert update_called, "status must call _check_for_updates()"
    assert not load_ads_called, "status must not call load_ads()"
    assert not browser_called, "status must not open browser or login"


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
