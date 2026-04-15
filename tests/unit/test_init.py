# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import asyncio, copy, fnmatch, gc, io, json, logging, os, tempfile  # isort: skip
from collections.abc import Callable, Generator
from contextlib import ExitStack, contextmanager, redirect_stdout
from datetime import timedelta
from pathlib import Path, PureWindowsPath
from typing import Any, Awaitable, Iterator, cast
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from nodriver.core.connection import ProtocolException
from pydantic import ValidationError

from kleinanzeigen_bot import LOG, SUBMISSION_MAX_RETRIES, AdUpdateStrategy, KleinanzeigenBot, LoginDetectionReason, LoginDetectionResult, misc
from kleinanzeigen_bot._version import __version__
from kleinanzeigen_bot.model.ad_model import Ad
from kleinanzeigen_bot.model.config_model import AdDefaults, AutoPriceReductionConfig, Config, DiagnosticsConfig, PublishingConfig
from kleinanzeigen_bot.utils import dicts, loggers, xdg_paths
from kleinanzeigen_bot.utils.exceptions import PublishedAdsFetchIncompleteError, PublishSubmissionUncertainError
from kleinanzeigen_bot.utils.web_scraping_mixin import By, Element


@pytest.fixture
def mock_page() -> MagicMock:
    """Provide a mock page object for testing."""
    mock = MagicMock()
    mock.sleep = AsyncMock()
    mock.evaluate = AsyncMock()
    mock.click = AsyncMock()
    mock.type = AsyncMock()
    mock.select = AsyncMock()
    mock.wait_for_selector = AsyncMock()
    mock.wait_for_navigation = AsyncMock()
    mock.wait_for_load_state = AsyncMock()
    mock.content = AsyncMock(return_value = "<html></html>")
    mock.goto = AsyncMock()
    mock.close = AsyncMock()
    return mock


@pytest.fixture
def base_ad_config() -> dict[str, Any]:
    """Provide a base ad configuration that can be used across tests."""
    return {
        "id": None,
        "title": "Test Title",
        "description": "Test Description",
        "type": "OFFER",
        "price_type": "FIXED",
        "price": 100,
        "shipping_type": "SHIPPING",
        "shipping_options": [],
        "category": "160",
        "special_attributes": {},
        "sell_directly": False,
        "images": [],
        "active": True,
        "republication_interval": 7,
        "created_on": None,
        "contact": {"name": "Test User", "zipcode": "12345", "location": "Test City", "street": "", "phone": ""},
    }


def remove_fields(config:dict[str, Any], *fields:str) -> dict[str, Any]:
    """Create a new ad configuration with specified fields removed.

    Args:
        config: The configuration to remove fields from
        *fields: Field names to remove

    Returns:
        A new ad configuration dictionary with specified fields removed
    """
    result = copy.deepcopy(config)
    for field in fields:
        if "." in field:
            # Handle nested fields (e.g., "contact.phone")
            parts = field.split(".", maxsplit = 1)
            current = result
            for part in parts[:-1]:
                if part in current:
                    current = current[part]
            if parts[-1] in current:
                del current[parts[-1]]
        elif field in result:
            del result[field]
    return result


@pytest.fixture
def minimal_ad_config(base_ad_config:dict[str, Any]) -> dict[str, Any]:
    """Provide a minimal ad configuration with only required fields."""
    return remove_fields(base_ad_config, "id", "created_on", "shipping_options", "special_attributes", "contact.street", "contact.phone")


@pytest.fixture
def mock_config_setup(test_bot:KleinanzeigenBot) -> Generator[None]:
    """Provide a centralized mock configuration setup for tests.
    This fixture mocks load_config and other essential configuration-related methods."""
    with (
        patch.object(test_bot, "load_config"),
        patch.object(test_bot, "create_browser_session", new_callable = AsyncMock),
        patch.object(test_bot, "login", new_callable = AsyncMock),
        patch.object(test_bot, "web_request", new_callable = AsyncMock) as mock_request,
    ):
        # Mock the web request for published ads
        mock_request.return_value = {"content": '{"ads": []}'}
        yield


def _make_fake_resolve_workspace(
    captured_mode:dict[str, xdg_paths.InstallationMode | None],
    workspace:xdg_paths.Workspace,
) -> Callable[..., xdg_paths.Workspace]:
    """Create a fake resolve_workspace that captures the workspace_mode argument."""

    def fake_resolve_workspace(
        config_arg:str | None,
        logfile_arg:str | None,
        *,
        workspace_mode:xdg_paths.InstallationMode | None,
        logfile_explicitly_provided:bool,
        log_basename:str,
    ) -> xdg_paths.Workspace:
        captured_mode["value"] = workspace_mode
        return workspace

    return fake_resolve_workspace


def _login_detection_result(is_logged_in:bool, reason:LoginDetectionReason) -> LoginDetectionResult:
    return LoginDetectionResult(is_logged_in = is_logged_in, reason = reason)


class TestKleinanzeigenBotInitialization:
    """Tests for KleinanzeigenBot initialization and basic functionality."""

    def test_constructor_initializes_default_values(self, test_bot:KleinanzeigenBot) -> None:
        """Verify that constructor sets all default values correctly."""
        assert test_bot.root_url == "https://www.kleinanzeigen.de"
        assert isinstance(test_bot.config, Config)
        assert test_bot.command == "help"
        assert test_bot.ads_selector == "due"
        assert test_bot.keep_old_ads is False
        assert test_bot.log_file_path is not None
        assert test_bot.file_log is None

    @pytest.mark.parametrize("command", ["help", "create-config", "version"])
    def test_resolve_workspace_skips_non_workspace_commands(self, test_bot:KleinanzeigenBot, command:str) -> None:
        """Workspace resolution should remain None for commands that need no workspace."""
        test_bot.command = command
        test_bot.workspace = None
        test_bot._resolve_workspace()
        assert test_bot.workspace is None

    def test_resolve_workspace_exits_on_workspace_resolution_error(self, test_bot:KleinanzeigenBot, caplog:pytest.LogCaptureFixture) -> None:
        """Workspace resolution errors should terminate with code 2."""
        caplog.set_level(logging.ERROR)
        test_bot.command = "verify"

        with (
            patch("kleinanzeigen_bot.xdg_paths.resolve_workspace", side_effect = ValueError("workspace error")),
            pytest.raises(SystemExit) as exc_info,
        ):
            test_bot._resolve_workspace()

        assert exc_info.value.code == 2
        assert "workspace error" in caplog.text

    def test_resolve_workspace_fails_fast_when_config_parent_cannot_be_created(self, test_bot:KleinanzeigenBot, tmp_path:Path) -> None:
        """Workspace resolution should fail immediately when config directory creation fails."""
        test_bot.command = "verify"
        workspace = xdg_paths.Workspace.for_config(tmp_path / "blocked" / "config.yaml", "kleinanzeigen-bot")

        with (
            patch("kleinanzeigen_bot.xdg_paths.resolve_workspace", return_value = workspace),
            patch("kleinanzeigen_bot.xdg_paths.ensure_directory", side_effect = OSError("mkdir denied")),
            pytest.raises(OSError, match = "mkdir denied"),
        ):
            test_bot._resolve_workspace()

    def test_resolve_workspace_programmatic_config_in_xdg_defaults_to_xdg(self, test_bot:KleinanzeigenBot, tmp_path:Path) -> None:
        """Programmatic config_file_path in XDG config tree should default workspace mode to xdg."""
        test_bot.command = "verify"
        xdg_dirs = {
            "config": tmp_path / "xdg-config" / xdg_paths.APP_NAME,
            "state": tmp_path / "xdg-state" / xdg_paths.APP_NAME,
            "cache": tmp_path / "xdg-cache" / xdg_paths.APP_NAME,
        }
        for path in xdg_dirs.values():
            path.mkdir(parents = True, exist_ok = True)
        config_path = xdg_dirs["config"] / "config.yaml"
        config_path.touch()
        test_bot.config_file_path = str(config_path)

        workspace = xdg_paths.Workspace.for_config(tmp_path / "resolved" / "config.yaml", "kleinanzeigen-bot")
        captured_mode:dict[str, xdg_paths.InstallationMode | None] = {"value": None}

        with (
            patch("kleinanzeigen_bot.xdg_paths.get_xdg_base_dir", side_effect = lambda category: xdg_dirs[category]),
            patch("kleinanzeigen_bot.xdg_paths.resolve_workspace", side_effect = _make_fake_resolve_workspace(captured_mode, workspace)),
            patch("kleinanzeigen_bot.xdg_paths.ensure_directory"),
        ):
            test_bot._resolve_workspace()

        assert captured_mode["value"] == "xdg"

    def test_resolve_workspace_programmatic_config_outside_xdg_defaults_to_portable(self, test_bot:KleinanzeigenBot, tmp_path:Path) -> None:
        """Programmatic config_file_path outside XDG config tree should default workspace mode to portable."""
        test_bot.command = "verify"
        xdg_dirs = {
            "config": tmp_path / "xdg-config" / xdg_paths.APP_NAME,
            "state": tmp_path / "xdg-state" / xdg_paths.APP_NAME,
            "cache": tmp_path / "xdg-cache" / xdg_paths.APP_NAME,
        }
        for path in xdg_dirs.values():
            path.mkdir(parents = True, exist_ok = True)
        config_path = tmp_path / "external" / "config.yaml"
        config_path.parent.mkdir(parents = True, exist_ok = True)
        config_path.touch()
        test_bot.config_file_path = str(config_path)

        workspace = xdg_paths.Workspace.for_config(tmp_path / "resolved" / "config.yaml", "kleinanzeigen-bot")
        captured_mode:dict[str, xdg_paths.InstallationMode | None] = {"value": None}

        with (
            patch("kleinanzeigen_bot.xdg_paths.get_xdg_base_dir", side_effect = lambda category: xdg_dirs[category]),
            patch("kleinanzeigen_bot.xdg_paths.resolve_workspace", side_effect = _make_fake_resolve_workspace(captured_mode, workspace)),
            patch("kleinanzeigen_bot.xdg_paths.ensure_directory"),
        ):
            test_bot._resolve_workspace()

        assert captured_mode["value"] == "portable"

    def test_create_default_config_creates_parent_without_workspace(self, test_bot:KleinanzeigenBot, tmp_path:Path) -> None:
        """create_default_config should create parent directories when no workspace is set."""
        config_path = tmp_path / "nested" / "config.yaml"
        test_bot.workspace = None
        test_bot.config_file_path = str(config_path)

        test_bot.create_default_config()

        assert config_path.exists()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("command", ["verify", "update-check", "update-content-hash", "publish", "delete", "download"])
    async def test_run_uses_workspace_state_file_for_update_checker(self, test_bot:KleinanzeigenBot, command:str, tmp_path:Path) -> None:
        """Ensure UpdateChecker is initialized with the workspace state file."""
        update_checker_calls:list[tuple[Config, Path]] = []

        class DummyUpdateChecker:
            def __init__(self, config:Config, state_file:Path) -> None:
                update_checker_calls.append((config, state_file))

            def check_for_updates(self, *_args:Any, **_kwargs:Any) -> None:
                return None

        def set_workspace() -> None:
            test_bot.workspace = xdg_paths.Workspace.for_config(tmp_path / "config.yaml", "kleinanzeigen-bot")

        with (
            patch.object(test_bot, "configure_file_logging"),
            patch.object(test_bot, "load_config"),
            patch.object(test_bot, "load_ads", return_value = []),
            patch.object(test_bot, "create_browser_session", new_callable = AsyncMock),
            patch.object(test_bot, "login", new_callable = AsyncMock),
            patch.object(test_bot, "download_ads", new_callable = AsyncMock),
            patch.object(test_bot, "close_browser_session"),
            patch.object(test_bot, "_resolve_workspace", side_effect = set_workspace),
            patch("kleinanzeigen_bot.UpdateChecker", DummyUpdateChecker),
        ):
            await test_bot.run(["app", command])

        expected_state_path = (tmp_path / "config.yaml").resolve().parent / ".temp" / "update_check_state.json"
        assert update_checker_calls == [(test_bot.config, expected_state_path)]

    @pytest.mark.asyncio
    async def test_download_ads_passes_download_dir_and_published_ads(self, test_bot:KleinanzeigenBot, tmp_path:Path) -> None:
        """Ensure download_ads wires resolved download_dir and published_ads_by_id into AdExtractor."""
        config_file = tmp_path / "config.yaml"
        test_bot.workspace = xdg_paths.Workspace.for_config(config_file, "kleinanzeigen-bot")
        test_bot.config_file_path = str(config_file)
        test_bot.ads_selector = "all"
        test_bot.browser = MagicMock()

        extractor_mock = MagicMock()
        extractor_mock.extract_own_ads_urls = AsyncMock(return_value = [])

        mock_published_ads = [{"id": 123, "buyNowEligible": True}, {"id": 456, "buyNowEligible": False}]

        with (
            patch.object(test_bot, "_fetch_published_ads", new_callable = AsyncMock, return_value = mock_published_ads),
            patch("kleinanzeigen_bot.extract.AdExtractor", return_value = extractor_mock) as mock_extractor,
        ):
            await test_bot.download_ads()

        # Verify published_ads_by_id is built correctly and passed to extractor
        mock_extractor.assert_called_once_with(
            test_bot.browser,
            test_bot.config,
            test_bot.workspace.download_dir,
            published_ads_by_id = {123: mock_published_ads[0], 456: mock_published_ads[1]},
        )

    def test_resolve_download_dir_uses_workspace_default_for_literal_default(self, test_bot:KleinanzeigenBot, tmp_path:Path) -> None:
        config_file = tmp_path / "config.yaml"
        test_bot.workspace = xdg_paths.Workspace.for_config(config_file, "kleinanzeigen-bot")
        test_bot.config_file_path = str(config_file)
        test_bot.browser = cast(Any, None)

        assert test_bot._resolve_download_dir() == test_bot.workspace.download_dir

    def test_resolve_download_dir_uses_config_relative_path(self, test_bot:KleinanzeigenBot, tmp_path:Path) -> None:
        config_file = tmp_path / "config.yaml"
        test_bot.workspace = xdg_paths.Workspace.for_config(config_file, "kleinanzeigen-bot")
        test_bot.config_file_path = str(config_file)
        test_bot.browser = cast(Any, None)
        test_bot.config.download.dir = "./my-ads"

        assert test_bot._resolve_download_dir() == (tmp_path / "my-ads").resolve()

    def test_resolve_download_dir_uses_absolute_path(self, test_bot:KleinanzeigenBot, tmp_path:Path) -> None:
        config_file = tmp_path / "config.yaml"
        test_bot.workspace = xdg_paths.Workspace.for_config(config_file, "kleinanzeigen-bot")
        test_bot.config_file_path = str(config_file)
        test_bot.browser = cast(Any, None)
        test_bot.config.download.dir = str((tmp_path / "absolute-target").resolve())

        assert test_bot._resolve_download_dir() == (tmp_path / "absolute-target").resolve()

    @pytest.mark.asyncio
    async def test_download_ads_uses_configured_relative_download_dir(self, test_bot:KleinanzeigenBot, tmp_path:Path) -> None:
        config_file = tmp_path / "config.yaml"
        test_bot.workspace = xdg_paths.Workspace.for_config(config_file, "kleinanzeigen-bot")
        test_bot.config_file_path = str(config_file)
        test_bot.config.download.dir = "./ads"
        test_bot.ads_selector = "all"
        test_bot.browser = MagicMock()

        extractor_mock = MagicMock()
        extractor_mock.extract_own_ads_urls = AsyncMock(return_value = [])

        with (
            patch.object(test_bot, "_fetch_published_ads", new_callable = AsyncMock, return_value = []),
            patch("kleinanzeigen_bot.extract.AdExtractor", return_value = extractor_mock) as mock_extractor,
        ):
            await test_bot.download_ads()

        mock_extractor.assert_called_once()
        assert mock_extractor.call_args.args[2] == (tmp_path / "ads").resolve()

    @pytest.mark.parametrize(
        ("published_ads_by_id", "ad_id", "expected_active", "expected_owned"),
        [
            ({123: {"id": 123, "state": "active"}}, 123, True, True),
            ({123: {"id": 123, "state": "inactive"}}, 123, False, True),
            ({123: {"id": 123, "state": "paused"}}, 123, False, True),
            ({123: {"id": 123}}, 123, False, True),  # Missing "state" key - treated as inactive
            ({}, 123, False, False),
        ],
    )
    def test_resolve_download_ad_activity(
        self,
        test_bot:KleinanzeigenBot,
        published_ads_by_id:dict[int, dict[str, Any]],
        ad_id:int,
        expected_active:bool,
        expected_owned:bool,
    ) -> None:
        resolved = test_bot._resolve_download_ad_activity(ad_id, published_ads_by_id)

        assert resolved.active is expected_active
        assert resolved.owned is expected_owned

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "scenario",
        [
            {
                "published_ads": [{"id": 123, "state": "active"}],
                "expected_active": True,
            },
            {
                "published_ads": [{"id": 999, "state": "active"}],
                "expected_active": False,
            },
            {
                "published_ads": [{"id": 123, "state": "inactive"}],
                "expected_active": False,
            },
            {
                "published_ads": [{"id": 123, "state": "paused"}],
                "expected_active": False,
            },
        ],
    )
    async def test_download_ads_numeric_selector_resolves_and_passes_active_state(
        self,
        test_bot:KleinanzeigenBot,
        tmp_path:Path,
        scenario:dict[str, Any],
    ) -> None:
        published_ads = scenario["published_ads"]
        expected_active = scenario["expected_active"]

        test_bot.workspace = xdg_paths.Workspace.for_config(tmp_path / "config.yaml", "kleinanzeigen-bot")
        test_bot.ads_selector = "123"
        test_bot.browser = MagicMock()

        extractor_mock = MagicMock()
        extractor_mock.navigate_to_ad_page = AsyncMock(return_value = True)
        extractor_mock.download_ad = AsyncMock()

        with (
            patch.object(test_bot, "_fetch_published_ads", new_callable = AsyncMock, return_value = published_ads) as mock_fetch_published_ads,
            patch("kleinanzeigen_bot.extract.AdExtractor", return_value = extractor_mock),
        ):
            await test_bot.download_ads()

        mock_fetch_published_ads.assert_awaited_once_with(strict = True)

        extractor_mock.download_ad.assert_awaited_once_with(123, active = expected_active)

    @pytest.mark.asyncio
    async def test_download_ads_numeric_selector_fails_when_published_ads_fetch_incomplete(self, test_bot:KleinanzeigenBot, tmp_path:Path) -> None:
        test_bot.workspace = xdg_paths.Workspace.for_config(tmp_path / "config.yaml", "kleinanzeigen-bot")
        test_bot.ads_selector = "123"
        test_bot.browser = MagicMock()

        with (
            patch.object(
                test_bot,
                "_fetch_published_ads",
                new_callable = AsyncMock,
                side_effect = PublishedAdsFetchIncompleteError("incomplete fetch"),
            ),
            patch("kleinanzeigen_bot.extract.AdExtractor") as mock_extractor,
            pytest.raises(PublishedAdsFetchIncompleteError, match = "incomplete fetch"),
        ):
            await test_bot.download_ads()

        mock_extractor.assert_not_called()

    @pytest.mark.asyncio
    async def test_download_ads_all_selector_uses_tolerant_published_ads_fetch(self, test_bot:KleinanzeigenBot, tmp_path:Path) -> None:
        test_bot.workspace = xdg_paths.Workspace.for_config(tmp_path / "config.yaml", "kleinanzeigen-bot")
        test_bot.ads_selector = "all"
        test_bot.browser = MagicMock()

        extractor_mock = MagicMock()
        extractor_mock.extract_own_ads_urls = AsyncMock(return_value = [])

        with (
            patch.object(test_bot, "_fetch_published_ads", new_callable = AsyncMock, return_value = []) as mock_fetch_published_ads,
            patch("kleinanzeigen_bot.extract.AdExtractor", return_value = extractor_mock),
        ):
            await test_bot.download_ads()

        mock_fetch_published_ads.assert_awaited_once_with(strict = False)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "scenario",
        [
            {
                "name": "active ad",
                "published_ads": [{"id": 123, "state": "active"}],
                "expected_active": True,
                "expect_ownership_warning": False,
            },
            {
                "name": "inactive ad",
                "published_ads": [{"id": 123, "state": "inactive"}],
                "expected_active": False,
                "expect_ownership_warning": False,
            },
            {
                "name": "paused ad",
                "published_ads": [{"id": 123, "state": "paused"}],
                "expected_active": False,
                "expect_ownership_warning": False,
            },
            {
                "name": "ad not in published profile",
                "published_ads": [{"id": 999, "state": "active"}],  # Different ID
                "expected_active": False,
                "expect_ownership_warning": True,
            },
        ],
        ids = lambda s: s["name"],
    )
    async def test_download_ads_all_selector_resolves_and_passes_active_state(
        self,
        test_bot:KleinanzeigenBot,
        tmp_path:Path,
        caplog:pytest.LogCaptureFixture,
        scenario:dict[str, Any],
    ) -> None:
        """Test that --ads=all resolves and passes correct active state to download_ad."""
        # Setup
        test_bot.workspace = xdg_paths.Workspace.for_config(tmp_path / "config.yaml", "kleinanzeigen-bot")
        test_bot.ads_selector = "all"
        test_bot.browser = MagicMock()

        extractor_mock = MagicMock()
        extractor_mock.extract_own_ads_urls = AsyncMock(return_value = ["https://www.kleinanzeigen.de/s-anzeige/test-ad/123-234-5678"])
        extractor_mock.extract_ad_id_from_ad_url = MagicMock(return_value = 123)
        extractor_mock.navigate_to_ad_page = AsyncMock(return_value = True)
        extractor_mock.download_ad = AsyncMock()

        caplog.set_level(logging.WARNING)

        with (
            patch.object(test_bot, "_fetch_published_ads", new_callable = AsyncMock, return_value = scenario["published_ads"]) as mock_fetch_published_ads,
            patch("kleinanzeigen_bot.extract.AdExtractor", return_value = extractor_mock),
        ):
            await test_bot.download_ads()

        # Verify published ads fetched with strict=False (tolerant mode for "all")
        mock_fetch_published_ads.assert_awaited_once_with(strict = False)

        # Verify download_ad called with correct active parameter
        extractor_mock.download_ad.assert_awaited_once_with(123, active = scenario["expected_active"])

        # Verify ownership warning only when expected
        if scenario["expect_ownership_warning"]:
            assert any("found in overview but not in published profile" in msg for msg in caplog.messages)
        else:
            assert not any("found in overview but not in published profile" in msg for msg in caplog.messages)

    @pytest.mark.asyncio
    async def test_download_ads_all_selector_skips_invalid_ad_id(
        self,
        test_bot:KleinanzeigenBot,
        tmp_path:Path,
        caplog:pytest.LogCaptureFixture,
    ) -> None:
        """Test that --ads=all skips ads with invalid URL parsing (ad_id=-1) without misleading warnings."""
        test_bot.workspace = xdg_paths.Workspace.for_config(tmp_path / "config.yaml", "kleinanzeigen-bot")
        test_bot.ads_selector = "all"
        test_bot.browser = MagicMock()

        extractor_mock = MagicMock()
        extractor_mock.extract_own_ads_urls = AsyncMock(return_value = ["https://www.kleinanzeigen.de/s-anzeige/test/invalid-url"])
        extractor_mock.extract_ad_id_from_ad_url = MagicMock(return_value = -1)  # URL parsing failed
        extractor_mock.navigate_to_ad_page = AsyncMock(return_value = True)
        extractor_mock.download_ad = AsyncMock()

        caplog.set_level(logging.WARNING)

        with (
            patch.object(test_bot, "_fetch_published_ads", new_callable = AsyncMock, return_value = []),
            patch("kleinanzeigen_bot.extract.AdExtractor", return_value = extractor_mock),
        ):
            await test_bot.download_ads()

        # Verify download_ad was NOT called for invalid ad_id
        extractor_mock.download_ad.assert_not_called()

        # Verify no misleading warning about "not in published profile"
        assert not any("not in published profile" in msg for msg in caplog.messages)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "scenario",
        [
            {
                "name": "new active ad",
                "published_ads": [{"id": 999, "state": "active"}],
                "saved_ad_ids": [123, 456],  # 999 is not saved, so it's "new"
                "expected_active": True,
            },
            {
                "name": "new inactive ad",
                "published_ads": [{"id": 999, "state": "inactive"}],
                "saved_ad_ids": [123, 456],  # 999 is not saved, so it's "new"
                "expected_active": False,
            },
            {
                "name": "new paused ad",
                "published_ads": [{"id": 999, "state": "paused"}],
                "saved_ad_ids": [123, 456],  # 999 is not saved, so it's "new"
                "expected_active": False,
            },
        ],
        ids = lambda s: s["name"],
    )
    async def test_download_ads_new_selector_resolves_and_passes_active_state(
        self,
        test_bot:KleinanzeigenBot,
        tmp_path:Path,
        scenario:dict[str, Any],
    ) -> None:
        """Test that --ads=new resolves and passes correct active state to download_ad."""
        test_bot.workspace = xdg_paths.Workspace.for_config(tmp_path / "config.yaml", "kleinanzeigen-bot")
        test_bot.ads_selector = "new"
        test_bot.browser = MagicMock()

        extractor_mock = MagicMock()
        extractor_mock.extract_own_ads_urls = AsyncMock(return_value = ["https://www.kleinanzeigen.de/s-anzeige/test-ad/999-234-5678"])
        extractor_mock.extract_ad_id_from_ad_url = MagicMock(return_value = 999)
        extractor_mock.navigate_to_ad_page = AsyncMock(return_value = True)
        extractor_mock.download_ad = AsyncMock()

        # Mock load_ads to return the saved_ad_ids
        saved_ads:list[tuple[str, MagicMock, dict[str, Any]]] = [
            (
                f"ad_{ad_id}.yaml",
                MagicMock(spec = Ad, id = ad_id),
                {},
            )
            for ad_id in scenario["saved_ad_ids"]
        ]

        with (
            patch.object(test_bot, "_fetch_published_ads", new_callable = AsyncMock, return_value = scenario["published_ads"]) as mock_fetch_published_ads,
            patch("kleinanzeigen_bot.extract.AdExtractor", return_value = extractor_mock),
            patch.object(test_bot, "load_ads", return_value = saved_ads),
        ):
            await test_bot.download_ads()

        # Verify published ads fetched with strict=False (tolerant mode for "new")
        mock_fetch_published_ads.assert_awaited_once_with(strict = False)

        # Verify download_ad called with correct active parameter
        extractor_mock.download_ad.assert_awaited_once_with(999, active = scenario["expected_active"])

    @pytest.mark.asyncio
    async def test_download_ads_new_selector_skips_already_saved(
        self,
        test_bot:KleinanzeigenBot,
        tmp_path:Path,
    ) -> None:
        """Test that --ads=new skips already-saved ads (existing behavior unchanged)."""
        test_bot.workspace = xdg_paths.Workspace.for_config(tmp_path / "config.yaml", "kleinanzeigen-bot")
        test_bot.ads_selector = "new"
        test_bot.browser = MagicMock()

        extractor_mock = MagicMock()
        extractor_mock.extract_own_ads_urls = AsyncMock(return_value = ["https://www.kleinanzeigen.de/s-anzeige/test-ad/123-234-5678"])
        extractor_mock.extract_ad_id_from_ad_url = MagicMock(return_value = 123)
        extractor_mock.navigate_to_ad_page = AsyncMock(return_value = True)
        extractor_mock.download_ad = AsyncMock()

        # Mock load_ads to return ad 123 as already saved
        saved_ads:list[tuple[str, MagicMock, dict[str, Any]]] = [("ad_123.yaml", MagicMock(spec = Ad, id = 123), {})]

        with (
            patch.object(test_bot, "_fetch_published_ads", new_callable = AsyncMock, return_value = []),
            patch("kleinanzeigen_bot.extract.AdExtractor", return_value = extractor_mock),
            patch.object(test_bot, "load_ads", return_value = saved_ads),
        ):
            await test_bot.download_ads()

        # Verify download_ad was NOT called for already-saved ad
        extractor_mock.download_ad.assert_not_called()

    @pytest.mark.asyncio
    async def test_download_ads_new_selector_skips_invalid_ad_id(
        self,
        test_bot:KleinanzeigenBot,
        tmp_path:Path,
        caplog:pytest.LogCaptureFixture,
    ) -> None:
        """Test that --ads=new skips ads with invalid URL parsing (ad_id=-1)."""
        test_bot.workspace = xdg_paths.Workspace.for_config(tmp_path / "config.yaml", "kleinanzeigen-bot")
        test_bot.ads_selector = "new"
        test_bot.browser = MagicMock()

        extractor_mock = MagicMock()
        extractor_mock.extract_own_ads_urls = AsyncMock(return_value = ["https://www.kleinanzeigen.de/s-anzeige/test/invalid-url"])
        extractor_mock.extract_ad_id_from_ad_url = MagicMock(return_value = -1)  # URL parsing failed
        extractor_mock.navigate_to_ad_page = AsyncMock(return_value = True)
        extractor_mock.download_ad = AsyncMock()

        caplog.set_level(logging.WARNING)

        with (
            patch.object(test_bot, "_fetch_published_ads", new_callable = AsyncMock, return_value = []),
            patch("kleinanzeigen_bot.extract.AdExtractor", return_value = extractor_mock),
            patch.object(test_bot, "load_ads", return_value = []),
        ):
            await test_bot.download_ads()

        # Verify download_ad was NOT called for invalid ad_id
        extractor_mock.download_ad.assert_not_called()

        # Verify no misleading warning about "not in published profile"
        assert not any("not in published profile" in msg for msg in caplog.messages)

    @pytest.mark.asyncio
    async def test_download_ads_new_selector_passes_inactive_for_ad_not_in_published_profile(
        self,
        test_bot:KleinanzeigenBot,
        tmp_path:Path,
    ) -> None:
        """Test that --ads=new passes active=False when ad is not in published profile."""
        test_bot.workspace = xdg_paths.Workspace.for_config(tmp_path / "config.yaml", "kleinanzeigen-bot")
        test_bot.ads_selector = "new"
        test_bot.browser = MagicMock()

        extractor_mock = MagicMock()
        extractor_mock.extract_own_ads_urls = AsyncMock(return_value = ["https://www.kleinanzeigen.de/s-anzeige/test-ad/999-234-5678"])
        extractor_mock.extract_ad_id_from_ad_url = MagicMock(return_value = 999)
        extractor_mock.navigate_to_ad_page = AsyncMock(return_value = True)
        extractor_mock.download_ad = AsyncMock()

        # Mock load_ads to return different saved ads (999 is new but not in published profile)
        saved_ads:list[tuple[str, MagicMock, dict[str, Any]]] = [("ad_123.yaml", MagicMock(spec = Ad, id = 123), {})]

        with (
            patch.object(test_bot, "_fetch_published_ads", new_callable = AsyncMock, return_value = []),
            patch("kleinanzeigen_bot.extract.AdExtractor", return_value = extractor_mock),
            patch.object(test_bot, "load_ads", return_value = saved_ads),
        ):
            await test_bot.download_ads()

        # Verify download_ad was called with active=False (not in profile)
        extractor_mock.download_ad.assert_awaited_once_with(999, active = False)

    @pytest.mark.asyncio
    async def test_download_ads_all_selector_skips_when_navigation_fails(
        self,
        test_bot:KleinanzeigenBot,
        tmp_path:Path,
    ) -> None:
        """Test that --ads=all skips download when navigate_to_ad_page returns False."""
        test_bot.workspace = xdg_paths.Workspace.for_config(tmp_path / "config.yaml", "kleinanzeigen-bot")
        test_bot.ads_selector = "all"
        test_bot.browser = MagicMock()

        extractor_mock = MagicMock()
        extractor_mock.extract_own_ads_urls = AsyncMock(return_value = ["https://www.kleinanzeigen.de/s-anzeige/test/123"])
        extractor_mock.extract_ad_id_from_ad_url = MagicMock(return_value = 123)
        extractor_mock.navigate_to_ad_page = AsyncMock(return_value = False)  # Navigation fails
        extractor_mock.download_ad = AsyncMock()

        with (
            patch.object(test_bot, "_fetch_published_ads", AsyncMock(return_value = [{"id": 123, "state": "active"}])),
            patch("kleinanzeigen_bot.extract.AdExtractor", return_value = extractor_mock),
        ):
            await test_bot.download_ads()

        # Verify download_ad was NOT called when navigation fails
        extractor_mock.download_ad.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("state_value", ["", "deleted", "expired", "pending", "draft", None])
    async def test_download_ads_all_selector_treats_unexpected_states_as_inactive(
        self,
        test_bot:KleinanzeigenBot,
        tmp_path:Path,
        state_value:str | None,
    ) -> None:
        """Test that unexpected state values are treated as inactive."""
        test_bot.workspace = xdg_paths.Workspace.for_config(tmp_path / "config.yaml", "kleinanzeigen-bot")
        test_bot.ads_selector = "all"
        test_bot.browser = MagicMock()

        published_ads = [{"id": 123, "state": state_value}] if state_value is not None else [{"id": 123}]

        extractor_mock = MagicMock()
        extractor_mock.extract_own_ads_urls = AsyncMock(return_value = ["https://www.kleinanzeigen.de/s-anzeige/test/123"])
        extractor_mock.extract_ad_id_from_ad_url = MagicMock(return_value = 123)
        extractor_mock.navigate_to_ad_page = AsyncMock(return_value = True)
        extractor_mock.download_ad = AsyncMock()

        with (
            patch.object(test_bot, "_fetch_published_ads", AsyncMock(return_value = published_ads)),
            patch("kleinanzeigen_bot.extract.AdExtractor", return_value = extractor_mock),
        ):
            await test_bot.download_ads()

        # All non-"active" states should result in active=False
        extractor_mock.download_ad.assert_awaited_once_with(123, active = False)

    def test_create_default_config_preserves_existing_file(self, tmp_path:Path, test_bot:KleinanzeigenBot) -> None:
        """Test that create_default_config does not overwrite an existing config file."""
        config_path = tmp_path / "config.yaml"
        original_content = "dummy: value"
        config_path.write_text(original_content)
        test_bot.config_file_path = str(config_path)
        test_bot.create_default_config()
        assert config_path.read_text() == original_content

    def test_create_default_config_creates_file(self, tmp_path:Path, test_bot:KleinanzeigenBot) -> None:
        """Test that create_default_config creates a config file if it does not exist."""
        config_path = tmp_path / "config.yaml"
        test_bot.config_file_path = str(config_path)
        assert not config_path.exists()
        test_bot.create_default_config()
        assert config_path.exists()
        content = config_path.read_text()
        assert "username: changeme" in content


