# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Tests for human-like interaction pacing and viewport behavior in WebScrapingMixin."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest

from kleinanzeigen_bot.model.config_model import Config, HumanizationConfig
from kleinanzeigen_bot.utils.web_scraping_mixin import (
    By,
    Element,
    WebScrapingMixin,
    _filter_viewport_sizes,  # noqa: PLC2701 # type: ignore[attr-defined]
    _jitter_viewport,  # noqa: PLC2701 # type: ignore[attr-defined]
    _parse_viewport_size,  # noqa: PLC2701 # type: ignore[attr-defined]
)


def make_scraper(humanization:HumanizationConfig | None = None) -> WebScrapingMixin:
    scraper = WebScrapingMixin()
    scraper.config = Config.model_validate({
        "login": {"username": "user@example.com", "password": "secret"},  # noqa: S106
        "humanization": (humanization or HumanizationConfig()).model_dump(),
    })
    return scraper


# ---------------------------------------------------------------------------
# web_click: always use plain element click
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_web_click_uses_plain_click() -> None:
    scraper = make_scraper()
    elem = AsyncMock(spec = Element)
    with (
        patch.object(scraper, "web_find", new_callable = AsyncMock, return_value = elem),
        patch.object(scraper, "web_sleep", new_callable = AsyncMock),
    ):
        result = await scraper.web_click(By.ID, "x")

    elem.click.assert_awaited_once()
    assert result is elem


# ---------------------------------------------------------------------------
# web_input: typing jitter
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_web_input_types_per_character_when_jitter_enabled() -> None:
    scraper = make_scraper(HumanizationConfig(typing_jitter = True, typing_delay_min_ms = 0, typing_delay_max_ms = 0))
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


@pytest.mark.asyncio
async def test_web_input_sends_per_character_by_default() -> None:
    scraper = make_scraper()
    field = AsyncMock(spec = Element)
    with (
        patch.object(scraper, "web_find", new_callable = AsyncMock, return_value = field),
        patch.object(scraper, "_clear_input", new_callable = AsyncMock),
        patch.object(scraper, "web_sleep", new_callable = AsyncMock),
    ):
        await scraper.web_input(By.ID, "x", "abc")

    assert [call.args[0] for call in field.send_keys.await_args_list] == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# web_sleep
# ---------------------------------------------------------------------------

# web_sleep is always independent of humanization.enabled
@pytest.mark.asyncio
async def test_web_sleep_is_independent_from_enabled_flag() -> None:
    scraper = make_scraper(HumanizationConfig(enabled = False, action_delay_min_ms = 1, action_delay_max_ms = 1))
    with patch("kleinanzeigen_bot.utils.web_scraping_mixin.asyncio.sleep", new_callable = AsyncMock) as sleep:
        await scraper.web_sleep()
    assert sleep.await_count == 1
    assert sleep.await_args is not None
    assert sleep.await_args.args[0] == 0.001


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


@pytest.mark.asyncio
async def test_web_sleep_with_max_only_respects_hard_cap() -> None:
    """web_sleep(max_ms=500) must never sleep above 0.5 s."""
    scraper = make_scraper()
    with patch("kleinanzeigen_bot.utils.web_scraping_mixin.asyncio.sleep", new_callable = AsyncMock) as sleep:
        await scraper.web_sleep(max_ms = 500)
    assert sleep.await_args is not None
    assert 0 <= sleep.await_args.args[0] <= 0.5


@pytest.mark.asyncio
async def test_web_sleep_with_min_only_is_fixed_delay() -> None:
    """web_sleep(min_ms=500) with omitted max resolves to exactly 0.5 s."""
    scraper = make_scraper()
    with patch("kleinanzeigen_bot.utils.web_scraping_mixin.asyncio.sleep", new_callable = AsyncMock) as sleep:
        await scraper.web_sleep(min_ms = 500)
    assert sleep.await_args is not None
    assert sleep.await_args.args[0] == 0.5


# ---------------------------------------------------------------------------
# viewport launch arguments
# ---------------------------------------------------------------------------

def test_viewport_arg_not_appended_by_bot_at_launch() -> None:
    """Bot-selected viewport sizes are applied post-open, not as launch args."""
    scraper = make_scraper(HumanizationConfig(randomize_viewport = True, viewport_sizes = ["1600x900"]))
    args, _ = scraper._build_new_browser_launch_args()
    assert not any(a.startswith("--window-size") for a in args)


