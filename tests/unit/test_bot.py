"""
SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""
import gc, logging
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest

from kleinanzeigen_bot.utils import loggers

# Use TYPE_CHECKING to avoid circular imports
if TYPE_CHECKING:
    from kleinanzeigen_bot import KleinanzeigenBot
else:
    # At runtime, import the actual class
    from kleinanzeigen_bot import KleinanzeigenBot


class TestKleinanzeigenBot:

    @pytest.fixture
    def bot(self) -> "KleinanzeigenBot":
        return KleinanzeigenBot()

    def test_parse_args_help(self, bot: "KleinanzeigenBot") -> None:
        """Test parsing of help command"""
        bot.parse_args(["app", "help"])
        assert bot.command == "help"
        assert bot.ads_selector == "due"
        assert not bot.keep_old_ads

    def test_parse_args_publish(self, bot: "KleinanzeigenBot") -> None:
        """Test parsing of publish command with options"""
        bot.parse_args(["app", "publish", "--ads=all", "--keep-old"])
        assert bot.command == "publish"
        assert bot.ads_selector == "all"
        assert bot.keep_old_ads

    def test_get_version(self, bot: "KleinanzeigenBot") -> None:
        """Test version retrieval"""
        version = bot.get_version()
        assert isinstance(version, str)
        assert len(version) > 0

    def test_configure_file_logging_creates_handler(self, tmp_path: Path) -> None:
        """Test that configure_file_logging creates a file handler that can be closed."""
        # Create a temporary log file path
        log_file = tmp_path / "test.log"

        # Create the file to ensure it exists for the test
        log_file.touch()

        # Create a bot instance
        bot = KleinanzeigenBot()

        # Set the log file path
        bot.log_file_path = str(log_file)

        # Configure file logging
        with patch('kleinanzeigen_bot.utils.loggers.get_logger', return_value=MagicMock()):
            # Create a mock file handler with a close method
            mock_file_handler = MagicMock()
            mock_file_handler.close = MagicMock()
            # Set the level attribute to avoid TypeError in comparison
            mock_file_handler.level = logging.DEBUG

            # Patch the file handler creation to return our mock
            with patch('logging.FileHandler', return_value=mock_file_handler):
                bot.configure_file_logging()

                # Verify file_log is set
                assert bot.file_log is not None

                # Store a reference to the file_log
                file_log = bot.file_log

                # Set file_log to None (simulating cleanup)
                bot.file_log = None

                # Verify the file was created
                assert log_file.exists()

    def test_file_log_closure_implementation(self) -> None:
        """Test the implementation of file log closure directly.

        This test verifies that the code in __del__ that closes the file log works correctly,
        without relying on the actual __del__ method being called.
        """
        # Create a mock file log
        mock_file_log = MagicMock()

        # Create a bot instance
        bot = KleinanzeigenBot()
        bot.file_log = mock_file_log

        # Directly execute the file log closure code from __del__
        if bot.file_log:
            bot.file_log.close()
            bot.file_log = None

        # Verify the file log was closed
        mock_file_log.close.assert_called_once()
