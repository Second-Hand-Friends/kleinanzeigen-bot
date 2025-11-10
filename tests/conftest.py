# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""
Shared test fixtures for the kleinanzeigen-bot test suite.

This module contains fixtures that are used across multiple test files.
Test-specific fixtures should be defined in individual test files or local conftest.py files.

Fixture Organization:
- Core fixtures: Basic test infrastructure (test_data_dir, test_bot_config, test_bot)
- Mock fixtures: Mock objects for external dependencies (browser_mock)
- Utility fixtures: Helper fixtures for common test scenarios (log_file_path)
- Smoke test fixtures: Special fixtures for smoke tests (smoke_bot, DummyBrowser, etc.)
- Test data fixtures: Shared test data (description_test_cases)
"""
import os
from collections.abc import Iterator
from typing import Any, Final, cast
from unittest.mock import MagicMock

import pytest

from kleinanzeigen_bot import KleinanzeigenBot
from kleinanzeigen_bot.model.ad_model import Ad
from kleinanzeigen_bot.model.config_model import Config
from kleinanzeigen_bot.utils import i18n, loggers
from kleinanzeigen_bot.utils.web_scraping_mixin import Browser

loggers.configure_console_logging()

LOG:Final[loggers.Logger] = loggers.get_logger("kleinanzeigen_bot")
LOG.setLevel(loggers.DEBUG)

os.environ.setdefault("PYTEST_XDIST_AUTO_NUM_WORKERS", str(max(1, (os.cpu_count() or 1))))


# ============================================================================
# Core Fixtures - Basic test infrastructure
# ============================================================================

@pytest.fixture
def test_data_dir(tmp_path:str) -> str:
    """Provides a temporary directory for test data.

    This fixture uses pytest's built-in tmp_path fixture to create a temporary
    directory that is automatically cleaned up after each test.
    """
    return str(tmp_path)


@pytest.fixture
def test_bot_config() -> Config:
    """Provides a basic sample configuration for testing.

    This configuration includes all required fields for the bot to function:
    - Login credentials (username/password)
    - Publishing settings
    """
    return Config.model_validate({
        "ad_defaults": {
            "contact": {
                "name": "dummy_name",
                "zipcode": "12345"
            },
        },
        "login": {
            "username": "dummy_user",
            "password": "dummy_password"
        },
        "publishing": {
            "delete_old_ads": "BEFORE_PUBLISH",
            "delete_old_ads_by_title": False
        }
    })


@pytest.fixture
def test_bot(test_bot_config:Config) -> KleinanzeigenBot:
    """Provides a fresh KleinanzeigenBot instance for all test methods.

    Dependencies:
        - test_bot_config: Used to initialize the bot with a valid configuration
    """
    bot_instance = KleinanzeigenBot()
    bot_instance.config = test_bot_config
    return bot_instance


# ============================================================================
# Mock Fixtures - Mock objects for external dependencies
# ============================================================================

@pytest.fixture
def browser_mock() -> MagicMock:
    """Provides a mock browser instance for testing.

    This mock is configured with the Browser spec to ensure it has all
    the required methods and attributes of a real Browser instance.
    """
    return MagicMock(spec = Browser)


# ============================================================================
# Utility Fixtures - Helper fixtures for common test scenarios
# ============================================================================

@pytest.fixture
def log_file_path(test_data_dir:str) -> str:
    """Provides a temporary path for log files.

    Dependencies:
        - test_data_dir: Used to create the log file in the temporary test directory
    """
    return os.path.join(str(test_data_dir), "test.log")


# ============================================================================
# Test Data Fixtures - Shared test data
# ============================================================================


@pytest.fixture
def description_test_cases() -> list[tuple[dict[str, Any], str, str]]:
    """Provides test cases for description prefix/suffix handling.

    Returns tuples of (config, raw_description, expected_description)
    Used by test_init.py and test_extract.py for testing description processing.
    """
    return [
        # Test case 1: New flattened format
        (
            {
                "ad_defaults": {
                    "description_prefix": "Global Prefix\n",
                    "description_suffix": "\nGlobal Suffix"
                }
            },
            "Original Description",  # Raw description without affixes
            "Global Prefix\nOriginal Description\nGlobal Suffix"  # Expected with affixes
        ),
        # Test case 2: Legacy nested format
        (
            {
                "ad_defaults": {
                    "description": {
                        "prefix": "Legacy Prefix\n",
                        "suffix": "\nLegacy Suffix"
                    }
                }
            },
            "Original Description",
            "Legacy Prefix\nOriginal Description\nLegacy Suffix"
        ),
        # Test case 3: Both formats - new format takes precedence
        (
            {
                "ad_defaults": {
                    "description_prefix": "New Prefix\n",
                    "description_suffix": "\nNew Suffix",
                    "description": {
                        "prefix": "Legacy Prefix\n",
                        "suffix": "\nLegacy Suffix"
                    }
                }
            },
            "Original Description",
            "New Prefix\nOriginal Description\nNew Suffix"
        ),
        # Test case 4: Empty config
        (
            {"ad_defaults": {}},
            "Original Description",
            "Original Description"
        ),
        # Test case 5: None values in config
        (
            {
                "ad_defaults": {
                    "description_prefix": None,
                    "description_suffix": None,
                    "description": {
                        "prefix": None,
                        "suffix": None
                    }
                }
            },
            "Original Description",
            "Original Description"
        ),
    ]


# ============================================================================
# Global Setup Fixtures - Applied automatically to all tests
# ============================================================================

@pytest.fixture(autouse = True)
def silence_nodriver_logs() -> None:
    """Silence nodriver logs during testing to reduce noise."""
    loggers.get_logger("nodriver").setLevel(loggers.WARNING)


@pytest.fixture(autouse = True)
def force_english_locale() -> Iterator[None]:
    """Ensure tests run with a deterministic English locale."""
    previous_locale = i18n.get_current_locale()
    i18n.set_current_locale(i18n.Locale("en", "US", "UTF-8"))
    yield
    i18n.set_current_locale(previous_locale)


# ============================================================================
# Smoke Test Fixtures - Special fixtures for smoke tests
# ============================================================================

class DummyBrowser:
    def __init__(self) -> None:
        self.page = DummyPage()
        self._process_pid = None  # Use None to indicate no real process

    def stop(self) -> None:
        pass  # Dummy method to satisfy close_browser_session


class DummyPage:
    def find_element(self, selector:str) -> "DummyElement":
        return DummyElement()


class DummyElement:
    def click(self) -> None:
        pass

    def type(self, text:str) -> None:
        pass


class SmokeKleinanzeigenBot(KleinanzeigenBot):
    """A test subclass that overrides async methods for smoke testing."""

    def __init__(self) -> None:
        super().__init__()
        # Use cast to satisfy type checker for browser attribute
        self.browser = cast(Browser, DummyBrowser())

    def close_browser_session(self) -> None:
        # Override to avoid psutil.Process logic in tests
        self.page = None  # pyright: ignore[reportAttributeAccessIssue]
        if self.browser:
            self.browser.stop()
            self.browser = None  # pyright: ignore[reportAttributeAccessIssue]

    async def login(self) -> None:
        return None

    async def publish_ads(self, ad_cfgs:list[tuple[str, Ad, dict[str, Any]]]) -> None:
        return None

    def load_ads(self, *, ignore_inactive:bool = True, exclude_ads_with_id:bool = True) -> list[tuple[str, Ad, dict[str, Any]]]:
        # Use cast to satisfy type checker for dummy Ad value
        return [("dummy_file", cast(Ad, None), {})]

    def load_config(self) -> None:
        return None


@pytest.fixture
def smoke_bot() -> SmokeKleinanzeigenBot:
    """Fixture providing a ready-to-use smoke test bot instance."""
    bot = SmokeKleinanzeigenBot()
    bot.command = "publish"
    return bot