def test_viewport_arg_not_appended_when_user_supplied() -> None:
    scraper = make_scraper(HumanizationConfig(randomize_viewport = True, viewport_sizes = ["1600x900"]))
    scraper.browser_config.arguments = ["--window-size=800,600"]
    args, _ = scraper._build_new_browser_launch_args()
    assert "--window-size=1600,900" not in args
    assert "--window-size=800,600" in args


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


# ---------------------------------------------------------------------------
# viewport-size helper functions (_filter_viewport_sizes)
# ---------------------------------------------------------------------------


def test_filter_viewport_sizes_all_fit() -> None:
    sizes = ["1920x1080", "1366x768", "2560x1440"]
    assert _filter_viewport_sizes(sizes, 2560, 1440) == sizes


def test_filter_viewport_sizes_some_fit() -> None:
    sizes = ["1920x1080", "2560x1440", "1366x768"]
    result = _filter_viewport_sizes(sizes, 1920, 1080)
    assert result == ["1920x1080", "1366x768"]


def test_filter_viewport_sizes_none_fit() -> None:
    sizes = ["2560x1440", "3840x2160"]
    assert _filter_viewport_sizes(sizes, 1920, 1080) == []


def test_filter_viewport_sizes_boundary_exact() -> None:
    """Sizes exactly equal to the available area must fit."""
    assert _filter_viewport_sizes(["1920x1080"], 1920, 1080) == ["1920x1080"]


def test_filter_viewport_sizes_skips_parse_errors() -> None:
    sizes = ["1920x1080", "not-valid", "1366x768"]
    result = _filter_viewport_sizes(sizes, 1920, 1080)
    assert result == ["1920x1080", "1366x768"]


def test_filter_viewport_sizes_empty_list() -> None:
    assert _filter_viewport_sizes([], 1920, 1080) == []


def test_parse_viewport_size() -> None:
    assert _parse_viewport_size("1920x1080") == (1920, 1080)
    assert _parse_viewport_size(" 1366x 768 ") == (1366, 768)
    assert _parse_viewport_size("1366X768") == (1366, 768)


def test_parse_viewport_size_rejects_invalid_values() -> None:
    assert _parse_viewport_size(cast(Any, None)) is None
    assert _parse_viewport_size("invalid") is None
    assert _parse_viewport_size("1920x") is None
    assert _parse_viewport_size("1920xabc") is None
    assert _parse_viewport_size("0x720") is None


# ---------------------------------------------------------------------------
# _jitter_viewport
# ---------------------------------------------------------------------------


def test_jitter_viewport_basic() -> None:
    """Jitter stays within ±24 width and ±16 height."""
    with patch("kleinanzeigen_bot.utils.web_scraping_mixin._rng.randint", side_effect = [1024, 600]) as mock_rand:
        jw, jh = _jitter_viewport(1024, 600, 2000, 1200)
    assert jw == 1024
    assert jh == 600
    assert mock_rand.call_args_list[0].args == (max(1, 1024 - 24), min(2000, 1024 + 24))
    assert mock_rand.call_args_list[1].args == (max(1, 600 - 16), min(1200, 600 + 16))


def test_jitter_viewport_caps_by_avail() -> None:
    """max_w / max_h are clamped to avail dimensions."""
    with patch("kleinanzeigen_bot.utils.web_scraping_mixin._rng.randint", side_effect = [1900, 1050]) as mock_rand:
        jw, jh = _jitter_viewport(1920, 1080, 1920, 1080)
    assert jw == 1900
    assert jh == 1050
    # base_w + 24 = 1944 but avail_w caps at 1920
    assert mock_rand.call_args_list[0].args == (max(1, 1920 - 24), 1920)
    assert mock_rand.call_args_list[1].args == (max(1, 1080 - 16), 1080)


def test_jitter_viewport_floor_at_one() -> None:
    """min_w / min_h never drop below 1, even for tiny bases."""
    with patch("kleinanzeigen_bot.utils.web_scraping_mixin._rng.randint", side_effect = [1, 1]) as mock_rand:
        jw, jh = _jitter_viewport(10, 10, 1920, 1080)
    assert jw == 1
    assert jh == 1
    # base_w - 24 = -14, clamped to 1
    assert mock_rand.call_args_list[0].args == (1, 10 + 24)
    assert mock_rand.call_args_list[1].args == (1, 10 + 16)


