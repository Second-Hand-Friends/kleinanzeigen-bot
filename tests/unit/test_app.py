# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import os
from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from kleinanzeigen_bot._version import __version__
from ruamel.yaml import YAML as _YAML

from kleinanzeigen_bot.app import KleinanzeigenBot
from kleinanzeigen_bot.model.config_model import Config
from kleinanzeigen_bot.runtime_config import RuntimeState
from kleinanzeigen_bot.utils import xdg_paths


@pytest.fixture
def mock_config_setup(test_bot:KleinanzeigenBot, tmp_path:Path) -> Generator[None, None, None]:
    """Provide a centralized mock configuration setup for tests.
    This fixture mocks load_config and other essential configuration-related methods."""
    test_bot.config_file_path = str(tmp_path / "config.yaml")
    workspace = xdg_paths.Workspace.for_config(tmp_path / "config.yaml", "kleinanzeigen-bot")
    with (
        patch("kleinanzeigen_bot.runtime_config.resolve_workspace", return_value = workspace),
        patch(
            "kleinanzeigen_bot.runtime_config.load_config",
            return_value = RuntimeState(config = test_bot.config, categories = {}, timing_collector = None),
        ),
        patch("kleinanzeigen_bot.runtime_config.apply_browser_config"),
        patch("kleinanzeigen_bot.runtime_config.configure_file_logging", return_value = None),
        patch.object(test_bot, "create_browser_session", new_callable = AsyncMock),
        patch.object(test_bot, "login", new_callable = AsyncMock),
        patch.object(test_bot, "web_request", new_callable = AsyncMock) as mock_request,
    ):
        # Mock the web request for published ads
        mock_request.return_value = {"content": '{"ads": []}'}
        yield


