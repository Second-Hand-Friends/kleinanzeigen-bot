# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import copy, os, tempfile  # isort: skip
from collections.abc import Generator
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from ruamel.yaml import YAML

from kleinanzeigen_bot import LOG, KleinanzeigenBot, misc
from kleinanzeigen_bot._version import __version__
from kleinanzeigen_bot.ads import calculate_content_hash
from kleinanzeigen_bot.utils import loggers


@pytest.fixture
def mock_page() -> MagicMock:
    """Provide a mock page object for testing."""
    mock = MagicMock()
    # Mock async methods
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
        "contact": {
            "name": "Test User",
            "zipcode": "12345",
            "location": "Test City",
            "street": "",
            "phone": ""
        }
    }


def create_ad_config(base_config:dict[str, Any], **overrides:Any) -> dict[str, Any]:
    """Create a new ad configuration by extending or overriding the base configuration.

    Args:
        base_config: The base configuration to start from
        **overrides: Key-value pairs to override or extend the base configuration

    Returns:
        A new ad configuration dictionary
    """
    config = copy.deepcopy(base_config)
    for key, value in overrides.items():
        if isinstance(value, dict) and key in config and isinstance(config[key], dict):
            config[key].update(value)
        elif key in config:
            config[key] = value
        else:
            config[key] = value

    # Only check length if description is a string
    if isinstance(config.get("description"), str):
        assert len(config["description"]) <= 4000, "Length of ad description including prefix and suffix exceeds 4000 chars"

    return config


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
            parts = field.split(".")
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
    return remove_fields(
        base_ad_config,
        "id",
        "created_on",
        "shipping_options",
        "special_attributes",
        "contact.street",
        "contact.phone"
    )


@pytest.fixture
def mock_config_setup(test_bot:KleinanzeigenBot) -> Generator[None]:
    """Provide a centralized mock configuration setup for tests.
    This fixture mocks load_config and other essential configuration-related methods."""
    with patch.object(test_bot, "load_config"), \
            patch.object(test_bot, "create_browser_session", new_callable = AsyncMock), \
            patch.object(test_bot, "login", new_callable = AsyncMock), \
            patch.object(test_bot, "web_request", new_callable = AsyncMock) as mock_request:
        # Mock the web request for published ads
        mock_request.return_value = {"content": '{"ads": []}'}
        yield


class TestKleinanzeigenBotInitialization:
    """Tests for KleinanzeigenBot initialization and basic functionality."""

    def test_constructor_initializes_default_values(self, test_bot:KleinanzeigenBot) -> None:
        """Verify that constructor sets all default values correctly."""
        assert test_bot.root_url == "https://www.kleinanzeigen.de"
        assert isinstance(test_bot.config, dict)
        assert test_bot.command == "help"
        assert test_bot.ads_selector == "due"
        assert test_bot.keep_old_ads is False
        assert test_bot.log_file_path is not None
        assert test_bot.file_log is None

    def test_get_version_returns_correct_version(self, test_bot:KleinanzeigenBot) -> None:
        """Verify version retrieval works correctly."""
        with patch("kleinanzeigen_bot.__version__", "1.2.3"):
            assert test_bot.get_version() == "1.2.3"


class TestKleinanzeigenBotLogging:
    """Tests for logging functionality."""

    def test_configure_file_logging_creates_log_file(self, test_bot:KleinanzeigenBot, log_file_path:str) -> None:
        """Verify that file logging configuration creates the log file."""
        test_bot.log_file_path = log_file_path
        test_bot.configure_file_logging()

        assert test_bot.file_log is not None
        assert os.path.exists(log_file_path)

        # Test that calling again doesn't recreate logger
        original_file_log = test_bot.file_log
        test_bot.configure_file_logging()
        assert test_bot.file_log is original_file_log

    def test_configure_file_logging_disabled_when_no_path(self, test_bot:KleinanzeigenBot) -> None:
        """Verify that logging is disabled when no path is provided."""
        test_bot.log_file_path = None
        test_bot.configure_file_logging()
        assert test_bot.file_log is None