class TestKleinanzeigenBotLogging:
    """Tests for logging functionality."""

    def test_configure_file_logging_adds_and_removes_handlers(self, test_bot:KleinanzeigenBot, tmp_path:Path) -> None:
        """Ensure file logging registers a handler and cleans it up afterward."""
        log_path = tmp_path / "bot.log"
        test_bot.log_file_path = str(log_path)
        root_logger = logging.getLogger()
        initial_handlers = list(root_logger.handlers)

        test_bot.configure_file_logging()

        assert test_bot.file_log is not None
        assert log_path.exists()
        assert len(root_logger.handlers) == len(initial_handlers) + 1

        test_bot.file_log.close()
        assert test_bot.file_log.is_closed()
        assert len(root_logger.handlers) == len(initial_handlers)

    def test_configure_file_logging_skips_when_path_missing(self, test_bot:KleinanzeigenBot) -> None:
        """Ensure no handler is added when no log path is configured."""
        root_logger = logging.getLogger()
        initial_handlers = list(root_logger.handlers)

        test_bot.log_file_path = None
        test_bot.configure_file_logging()

        assert test_bot.file_log is None
        assert list(root_logger.handlers) == initial_handlers

    def test_file_log_closed_after_bot_shutdown(self, tmp_path:Path) -> None:
        """Ensure the file log handler is properly closed after the bot is deleted."""

        # Directly instantiate the bot to control its lifecycle within the test
        bot = KleinanzeigenBot()
        log_path = tmp_path / "test.log"
        bot.log_file_path = str(log_path)

        bot.configure_file_logging()
        file_log = bot.file_log
        assert file_log is not None
        assert log_path.exists()
        assert not file_log.is_closed()

        # Delete and garbage collect the bot instance to ensure the destructor (__del__) is called
        del bot
        gc.collect()

        assert file_log.is_closed()


class TestKleinanzeigenBotCommandLine:
    """Tests for command line argument parsing."""

    @pytest.mark.parametrize(
        ("args", "expected_command", "expected_selector", "expected_keep_old"),
        [
            (["publish", "--ads=all"], "publish", "all", False),
            (["verify"], "verify", "due", False),
            (["download", "--ads=12345"], "download", "12345", False),
            (["publish", "--force"], "publish", "all", False),
            (["publish", "--keep-old"], "publish", "due", True),
            (["publish", "--ads=all", "--keep-old"], "publish", "all", True),
            (["download", "--ads=new"], "download", "new", False),
            (["publish", "--ads=changed"], "publish", "changed", False),
            (["publish", "--ads=changed,due"], "publish", "changed,due", False),
            (["publish", "--ads=changed,new"], "publish", "changed,new", False),
            (["version"], "version", "due", False),
        ],
    )
    def test_parse_args_handles_valid_arguments(
        self, test_bot:KleinanzeigenBot, args:list[str], expected_command:str, expected_selector:str, expected_keep_old:bool
    ) -> None:
        """Verify that valid command line arguments are parsed correctly."""
        test_bot.parse_args(["dummy"] + args)  # Add dummy arg to simulate sys.argv[0]
        assert test_bot.command == expected_command
        assert test_bot.ads_selector == expected_selector
        assert test_bot.keep_old_ads == expected_keep_old

    def test_parse_args_handles_help_command(self, test_bot:KleinanzeigenBot) -> None:
        """Verify that help command is handled correctly."""
        buf = io.StringIO()
        with pytest.raises(SystemExit) as exc_info, redirect_stdout(buf):
            test_bot.parse_args(["dummy", "--help"])
        assert exc_info.value.code == 0
        stdout = buf.getvalue()
        assert "publish" in stdout
        assert "verify" in stdout
        assert "help" in stdout
        assert "version" in stdout
        assert "--verbose" in stdout

    def test_parse_args_handles_verbose_flag(self, test_bot:KleinanzeigenBot) -> None:
        """Verify that verbose flag sets correct log level."""
        test_bot.parse_args(["dummy", "--verbose"])
        assert loggers.is_debug(LOG)

    def test_parse_args_handles_config_path(self, test_bot:KleinanzeigenBot, test_data_dir:str) -> None:
        """Verify that config path is set correctly."""
        config_path = Path(test_data_dir) / "custom_config.yaml"
        test_bot.parse_args(["dummy", "--config", str(config_path)])
        assert test_bot.config_file_path == str(config_path.absolute())

    def test_parse_args_create_config(self, test_bot:KleinanzeigenBot) -> None:
        """Test parsing of create-config command"""
        test_bot.parse_args(["app", "create-config"])
        assert test_bot.command == "create-config"


class TestKleinanzeigenBotConfiguration:
    """Tests for configuration loading and validation."""

    def test_load_config_handles_missing_file(self, test_bot:KleinanzeigenBot, test_data_dir:str) -> None:
        """Verify that loading a missing config file creates default config. No info log is expected anymore."""
        config_path = Path(test_data_dir) / "missing_config.yaml"
        config_path.unlink(missing_ok = True)
        test_bot.config_file_path = str(config_path)
        test_bot.load_config()
        assert config_path.exists()

    def test_load_config_validates_required_fields(self, test_bot:KleinanzeigenBot, test_data_dir:str) -> None:
        """Verify that config validation checks required fields."""
        config_path = Path(test_data_dir) / "config.yaml"
        config_content = """
login:
  username: dummy_user
  # Missing password
"""
        with open(config_path, "w", encoding = "utf-8") as f:
            f.write(config_content)
        test_bot.config_file_path = str(config_path)

        with pytest.raises(ValidationError) as exc_info:
            test_bot.load_config()
        assert "login.username" not in str(exc_info.value)
        assert "login.password" in str(exc_info.value)