class TestKleinanzeigenBotInitialization:
    """Tests for run-path initialization and download plumbing."""
    @pytest.mark.asyncio
    @pytest.mark.parametrize("command", ["verify", "update-check", "update-content-hash", "publish", "delete", "extend", "download"])
    async def test_run_uses_workspace_state_file_for_update_checker(self, test_bot:KleinanzeigenBot, command:str, tmp_path:Path) -> None:
        """Ensure UpdateChecker is initialized with the workspace state file."""
        update_checker_calls:list[tuple[Config, Path]] = []
        workspace = xdg_paths.Workspace.for_config(tmp_path / "config.yaml", "kleinanzeigen-bot")

        class DummyUpdateChecker:
            def __init__(self, config:Config, state_file:Path) -> None:
                update_checker_calls.append((config, state_file))

            def check_for_updates(self, *_args:Any, **_kwargs:Any) -> None:
                return None

        with (
            patch("kleinanzeigen_bot.runtime_config.resolve_workspace", return_value = workspace),
            patch(
                "kleinanzeigen_bot.runtime_config.load_config",
                return_value = RuntimeState(config = test_bot.config, categories = {}, timing_collector = None),
            ),
            patch("kleinanzeigen_bot.runtime_config.configure_file_logging", return_value = None),
            patch("kleinanzeigen_bot.runtime_config.apply_browser_config"),
            patch.object(test_bot, "load_ads", return_value = []),
            patch.object(test_bot, "create_browser_session", new_callable = AsyncMock),
            patch.object(test_bot, "login", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.download_flow.download_ads", new_callable = AsyncMock),


            patch.object(test_bot, "close_browser_session"),
            patch("kleinanzeigen_bot.update_checker.UpdateChecker", DummyUpdateChecker),
        ):
            await test_bot.run(["app", command])

        expected_state_path = (tmp_path / "config.yaml").resolve().parent / ".temp" / "update_check_state.json"
        assert update_checker_calls == [(test_bot.config, expected_state_path)]


class TestKleinanzeigenBotBasics:
    """Basic tests for KleinanzeigenBot."""

    def test_get_version(self, test_bot:KleinanzeigenBot) -> None:
        """Test version retrieval."""
        assert test_bot.get_version() == __version__

    def test_get_config_file_path(self, test_bot:KleinanzeigenBot, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        """Test config file path handling."""
        monkeypatch.chdir(tmp_path)
        test_bot.config_file_path = os.path.abspath("config.yaml")
        default_path = os.path.abspath("config.yaml")
        assert test_bot.config_file_path == default_path
        test_path = os.path.abspath("custom_config.yaml")
        test_bot.config_file_path = test_path
        assert test_bot.config_file_path == test_path

    def test_get_log_file_path(self, test_bot:KleinanzeigenBot, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        """Test log file path handling."""
        monkeypatch.chdir(tmp_path)
        test_bot.log_file_path = os.path.abspath("kleinanzeigen_bot.log")
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
        with patch("kleinanzeigen_bot.update_checker.UpdateChecker.check_for_updates"):
            await test_bot.run(["script.py", "verify", "--config", str(config_path), "--workspace-mode", "portable"])
        assert test_bot.config.login.username == "test"

    @pytest.mark.asyncio
    async def test_run_update_does_not_instantiate_update_checker(  # pylint: disable=unused-argument
        self, test_bot:KleinanzeigenBot, mock_config_setup:None,
    ) -> None:
        """Verify update command does NOT instantiate or run UpdateChecker."""
        update_checker_calls:list[tuple[object, object]] = []

        class _DummyUpdateChecker:
            def __init__(self, config:object, state_file:object) -> None:
                update_checker_calls.append((config, state_file))

            def check_for_updates(self, *args:object, **kwargs:object) -> None:
                pass

        with (
            patch.object(test_bot, "load_ads", return_value = []),
            patch("kleinanzeigen_bot.update_checker.UpdateChecker", _DummyUpdateChecker),
        ):
            await test_bot.run(["script.py", "update"])

        assert update_checker_calls == [], "UpdateChecker should not be instantiated for the update command"

    @pytest.mark.asyncio
    async def test_run_invalid_download_selector_skips_bootstrap(self, test_bot:KleinanzeigenBot, tmp_path:Path) -> None:
        """Verify download with invalid explicit --ads exits before bootstrap/config loading."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "login:\n  username: test\n  password: test"
            "\nad_defaults:\n  contact:\n    name: T\n    zipcode: '12345'"
            "\npublishing:\n  delete_old_ads: BEFORE_PUBLISH\n  delete_old_ads_by_title: false\n",
            encoding = "utf-8",
        )
        workspace = xdg_paths.Workspace.for_config(config_path, "kleinanzeigen-bot")

        with (
            patch("kleinanzeigen_bot.runtime_config.resolve_workspace", return_value = workspace),
            patch("kleinanzeigen_bot.runtime_config.load_config") as mock_load_config,
        ):
            with pytest.raises(SystemExit) as exc_info:
                await test_bot.run(["script.py", "download", "--ads=invalid"])
            assert exc_info.value.code == 2
            mock_load_config.assert_not_called()


# ---------------------------------------------------------------------------
# Translation integrity
# ---------------------------------------------------------------------------


class TestAppTranslations:
    """Verify German translation keys exist for extracted app.py handler methods."""

    def _load_translations(self) -> dict[str, object]:
        trans_path = Path(__file__).resolve().parent.parent.parent / "src" / "kleinanzeigen_bot" / "resources" / "translations.de.yaml"
        with open(trans_path, encoding = "utf-8") as f:
            trans:dict[str, object] = _YAML(typ = "safe").load(f)
        return trans

    def test_handler_translation_sections_present(self) -> None:
        """All handler methods with LOG calls have corresponding translation sections."""
        trans = self._load_translations()
        app_section = trans.get("kleinanzeigen_bot/app.py", {})
        assert isinstance(app_section, dict)

        # Every handler that emits LOG.* calls must have a translation section
        expected_sections:set[str] = {
            "_handle_verify",
            "_handle_update_content_hash",
            "_handle_publish",
            "_handle_update",
            "_handle_delete",
            "_handle_extend",
            "_handle_download",
        }
        actual_sections = {k for k in app_section if isinstance(app_section[k], dict)}
        for section in expected_sections:
            assert section in actual_sections, f"Missing translation section for {section}"

    def test_key_handler_messages_are_translated(self) -> None:
        """Spot-check that critical messages moved to handlers are still translated."""
        trans = self._load_translations()
        app_section = trans.get("kleinanzeigen_bot/app.py", {})
        assert isinstance(app_section, dict)

        # Verify messages in their new handler homes
        verify_section = app_section.get("_handle_verify", {})
        assert isinstance(verify_section, dict)
        assert verify_section["DONE: No configuration errors found."] == "FERTIG: Keine Konfigurationsfehler gefunden."

        update_section = app_section.get("_handle_update", {})
        assert isinstance(update_section, dict)
        assert update_section["DONE: No changed ads found."] == "FERTIG: Keine geänderten Anzeigen gefunden."

        extend_section = app_section.get("_handle_extend", {})
        assert isinstance(extend_section, dict)
        assert extend_section["DONE: No ads found to extend."] == "FERTIG: Keine Anzeigen zum Verlängern gefunden."

        # Verify original run section keeps only its own messages
        run_section = app_section.get("run", {})
        assert isinstance(run_section, dict)
        assert "Unknown command: %s" in run_section
        assert "DONE: No configuration errors found." not in run_section


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
        with patch("kleinanzeigen_bot.download_flow.download_ads", new_callable = AsyncMock) as mock_download:
            await test_bot.run(["script.py", "download"])
            assert test_bot.ads_selector == "new"
            mock_download.assert_awaited_once()

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


class TestKleinanzeigenBotAdManagement:
    """Tests for ad management functionality."""

    @pytest.mark.asyncio
    async def test_download_ads_with_specific_ids(self, test_bot:KleinanzeigenBot, mock_config_setup:None) -> None:  # pylint: disable=unused-argument
        """Test downloading ads with specific IDs."""
        test_bot.ads_selector = "123,456"
        with patch("kleinanzeigen_bot.download_flow.download_ads", new_callable = AsyncMock) as mock_download:
            await test_bot.run(["script.py", "download", "--ads=123,456"])
            assert test_bot.ads_selector == "123,456"
            mock_download.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("command", ["publish", "download", "update", "extend"])
    async def test_run_invalid_selector_exits_with_error(  # pylint: disable=unused-argument
        self, test_bot:KleinanzeigenBot, mock_config_setup:None, command:str,
    ) -> None:
        """Test running command with invalid selector exits with error."""
        with pytest.raises(SystemExit) as exc_info:
            await test_bot.run(["script.py", command, "--ads=invalid"])
        assert exc_info.value.code == 2
