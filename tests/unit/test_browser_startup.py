# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Check test-only browser startup retries without launching any process."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nodriver.core.browser import Browser

from kleinanzeigen_bot.utils.web_scraping_mixin import WebScrapingMixin
from tests import browser_startup


def _browser(profile:str = "test-browser-profile") -> MagicMock:
    return MagicMock(
        spec = Browser,
        _process = MagicMock(spec = asyncio.subprocess.Process, pid = 123, returncode = None),
        _process_pid = 123,
        config = SimpleNamespace(user_data_dir = profile, uses_custom_data_dir = bool(profile)),
    )


@pytest.fixture
def web() -> MagicMock:
    return MagicMock(
        spec = WebScrapingMixin,
        browser = None,
        browser_config = SimpleNamespace(user_data_dir = "test-browser-profile"),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("exhausted", [False, True])
async def test_retry_reaps_each_failed_browser_before_next_start(web:MagicMock, exhausted:bool) -> None:
    """Clean even the last failed attempt; never touch an earlier registered browser."""
    previous = _browser()
    registry = {previous}
    failed:list[MagicMock] = []
    failure = Exception("Failed to connect to browser")

    async def create() -> None:
        assert web.browser is None
        assert all(browser._process.returncode is not None for browser in failed)
        if not failed or exhausted:
            browser = _browser()
            failed.append(browser)
            registry.add(browser)
            raise failure

    async def close() -> None:
        assert web.browser is failed[-1]
        web.browser._process.returncode = -15
        web.browser = None

    with (
        patch.object(browser_startup, "get_registered_instances", return_value = registry),
        patch.object(web, "create_browser_session", side_effect = create) as start,
        patch.object(web, "close_browser_session", side_effect = close) as cleanup,
        patch("tests.browser_startup.asyncio.sleep", new_callable = AsyncMock) as sleep,
    ):
        if exhausted:
            with pytest.raises(Exception, match = "Failed to connect to browser") as exc_info:
                await browser_startup.start_browser_session(web)
            assert exc_info.value is failure
        else:
            await browser_startup.start_browser_session(web)

    expected_failures = browser_startup._BROWSER_START_ATTEMPTS if exhausted else 1
    assert cleanup.await_count == expected_failures
    assert start.await_count == expected_failures + (not exhausted)
    assert sleep.await_count == expected_failures - exhausted
    previous._process.terminate.assert_not_called()
    previous._process.kill.assert_not_called()
    assert registry == {previous, *failed}


@pytest.mark.asyncio
async def test_unrelated_startup_error_is_not_retried(web:MagicMock) -> None:
    failure = ValueError("invalid browser configuration")
    with (
        patch.object(browser_startup, "get_registered_instances", return_value = set()),
        patch.object(web, "create_browser_session", side_effect = failure) as start,
        patch.object(web, "close_browser_session") as cleanup,
        patch("tests.browser_startup.asyncio.sleep", new_callable = AsyncMock) as sleep,
        pytest.raises(ValueError, match = "invalid browser configuration") as exc_info,
    ):
        await browser_startup.start_browser_session(web)
    assert exc_info.value is failure
    start.assert_awaited_once()
    cleanup.assert_not_awaited()
    sleep.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing", "ambiguous", "profile", "process", "pid", "cleanup", "unreaped"])
async def test_unsafe_or_failed_cleanup_stops_retry(web:MagicMock, fault:str) -> None:
    previous = _browser()
    failed = _browser()
    registry = {previous}
    failure = Exception("Failed to connect to browser")
    if fault == "profile":
        failed.config.user_data_dir = "other-browser-profile"
    elif fault == "process":
        failed._process = None
    elif fault == "pid":
        failed._process_pid = 999

    async def create() -> None:
        if fault != "missing":
            registry.add(failed)
        if fault == "ambiguous":
            registry.add(_browser())
        raise failure

    with (
        patch.object(browser_startup, "get_registered_instances", return_value = registry),
        patch.object(web, "create_browser_session", side_effect = create) as start,
        patch.object(web, "close_browser_session", side_effect = RuntimeError("cleanup failed") if fault == "cleanup" else None) as cleanup,
        patch("tests.browser_startup.asyncio.sleep", new_callable = AsyncMock) as sleep,
        pytest.raises(Exception, match = "Failed to connect to browser") as exc_info,
    ):
        await browser_startup.start_browser_session(web)

    assert exc_info.value is failure
    assert "cleanup failed; retry stopped" in failure.__notes__[0]
    start.assert_awaited_once()
    assert cleanup.await_count == (fault in {"cleanup", "unreaped"})
    sleep.assert_not_awaited()
    previous._process.terminate.assert_not_called()
    previous._process.kill.assert_not_called()
    assert previous in registry


@pytest.mark.asyncio
@pytest.mark.parametrize("custom", [False, True])
async def test_unconfigured_profile_accepts_only_nodriver_temporary_profile(web:MagicMock, tmp_path:Path, custom:bool) -> None:
    web.browser_config.user_data_dir = None
    failed = _browser(str(tmp_path))
    failed.config.uses_custom_data_dir = custom
    registry:set[Browser] = set()
    sentinel = tmp_path / "cookie-profile-sentinel"
    sentinel.write_text("preserve", encoding = "utf-8")
    failure = Exception("Failed to connect to browser")

    async def create() -> None:
        if not registry:
            registry.add(failed)
            raise failure

    async def close() -> None:
        failed._process.returncode = -15
        web.browser = None

    with (
        patch.object(browser_startup, "get_registered_instances", return_value = registry),
        patch.object(web, "create_browser_session", side_effect = create) as start,
        patch.object(web, "close_browser_session", side_effect = close) as cleanup,
        patch("tests.browser_startup.asyncio.sleep", new_callable = AsyncMock),
    ):
        if custom:
            with pytest.raises(Exception, match = "Failed to connect to browser") as exc_info:
                await browser_startup.start_browser_session(web)
            assert exc_info.value is failure
            cleanup.assert_not_awaited()
            start.assert_awaited_once()
        else:
            await browser_startup.start_browser_session(web)
            cleanup.assert_awaited_once()
            assert start.await_count == 2
    assert sentinel.read_text(encoding = "utf-8") == "preserve"