class TestKleinanzeigenBotAuthentication:
    """Tests for login and authentication functionality."""

    @pytest.mark.asyncio
    async def test_is_logged_in_returns_true_when_logged_in(self, test_bot:KleinanzeigenBot) -> None:
        """Verify that login check returns true when logged in."""
        with patch.object(
            test_bot,
            "web_text_first_available",
            new_callable = AsyncMock,
            return_value = ("Welcome dummy_user", 0),
        ):
            assert await test_bot.is_logged_in() is True

    @pytest.mark.asyncio
    async def test_is_logged_in_returns_false_when_not_logged_in(self, test_bot:KleinanzeigenBot) -> None:
        """Verify that login check returns false when not logged in."""
        with (
            patch.object(
                test_bot,
                "web_text_first_available",
                new_callable = AsyncMock,
                side_effect = [("nicht-eingeloggt", 0), ("kein user signal", 0)],
            ),
            patch.object(test_bot, "_has_logged_out_cta", new_callable = AsyncMock, return_value = False),
        ):
            assert await test_bot.is_logged_in() is False

    @pytest.mark.asyncio
    async def test_has_logged_out_cta_requires_visible_candidate(self, test_bot:KleinanzeigenBot) -> None:
        matched_element = MagicMock(spec = Element)
        with (
            patch.object(test_bot, "web_find_first_available", new_callable = AsyncMock, return_value = (matched_element, 0)),
            patch.object(test_bot, "_extract_visible_text", new_callable = AsyncMock, return_value = ""),
        ):
            assert await test_bot._has_logged_out_cta() is False

    @pytest.mark.asyncio
    async def test_has_logged_out_cta_accepts_visible_candidate(self, test_bot:KleinanzeigenBot) -> None:
        matched_element = MagicMock(spec = Element)
        with (
            patch.object(test_bot, "web_find_first_available", new_callable = AsyncMock, return_value = (matched_element, 0)),
            patch.object(test_bot, "_extract_visible_text", new_callable = AsyncMock, return_value = "Einloggen"),
        ):
            assert await test_bot._has_logged_out_cta() is True

    @pytest.mark.asyncio
    async def test_is_logged_in_uses_selector_group_timeout_key(self, test_bot:KleinanzeigenBot) -> None:
        """Verify login detection uses selector-group lookup with login_detection timeout key."""
        with patch.object(
            test_bot,
            "web_text_first_available",
            new_callable = AsyncMock,
            side_effect = [TimeoutError(), ("Welcome dummy_user", 0)],
        ) as group_text:
            assert await test_bot.is_logged_in() is True

        group_text.assert_awaited()
        assert any(call.kwargs.get("timeout") == test_bot._timeout("login_detection") for call in group_text.await_args_list)

    @pytest.mark.asyncio
    async def test_is_logged_in_runs_full_selector_group_before_cta_precheck(self, test_bot:KleinanzeigenBot) -> None:
        """Quick CTA checks must not short-circuit before full logged-in selector checks."""
        with patch.object(
            test_bot,
            "web_text_first_available",
            new_callable = AsyncMock,
            side_effect = [TimeoutError(), ("Welcome dummy_user", 0)],
        ) as group_text:
            assert await test_bot.is_logged_in() is True

        group_text.assert_awaited()
        assert group_text.await_count >= 1

    @pytest.mark.asyncio
    async def test_is_logged_in_short_circuits_before_cta_check_when_quick_user_signal_matches(self, test_bot:KleinanzeigenBot) -> None:
        """Logged-in quick pre-check should win even if incidental login links exist elsewhere."""
        with patch.object(
            test_bot,
            "web_text_first_available",
            new_callable = AsyncMock,
            return_value = ("angemeldet als: dummy_user", 0),
        ) as group_text:
            assert await test_bot.is_logged_in() is True

        group_text.assert_awaited()
        assert group_text.await_count >= 1

    @pytest.mark.asyncio
    async def test_get_login_state_prefers_dom_checks(self, test_bot:KleinanzeigenBot) -> None:
        with (
            patch.object(
                test_bot,
                "web_text_first_available",
                new_callable = AsyncMock,
                return_value = ("Welcome dummy_user", 0),
            ) as web_text,
        ):
            result = await test_bot.get_login_state()
            assert result.is_logged_in is True
            assert result.reason == LoginDetectionReason.USER_INFO_MATCH
            web_text.assert_awaited_once()

    def test_current_page_url_strips_query_and_fragment(self, test_bot:KleinanzeigenBot) -> None:
        page = MagicMock()
        page.url = "https://login.kleinanzeigen.de/u/login/password?state=secret&code=abc#frag"
        test_bot.page = page

        assert test_bot._current_page_url() == "https://login.kleinanzeigen.de/u/login/password"

    def test_is_valid_post_auth0_destination_filters_invalid_urls(self, test_bot:KleinanzeigenBot) -> None:
        assert test_bot._is_valid_post_auth0_destination("https://www.kleinanzeigen.de/") is True
        assert test_bot._is_valid_post_auth0_destination("https://www.kleinanzeigen.de/m-meine-anzeigen.html") is True
        assert test_bot._is_valid_post_auth0_destination("https://foo.kleinanzeigen.de/") is True
        assert test_bot._is_valid_post_auth0_destination("unknown") is False
        assert test_bot._is_valid_post_auth0_destination("about:blank") is False
        assert test_bot._is_valid_post_auth0_destination("https://evilkleinanzeigen.de/") is False
        assert test_bot._is_valid_post_auth0_destination("https://kleinanzeigen.de.evil.com/") is False
        assert test_bot._is_valid_post_auth0_destination("https://login.kleinanzeigen.de/u/login/password") is False
        assert test_bot._is_valid_post_auth0_destination("https://www.kleinanzeigen.de/login-error-500") is False

    @pytest.mark.asyncio
    async def test_get_login_state_returns_selector_timeout_when_dom_checks_are_inconclusive(self, test_bot:KleinanzeigenBot) -> None:
        with (
            patch.object(test_bot, "web_text_first_available", side_effect = [TimeoutError(), TimeoutError()]) as web_text,
            patch.object(test_bot, "web_find_first_available", side_effect = TimeoutError()) as cta_find,
        ):
            result = await test_bot.get_login_state()
            assert result.is_logged_in is False
            assert result.reason == LoginDetectionReason.SELECTOR_TIMEOUT
            assert web_text.await_count == 2
            assert cta_find.await_count == 1

    @pytest.mark.asyncio
    async def test_get_login_state_returns_logged_out_when_cta_detected(self, test_bot:KleinanzeigenBot) -> None:
        matched_element = MagicMock(spec = Element)
        with (
            patch.object(
                test_bot,
                "web_text_first_available",
                side_effect = [TimeoutError(), TimeoutError()],
            ) as web_text,
            patch.object(test_bot, "web_find_first_available", new_callable = AsyncMock, return_value = (matched_element, 0)),
            patch.object(test_bot, "_extract_visible_text", new_callable = AsyncMock, return_value = "Hier einloggen"),
        ):
            result = await test_bot.get_login_state()
            assert result.is_logged_in is False
            assert result.reason == LoginDetectionReason.CTA_MATCH
            assert web_text.await_count == 2

    @pytest.mark.asyncio
    async def test_get_login_state_checks_logged_out_cta_only_once(self, test_bot:KleinanzeigenBot) -> None:
        with (
            patch.object(test_bot, "_has_logged_in_marker", new_callable = AsyncMock, return_value = False),
            patch.object(test_bot, "_has_logged_out_cta", new_callable = AsyncMock, return_value = False) as cta_check,
        ):
            result = await test_bot.get_login_state(capture_diagnostics = False)
            assert result.is_logged_in is False
            assert result.reason == LoginDetectionReason.SELECTOR_TIMEOUT
            cta_check.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_login_state_selector_timeout_captures_diagnostics_when_enabled(self, test_bot:KleinanzeigenBot, tmp_path:Path) -> None:
        test_bot.config.diagnostics = DiagnosticsConfig.model_validate({"capture_on": {"login_detection": True}, "output_dir": str(tmp_path)})

        page = MagicMock()
        page.save_screenshot = AsyncMock()
        page.get_content = AsyncMock(return_value = "<html></html>")
        test_bot.page = page

        with (
            patch.object(test_bot, "web_text_first_available", side_effect = [TimeoutError(), TimeoutError(), TimeoutError(), TimeoutError()]),
            patch.object(test_bot, "web_find_first_available", side_effect = TimeoutError()),
        ):
            result = await test_bot.get_login_state()
            assert result.is_logged_in is False
            assert result.reason == LoginDetectionReason.SELECTOR_TIMEOUT

        page.save_screenshot.assert_awaited_once()
        page.get_content.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_login_state_selector_timeout_does_not_capture_diagnostics_when_disabled(self, test_bot:KleinanzeigenBot, tmp_path:Path) -> None:
        test_bot.config.diagnostics = DiagnosticsConfig.model_validate({"capture_on": {"login_detection": False}, "output_dir": str(tmp_path)})

        page = MagicMock()
        page.save_screenshot = AsyncMock()
        page.get_content = AsyncMock(return_value = "<html></html>")
        test_bot.page = page

        with (
            patch.object(test_bot, "web_text_first_available", side_effect = [TimeoutError(), TimeoutError(), TimeoutError(), TimeoutError()]),
            patch.object(test_bot, "web_find_first_available", side_effect = TimeoutError()),
        ):
            result = await test_bot.get_login_state()
            assert result.is_logged_in is False
            assert result.reason == LoginDetectionReason.SELECTOR_TIMEOUT

        page.save_screenshot.assert_not_called()
        page.get_content.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_login_state_selector_timeout_pauses_for_inspection_when_enabled_and_interactive(
        self, test_bot:KleinanzeigenBot, tmp_path:Path
    ) -> None:
        test_bot.config.diagnostics = DiagnosticsConfig.model_validate(
            {"capture_on": {"login_detection": True}, "pause_on_login_detection_failure": True, "output_dir": str(tmp_path)}
        )

        page = MagicMock()
        page.save_screenshot = AsyncMock()
        page.get_content = AsyncMock(return_value = "<html></html>")
        test_bot.page = page

        stdin_mock = MagicMock()
        stdin_mock.isatty.return_value = True

        with (
            patch.object(
                test_bot,
                "web_text_first_available",
                side_effect = [
                    TimeoutError(),
                    TimeoutError(),
                    TimeoutError(),
                    TimeoutError(),
                    TimeoutError(),
                    TimeoutError(),
                    TimeoutError(),
                    TimeoutError(),
                ],
            ),
            patch.object(test_bot, "web_find_first_available", side_effect = TimeoutError()),
            patch("kleinanzeigen_bot.sys.stdin", stdin_mock),
            patch("kleinanzeigen_bot.ainput", new_callable = AsyncMock) as mock_ainput,
        ):
            first_result = await test_bot.get_login_state()
            assert first_result.is_logged_in is False
            assert first_result.reason == LoginDetectionReason.SELECTOR_TIMEOUT
            # Call twice to ensure the capture/pause guard triggers only once per process.
            second_result = await test_bot.get_login_state()
            assert second_result.is_logged_in is False
            assert second_result.reason == LoginDetectionReason.SELECTOR_TIMEOUT

        page.save_screenshot.assert_awaited_once()
        page.get_content.assert_awaited_once()
        mock_ainput.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_login_state_selector_timeout_does_not_pause_when_non_interactive(self, test_bot:KleinanzeigenBot, tmp_path:Path) -> None:
        test_bot.config.diagnostics = DiagnosticsConfig.model_validate(
            {"capture_on": {"login_detection": True}, "pause_on_login_detection_failure": True, "output_dir": str(tmp_path)}
        )

        page = MagicMock()
        page.save_screenshot = AsyncMock()
        page.get_content = AsyncMock(return_value = "<html></html>")
        test_bot.page = page

        stdin_mock = MagicMock()
        stdin_mock.isatty.return_value = False

        with (
            patch.object(test_bot, "web_text_first_available", side_effect = [TimeoutError(), TimeoutError(), TimeoutError(), TimeoutError()]),
            patch.object(test_bot, "web_find_first_available", side_effect = TimeoutError()),
            patch("kleinanzeigen_bot.sys.stdin", stdin_mock),
            patch("kleinanzeigen_bot.ainput", new_callable = AsyncMock) as mock_ainput,
        ):
            result = await test_bot.get_login_state()
            assert result.is_logged_in is False
            assert result.reason == LoginDetectionReason.SELECTOR_TIMEOUT

        mock_ainput.assert_not_called()

    @pytest.mark.asyncio
    async def test_login_flow_completes_successfully(self, test_bot:KleinanzeigenBot) -> None:
        """Verify that normal login flow completes successfully."""
        with (
            patch.object(test_bot, "web_open") as mock_open,
            patch.object(
                test_bot,
                "get_login_state",
                new_callable = AsyncMock,
                side_effect = [
                    _login_detection_result(False, LoginDetectionReason.CTA_MATCH),
                    _login_detection_result(True, LoginDetectionReason.USER_INFO_MATCH),
                ],
            ) as mock_logged_in,
            patch.object(test_bot, "_click_gdpr_banner", new_callable = AsyncMock),
            patch.object(test_bot, "fill_login_data_and_send", new_callable = AsyncMock) as mock_fill,
            patch.object(test_bot, "handle_after_login_logic", new_callable = AsyncMock) as mock_after_login,
            patch.object(test_bot, "_dismiss_consent_banner", new_callable = AsyncMock),
        ):
            await test_bot.login()

            opened_urls = [call.args[0] for call in mock_open.call_args_list]
            assert any(url.startswith(test_bot.root_url) for url in opened_urls)
            assert any(url.endswith("/m-einloggen-sso.html") for url in opened_urls)
            mock_logged_in.assert_awaited()
            mock_fill.assert_awaited_once()
            mock_after_login.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_login_flow_returns_early_when_already_logged_in(self, test_bot:KleinanzeigenBot) -> None:
        """Login should return early when state is already LOGGED_IN."""
        with (
            patch.object(test_bot, "web_open") as mock_open,
            patch.object(
                test_bot,
                "get_login_state",
                new_callable = AsyncMock,
                return_value = _login_detection_result(True, LoginDetectionReason.USER_INFO_MATCH),
            ) as mock_state,
            patch.object(test_bot, "_click_gdpr_banner", new_callable = AsyncMock),
            patch.object(test_bot, "fill_login_data_and_send", new_callable = AsyncMock) as mock_fill,
            patch.object(test_bot, "handle_after_login_logic", new_callable = AsyncMock) as mock_after_login,
        ):
            await test_bot.login()

            mock_open.assert_awaited_once()
            assert mock_open.await_args is not None
            assert mock_open.await_args.args[0] == test_bot.root_url
            mock_state.assert_awaited_once()
            mock_fill.assert_not_called()
            mock_after_login.assert_not_called()

    @pytest.mark.asyncio
    async def test_login_flow_raises_when_state_remains_inconclusive(self, test_bot:KleinanzeigenBot) -> None:
        """Post-login inconclusive state should fail fast with diagnostics."""
        with (
            patch.object(test_bot, "web_open"),
            patch.object(
                test_bot,
                "get_login_state",
                new_callable = AsyncMock,
                side_effect = [
                    _login_detection_result(False, LoginDetectionReason.CTA_MATCH),
                    _login_detection_result(False, LoginDetectionReason.SELECTOR_TIMEOUT),
                ],
            ) as mock_state,
            patch.object(test_bot, "_click_gdpr_banner", new_callable = AsyncMock),
            patch.object(test_bot, "fill_login_data_and_send", new_callable = AsyncMock),
            patch.object(test_bot, "handle_after_login_logic", new_callable = AsyncMock),
            patch.object(test_bot, "_dismiss_consent_banner", new_callable = AsyncMock),
            patch.object(test_bot, "_capture_login_detection_diagnostics_if_enabled", new_callable = AsyncMock) as mock_diagnostics,
        ):
            with pytest.raises(AssertionError, match = "reason=SELECTOR_TIMEOUT"):
                await test_bot.login()

            mock_diagnostics.assert_awaited_once()
            assert mock_diagnostics.await_args is not None
            assert mock_diagnostics.await_args.kwargs.get("base_prefix") == "login_detection_selector_timeout"
            mock_state.assert_awaited()
            assert mock_state.await_count == 2
            assert mock_state.await_args_list[1].kwargs.get("capture_diagnostics") is False

    @pytest.mark.asyncio
    async def test_capture_login_detection_diagnostics_honors_capture_log_copy(self, test_bot:KleinanzeigenBot, tmp_path:Path) -> None:
        test_bot.config.diagnostics = DiagnosticsConfig.model_validate(
            {
                "capture_on": {"login_detection": True},
                "capture_log_copy": True,
                "output_dir": str(tmp_path),
            }
        )
        test_bot.log_file_path = str(tmp_path / "bot.log")
        test_bot._login_detection_diagnostics_captured = False

        page = MagicMock()
        test_bot.page = page

        with patch("kleinanzeigen_bot.diagnostics.capture_diagnostics", new_callable = AsyncMock) as mock_capture:
            await test_bot._capture_login_detection_diagnostics_if_enabled(base_prefix = "login_detection_test")

            mock_capture.assert_awaited_once()
            assert mock_capture.await_args is not None
            assert mock_capture.await_args.kwargs.get("base_prefix") == "login_detection_test"
            assert mock_capture.await_args.kwargs.get("copy_log") is True
            assert mock_capture.await_args.kwargs.get("log_file_path") == test_bot.log_file_path
            assert test_bot._login_detection_diagnostics_captured is True

    @pytest.mark.asyncio
    async def test_capture_login_detection_diagnostics_with_no_page_still_invokes_capture(self, test_bot:KleinanzeigenBot, tmp_path:Path) -> None:
        test_bot.config.diagnostics = DiagnosticsConfig.model_validate(
            {
                "capture_on": {"login_detection": True},
                "capture_log_copy": True,
                "output_dir": str(tmp_path),
            }
        )
        test_bot.log_file_path = str(tmp_path / "bot.log")
        test_bot._login_detection_diagnostics_captured = False
        test_bot.page = cast(Any, None)

        with patch("kleinanzeigen_bot.diagnostics.capture_diagnostics", new_callable = AsyncMock) as mock_capture:
            await test_bot._capture_login_detection_diagnostics_if_enabled(base_prefix = "login_detection_test")

            mock_capture.assert_awaited_once()
            assert mock_capture.await_args is not None
            assert mock_capture.await_args.kwargs.get("page") is None
            assert mock_capture.await_args.kwargs.get("copy_log") is True
            assert mock_capture.await_args.kwargs.get("log_file_path") == test_bot.log_file_path
            assert test_bot._login_detection_diagnostics_captured is True

    @pytest.mark.asyncio
    async def test_capture_login_detection_diagnostics_does_not_mark_captured_on_output_dir_error(self, test_bot:KleinanzeigenBot, tmp_path:Path) -> None:
        test_bot.config.diagnostics = DiagnosticsConfig.model_validate(
            {
                "capture_on": {"login_detection": True},
                "capture_log_copy": True,
                "output_dir": str(tmp_path),
            }
        )
        test_bot._login_detection_diagnostics_captured = False
        test_bot.page = MagicMock()

        with (
            patch.object(test_bot, "_diagnostics_output_dir", side_effect = RuntimeError("dir error")),
            patch("kleinanzeigen_bot.diagnostics.capture_diagnostics", new_callable = AsyncMock) as mock_capture,
        ):
            await test_bot._capture_login_detection_diagnostics_if_enabled(base_prefix = "login_detection_test")

            mock_capture.assert_not_awaited()
            assert test_bot._login_detection_diagnostics_captured is False

    @pytest.mark.asyncio
    async def test_capture_login_detection_diagnostics_does_not_mark_captured_on_capture_error(self, test_bot:KleinanzeigenBot, tmp_path:Path) -> None:
        test_bot.config.diagnostics = DiagnosticsConfig.model_validate(
            {
                "capture_on": {"login_detection": True},
                "capture_log_copy": True,
                "output_dir": str(tmp_path),
            }
        )
        test_bot._login_detection_diagnostics_captured = False
        test_bot.page = MagicMock()

        with patch(
            "kleinanzeigen_bot.diagnostics.capture_diagnostics",
            new_callable = AsyncMock,
            side_effect = RuntimeError("capture error"),
        ):
            await test_bot._capture_login_detection_diagnostics_if_enabled(base_prefix = "login_detection_test")

            assert test_bot._login_detection_diagnostics_captured is False

    def test_login_detection_result_accepts_logged_in_user_info_match(self) -> None:
        result = LoginDetectionResult(is_logged_in = True, reason = LoginDetectionReason.USER_INFO_MATCH)
        assert result.is_logged_in is True
        assert result.reason == LoginDetectionReason.USER_INFO_MATCH

    @pytest.mark.parametrize("reason", [LoginDetectionReason.CTA_MATCH, LoginDetectionReason.SELECTOR_TIMEOUT])
    def test_login_detection_result_rejects_invalid_logged_in_reason(self, reason:LoginDetectionReason) -> None:
        with pytest.raises(ValueError, match = "USER_INFO_MATCH"):
            LoginDetectionResult(is_logged_in = True, reason = reason)

    def test_login_detection_result_rejects_invalid_logged_out_user_info_match(self) -> None:
        with pytest.raises(ValueError, match = "CTA_MATCH or SELECTOR_TIMEOUT"):
            LoginDetectionResult(is_logged_in = False, reason = LoginDetectionReason.USER_INFO_MATCH)

    def test_login_detection_result_rejects_non_bool_is_logged_in(self) -> None:
        with pytest.raises(TypeError, match = "is_logged_in must be a bool"):
            LoginDetectionResult(is_logged_in = "yes", reason = LoginDetectionReason.CTA_MATCH)  # type: ignore[arg-type]

    def test_login_detection_result_rejects_non_enum_reason(self) -> None:
        with pytest.raises(TypeError, match = "reason must be a LoginDetectionReason"):
            LoginDetectionResult(is_logged_in = False, reason = "bogus")  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_login_flow_raises_when_sso_navigation_times_out(self, test_bot:KleinanzeigenBot) -> None:
        """SSO navigation timeout should trigger diagnostics and re-raise."""
        with (
            patch.object(test_bot, "web_open", new_callable = AsyncMock, side_effect = [None, TimeoutError("sso timeout")]),
            patch.object(
                test_bot,
                "get_login_state",
                new_callable = AsyncMock,
                return_value = _login_detection_result(False, LoginDetectionReason.CTA_MATCH),
            ) as mock_state,
            patch.object(test_bot, "_click_gdpr_banner", new_callable = AsyncMock),
            patch.object(test_bot, "_capture_login_detection_diagnostics_if_enabled", new_callable = AsyncMock) as mock_diagnostics,
        ):
            with pytest.raises(TimeoutError, match = "sso timeout"):
                await test_bot.login()

            mock_diagnostics.assert_awaited_once()
            assert mock_diagnostics.await_args is not None
            assert mock_diagnostics.await_args.kwargs.get("base_prefix") == "login_detection_sso_navigation_timeout"
            mock_state.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_check_and_wait_for_captcha(self, test_bot:KleinanzeigenBot) -> None:
        """Verify that captcha detection works correctly."""
        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock) as mock_probe,
            patch("kleinanzeigen_bot.ainput", new_callable = AsyncMock) as mock_ainput,
        ):
            # Test case 1: Captcha found
            mock_probe.return_value = MagicMock()
            mock_ainput.return_value = ""

            await test_bot.check_and_wait_for_captcha(is_login_page = True)

            mock_ainput.assert_awaited_once()

            # Test case 2: No captcha
            mock_probe.return_value = None
            mock_ainput.reset_mock()

            await test_bot.check_and_wait_for_captcha(is_login_page = True)

            mock_ainput.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_fill_login_data_and_send(self, test_bot:KleinanzeigenBot) -> None:
        """Verify that login form filling works correctly."""
        with (
            patch.object(test_bot, "_wait_for_auth0_login_context", new_callable = AsyncMock) as wait_context,
            patch.object(test_bot, "_wait_for_auth0_password_step", new_callable = AsyncMock) as wait_password,
            patch.object(test_bot, "_wait_for_post_auth0_submit_transition", new_callable = AsyncMock) as wait_transition,
            patch.object(test_bot, "web_input") as mock_input,
            patch.object(test_bot, "web_click") as mock_click,
            patch.object(test_bot, "check_and_wait_for_captcha", new_callable = AsyncMock) as mock_captcha,
        ):
            await test_bot.fill_login_data_and_send()

            wait_context.assert_awaited_once()
            wait_password.assert_awaited_once()
            wait_transition.assert_awaited_once()
            mock_captcha.assert_awaited_once_with(is_login_page = True)
            assert mock_input.call_args_list == [
                call(By.ID, "username", test_bot.config.login.username),
                call(By.CSS_SELECTOR, "input[type='password']", test_bot.config.login.password),
            ]
            assert mock_click.call_args_list == [
                call(By.CSS_SELECTOR, "button[type='submit']"),
                call(By.CSS_SELECTOR, "button[type='submit']"),
            ]

    @pytest.mark.asyncio
    async def test_fill_login_data_and_send_fails_when_password_step_missing(self, test_bot:KleinanzeigenBot) -> None:
        """Missing Auth0 password step should fail fast."""
        with (
            patch.object(test_bot, "_wait_for_auth0_login_context", new_callable = AsyncMock),
            patch.object(test_bot, "_wait_for_auth0_password_step", new_callable = AsyncMock, side_effect = AssertionError("missing password")),
            patch.object(test_bot, "web_input") as mock_input,
            patch.object(test_bot, "web_click") as mock_click,
        ):
            with pytest.raises(AssertionError, match = "missing password"):
                await test_bot.fill_login_data_and_send()

            assert mock_input.call_count == 1
            assert mock_click.call_count == 1

    @pytest.mark.asyncio
    async def test_wait_for_post_auth0_submit_transition_url_branch(self, test_bot:KleinanzeigenBot) -> None:
        """URL transition success should return without fallback checks."""
        with (
            patch.object(test_bot, "web_await", new_callable = AsyncMock, return_value = True) as mock_wait,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as mock_sleep,
        ):
            await test_bot._wait_for_post_auth0_submit_transition()

            mock_wait.assert_awaited_once()
            mock_sleep.assert_not_called()

    @pytest.mark.asyncio
    async def test_wait_for_post_auth0_submit_transition_dom_fallback_branch(self, test_bot:KleinanzeigenBot) -> None:
        """DOM fallback should run when URL transition is inconclusive."""
        with (
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = [TimeoutError()]) as mock_wait,
            patch.object(test_bot, "is_logged_in", new_callable = AsyncMock, return_value = True) as mock_is_logged_in,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as mock_sleep,
        ):
            await test_bot._wait_for_post_auth0_submit_transition()

            mock_wait.assert_awaited_once()
            mock_is_logged_in.assert_awaited_once()
            mock_sleep.assert_not_called()

    @pytest.mark.asyncio
    async def test_wait_for_post_auth0_submit_transition_sleep_fallback_branch(self, test_bot:KleinanzeigenBot) -> None:
        """Sleep fallback should run when bounded login check times out."""
        with (
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = [TimeoutError()]) as mock_wait,
            patch.object(test_bot, "is_logged_in", new_callable = AsyncMock, side_effect = asyncio.TimeoutError) as mock_is_logged_in,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as mock_sleep,
        ):
            with pytest.raises(TimeoutError, match = "Auth0 post-submit verification remained inconclusive"):
                await test_bot._wait_for_post_auth0_submit_transition()

            mock_wait.assert_awaited_once()
            assert mock_is_logged_in.await_count == 2
            mock_sleep.assert_awaited_once()
            assert mock_sleep.await_args is not None
            sleep_kwargs = cast(Any, mock_sleep.await_args).kwargs
            assert sleep_kwargs["min_ms"] < sleep_kwargs["max_ms"]

    @pytest.mark.asyncio
    async def test_click_gdpr_banner_uses_quick_dom_timeout_and_clicks_found_element(self, test_bot:KleinanzeigenBot) -> None:
        mock_element = AsyncMock()
        with (
            patch.object(test_bot, "_timeout", return_value = 1.25) as mock_timeout,
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = mock_element) as mock_probe,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as mock_sleep,
        ):
            await test_bot._click_gdpr_banner()

            mock_timeout.assert_called_once_with("quick_dom")
            mock_probe.assert_awaited_once()
            assert mock_probe.await_args is not None
            assert mock_probe.await_args.args == (By.ID, "gdpr-banner-accept")
            mock_element.click.assert_awaited_once()
            mock_sleep.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_click_gdpr_banner_does_nothing_when_banner_absent(self, test_bot:KleinanzeigenBot) -> None:
        with (
            patch.object(test_bot, "_timeout", return_value = 1.25),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = None) as mock_probe,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as mock_sleep,
        ):
            await test_bot._click_gdpr_banner()

            mock_probe.assert_awaited_once()
            mock_sleep.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dismiss_consent_banner_clicks_when_present(self, test_bot:KleinanzeigenBot) -> None:
        mock_element = AsyncMock()
        with (
            patch.object(test_bot, "_timeout", return_value = 2.0) as mock_timeout,
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = mock_element) as mock_probe,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as mock_sleep,
        ):
            await test_bot._dismiss_consent_banner()

            mock_timeout.assert_called_once_with("quick_dom")
            mock_probe.assert_awaited_once()
            assert mock_probe.await_args is not None
            assert mock_probe.await_args.args == (By.ID, "gdpr-banner-accept")
            mock_element.click.assert_awaited_once()
            mock_sleep.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dismiss_consent_banner_does_nothing_when_absent(self, test_bot:KleinanzeigenBot) -> None:
        with (
            patch.object(test_bot, "_timeout", return_value = 2.0),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = None) as mock_probe,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as mock_sleep,
        ):
            await test_bot._dismiss_consent_banner()

            mock_probe.assert_awaited_once()
            mock_sleep.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_check_sms_verification_prompts_user_when_detected(self, test_bot:KleinanzeigenBot) -> None:
        mock_element = MagicMock()
        with (
            patch.object(test_bot, "_timeout", return_value = 3.0) as mock_timeout,
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = mock_element) as mock_probe,
            patch("kleinanzeigen_bot.ainput", new_callable = AsyncMock) as mock_ainput,
        ):
            await test_bot._check_sms_verification()

            mock_timeout.assert_called_once_with("sms_verification")
            mock_probe.assert_awaited_once()
            assert mock_probe.await_args is not None
            assert mock_probe.await_args.args == (By.TEXT, "Wir haben dir gerade einen 6-stelligen Code für die Telefonnummer")
            mock_ainput.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_check_sms_verification_returns_silently_when_absent(self, test_bot:KleinanzeigenBot) -> None:
        with (
            patch.object(test_bot, "_timeout", return_value = 3.0),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = None) as mock_probe,
            patch("kleinanzeigen_bot.ainput", new_callable = AsyncMock) as mock_ainput,
        ):
            await test_bot._check_sms_verification()

            mock_probe.assert_awaited_once()
            mock_ainput.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_check_email_verification_prompts_user_when_detected(self, test_bot:KleinanzeigenBot) -> None:
        mock_element = MagicMock()
        with (
            patch.object(test_bot, "_timeout", return_value = 4.0) as mock_timeout,
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = mock_element) as mock_probe,
            patch("kleinanzeigen_bot.ainput", new_callable = AsyncMock) as mock_ainput,
        ):
            await test_bot._check_email_verification()

            mock_timeout.assert_called_once_with("email_verification")
            mock_probe.assert_awaited_once()
            assert mock_probe.await_args is not None
            assert mock_probe.await_args.args == (By.TEXT, "Um dein Konto zu schützen haben wir dir eine E-Mail geschickt")
            mock_ainput.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_check_email_verification_returns_silently_when_absent(self, test_bot:KleinanzeigenBot) -> None:
        with (
            patch.object(test_bot, "_timeout", return_value = 4.0),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = None) as mock_probe,
            patch("kleinanzeigen_bot.ainput", new_callable = AsyncMock) as mock_ainput,
        ):
            await test_bot._check_email_verification()

            mock_probe.assert_awaited_once()
            mock_ainput.assert_not_awaited()


class TestKleinanzeigenBotDiagnostics:
    @pytest.fixture
    def diagnostics_ad_config(self) -> dict[str, Any]:
        return {
            "active": True,
            "type": "OFFER",
            "title": "Test ad title",
            "description": "Test description",
            "category": "161/176/sonstige",
            "price_type": "NEGOTIABLE",
            "shipping_type": "PICKUP",
            "sell_directly": False,
            "contact": {
                "name": "Tester",
                "zipcode": "12345",
            },
            "republication_interval": 7,
        }

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_publish_ads_captures_diagnostics_on_failures(
        self,
        test_bot:KleinanzeigenBot,
        tmp_path:Path,
        diagnostics_ad_config:dict[str, Any],
    ) -> None:
        """Ensure publish failures capture diagnostics artifacts."""
        log_file_path = tmp_path / "test.log"
        log_file_path.write_text("Test log content\n", encoding = "utf-8")
        test_bot.log_file_path = str(log_file_path)

        test_bot.config.diagnostics = DiagnosticsConfig.model_validate({"capture_on": {"publish": True}, "output_dir": str(tmp_path)})

        page = MagicMock()
        page.save_screenshot = AsyncMock()
        page.get_content = AsyncMock(return_value = "<html></html>")
        page.sleep = AsyncMock()
        page.url = "https://example.com/fail"
        test_bot.page = page

        ad_cfg = Ad.model_validate(diagnostics_ad_config)
        ad_cfg_orig = copy.deepcopy(diagnostics_ad_config)
        ad_file = str(tmp_path / "ad_000001_Test.yml")
        ads_response = {"content": json.dumps({"ads": [], "paging": {"pageNum": 1, "last": 1}})}

        with (
            patch.object(test_bot, "web_request", new_callable = AsyncMock, return_value = ads_response),
            patch.object(test_bot, "publish_ad", new_callable = AsyncMock, side_effect = TimeoutError("boom")),
        ):
            await test_bot.publish_ads([(ad_file, ad_cfg, ad_cfg_orig)])

        expected_retries = SUBMISSION_MAX_RETRIES
        assert page.save_screenshot.await_count == expected_retries
        assert page.get_content.await_count == expected_retries
        entries = os.listdir(tmp_path)
        html_files = [name for name in entries if fnmatch.fnmatch(name, "publish_error_*_attempt*_ad_000001_Test.html")]
        json_files = [name for name in entries if fnmatch.fnmatch(name, "publish_error_*_attempt*_ad_000001_Test.json")]
        assert len(html_files) == expected_retries
        assert len(json_files) == expected_retries

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_publish_ads_captures_log_copy_when_enabled(
        self,
        test_bot:KleinanzeigenBot,
        tmp_path:Path,
        diagnostics_ad_config:dict[str, Any],
    ) -> None:
        """Ensure publish failures copy log file when capture_log_copy is enabled."""
        log_file_path = tmp_path / "test.log"
        log_file_path.write_text("Test log content\n", encoding = "utf-8")
        test_bot.log_file_path = str(log_file_path)

        test_bot.config.diagnostics = DiagnosticsConfig.model_validate({"capture_on": {"publish": True}, "capture_log_copy": True, "output_dir": str(tmp_path)})

        page = MagicMock()
        page.save_screenshot = AsyncMock()
        page.get_content = AsyncMock(return_value = "<html></html>")
        page.sleep = AsyncMock()
        page.url = "https://example.com/fail"
        test_bot.page = page

        ad_cfg = Ad.model_validate(diagnostics_ad_config)
        ad_cfg_orig = copy.deepcopy(diagnostics_ad_config)
        ad_file = str(tmp_path / "ad_000001_Test.yml")
        ads_response = {"content": json.dumps({"ads": [], "paging": {"pageNum": 1, "last": 1}})}

        with (
            patch.object(test_bot, "web_request", new_callable = AsyncMock, return_value = ads_response),
            patch.object(test_bot, "publish_ad", new_callable = AsyncMock, side_effect = TimeoutError("boom")),
        ):
            await test_bot.publish_ads([(ad_file, ad_cfg, ad_cfg_orig)])

        entries = os.listdir(tmp_path)
        log_files = [name for name in entries if fnmatch.fnmatch(name, "publish_error_*_attempt*_ad_000001_Test.log")]
        assert len(log_files) == SUBMISSION_MAX_RETRIES

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_publish_ads_does_not_capture_diagnostics_when_disabled(
        self,
        test_bot:KleinanzeigenBot,
        tmp_path:Path,
        diagnostics_ad_config:dict[str, Any],
    ) -> None:
        """Ensure diagnostics are not captured when disabled."""
        test_bot.config.diagnostics = DiagnosticsConfig.model_validate({"capture_on": {"publish": False}, "output_dir": str(tmp_path)})

        page = MagicMock()
        page.save_screenshot = AsyncMock()
        page.get_content = AsyncMock(return_value = "<html></html>")
        page.sleep = AsyncMock()
        page.url = "https://example.com/fail"
        test_bot.page = page

        ad_cfg = Ad.model_validate(diagnostics_ad_config)
        ad_cfg_orig = copy.deepcopy(diagnostics_ad_config)
        ad_file = str(tmp_path / "ad_000001_Test.yml")

        with (
            patch.object(test_bot, "web_request", new_callable = AsyncMock, return_value = {"content": json.dumps({"ads": []})}),
            patch.object(test_bot, "publish_ad", new_callable = AsyncMock, side_effect = TimeoutError("boom")),
        ):
            await test_bot.publish_ads([(ad_file, ad_cfg, ad_cfg_orig)])

        page.save_screenshot.assert_not_called()
        page.get_content.assert_not_called()
        entries = os.listdir(tmp_path)
        html_files = [name for name in entries if fnmatch.fnmatch(name, "publish_error_*_attempt*_ad_000001_Test.html")]
        json_files = [name for name in entries if fnmatch.fnmatch(name, "publish_error_*_attempt*_ad_000001_Test.json")]
        assert not html_files
        assert not json_files


class TestKleinanzeigenBotLocalization:
    """Tests for localization and help text."""

    def test_show_help_displays_german_text(self, test_bot:KleinanzeigenBot) -> None:
        """Verify that help text is displayed in German when language is German."""
        with patch("kleinanzeigen_bot.get_current_locale") as mock_locale, patch("builtins.print") as mock_print:
            mock_locale.return_value.language = "de"
            test_bot.show_help()
            printed_text = "".join(str(call.args[0]) for call in mock_print.call_args_list)
            assert "Verwendung:" in printed_text
            assert "Befehle:" in printed_text

    def test_show_help_displays_english_text(self, test_bot:KleinanzeigenBot) -> None:
        """Verify that help text is displayed in English when language is English."""
        with patch("kleinanzeigen_bot.get_current_locale") as mock_locale, patch("builtins.print") as mock_print:
            mock_locale.return_value.language = "en"
            test_bot.show_help()
            printed_text = "".join(str(call.args[0]) for call in mock_print.call_args_list)
            assert "Usage:" in printed_text
            assert "Commands:" in printed_text