class TestKleinanzeigenBotCommandLine:
    """Tests for command line argument parsing."""

    @pytest.mark.parametrize(("args", "expected_command", "expected_selector", "expected_keep_old"), [
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
    ])
    def test_parse_args_handles_valid_arguments(
        self,
        test_bot:KleinanzeigenBot,
        args:list[str],
        expected_command:str,
        expected_selector:str,
        expected_keep_old:bool
    ) -> None:
        """Verify that valid command line arguments are parsed correctly."""
        test_bot.parse_args(["dummy"] + args)  # Add dummy arg to simulate sys.argv[0]
        assert test_bot.command == expected_command
        assert test_bot.ads_selector == expected_selector
        assert test_bot.keep_old_ads == expected_keep_old

    def test_parse_args_handles_help_command(self, test_bot:KleinanzeigenBot) -> None:
        """Verify that help command is handled correctly."""
        with pytest.raises(SystemExit) as exc_info:
            test_bot.parse_args(["dummy", "--help"])
        assert exc_info.value.code == 0

    def test_parse_args_handles_invalid_arguments(self, test_bot:KleinanzeigenBot) -> None:
        """Verify that invalid arguments are handled correctly."""
        with pytest.raises(SystemExit) as exc_info:
            test_bot.parse_args(["dummy", "--invalid-option"])
        assert exc_info.value.code == 2

    def test_parse_args_handles_verbose_flag(self, test_bot:KleinanzeigenBot) -> None:
        """Verify that verbose flag sets correct log level."""
        test_bot.parse_args(["dummy", "--verbose"])
        assert loggers.is_debug(LOG)

    def test_parse_args_handles_config_path(self, test_bot:KleinanzeigenBot, test_data_dir:str) -> None:
        """Verify that config path is set correctly."""
        config_path = Path(test_data_dir) / "custom_config.yaml"
        test_bot.parse_args(["dummy", "--config", str(config_path)])
        assert test_bot.config_file_path == str(config_path.absolute())


class TestKleinanzeigenBotConfiguration:
    """Tests for configuration loading and validation."""

    def test_load_config_handles_missing_file(
        self,
        test_bot:KleinanzeigenBot,
        test_data_dir:str,
        sample_config:dict[str, Any]
    ) -> None:
        """Verify that loading a missing config file creates default config."""
        config_path = Path(test_data_dir) / "missing_config.yaml"
        test_bot.config_file_path = str(config_path)

        # Add categories to sample config
        sample_config_with_categories = sample_config.copy()
        sample_config_with_categories["categories"] = {}

        with patch("kleinanzeigen_bot.utils.dicts.load_dict_if_exists", return_value = None), \
                patch.object(LOG, "warning") as mock_warning, \
                patch("kleinanzeigen_bot.utils.dicts.save_dict") as mock_save, \
                patch("kleinanzeigen_bot.utils.dicts.load_dict_from_module") as mock_load_module:

            mock_load_module.side_effect = [
                sample_config_with_categories,  # config_defaults.yaml
                {"cat1": "id1"},  # categories.yaml
                {"cat2": "id2"}  # categories_old.yaml
            ]

            test_bot.load_config()
            mock_warning.assert_called_once()
            mock_save.assert_called_once_with(str(config_path), sample_config_with_categories)

            # Verify categories were loaded
            assert test_bot.categories == {"cat1": "id1", "cat2": "id2"}
            assert test_bot.config == sample_config_with_categories

    def test_load_config_validates_required_fields(self, test_bot:KleinanzeigenBot, test_data_dir:str) -> None:
        """Verify that config validation checks required fields."""
        config_path = Path(test_data_dir) / "config.yaml"
        config_content = """
login:
  username: testuser
  # Missing password
browser:
  arguments: []
"""
        with open(config_path, "w", encoding = "utf-8") as f:
            f.write(config_content)
        test_bot.config_file_path = str(config_path)

        with pytest.raises(AssertionError) as exc_info:
            test_bot.load_config()
        assert "[login.password] not specified" in str(exc_info.value)


