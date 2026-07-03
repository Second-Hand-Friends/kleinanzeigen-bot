# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Tests for the human-like interaction behavior in WebScrapingMixin (bot-detection evasion)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from kleinanzeigen_bot.model.config_model import Config, HumanizationConfig
from kleinanzeigen_bot.utils.web_scraping_mixin import By, Element, WebScrapingMixin


def make_scraper(humanization:HumanizationConfig | None = None) -> WebScrapingMixin:
    scraper = WebScrapingMixin()
    scraper.config = Config.model_validate({
        "login": {"username": "user@example.com", "password": "secret"},  # noqa: S106
        "humanization": (humanization or HumanizationConfig()).model_dump(),
    })
    return scraper


# ---------------------------------------------------------------------------
# web_click: mouse-based clicking with fallback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_web_click_uses_humanized_path_when_enabled() -> None:
    scraper = make_scraper(HumanizationConfig(mouse_movement = True))
    elem = AsyncMock(spec = Element)
    with (
        patch.object(scraper, "web_find", new_callable = AsyncMock, return_value = elem),
        patch.object(scraper, "_humanized_click", new_callable = AsyncMock) as humanized,
        patch.object(scraper, "web_sleep", new_callable = AsyncMock),
    ):
        result = await scraper.web_click(By.ID, "x")

    humanized.assert_awaited_once_with(elem)
    elem.click.assert_not_awaited()
    assert result is elem


@pytest.mark.asyncio
async def test_web_click_falls_back_to_plain_click_on_failure() -> None:
    scraper = make_scraper(HumanizationConfig(mouse_movement = True))
    elem = AsyncMock(spec = Element)
    with (
        patch.object(scraper, "web_find", new_callable = AsyncMock, return_value = elem),
        patch.object(scraper, "_humanized_click", new_callable = AsyncMock, side_effect = RuntimeError("no geometry")),
        patch.object(scraper, "web_sleep", new_callable = AsyncMock),
    ):
        await scraper.web_click(By.ID, "x")

    elem.click.assert_awaited_once()


@pytest.mark.asyncio
async def test_web_click_uses_plain_click_when_mouse_movement_disabled() -> None:
    scraper = make_scraper(HumanizationConfig(mouse_movement = False))
    elem = AsyncMock(spec = Element)
    with (
        patch.object(scraper, "web_find", new_callable = AsyncMock, return_value = elem),
        patch.object(scraper, "_humanized_click", new_callable = AsyncMock) as humanized,
        patch.object(scraper, "web_sleep", new_callable = AsyncMock),
    ):
        await scraper.web_click(By.ID, "x")

    humanized.assert_not_awaited()
    elem.click.assert_awaited_once()


@pytest.mark.asyncio
async def test_humanized_click_dispatches_mouse_events() -> None:
    scraper = make_scraper()
    tab = AsyncMock()
    elem = AsyncMock(spec = Element)
    elem.scroll_into_view = AsyncMock()
    elem.get_position = AsyncMock(return_value = SimpleNamespace(center = (100.0, 200.0)))
    elem._tab = tab
    with (
        patch("kleinanzeigen_bot.utils.web_scraping_mixin.asyncio.sleep", new_callable = AsyncMock),
        patch("kleinanzeigen_bot.utils.web_scraping_mixin.cdp_input.dispatch_mouse_event") as dispatch,
    ):
        await scraper._humanized_click(elem)

    # at least the moves plus a press and a release were dispatched
    assert tab.send.await_count >= 3
    dispatched_types = [call.args[0] for call in dispatch.call_args_list]
    assert "mouseMoved" in dispatched_types
    assert "mousePressed" in dispatched_types
    assert "mouseReleased" in dispatched_types


# ---------------------------------------------------------------------------
# web_input: typing jitter
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_web_input_types_per_character_when_jitter_enabled() -> None:
    scraper = make_scraper(HumanizationConfig(typing_delay_min_ms = 0, typing_delay_max_ms = 0))
    field = AsyncMock(spec = Element)
    with (
        patch.object(scraper, "web_find", new_callable = AsyncMock, return_value = field),
        patch.object(scraper, "_clear_input", new_callable = AsyncMock),
        patch.object(scraper, "web_sleep", new_callable = AsyncMock),
    ):
        await scraper.web_input(By.ID, "x", "abc")

    assert field.send_keys.await_count == 3
    assert [call.args[0] for call in field.send_keys.await_args_list] == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_web_input_single_burst_when_jitter_disabled() -> None:
    scraper = make_scraper(HumanizationConfig(typing_jitter = False))
    field = AsyncMock(spec = Element)
    with (
        patch.object(scraper, "web_find", new_callable = AsyncMock, return_value = field),
        patch.object(scraper, "_clear_input", new_callable = AsyncMock),
        patch.object(scraper, "web_sleep", new_callable = AsyncMock),
    ):
        await scraper.web_input(By.ID, "x", "abc")

    field.send_keys.assert_awaited_once_with("abc")


