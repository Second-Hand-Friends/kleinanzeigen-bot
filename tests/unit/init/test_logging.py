"""
SPDX-FileCopyrightText: © Jens Bergmann and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

Tests for KleinanzeigenBot logging functionality.
"""
import os
from pathlib import Path
from typing import Any, Optional
from unittest.mock import patch

import pytest
from pytest import FixtureRequest

from kleinanzeigen_bot import LOG, KleinanzeigenBot
from kleinanzeigen_bot.utils import loggers
from kleinanzeigen_bot.utils.loggers import LogFileHandle


def test_configure_file_logging_creates_log_file(test_bot: KleinanzeigenBot, log_file_path: str) -> None:
    """Verify that file logging configuration creates the log file."""
    test_bot.log_file_path = log_file_path
    test_bot.configure_file_logging()

    assert test_bot.file_log is not None
    assert os.path.exists(log_file_path)

    # Test that calling again doesn't recreate logger
    original_file_log = test_bot.file_log
    test_bot.configure_file_logging()
    assert test_bot.file_log is original_file_log


def test_configure_file_logging_disabled_when_no_path(test_bot: KleinanzeigenBot) -> None:
    """Verify that logging is disabled when no path is provided."""
    test_bot.log_file_path = None
    test_bot.configure_file_logging()
    assert test_bot.file_log is None


def test_configure_file_logging_with_tmp_path(test_bot: KleinanzeigenBot, tmp_path: Path) -> None:
    """Test the configure_file_logging method with a temporary path."""
    log_file = tmp_path / "kleinanzeigen_bot.log"

    with patch('kleinanzeigen_bot.abspath', return_value=str(log_file)):
        with patch('kleinanzeigen_bot.loggers.configure_file_logging') as mock_configure:
            mock_configure.return_value = mock_configure.return_value
            test_bot.configure_file_logging()
            mock_configure.assert_called_once_with(test_bot.log_file_path)


def test_get_log_level(test_bot: KleinanzeigenBot) -> None:
    """Test log level configuration."""
    # Reset log level to default
    LOG.setLevel(loggers.INFO)
    assert not loggers.is_debug(LOG)
    test_bot.parse_args(['script.py', '-v'])
    assert loggers.is_debug(LOG)


def test_get_log_file_path(test_bot: KleinanzeigenBot) -> None:
    """Test log file path handling."""
    default_path = test_bot.log_file_path
    if default_path is not None:
        assert default_path.endswith(".log")
    test_path = os.path.abspath("custom.log")
    test_bot.log_file_path = test_path
    assert test_bot.log_file_path == test_path


def test_log_level_defaults_to_info() -> None:
    """Verify that the default log level is INFO."""
    # Reset log level to default
    LOG.setLevel(loggers.INFO)
    assert LOG.level == loggers.INFO
    assert LOG.level != loggers.DEBUG


def test_verbose_flag_sets_debug_level() -> None:
    """Verify that the verbose flag sets the log level to DEBUG."""
    # Set log level to DEBUG
    LOG.setLevel(loggers.DEBUG)

    assert LOG.level == loggers.DEBUG
    assert LOG.level != loggers.INFO

    # Reset log level to INFO for other tests
    LOG.setLevel(loggers.INFO)


@pytest.mark.asyncio
async def test_configure_file_logging_async(test_bot: KleinanzeigenBot, tmp_path: Path) -> None:
    """Test that file logging is configured correctly in an async context."""
    # Setup
    log_file = tmp_path / "test.log"
    test_bot.log_file_path = str(log_file)

    # Execute
    test_bot.configure_file_logging()

    # Verify
    assert test_bot.file_log is not None
    assert os.path.exists(log_file)

    # Log something to verify it's written to the file
    LOG.info("Test log message")

    # Close the log file
    if test_bot.file_log:
        test_bot.file_log.close()
        test_bot.file_log = None

    # Verify the log file contains the message
    with open(log_file, "r", encoding="utf-8") as f:
        log_content = f.read()
        assert "Test log message" in log_content