# ---------------------------------------------------------------------------
# post-open viewport resize
# ---------------------------------------------------------------------------


def test_select_viewport_size_for_metrics_applies_jitter() -> None:
    """When metrics are available, a fitting base is jittered before returning."""
    scraper = make_scraper(HumanizationConfig(randomize_viewport = True, viewport_sizes = ["1600x900", "1920x1080"]))
    with (
        patch("kleinanzeigen_bot.utils.web_scraping_mixin._rng.choice", return_value = "1600x900"),
        patch("kleinanzeigen_bot.utils.web_scraping_mixin._rng.randint", side_effect = [1590, 890]),
    ):
        result = scraper._select_viewport_size_for_metrics((1920, 1080))
    assert result == "1590x890"


@pytest.mark.parametrize(
    "metrics",
    [
        None,
        (0, 1080),
        (1920, 0),
        (-1, 1080),
        (1920, -1),
        ("1920", 1080),
        (1920, "1080"),
        (1920,),
        (1920, 1080, 1),
        ({"availWidth": 1920},),
        (True, 1080),
        (float("nan"), 1080),
        (float("inf"), 1080),
    ],
)
def test_select_viewport_size_for_metrics_returns_none_for_invalid_metrics(metrics:object) -> None:
    scraper = make_scraper(HumanizationConfig(randomize_viewport = True, viewport_sizes = ["2560x1440", "1366x768"]))
    with patch("kleinanzeigen_bot.utils.web_scraping_mixin._rng.choice", autospec = True) as choice_mock:
        result = scraper._select_viewport_size_for_metrics(metrics)
    assert result is None
    choice_mock.assert_not_called()


def test_select_viewport_size_for_metrics_returns_none_when_none_fit() -> None:
    scraper = make_scraper(HumanizationConfig(randomize_viewport = True, viewport_sizes = ["2560x1440"]))
    assert scraper._select_viewport_size_for_metrics((1920, 1080)) is None


@pytest.mark.asyncio
async def test_create_browser_session_no_prelaunch_viewport_probe() -> None:
    scraper = WebScrapingMixin()
    scraper.browser_config.binary_location = "fake-browser"
    scraper.browser_config.arguments = ["--timeout=1"]
    scraper.config = Config.model_validate({
        "login": {"username": "u", "password": "p"},  # noqa: S106
        "humanization": HumanizationConfig(randomize_viewport = True, viewport_sizes = ["1366x768"]).model_dump(),
    })

    main_browser = SimpleNamespace(websocket_url = "ws://test")

    with (
        patch.object(scraper, "_validate_chrome_version_configuration", new_callable = AsyncMock),
        patch.object(scraper, "_resolve_effective_user_data_dir", new_callable = AsyncMock, return_value = None),
        patch.object(scraper, "_add_browser_extensions", new_callable = AsyncMock),
        patch("kleinanzeigen_bot.utils.web_scraping_mixin.files.exists", new_callable = AsyncMock, return_value = True),
        patch("kleinanzeigen_bot.utils.web_scraping_mixin.nodriver.start", new_callable = AsyncMock, return_value = main_browser) as start,
    ):
        await scraper.create_browser_session()

    assert start.await_count == 1
    cfg_arg = start.call_args_list[0].args[0]
    assert "--timeout=1" in cfg_arg.browser_args
    assert not any(arg.startswith("--window-size") for arg in cfg_arg.browser_args)


@pytest.mark.asyncio
async def test_create_browser_session_marks_remote_sessions() -> None:
    scraper = WebScrapingMixin()
    scraper.browser_config.binary_location = "fake-browser"
    scraper.browser_config.arguments = ["--remote-debugging-port=9222"]
    scraper.config = Config.model_validate({
        "login": {"username": "u", "password": "p"},  # noqa: S106
        "humanization": HumanizationConfig(randomize_viewport = True, viewport_sizes = ["1366x768"]).model_dump(),
    })
    remote_browser = SimpleNamespace(websocket_url = "ws://remote")

    with (
        patch("kleinanzeigen_bot.utils.web_scraping_mixin.files.exists", new_callable = AsyncMock, return_value = True),
        patch.object(scraper, "_validate_chrome_version_configuration", new_callable = AsyncMock),
        patch.object(scraper, "_connect_to_remote_browser", new_callable = AsyncMock, return_value = remote_browser),
    ):
        await scraper.create_browser_session()

    assert scraper.browser is remote_browser
    assert scraper._browser_session_is_remote is True