# ---------------------------------------------------------------------------
# idle actions + thinking pauses
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_perform_random_human_actions_noop_when_disabled() -> None:
    scraper = make_scraper(HumanizationConfig(enabled = False))
    with (
        patch.object(scraper, "_idle_scroll", new_callable = AsyncMock) as scroll,
        patch.object(scraper, "_idle_mouse_wiggle", new_callable = AsyncMock) as wiggle,
        patch.object(scraper, "web_think", new_callable = AsyncMock) as think,
    ):
        await scraper.perform_random_human_actions()

    scroll.assert_not_awaited()
    wiggle.assert_not_awaited()
    think.assert_not_awaited()


@pytest.mark.asyncio
async def test_perform_random_human_actions_runs_subset_when_gate_fires() -> None:
    scraper = make_scraper(HumanizationConfig(idle_action_probability = 1.0))
    with (
        patch("kleinanzeigen_bot.utils.web_scraping_mixin._rng.random", return_value = 0.0),
        patch("kleinanzeigen_bot.utils.web_scraping_mixin._rng.shuffle"),
        patch("kleinanzeigen_bot.utils.web_scraping_mixin._rng.randint", return_value = 1),
        patch.object(scraper, "_idle_scroll", new_callable = AsyncMock) as scroll,
        patch.object(scraper, "_idle_mouse_wiggle", new_callable = AsyncMock) as wiggle,
        patch.object(scraper, "web_think", new_callable = AsyncMock) as think,
    ):
        await scraper.perform_random_human_actions()

    # exactly one action runs (subset size forced to 1, shuffle disabled -> first action)
    assert scroll.await_count + wiggle.await_count + think.await_count == 1


@pytest.mark.asyncio
async def test_perform_random_human_actions_swallows_action_errors() -> None:
    scraper = make_scraper(HumanizationConfig(idle_action_probability = 1.0))
    with (
        patch("kleinanzeigen_bot.utils.web_scraping_mixin._rng.random", return_value = 0.0),
        patch("kleinanzeigen_bot.utils.web_scraping_mixin._rng.shuffle"),
        patch("kleinanzeigen_bot.utils.web_scraping_mixin._rng.randint", return_value = 3),
        patch.object(scraper, "_idle_scroll", new_callable = AsyncMock, side_effect = RuntimeError("boom")),
        patch.object(scraper, "_idle_mouse_wiggle", new_callable = AsyncMock),
        patch.object(scraper, "web_think", new_callable = AsyncMock),
    ):
        # must not raise even though the first action fails
        await scraper.perform_random_human_actions()


@pytest.mark.asyncio
async def test_web_think_pauses_only_within_probability() -> None:
    scraper = make_scraper(HumanizationConfig(long_pause_probability = 0.1))
    with (
        patch("kleinanzeigen_bot.utils.web_scraping_mixin._rng.random", return_value = 0.05),
        patch.object(scraper, "web_sleep", new_callable = AsyncMock) as sleep,
    ):
        await scraper.web_think()
    sleep.assert_awaited_once()

    with (
        patch("kleinanzeigen_bot.utils.web_scraping_mixin._rng.random", return_value = 0.5),
        patch.object(scraper, "web_sleep", new_callable = AsyncMock) as sleep2,
    ):
        await scraper.web_think()
    sleep2.assert_not_awaited()


# ---------------------------------------------------------------------------
# web_sleep configurable band
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_web_sleep_uses_configured_band() -> None:
    scraper = make_scraper(HumanizationConfig(action_delay_min_ms = 10, action_delay_max_ms = 11))
    with patch("kleinanzeigen_bot.utils.web_scraping_mixin.asyncio.sleep", new_callable = AsyncMock) as sleep:
        await scraper.web_sleep()
    # 10 ms lower bound -> 0.010 s
    assert sleep.await_args is not None
    slept_seconds = sleep.await_args.args[0]
    assert 0.010 <= slept_seconds <= 0.011


@pytest.mark.asyncio
async def test_web_sleep_respects_explicit_bounds() -> None:
    scraper = make_scraper()
    with patch("kleinanzeigen_bot.utils.web_scraping_mixin.asyncio.sleep", new_callable = AsyncMock) as sleep:
        await scraper.web_sleep(50, 51)
    assert sleep.await_args is not None
    assert 0.050 <= sleep.await_args.args[0] <= 0.051


# ---------------------------------------------------------------------------
# viewport randomization at launch
# ---------------------------------------------------------------------------