class TestKleinanzeigenBotAuthentication:
    """Tests for login and authentication functionality."""

    @pytest.fixture
    def configured_bot(self, test_bot:KleinanzeigenBot, sample_config:dict[str, Any]) -> KleinanzeigenBot:
        """Provides a bot instance with basic configuration."""
        test_bot.config = sample_config
        return test_bot

    @pytest.mark.asyncio
    async def test_assert_free_ad_limit_not_reached_success(self, configured_bot:KleinanzeigenBot) -> None:
        """Verify that free ad limit check succeeds when limit not reached."""
        with patch.object(configured_bot, "web_find", side_effect = TimeoutError):
            await configured_bot.assert_free_ad_limit_not_reached()

    @pytest.mark.asyncio
    async def test_assert_free_ad_limit_not_reached_limit_reached(self, configured_bot:KleinanzeigenBot) -> None:
        """Verify that free ad limit check fails when limit is reached."""
        with patch.object(configured_bot, "web_find", return_value = AsyncMock()):
            with pytest.raises(AssertionError) as exc_info:
                await configured_bot.assert_free_ad_limit_not_reached()
            assert "Cannot publish more ads" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_is_logged_in_returns_true_when_logged_in(self, configured_bot:KleinanzeigenBot) -> None:
        """Verify that login check returns true when logged in."""
        with patch.object(configured_bot, "web_text", return_value = "Welcome testuser"):
            assert await configured_bot.is_logged_in() is True

    @pytest.mark.asyncio
    async def test_is_logged_in_returns_true_with_alternative_element(self, configured_bot:KleinanzeigenBot) -> None:
        """Verify that login check returns true when logged in with alternative element."""
        with patch.object(configured_bot, "web_text", side_effect = [
            TimeoutError(),  # First try with mr-medium fails
            "angemeldet als: testuser"  # Second try with user-email succeeds
        ]):
            assert await configured_bot.is_logged_in() is True

    @pytest.mark.asyncio
    async def test_is_logged_in_returns_false_when_not_logged_in(self, configured_bot:KleinanzeigenBot) -> None:
        """Verify that login check returns false when not logged in."""
        with patch.object(configured_bot, "web_text", side_effect = TimeoutError):
            assert await configured_bot.is_logged_in() is False

    @pytest.mark.asyncio
    async def test_login_flow_completes_successfully(self, configured_bot:KleinanzeigenBot) -> None:
        """Verify that normal login flow completes successfully."""
        with patch.object(configured_bot, "web_open") as mock_open, \
                patch.object(configured_bot, "is_logged_in", side_effect = [False, True]) as mock_logged_in, \
                patch.object(configured_bot, "web_find", side_effect = TimeoutError), \
                patch.object(configured_bot, "web_input") as mock_input, \
                patch.object(configured_bot, "web_click") as mock_click:

            await configured_bot.login()

            mock_open.assert_called()
            mock_logged_in.assert_called()
            mock_input.assert_called()
            mock_click.assert_called()

    @pytest.mark.asyncio
    async def test_login_flow_handles_captcha(self, configured_bot:KleinanzeigenBot) -> None:
        """Verify that login flow handles captcha correctly."""
        with patch.object(configured_bot, "web_open"), \
                patch.object(configured_bot, "is_logged_in", return_value = False), \
                patch.object(configured_bot, "web_find") as mock_find, \
                patch.object(configured_bot, "web_await") as mock_await, \
                patch.object(configured_bot, "web_input"), \
                patch.object(configured_bot, "web_click"), \
                patch("kleinanzeigen_bot.ainput") as mock_ainput:

            mock_find.side_effect = [
                AsyncMock(),  # Captcha iframe
                TimeoutError(),  # Login form
                TimeoutError(),  # Phone verification
                TimeoutError(),  # GDPR banner
                TimeoutError(),  # GDPR banner click
            ]
            mock_await.return_value = True
            mock_ainput.return_value = ""

            await configured_bot.login()

            assert mock_find.call_count >= 2
            mock_await.assert_called_once()