def test_is_kleinanzeigen_page_rejects_missing_and_unparseable_urls() -> None:
    scraper = make_scraper()

    assert scraper._is_kleinanzeigen_page(None) is False
    with patch("kleinanzeigen_bot.utils.web_scraping_mixin.urlparse", side_effect = ValueError("bad url")):
        assert scraper._is_kleinanzeigen_page("https://www.kleinanzeigen.de/") is False


def test_is_kleinanzeigen_page_matches_domain_boundary() -> None:
    scraper = make_scraper()

    assert scraper._is_kleinanzeigen_page("https://kleinanzeigen.de/") is True
    assert scraper._is_kleinanzeigen_page("https://www.kleinanzeigen.de/") is True
    assert scraper._is_kleinanzeigen_page("https://notkleinanzeigen.de/") is False
    assert scraper._is_kleinanzeigen_page("https://kleinanzeigen.de.evil.test/") is False


@pytest.mark.asyncio
async def test_available_screen_size_handles_missing_page_and_non_dict() -> None:
    scraper = make_scraper()

    assert await scraper._available_screen_size() is None

    scraper.page = cast(Any, SimpleNamespace(url = "https://www.kleinanzeigen.de/"))
    with patch.object(scraper, "web_execute", new_callable = AsyncMock, return_value = "not-a-dict"):
        assert await scraper._available_screen_size() is None


@pytest.mark.asyncio
async def test_resize_viewport_after_open_skips_user_window_size() -> None:
    scraper = make_scraper(HumanizationConfig(randomize_viewport = True, viewport_sizes = ["1366x768"]))
    scraper.browser_config.arguments = ["--window-size=800,600"]
    scraper.page = cast(Any, SimpleNamespace(url = "https://www.kleinanzeigen.de/"))

    with (
        patch.object(scraper, "_available_screen_size", new_callable = AsyncMock) as collect_metrics,
        patch.object(scraper, "_apply_viewport_size", new_callable = AsyncMock) as apply_size,
    ):
        await scraper._resize_viewport_after_open()

    collect_metrics.assert_not_awaited()
    apply_size.assert_not_awaited()
    assert scraper._viewport_resize_attempted is True


@pytest.mark.asyncio
async def test_resize_viewport_after_open_does_not_skip_window_size_prefix_typos() -> None:
    scraper = make_scraper(HumanizationConfig(randomize_viewport = True, viewport_sizes = ["1366x768"]))
    scraper.browser_config.arguments = ["--window-size-mode=weird"]
    scraper.page = cast(Any, SimpleNamespace(url = "https://www.kleinanzeigen.de/"))

    with (
        patch("kleinanzeigen_bot.utils.web_scraping_mixin._has_display_available", return_value = True),
        patch.object(scraper, "_available_screen_size", new_callable = AsyncMock, return_value = (1920, 1080)) as collect_metrics,
        patch.object(scraper, "_apply_viewport_size", new_callable = AsyncMock, return_value = True) as apply_size,
        patch("kleinanzeigen_bot.utils.web_scraping_mixin._rng.choice", return_value = "1366x768"),
        patch("kleinanzeigen_bot.utils.web_scraping_mixin._rng.randint", side_effect = [1366, 768]),
    ):
        await scraper._resize_viewport_after_open()

    collect_metrics.assert_awaited_once()
    apply_size.assert_awaited_once_with("1366x768")


@pytest.mark.asyncio
async def test_resize_viewport_after_open_ignores_non_kleinanzeigen_page() -> None:
    scraper = make_scraper(HumanizationConfig(randomize_viewport = True, viewport_sizes = ["1366x768"]))
    scraper.page = cast(Any, SimpleNamespace(url = "about:blank"))

    await scraper._resize_viewport_after_open()

    assert scraper._viewport_resize_attempted is False


