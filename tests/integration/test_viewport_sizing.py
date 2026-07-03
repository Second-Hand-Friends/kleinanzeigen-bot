# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Integration test for screen-aware viewport sizing.

Launches a real browser, reads CSS-pixel screen dimensions, verifies that
viewport-size selection respects available screen area, and that an oversized
candidate is correctly excluded.

.. warning::
   This test starts a **real browser window** for a few seconds.  It is
   marked ``itest`` and ``slow`` so it is excluded from the default (unit-only)
   test run.
"""
from __future__ import annotations

import os
import platform
import shutil
import tempfile
from typing import Any, cast
from unittest.mock import patch

import pytest

from kleinanzeigen_bot.model.config_model import Config, HumanizationConfig
from kleinanzeigen_bot.utils.web_scraping_mixin import WebScrapingMixin

pytestmark = [pytest.mark.slow, pytest.mark.itest]

_HTML_BLANK = "about:blank"


def _make_bare_config(humanization:HumanizationConfig | None = None) -> Config:
    """Return a minimal ``Config`` with the given humanization settings."""
    return Config.model_validate({
        "login": {"username": "test@example.com", "password": "secret"},  # noqa: S106
        "humanization": (humanization or HumanizationConfig()).model_dump(),
    })


def _has_browser() -> bool:
    """Return True if a compatible browser binary is available on this host."""
    try:
        return bool(WebScrapingMixin().get_compatible_browser())
    except (AssertionError, RuntimeError, OSError):
        return False


def _setup_mixin() -> WebScrapingMixin:
    """Create a configured ``WebScrapingMixin`` for integration testing."""
    mixin = WebScrapingMixin()
    if platform.system() == "Linux":
        mixin.browser_config.arguments.append("--no-sandbox")
    mixin.browser_config.binary_location = mixin.get_compatible_browser()
    return mixin


def _display_available() -> bool:
    if platform.system() == "Linux" or os.environ.get("CI", "").lower() == "true":
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    return True


async def _collect_or_skip_metrics(mixin:WebScrapingMixin) -> tuple[int, int]:
    metrics = await mixin._collect_current_viewport_metrics()
    if not isinstance(metrics, dict):
        pytest.skip("Screen metrics unavailable")
    avail_w = metrics.get("availWidth")
    avail_h = metrics.get("availHeight")
    if not isinstance(avail_w, (int, float)) or not isinstance(avail_h, (int, float)) or avail_w <= 0 or avail_h <= 0:
        pytest.skip(f"Screen metrics unavailable: {metrics!r}")
    return int(avail_w), int(avail_h)


# ---------------------------------------------------------------------------
# post-open viewport resize
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.skipif(not _has_browser(), reason = "No compatible browser binary detected")
@pytest.mark.skipif(not _display_available(), reason = "No real display/window manager available")
async def test_web_open_triggers_post_open_viewport_resize() -> None:
    """A real browser is resized after page open, with no temporary probe browser."""
    mixin = _setup_mixin()

    ud_dir = tempfile.mkdtemp(prefix = "kbb-vptest-")
    mixin.browser_config.user_data_dir = ud_dir
    mixin.config = _make_bare_config(HumanizationConfig(
        randomize_viewport = True,
        viewport_sizes = ["32000x32000", "900x700"],
    ))

    try:
        try:
            await mixin.create_browser_session()
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"Browser failed to start in this environment: {exc}")
        with patch.object(mixin, "_is_kleinanzeigen_page", return_value = True):
            await mixin.web_open(_HTML_BLANK)

        status = mixin.get_viewport_resize_status()
        assert status["status"] in {"applied", "no-fitting-size"}
        assert status["attempted"] is True
        if status["status"] == "applied":
            assert status["applied"] is True
            assert status["selected_viewport"]
            avail_w, avail_h = await _collect_or_skip_metrics(mixin)
            assert avail_w > 0
            assert avail_h > 0
    finally:
        mixin.close_browser_session()
        shutil.rmtree(ud_dir, ignore_errors = True)


@pytest.mark.asyncio
async def test_select_viewport_size_for_metrics_returns_none_when_all_oversized() -> None:
    """When all configured viewports exceed screen metrics, no fallback size is selected."""
    mixin = WebScrapingMixin()
    mixin.config = _make_bare_config(HumanizationConfig(randomize_viewport = True, viewport_sizes = ["5000x5000", "6000x6000"]))

    selected = mixin._select_viewport_size_for_metrics((1920, 1080))

    assert selected is None


@pytest.mark.asyncio
async def test_resize_status_remains_not_attempted_for_non_matching_url() -> None:
    """The post-open hook ignores non-kleinanzeigen pages without marking an attempt."""
    mixin = WebScrapingMixin()
    mixin.config = _make_bare_config(HumanizationConfig(randomize_viewport = True, viewport_sizes = ["900x700"]))
    mixin.page = cast(Any, type("Page", (), {"url": "about:blank"})())

    await mixin._resize_viewport_after_open()

    assert mixin.get_viewport_resize_status()["status"] == "not-attempted"
    assert mixin.get_viewport_resize_status()["attempted"] is False