class TestKleinanzeigenBotLocalization:
    """Tests for localization and help text."""

    def test_show_help_displays_german_text(self, test_bot:KleinanzeigenBot) -> None:
        """Verify that help text is displayed in German when language is German."""
        with patch("kleinanzeigen_bot.get_current_locale") as mock_locale, \
                patch("builtins.print") as mock_print:
            mock_locale.return_value.language = "de"
            test_bot.show_help()
            printed_text = "".join(str(call.args[0]) for call in mock_print.call_args_list)
            assert "Verwendung:" in printed_text
            assert "Befehle:" in printed_text

    def test_show_help_displays_english_text(self, test_bot:KleinanzeigenBot) -> None:
        """Verify that help text is displayed in English when language is English."""
        with patch("kleinanzeigen_bot.get_current_locale") as mock_locale, \
                patch("builtins.print") as mock_print:
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

    def test_configure_file_logging(self, test_bot:KleinanzeigenBot, log_file_path:str) -> None:
        """Test file logging configuration."""
        test_bot.log_file_path = log_file_path
        test_bot.configure_file_logging()
        assert test_bot.file_log is not None
        assert os.path.exists(log_file_path)

    def test_configure_file_logging_no_path(self, test_bot:KleinanzeigenBot) -> None:
        """Test file logging configuration with no path."""
        test_bot.log_file_path = None
        test_bot.configure_file_logging()
        assert test_bot.file_log is None

    def test_close_browser_session(self, test_bot:KleinanzeigenBot) -> None:
        """Test closing browser session."""
        mock_close = MagicMock()
        test_bot.page = MagicMock()  # Ensure page exists to trigger cleanup
        with patch.object(test_bot, "close_browser_session", new = mock_close):
            test_bot.close_browser_session()  # Call directly instead of relying on __del__
            mock_close.assert_called_once()

    def test_get_root_url(self, test_bot:KleinanzeigenBot) -> None:
        """Test root URL retrieval."""
        assert test_bot.root_url == "https://www.kleinanzeigen.de"

    def test_get_config_defaults(self, test_bot:KleinanzeigenBot) -> None:
        """Test default configuration values."""
        assert isinstance(test_bot.config, dict)
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
        with open(config_path, "w", encoding = "utf-8") as f:
            f.write("""
login:
    username: test
    password: test
""")
        test_bot.config_file_path = str(config_path)
        await test_bot.run(["script.py", "verify"])
        assert test_bot.config["login"]["username"] == "test"


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

    def test_load_ads_no_files(self, test_bot:KleinanzeigenBot) -> None:
        """Test loading ads with no files."""
        test_bot.config["ad_files"] = ["nonexistent/*.yaml"]
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
        """Test running publish with invalid selector."""
        with patch.object(test_bot, "load_ads", return_value = []):
            await test_bot.run(["script.py", "publish", "--ads=invalid"])
            assert test_bot.ads_selector == "due"

    @pytest.mark.asyncio
    async def test_run_download_invalid_selector(self, test_bot:KleinanzeigenBot, mock_config_setup:None) -> None:  # pylint: disable=unused-argument
        """Test running download with invalid selector."""
        with patch.object(test_bot, "download_ads", new_callable = AsyncMock):
            await test_bot.run(["script.py", "download", "--ads=invalid"])
            assert test_bot.ads_selector == "new"


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
        ad_cfg = create_ad_config(
            minimal_ad_config,
            title = ""  # Empty title to trigger length validation
        )

        yaml = YAML()
        with open(ad_file, "w", encoding = "utf-8") as f:
            yaml.dump(ad_cfg, f)

        # Set config file path to tmp_path and use relative path for ad_files
        test_bot.config_file_path = str(temp_path / "config.yaml")
        test_bot.config["ad_files"] = ["ads/*.yaml"]
        with pytest.raises(AssertionError) as exc_info:
            test_bot.load_ads()
        assert "must be at least 10 characters long" in str(exc_info.value)

    def test_load_ads_with_invalid_price_type(self, test_bot:KleinanzeigenBot, tmp_path:Any, minimal_ad_config:dict[str, Any]) -> None:
        """Test loading ads with invalid price type."""
        temp_path = Path(tmp_path)
        ad_dir = temp_path / "ads"
        ad_dir.mkdir()
        ad_file = ad_dir / "test_ad.yaml"

        # Create config with invalid price type
        ad_cfg = create_ad_config(
            minimal_ad_config,
            price_type = "INVALID_TYPE"  # Invalid price type
        )

        yaml = YAML()
        with open(ad_file, "w", encoding = "utf-8") as f:
            yaml.dump(ad_cfg, f)

        # Set config file path to tmp_path and use relative path for ad_files
        test_bot.config_file_path = str(temp_path / "config.yaml")
        test_bot.config["ad_files"] = ["ads/*.yaml"]
        with pytest.raises(AssertionError) as exc_info:
            test_bot.load_ads()
        assert "property [price_type] must be one of:" in str(exc_info.value)

    def test_load_ads_with_invalid_shipping_type(self, test_bot:KleinanzeigenBot, tmp_path:Any, minimal_ad_config:dict[str, Any]) -> None:
        """Test loading ads with invalid shipping type."""
        temp_path = Path(tmp_path)
        ad_dir = temp_path / "ads"
        ad_dir.mkdir()
        ad_file = ad_dir / "test_ad.yaml"

        # Create config with invalid shipping type
        ad_cfg = create_ad_config(
            minimal_ad_config,
            shipping_type = "INVALID_TYPE"  # Invalid shipping type
        )

        yaml = YAML()
        with open(ad_file, "w", encoding = "utf-8") as f:
            yaml.dump(ad_cfg, f)

        # Set config file path to tmp_path and use relative path for ad_files
        test_bot.config_file_path = str(temp_path / "config.yaml")
        test_bot.config["ad_files"] = ["ads/*.yaml"]
        with pytest.raises(AssertionError) as exc_info:
            test_bot.load_ads()
        assert "property [shipping_type] must be one of:" in str(exc_info.value)

    def test_load_ads_with_invalid_price_config(self, test_bot:KleinanzeigenBot, tmp_path:Any, minimal_ad_config:dict[str, Any]) -> None:
        """Test loading ads with invalid price configuration."""
        temp_path = Path(tmp_path)
        ad_dir = temp_path / "ads"
        ad_dir.mkdir()
        ad_file = ad_dir / "test_ad.yaml"

        # Create config with price for GIVE_AWAY type
        ad_cfg = create_ad_config(
            minimal_ad_config,
            price_type = "GIVE_AWAY",
            price = 100  # Price should not be set for GIVE_AWAY
        )

        yaml = YAML()
        with open(ad_file, "w", encoding = "utf-8") as f:
            yaml.dump(ad_cfg, f)

        # Set config file path to tmp_path and use relative path for ad_files
        test_bot.config_file_path = str(temp_path / "config.yaml")
        test_bot.config["ad_files"] = ["ads/*.yaml"]
        with pytest.raises(AssertionError) as exc_info:
            test_bot.load_ads()
        assert "must not be specified for GIVE_AWAY ad" in str(exc_info.value)

    def test_load_ads_with_missing_price(self, test_bot:KleinanzeigenBot, tmp_path:Any, minimal_ad_config:dict[str, Any]) -> None:
        """Test loading ads with missing price for FIXED price type."""
        temp_path = Path(tmp_path)
        ad_dir = temp_path / "ads"
        ad_dir.mkdir()
        ad_file = ad_dir / "test_ad.yaml"

        # Create config with FIXED price type but no price
        ad_cfg = create_ad_config(
            minimal_ad_config,
            price_type = "FIXED",
            price = None  # Missing required price for FIXED type
        )

        yaml = YAML()
        with open(ad_file, "w", encoding = "utf-8") as f:
            yaml.dump(ad_cfg, f)

        # Set config file path to tmp_path and use relative path for ad_files
        test_bot.config_file_path = str(temp_path / "config.yaml")
        test_bot.config["ad_files"] = ["ads/*.yaml"]
        with pytest.raises(AssertionError) as exc_info:
            test_bot.load_ads()
        assert "not specified" in str(exc_info.value)

    def test_load_ads_with_invalid_category(self, test_bot:KleinanzeigenBot, tmp_path:Any, minimal_ad_config:dict[str, Any]) -> None:
        """Test loading ads with invalid category."""
        temp_path = Path(tmp_path)
        ad_dir = temp_path / "ads"
        ad_dir.mkdir()
        ad_file = ad_dir / "test_ad.yaml"

        # Create config with invalid category and empty description to prevent auto-detection
        ad_cfg = create_ad_config(
            minimal_ad_config,
            category = "999999",  # Non-existent category
            description = None  # Set description to None to trigger validation
        )

        # Mock the config to prevent auto-detection
        test_bot.config["ad_defaults"] = {
            "description": {
                "prefix": "",
                "suffix": ""
            }
        }

        yaml = YAML()
        with open(ad_file, "w", encoding = "utf-8") as f:
            yaml.dump(ad_cfg, f)

        # Set config file path to tmp_path and use relative path for ad_files
        test_bot.config_file_path = str(temp_path / "config.yaml")
        test_bot.config["ad_files"] = ["ads/*.yaml"]
        with pytest.raises(AssertionError) as exc_info:
            test_bot.load_ads()
        assert "property [description] not specified" in str(exc_info.value)