def test_viewport_arg_appended_when_enabled() -> None:
    scraper = make_scraper(HumanizationConfig(randomize_viewport = True, viewport_sizes = ["1600x900"]))
    args, _ = scraper._build_new_browser_launch_args()
    assert "--window-size=1600,900" in args


def test_viewport_arg_not_appended_when_user_supplied() -> None:
    scraper = make_scraper(HumanizationConfig(randomize_viewport = True, viewport_sizes = ["1600x900"]))
    scraper.browser_config.arguments = ["--window-size=800,600"]
    args, _ = scraper._build_new_browser_launch_args()
    assert "--window-size=1600,900" not in args
    assert "--window-size=800,600" in args


def test_viewport_arg_not_appended_when_disabled() -> None:
    scraper = make_scraper(HumanizationConfig(randomize_viewport = False))
    args, _ = scraper._build_new_browser_launch_args()
    assert not any(arg.startswith("--window-size") for arg in args)


# ---------------------------------------------------------------------------
# web_sleep: zero-delay range does not raise
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_web_sleep_zero_delay() -> None:
    """web_sleep(0, 0) must not raise and must sleep 0 seconds."""
    scraper = make_scraper()
    with patch("kleinanzeigen_bot.utils.web_scraping_mixin.asyncio.sleep", new_callable = AsyncMock) as sleep:
        await scraper.web_sleep(0, 0)
    assert sleep.await_count == 1
    assert sleep.await_args is not None
    assert sleep.await_args.args[0] == 0.0


@pytest.mark.asyncio
async def test_web_sleep_default_zero_band() -> None:
    """web_sleep() without args with config band (0, 0) must not raise and sleep 0."""
    scraper = make_scraper(HumanizationConfig(action_delay_min_ms = 0, action_delay_max_ms = 0))
    with patch("kleinanzeigen_bot.utils.web_scraping_mixin.asyncio.sleep", new_callable = AsyncMock) as sleep:
        await scraper.web_sleep()
    assert sleep.await_args is not None
    assert sleep.await_args.args[0] == 0.0


# ---------------------------------------------------------------------------
# HumanizationConfig validators
# ---------------------------------------------------------------------------

def test_invalid_viewport_format_raises_error() -> None:
    with pytest.raises(ValueError, match = "Invalid viewport size"):
        HumanizationConfig(viewport_sizes = ["invalid"])

    with pytest.raises(ValueError, match = "Invalid viewport size"):
        HumanizationConfig(viewport_sizes = ["1920x1080x720"])

    with pytest.raises(ValueError, match = "Invalid viewport size"):
        HumanizationConfig(viewport_sizes = ["abcxdef"])

    with pytest.raises(ValueError, match = "Invalid viewport size"):
        HumanizationConfig(viewport_sizes = ["0x0"])

    with pytest.raises(ValueError, match = "Invalid viewport size"):
        HumanizationConfig(viewport_sizes = ["0x720"])

    with pytest.raises(ValueError, match = "Invalid viewport size"):
        HumanizationConfig(viewport_sizes = ["1920x0"])


def test_reversed_min_max_raises_error() -> None:
    with pytest.raises(ValueError, match = "must be >="):
        HumanizationConfig(typing_delay_min_ms = 100, typing_delay_max_ms = 50)

    with pytest.raises(ValueError, match = "must be >="):
        HumanizationConfig(action_delay_min_ms = 3_000, action_delay_max_ms = 1_000)

    with pytest.raises(ValueError, match = "must be >="):
        HumanizationConfig(long_pause_min_ms = 5_000, long_pause_max_ms = 2_000)


# ---------------------------------------------------------------------------
# _humanized_type fallback on per-character failure
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_humanized_type_fallback_clears_and_sends_full_text() -> None:
    """When per-character typing fails, fallback clears and sends full text once."""
    scraper = make_scraper(HumanizationConfig(typing_delay_min_ms = 0, typing_delay_max_ms = 0))
    field = AsyncMock(spec = Element)
    field.send_keys = AsyncMock(side_effect = [None, RuntimeError("cdp fail"), None])

    with (
        patch.object(scraper, "_clear_input", new_callable = AsyncMock) as clear_mock,
        patch("kleinanzeigen_bot.utils.web_scraping_mixin.asyncio.sleep", new_callable = AsyncMock),
    ):
        await scraper._humanized_type(field, "abc")

    # _clear_input was called in the fallback path
    clear_mock.assert_awaited_once_with(field)
    # send_keys: "a" (ok), "b" (fails), then full "abc" after clear
    assert field.send_keys.await_count == 3
    assert field.send_keys.await_args_list[0].args[0] == "a"
    assert field.send_keys.await_args_list[1].args[0] == "b"
    assert field.send_keys.await_args_list[2].args[0] == "abc"