class TestKleinanzeigenBotBasics:
    """Basic tests for KleinanzeigenBot."""

    def test_get_version(self, test_bot:KleinanzeigenBot) -> None:
        """Test version retrieval."""
        assert test_bot.get_version() == __version__

    @pytest.mark.asyncio
    async def test_publish_ads_triggers_publish_and_cleanup(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        mock_page:MagicMock,
    ) -> None:
        """Simulate publish job wiring without hitting the live site."""
        test_bot.page = mock_page
        test_bot.config.publishing.delete_old_ads = "AFTER_PUBLISH"
        test_bot.keep_old_ads = False

        payload:dict[str, list[Any]] = {"ads": []}
        ad_cfgs:list[tuple[str, Ad, dict[str, Any]]] = [("ad.yaml", Ad.model_validate(base_ad_config), {})]

        with (
            patch.object(test_bot, "web_request", new_callable = AsyncMock, return_value = {"content": json.dumps(payload)}) as web_request_mock,
            patch.object(test_bot, "publish_ad", new_callable = AsyncMock) as publish_ad_mock,
            patch.object(test_bot, "web_await", new_callable = AsyncMock, return_value = True) as web_await_mock,
            patch.object(test_bot, "delete_ad", new_callable = AsyncMock) as delete_ad_mock,
        ):
            await test_bot.publish_ads(ad_cfgs)

            # web_request is called once for initial published-ads snapshot
            expected_url = f"{test_bot.root_url}/m-meine-anzeigen-verwalten.json?sort=DEFAULT&pageNum=1"
            web_request_mock.assert_awaited_once_with(expected_url)
            publish_ad_mock.assert_awaited_once_with("ad.yaml", ad_cfgs[0][1], {}, [], AdUpdateStrategy.REPLACE)
            web_await_mock.assert_awaited_once()
            delete_ad_mock.assert_awaited_once_with(ad_cfgs[0][1], [], delete_old_ads_by_title = False)

    @pytest.mark.asyncio
    async def test_publish_ads_uses_millisecond_retry_delay_on_retryable_failure(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        mock_page:MagicMock,
    ) -> None:
        """Retry branch should sleep with explicit millisecond delay and reset price-reduction mutations."""
        test_bot.page = mock_page
        test_bot.keep_old_ads = True

        ad_cfg = Ad.model_validate(base_ad_config | {"price": 100, "price_reduction_count": 0, "repost_count": 1})
        ad_cfg_orig = copy.deepcopy(base_ad_config)
        ad_file = "ad.yaml"
        ads_response = {"content": json.dumps({"ads": [], "paging": {"pageNum": 1, "last": 1}})}
        seen_prices:list[tuple[int | None, int | None]] = []

        async def publish_side_effect(
            _ad_file:str,
            candidate_cfg:Ad,
            _candidate_orig:dict[str, Any],
            _published_ads:list[dict[str, Any]],
            _mode:AdUpdateStrategy,
        ) -> None:
            seen_prices.append((candidate_cfg.price, candidate_cfg.price_reduction_count))
            if len(seen_prices) == 1:
                # Simulate in-memory mutation done by apply_auto_price_reduction before a failed attempt.
                candidate_cfg.price = 90
                candidate_cfg.price_reduction_count = 1
                raise TimeoutError("transient")

        with (
            patch.object(test_bot, "web_request", new_callable = AsyncMock, return_value = ads_response),
            patch.object(test_bot, "publish_ad", new_callable = AsyncMock, side_effect = publish_side_effect) as publish_mock,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as sleep_mock,
            patch.object(test_bot, "web_await", new_callable = AsyncMock, return_value = True),
        ):
            await test_bot.publish_ads([(ad_file, ad_cfg, ad_cfg_orig)])

            assert publish_mock.await_count == 2
            assert seen_prices == [(100, 0), (100, 0)]
            sleep_mock.assert_awaited_once_with(2_000)

    @pytest.mark.asyncio
    async def test_publish_ads_does_not_retry_when_submission_state_is_uncertain(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        mock_page:MagicMock,
    ) -> None:
        """Post-submit uncertainty must fail closed and skip retries."""
        test_bot.page = mock_page
        test_bot.keep_old_ads = True

        ad_cfg = Ad.model_validate(base_ad_config)
        ad_cfg_orig = copy.deepcopy(base_ad_config)
        ad_file = "ad.yaml"

        with (
            patch.object(
                test_bot,
                "web_request",
                new_callable = AsyncMock,
                return_value = {"content": json.dumps({"ads": [], "paging": {"pageNum": 1, "last": 1}})},
            ),
            patch.object(
                test_bot,
                "publish_ad",
                new_callable = AsyncMock,
                side_effect = PublishSubmissionUncertainError("submission may have succeeded before failure"),
            ) as publish_mock,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as sleep_mock,
        ):
            await test_bot.publish_ads([(ad_file, ad_cfg, ad_cfg_orig)])

            assert publish_mock.await_count == 1
            sleep_mock.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("mode", "expected_path"),
        [
            (AdUpdateStrategy.REPLACE, "/p-anzeige-aufgeben-schritt2.html"),
            (AdUpdateStrategy.MODIFY, "/p-anzeige-bearbeiten.html?adId=12345"),
        ],
        ids = ["replace", "modify"],
    )
    async def test_publish_ad_keeps_pre_submit_timeouts_retryable(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        mode:AdUpdateStrategy,
        expected_path:str,
    ) -> None:
        """Timeouts before submit boundary should remain plain retryable failures and force reload."""
        ad_cfg = Ad.model_validate(base_ad_config | {"id": 12345, "shipping_type": "NOT_APPLICABLE", "price_type": "NOT_APPLICABLE"})
        ad_cfg_orig = copy.deepcopy(base_ad_config)
        expected_url = f"{test_bot.root_url}{expected_path}"
        test_bot.keep_old_ads = True

        with (
            patch.object(test_bot, "web_open", new_callable = AsyncMock) as web_open_mock,
            patch.object(test_bot, "_dismiss_consent_banner", new_callable = AsyncMock),
            patch.object(test_bot, "_KleinanzeigenBot__set_category", new_callable = AsyncMock, side_effect = TimeoutError("image upload timeout")),
            pytest.raises(TimeoutError, match = "image upload timeout"),
        ):
            await test_bot.publish_ad("ad.yaml", ad_cfg, ad_cfg_orig, [], mode)

        web_open_mock.assert_awaited_once_with(expected_url, reload_if_already_open = True)

    @staticmethod
    def _build_publish_ad_cfg(base_ad_config:dict[str, Any]) -> tuple[Ad, dict[str, Any]]:
        """Build ad config and original dict for publish_ad tests."""
        ad_cfg = Ad.model_validate(base_ad_config | {"id": 12345, "shipping_type": "NOT_APPLICABLE", "price_type": "NOT_APPLICABLE"})
        ad_cfg_orig = copy.deepcopy(base_ad_config)
        return ad_cfg, ad_cfg_orig

    @contextmanager
    def _mock_post_submit_dependencies(
        self,
        test_bot:KleinanzeigenBot,
        mock_page:MagicMock,
        *,
        web_await_side_effect:BaseException | None = None,
        redirect_recovery_return:int | None = None,
        redirect_recovery_side_effect:BaseException | None = None,
        include_success_mocks:bool = False,
    ) -> Iterator[None]:
        """Mock all post-submit publish_ad dependencies for confirmation fallback tests.

        Parameters
        ----------
            web_await_side_effect: Exception to raise from web_await (simulates confirmation timeout).
            redirect_recovery_return: Return value for _try_recover_ad_id_from_redirect.
            redirect_recovery_side_effect: Exception to raise from _try_recover_ad_id_from_redirect.
            include_success_mocks: If True, also mock dicts.save_dict (for success-path tests).
        """
        test_bot.page = mock_page

        common_patches:list[Any] = [
            patch.object(test_bot, "web_open", new_callable = AsyncMock),
            patch.object(test_bot, "_dismiss_consent_banner", new_callable = AsyncMock),
            patch.object(test_bot, "_KleinanzeigenBot__set_category", new_callable = AsyncMock),
            patch.object(test_bot, "_KleinanzeigenBot__set_special_attributes", new_callable = AsyncMock),
            patch.object(test_bot, "_KleinanzeigenBot__set_contact_fields", new_callable = AsyncMock),
            patch.object(test_bot, "check_and_wait_for_captcha", new_callable = AsyncMock),
            patch.object(test_bot, "web_input", new_callable = AsyncMock),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = None),
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_check", new_callable = AsyncMock, return_value = False),
            patch.object(test_bot, "web_execute", new_callable = AsyncMock),
            patch.object(test_bot, "web_find", new_callable = AsyncMock),
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, return_value = []),
            patch.object(test_bot, "_web_find_all_once", new_callable = AsyncMock, return_value = []),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = web_await_side_effect),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch.object(test_bot, "_try_recover_ad_id_from_redirect", new_callable = AsyncMock,
                         return_value = redirect_recovery_return, side_effect = redirect_recovery_side_effect),
        ]

        if include_success_mocks:
            common_patches.append(patch("kleinanzeigen_bot.dicts.save_dict"))

        with ExitStack() as stack:
            for p in common_patches:
                stack.enter_context(p)
            yield

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "web_await_error",
        [TimeoutError("confirmation timeout"), ProtocolException(MagicMock(), "connection lost", 0)],
        ids = ["timeout", "protocol-exception"],
    )
    async def test_publish_ad_marks_post_submit_errors_as_uncertain(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        mock_page:MagicMock,
        web_await_error:Exception,
    ) -> None:
        """Post-submit exceptions (TimeoutError, ProtocolException) should be converted to non-retryable uncertainty."""
        ad_cfg, ad_cfg_orig = self._build_publish_ad_cfg(base_ad_config)

        with (
            self._mock_post_submit_dependencies(test_bot, mock_page,
                                                web_await_side_effect = web_await_error,
                                                redirect_recovery_return = None),
            pytest.raises(PublishSubmissionUncertainError, match = "submission may have succeeded before failure"),
        ):
            await test_bot.publish_ad("ad.yaml", ad_cfg, ad_cfg_orig, [], AdUpdateStrategy.MODIFY)

    @pytest.mark.asyncio
    async def test_publish_ad_confirmation_fallback_from_referrer(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        mock_page:MagicMock,
    ) -> None:
        """When confirmation URL polling times out, ad ID should be recovered from document.referrer."""
        ad_cfg, ad_cfg_orig = self._build_publish_ad_cfg(base_ad_config)

        with self._mock_post_submit_dependencies(
            test_bot, mock_page,
            web_await_side_effect = TimeoutError("confirmation timeout"),
            redirect_recovery_return = 99887766,
            include_success_mocks = True,
        ):
            await test_bot.publish_ad("ad.yaml", ad_cfg, ad_cfg_orig, [], AdUpdateStrategy.MODIFY)

        assert ad_cfg_orig["id"] == 99887766

    @pytest.mark.asyncio
    async def test_publish_ad_confirmation_fallback_when_redirect_happens_after_url_poll(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        mock_page:MagicMock,
    ) -> None:
        """When the confirmation URL was observed by polling but the page redirected before extraction, the fallback should recover the ad ID."""
        ad_cfg, ad_cfg_orig = self._build_publish_ad_cfg(base_ad_config)

        # web_await succeeds (confirmation URL was seen during polling), but the page
        # redirects before line 2479 can extract the URL, causing IndexError in the
        # extraction which falls into the except block and triggers the fallback.
        with self._mock_post_submit_dependencies(
            test_bot, mock_page,
            redirect_recovery_return = 55667788,
            include_success_mocks = True,
        ):
            await test_bot.publish_ad("ad.yaml", ad_cfg, ad_cfg_orig, [], AdUpdateStrategy.MODIFY)

        assert ad_cfg_orig["id"] == 55667788

    @pytest.mark.asyncio
    async def test_publish_ad_confirmation_fallback_from_tracking_raises_uncertain_when_not_found(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        mock_page:MagicMock,
    ) -> None:
        """When both confirmation URL polling and tracking fallback fail, PublishSubmissionUncertainError should be raised."""
        ad_cfg, ad_cfg_orig = self._build_publish_ad_cfg(base_ad_config)

        with (
            self._mock_post_submit_dependencies(test_bot, mock_page,
                                                web_await_side_effect = TimeoutError("confirmation timeout"),
                                                redirect_recovery_return = None),
            pytest.raises(PublishSubmissionUncertainError, match = "submission may have succeeded before failure"),
        ):
            await test_bot.publish_ad("ad.yaml", ad_cfg, ad_cfg_orig, [], AdUpdateStrategy.MODIFY)

    @pytest.mark.asyncio
    async def test_publish_ad_confirmation_fallback_helper_failure_still_raises_uncertain(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        mock_page:MagicMock,
    ) -> None:
        """When the tracking fallback helper itself raises, it should not change retry behavior — PublishSubmissionUncertainError must still be raised."""
        ad_cfg, ad_cfg_orig = self._build_publish_ad_cfg(base_ad_config)

        with (
            self._mock_post_submit_dependencies(test_bot, mock_page,
                                                web_await_side_effect = TimeoutError("confirmation timeout"),
                                                redirect_recovery_side_effect = RuntimeError("browser disconnected")),
            pytest.raises(PublishSubmissionUncertainError, match = "submission may have succeeded before failure"),
        ):
            await test_bot.publish_ad("ad.yaml", ad_cfg, ad_cfg_orig, [], AdUpdateStrategy.MODIFY)

    def test_get_root_url(self, test_bot:KleinanzeigenBot) -> None:
        """Test root URL retrieval."""
        assert test_bot.root_url == "https://www.kleinanzeigen.de"

    def test_get_config_defaults(self, test_bot:KleinanzeigenBot) -> None:
        """Test default configuration values."""
        assert isinstance(test_bot.config, Config)
        assert test_bot.command == "help"
        assert test_bot.ads_selector == "due"
        assert test_bot.keep_old_ads is False

    def test_get_log_level(self, test_bot:KleinanzeigenBot) -> None:
        """Test log level configuration."""
        # Reset log level to default
        LOG.setLevel(loggers.INFO)
        assert not loggers.is_debug(LOG)
        test_bot.parse_args(["script.py", "-v"])
        assert loggers.is_debug(LOG)

    def test_get_config_file_path(self, test_bot:KleinanzeigenBot) -> None:
        """Test config file path handling."""
        default_path = os.path.abspath("config.yaml")
        assert test_bot.config_file_path == default_path
        test_path = os.path.abspath("custom_config.yaml")
        test_bot.config_file_path = test_path
        assert test_bot.config_file_path == test_path

    def test_get_log_file_path(self, test_bot:KleinanzeigenBot) -> None:
        """Test log file path handling."""
        default_path = os.path.abspath("kleinanzeigen_bot.log")
        assert test_bot.log_file_path == default_path
        test_path = os.path.abspath("custom.log")
        test_bot.log_file_path = test_path
        assert test_bot.log_file_path == test_path

    def test_get_categories(self, test_bot:KleinanzeigenBot) -> None:
        """Test categories handling."""
        test_categories = {"test_cat": "test_id"}
        test_bot.categories = test_categories
        assert test_bot.categories == test_categories


class TestKleinanzeigenBotUpdateAdsResilience:
    @staticmethod
    def _build_update_ad(base_ad_config:dict[str, Any], ad_id:int, title:str) -> tuple[str, Ad, dict[str, Any]]:
        ad_payload = copy.deepcopy(base_ad_config) | {"id": ad_id, "title": title}
        return (f"{ad_id}.yaml", Ad.model_validate(ad_payload), ad_payload)

    @staticmethod
    def _build_published_ads(*ad_ids:int) -> list[dict[str, Any]]:
        return [{"id": ad_id, "state": "active"} for ad_id in ad_ids]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("first_failure", "first_title"),
        [
            (TimeoutError("transient timeout"), "Timeout Ad"),
            (ProtocolException(MagicMock(), "connection lost", 0), "Protocol Failing"),
        ],
        ids = ["timeout_error", "protocol_exception"],
    )
    async def test_update_ads_continues_after_retryable_first_ad_failure(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        first_failure:Exception,
        first_title:str,
    ) -> None:
        ad_one = self._build_update_ad(base_ad_config, 101, first_title)
        ad_two = self._build_update_ad(base_ad_config, 102, "Success Ad")

        async def publish_side_effect(
            _ad_file:str,
            ad_cfg:Ad,
            _ad_cfg_orig:dict[str, Any],
            _published_ads:list[dict[str, Any]],
            _mode:AdUpdateStrategy,
        ) -> None:
            if ad_cfg.id == 101:
                raise first_failure

        with (
            patch.object(test_bot, "_fetch_published_ads", new_callable = AsyncMock, return_value = self._build_published_ads(101, 102)),
            patch.object(test_bot, "publish_ad", new_callable = AsyncMock, side_effect = publish_side_effect) as publish_mock,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as sleep_mock,
            patch.object(test_bot, "web_await", new_callable = AsyncMock, return_value = True),
        ):
            await test_bot.update_ads([ad_one, ad_two])

        assert publish_mock.await_count == SUBMISSION_MAX_RETRIES + 1
        assert any(call.args[1].id == 102 for call in publish_mock.await_args_list)
        assert all(call.args[4] == AdUpdateStrategy.MODIFY for call in publish_mock.await_args_list)
        assert sleep_mock.await_count == SUBMISSION_MAX_RETRIES - 1

    @pytest.mark.asyncio
    async def test_update_ads_publish_submission_uncertain_is_not_retried(self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any]) -> None:
        ad_one = self._build_update_ad(base_ad_config, 301, "Uncertain Update")
        ad_two = self._build_update_ad(base_ad_config, 302, "Second Update")

        async def publish_side_effect(
            _ad_file:str,
            ad_cfg:Ad,
            _ad_cfg_orig:dict[str, Any],
            _published_ads:list[dict[str, Any]],
            _mode:AdUpdateStrategy,
        ) -> None:
            if ad_cfg.id == 301:
                raise PublishSubmissionUncertainError("submission may have succeeded before failure")

        with (
            patch.object(test_bot, "_fetch_published_ads", new_callable = AsyncMock, return_value = self._build_published_ads(301, 302)),
            patch.object(test_bot, "publish_ad", new_callable = AsyncMock, side_effect = publish_side_effect) as publish_mock,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as sleep_mock,
            patch.object(test_bot, "web_await", new_callable = AsyncMock, return_value = True),
        ):
            await test_bot.update_ads([ad_one, ad_two])

        assert publish_mock.await_count == 2
        sleep_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_update_ads_cancelled_error_propagates_immediately(self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any]) -> None:
        ad_one = self._build_update_ad(base_ad_config, 401, "Cancelled Ad")
        ad_two = self._build_update_ad(base_ad_config, 402, "Should Not Run")

        with (
            patch.object(test_bot, "_fetch_published_ads", new_callable = AsyncMock, return_value = self._build_published_ads(401, 402)),
            patch.object(test_bot, "publish_ad", new_callable = AsyncMock, side_effect = asyncio.CancelledError()) as publish_mock,
            patch.object(test_bot, "web_await", new_callable = AsyncMock, return_value = True),
            pytest.raises(asyncio.CancelledError),
        ):
            await test_bot.update_ads([ad_one, ad_two])

        assert publish_mock.await_count == 1

    @pytest.mark.asyncio
    async def test_update_ads_publishing_result_timeout_is_non_fatal(self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any]) -> None:
        ad_one = self._build_update_ad(base_ad_config, 501, "Result Timeout")

        with (
            patch.object(test_bot, "_fetch_published_ads", new_callable = AsyncMock, return_value = self._build_published_ads(501)),
            patch.object(test_bot, "publish_ad", new_callable = AsyncMock) as publish_mock,
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = TimeoutError("result timeout")),
        ):
            await test_bot.update_ads([ad_one])

        publish_mock.assert_awaited_once()


class TestDisplayCounterProgression:
    """Regression tests for issue #977: progress counter must increment for every ad, including skipped ones."""

    @staticmethod
    def _build_ad(base_ad_config:dict[str, Any], ad_id:int | None, title:str) -> tuple[str, Ad, dict[str, Any]]:
        ad_payload = copy.deepcopy(base_ad_config) | {"id": ad_id, "title": title}
        return (f"{ad_id}.yaml", Ad.model_validate(ad_payload), ad_payload)

    @staticmethod
    def _build_published_ads(*ad_specs:tuple[int, str]) -> list[dict[str, Any]]:
        return [{"id": ad_id, "state": state} for ad_id, state in ad_specs]

    def test_update_content_hashes_counter_progression(
        self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any], caplog:pytest.LogCaptureFixture
    ) -> None:
        """Display counter must advance for every ad, even when hash is unchanged."""
        ads = [
            self._build_ad(base_ad_config, None, "Unchanged Ad 1"),
            self._build_ad(base_ad_config, None, "Changed Ad"),
            self._build_ad(base_ad_config, None, "Unchanged Ad 2"),
        ]

        # Pre-compute hashes so two match and one differs
        for _ad_file, ad_cfg, ad_cfg_orig in ads:
            ad_cfg.update_content_hash()
            ad_cfg_orig["content_hash"] = ad_cfg.content_hash

        # Make the middle ad's original hash differ
        ads[1][2]["content_hash"] = "deliberately_wrong_hash"

        with (
            caplog.at_level(logging.INFO),
            patch.object(dicts, "save_dict"),
        ):
            test_bot.update_content_hashes(ads)

        processing = [r for r in caplog.records if r.message.startswith("Processing")]
        assert len(processing) == 3
        assert "1/3" in processing[0].message
        assert "2/3" in processing[1].message
        assert "3/3" in processing[2].message

        summary = [r for r in caplog.records if "DONE:" in r.message and "content_hash" in r.message]
        assert any("1 ad" in r.message for r in summary)

    @pytest.mark.asyncio
    async def test_publish_ads_counter_progression_with_paused_ads(
        self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any], caplog:pytest.LogCaptureFixture
    ) -> None:
        """Display counter must advance for paused ads, and only non-paused ads are published."""
        ad_cfgs = [
            self._build_ad(base_ad_config, 101, "Paused Ad 1"),
            self._build_ad(base_ad_config, 102, "Active Ad 102"),
            self._build_ad(base_ad_config, 103, "Paused Ad 2"),
        ]
        published_ads = self._build_published_ads((101, "paused"), (102, "active"), (103, "paused"))

        with (
            caplog.at_level(logging.INFO),
            patch.object(test_bot, "_fetch_published_ads", new_callable = AsyncMock, return_value = published_ads),
            patch.object(test_bot, "publish_ad", new_callable = AsyncMock) as publish_mock,
            patch.object(test_bot, "web_await", new_callable = AsyncMock, return_value = True),
        ):
            await test_bot.publish_ads(ad_cfgs)

        processing = [r for r in caplog.records if r.message.startswith("Processing")]
        assert len(processing) == 3
        assert "1/3" in processing[0].message
        assert "2/3" in processing[1].message
        assert "3/3" in processing[2].message

        skip_msgs = [r for r in caplog.records if "Skipping because ad is reserved" in r.message]
        assert len(skip_msgs) == 2

        publish_mock.assert_awaited_once()
        assert publish_mock.call_args.args[1].id == 102

        summary = [r for r in caplog.records if "DONE:" in r.message]
        assert any("1 ad" in r.message for r in summary)

    @pytest.mark.asyncio
    async def test_update_ads_counter_progression_with_paused_ads(
        self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any], caplog:pytest.LogCaptureFixture
    ) -> None:
        """Display counter must advance for paused ads, and only non-paused ads are updated."""
        ad_cfgs = [
            self._build_ad(base_ad_config, 201, "Paused Ad 1"),
            self._build_ad(base_ad_config, 202, "Active Ad 202"),
            self._build_ad(base_ad_config, 203, "Paused Ad 2"),
        ]
        published_ads = self._build_published_ads((201, "paused"), (202, "active"), (203, "paused"))

        with (
            caplog.at_level(logging.INFO),
            patch.object(test_bot, "_fetch_published_ads", new_callable = AsyncMock, return_value = published_ads),
            patch.object(test_bot, "publish_ad", new_callable = AsyncMock) as publish_mock,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, return_value = True),
        ):
            await test_bot.update_ads(ad_cfgs)

        processing = [r for r in caplog.records if r.message.startswith("Processing")]
        assert len(processing) == 3
        assert "1/3" in processing[0].message
        assert "2/3" in processing[1].message
        assert "3/3" in processing[2].message

        skip_msgs = [r for r in caplog.records if "Skipping because ad is reserved" in r.message]
        assert len(skip_msgs) == 2

        publish_mock.assert_awaited_once()
        assert publish_mock.call_args.args[1].id == 202

        summary = [r for r in caplog.records if "DONE:" in r.message]
        assert any("1 ad" in r.message for r in summary)

    @pytest.mark.asyncio
    async def test_update_ads_counter_includes_not_found_ads(
        self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any], caplog:pytest.LogCaptureFixture
    ) -> None:
        """Display counter must advance even for ads not found in published ads."""
        ad_cfgs = [
            self._build_ad(base_ad_config, 301, "Not Found Ad"),
            self._build_ad(base_ad_config, 302, "Active Ad 302"),
        ]
        published_ads = self._build_published_ads((302, "active"))

        with (
            caplog.at_level(logging.INFO),
            patch.object(test_bot, "_fetch_published_ads", new_callable = AsyncMock, return_value = published_ads),
            patch.object(test_bot, "publish_ad", new_callable = AsyncMock) as publish_mock,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, return_value = True),
        ):
            await test_bot.update_ads(ad_cfgs)

        processing = [r for r in caplog.records if r.message.startswith("Processing")]
        assert len(processing) == 2
        assert "1/2" in processing[0].message
        assert "2/2" in processing[1].message
        assert "Not Found Ad" in processing[0].message

        skip_msgs = [r for r in caplog.records if "SKIPPED" in r.message and "not found" in r.message]
        assert len(skip_msgs) == 1

        publish_mock.assert_awaited_once()
        assert publish_mock.call_args.args[1].id == 302