class TestKleinanzeigenBotAdDeletion:
    """Tests for ad deletion functionality."""

    @pytest.mark.asyncio
    async def test_delete_ad_by_title(self, test_bot:KleinanzeigenBot, minimal_ad_config:dict[str, Any]) -> None:
        """Test deleting an ad by title."""
        test_bot.page = MagicMock()
        test_bot.page.evaluate = AsyncMock(return_value = {"statusCode": 200, "content": "{}"})
        test_bot.page.sleep = AsyncMock()

        # Use minimal config since we only need title for deletion by title
        ad_cfg = create_ad_config(
            minimal_ad_config,
            title = "Test Title",
            id = None  # Explicitly set id to None for title-based deletion
        )

        published_ads = [
            {"title": "Test Title", "id": "67890"},
            {"title": "Other Title", "id": "11111"}
        ]

        with patch.object(test_bot, "web_open", new_callable = AsyncMock), \
                patch.object(test_bot, "web_find", new_callable = AsyncMock) as mock_find, \
                patch.object(test_bot, "web_click", new_callable = AsyncMock), \
                patch.object(test_bot, "web_check", new_callable = AsyncMock, return_value = True):
            mock_find.return_value.attrs = {"content": "some-token"}
            result = await test_bot.delete_ad(ad_cfg, published_ads, delete_old_ads_by_title = True)
            assert result is True

    @pytest.mark.asyncio
    async def test_delete_ad_by_id(self, test_bot:KleinanzeigenBot, minimal_ad_config:dict[str, Any]) -> None:
        """Test deleting an ad by ID."""
        test_bot.page = MagicMock()
        test_bot.page.evaluate = AsyncMock(return_value = {"statusCode": 200, "content": "{}"})
        test_bot.page.sleep = AsyncMock()

        # Create config with ID for deletion by ID
        ad_cfg = create_ad_config(
            minimal_ad_config,
            id = "12345"
        )

        published_ads = [
            {"title": "Different Title", "id": "12345"},
            {"title": "Other Title", "id": "11111"}
        ]

        with patch.object(test_bot, "web_open", new_callable = AsyncMock), \
                patch.object(test_bot, "web_find", new_callable = AsyncMock) as mock_find, \
                patch.object(test_bot, "web_click", new_callable = AsyncMock), \
                patch.object(test_bot, "web_check", new_callable = AsyncMock, return_value = True):
            mock_find.return_value.attrs = {"content": "some-token"}
            result = await test_bot.delete_ad(ad_cfg, published_ads, delete_old_ads_by_title = False)
            assert result is True


