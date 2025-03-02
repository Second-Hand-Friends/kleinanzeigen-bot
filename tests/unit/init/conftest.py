"""
SPDX-FileCopyrightText: © Jens Bergmann and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

This file contains fixtures for the init tests.
"""
from __future__ import annotations

import os
import sys
import types
import logging
import functools
import warnings
from collections.abc import Callable, Generator, AsyncGenerator
from pathlib import Path
from typing import Any, TypeVar, cast
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from kleinanzeigen_bot import KleinanzeigenBot
from kleinanzeigen_bot.utils import loggers


def pytest_configure() -> None:
    """Configure pytest with custom warning filters."""
    # Only ignore the specific I/O error in the logging atexit handler
    warnings.filterwarnings(
        "ignore",
        message="I/O operation on closed file",
        module="logging"
    )

    # Document why we're ignoring these specific warnings
    # These are AsyncMock objects in test teardown that can't be properly awaited
    warnings.filterwarnings(
        "ignore",
        message="coroutine 'AsyncMockMixin._execute_mock_call' was never awaited",
        module="kleinanzeigen_bot.__init__",
        lineno=65  # Only ignore at this specific line where close_browser_session is called
    )

    # Additional warning filters for specific cases
    warnings.filterwarnings(
        "ignore",
        message="coroutine 'AsyncMockMixin._execute_mock_call' was never awaited",
        module="kleinanzeigen_bot.__init__",
        lineno=127  # Ignore at line 127 where close_browser_session is called in a different context
    )

    # Filter warnings in unittest.mock module during test teardown
    warnings.filterwarnings(
        "ignore",
        message="coroutine 'AsyncMockMixin._execute_mock_call' was never awaited",
        module="unittest.mock"
    )

    # Filter warnings in inspect module during test execution
    warnings.filterwarnings(
        "ignore",
        message="coroutine 'AsyncMockMixin._execute_mock_call' was never awaited",
        module="inspect",
        lineno=966  # Specific line in inspect.py where the warning occurs
    )