class TestKleinanzeigenBotContactLocationHardening:
    @pytest.mark.parametrize(
        ("target", "candidate", "expected"),
        [
            ("10115 - Metroville", "10115 - Metroville", True),
            ("10115 - Metroville", "12623 - Metroville", False),
            ("Metroville", "12623 - Metroville", True),
            ("Berlin", "Berlin - Mitte", True),
            ("Metroville", None, False),
            ("Berlin", "Hamburg", False),
            ("Berlin", "berlin", True),
            ("Berlin", "  Berlin  ", True),
        ],
    )
    def test_location_matches_target(self, test_bot:KleinanzeigenBot, target:str, candidate:str | None, expected:bool) -> None:
        matcher = getattr(test_bot, "_KleinanzeigenBot__location_matches_target")
        assert matcher(target, candidate) is expected

    @pytest.mark.asyncio
    async def test_read_city_selection_text_prefers_live_input_value(self, test_bot:KleinanzeigenBot) -> None:
        city_input = MagicMock(spec = Element)
        city_input.local_name = "input"
        city_input.apply = AsyncMock(return_value = "Live City")

        with (
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = city_input),
            patch.object(test_bot, "web_text", new_callable = AsyncMock) as web_text_mock,
        ):
            selected = await getattr(test_bot, "_KleinanzeigenBot__read_city_selection_text")()

        assert selected == "Live City"
        web_text_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_set_contact_fields_fails_closed_when_zipcode_cannot_be_set(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        ad_cfg = Ad.model_validate(base_ad_config)

        with (
            patch.object(test_bot, "web_input", new_callable = AsyncMock, side_effect = TimeoutError("zip timeout")),
            patch.object(test_bot, "_KleinanzeigenBot__set_contact_location", new_callable = AsyncMock) as set_location_mock,
            pytest.raises(TimeoutError, match = "Failed to set contact zipcode"),
        ):
            await getattr(test_bot, "_KleinanzeigenBot__set_contact_fields")(ad_cfg.contact)

        set_location_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_set_contact_fields_skips_zipcode_and_location_when_empty(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """When no zipcode is configured, both ZIP entry and location setting are skipped without error."""
        config = base_ad_config | {"contact": base_ad_config["contact"] | {"zipcode": ""}}
        ad_cfg = Ad.model_validate(config)

        with (
            patch.object(test_bot, "web_input", new_callable = AsyncMock) as web_input_mock,
            patch.object(test_bot, "_KleinanzeigenBot__set_contact_location", new_callable = AsyncMock) as set_location_mock,
            patch.object(test_bot, "web_check", new_callable = AsyncMock, return_value = True),
        ):
            await getattr(test_bot, "_KleinanzeigenBot__set_contact_fields")(ad_cfg.contact)

        web_input_mock.assert_not_awaited()
        set_location_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_set_contact_location_fails_when_city_suffix_matches_multiple_zip_codes(self, test_bot:KleinanzeigenBot) -> None:
        """When multiple ZIP codes share the same city name and no exact match, selection must fail closed."""
        city_button = MagicMock(spec = Element)
        city_button.local_name = "button"
        city_button.attrs = {"role": "combobox", "aria-controls": "ad-city-menu"}

        option_a = MagicMock(spec = Element)
        option_a.text = "10115 - Metroville"
        option_b = MagicMock(spec = Element)
        option_b.text = "12623 - Metroville"

        def _mock_city_option_text(elem:Element) -> str:
            return str(getattr(elem, "text", "") or "")

        async def _web_await_side_effect(condition:Callable[..., Awaitable[bool] | bool], **_:Any) -> Any:
            result = condition()
            return await result if asyncio.iscoroutine(result) else result

        with (
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = city_button),
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, return_value = [option_a, option_b]),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = _web_await_side_effect),
            patch.object(test_bot, "_KleinanzeigenBot__read_city_selection_text", new_callable = AsyncMock, return_value = None),
            patch.object(test_bot, "_KleinanzeigenBot__city_option_text", new_callable = AsyncMock, side_effect = _mock_city_option_text),
            pytest.raises(TimeoutError, match = "City combobox options are ambiguous for location: Metroville"),
        ):
            await getattr(test_bot, "_KleinanzeigenBot__set_contact_location")("Metroville")

    @pytest.mark.asyncio
    async def test_set_contact_location_raises_when_selection_does_not_converge(self, test_bot:KleinanzeigenBot) -> None:
        city_button = MagicMock(spec = Element)
        city_button.local_name = "button"
        city_button.attrs = {"role": "combobox", "aria-controls": "ad-city-menu"}

        target_option = MagicMock(spec = Element)
        target_option.text = "10115 - Metroville"
        target_option.click = AsyncMock()

        wait_calls = 0

        async def web_await_side_effect(condition:Callable[..., Awaitable[bool] | bool], **_:Any) -> Any:
            nonlocal wait_calls
            wait_calls += 1

            result = condition()
            condition_value = await result if asyncio.iscoroutine(result) else result
            if wait_calls == 1:
                return condition_value
            raise TimeoutError("Condition not met")

        with (
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = city_button),
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, return_value = [target_option]),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = web_await_side_effect),
            patch.object(test_bot, "_KleinanzeigenBot__read_city_selection_text", new_callable = AsyncMock, return_value = "20095 - Rivertown"),
            pytest.raises(TimeoutError, match = "City selection did not converge"),
        ):
            await getattr(test_bot, "_KleinanzeigenBot__set_contact_location")("10115 - Metroville")


class TestKleinanzeigenBotArgParsing:
    """Tests for command line argument parsing."""

    def test_parse_args_help(self, test_bot:KleinanzeigenBot) -> None:
        """Test parsing help command."""
        test_bot.parse_args(["script.py", "help"])
        assert test_bot.command == "help"

    def test_parse_args_version(self, test_bot:KleinanzeigenBot) -> None:
        """Test parsing version command."""
        test_bot.parse_args(["script.py", "version"])
        assert test_bot.command == "version"

    def test_parse_args_verbose(self, test_bot:KleinanzeigenBot) -> None:
        """Test parsing verbose flag."""
        test_bot.parse_args(["script.py", "-v", "help"])
        assert loggers.is_debug(loggers.get_logger("kleinanzeigen_bot"))

    def test_parse_args_config_path(self, test_bot:KleinanzeigenBot) -> None:
        """Test parsing config path."""
        test_bot.parse_args(["script.py", "--config=test.yaml", "help"])
        assert test_bot.config_file_path.endswith("test.yaml")

    def test_parse_args_logfile(self, test_bot:KleinanzeigenBot) -> None:
        """Test parsing log file path."""
        test_bot.parse_args(["script.py", "--logfile=test.log", "help"])
        assert test_bot.log_file_path is not None
        assert "test.log" in test_bot.log_file_path

    def test_parse_args_workspace_mode(self, test_bot:KleinanzeigenBot) -> None:
        """Test parsing workspace mode option."""
        test_bot.parse_args(["script.py", "--workspace-mode=xdg", "help"])
        assert test_bot._workspace_mode_arg == "xdg"

    def test_parse_args_workspace_mode_invalid(self, test_bot:KleinanzeigenBot) -> None:
        """Test invalid workspace mode exits with error."""
        with pytest.raises(SystemExit) as exc_info:
            test_bot.parse_args(["script.py", "--workspace-mode=invalid", "help"])
        assert exc_info.value.code == 2

    def test_parse_args_ads_selector(self, test_bot:KleinanzeigenBot) -> None:
        """Test parsing ads selector."""
        test_bot.parse_args(["script.py", "--ads=all", "publish"])
        assert test_bot.ads_selector == "all"

    def test_parse_args_force(self, test_bot:KleinanzeigenBot) -> None:
        """Test parsing force flag."""
        test_bot.parse_args(["script.py", "--force", "publish"])
        assert test_bot.ads_selector == "all"

    def test_parse_args_keep_old(self, test_bot:KleinanzeigenBot) -> None:
        """Test parsing keep-old flag."""
        test_bot.parse_args(["script.py", "--keep-old", "publish"])
        assert test_bot.keep_old_ads is True

    def test_parse_args_logfile_empty(self, test_bot:KleinanzeigenBot) -> None:
        """Test parsing empty log file path."""
        test_bot.parse_args(["script.py", "--logfile=", "help"])
        assert test_bot.log_file_path is None

    def test_parse_args_lang_option(self, test_bot:KleinanzeigenBot) -> None:
        """Test parsing language option."""
        test_bot.parse_args(["script.py", "--lang=en", "help"])
        assert test_bot.command == "help"

    def test_parse_args_no_arguments(self, test_bot:KleinanzeigenBot) -> None:
        """Test parsing no arguments defaults to help."""
        test_bot.parse_args(["script.py"])
        assert test_bot.command == "help"

    def test_parse_args_multiple_commands(self, test_bot:KleinanzeigenBot) -> None:
        """Test parsing multiple commands raises error."""
        with pytest.raises(SystemExit) as exc_info:
            test_bot.parse_args(["script.py", "help", "version"])
        assert exc_info.value.code == 2

    def test_parse_args_explicit_flags(self, test_bot:KleinanzeigenBot, tmp_path:Path) -> None:
        """Test that explicit flags are set when config/logfile/workspace options are provided."""
        config_path = tmp_path / "custom_config.yaml"
        log_path = tmp_path / "custom.log"

        # Test --config flag stores raw config arg
        test_bot.parse_args(["script.py", "--config", str(config_path), "help"])
        assert test_bot._config_arg == str(config_path)
        assert str(config_path.absolute()) == test_bot.config_file_path

        # Test --logfile flag sets explicit logfile values
        test_bot.parse_args(["script.py", "--logfile", str(log_path), "help"])
        assert test_bot._logfile_explicitly_provided is True
        assert test_bot._logfile_arg == str(log_path)
        assert str(log_path.absolute()) == test_bot.log_file_path

        # Test both flags together
        test_bot._config_arg = None
        test_bot._logfile_explicitly_provided = False
        test_bot._workspace_mode_arg = None
        test_bot.parse_args(["script.py", "--config", str(config_path), "--logfile", str(log_path), "--workspace-mode", "portable", "help"])
        assert test_bot._config_arg == str(config_path)
        assert test_bot._logfile_explicitly_provided is True
        assert test_bot._workspace_mode_arg == "portable"


class TestKleinanzeigenBotCommands:
    """Tests for command execution."""

    @pytest.mark.asyncio
    async def test_run_version_command(self, test_bot:KleinanzeigenBot, capsys:Any) -> None:
        """Test running version command."""
        await test_bot.run(["script.py", "version"])
        captured = capsys.readouterr()
        assert __version__ in captured.out

    @pytest.mark.asyncio
    async def test_run_help_command(self, test_bot:KleinanzeigenBot, capsys:Any) -> None:
        """Test running help command."""
        await test_bot.run(["script.py", "help"])
        captured = capsys.readouterr()
        assert "Usage:" in captured.out

    @pytest.mark.asyncio
    async def test_run_unknown_command(self, test_bot:KleinanzeigenBot) -> None:
        """Test running unknown command."""
        with pytest.raises(SystemExit) as exc_info:
            await test_bot.run(["script.py", "unknown"])
        assert exc_info.value.code == 2

    @pytest.mark.asyncio
    async def test_verify_command(self, test_bot:KleinanzeigenBot, tmp_path:Any) -> None:
        """Test verify command with minimal config."""
        config_path = Path(tmp_path) / "config.yaml"
        config_path.write_text(
            """
login:
    username: test
    password: test
""",
            encoding = "utf-8",
        )
        test_bot.config_file_path = str(config_path)
        await test_bot.run(["script.py", "verify"])
        assert test_bot.config.login.username == "test"


class TestKleinanzeigenBotAdOperations:
    """Tests for ad-related operations."""

    @pytest.mark.asyncio
    async def test_run_delete_command_no_ads(self, test_bot:KleinanzeigenBot, mock_config_setup:None) -> None:  # pylint: disable=unused-argument
        """Test running delete command with no ads."""
        with patch.object(test_bot, "load_ads", return_value = []):
            await test_bot.run(["script.py", "delete"])
            assert test_bot.command == "delete"

    @pytest.mark.asyncio
    async def test_run_publish_command_no_ads(self, test_bot:KleinanzeigenBot, mock_config_setup:None) -> None:  # pylint: disable=unused-argument
        """Test running publish command with no ads."""
        with patch.object(test_bot, "load_ads", return_value = []):
            await test_bot.run(["script.py", "publish"])
            assert test_bot.command == "publish"

    @pytest.mark.asyncio
    async def test_run_download_command_default_selector(self, test_bot:KleinanzeigenBot, mock_config_setup:None) -> None:  # pylint: disable=unused-argument
        """Test running download command with default selector."""
        with patch.object(test_bot, "download_ads", new_callable = AsyncMock):
            await test_bot.run(["script.py", "download"])
            assert test_bot.ads_selector == "new"

    @pytest.mark.asyncio
    async def test_run_update_default_selector(self, test_bot:KleinanzeigenBot, mock_config_setup:None) -> None:  # pylint: disable=unused-argument
        """Test running update command with default selector falls back to changed."""
        with patch.object(test_bot, "load_ads", return_value = []):
            await test_bot.run(["script.py", "update"])
            assert test_bot.ads_selector == "changed"

    @pytest.mark.asyncio
    async def test_run_extend_default_selector(self, test_bot:KleinanzeigenBot, mock_config_setup:None) -> None:  # pylint: disable=unused-argument
        """Test running extend command with default selector falls back to all."""
        with patch.object(test_bot, "load_ads", return_value = []):
            await test_bot.run(["script.py", "extend"])
            assert test_bot.ads_selector == "all"

    def test_load_ads_no_files(self, test_bot:KleinanzeigenBot) -> None:
        """Test loading ads with no files."""
        test_bot.config.ad_files = ["nonexistent/*.yaml"]
        ads = test_bot.load_ads()
        assert len(ads) == 0


class TestKleinanzeigenBotAdManagement:
    """Tests for ad management functionality."""

    @pytest.mark.asyncio
    async def test_download_ads_with_specific_ids(self, test_bot:KleinanzeigenBot, mock_config_setup:None) -> None:  # pylint: disable=unused-argument
        """Test downloading ads with specific IDs."""
        test_bot.ads_selector = "123,456"
        with patch.object(test_bot, "download_ads", new_callable = AsyncMock):
            await test_bot.run(["script.py", "download", "--ads=123,456"])
            assert test_bot.ads_selector == "123,456"

    @pytest.mark.asyncio
    async def test_run_publish_invalid_selector(self, test_bot:KleinanzeigenBot, mock_config_setup:None) -> None:  # pylint: disable=unused-argument
        """Test running publish with invalid selector exits with error."""
        with pytest.raises(SystemExit) as exc_info:
            await test_bot.run(["script.py", "publish", "--ads=invalid"])
        assert exc_info.value.code == 2

    @pytest.mark.asyncio
    async def test_run_download_invalid_selector(self, test_bot:KleinanzeigenBot, mock_config_setup:None) -> None:  # pylint: disable=unused-argument
        """Test running download with invalid selector exits with error."""
        with pytest.raises(SystemExit) as exc_info:
            await test_bot.run(["script.py", "download", "--ads=invalid"])
        assert exc_info.value.code == 2

    @pytest.mark.asyncio
    async def test_run_update_invalid_selector(self, test_bot:KleinanzeigenBot, mock_config_setup:None) -> None:  # pylint: disable=unused-argument
        """Test running update with invalid selector exits with error."""
        with pytest.raises(SystemExit) as exc_info:
            await test_bot.run(["script.py", "update", "--ads=invalid"])
        assert exc_info.value.code == 2

    @pytest.mark.asyncio
    async def test_run_extend_invalid_selector(self, test_bot:KleinanzeigenBot, mock_config_setup:None) -> None:  # pylint: disable=unused-argument
        """Test running extend with invalid selector exits with error."""
        with pytest.raises(SystemExit) as exc_info:
            await test_bot.run(["script.py", "extend", "--ads=invalid"])
        assert exc_info.value.code == 2


class TestKleinanzeigenBotAdConfiguration:
    """Tests for ad configuration functionality."""

    def test_load_config_with_categories(self, test_bot:KleinanzeigenBot, tmp_path:Any) -> None:
        """Test loading config with custom categories."""
        config_path = Path(tmp_path) / "config.yaml"
        with open(config_path, "w", encoding = "utf-8") as f:
            f.write("""
login:
    username: test
    password: test
categories:
    custom_cat: custom_id
""")
        test_bot.config_file_path = str(config_path)
        test_bot.load_config()
        assert "custom_cat" in test_bot.categories
        assert test_bot.categories["custom_cat"] == "custom_id"

    def test_load_ads_with_missing_title(self, test_bot:KleinanzeigenBot, tmp_path:Any, minimal_ad_config:dict[str, Any]) -> None:
        """Test loading ads with missing title."""
        temp_path = Path(tmp_path)
        ad_dir = temp_path / "ads"
        ad_dir.mkdir()
        ad_file = ad_dir / "test_ad.yaml"

        # Create a minimal config with empty title to trigger validation
        ad_cfg = minimal_ad_config | {"title": ""}
        dicts.save_dict(ad_file, ad_cfg)

        # Set config file path to tmp_path and use relative path for ad_files
        test_bot.config_file_path = str(temp_path / "config.yaml")
        test_bot.config.ad_files = ["ads/*.yaml"]
        with pytest.raises(ValidationError) as exc_info:
            test_bot.load_ads()
        assert "title" in str(exc_info.value)

    def test_load_ads_with_invalid_price_type(self, test_bot:KleinanzeigenBot, tmp_path:Any, minimal_ad_config:dict[str, Any]) -> None:
        """Test loading ads with invalid price type."""
        temp_path = Path(tmp_path)
        ad_dir = temp_path / "ads"
        ad_dir.mkdir()
        ad_file = ad_dir / "test_ad.yaml"

        # Create config with invalid price type
        ad_cfg = minimal_ad_config | {"price_type": "INVALID_TYPE"}
        dicts.save_dict(ad_file, ad_cfg)

        # Set config file path to tmp_path and use relative path for ad_files
        test_bot.config_file_path = str(temp_path / "config.yaml")
        test_bot.config.ad_files = ["ads/*.yaml"]
        with pytest.raises(ValidationError) as exc_info:
            test_bot.load_ads()
        assert "price_type" in str(exc_info.value)

    def test_load_ads_with_invalid_shipping_type(self, test_bot:KleinanzeigenBot, tmp_path:Any, minimal_ad_config:dict[str, Any]) -> None:
        """Test loading ads with invalid shipping type."""
        temp_path = Path(tmp_path)
        ad_dir = temp_path / "ads"
        ad_dir.mkdir()
        ad_file = ad_dir / "test_ad.yaml"

        # Create config with invalid shipping type
        ad_cfg = minimal_ad_config | {"shipping_type": "INVALID_TYPE"}
        dicts.save_dict(ad_file, ad_cfg)

        # Set config file path to tmp_path and use relative path for ad_files
        test_bot.config_file_path = str(temp_path / "config.yaml")
        test_bot.config.ad_files = ["ads/*.yaml"]
        with pytest.raises(ValidationError) as exc_info:
            test_bot.load_ads()
        assert "shipping_type" in str(exc_info.value)

    def test_load_ads_with_invalid_price_config(self, test_bot:KleinanzeigenBot, tmp_path:Any, minimal_ad_config:dict[str, Any]) -> None:
        """Test loading ads with invalid price configuration."""
        temp_path = Path(tmp_path)
        ad_dir = temp_path / "ads"
        ad_dir.mkdir()
        ad_file = ad_dir / "test_ad.yaml"

        # Create config with price for GIVE_AWAY type
        ad_cfg = minimal_ad_config | {
            "price_type": "GIVE_AWAY",
            "price": 100,  # Price should not be set for GIVE_AWAY
        }
        dicts.save_dict(ad_file, ad_cfg)

        # Set config file path to tmp_path and use relative path for ad_files
        test_bot.config_file_path = str(temp_path / "config.yaml")
        test_bot.config.ad_files = ["ads/*.yaml"]
        with pytest.raises(ValidationError) as exc_info:
            test_bot.load_ads()
        assert "price" in str(exc_info.value)

    def test_load_ads_with_missing_price(self, test_bot:KleinanzeigenBot, tmp_path:Any, minimal_ad_config:dict[str, Any]) -> None:
        """Test loading ads with missing price for FIXED price type."""
        temp_path = Path(tmp_path)
        ad_dir = temp_path / "ads"
        ad_dir.mkdir()
        ad_file = ad_dir / "test_ad.yaml"

        # Create config with FIXED price type but no price
        ad_cfg = minimal_ad_config | {
            "price_type": "FIXED",
            "price": None,  # Missing required price for FIXED type
        }
        dicts.save_dict(ad_file, ad_cfg)

        # Set config file path to tmp_path and use relative path for ad_files
        test_bot.config_file_path = str(temp_path / "config.yaml")
        test_bot.config.ad_files = ["ads/*.yaml"]
        with pytest.raises(ValidationError) as exc_info:
            test_bot.load_ads()
        assert "price is required when price_type is FIXED" in str(exc_info.value)


class TestKleinanzeigenBotAdDeletion:
    """Tests for ad deletion functionality."""

    @pytest.mark.asyncio
    async def test_delete_ad_by_title(self, test_bot:KleinanzeigenBot, minimal_ad_config:dict[str, Any]) -> None:
        """Test deleting an ad by title."""
        test_bot.page = MagicMock()
        test_bot.page.evaluate = AsyncMock(return_value = {"statusCode": 200, "statusMessage": "OK", "content": "{}"})
        test_bot.page.sleep = AsyncMock()

        # Use minimal config since we only need title for deletion by title
        ad_cfg = Ad.model_validate(
            minimal_ad_config
            | {
                "title": "Test Title",
                "id": None,  # Explicitly set id to None for title-based deletion
            }
        )

        published_ads = [{"title": "Test Title", "id": "67890"}, {"title": "Other Title", "id": "11111"}]

        with (
            patch.object(test_bot, "web_open", new_callable = AsyncMock),
            patch.object(test_bot, "web_find", new_callable = AsyncMock) as mock_find,
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_check", new_callable = AsyncMock, return_value = True),
        ):
            mock_find.return_value.attrs = {"content": "some-token"}
            result = await test_bot.delete_ad(ad_cfg, published_ads, delete_old_ads_by_title = True)
            assert result is True

    @pytest.mark.asyncio
    async def test_delete_ad_by_id(self, test_bot:KleinanzeigenBot, minimal_ad_config:dict[str, Any]) -> None:
        """Test deleting an ad by ID."""
        test_bot.page = MagicMock()
        test_bot.page.evaluate = AsyncMock(return_value = {"statusCode": 200, "statusMessage": "OK", "content": "{}"})
        test_bot.page.sleep = AsyncMock()

        # Create config with ID for deletion by ID
        ad_cfg = Ad.model_validate(
            minimal_ad_config
            | {
                "id": "12345"  # Fixed: use proper dict key syntax
            }
        )

        published_ads = [{"title": "Different Title", "id": "12345"}, {"title": "Other Title", "id": "11111"}]

        with (
            patch.object(test_bot, "web_open", new_callable = AsyncMock),
            patch.object(test_bot, "web_find", new_callable = AsyncMock) as mock_find,
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_check", new_callable = AsyncMock, return_value = True),
        ):
            mock_find.return_value.attrs = {"content": "some-token"}
            result = await test_bot.delete_ad(ad_cfg, published_ads, delete_old_ads_by_title = False)
            assert result is True

    @pytest.mark.asyncio
    async def test_delete_ad_by_id_with_non_string_csrf_token(self, test_bot:KleinanzeigenBot, minimal_ad_config:dict[str, Any]) -> None:
        """Test deleting an ad by ID with non-string CSRF token to cover str() conversion."""
        test_bot.page = MagicMock()
        test_bot.page.evaluate = AsyncMock(return_value = {"statusCode": 200, "statusMessage": "OK", "content": "{}"})
        test_bot.page.sleep = AsyncMock()

        # Create config with ID for deletion by ID
        ad_cfg = Ad.model_validate(minimal_ad_config | {"id": "12345"})

        published_ads = [{"title": "Different Title", "id": "12345"}, {"title": "Other Title", "id": "11111"}]

        with (
            patch.object(test_bot, "web_open", new_callable = AsyncMock),
            patch.object(test_bot, "web_find", new_callable = AsyncMock) as mock_find,
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_check", new_callable = AsyncMock, return_value = True),
            patch.object(test_bot, "web_request", new_callable = AsyncMock) as mock_request,
        ):
            # Mock non-string CSRF token to test str() conversion
            mock_find.return_value.attrs = {"content": 12345}  # Non-string token
            result = await test_bot.delete_ad(ad_cfg, published_ads, delete_old_ads_by_title = False)
            assert result is True

            # Verify that str() was called on the CSRF token
            mock_request.assert_called_once()
            call_args = mock_request.call_args
            assert call_args[1]["headers"]["x-csrf-token"] == "12345"  # Should be converted to string


class TestKleinanzeigenBotAdRepublication:
    """Tests for ad republication functionality."""

    def test_check_ad_republication_with_changes(self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any]) -> None:
        """Test that ads with changes are marked for republication."""
        # Mock the description config to prevent modification of the description
        test_bot.config.ad_defaults = AdDefaults.model_validate({"description": {"prefix": "", "suffix": ""}})

        # Create ad config with all necessary fields for republication
        ad_cfg = Ad.model_validate(
            base_ad_config | {"id": "12345", "updated_on": "2024-01-01T00:00:01", "created_on": "2024-01-01T00:00:01", "description": "Changed description"}
        )

        # Create a temporary directory and file
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            ad_dir = temp_path / "ads"
            ad_dir.mkdir()
            ad_file = ad_dir / "test_ad.yaml"

            dicts.save_dict(ad_file, ad_cfg.model_dump())

            # Set config file path and use relative path for ad_files
            test_bot.config_file_path = str(temp_path / "config.yaml")
            test_bot.config.ad_files = ["ads/*.yaml"]

            ads_to_publish = test_bot.load_ads()
            assert len(ads_to_publish) == 1

    def test_check_ad_republication_no_changes(self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any]) -> None:
        """Test that unchanged ads within interval are not marked for republication."""
        current_time = misc.now()
        three_days_ago = (current_time - timedelta(days = 3)).isoformat()

        # Create ad config with timestamps for republication check
        ad_cfg = Ad.model_validate(base_ad_config | {"id": "12345", "updated_on": three_days_ago, "created_on": three_days_ago})

        # Calculate hash before making the copy to ensure they match
        ad_cfg_orig = ad_cfg.model_dump()
        current_hash = ad_cfg.update_content_hash().content_hash
        ad_cfg_orig["content_hash"] = current_hash

        # Mock the config to prevent actual file operations
        test_bot.config.ad_files = ["test.yaml"]
        with (
            patch("kleinanzeigen_bot.utils.dicts.load_dict_if_exists", return_value = ad_cfg_orig),
            patch("kleinanzeigen_bot.utils.dicts.load_dict", return_value = {}),
        ):  # Mock ad_fields.yaml
            ads_to_publish = test_bot.load_ads()
            assert len(ads_to_publish) == 0  # No ads should be marked for republication