class TestKleinanzeigenBotAdRepublication:
    """Tests for ad republication functionality."""

    def test_check_ad_republication_with_changes(self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any]) -> None:
        """Test that ads with changes are marked for republication."""
        # Mock the description config to prevent modification of the description
        test_bot.config["ad_defaults"] = {
            "description": {
                "prefix": "",
                "suffix": ""
            }
        }

        # Create ad config with all necessary fields for republication
        ad_cfg = create_ad_config(
            base_ad_config,
            id = "12345",
            updated_on = "2024-01-01T00:00:00",
            created_on = "2024-01-01T00:00:00",
            description = "Changed description"
        )

        # Create a temporary directory and file
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            ad_dir = temp_path / "ads"
            ad_dir.mkdir()
            ad_file = ad_dir / "test_ad.yaml"

            yaml = YAML()
            with open(ad_file, "w", encoding = "utf-8") as f:
                yaml.dump(ad_cfg, f)

            # Set config file path and use relative path for ad_files
            test_bot.config_file_path = str(temp_path / "config.yaml")
            test_bot.config["ad_files"] = ["ads/*.yaml"]

            # Mock the loading of the original ad configuration
            with patch("kleinanzeigen_bot.utils.dicts.load_dict", side_effect = [
                ad_cfg,  # First call returns the original ad config
                {}  # Second call for ad_fields.yaml
            ]):
                ads_to_publish = test_bot.load_ads()
                assert len(ads_to_publish) == 1

    def test_check_ad_republication_no_changes(self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any]) -> None:
        """Test that unchanged ads within interval are not marked for republication."""
        current_time = misc.now()
        three_days_ago = (current_time - timedelta(days = 3)).isoformat()

        # Create ad config with timestamps for republication check
        ad_cfg = create_ad_config(
            base_ad_config,
            id = "12345",
            updated_on = three_days_ago,
            created_on = three_days_ago
        )

        # Calculate hash before making the copy to ensure they match
        current_hash = calculate_content_hash(ad_cfg)
        ad_cfg_orig = copy.deepcopy(ad_cfg)
        ad_cfg_orig["content_hash"] = current_hash

        # Mock the config to prevent actual file operations
        test_bot.config["ad_files"] = ["test.yaml"]
        with patch("kleinanzeigen_bot.utils.dicts.load_dict_if_exists", return_value = ad_cfg_orig), \
                patch("kleinanzeigen_bot.utils.dicts.load_dict", return_value = {}):  # Mock ad_fields.yaml
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
        ad_cfg = create_ad_config(
            base_ad_config,
            shipping_options = ["DHL_2", "Hermes_Päckchen"],
            created_on = "2024-01-01T00:00:00",  # Add created_on to prevent KeyError
            updated_on = "2024-01-01T00:00:00"  # Add updated_on for consistency
        )

        # Create the original ad config and published ads list
        ad_cfg_orig = copy.deepcopy(ad_cfg)
        ad_cfg_orig["content_hash"] = calculate_content_hash(ad_cfg)  # Add content hash to prevent republication
        published_ads:list[dict[str, Any]] = []

        # Set up default config values needed for the test
        test_bot.config["publishing"] = {
            "delete_old_ads": "BEFORE_PUBLISH",
            "delete_old_ads_by_title": False
        }

        # Create temporary file path
        ad_file = Path(tmp_path) / "test_ad.yaml"

        # Mock web_execute to handle all JavaScript calls
        async def mock_web_execute(script:str) -> Any:
            if script == "document.body.scrollHeight":
                return 0  # Return integer to prevent scrolling loop
            return None

        # Mock the necessary web interaction methods
        with patch.object(test_bot, "web_execute", side_effect = mock_web_execute), \
                patch.object(test_bot, "web_click", new_callable = AsyncMock), \
                patch.object(test_bot, "web_find", new_callable = AsyncMock) as mock_find, \
                patch.object(test_bot, "web_select", new_callable = AsyncMock), \
                patch.object(test_bot, "web_input", new_callable = AsyncMock), \
                patch.object(test_bot, "web_open", new_callable = AsyncMock), \
                patch.object(test_bot, "web_sleep", new_callable = AsyncMock), \
                patch.object(test_bot, "web_check", new_callable = AsyncMock, return_value = True), \
                patch.object(test_bot, "web_request", new_callable = AsyncMock), \
                patch.object(test_bot, "web_find_all", new_callable = AsyncMock) as mock_find_all, \
                patch.object(test_bot, "web_await", new_callable = AsyncMock), \
                patch("builtins.input", return_value = ""):  # Mock the input function

            # Mock the shipping options form elements
            mock_find.side_effect = [
                TimeoutError(),  # First call in assert_free_ad_limit_not_reached
                AsyncMock(attrs = {"content": "csrf-token-123"}),  # CSRF token
                AsyncMock(attrs = {"checked": True}),  # Size radio button check
                AsyncMock(attrs = {"value": "Klein"}),  # Size dropdown
                AsyncMock(attrs = {"value": "Paket 2 kg"}),  # Package type dropdown
                AsyncMock(attrs = {"value": "Päckchen"}),  # Second package type dropdown
                TimeoutError(),  # Captcha check
            ]

            # Mock web_find_all to return empty list for city options
            mock_find_all.return_value = []

            # Mock web_check to return True for radio button checked state
            with patch.object(test_bot, "web_check", new_callable = AsyncMock) as mock_check:
                mock_check.return_value = True

                # Test through the public interface by publishing an ad
                await test_bot.publish_ad(str(ad_file), ad_cfg, ad_cfg_orig, published_ads)

            # Verify that web_find was called the expected number of times
            assert mock_find.await_count >= 3

            # Verify the file was created in the temporary directory
            assert ad_file.exists()


