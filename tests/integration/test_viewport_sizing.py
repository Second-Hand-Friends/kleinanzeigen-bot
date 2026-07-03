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


async def _probe_or_skip_metrics(mixin:WebScrapingMixin) -> tuple[int, int]:
    metrics = await mixin._probe_screen_metrics()
    if metrics is None:
        pytest.skip("Screen metrics unavailable")
    return metrics


def _viewport_fits(size:str, avail_w:int, avail_h:int) -> bool:
    """Return True if the WxH string fits within the given available area."""
    try:
        parts = size.lower().split("x")
        w = int(parts[0].strip())
        h = int(parts[1].strip())
        return w <= avail_w and h <= avail_h
    except (ValueError, IndexError):
        return False


# ---------------------------------------------------------------------------
# helper: probe screen metrics in an isolated browser
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.skipif(not _has_browser(), reason = "No compatible browser binary detected")
@pytest.mark.skipif(not _display_available(), reason = "No real display/window manager available")
async def test_probe_screen_metrics_returns_positive_dimensions() -> None:
    """The probe browser must report positive CSS-pixel availWidth/availHeight."""
    mixin = _setup_mixin()
    avail_w, avail_h = await _probe_or_skip_metrics(mixin)
    assert isinstance(avail_w, int)
    assert avail_w > 0, f"availWidth must be positive, got {avail_w!r}"
    assert isinstance(avail_h, int)
    assert avail_h > 0, f"availHeight must be positive, got {avail_h!r}"
    # Sanity: modern displays are usually at least 720p
    assert avail_h >= 720 or avail_w >= 1280, f"Screen seems too small: {avail_w}x{avail_h}"


# ---------------------------------------------------------------------------
# viewport selection: runtime filtering path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.skipif(not _has_browser(), reason = "No compatible browser binary detected")
@pytest.mark.skipif(not _display_available(), reason = "No real display/window manager available")
async def test_viewport_selection_respects_screen_size() -> None:
    """Configure one oversized and one fitting viewport; verify the oversized
    candidate is excluded by the runtime filtering path.
    """
    mixin = _setup_mixin()
    avail_w, avail_h = await _probe_or_skip_metrics(mixin)

    # 2. Build a viewport list with exactly one oversized candidate (> screen)
    #    and one that clearly fits (0.7 × screen).
    oversized = f"{avail_w + 200}x{avail_h + 100}"
    fitting = f"{int(avail_w * 0.7)}x{int(avail_h * 0.7)}"
    sizes = [oversized, fitting]

    # 3. Verify the static filter matches expectations.
    fitting_result = [s for s in sizes if _viewport_fits(s, avail_w, avail_h)]
    assert oversized not in fitting_result, f"{oversized} should be filtered out for {avail_w}x{avail_h} screen"
    assert fitting in fitting_result, f"{fitting} should fit {avail_w}x{avail_h} screen"

    # 4. Configure the mixin with these sizes and run the selection logic.
    mixin.config = _make_bare_config(HumanizationConfig(randomize_viewport = True, viewport_sizes = sizes))

    with patch.object(mixin, "_probe_screen_metrics", return_value = (avail_w, avail_h)):
        selected = await mixin._select_viewport_size()
    assert selected is not None, "At least the fitting size should be selected"
    # The selected viewport must fit within available screen when parsed.
    # (Jitter is bounded by avail so this always holds.)
    parts = selected.lower().split("x")
    sel_w, sel_h = int(parts[0].strip()), int(parts[1].strip())
    assert sel_w <= avail_w, f"Selected width {sel_w} exceeds available {avail_w}"
    assert sel_h <= avail_h, f"Selected height {sel_h} exceeds available {avail_h}"