@pytest.mark.asyncio
async def test_resize_viewport_after_open_applies_once() -> None:
    scraper = make_scraper(HumanizationConfig(randomize_viewport = True, viewport_sizes = ["1366x768"]))
    scraper.page = cast(Any, SimpleNamespace(url = "https://www.kleinanzeigen.de/"))
    with (
        patch("kleinanzeigen_bot.utils.web_scraping_mixin._has_display_available", return_value = True),
        patch.object(scraper, "_available_screen_size", new_callable = AsyncMock, return_value = (1920, 1080)) as collect_metrics,
        patch.object(scraper, "_apply_viewport_size", new_callable = AsyncMock, return_value = True) as apply_size,
        patch("kleinanzeigen_bot.utils.web_scraping_mixin._rng.choice", return_value = "1366x768"),
        patch("kleinanzeigen_bot.utils.web_scraping_mixin._rng.randint", side_effect = [1377, 777]),
    ):
        await scraper._resize_viewport_after_open()
        await scraper._resize_viewport_after_open()

    assert collect_metrics.await_count == 1
    apply_size.assert_awaited_once_with("1377x777")
    assert scraper._viewport_resize_attempted is True


@pytest.mark.asyncio
async def test_resize_viewport_after_open_skips_invalid_metrics() -> None:
    scraper = make_scraper(HumanizationConfig(randomize_viewport = True, viewport_sizes = ["1366x768"]))
    scraper.page = cast(Any, SimpleNamespace(url = "https://www.kleinanzeigen.de/"))
    with (
        patch("kleinanzeigen_bot.utils.web_scraping_mixin._has_display_available", return_value = True),
        patch.object(scraper, "web_execute", new_callable = AsyncMock, return_value = {"availWidth": True, "availHeight": 1080}),
        patch.object(scraper, "_apply_viewport_size", new_callable = AsyncMock) as apply_size,
    ):
        await scraper._resize_viewport_after_open()

    apply_size.assert_not_awaited()
    assert scraper._viewport_resize_attempted is True


@pytest.mark.asyncio
async def test_resize_viewport_after_open_skips_when_metrics_unavailable() -> None:
    scraper = make_scraper(HumanizationConfig(randomize_viewport = True, viewport_sizes = ["1366x768"]))
    scraper.page = cast(Any, SimpleNamespace(url = "https://www.kleinanzeigen.de/"))

    with (
        patch("kleinanzeigen_bot.utils.web_scraping_mixin._has_display_available", return_value = True),
        patch.object(scraper, "_available_screen_size", new_callable = AsyncMock, return_value = None),
    ):
        await scraper._resize_viewport_after_open()

    assert scraper._viewport_resize_attempted is True


@pytest.mark.asyncio
async def test_resize_viewport_after_open_treats_metrics_collection_error_as_nonfatal() -> None:
    scraper = make_scraper(HumanizationConfig(randomize_viewport = True, viewport_sizes = ["1366x768"]))
    scraper.page = cast(Any, SimpleNamespace(url = "https://www.kleinanzeigen.de/"))

    with (
        patch("kleinanzeigen_bot.utils.web_scraping_mixin._has_display_available", return_value = True),
        patch.object(scraper, "_available_screen_size", new_callable = AsyncMock, side_effect = RuntimeError("page gone")),
    ):
        await scraper._resize_viewport_after_open()

    assert scraper._viewport_resize_attempted is True


@pytest.mark.asyncio
async def test_resize_viewport_after_open_skips_when_no_fitting_size() -> None:
    scraper = make_scraper(HumanizationConfig(randomize_viewport = True, viewport_sizes = ["2560x1440"]))
    scraper.page = cast(Any, SimpleNamespace(url = "https://www.kleinanzeigen.de/"))

    with (
        patch("kleinanzeigen_bot.utils.web_scraping_mixin._has_display_available", return_value = True),
        patch.object(scraper, "_available_screen_size", new_callable = AsyncMock, return_value = (1920, 1080)),
        patch.object(scraper, "_apply_viewport_size", new_callable = AsyncMock) as apply_size,
    ):
        await scraper._resize_viewport_after_open()

    apply_size.assert_not_awaited()
    assert scraper._viewport_resize_attempted is True