class TestKleinanzeigenBotShippingOptions:
    """Tests for shipping options functionality."""

    @pytest.mark.asyncio
    async def test_shipping_options_mapping(self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any], tmp_path:Any) -> None:
        """Test that shipping options are mapped correctly."""
        # Create a mock page to simulate browser context
        test_bot.page = MagicMock()
        test_bot.page.url = "https://www.kleinanzeigen.de/p-anzeige-aufgeben-bestaetigung.html?adId=12345"
        test_bot.page.evaluate = AsyncMock()

        # Create ad config with specific shipping options
        ad_cfg = Ad.model_validate(
            base_ad_config
            | {
                "shipping_options": ["DHL_2", "Hermes_Päckchen"],
                "updated_on": "2024-01-01T00:00:00",  # Add created_on to prevent KeyError
                "created_on": "2024-01-01T00:00:00",  # Add updated_on for consistency
            }
        )

        # Create the original ad config and published ads list
        ad_cfg.update_content_hash()  # Add content hash to prevent republication
        ad_cfg_orig = ad_cfg.model_dump()
        published_ads:list[dict[str, Any]] = []

        # Set up default config values needed for the test
        test_bot.config.publishing = PublishingConfig.model_validate({"delete_old_ads": "BEFORE_PUBLISH", "delete_old_ads_by_title": False})

        # Create temporary file path
        ad_file = Path(tmp_path) / "test_ad.yaml"

        # Mock web_execute to handle all JavaScript calls
        async def mock_web_execute(script:str) -> Any:
            if script == "document.body.scrollHeight":
                return 0  # Return integer to prevent scrolling loop
            if "window.location.href" in script:
                return test_bot.page.url  # Return confirmation URL for ad_id extraction
            return None

        # Create mock elements
        csrf_token_elem = MagicMock()
        csrf_token_elem.attrs = {"content": "csrf-token-123"}

        shipping_form_elem = MagicMock()
        shipping_form_elem.attrs = {}

        shipping_size_radio = MagicMock()
        shipping_size_radio.attrs = {"checked": ""}  # SMALL radio is pre-checked

        shipping_checkbox = MagicMock()
        shipping_checkbox.attrs = {"checked": ""}  # Simulate pre-checked carriers for SMALL

        category_path_elem = MagicMock()
        category_path_elem.apply = AsyncMock(return_value = "Test Category")

        # Mock the necessary web interaction methods
        with (
            patch.object(test_bot, "web_execute", side_effect = mock_web_execute),
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            patch.object(test_bot, "web_find", new_callable = AsyncMock) as mock_find,
            patch.object(test_bot, "web_probe", new_callable = AsyncMock) as mock_probe,
            patch.object(test_bot, "web_select", new_callable = AsyncMock),
            patch.object(test_bot, "web_input", new_callable = AsyncMock),
            patch.object(test_bot, "web_open", new_callable = AsyncMock),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch.object(test_bot, "web_check", new_callable = AsyncMock, return_value = True),
            patch.object(test_bot, "web_request", new_callable = AsyncMock),
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock),
            patch.object(test_bot, "web_await", new_callable = AsyncMock),
            patch.object(test_bot, "_KleinanzeigenBot__set_contact_fields", new_callable = AsyncMock),
            patch("builtins.input", return_value = ""),
            patch.object(test_bot, "web_scroll_page_down", new_callable = AsyncMock),
        ):

            async def probe_side_effect(selector_type:By, selector_value:str, **_:Any) -> Element | None:
                if selector_type == By.ID and selector_value == "ad-category-path":
                    return category_path_elem
                return None

            mock_probe.side_effect = probe_side_effect

            # Mock web_find to simulate element detection
            async def mock_find_side_effect(selector_type:By, selector_value:str, **_:Any) -> Element | None:
                if selector_value == "meta[name=_csrf]":
                    return csrf_token_elem
                if selector_value == "myftr-shppngcrt-frm":
                    return shipping_form_elem
                # New shipping dialog: size radio via XPath with value attribute
                if selector_type == By.XPATH and '@type="radio"' in selector_value and "@value=" in selector_value:
                    return shipping_size_radio
                if selector_type == By.XPATH and '@type="checkbox"' in selector_value and "@value=" in selector_value:
                    return shipping_checkbox
                return None

            mock_find.side_effect = mock_find_side_effect

            # Mock web_check to return True for radio button checked state
            with patch.object(test_bot, "web_check", new_callable = AsyncMock) as mock_check:
                mock_check.return_value = True

                # Test through the public interface by publishing an ad
                await test_bot.publish_ad(str(ad_file), ad_cfg, ad_cfg_orig, published_ads)

            # Verify that the shipping dialog was interacted with:
            # - web_find should have been called for the size radio (XPath with @type="radio")
            # - web_click should have been called to deselect unwanted carriers and close dialog
            radio_find_calls = [c for c in mock_find.await_args_list if len(c.args) >= 2 and '@type="radio"' in str(c.args[1])]
            assert len(radio_find_calls) >= 1, "Expected at least one web_find for size radio"

            click_xpath_values = [str(c.args[1]) for c in mock_click.await_args_list if len(c.args) >= 2 and c.args[0] == By.XPATH]
            # Should click Weiter, deselect HERMES_002 (unwanted), and click Fertig
            assert any("Weiter" in v for v in click_xpath_values), "Expected click on Weiter button"
            assert any("HERMES_002" in v for v in click_xpath_values), "Expected click to deselect HERMES_002"
            assert not any("DHL_001" in v for v in click_xpath_values), "Did not expect click for wanted DHL_001"
            assert not any("HERMES_001" in v for v in click_xpath_values), "Did not expect click for wanted HERMES_001"
            assert any("Fertig" in v for v in click_xpath_values), "Expected click on Fertig button"

            # Verify the file was created in the temporary directory
            assert ad_file.exists()

    @pytest.mark.asyncio
    async def test_cross_drive_path_fallback_windows(self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any]) -> None:
        """Test that cross-drive path handling falls back to absolute path on Windows."""
        # Create ad config
        ad_cfg = Ad.model_validate(
            base_ad_config
            | {
                "updated_on": "2024-01-01T00:00:00",
                "created_on": "2024-01-01T00:00:00",
                "auto_price_reduction": {"enabled": True, "strategy": "FIXED", "amount": 10, "min_price": 50, "delay_reposts": 0, "delay_days": 0},
                "price": 100,
                "repost_count": 1,
                "price_reduction_count": 0,
            }
        )
        ad_cfg.update_content_hash()
        ad_cfg_orig = ad_cfg.model_dump()

        # Simulate Windows cross-drive scenario
        # Config on D:, ad file on C:
        test_bot.config_file_path = "D:\\project\\config.yaml"
        ad_file = "C:\\temp\\test_ad.yaml"

        # Create a sentinel exception to abort publish_ad early
        class _SentinelException(Exception):
            pass

        # Track what path argument __apply_auto_price_reduction receives
        recorded_path:list[str] = []

        def mock_apply_auto_price_reduction(
            ad_cfg:Ad,
            ad_cfg_orig:dict[str, Any],
            ad_file_relative:str,
            *,
            mode:AdUpdateStrategy = AdUpdateStrategy.REPLACE,
        ) -> None:
            _ = mode
            recorded_path.append(ad_file_relative)
            raise _SentinelException("Abort early for test")

        # Mock Path to use PureWindowsPath for testing cross-drive behavior
        with (
            patch("kleinanzeigen_bot.Path", PureWindowsPath),
            patch("kleinanzeigen_bot.apply_auto_price_reduction", side_effect = mock_apply_auto_price_reduction),
            patch.object(test_bot, "web_open", new_callable = AsyncMock),
            patch.object(test_bot, "delete_ad", new_callable = AsyncMock),
        ):
            # Call publish_ad and expect sentinel exception
            try:
                await test_bot.publish_ad(ad_file, ad_cfg, ad_cfg_orig, [], AdUpdateStrategy.REPLACE)
                pytest.fail("Expected _SentinelException to be raised")
            except _SentinelException:
                # This is expected - the test aborts early
                pass

        # Verify the path argument is the absolute path (fallback behavior)
        assert len(recorded_path) == 1
        assert recorded_path[0] == ad_file, f"Expected absolute path fallback, got: {recorded_path[0]}"

    @pytest.mark.asyncio
    async def test_auto_price_reduction_conditional_on_mode_and_on_update(
        self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any], tmp_path:Path
    ) -> None:
        """Test price reduction dispatch across REPLACE and MODIFY modes.

        - REPLACE mode always calls apply_auto_price_reduction.
        - MODIFY mode with on_update=false still calls it for restore-first (no cycle advance).
        - MODIFY mode with on_update=true calls it with full evaluation and cycle advance.
        """
        # Shared ad config with auto price reduction enabled
        ad_cfg = Ad.model_validate(
            base_ad_config
            | {
                "id": 12345,
                "price": 200,
                "auto_price_reduction": {"enabled": True, "strategy": "FIXED", "amount": 50, "min_price": 50, "delay_reposts": 0, "delay_days": 0},
                "repost_count": 1,
                "price_reduction_count": 0,
                "updated_on": "2024-01-01T00:00:00",
                "created_on": "2024-01-01T00:00:00",
            }
        )
        ad_cfg.update_content_hash()
        ad_cfg_orig = ad_cfg.model_dump()

        mock_response = {"statusCode": 200, "statusMessage": "OK", "content": "{}"}

        async def mock_web_execute_price_reduction(script:str) -> Any:
            if "window.location.href" in script:
                return "https://www.kleinanzeigen.de/p-anzeige-aufgeben-bestaetigung.html?adId=12345"
            return mock_response

        with (
            patch("kleinanzeigen_bot.apply_auto_price_reduction") as mock_apply,
            patch.object(test_bot, "web_find", new_callable = AsyncMock),
            patch.object(test_bot, "web_input", new_callable = AsyncMock),
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_open", new_callable = AsyncMock),
            patch.object(test_bot, "web_select", new_callable = AsyncMock),
            patch.object(test_bot, "web_check", new_callable = AsyncMock, return_value = False),
            patch.object(test_bot, "web_await", new_callable = AsyncMock),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch.object(test_bot, "web_execute", side_effect = mock_web_execute_price_reduction),
            patch.object(test_bot, "web_request", new_callable = AsyncMock, return_value = mock_response),
            patch.object(test_bot, "web_scroll_page_down", new_callable = AsyncMock),
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, return_value = []),
            patch.object(test_bot, "_web_find_all_once", new_callable = AsyncMock, return_value = []),
            patch.object(test_bot, "check_and_wait_for_captcha", new_callable = AsyncMock),
            patch.object(test_bot, "_KleinanzeigenBot__set_contact_fields", new_callable = AsyncMock),
            patch("builtins.input", return_value = ""),
            patch("kleinanzeigen_bot.utils.misc.ainput", new_callable = AsyncMock, return_value = ""),
        ):
            test_bot.page = MagicMock()
            test_bot.page.url = "https://www.kleinanzeigen.de/p-anzeige-aufgeben-bestaetigung.html?adId=12345"
            test_bot.config.publishing.delete_old_ads = "BEFORE_PUBLISH"

            # --- REPLACE mode: always calls apply_auto_price_reduction ---
            await test_bot.publish_ad(str(tmp_path / "ad.yaml"), ad_cfg, ad_cfg_orig, [], AdUpdateStrategy.REPLACE)
            mock_apply.assert_called_once()
            assert mock_apply.call_args.kwargs["mode"] == AdUpdateStrategy.REPLACE

            # --- MODIFY mode with default config (on_update=false): still calls for restore-first ---
            mock_apply.reset_mock()
            await test_bot.publish_ad(str(tmp_path / "ad.yaml"), ad_cfg, ad_cfg_orig, [], AdUpdateStrategy.MODIFY)
            mock_apply.assert_called_once()
            assert mock_apply.call_args.kwargs["mode"] == AdUpdateStrategy.MODIFY

            # --- MODIFY mode with on_update=true: SHOULD call (new conditional behavior) ---
            mock_apply.reset_mock()
            ad_cfg.auto_price_reduction = AutoPriceReductionConfig(
                enabled = True,
                strategy = "FIXED",
                amount = 50,
                min_price = 50,
                delay_reposts = 0,
                delay_days = 0,
                on_update = True,
            )
            await test_bot.publish_ad(str(tmp_path / "ad.yaml"), ad_cfg, ad_cfg_orig, [], AdUpdateStrategy.MODIFY)
            mock_apply.assert_called_once()
            assert mock_apply.call_args.kwargs["mode"] == AdUpdateStrategy.MODIFY

    @pytest.mark.asyncio
    async def test_special_attributes_compound_name_lookup(self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any]) -> None:
        """Compound special-attribute names should be matched via original key in @name."""
        ad_cfg = Ad.model_validate(
            base_ad_config
            | {
                "special_attributes": {"autos.model_s": "a3"},
                "updated_on": "2024-01-01T00:00:00",
                "created_on": "2024-01-01T00:00:00",
            }
        )

        model_elem = MagicMock()
        model_attrs = MagicMock()
        model_attrs.id = None
        model_attrs.name = "attributeMap[autos.marke_s+autos.model_s]"
        model_attrs.get.side_effect = lambda key, default = None: {
            "id": None,
            "name": "attributeMap[autos.marke_s+autos.model_s]",
            "type": None,
            "role": None,
        }.get(key, default)
        model_elem.attrs = model_attrs
        model_elem.local_name = "select"

        with (
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock) as mock_find_all,
            patch.object(test_bot, "web_select", new_callable = AsyncMock) as mock_select,
        ):

            async def find_all_side_effect(selector_type:By, selector_value:str, **_:Any) -> list[Element]:
                if selector_type == By.XPATH and "autos.model_s" in selector_value:
                    return [model_elem]
                return []

            mock_find_all.side_effect = find_all_side_effect

            await getattr(test_bot, "_KleinanzeigenBot__set_special_attributes")(ad_cfg)

            assert mock_select.await_count == 1
            assert mock_select.await_args is not None
            assert mock_select.await_args.args[0] == By.XPATH
            assert "contains(@name, 'autos.model_s')" in str(mock_select.await_args.args[1])
            assert mock_select.await_args.args[2] == "a3"

    @pytest.mark.asyncio
    async def test_special_attributes_prefers_button_combobox_over_hidden_input(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """Hidden backing inputs must not win over visible button combobox controls."""
        ad_cfg = Ad.model_validate(
            base_ad_config
            | {
                "special_attributes": {"color_s": "beige"},
                "updated_on": "2024-01-01T00:00:00",
                "created_on": "2024-01-01T00:00:00",
            }
        )

        hidden_elem = MagicMock()
        hidden_attrs = MagicMock()
        hidden_attrs.id = None
        hidden_attrs.type = "hidden"
        hidden_attrs.get.side_effect = lambda key, default = None: {
            "id": None,
            "name": "attributeMap[kleidung_herren.color]",
            "type": "hidden",
            "role": None,
        }.get(key, default)
        hidden_elem.attrs = hidden_attrs
        hidden_elem.local_name = "input"

        button_elem = MagicMock()
        button_attrs = MagicMock()
        button_attrs.id = "kleidung_herren.color"
        button_attrs.type = "button"
        button_attrs.get.side_effect = lambda key, default = None: {
            "id": "kleidung_herren.color",
            "name": None,
            "type": "button",
            "role": "combobox",
        }.get(key, default)
        button_elem.attrs = button_attrs
        button_elem.local_name = "button"

        with (
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, return_value = [hidden_elem, button_elem]),
            patch.object(test_bot, "_KleinanzeigenBot__select_button_combobox", new_callable = AsyncMock) as mock_button_combobox,
            patch.object(test_bot, "web_input", new_callable = AsyncMock) as mock_input,
        ):
            await getattr(test_bot, "_KleinanzeigenBot__set_special_attributes")(ad_cfg)

        mock_button_combobox.assert_awaited_once_with("kleidung_herren.color", "beige")
        mock_input.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("combobox_type", ["text", None], ids = ["type-text", "type-absent"])
    async def test_special_attributes_combobox_routed_over_hidden_input(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        combobox_type:str | None,
    ) -> None:
        """Combobox <input> must be routed to web_select_combobox regardless of type attribute presence."""
        ad_cfg = Ad.model_validate(
            base_ad_config
            | {
                "special_attributes": {"brand_s": "armani"},
                "updated_on": "2024-01-01T00:00:00",
                "created_on": "2024-01-01T00:00:00",
            }
        )

        hidden_elem = MagicMock()
        hidden_attrs = MagicMock()
        hidden_attrs.id = None
        hidden_attrs.type = "hidden"
        hidden_attrs.get.side_effect = lambda key, default = None: {
            "id": None,
            "name": "attributeMap[kleidung_herren.brand]",
            "type": "hidden",
            "role": None,
        }.get(key, default)
        hidden_elem.attrs = hidden_attrs
        hidden_elem.local_name = "input"

        combobox_elem = MagicMock()
        combobox_attrs = MagicMock()
        combobox_attrs.id = "kleidung_herren.brand"
        combobox_attrs.type = combobox_type
        combobox_attrs.get.side_effect = lambda key, default = None: {
            "id": "kleidung_herren.brand",
            "name": None,
            "type": combobox_type,
            "role": "combobox",
        }.get(key, default)
        combobox_elem.attrs = combobox_attrs
        combobox_elem.local_name = "input"

        with (
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, return_value = [hidden_elem, combobox_elem]),
            patch.object(test_bot, "web_select_combobox", new_callable = AsyncMock) as mock_select_combobox,
            patch.object(test_bot, "web_input", new_callable = AsyncMock) as mock_input,
        ):
            await getattr(test_bot, "_KleinanzeigenBot__set_special_attributes")(ad_cfg)

        mock_select_combobox.assert_awaited_once_with(By.ID, "kleidung_herren.brand", "armani")
        mock_input.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("checked_attr", "attribute_value", "expect_click"),
        [(None, "true", True), ("checked", "true", False), ("checked", "false", True)],
    )
    async def test_special_attributes_checkbox_clicks_only_on_state_change(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        checked_attr:str | None,
        attribute_value:str,
        expect_click:bool,
    ) -> None:
        """Checkbox attributes should only click when current and desired states differ."""
        ad_cfg = Ad.model_validate(
            base_ad_config
            | {
                "special_attributes": {"feature_b": attribute_value},
                "updated_on": "2024-01-01T00:00:00",
                "created_on": "2024-01-01T00:00:00",
            }
        )

        checkbox_elem = MagicMock()
        checkbox_attrs = MagicMock()
        checkbox_attrs.id = "feature"
        checkbox_attrs.type = "checkbox"
        checkbox_attrs.get.side_effect = lambda key, default = None: {
            "id": "feature",
            "name": "attributeMap[feature]",
            "type": "checkbox",
            "role": None,
            "checked": checked_attr,
        }.get(key, default)
        checkbox_elem.attrs = checkbox_attrs
        checkbox_elem.local_name = "input"

        with (
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, return_value = [checkbox_elem]),
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
        ):
            await getattr(test_bot, "_KleinanzeigenBot__set_special_attributes")(ad_cfg)

        if expect_click:
            mock_click.assert_awaited_once_with(By.ID, "feature")
        else:
            mock_click.assert_not_awaited()


class TestConditionSelector:
    """Regression tests for condition dialog selection."""

    @pytest.mark.asyncio
    async def test_condition_selects_radio_by_value(self, test_bot:KleinanzeigenBot) -> None:
        """Condition selection should resolve radios by value in the new dialog."""
        dialog = MagicMock()
        trigger = MagicMock()
        trigger.attrs = {"id": "condition-trigger", "aria-controls": "condition-dialog"}
        trigger.click = AsyncMock()
        radio = MagicMock()
        radio_attrs = MagicMock()
        radio_attrs.id = "radio-condition-ok"
        radio_attrs.get.side_effect = lambda key, default = None: "radio-condition-ok" if key == "id" else default
        radio.attrs = radio_attrs
        radio.click = AsyncMock()

        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = trigger),
            patch.object(test_bot, "web_find", new_callable = AsyncMock) as mock_find,
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
        ):

            async def find_side_effect(selector_type:By, selector_value:str, **_:Any) -> Element:
                if selector_type != By.XPATH:
                    raise TimeoutError("unexpected selector")
                if "@type='radio'" in selector_value and "@value='ok'" in selector_value:
                    return radio
                if "dialog" in selector_value:
                    return dialog
                raise TimeoutError("selector not found")

            mock_find.side_effect = find_side_effect

            handled = await getattr(test_bot, "_KleinanzeigenBot__set_condition")("ok")

            assert handled is True

            clicked_xpath_selectors = [str(call.args[1]) for call in mock_click.await_args_list if len(call.args) > 1]
            trigger.click.assert_awaited_once()
            assert any("label[@for=" in selector and "radio-condition-ok" in selector for selector in clicked_xpath_selectors)
            assert any("Bestätigen" in selector for selector in clicked_xpath_selectors)

    @pytest.mark.asyncio
    async def test_condition_missing_selector_returns_not_handled(self, test_bot:KleinanzeigenBot) -> None:
        """Missing condition trigger should return not-handled and use generic fallback path."""

        async def find_side_effect(selector_type:By, selector_value:str, *_:Any, **__:Any) -> Element:
            if selector_type == By.XPATH and "contains(@for, '.condition')" in selector_value and "@aria-haspopup='dialog'" in selector_value:
                raise TimeoutError("missing trigger")
            raise TimeoutError("unexpected selector")

        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = None),
            patch.object(test_bot, "web_find", new_callable = AsyncMock, side_effect = find_side_effect),
        ):
            handled = await getattr(test_bot, "_KleinanzeigenBot__set_condition")("ok")

        assert handled is False

    @pytest.mark.asyncio
    async def test_condition_unknown_value_raises(self, test_bot:KleinanzeigenBot) -> None:
        """Unknown condition values should raise when no matching radio option is present."""
        dialog = MagicMock()
        trigger = MagicMock()
        trigger.attrs = {"id": "condition-trigger", "aria-controls": "condition-dialog"}
        trigger.click = AsyncMock()

        async def find_side_effect(selector_type:By, selector_value:str, **_:Any) -> Element:
            if selector_type != By.XPATH:
                raise TimeoutError("unexpected selector")
            if "@type='radio'" in selector_value:
                raise TimeoutError("value not found")
            if "dialog" in selector_value:
                return dialog
            raise TimeoutError("selector not found")

        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = trigger),
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_find", new_callable = AsyncMock) as mock_find,
        ):
            mock_find.side_effect = find_side_effect

            with pytest.raises(TimeoutError, match = "Failed to set attribute 'condition_s'"):
                await getattr(test_bot, "_KleinanzeigenBot__set_condition")("defect")

    @pytest.mark.asyncio
    async def test_condition_rejects_shipping_trigger(self, test_bot:KleinanzeigenBot) -> None:
        """Condition dialog path should not click shipping trigger controls."""
        trigger = MagicMock()
        trigger.attrs = {
            "id": "ad-shipping-options",
            "aria-controls": None,
            "aria-haspopup": "dialog",
        }
        trigger.click = AsyncMock()

        async def find_side_effect(selector_type:By, selector_value:str, **_:Any) -> Element:
            if selector_type == By.XPATH and "contains(@for, '.condition')" in selector_value:
                return trigger
            raise TimeoutError("unexpected selector")

        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = trigger),
            patch.object(test_bot, "web_find", new_callable = AsyncMock, side_effect = find_side_effect),
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
        ):
            handled = await getattr(test_bot, "_KleinanzeigenBot__set_condition")("new")

        assert handled is False
        # Regression guard: wrong shipping trigger must never be clicked by condition handler
        trigger.click.assert_not_awaited()
        mock_click.assert_not_awaited()


class TestConditionFallbackToGenericHandler:
    """Regression tests for condition_s fallback behavior.

    When __set_condition reports "not handled" (e.g. category uses a button-combobox
    instead of a dialog), __set_special_attributes should fall through to the generic
    XPath-based handler.
    """

    @pytest.mark.asyncio
    async def test_condition_falls_back_to_generic_handler_when_dialog_not_handled(self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any]) -> None:
        """When condition dialog is not handled, generic handler should be used as fallback."""
        ad_cfg = Ad.model_validate(base_ad_config | {"category": "185/249", "special_attributes": {"condition_s": "new"}, "shipping_type": "PICKUP"})

        button_elem = MagicMock()
        button_attrs = MagicMock()
        button_attrs.get.side_effect = lambda key, default = None: {
            "id": "modellbau.condition",
            "type": "button",
            "role": "combobox",
            "name": None,
        }.get(key, default)
        button_elem.attrs = button_attrs
        button_elem.local_name = "button"

        with (
            patch.object(
                test_bot,
                "_KleinanzeigenBot__set_condition",
                new_callable = AsyncMock,
                return_value = False,
            ) as mock_set_condition,
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, return_value = [button_elem]),
            patch.object(
                test_bot,
                "_KleinanzeigenBot__select_button_combobox",
                new_callable = AsyncMock,
            ) as mock_select_combobox,
        ):
            await getattr(test_bot, "_KleinanzeigenBot__set_special_attributes")(ad_cfg)

        mock_set_condition.assert_awaited_once_with("new")
        mock_select_combobox.assert_awaited_once_with("modellbau.condition", "new")

    @pytest.mark.asyncio
    async def test_condition_timeout_propagates_instead_of_falling_back(self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any]) -> None:
        """Real condition dialog failures should propagate and not silently use generic fallback."""
        ad_cfg = Ad.model_validate(base_ad_config | {"category": "161/176", "special_attributes": {"condition_s": "ok"}})

        with (
            patch.object(
                test_bot,
                "_KleinanzeigenBot__set_condition",
                new_callable = AsyncMock,
                side_effect = TimeoutError("dialog timeout"),
            ),
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock) as mock_find_all,
            pytest.raises(TimeoutError, match = "dialog timeout"),
        ):
            await getattr(test_bot, "_KleinanzeigenBot__set_special_attributes")(ad_cfg)

        mock_find_all.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_condition_uses_dialog_when_available(self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any]) -> None:
        """When condition dialog works, it should be used without falling back."""
        ad_cfg = Ad.model_validate(base_ad_config | {"category": "161/176", "special_attributes": {"condition_s": "ok"}})

        with patch.object(
            test_bot,
            "_KleinanzeigenBot__set_condition",
            new_callable = AsyncMock,
            return_value = True,
        ) as mock_set_condition:
            await getattr(test_bot, "_KleinanzeigenBot__set_special_attributes")(ad_cfg)

        mock_set_condition.assert_awaited_once_with("ok")


class TestCategoryProbeBehavior:
    """Tests for category marker probing without retry backoff."""

    @pytest.mark.asyncio
    async def test_set_category_uses_probe_for_auto_selected_marker(self, test_bot:KleinanzeigenBot) -> None:
        """In __set_category, category marker lookup should go through web_probe."""
        category_marker = MagicMock()
        category_marker.apply = AsyncMock(return_value = "Auto Category")

        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = category_marker) as mock_probe,
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_find", new_callable = AsyncMock),
            patch.object(test_bot, "web_open", new_callable = AsyncMock),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
        ):
            await getattr(test_bot, "_KleinanzeigenBot__set_category")("185/249", "data/my_ads/ad.yaml")

        mock_probe.assert_awaited_once_with(By.ID, "ad-category-path")

    @pytest.mark.asyncio
    async def test_set_category_without_explicit_category_requires_probe_match(self, test_bot:KleinanzeigenBot) -> None:
        """When no category is configured, missing marker should fail fast."""
        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = None),
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            pytest.raises(AssertionError, match = "No category specified"),
        ):
            await getattr(test_bot, "_KleinanzeigenBot__set_category")(None, "data/my_ads/ad.yaml")