@pytest.mark.asyncio
@pytest.mark.skipif(not _has_browser(), reason = "No compatible browser binary detected")
@pytest.mark.skipif(not _display_available(), reason = "No real display/window manager available")
async def test_viewport_selection_returns_none_when_all_oversized() -> None:
    """When *all* configured viewports exceed the available screen, selection
    must return ``None`` (meaning omit ``--window-size``).
    """
    mixin = _setup_mixin()
    avail_w, avail_h = await _probe_or_skip_metrics(mixin)

    # All sizes are way oversized.
    sizes = [f"{avail_w + 500}x{avail_h + 500}", f"{avail_w + 1000}x{avail_h + 1000}"]

    # Static filter must return empty.
    assert not any(_viewport_fits(s, avail_w, avail_h) for s in sizes)

    mixin.config = _make_bare_config(HumanizationConfig(randomize_viewport = True, viewport_sizes = sizes))
    with patch.object(mixin, "_probe_screen_metrics", return_value = (avail_w, avail_h)):
        selected = await mixin._select_viewport_size()
    assert selected is None, f"Expected None (no sizes fit {avail_w}x{avail_h}), got {selected!r}"


# ---------------------------------------------------------------------------
# full launch path: create_browser_session with screen-aware viewport
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.skipif(not _has_browser(), reason = "No compatible browser binary detected")
@pytest.mark.skipif(not _display_available(), reason = "No real display/window manager available")
async def test_create_browser_session_selects_fitting_viewport() -> None:
    """Start a browser via create_browser_session() with one oversized and one
    fitting viewport candidate, and verify the final window fits within the
    available screen area.  This exercises the complete launch path: probing,
    filtering, jitter, and ``--window-size`` injection.
    """
    mixin = _setup_mixin()
    avail_w, avail_h = await _probe_or_skip_metrics(mixin)

    # 2. Build viewport list: one oversized (exceeds screen), one clearly fitting.
    oversized = f"{avail_w + 200}x{avail_h + 100}"
    fitting = f"{int(avail_w * 0.7)}x{int(avail_h * 0.7)}"

    # 3. Assign a temporary user-data-dir and configure humanization.
    ud_dir = tempfile.mkdtemp(prefix = "kbb-vptest-")
    mixin.browser_config.user_data_dir = ud_dir
    mixin.config = _make_bare_config(HumanizationConfig(
        randomize_viewport = True,
        viewport_sizes = [oversized, fitting],
    ))

    try:
        # 4. Start the browser through the full session-creation path, reusing
        #    the already-probed metrics so a second flaky probe cannot fail CI.
        with patch.object(mixin, "_probe_screen_metrics", return_value = (avail_w, avail_h)):
            await mixin.create_browser_session()

        # 5. Navigate to about:blank and read the actual inner window size.
        #    Uses web_execute() which normalizes nodriver RemoteObject values.
        await mixin.web_open(_HTML_BLANK)
        dims = await mixin.web_execute(
            "({w: window.innerWidth, h: window.innerHeight})"
        )
        assert isinstance(dims, dict), f"Unexpected dimensions result: {dims!r}"
        w = dims.get("w", 0)
        h = dims.get("h", 0)
        assert isinstance(w, (int, float)), f"Inner width has unexpected type: {type(w).__name__}"
        assert w > 0, f"Inner width must be positive, got {w}"
        assert isinstance(h, (int, float)), f"Inner height has unexpected type: {type(h).__name__}"
        assert h > 0, f"Inner height must be positive, got {h}"

        # 6. The window must never exceed the available screen area.
        assert w <= avail_w, f"Window width {w} exceeds available {avail_w}"
        assert h <= avail_h, f"Window height {h} exceeds available {avail_h}"
    finally:
        mixin.close_browser_session()
        shutil.rmtree(ud_dir, ignore_errors = True)


# ---------------------------------------------------------------------------
# fallback behaviour: probe failure picks smallest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_select_viewport_size_fallback_to_smallest_on_probe_failure() -> None:
    """When _probe_screen_metrics fails, _select_viewport_size must return None."""
    mixin = WebScrapingMixin()
    sizes = ["2560x1440", "1920x1080", "1366x768"]
    mixin.config = _make_bare_config(HumanizationConfig(randomize_viewport = True, viewport_sizes = sizes))

    with patch.object(mixin, "_probe_screen_metrics", return_value = None):
        selected = await mixin._select_viewport_size()

    assert selected is None


@pytest.mark.asyncio
async def test_select_viewport_size_returns_none_for_empty_sizes() -> None:
    """When viewport_sizes is empty, _select_viewport_size must return None."""
    mixin = WebScrapingMixin()
    mixin.config = _make_bare_config(HumanizationConfig(randomize_viewport = True, viewport_sizes = []))
    selected = await mixin._select_viewport_size()
    assert selected is None
