"""
SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""
import logging
import os
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from kleinanzeigen_bot import LOG, KleinanzeigenBot


class TestKleinanzeigenBotInitialization:
    """Tests for KleinanzeigenBot initialization and basic functionality."""

    def test_constructor_initializes_default_values(self, test_bot: KleinanzeigenBot) -> None:
        """Verify that constructor sets all default values correctly."""
        assert test_bot.root_url == "https://www.kleinanzeigen.de"
        assert isinstance(test_bot.config, dict)
        assert test_bot.command == "help"
        assert test_bot.ads_selector == "due"
        assert test_bot.keep_old_ads is False
        assert test_bot.log_file_path is not None
        assert test_bot.file_log is None

    def test_get_version_returns_correct_version(self, test_bot: KleinanzeigenBot) -> None:
        """Verify version retrieval works correctly."""
        with patch('kleinanzeigen_bot.__version__', '1.2.3'):
            assert test_bot.get_version() == '1.2.3'


class TestKleinanzeigenBotLogging:
    """Tests for logging functionality."""

    def test_configure_file_logging_creates_log_file(self, test_bot: KleinanzeigenBot, log_file_path: str) -> None:
        """Verify that file logging configuration creates the log file."""
        test_bot.log_file_path = log_file_path
        test_bot.configure_file_logging()

        assert test_bot.file_log is not None
        assert os.path.exists(log_file_path)

        # Test that calling again doesn't recreate logger
        original_file_log = test_bot.file_log
        test_bot.configure_file_logging()
        assert test_bot.file_log is original_file_log

    def test_configure_file_logging_disabled_when_no_path(self, test_bot: KleinanzeigenBot) -> None:
        """Verify that logging is disabled when no path is provided."""
        test_bot.log_file_path = None
        test_bot.configure_file_logging()
        assert test_bot.file_log is None


class TestKleinanzeigenBotCommandLine:
    """Tests for command line argument parsing."""

    @pytest.mark.parametrize("args,expected_command,expected_selector,expected_keep_old", [
        (["publish", "--ads=all"], "publish", "all", False),
        (["verify"], "verify", "due", False),
        (["download", "--ads=12345"], "download", "12345", False),
        (["publish", "--force"], "publish", "all", False),
        (["publish", "--keep-old"], "publish", "due", True),
        (["publish", "--ads=all", "--keep-old"], "publish", "all", True),
        (["download", "--ads=new"], "download", "new", False),
        (["version"], "version", "due", False),
    ])
    def test_parse_args_handles_valid_arguments(
        self,
        test_bot: KleinanzeigenBot,
        args: list[str],
        expected_command: str,
        expected_selector: str,
        expected_keep_old: bool
    ) -> None:
        """Verify that valid command line arguments are parsed correctly."""
        test_bot.parse_args(["dummy"] + args)  # Add dummy arg to simulate sys.argv[0]
        assert test_bot.command == expected_command
        assert test_bot.ads_selector == expected_selector
        assert test_bot.keep_old_ads == expected_keep_old

    def test_parse_args_handles_help_command(self, test_bot: KleinanzeigenBot) -> None:
        """Verify that help command is handled correctly."""
        with pytest.raises(SystemExit) as exc_info:
            test_bot.parse_args(["dummy", "--help"])
        assert exc_info.value.code == 0

    def test_parse_args_handles_invalid_arguments(self, test_bot: KleinanzeigenBot) -> None:
        """Verify that invalid arguments are handled correctly."""
        with pytest.raises(SystemExit) as exc_info:
            test_bot.parse_args(["dummy", "--invalid-option"])
        assert exc_info.value.code == 2

    def test_parse_args_handles_verbose_flag(self, test_bot: KleinanzeigenBot) -> None:
        """Verify that verbose flag sets correct log level."""
        test_bot.parse_args(["dummy", "--verbose"])
        assert LOG.level == logging.DEBUG

    def test_parse_args_handles_config_path(self, test_bot: KleinanzeigenBot, test_data_dir: str) -> None:
        """Verify that config path is set correctly."""
        config_path = os.path.join(test_data_dir, "custom_config.yaml")
        test_bot.parse_args(["dummy", "--config", config_path])
        assert test_bot.config_file_path == os.path.abspath(config_path)


class TestKleinanzeigenBotConfiguration:
    """Tests for configuration loading and validation."""

    def test_load_config_handles_missing_file(
        self,
        test_bot: KleinanzeigenBot,
        test_data_dir: str,
        sample_config: dict[str, Any]
    ) -> None:
        """Verify that loading a missing config file creates default config."""
        config_path = os.path.join(str(test_data_dir), "missing_config.yaml")
        test_bot.config_file_path = config_path

        # Add categories to sample config
        sample_config_with_categories = sample_config.copy()
        sample_config_with_categories["categories"] = {}

        with patch('kleinanzeigen_bot.utils.load_dict_if_exists', return_value=None), \
                patch.object(LOG, 'warning') as mock_warning, \
                patch('kleinanzeigen_bot.utils.save_dict') as mock_save, \
                patch('kleinanzeigen_bot.utils.load_dict_from_module') as mock_load_module:

            mock_load_module.side_effect = [
                sample_config_with_categories,  # config_defaults.yaml
                {'cat1': 'id1'},      # categories.yaml
                {'cat2': 'id2'}       # categories_old.yaml
            ]

            test_bot.load_config()
            mock_warning.assert_called_once()
            mock_save.assert_called_once_with(config_path, sample_config_with_categories)

            # Verify categories were loaded
            assert test_bot.categories == {'cat1': 'id1', 'cat2': 'id2'}
            assert test_bot.config == sample_config_with_categories

    def test_load_config_validates_required_fields(self, test_bot: KleinanzeigenBot, test_data_dir: str) -> None:
        """Verify that config validation checks required fields."""
        config_path = os.path.join(str(test_data_dir), "config.yaml")
        config_content = """