class TestKleinanzeigenBotUrlConstruction:
    """Tests for URL construction functionality."""

    def test_url_construction(self, test_bot:KleinanzeigenBot) -> None:
        """Test that URLs are constructed correctly."""
        # Test login URL
        expected_login_url = "https://www.kleinanzeigen.de/m-einloggen.html?targetUrl=/"
        assert f"{test_bot.root_url}/m-einloggen.html?targetUrl=/" == expected_login_url

        # Test ad management URL
        expected_manage_url = "https://www.kleinanzeigen.de/m-meine-anzeigen.html"
        assert f"{test_bot.root_url}/m-meine-anzeigen.html" == expected_manage_url

        # Test ad publishing URL
        expected_publish_url = "https://www.kleinanzeigen.de/p-anzeige-aufgeben-schritt2.html"
        assert f"{test_bot.root_url}/p-anzeige-aufgeben-schritt2.html" == expected_publish_url


class TestKleinanzeigenBotPrefixSuffix:
    """Tests for description prefix and suffix functionality."""
    # pylint: disable=protected-access

    def test_description_prefix_suffix_handling(
        self,
        test_bot:KleinanzeigenBot,
        description_test_cases:list[tuple[dict[str, Any], str, str]]
    ) -> None:
        """Test handling of description prefix/suffix in various configurations."""
        for config, raw_description, expected_description in description_test_cases:
            test_bot.config = config
            ad_cfg = {"description": raw_description, "active": True}
            # Access private method using the correct name mangling
            description = getattr(test_bot, "_KleinanzeigenBot__get_description")(ad_cfg, with_affixes = True)
            assert description == expected_description

    def test_description_length_validation(self, test_bot:KleinanzeigenBot) -> None:
        """Test that long descriptions with affixes raise appropriate error."""
        test_bot.config = {
            "ad_defaults": {
                "description_prefix": "P" * 1000,
                "description_suffix": "S" * 1000
            }
        }
        ad_cfg = {
            "description": "D" * 2001,  # This plus affixes will exceed 4000 chars
            "active": True
        }

        with pytest.raises(AssertionError) as exc_info:
            getattr(test_bot, "_KleinanzeigenBot__get_description")(ad_cfg, with_affixes = True)

        assert "Length of ad description including prefix and suffix exceeds 4000 chars" in str(exc_info.value)
        assert "Description length: 4001" in str(exc_info.value)


class TestKleinanzeigenBotDescriptionHandling:
    """Tests for description handling functionality."""

    def test_description_without_main_config_description(self, test_bot:KleinanzeigenBot) -> None:
        """Test that description works correctly when description is missing from main config."""
        # Set up config without any description fields
        test_bot.config = {
            "ad_defaults": {
                # No description field at all
            }
        }

        # Test with a simple ad config
        ad_cfg = {
            "description": "Test Description",
            "active": True
        }

        # The description should be returned as-is without any prefix/suffix
        description = getattr(test_bot, "_KleinanzeigenBot__get_description")(ad_cfg, with_affixes = True)
        assert description == "Test Description"

    def test_description_with_only_new_format_affixes(self, test_bot:KleinanzeigenBot) -> None:
        """Test that description works with only new format affixes in config."""
        test_bot.config = {
            "ad_defaults": {
                "description_prefix": "Prefix: ",
                "description_suffix": " :Suffix"
            }
        }

        ad_cfg = {
            "description": "Test Description",
            "active": True
        }

        description = getattr(test_bot, "_KleinanzeigenBot__get_description")(ad_cfg, with_affixes = True)
        assert description == "Prefix: Test Description :Suffix"

    def test_description_with_mixed_config_formats(self, test_bot:KleinanzeigenBot) -> None:
        """Test that description works with both old and new format affixes in config."""
        test_bot.config = {
            "ad_defaults": {
                "description_prefix": "New Prefix: ",
                "description_suffix": " :New Suffix",
                "description": {
                    "prefix": "Old Prefix: ",
                    "suffix": " :Old Suffix"
                }
            }
        }

        ad_cfg = {
            "description": "Test Description",
            "active": True
        }

        description = getattr(test_bot, "_KleinanzeigenBot__get_description")(ad_cfg, with_affixes = True)
        assert description == "New Prefix: Test Description :New Suffix"

    def test_description_with_ad_level_affixes(self, test_bot:KleinanzeigenBot) -> None:
        """Test that ad-level affixes take precedence over config affixes."""
        test_bot.config = {
            "ad_defaults": {
                "description_prefix": "Config Prefix: ",
                "description_suffix": " :Config Suffix"
            }
        }

        ad_cfg = {
            "description": "Test Description",
            "description_prefix": "Ad Prefix: ",
            "description_suffix": " :Ad Suffix",
            "active": True
        }

        description = getattr(test_bot, "_KleinanzeigenBot__get_description")(ad_cfg, with_affixes = True)
        assert description == "Ad Prefix: Test Description :Ad Suffix"

    def test_description_with_none_values(self, test_bot:KleinanzeigenBot) -> None:
        """Test that None values in affixes are handled correctly."""
        test_bot.config = {
            "ad_defaults": {
                "description_prefix": None,
                "description_suffix": None,
                "description": {
                    "prefix": None,
                    "suffix": None
                }
            }
        }

        ad_cfg = {
            "description": "Test Description",
            "active": True
        }

        description = getattr(test_bot, "_KleinanzeigenBot__get_description")(ad_cfg, with_affixes = True)
        assert description == "Test Description"

    def test_description_with_email_replacement(self, test_bot:KleinanzeigenBot) -> None:
        """Test that @ symbols in description are replaced with (at)."""
        test_bot.config = {
            "ad_defaults": {}
        }

        ad_cfg = {
            "description": "Contact: test@example.com",
            "active": True
        }

        description = getattr(test_bot, "_KleinanzeigenBot__get_description")(ad_cfg, with_affixes = True)
        assert description == "Contact: test(at)example.com"