class TestShippingDialogFlow:
    """Regression tests for shipping dialog flow using new radio selectors only."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("selected", [False, True])
    async def test_pickup_shipping_radio_selection(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        selected:bool,
    ) -> None:
        """PICKUP shipping should click the pickup radio only when it is not already selected."""
        ad_cfg = Ad.model_validate(base_ad_config | {"shipping_type": "PICKUP"})

        with (
            patch.object(test_bot, "web_check", new_callable = AsyncMock, return_value = selected),
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
        ):
            await getattr(test_bot, "_KleinanzeigenBot__set_shipping")(ad_cfg)

        if selected:
            mock_click.assert_not_awaited()
        else:
            mock_click.assert_awaited_once()
            assert mock_click.call_args.args[:2] == (By.ID, "ad-shipping-enabled-no")

    @pytest.mark.asyncio
    async def test_pickup_shipping_raises_when_radio_lookup_times_out(self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any]) -> None:
        """PICKUP shipping should fail fast when pickup radio selector is unavailable."""
        ad_cfg = Ad.model_validate(base_ad_config | {"shipping_type": "PICKUP"})

        with (
            patch.object(test_bot, "web_check", new_callable = AsyncMock, side_effect = TimeoutError("pickup lookup timed out")),
            pytest.raises(TimeoutError, match = "Failed to set shipping attribute for type 'PICKUP'!"),
        ):
            await getattr(test_bot, "_KleinanzeigenBot__set_shipping")(ad_cfg)

    @pytest.mark.asyncio
    async def test_shipping_without_options_uses_radio_and_dialog(self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any]) -> None:
        """Shipping without package options should use radio + dialog flow."""
        ad_cfg = Ad.model_validate(base_ad_config | {"shipping_type": "SHIPPING", "shipping_options": [], "shipping_costs": 4.95})

        with (
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = None),
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = MagicMock()),
            patch.object(test_bot, "web_input", new_callable = AsyncMock) as mock_input,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
        ):
            await getattr(test_bot, "_KleinanzeigenBot__set_shipping")(ad_cfg)

            click_args = [c.args for c in mock_click.await_args_list]
            assert any(len(a) >= 2 and a[0] == By.ID and a[1] == "ad-individual-shipping-checkbox-control" for a in click_args)
            assert any("Fertig" in str(a[1]) for a in click_args if len(a) >= 2)
            mock_input.assert_awaited_once_with(By.ID, "ad-individual-shipping-price", "4,95")

    @pytest.mark.asyncio
    async def test_shipping_finish_timeout_raises(self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any]) -> None:
        """Timeout while confirming shipping dialog should raise a clear error."""
        ad_cfg = Ad.model_validate(base_ad_config | {"shipping_type": "SHIPPING", "shipping_options": []})

        async def click_side_effect(selector_type:By, selector_value:str, **_:Any) -> None:
            if selector_type == By.XPATH and "Fertig" in selector_value:
                raise TimeoutError("finish timeout")

        with (
            patch.object(test_bot, "web_click", new_callable = AsyncMock, side_effect = click_side_effect),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = None),
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = MagicMock()),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            pytest.raises(TimeoutError, match = "Unable to close shipping dialog!"),
        ):
            await getattr(test_bot, "_KleinanzeigenBot__set_shipping")(ad_cfg)

    @pytest.mark.asyncio
    async def test_shipping_without_options_does_not_toggle_checkbox_when_price_input_visible(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """When price input is already visible, individual-shipping checkbox is not toggled."""
        ad_cfg = Ad.model_validate(base_ad_config | {"shipping_type": "SHIPPING", "shipping_options": [], "shipping_costs": 4.95})

        with (
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = MagicMock()),
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = MagicMock()),
            patch.object(test_bot, "web_input", new_callable = AsyncMock) as mock_input,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
        ):
            await getattr(test_bot, "_KleinanzeigenBot__set_shipping")(ad_cfg)

        click_args = [c.args for c in mock_click.await_args_list]
        assert not any(len(a) >= 2 and a[0] == By.ID and a[1] == "ad-individual-shipping-checkbox-control" for a in click_args)
        mock_input.assert_awaited_once_with(By.ID, "ad-individual-shipping-price", "4,95")


class TestShippingOptionsDialog:
    """Tests for __set_shipping_options using carrier-code-based selectors."""

    @staticmethod
    def _make_ad_with_options(base_ad_config:dict[str, Any], options:list[str]) -> Ad:
        return Ad.model_validate(
            base_ad_config
            | {
                "shipping_type": "SHIPPING",
                "shipping_options": options,
            }
        )

    @staticmethod
    def _mock_checkbox(checked:bool = False) -> MagicMock:
        """Create a mock checkbox element with optional checked attribute."""
        el = MagicMock()
        if checked:
            el.attrs = {"checked": ""}
        else:
            el.attrs = {}
        return el

    @pytest.mark.parametrize(
        "case",
        [
            # SMALL pre-checked, only unwanted carriers are toggled
            {
                "options": ["Hermes_Päckchen"],
                "radio_checked": True,
                "expected_radio_click": False,
                "expected_clicked_carriers": ["HERMES_002", "DHL_001"],
                "expected_not_clicked_carriers": ["HERMES_001"],
            },
            # LARGE not checked, radio click needed and only unwanted carriers are toggled
            {
                "options": ["DHL_10"],
                "radio_checked": False,
                "expected_radio_click": True,
                "expected_clicked_carriers": ["HERMES_004", "DHL_004", "DHL_005"],
                "expected_not_clicked_carriers": ["DHL_003"],
            },
        ],
    )
    @pytest.mark.asyncio
    async def test_replace_mode_handles_radio_state(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        case:dict[str, Any],
    ) -> None:
        """REPLACE mode: handles both pre-checked and unchecked radio states."""
        ad_cfg = self._make_ad_with_options(base_ad_config, case["options"])

        radio_mock = self._mock_checkbox(checked = case["radio_checked"])

        async def find_side_effect(selector_type:By, selector_value:str, **_:Any) -> MagicMock:
            if "radio" in selector_value:
                return radio_mock
            return self._mock_checkbox(checked = True)  # all checkboxes pre-checked

        with (
            patch.object(test_bot, "web_find", new_callable = AsyncMock, side_effect = find_side_effect),
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
        ):
            await getattr(test_bot, "_KleinanzeigenBot__set_shipping_options")(ad_cfg, mode = AdUpdateStrategy.REPLACE)

        click_args = [(c.args[0], c.args[1]) for c in mock_click.await_args_list if len(c.args) >= 2]

        # Radio click behavior matches expectation
        radio_clicked = any("radio" in str(a[1]) for a in click_args)
        assert radio_clicked == case["expected_radio_click"]

        # Should click Weiter and Fertig
        assert any("Weiter" in str(a[1]) for a in click_args)
        assert any("Fertig" in str(a[1]) for a in click_args)

        # Should toggle exactly the expected carriers for this scenario
        for carrier_code in case["expected_clicked_carriers"]:
            assert any(carrier_code in str(a[1]) for a in click_args)

        for carrier_code in case["expected_not_clicked_carriers"]:
            assert not any(carrier_code in str(a[1]) for a in click_args)

    @pytest.mark.asyncio
    async def test_replace_mode_dom_verified_unchecked_defaults_select_wanted_carrier(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """REPLACE mode must select wanted carriers when defaults are unchecked (DOM-verified for MEDIUM/LARGE)."""
        ad_cfg = self._make_ad_with_options(base_ad_config, ["DHL_5"])

        radio_mock = self._mock_checkbox(checked = False)  # MEDIUM radio not selected yet

        async def find_side_effect(selector_type:By, selector_value:str, **_:Any) -> MagicMock:
            if "radio" in selector_value and "MEDIUM" in selector_value:
                return radio_mock
            # DOM probe confirms MEDIUM defaults can be unchecked after "Weiter"
            if "HERMES_003" in selector_value:
                return self._mock_checkbox(checked = False)
            if "DHL_002" in selector_value:
                return self._mock_checkbox(checked = False)
            return self._mock_checkbox(checked = False)

        with (
            patch.object(test_bot, "web_find", new_callable = AsyncMock, side_effect = find_side_effect),
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
        ):
            await getattr(test_bot, "_KleinanzeigenBot__set_shipping_options")(ad_cfg, mode = AdUpdateStrategy.REPLACE)

        click_args = [(c.args[0], c.args[1]) for c in mock_click.await_args_list if len(c.args) >= 2]

        # Regression guard for issue #956: wanted DHL_002 must be selected
        assert any("DHL_002" in str(a[1]) for a in click_args)
        # Unwanted Hermes checkbox must remain untouched when already unchecked
        assert not any("HERMES_003" in str(a[1]) for a in click_args)

    @pytest.mark.asyncio
    async def test_modify_mode_toggles_carriers(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """MODIFY mode: explicitly (de-)selects each carrier based on wanted set."""
        ad_cfg = self._make_ad_with_options(base_ad_config, ["Hermes_Päckchen", "DHL_2"])

        radio_mock = self._mock_checkbox(checked = True)  # SMALL already selected

        async def find_side_effect(selector_type:By, selector_value:str, **_:Any) -> MagicMock:
            if "radio" in selector_value and "SMALL" in selector_value:
                return radio_mock
            # HERMES_001 checked, HERMES_002 checked, DHL_001 unchecked
            if "HERMES_001" in selector_value:
                return self._mock_checkbox(checked = True)
            if "HERMES_002" in selector_value:
                return self._mock_checkbox(checked = True)
            if "DHL_001" in selector_value:
                return self._mock_checkbox(checked = False)
            return self._mock_checkbox(checked = False)

        with (
            patch.object(test_bot, "web_find", new_callable = AsyncMock, side_effect = find_side_effect),
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
        ):
            await getattr(test_bot, "_KleinanzeigenBot__set_shipping_options")(ad_cfg, mode = AdUpdateStrategy.MODIFY)

        click_args = [(c.args[0], c.args[1]) for c in mock_click.await_args_list if len(c.args) >= 2]
        # HERMES_002 should be deselected (was checked, not wanted)
        assert any("HERMES_002" in str(a[1]) for a in click_args)
        # DHL_001 should be selected (was unchecked, wanted via DHL_2 → DHL_001)
        assert any("DHL_001" in str(a[1]) for a in click_args)
        # HERMES_001 should NOT be clicked (was checked, wanted)
        assert not any("HERMES_001" in str(a[1]) for a in click_args)

    @pytest.mark.asyncio
    async def test_unknown_option_raises_key_error(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """Unknown shipping option name raises KeyError with helpful message."""
        ad_cfg = self._make_ad_with_options(base_ad_config, ["NonExistent_Option"])

        with (
            patch.object(test_bot, "web_find", new_callable = AsyncMock) as mock_find,
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as mock_sleep,
            pytest.raises(KeyError, match = "Unknown shipping option"),
        ):
            await getattr(test_bot, "_KleinanzeigenBot__set_shipping_options")(ad_cfg)

        # Validation errors must occur before any DOM interaction
        mock_find.assert_not_awaited()
        mock_click.assert_not_awaited()
        mock_sleep.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mixed_size_options_raises_value_error(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """Options from different size groups raise ValueError."""
        ad_cfg = self._make_ad_with_options(base_ad_config, ["Hermes_Päckchen", "DHL_5"])

        with (
            patch.object(test_bot, "web_find", new_callable = AsyncMock) as mock_find,
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as mock_sleep,
            pytest.raises(ValueError, match = "one package size"),
        ):
            await getattr(test_bot, "_KleinanzeigenBot__set_shipping_options")(ad_cfg)

        # Validation errors must occur before any DOM interaction
        mock_find.assert_not_awaited()
        mock_click.assert_not_awaited()
        mock_sleep.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_timeout_in_dialog_raises(self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any]) -> None:
        """TimeoutError during dialog interaction is re-raised with descriptive message."""
        ad_cfg = self._make_ad_with_options(base_ad_config, ["Hermes_Päckchen"])

        with (
            patch.object(test_bot, "web_find", new_callable = AsyncMock, side_effect = TimeoutError("radio not found")),
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            pytest.raises(TimeoutError, match = "Failed to configure shipping options in dialog!"),
        ):
            await getattr(test_bot, "_KleinanzeigenBot__set_shipping_options")(ad_cfg)


class TestWantedShippingSelection:
    """Regression tests for WANTED shipping path using button-combobox dropdowns.

    WANTED ads render shipping as a special-attribute combobox dropdown
    (``<button role="combobox">``) rather than radio buttons.  These tests
    verify that the correct CSS selector lookup and ``web_select_button_combobox``
    dispatch happen during ``publish_ad``.
    """

    @contextmanager
    def _mock_publish_dependencies(
        self,
        test_bot:KleinanzeigenBot,
        mock_page:MagicMock,
    ) -> Iterator[tuple[AsyncMock, AsyncMock]]:
        """Mock all publish_ad dependencies, yielding (mock_find, mock_select_btn_combo)."""
        test_bot.page = mock_page
        test_bot.page.url = "https://www.kleinanzeigen.de/p-anzeige-aufgeben-bestaetigung.html?adId=12345"

        async def execute_side_effect(script:str) -> Any:
            if "window.location.href" in script:
                return test_bot.page.url
            return None

        with (
            patch("kleinanzeigen_bot.ainput", new_callable = AsyncMock, return_value = ""),
            patch.object(test_bot, "web_open", new_callable = AsyncMock),
            patch.object(test_bot, "_dismiss_consent_banner", new_callable = AsyncMock),
            patch.object(test_bot, "_KleinanzeigenBot__set_category", new_callable = AsyncMock),
            patch.object(test_bot, "_KleinanzeigenBot__set_special_attributes", new_callable = AsyncMock),
            patch.object(test_bot, "_KleinanzeigenBot__set_shipping", new_callable = AsyncMock),
            patch.object(test_bot, "_KleinanzeigenBot__set_contact_fields", new_callable = AsyncMock),
            patch.object(test_bot, "_KleinanzeigenBot__upload_images", new_callable = AsyncMock),
            patch.object(test_bot, "_KleinanzeigenBot__set_input_value", new_callable = AsyncMock),
            patch.object(test_bot, "check_and_wait_for_captcha", new_callable = AsyncMock),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = None),
            patch.object(test_bot, "web_find", new_callable = AsyncMock) as mock_find,
            patch.object(test_bot, "_web_find_all_once", new_callable = AsyncMock, return_value = []),
            patch.object(test_bot, "web_scroll_page_down", new_callable = AsyncMock),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, return_value = True),
            patch.object(test_bot, "web_execute", side_effect = execute_side_effect),
            patch.object(test_bot, "web_check", new_callable = AsyncMock),
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_select_button_combobox", new_callable = AsyncMock) as mock_select_btn_combo,
        ):
            yield mock_find, mock_select_btn_combo

    @staticmethod
    def _build_wanted_ad(base_ad_config:dict[str, Any], shipping_type:str) -> tuple[Ad, dict[str, Any]]:
        ad_cfg = Ad.model_validate(
            base_ad_config
            | {
                "type": "WANTED",
                "shipping_type": shipping_type,
                "shipping_options": [],
                "price_type": "NOT_APPLICABLE",
                "price": None,
            }
        )
        return ad_cfg, ad_cfg.model_dump()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("shipping_type", "expected_label"),
        [("SHIPPING", "Versand möglich"), ("PICKUP", "Nur Abholung")],
        ids = ["shipping", "pickup"],
    )
    async def test_wanted_shipping_selects_combobox_dropdown(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        mock_page:MagicMock,
        tmp_path:Path,
        shipping_type:str,
        expected_label:str,
    ) -> None:
        """WANTED ads should select shipping via button-combobox dropdown, not radios."""
        ad_cfg, ad_cfg_orig = self._build_wanted_ad(base_ad_config, shipping_type)
        ad_file = str(tmp_path / "ad.yaml")

        combobox_btn = MagicMock()
        combobox_btn.attrs = {"id": "babyausstattung.versand"}

        async def find_side_effect(selector_type:By, selector_value:str, **_:Any) -> MagicMock:
            if selector_type == By.CSS_SELECTOR and selector_value == '[role="combobox"][id$=".versand"]':
                return combobox_btn
            return MagicMock()

        with self._mock_publish_dependencies(test_bot, mock_page) as (mock_find, mock_select_btn_combo):
            mock_find.side_effect = find_side_effect
            await test_bot.publish_ad(ad_file, ad_cfg, ad_cfg_orig, [], AdUpdateStrategy.REPLACE)

        mock_select_btn_combo.assert_awaited_once_with(
            "babyausstattung.versand",
            expected_label,
        )

    @pytest.mark.asyncio
    async def test_wanted_shipping_raises_when_combobox_not_found(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        mock_page:MagicMock,
        tmp_path:Path,
    ) -> None:
        """WANTED shipping should fail when the combobox button cannot be found."""
        ad_cfg, ad_cfg_orig = self._build_wanted_ad(base_ad_config, "SHIPPING")
        ad_file = str(tmp_path / "ad.yaml")

        async def find_side_effect(selector_type:By, selector_value:str, **_:Any) -> MagicMock:
            if selector_type == By.CSS_SELECTOR and selector_value == '[role="combobox"][id$=".versand"]':
                raise TimeoutError("combobox not found in DOM")
            return MagicMock()

        with self._mock_publish_dependencies(test_bot, mock_page) as (mock_find, _):
            mock_find.side_effect = find_side_effect
            with pytest.raises(TimeoutError, match = "Failed to set shipping attribute for type 'SHIPPING'!"):
                await test_bot.publish_ad(ad_file, ad_cfg, ad_cfg_orig, [], AdUpdateStrategy.REPLACE)

    @pytest.mark.asyncio
    async def test_wanted_shipping_not_applicable_skips_combobox(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        mock_page:MagicMock,
        tmp_path:Path,
    ) -> None:
        """WANTED ads with NOT_APPLICABLE shipping should skip the combobox entirely."""
        ad_cfg = Ad.model_validate(
            base_ad_config
            | {
                "type": "WANTED",
                "shipping_type": "NOT_APPLICABLE",
                "shipping_options": [],
                "price_type": "NOT_APPLICABLE",
                "price": None,
            }
        )
        ad_cfg_orig = ad_cfg.model_dump()
        ad_file = str(tmp_path / "ad.yaml")

        with self._mock_publish_dependencies(test_bot, mock_page) as (_, mock_select_btn_combo):
            await test_bot.publish_ad(ad_file, ad_cfg, ad_cfg_orig, [], AdUpdateStrategy.REPLACE)

        mock_select_btn_combo.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_wanted_shipping_raises_when_combobox_has_no_id(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        mock_page:MagicMock,
        tmp_path:Path,
    ) -> None:
        """WANTED shipping should fail when the combobox button has no id attribute."""
        ad_cfg, ad_cfg_orig = self._build_wanted_ad(base_ad_config, "SHIPPING")
        ad_file = str(tmp_path / "ad.yaml")

        combobox_btn = MagicMock()
        combobox_btn.attrs = {}  # No "id" key

        async def find_side_effect(selector_type:By, selector_value:str, **_:Any) -> MagicMock:
            if selector_type == By.CSS_SELECTOR and selector_value == '[role="combobox"][id$=".versand"]':
                return combobox_btn
            return MagicMock()

        with self._mock_publish_dependencies(test_bot, mock_page) as (mock_find, _):
            mock_find.side_effect = find_side_effect
            with pytest.raises(TimeoutError, match = "Failed to set shipping attribute for type 'SHIPPING'!"):
                await test_bot.publish_ad(ad_file, ad_cfg, ad_cfg_orig, [], AdUpdateStrategy.REPLACE)


class TestKleinanzeigenBotPrefixSuffix:
    """Tests for description prefix and suffix functionality."""

    # pylint: disable=protected-access

    def test_description_prefix_suffix_handling(self, test_bot_config:Config, description_test_cases:list[tuple[dict[str, Any], str, str]]) -> None:
        """Test handling of description prefix/suffix in various configurations."""
        for config, raw_description, expected_description in description_test_cases:
            test_bot = KleinanzeigenBot()
            test_bot.config = test_bot_config.with_values(config)
            ad_cfg = test_bot.load_ad(
                {
                    "description": raw_description,
                    "active": True,
                    "title": "0123456789",
                    "category": "whatever",
                }
            )

            # Access private method using the correct name mangling
            description = getattr(test_bot, "_KleinanzeigenBot__get_description")(ad_cfg, with_affixes = True)
            assert description == expected_description

    def test_description_length_validation(self, test_bot_config:Config) -> None:
        """Test that long descriptions with affixes raise appropriate error."""
        test_bot = KleinanzeigenBot()
        test_bot.config = test_bot_config.with_values({"ad_defaults": {"description_prefix": "P" * 1000, "description_suffix": "S" * 1000}})
        ad_cfg = test_bot.load_ad(
            {
                "description": "D" * 2001,  # This plus affixes will exceed 4000 chars
                "active": True,
                "title": "0123456789",
                "category": "whatever",
            }
        )

        with pytest.raises(AssertionError) as exc_info:
            getattr(test_bot, "_KleinanzeigenBot__get_description")(ad_cfg, with_affixes = True)

        assert "Length of ad description including prefix and suffix exceeds 4000 chars" in str(exc_info.value)
        assert "Description length: 4001" in str(exc_info.value)


class TestKleinanzeigenBotDescriptionHandling:
    """Tests for description handling functionality."""

    def test_description_without_main_config_description(self, test_bot_config:Config) -> None:
        """Test that description works correctly when description is missing from main config."""
        test_bot = KleinanzeigenBot()
        test_bot.config = test_bot_config

        # Test with a simple ad config
        ad_cfg = test_bot.load_ad(
            {
                "description": "Test Description",
                "active": True,
                "title": "0123456789",
                "category": "whatever",
            }
        )

        # The description should be returned as-is without any prefix/suffix
        description = getattr(test_bot, "_KleinanzeigenBot__get_description")(ad_cfg, with_affixes = True)
        assert description == "Test Description"

    def test_description_with_only_new_format_affixes(self, test_bot_config:Config) -> None:
        """Test that description works with only new format affixes in config."""
        test_bot = KleinanzeigenBot()
        test_bot.config = test_bot_config.with_values({"ad_defaults": {"description_prefix": "Prefix: ", "description_suffix": " :Suffix"}})

        ad_cfg = test_bot.load_ad(
            {
                "description": "Test Description",
                "active": True,
                "title": "0123456789",
                "category": "whatever",
            }
        )

        description = getattr(test_bot, "_KleinanzeigenBot__get_description")(ad_cfg, with_affixes = True)
        assert description == "Prefix: Test Description :Suffix"

    def test_description_with_mixed_config_formats(self, test_bot_config:Config) -> None:
        """Test that description works with both old and new format affixes in config."""
        test_bot = KleinanzeigenBot()
        test_bot.config = test_bot_config.with_values(
            {
                "ad_defaults": {
                    "description_prefix": "New Prefix: ",
                    "description_suffix": " :New Suffix",
                    "description": {"prefix": "Old Prefix: ", "suffix": " :Old Suffix"},
                }
            }
        )

        ad_cfg = test_bot.load_ad(
            {
                "description": "Test Description",
                "active": True,
                "title": "0123456789",
                "category": "whatever",
            }
        )

        description = getattr(test_bot, "_KleinanzeigenBot__get_description")(ad_cfg, with_affixes = True)
        assert description == "New Prefix: Test Description :New Suffix"

    def test_description_with_ad_level_affixes(self, test_bot_config:Config) -> None:
        """Test that ad-level affixes take precedence over config affixes."""
        test_bot = KleinanzeigenBot()
        test_bot.config = test_bot_config.with_values({"ad_defaults": {"description_prefix": "Config Prefix: ", "description_suffix": " :Config Suffix"}})

        ad_cfg = test_bot.load_ad(
            {
                "description": "Test Description",
                "description_prefix": "Ad Prefix: ",
                "description_suffix": " :Ad Suffix",
                "active": True,
                "title": "0123456789",
                "category": "whatever",
            }
        )

        description = getattr(test_bot, "_KleinanzeigenBot__get_description")(ad_cfg, with_affixes = True)
        assert description == "Ad Prefix: Test Description :Ad Suffix"

    def test_description_with_none_values(self, test_bot_config:Config) -> None:
        """Test that None values in affixes are handled correctly."""
        test_bot = KleinanzeigenBot()
        test_bot.config = test_bot_config.with_values(
            {"ad_defaults": {"description_prefix": None, "description_suffix": None, "description": {"prefix": None, "suffix": None}}}
        )

        ad_cfg = test_bot.load_ad(
            {
                "description": "Test Description",
                "active": True,
                "title": "0123456789",
                "category": "whatever",
            }
        )

        description = getattr(test_bot, "_KleinanzeigenBot__get_description")(ad_cfg, with_affixes = True)
        assert description == "Test Description"

    def test_description_with_email_replacement(self, test_bot_config:Config) -> None:
        """Test that @ symbols in description are replaced with (at)."""
        test_bot = KleinanzeigenBot()
        test_bot.config = test_bot_config

        ad_cfg = test_bot.load_ad(
            {
                "description": "Contact: test@example.com",
                "active": True,
                "title": "0123456789",
                "category": "whatever",
            }
        )

        description = getattr(test_bot, "_KleinanzeigenBot__get_description")(ad_cfg, with_affixes = True)
        assert description == "Contact: test(at)example.com"


class TestKleinanzeigenBotChangedAds:
    """Tests for the 'changed' ads selector functionality."""

    def test_load_ads_with_changed_selector(self, test_bot_config:Config, base_ad_config:dict[str, Any]) -> None:
        """Test that only changed ads are loaded when using the 'changed' selector."""
        # Set up the bot with the 'changed' selector
        test_bot = KleinanzeigenBot()
        test_bot.ads_selector = "changed"
        test_bot.config = test_bot_config.with_values({"ad_defaults": {"description": {"prefix": "", "suffix": ""}}})

        # Create a changed ad
        ad_cfg = Ad.model_validate(
            base_ad_config | {"id": "12345", "title": "Changed Ad", "updated_on": "2024-01-01T00:00:00", "created_on": "2024-01-01T00:00:00", "active": True}
        )

        # Calculate hash for changed_ad and add it to the config
        # Then modify the ad to simulate a change
        changed_ad = ad_cfg.model_dump()
        changed_hash = ad_cfg.update_content_hash().content_hash
        changed_ad["content_hash"] = changed_hash
        # Now modify the ad to make it "changed"
        changed_ad["title"] = "Changed Ad - Modified"

        # Create temporary directory and file
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            ad_dir = temp_path / "ads"
            ad_dir.mkdir()

            # Write the ad file
            dicts.save_dict(ad_dir / "changed_ad.yaml", changed_ad)

            # Set config file path and use relative path for ad_files
            test_bot.config_file_path = str(temp_path / "config.yaml")
            test_bot.config.ad_files = ["ads/*.yaml"]

            # Mock the loading of the ad configuration
            with patch(
                "kleinanzeigen_bot.utils.dicts.load_dict",
                side_effect = [
                    changed_ad,  # First call returns the changed ad
                    {},  # Second call for ad_fields.yaml
                ],
            ):
                ads_to_publish = test_bot.load_ads()

                # The changed ad should be loaded
                assert len(ads_to_publish) == 1
                assert ads_to_publish[0][1].title == "Changed Ad - Modified"

    def test_load_ads_with_due_selector_includes_all_due_ads(self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any]) -> None:
        """Test that 'due' selector includes all ads that are due for republication, regardless of changes."""
        # Set up the bot with the 'due' selector
        test_bot.ads_selector = "due"

        # Create a changed ad that is also due for republication
        current_time = misc.now()
        old_date = (current_time - timedelta(days = 10)).isoformat()  # Past republication interval

        ad_cfg = Ad.model_validate(
            base_ad_config
            | {
                "id": "12345",
                "title": "Changed Ad",
                "updated_on": old_date,
                "created_on": old_date,
                "republication_interval": 7,  # Due for republication after 7 days
                "active": True,
            }
        )
        changed_ad = ad_cfg.model_dump()

        # Create temporary directory and file
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            ad_dir = temp_path / "ads"
            ad_dir.mkdir()

            # Write the ad file
            dicts.save_dict(ad_dir / "changed_ad.yaml", changed_ad)

            # Set config file path and use relative path for ad_files
            test_bot.config_file_path = str(temp_path / "config.yaml")
            test_bot.config.ad_files = ["ads/*.yaml"]

            # Mock the loading of the ad configuration
            with patch(
                "kleinanzeigen_bot.utils.dicts.load_dict",
                side_effect = [
                    changed_ad,  # First call returns the changed ad
                    {},  # Second call for ad_fields.yaml
                ],
            ):
                ads_to_publish = test_bot.load_ads()

                # The changed ad should be loaded with 'due' selector because it's due for republication
                assert len(ads_to_publish) == 1


def test_file_logger_writes_message(tmp_path:Path, caplog:pytest.LogCaptureFixture) -> None:
    """
    Unit: Logger can be initialized and used, robust to pytest log capture.
    """
    log_path = tmp_path / "logger_test.log"
    logger_name = "logger_test_logger_unique"
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    for h in list(logger.handlers):
        logger.removeHandler(h)
    handle = logging.FileHandler(str(log_path), encoding = "utf-8")
    handle.setLevel(logging.DEBUG)
    formatter = logging.Formatter("%(levelname)s:%(name)s:%(message)s")
    handle.setFormatter(formatter)
    logger.addHandler(handle)
    logger.info("Logger test log message")
    handle.flush()
    handle.close()
    logger.removeHandler(handle)
    assert log_path.exists()
    with open(log_path, "r", encoding = "utf-8") as f:
        contents = f.read()
    assert "Logger test log message" in contents


def _apply_price_reduction_persistence(count:int | None) -> dict[str, Any]:
    """Return a dict with price_reduction_count only when count is positive (count -> dict[str, Any])."""
    ad_cfg_orig:dict[str, Any] = {}
    if count is not None and count > 0:
        ad_cfg_orig["price_reduction_count"] = count
    return ad_cfg_orig


class TestPriceReductionPersistence:
    """Tests for price_reduction_count persistence logic."""

    @pytest.mark.unit
    def test_persistence_logic_saves_when_count_positive(self) -> None:
        """Test the conditional logic that decides whether to persist price_reduction_count."""
        # Simulate the logic from publish_ad lines 1076-1079
        # Test case 1: price_reduction_count = 3 (should persist)
        ad_cfg_orig = _apply_price_reduction_persistence(3)

        assert "price_reduction_count" in ad_cfg_orig
        assert ad_cfg_orig["price_reduction_count"] == 3

    @pytest.mark.unit
    def test_persistence_logic_skips_when_count_zero(self) -> None:
        """Test that price_reduction_count == 0 does not get persisted."""
        # Test case 2: price_reduction_count = 0 (should NOT persist)
        ad_cfg_orig = _apply_price_reduction_persistence(0)

        assert "price_reduction_count" not in ad_cfg_orig

    @pytest.mark.unit
    def test_persistence_logic_skips_when_count_none(self) -> None:
        """Test that price_reduction_count == None does not get persisted."""
        # Test case 3: price_reduction_count = None (should NOT persist)
        ad_cfg_orig = _apply_price_reduction_persistence(None)

        assert "price_reduction_count" not in ad_cfg_orig


class TestBuyNowRadioTimeout:
    """Regression tests for buy-now radio button handling with PICKUP shipping."""

    @contextmanager
    def _mock_publish_ad_dependencies(
        self,
        test_bot:KleinanzeigenBot,
        mock_page:MagicMock,
        probe_side_effect:Callable[[By, str], Awaitable[Element | None]],
        check_side_effect:Callable[[By, str, Any, Any], Awaitable[bool]],
    ) -> Iterator[tuple[MagicMock, MagicMock, MagicMock]]:
        """Context manager that mocks all publish_ad dependencies for buy-now radio tests.

        Args:
            test_bot: The bot instance to patch methods on.
            mock_page: Mock page object to assign to test_bot.page.
            probe_side_effect: Async function defining web_probe behavior.
            check_side_effect: Async function defining web_check behavior for ad-buy-now-false.

        Yields:
            Tuple of (mock_probe, mock_check, mock_click) for assertions in the test.
        """
        test_bot.page = mock_page
        test_bot.page.url = "https://www.kleinanzeigen.de/p-anzeige-aufgeben-bestaetigung.html?adId=12345"

        async def execute_side_effect(script:str) -> Any:
            if "window.location.href" in script:
                return test_bot.page.url
            return None

        with (
            patch.object(test_bot, "web_open", new_callable = AsyncMock),
            patch.object(test_bot, "_dismiss_consent_banner", new_callable = AsyncMock),
            patch.object(test_bot, "_KleinanzeigenBot__set_category", new_callable = AsyncMock),
            patch.object(test_bot, "_KleinanzeigenBot__set_special_attributes", new_callable = AsyncMock),
            patch.object(test_bot, "_KleinanzeigenBot__set_shipping", new_callable = AsyncMock),
            patch.object(test_bot, "web_input", new_callable = AsyncMock),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = probe_side_effect) as mock_probe,
            patch.object(test_bot, "web_check", new_callable = AsyncMock, side_effect = check_side_effect) as mock_check,
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            patch.object(test_bot, "web_execute", side_effect = execute_side_effect),
            patch.object(test_bot, "web_scroll_page_down", new_callable = AsyncMock),
            patch.object(test_bot, "_KleinanzeigenBot__set_contact_fields", new_callable = AsyncMock),
            patch.object(test_bot, "web_find", new_callable = AsyncMock),
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, return_value = []),
            patch.object(test_bot, "_web_find_all_once", new_callable = AsyncMock, return_value = []),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, return_value = True),
            patch.object(test_bot, "check_and_wait_for_captcha", new_callable = AsyncMock),
        ):
            yield mock_probe, mock_check, mock_click

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("scenario", "expected_click"),
        [
            ("radio_absent_swallowed", False),
            ("radio_visible_needs_click", True),
            ("radio_already_selected", False),
        ],
    )
    async def test_buy_now_radio_behavior_for_pickup(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        mock_page:MagicMock,
        tmp_path:Path,
        scenario:str,
        expected_click:bool,
    ) -> None:
        """Buy-now radio handling for PICKUP: skips when absent, clicks when needed."""
        ad_cfg = Ad.model_validate(base_ad_config | {"shipping_type": "PICKUP", "price_type": "FIXED", "price": 100})
        ad_cfg_orig = copy.deepcopy(base_ad_config)
        ad_file = str(tmp_path / "ad.yaml")

        buy_now_elem = MagicMock()

        async def probe_side_effect(selector_type:By, selector_value:str, **_:Any) -> Element | None:
            if selector_type == By.ID and selector_value == "ad-buy-now-false":
                if scenario == "radio_absent_swallowed":
                    return None
                return buy_now_elem
            return None

        async def check_side_effect(selector_type:By, selector_value:str, *_:Any, **__:Any) -> bool:
            if selector_type == By.ID and selector_value == "ad-buy-now-false":
                return scenario == "radio_already_selected"
            return False

        with self._mock_publish_ad_dependencies(test_bot, mock_page, probe_side_effect, check_side_effect) as (_, _, mock_click):
            await test_bot.publish_ad(ad_file, ad_cfg, ad_cfg_orig, [], AdUpdateStrategy.REPLACE)

        buy_now_clicks = [c for c in mock_click.call_args_list if len(c.args) >= 2 and c.args[0] == By.ID and c.args[1] == "ad-buy-now-false"]
        if expected_click:
            assert buy_now_clicks, "web_click should be called for ad-buy-now-false when visible but not selected"
        else:
            assert not buy_now_clicks, "web_click should not be called for ad-buy-now-false"

    @pytest.mark.asyncio
    async def test_buy_now_true_required_for_shipping_sell_directly(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        mock_page:MagicMock,
        tmp_path:Path,
    ) -> None:
        """Shipping ads with sell_directly enabled should fail if buy-now-true control is unavailable."""
        ad_cfg = Ad.model_validate(base_ad_config | {"shipping_type": "SHIPPING", "sell_directly": True, "price_type": "FIXED", "price": 100})
        ad_cfg_orig = copy.deepcopy(base_ad_config)
        ad_file = str(tmp_path / "ad.yaml")

        async def probe_side_effect(selector_type:By, selector_value:str, **_:Any) -> Element | None:
            if selector_type == By.ID and selector_value == "ad-buy-now-true":
                return None
            return None

        async def check_side_effect(*_:Any, **__:Any) -> bool:
            return False

        with (
            pytest.raises(TimeoutError, match = "Failed to enable direct-buy option"),
            self._mock_publish_ad_dependencies(test_bot, mock_page, probe_side_effect, check_side_effect),
        ):
            await test_bot.publish_ad(ad_file, ad_cfg, ad_cfg_orig, [], AdUpdateStrategy.REPLACE)


class TestImageUploadProcessedMarkerFallback:
    """Regression tests for image upload completion detection via hidden marker inputs."""

    @staticmethod
    def _build_two_image_ad(base_ad_config:dict[str, Any], tmp_path:Path) -> tuple[Ad, str, str]:
        image_a = tmp_path / "img_a.jpg"
        image_b = tmp_path / "img_b.jpg"
        image_a.write_bytes(b"")
        image_b.write_bytes(b"")
        ad_cfg = Ad.model_validate(base_ad_config | {"images": [str(image_a), str(image_b)]})
        return ad_cfg, str(image_a), str(image_b)

    @staticmethod
    def _build_marker(url:str) -> MagicMock:
        marker = MagicMock()
        marker.attrs.value = url
        return marker

    @staticmethod
    @contextmanager
    def _mock_upload_dependencies(
        test_bot:KleinanzeigenBot,
        file_input:MagicMock,
        find_all_side_effect:Callable[..., Awaitable[list[MagicMock]]],
        await_side_effect:Callable[..., Awaitable[Any]],
    ) -> Iterator[None]:
        async def find_all_once_side_effect(selector_type:By, selector_value:str, *_:Any, **__:Any) -> list[MagicMock]:
            return await find_all_side_effect(selector_type, selector_value, **__)

        with (
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = file_input),
            patch.object(test_bot, "_web_find_all_once", new_callable = AsyncMock, side_effect = find_all_once_side_effect),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = await_side_effect),
        ):
            yield

    @pytest.mark.asyncio
    async def test_upload_images_succeeds_with_hidden_markers_when_thumbnails_absent(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        tmp_path:Path,
    ) -> None:
        """Hidden adImages markers should satisfy completion when thumbnail list is missing."""
        ad_cfg, image_a, image_b = self._build_two_image_ad(base_ad_config, tmp_path)

        file_input = MagicMock()
        file_input.send_file = AsyncMock()

        marker_a = self._build_marker("https://img.example/a.jpg")
        marker_b = self._build_marker("https://img.example/b.jpg")
        marker_query_count = 0

        async def find_all_side_effect(selector_type:By, selector_value:str, *_:Any, **__:Any) -> list[MagicMock]:
            nonlocal marker_query_count
            if selector_type == By.CSS_SELECTOR and selector_value == "input[name^='adImages'][name$='.url']":
                marker_query_count += 1
                if marker_query_count == 1:
                    return []  # baseline before upload
                return [marker_a, marker_b]
            return []

        async def await_side_effect(condition:Callable[[], Awaitable[bool]], **_:Any) -> bool:
            if await condition():
                return True
            raise TimeoutError("condition did not pass")

        with self._mock_upload_dependencies(test_bot, file_input, find_all_side_effect, await_side_effect):
            await getattr(test_bot, "_KleinanzeigenBot__upload_images")(ad_cfg)

        file_input.send_file.assert_any_await(image_a)
        file_input.send_file.assert_any_await(image_b)

    @pytest.mark.asyncio
    async def test_upload_images_refetches_file_input_per_image_to_avoid_stale_element(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        tmp_path:Path,
    ) -> None:
        """Each image upload should re-fetch the file input because the DOM replaces it after selection."""
        ad_cfg, image_a, image_b = self._build_two_image_ad(base_ad_config, tmp_path)

        first_file_input = MagicMock()
        first_file_input.send_file = AsyncMock()
        second_file_input = MagicMock()
        second_file_input.send_file = AsyncMock()

        marker_a = self._build_marker("https://img.example/a.jpg")
        marker_b = self._build_marker("https://img.example/b.jpg")
        marker_query_count = 0

        async def find_side_effect(selector_type:By, selector_value:str, **_:Any) -> MagicMock:
            assert selector_type == By.CSS_SELECTOR
            assert selector_value == "input[type=file]"
            if first_file_input.send_file.await_count == 0:
                return first_file_input
            return second_file_input

        async def find_all_side_effect(selector_type:By, selector_value:str, *_:Any, **__:Any) -> list[MagicMock]:
            nonlocal marker_query_count
            if selector_type == By.CSS_SELECTOR and selector_value == "input[name^='adImages'][name$='.url']":
                marker_query_count += 1
                if marker_query_count == 1:
                    return []  # baseline before upload
                return [marker_a, marker_b]
            return []

        async def await_side_effect(condition:Callable[[], Awaitable[bool]], **_:Any) -> bool:
            if await condition():
                return True
            raise TimeoutError("condition did not pass")

        with (
            patch.object(test_bot, "web_find", new_callable = AsyncMock, side_effect = find_side_effect) as mock_find,
            patch.object(test_bot, "_web_find_all_once", new_callable = AsyncMock, side_effect = find_all_side_effect),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = await_side_effect),
        ):
            await getattr(test_bot, "_KleinanzeigenBot__upload_images")(ad_cfg)

        first_file_input.send_file.assert_awaited_once_with(image_a)
        second_file_input.send_file.assert_awaited_once_with(image_b)
        assert mock_find.await_count >= 2

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("baseline_count", "post_count", "expected_found"),
        [
            pytest.param(2, 2, 0, id = "stale-only-markers"),
            pytest.param(0, 1, 1, id = "one-new-marker"),
        ],
    )
    async def test_upload_images_timeout_reports_processed_count(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        tmp_path:Path,
        baseline_count:int,
        post_count:int,
        expected_found:int,
    ) -> None:
        """Upload timeout should report the correct processed-marker count based on baseline vs post-upload markers."""
        ad_cfg, image_a, image_b = self._build_two_image_ad(base_ad_config, tmp_path)

        file_input = MagicMock()
        file_input.send_file = AsyncMock()

        marker_query_count = 0

        async def find_all_side_effect(selector_type:By, selector_value:str, **_:Any) -> list[MagicMock]:
            nonlocal marker_query_count
            if selector_type == By.CSS_SELECTOR and selector_value == "input[name^='adImages'][name$='.url']":
                marker_query_count += 1
                if marker_query_count == 1:
                    return [self._build_marker(f"https://img.example/baseline-{i}.jpg") for i in range(baseline_count)]
                return [self._build_marker(f"https://img.example/post-{i}.jpg") for i in range(post_count)]
            return []

        async def await_timeout(*_:Any, **__:Any) -> None:
            raise TimeoutError("Image upload timeout exceeded")

        with (
            pytest.raises(TimeoutError, match = rf"Expected 2, found {expected_found} processed"),
            self._mock_upload_dependencies(test_bot, file_input, find_all_side_effect, await_timeout),
        ):
            await getattr(test_bot, "_KleinanzeigenBot__upload_images")(ad_cfg)

        file_input.send_file.assert_any_await(image_a)
        file_input.send_file.assert_any_await(image_b)

    @pytest.mark.asyncio
    async def test_upload_images_succeeds_when_new_markers_exceed_baseline(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        tmp_path:Path,
    ) -> None:
        """Only marker delta beyond baseline should satisfy completion when thumbnails are absent."""
        ad_cfg, image_a, image_b = self._build_two_image_ad(base_ad_config, tmp_path)

        file_input = MagicMock()
        file_input.send_file = AsyncMock()

        stale_marker = self._build_marker("https://img.example/stale.jpg")
        marker_a = self._build_marker("https://img.example/a.jpg")
        marker_b = self._build_marker("https://img.example/b.jpg")
        marker_query_count = 0

        async def find_all_side_effect(selector_type:By, selector_value:str, **_:Any) -> list[MagicMock]:
            nonlocal marker_query_count
            if selector_type == By.CSS_SELECTOR and selector_value == "input[name^='adImages'][name$='.url']":
                marker_query_count += 1
                if marker_query_count == 1:
                    return [stale_marker]  # baseline before upload
                return [stale_marker, marker_a, marker_b]  # 2 new markers beyond baseline
            return []

        async def await_side_effect(condition:Callable[[], Awaitable[bool]], **_:Any) -> bool:
            if await condition():
                return True
            raise TimeoutError("condition did not pass")

        with self._mock_upload_dependencies(test_bot, file_input, find_all_side_effect, await_side_effect):
            await getattr(test_bot, "_KleinanzeigenBot__upload_images")(ad_cfg)

        file_input.send_file.assert_any_await(image_a)
        file_input.send_file.assert_any_await(image_b)

    @pytest.mark.asyncio
    async def test_upload_images_baseline_capture_timeout_defaults_to_zero(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        tmp_path:Path,
    ) -> None:
        """If baseline marker lookup times out, marker fallback should still work with baseline=0."""
        ad_cfg, image_a, image_b = self._build_two_image_ad(base_ad_config, tmp_path)

        file_input = MagicMock()
        file_input.send_file = AsyncMock()

        marker_a = self._build_marker("https://img.example/a.jpg")
        marker_b = self._build_marker("https://img.example/b.jpg")
        marker_query_count = 0

        async def find_all_side_effect(selector_type:By, selector_value:str, **_:Any) -> list[MagicMock]:
            nonlocal marker_query_count
            if selector_type == By.CSS_SELECTOR and selector_value == "input[name^='adImages'][name$='.url']":
                marker_query_count += 1
                if marker_query_count == 1:
                    raise TimeoutError("baseline markers unavailable")
                return [marker_a, marker_b]
            return []

        async def await_side_effect(condition:Callable[[], Awaitable[bool]], **_:Any) -> bool:
            if await condition():
                return True
            raise TimeoutError("condition did not pass")

        with self._mock_upload_dependencies(test_bot, file_input, find_all_side_effect, await_side_effect):
            await getattr(test_bot, "_KleinanzeigenBot__upload_images")(ad_cfg)

        file_input.send_file.assert_any_await(image_a)
        file_input.send_file.assert_any_await(image_b)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("baseline_count", "post_count"),
        [
            pytest.param(0, 2, id = "no_baseline"),
            pytest.param(1, 3, id = "one_stale_plus_two_new"),
            pytest.param(2, 4, id = "two_stale_plus_two_new"),
        ],
    )
    async def test_upload_images_marker_delta_determines_completion(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        tmp_path:Path,
        baseline_count:int,
        post_count:int,
    ) -> None:
        """Completion should succeed when marker delta reaches expected count."""
        ad_cfg, image_a, image_b = self._build_two_image_ad(base_ad_config, tmp_path)

        file_input = MagicMock()
        file_input.send_file = AsyncMock()

        marker_query_count = 0

        async def find_all_side_effect(selector_type:By, selector_value:str, **_:Any) -> list[MagicMock]:
            nonlocal marker_query_count
            if selector_type == By.CSS_SELECTOR and selector_value == "input[name^='adImages'][name$='.url']":
                marker_query_count += 1
                if marker_query_count == 1:
                    return [self._build_marker(f"https://img.example/stale-{i}.jpg") for i in range(baseline_count)]
                return [self._build_marker(f"https://img.example/post-{i}.jpg") for i in range(post_count)]
            return []

        async def await_side_effect(condition:Callable[[], Awaitable[bool]], **_:Any) -> bool:
            if await condition():
                return True
            raise TimeoutError("condition did not pass")

        with self._mock_upload_dependencies(test_bot, file_input, find_all_side_effect, await_side_effect):
            await getattr(test_bot, "_KleinanzeigenBot__upload_images")(ad_cfg)

        file_input.send_file.assert_any_await(image_a)
        file_input.send_file.assert_any_await(image_b)


class TestImageCleanupInPublishAd:
    """Regression tests for image cleanup loop in publish_ad."""

    @pytest.mark.asyncio
    async def test_existing_images_removed_before_upload(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        mock_page:MagicMock,
        tmp_path:Path,
    ) -> None:
        """Cleanup should probe and click remove buttons before upload."""
        test_bot.page = mock_page
        test_bot.page.url = "https://www.kleinanzeigen.de/p-anzeige-aufgeben-bestaetigung.html?adId=12345"

        image_path = tmp_path / "img.jpg"
        ad_cfg = Ad.model_validate(base_ad_config | {"images": [str(image_path)]})
        ad_cfg = ad_cfg.model_copy(update = {"id": 12345})
        image_path.write_bytes(b"\xff\xd8\xff")
        ad_cfg_orig = ad_cfg.model_dump()
        ad_file = str(tmp_path / "ad.yaml")

        probe_call_count = 0
        remove_buttons:list[MagicMock] = []
        event_log:list[str] = []

        async def probe_side_effect(selector_type:By, selector_value:str, **_:Any) -> Element | None:
            nonlocal probe_call_count
            if selector_type == By.CSS_SELECTOR and selector_value == "button[aria-label='Bild entfernen']":
                probe_call_count += 1
                if probe_call_count <= 3:
                    remove_btn = MagicMock()
                    remove_btn.click = AsyncMock(side_effect = lambda idx = probe_call_count: event_log.append(f"remove-{idx}"))
                    remove_buttons.append(remove_btn)
                    return remove_btn
                return None
            return None

        async def find_all_side_effect(selector_type:By, selector_value:str, *_:Any, **__:Any) -> list[MagicMock]:
            if selector_type == By.CSS_SELECTOR and selector_value == "input[name^='adImages'][name$='.url']":
                markers:list[MagicMock] = []
                for index in range(3):
                    marker = MagicMock()
                    marker.attrs.value = f"https://img.example/{index}.jpg"
                    markers.append(marker)
                return markers
            return []

        async def execute_side_effect(script:str) -> Any:
            if "window.location.href" in script:
                return test_bot.page.url
            return None

        async def upload_side_effect(*_:Any, **__:Any) -> None:
            event_log.append("upload")

        with (
            patch.object(test_bot, "web_open", new_callable = AsyncMock),
            patch.object(test_bot, "_dismiss_consent_banner", new_callable = AsyncMock),
            patch.object(test_bot, "_KleinanzeigenBot__set_category", new_callable = AsyncMock),
            patch.object(test_bot, "_KleinanzeigenBot__set_special_attributes", new_callable = AsyncMock),
            patch.object(test_bot, "_KleinanzeigenBot__set_shipping", new_callable = AsyncMock),
            patch.object(test_bot, "web_input", new_callable = AsyncMock),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = probe_side_effect),
            patch.object(test_bot, "web_check", new_callable = AsyncMock, return_value = False),
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_execute", side_effect = execute_side_effect),
            patch.object(test_bot, "web_scroll_page_down", new_callable = AsyncMock),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch.object(test_bot, "_KleinanzeigenBot__set_contact_fields", new_callable = AsyncMock),
            patch.object(test_bot, "_KleinanzeigenBot__upload_images", new_callable = AsyncMock, side_effect = upload_side_effect) as mock_upload,
            patch.object(test_bot, "check_and_wait_for_captcha", new_callable = AsyncMock),
            patch.object(test_bot, "web_find", new_callable = AsyncMock),
            patch.object(test_bot, "_web_find_all_once", new_callable = AsyncMock, side_effect = find_all_side_effect),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, return_value = True),
        ):
            await test_bot.publish_ad(ad_file, ad_cfg, ad_cfg_orig, [], AdUpdateStrategy.MODIFY)

        assert sum(button.click.await_count for button in remove_buttons) == 3
        mock_upload.assert_awaited_once()
        assert event_log == ["remove-1", "remove-2", "remove-3", "upload"]


class TestTrackingFallback:
    """Tests for _try_recover_ad_id_from_redirect helper method."""

    @pytest.mark.asyncio
    async def test_extract_ad_id_from_referrer(self, test_bot:KleinanzeigenBot) -> None:
        """Ad ID should be extracted from document.referrer containing the confirmation URL."""
        referrer_url = "https://www.kleinanzeigen.de/p-anzeige-aufgeben-bestaetigung.html?adId=3382410263"
        with patch.object(test_bot, "web_execute", new_callable = AsyncMock, return_value = referrer_url):
            result = await test_bot._try_recover_ad_id_from_redirect()

        assert result == 3382410263

    @pytest.mark.asyncio
    async def test_extract_ad_id_from_script_content(self, test_bot:KleinanzeigenBot) -> None:
        """When referrer has no confirmation URL, ad ID should be extracted from inline script content."""
        referrer = "https://www.kleinanzeigen.de/m-meine-anzeigen.html"
        script_content = (
            'Belen.Tracking.initTrackingData({"page":"p-anzeige-aufgeben-bestaetigung.html?adId=44556677"});'
        )
        execute_returns = [referrer, script_content]

        with patch.object(test_bot, "web_execute", new_callable = AsyncMock, side_effect = execute_returns):
            result = await test_bot._try_recover_ad_id_from_redirect()

        assert result == 44556677

    @pytest.mark.asyncio
    async def test_extract_ad_id_returns_none_when_not_found(self, test_bot:KleinanzeigenBot) -> None:
        """When neither referrer nor scripts contain a confirmation URL, None should be returned."""
        execute_returns = [
            "https://www.kleinanzeigen.de/m-meine-anzeigen.html",  # referrer
            "var x = 42;",  # script content — no confirmation URL
        ]

        with patch.object(test_bot, "web_execute", new_callable = AsyncMock, side_effect = execute_returns):
            result = await test_bot._try_recover_ad_id_from_redirect()

        assert result is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("referrer_value", ["", None], ids = ["empty-referrer", "none-referrer"])
    async def test_extract_ad_id_falls_back_to_script_when_referrer_lacks_confirmation_url(
        self, test_bot:KleinanzeigenBot, referrer_value:str | None,
    ) -> None:
        """When document.referrer is empty or None, the script scan fallback should extract the ad ID."""
        script_content = 'initTrackingData("p-anzeige-aufgeben-bestaetigung.html?adId=11223344")'
        execute_returns = [referrer_value, script_content]

        with patch.object(test_bot, "web_execute", new_callable = AsyncMock, side_effect = execute_returns):
            result = await test_bot._try_recover_ad_id_from_redirect()

        assert result == 11223344