@pytest.mark.asyncio
async def test_apply_viewport_size_preserves_window_position() -> None:
    scraper = make_scraper()
    bounds = SimpleNamespace(left = 12, top = 34)
    page = SimpleNamespace(
        get_window = AsyncMock(return_value = (7, bounds)),
        send = AsyncMock(),
    )
    scraper.page = cast(Any, page)

    with patch("kleinanzeigen_bot.utils.web_scraping_mixin.cdp_browser.set_window_bounds", return_value = "set-bounds") as set_bounds:
        applied = await scraper._apply_viewport_size("1377x777")

    assert applied is True
    page.get_window.assert_awaited_once()
    page.send.assert_awaited_once_with("set-bounds")
    assert set_bounds.call_args.args[0] == 7
    new_bounds = set_bounds.call_args.kwargs["bounds"]
    assert new_bounds.left == 12
    assert new_bounds.top == 34
    assert new_bounds.width == 1377
    assert new_bounds.height == 777


@pytest.mark.asyncio
async def test_apply_viewport_size_omits_unknown_window_position() -> None:
    scraper = make_scraper()
    bounds = SimpleNamespace(left = None, top = None)
    page = SimpleNamespace(
        get_window = AsyncMock(return_value = (7, bounds)),
        send = AsyncMock(),
    )
    scraper.page = cast(Any, page)

    with patch("kleinanzeigen_bot.utils.web_scraping_mixin.cdp_browser.set_window_bounds", return_value = "set-bounds") as set_bounds:
        applied = await scraper._apply_viewport_size("1377x777")

    assert applied is True
    new_bounds = set_bounds.call_args.kwargs["bounds"]
    assert new_bounds.left is None
    assert new_bounds.top is None
    assert new_bounds.width == 1377
    assert new_bounds.height == 777


@pytest.mark.asyncio
async def test_apply_viewport_size_reports_invalid_inputs_and_cdp_errors() -> None:
    scraper = make_scraper()

    assert await scraper._apply_viewport_size("1377x777") is False

    scraper.page = cast(Any, SimpleNamespace(get_window = AsyncMock(side_effect = RuntimeError("no window"))))
    assert await scraper._apply_viewport_size("invalid") is False
    assert await scraper._apply_viewport_size("1377x777") is False


@pytest.mark.asyncio
async def test_resize_viewport_after_open_treats_resize_failure_as_nonfatal() -> None:
    scraper = make_scraper(HumanizationConfig(randomize_viewport = True, viewport_sizes = ["1366x768"]))
    scraper.page = cast(Any, SimpleNamespace(url = "https://www.kleinanzeigen.de/"))
    metrics = (1920, 1080)

    with (
        patch("kleinanzeigen_bot.utils.web_scraping_mixin._has_display_available", return_value = True),
        patch.object(scraper, "_available_screen_size", new_callable = AsyncMock, return_value = metrics),
        patch.object(scraper, "_apply_viewport_size", new_callable = AsyncMock, return_value = False) as apply_size,
        patch("kleinanzeigen_bot.utils.web_scraping_mixin._rng.choice", return_value = "1366x768"),
        patch("kleinanzeigen_bot.utils.web_scraping_mixin._rng.randint", side_effect = [1366, 768]),
    ):
        await scraper._resize_viewport_after_open()

    apply_size.assert_awaited_once_with("1366x768")
    assert scraper._viewport_resize_attempted is True


@pytest.mark.asyncio
async def test_web_open_triggers_resize_after_open() -> None:
    scraper = make_scraper()
    page = SimpleNamespace(url = "https://www.kleinanzeigen.de/")
    scraper.browser = cast(Any, SimpleNamespace(get = AsyncMock(return_value = page)))

    async def resize() -> None:
        pass

    with (
        patch.object(scraper, "web_await", new_callable = AsyncMock),
        patch.object(scraper, "_resize_viewport_after_open", side_effect = resize) as resize_mock,
    ):
        await scraper.web_open("https://www.kleinanzeigen.de/")

    resize_mock.assert_awaited_once()


def test_default_humanization_config() -> None:
    cfg = HumanizationConfig()
    assert cfg.enabled is True
    assert cfg.typing_jitter is True
    assert cfg.randomize_viewport is True
    assert cfg.action_delay_min_ms == 500
    assert cfg.action_delay_max_ms == 1500
    assert cfg.typing_delay_min_ms > 0
    assert cfg.typing_delay_max_ms >= cfg.typing_delay_min_ms