@pytest.fixture
def log_file_path(tmp_path: Path) -> str:
    """Create a temporary log file path for testing."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir(exist_ok=True)
    return str(log_dir / "test.log")


@pytest.fixture
def clean_logging_setup(log_file_path: str) -> Generator[logging.FileHandler]:
    """Set up and tear down logging properly to avoid warnings.

    This fixture ensures that all handlers are properly closed after the test,
    preventing "I/O operation on closed file" warnings during test teardown.
    """
    # Store original handlers to restore later
    original_handlers = logging.root.handlers.copy()

    # Set up a file handler for testing
    file_handler = logging.FileHandler(log_file_path)
    logging.root.addHandler(file_handler)

    # Run the test
    yield file_handler

    # Clean up after the test
    try:
        # Remove and close the file handler we added
        if file_handler in logging.root.handlers:
            logging.root.removeHandler(file_handler)
        file_handler.close()
    except Exception:
        pass

    # Restore original handlers
    logging.root.handlers = original_handlers


@pytest.fixture
def minimal_ad_config() -> dict[str, Any]:
    """Return a minimal ad configuration for testing."""
    return {
        "title": "Test Item XYZ",  # Needs 9 characters
        "description": "This is a test item",
        "price": 10.0,
        "price_type": "FIXED",
        "category": "12345",
        "shipping_type": "PICKUP",
        "shipping_options": ["DHL_5", "Hermes_M"],  # Add shipping options
        "location": "12345 Test City",
        "type": "OFFER",
        "contact": {
            "name": "Test User"
        },
        "republication_interval": 7,
        "active": True,
        "publishing": {
            "delete_old_ads": "BEFORE_PUBLISH",
            "delete_old_ads_by_title": False
        },
        "special_attributes": {},  # Add special attributes
        "images": [],  # Add empty images list
        "sell_directly": False
    }


@pytest.fixture
def create_ad_config() -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Return a function to create ad configurations with custom values."""
    def _create_ad_config(base_config: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        config = base_config.copy()
        config.update(kwargs)
        return config

    return _create_ad_config


@pytest.fixture
def sample_config() -> dict[str, Any]:
    """Return a sample configuration for testing."""
    return {
        "login": {
            "username": "test_user",
            "password": "test_pass"
        },
        "language": "en",
        "browser": {
            "type": "chrome",
            "headless": True,
            "arguments": [],
            "binary_location": "",
            "extensions": [],
            "use_private_window": False,
            "user_data_dir": "",
            "profile_name": ""
        },
        "categories": {},
        "ad_files": ["ads/*.yaml"],
        "ad_defaults": {}
    }


@pytest.fixture
def description_test_cases() -> list[tuple[dict[str, Any], str, str]]:
    """Return test cases for description prefix/suffix handling."""
    return [
        # (config, raw_description, expected_description)

        # No prefix/suffix in config
        (
            {"ad_defaults": {}},
            "Test Description",
            "Test Description"
        ),

        # Prefix/suffix in config (new format)
        (
            {"ad_defaults": {"description_prefix": "Prefix: ", "description_suffix": " :Suffix"}},
            "Test Description",
            "Prefix: Test Description :Suffix"
        ),

        # Prefix/suffix in config (old format)
        (
            {"ad_defaults": {"description": {"prefix": "Old Prefix: ", "suffix": " :Old Suffix"}}},
            "Test Description",
            "Old Prefix: Test Description :Old Suffix"
        ),

        # Both formats in config (new format should take precedence)
        (
            {
                "ad_defaults": {
                    "description_prefix": "New Prefix: ",
                    "description_suffix": " :New Suffix",
                    "description": {
                        "prefix": "Old Prefix: ",
                        "suffix": " :Old Suffix"
                    }
                }
            },
            "Test Description",
            "New Prefix: Test Description :New Suffix"
        ),

        # Email replacement
        (
            {"ad_defaults": {}},
            "Contact: test@example.com",
            "Contact: test(at)example.com"
        )
    ]


@pytest.fixture
async def test_bot(tmp_path: Path) -> AsyncGenerator[KleinanzeigenBot]:
    """Return a KleinanzeigenBot instance for testing.

    This fixture properly handles async setup and teardown to avoid coroutine warnings.
    """
    log_dir = tmp_path / "logs"
    log_dir.mkdir(exist_ok=True)

    # Create a patched version of the close_browser_session method
    async def mock_close_browser_session(self: KleinanzeigenBot) -> None:
        """Mock implementation of close_browser_session.

        This replaces the actual implementation to avoid psutil issues during testing.
        """
        # Use proper type handling to avoid mypy errors
        self.page = cast(Any, None)
        self.browser = cast(Any, None)

    # Create the bot instance
    bot = KleinanzeigenBot()
    bot.root_url = "https://www.kleinanzeigen.de"
    bot.log_file_path = str(log_dir / "kleinanzeigen-bot.log")
    bot.config_file_path = str(tmp_path / "config.yaml")

    # Add config with publishing key
    bot.config = {
        "publishing": {
            "delete_old_ads": "BEFORE_PUBLISH",
            "delete_old_ads_by_title": False
        },
        "login": {
            "username": "test_user"
        }
    }

    # Mock browser and page
    bot.browser = AsyncMock()
    bot.page = MagicMock()
    bot.page.sleep = AsyncMock()
    bot.page.evaluate = AsyncMock(return_value={"statusCode": 200, "content": "{}"})

    # Set keep_old_ads flag
    bot.keep_old_ads = False

    # Patch the close_browser_session method to avoid psutil issues
    # Use types.MethodType to properly bind the method
    # Use patch.object instead of direct assignment to avoid method-assign error
    with patch.object(bot, 'close_browser_session', types.MethodType(mock_close_browser_session, bot)):
        yield bot

    # Proper async teardown - this will be handled by pytest-asyncio
    # No need to manually call close_browser_session as it's now an async method
    # that will be properly awaited by the fixture


@pytest.fixture
def create_awaitable_mock(return_value: Any = None, side_effect: Any = None) -> AsyncMock:
    """Create an awaitable mock for testing async functions."""
    mock = AsyncMock()
    if return_value is not None:
        mock.return_value = return_value
    if side_effect is not None:
        mock.side_effect = side_effect
    return mock


def access_protected(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator for test functions that need to access protected members.

    This decorator adds pylint disable/enable comments for protected-access
    to allow tests to access protected members of classes under test.
    """
    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        result = func(*args, **kwargs)
        # pylint: enable=protected-access
        return result
    return wrapper


def async_access_protected(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator for async test functions that need to access protected members.

    This decorator adds pylint disable/enable comments for protected-access
    to allow tests to access protected members of classes under test.
    """
    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        result = await func(*args, **kwargs)
        # pylint: enable=protected-access
        return result
    return wrapper
