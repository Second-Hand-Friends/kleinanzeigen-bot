# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Tests for the error handlers module.

This module contains tests for the error handling functionality of the kleinanzeigen-bot application.
It tests both the exception handler and signal handler functionality.
"""

import sys
from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel, ValidationError

from kleinanzeigen_bot.utils.error_handlers import on_exception, on_sigint

# --------------------------------------------------------------------------- #
# Test fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def mock_logger() -> Generator[MagicMock, None, None]:
    """Fixture to mock the logger."""
    with patch("kleinanzeigen_bot.utils.error_handlers.LOG") as mock_log:
        yield mock_log


@pytest.fixture
def mock_sys_exit() -> Generator[MagicMock, None, None]:
    """Fixture to mock sys.exit to prevent actual program termination."""
    with patch("sys.exit") as mock_exit:
        yield mock_exit


# --------------------------------------------------------------------------- #
# Test cases
# --------------------------------------------------------------------------- #


class TestExceptionHandler:
    """Test cases for the exception handler."""

    def test_keyboard_interrupt(self, mock_logger:MagicMock, mock_sys_exit:MagicMock) -> None:
        """Test that KeyboardInterrupt is handled by the system excepthook."""
        with patch("sys.__excepthook__") as mock_excepthook:
            on_exception(KeyboardInterrupt, KeyboardInterrupt(), None)
            mock_excepthook.assert_called_once()
            mock_sys_exit.assert_called_once_with(1)

    def test_validation_error(self, mock_logger:MagicMock, mock_sys_exit:MagicMock) -> None:
        """Test that ValidationError is formatted and logged."""

        class TestModel(BaseModel):
            field:int

        try:
            TestModel(field = "not an int")  # type: ignore[arg-type]
        except ValidationError as error:
            on_exception(ValidationError, error, None)
            mock_logger.error.assert_called_once()
            mock_sys_exit.assert_called_once_with(1)

    def test_assertion_error(self, mock_logger:MagicMock, mock_sys_exit:MagicMock) -> None:
        """Test that AssertionError is logged directly."""
        error = AssertionError("Test error")
        on_exception(AssertionError, error, None)
        # Accept both with and without trailing newline
        logged = mock_logger.error.call_args[0][0]
        assert logged.strip() == str(error) or logged.strip() == f"{error.__class__.__name__}: {error}"
        mock_sys_exit.assert_called_once_with(1)

    def test_unknown_exception(self, mock_logger:MagicMock, mock_sys_exit:MagicMock) -> None:
        """Test that unknown exceptions are logged with type and message."""
        error = RuntimeError("Test error")
        on_exception(RuntimeError, error, None)
        logged = mock_logger.error.call_args[0][0]
        assert logged.strip() == f"{error.__class__.__name__}: {error}"
        mock_sys_exit.assert_called_once_with(1)

    def test_missing_exception_info(self, mock_logger:MagicMock, mock_sys_exit:MagicMock) -> None:
        """Test handling of missing exception information."""
        on_exception(None, None, None)
        mock_logger.error.assert_called_once()
        # sys.exit is not called for missing exception info
        mock_sys_exit.assert_not_called()

    def test_debug_mode_error(self, mock_logger:MagicMock, mock_sys_exit:MagicMock) -> None:
        """Test error handling in debug mode."""
        with patch("kleinanzeigen_bot.utils.error_handlers.loggers.is_debug", return_value = True):
            try:
                raise ValueError("Test error")
            except ValueError as error:
                _, _, tb = sys.exc_info()
                on_exception(ValueError, error, tb)
                mock_logger.error.assert_called_once()
                # Verify that traceback was included
                logged = mock_logger.error.call_args[0][0]
                assert "Traceback" in logged
                assert "ValueError: Test error" in logged
                mock_sys_exit.assert_called_once_with(1)

    def test_attribute_error(self, mock_logger:MagicMock, mock_sys_exit:MagicMock) -> None:
        """Test handling of AttributeError."""
        try:
            raise AttributeError("Test error")
        except AttributeError as error:
            _, _, tb = sys.exc_info()
            on_exception(AttributeError, error, tb)
            mock_logger.error.assert_called_once()
            # Verify that traceback was included
            logged = mock_logger.error.call_args[0][0]
            assert "Traceback" in logged
            assert "AttributeError: Test error" in logged
            mock_sys_exit.assert_called_once_with(1)

    def test_import_error(self, mock_logger:MagicMock, mock_sys_exit:MagicMock) -> None:
        """Test handling of ImportError."""
        try:
            raise ImportError("Test error")
        except ImportError as error:
            _, _, tb = sys.exc_info()
            on_exception(ImportError, error, tb)
            mock_logger.error.assert_called_once()
            # Verify that traceback was included
            logged = mock_logger.error.call_args[0][0]
            assert "Traceback" in logged
            assert "ImportError: Test error" in logged
            mock_sys_exit.assert_called_once_with(1)

    def test_name_error(self, mock_logger:MagicMock, mock_sys_exit:MagicMock) -> None:
        """Test handling of NameError."""
        try:
            raise NameError("Test error")
        except NameError as error:
            _, _, tb = sys.exc_info()
            on_exception(NameError, error, tb)
            mock_logger.error.assert_called_once()
            # Verify that traceback was included
            logged = mock_logger.error.call_args[0][0]
            assert "Traceback" in logged
            assert "NameError: Test error" in logged
            mock_sys_exit.assert_called_once_with(1)

    def test_type_error(self, mock_logger:MagicMock, mock_sys_exit:MagicMock) -> None:
        """Test handling of TypeError."""
        try:
            raise TypeError("Test error")
        except TypeError as error:
            _, _, tb = sys.exc_info()
            on_exception(TypeError, error, tb)
            mock_logger.error.assert_called_once()
            # Verify that traceback was included
            logged = mock_logger.error.call_args[0][0]
            assert "Traceback" in logged
            assert "TypeError: Test error" in logged
            mock_sys_exit.assert_called_once_with(1)


class TestSignalHandler:
    """Test cases for the signal handler."""

    def test_sigint_handler(self, mock_logger:MagicMock, mock_sys_exit:MagicMock) -> None:
        """Test that SIGINT is handled with a warning message."""
        on_sigint(2, None)  # 2 is SIGINT
        mock_logger.warning.assert_called_once_with("Aborted on user request.")
        mock_sys_exit.assert_called_once_with(0)
