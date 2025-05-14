# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import os
from typing import Any, Final
from unittest.mock import MagicMock

import pytest

from kleinanzeigen_bot import KleinanzeigenBot
from kleinanzeigen_bot.extract import AdExtractor
from kleinanzeigen_bot.model.config_model import Config
from kleinanzeigen_bot.utils import loggers
from kleinanzeigen_bot.utils.web_scraping_mixin import Browser

loggers.configure_console_logging()

LOG:Final[loggers.Logger] = loggers.get_logger("kleinanzeigen_bot")
LOG.setLevel(loggers.DEBUG)


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


@pytest.fixture
def browser_mock() -> MagicMock:
    """Provides a mock browser instance for testing.

    This mock is configured with the Browser spec to ensure it has all
    the required methods and attributes of a real Browser instance.
    """
    return MagicMock(spec = Browser)


@pytest.fixture
def log_file_path(test_data_dir:str) -> str:
    """Provides a temporary path for log files.

    Dependencies:
        - test_data_dir: Used to create the log file in the temporary test directory
    """
    return os.path.join(str(test_data_dir), "test.log")


@pytest.fixture
def test_extractor(browser_mock:MagicMock, test_bot_config:Config) -> AdExtractor:
    """Provides a fresh AdExtractor instance for testing.

    Dependencies:
        - browser_mock: Used to mock browser interactions
        - test_bot_config: Used to initialize the extractor with a valid configuration
    """
    return AdExtractor(browser_mock, test_bot_config)


@pytest.fixture
def description_test_cases() -> list[tuple[dict[str, Any], str, str]]:
    """Provides test cases for description prefix/suffix handling.

    Returns tuples of (config, raw_description, expected_description)
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


@pytest.fixture
def mock_web_text_responses() -> list[str]:
    """Provides common mock responses for web_text calls."""
    return [
        "Test Title",  # Title
        "Test Description",  # Description
        "03.02.2025"  # Creation date
    ]


@pytest.fixture(autouse = True)
def silence_nodriver_logs() -> None:
    loggers.get_logger("nodriver").setLevel(loggers.WARNING)