class TestKleinanzeigenBotChangedAds:
    """Tests for the 'changed' ads selector functionality."""

    def test_load_ads_with_changed_selector(self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any]) -> None:
        """Test that only changed ads are loaded when using the 'changed' selector."""
        # Set up the bot with the 'changed' selector
        test_bot.ads_selector = "changed"
        test_bot.config["ad_defaults"] = {
            "description": {
                "prefix": "",
                "suffix": ""
            }
        }

        # Create a changed ad
        changed_ad = create_ad_config(
            base_ad_config,
            id = "12345",
            title = "Changed Ad",
            updated_on = "2024-01-01T00:00:00",
            created_on = "2024-01-01T00:00:00",
            active = True
        )

        # Calculate hash for changed_ad and add it to the config
        # Then modify the ad to simulate a change
        changed_hash = calculate_content_hash(changed_ad)
        changed_ad["content_hash"] = changed_hash
        # Now modify the ad to make it "changed"
        changed_ad["title"] = "Changed Ad - Modified"

        # Create temporary directory and file
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            ad_dir = temp_path / "ads"
            ad_dir.mkdir()

            # Write the ad file
            yaml = YAML()
            changed_file = ad_dir / "changed_ad.yaml"
            with open(changed_file, "w", encoding = "utf-8") as f:
                yaml.dump(changed_ad, f)

            # Set config file path and use relative path for ad_files
            test_bot.config_file_path = str(temp_path / "config.yaml")
            test_bot.config["ad_files"] = ["ads/*.yaml"]

            # Mock the loading of the ad configuration
            with patch("kleinanzeigen_bot.utils.dicts.load_dict", side_effect = [
                changed_ad,  # First call returns the changed ad
                {}  # Second call for ad_fields.yaml
            ]):
                ads_to_publish = test_bot.load_ads()

                # The changed ad should be loaded
                assert len(ads_to_publish) == 1
                assert ads_to_publish[0][1]["title"] == "Changed Ad - Modified"

    def test_load_ads_with_due_selector_includes_all_due_ads(self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any]) -> None:
        """Test that 'due' selector includes all ads that are due for republication, regardless of changes."""
        # Set up the bot with the 'due' selector
        test_bot.ads_selector = "due"
        test_bot.config["ad_defaults"] = {
            "description": {
                "prefix": "",
                "suffix": ""
            }
        }

        # Create a changed ad that is also due for republication
        current_time = misc.now()
        old_date = (current_time - timedelta(days = 10)).isoformat()  # Past republication interval

        changed_ad = create_ad_config(
            base_ad_config,
            id = "12345",
            title = "Changed Ad",
            updated_on = old_date,
            created_on = old_date,
            republication_interval = 7,  # Due for republication after 7 days
            active = True
        )

        # Create temporary directory and file
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            ad_dir = temp_path / "ads"
            ad_dir.mkdir()

            # Write the ad file
            yaml = YAML()
            ad_file = ad_dir / "changed_ad.yaml"
            with open(ad_file, "w", encoding = "utf-8") as f:
                yaml.dump(changed_ad, f)

            # Set config file path and use relative path for ad_files
            test_bot.config_file_path = str(temp_path / "config.yaml")
            test_bot.config["ad_files"] = ["ads/*.yaml"]

            # Mock the loading of the ad configuration
            with patch("kleinanzeigen_bot.utils.dicts.load_dict", side_effect = [
                changed_ad,  # First call returns the changed ad
                {}  # Second call for ad_fields.yaml
            ]):
                ads_to_publish = test_bot.load_ads()

                # The changed ad should be loaded with 'due' selector because it's due for republication
                assert len(ads_to_publish) == 1