login:
  username: testuser
  # Missing password
browser:
  arguments: []
"""
        with open(config_path, 'w', encoding='utf-8') as f:
            f.write(config_content)
        test_bot.config_file_path = config_path

        with pytest.raises(AssertionError) as exc_info:
            test_bot.load_config()
        assert "[login.password] not specified" in str(exc_info.value)


class TestKleinanzeigenBotAuthentication:
    """Tests for login and authentication functionality."""

    @pytest.fixture
    def configured_bot(self, test_bot: KleinanzeigenBot, sample_config: dict[str, Any]) -> KleinanzeigenBot:
        """Provides a bot instance with basic configuration."""
        test_bot.config = sample_config
        return test_bot

    @pytest.mark.asyncio
    async def test_assert_free_ad_limit_not_reached_success(self, configured_bot: KleinanzeigenBot) -> None:
        """Verify that free ad limit check succeeds when limit not reached."""
        with patch.object(configured_bot, 'web_find', side_effect=TimeoutError):
            await configured_bot.assert_free_ad_limit_not_reached()

    @pytest.mark.asyncio
    async def test_assert_free_ad_limit_not_reached_limit_reached(self, configured_bot: KleinanzeigenBot) -> None:
        """Verify that free ad limit check fails when limit is reached."""
        with patch.object(configured_bot, 'web_find', return_value=AsyncMock()):
            with pytest.raises(AssertionError) as exc_info:
                await configured_bot.assert_free_ad_limit_not_reached()
            assert "Cannot publish more ads" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_is_logged_in_returns_true_when_logged_in(self, configured_bot: KleinanzeigenBot) -> None:
        """Verify that login check returns true when logged in."""
        with patch.object(configured_bot, 'web_text', return_value='Welcome testuser'):
            assert await configured_bot.is_logged_in() is True

    @pytest.mark.asyncio
    async def test_is_logged_in_returns_false_when_not_logged_in(self, configured_bot: KleinanzeigenBot) -> None:
        """Verify that login check returns false when not logged in."""
        with patch.object(configured_bot, 'web_text', side_effect=TimeoutError):
            assert await configured_bot.is_logged_in() is False

    @pytest.mark.asyncio
    async def test_login_flow_completes_successfully(self, configured_bot: KleinanzeigenBot) -> None:
        """Verify that normal login flow completes successfully."""
        with patch.object(configured_bot, 'web_open') as mock_open, \
                patch.object(configured_bot, 'is_logged_in', side_effect=[False, True]) as mock_logged_in, \
                patch.object(configured_bot, 'web_find', side_effect=TimeoutError), \
                patch.object(configured_bot, 'web_input') as mock_input, \
                patch.object(configured_bot, 'web_click') as mock_click:

            await configured_bot.login()

            mock_open.assert_called()
            mock_logged_in.assert_called()
            mock_input.assert_called()
            mock_click.assert_called()

    @pytest.mark.asyncio
    async def test_login_flow_handles_captcha(self, configured_bot: KleinanzeigenBot) -> None:
        """Verify that login flow handles captcha correctly."""
        with patch.object(configured_bot, 'web_open'), \
                patch.object(configured_bot, 'is_logged_in', return_value=False), \
                patch.object(configured_bot, 'web_find') as mock_find, \
                patch.object(configured_bot, 'web_await') as mock_await, \
                patch.object(configured_bot, 'web_input'), \
                patch.object(configured_bot, 'web_click'), \
                patch('kleinanzeigen_bot.ainput') as mock_ainput:

            mock_find.side_effect = [
                AsyncMock(),      # Captcha iframe
                TimeoutError(),   # Login form
                TimeoutError(),   # Phone verification
                TimeoutError(),   # GDPR banner
                TimeoutError(),   # GDPR banner click
            ]
            mock_await.return_value = True
            mock_ainput.return_value = ""

            await configured_bot.login()

            assert mock_find.call_count >= 2
            mock_await.assert_called_once()


class TestKleinanzeigenBotLocalization:
    """Tests for localization and help text."""

    def test_show_help_displays_german_text(self, test_bot: KleinanzeigenBot) -> None:
        """Verify that help text is displayed in German when language is German."""
        with patch('kleinanzeigen_bot.get_current_locale') as mock_locale, \
                patch('builtins.print') as mock_print:
            mock_locale.return_value.language = "de"
            test_bot.show_help()
            printed_text = ''.join(str(call.args[0]) for call in mock_print.call_args_list)
            assert "Verwendung:" in printed_text
            assert "Befehle:" in printed_text

    def test_show_help_displays_english_text(self, test_bot: KleinanzeigenBot) -> None:
        """Verify that help text is displayed in English when language is English."""
        with patch('kleinanzeigen_bot.get_current_locale') as mock_locale, \
                patch('builtins.print') as mock_print:
            mock_locale.return_value.language = "en"
            test_bot.show_help()
            printed_text = ''.join(str(call.args[0]) for call in mock_print.call_args_list)
            assert "Usage:" in printed_text
            assert "Commands:" in printed_text
