"""
SPDX-FileCopyrightText: © Jens Bergmann and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

Tests for KleinanzeigenBot configuration loading and validation.
"""
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from ruamel.yaml import YAML
from ruamel.yaml.scanner import ScannerError

from kleinanzeigen_bot import LOG
from tests.conftest import KleinanzeigenBotProtocol


def test_load_config_handles_missing_file(
    test_bot: KleinanzeigenBotProtocol,
    test_data_dir: str,
    sample_config: dict[str, Any]
) -> None:
    """Verify that loading a missing config file creates default config."""
    config_path = Path(test_data_dir) / "missing_config.yaml"
    test_bot.config_file_path = str(config_path)

    # Add categories to sample config
    sample_config_with_categories = sample_config.copy()
    sample_config_with_categories["categories"] = {}

    # Create a synchronous version of close_browser_session to avoid coroutine warnings
    def sync_close_browser_session() -> None:
        """Synchronous version of close_browser_session to avoid coroutine warnings."""

    # Use patch.object instead of direct assignment
    with patch.object(test_bot, 'close_browser_session', sync_close_browser_session), \
            patch('kleinanzeigen_bot.utils.dicts.load_dict_if_exists', return_value=None), \
            patch.object(LOG, 'warning') as mock_warning, \
            patch('kleinanzeigen_bot.utils.dicts.save_dict') as mock_save, \
            patch('kleinanzeigen_bot.utils.dicts.load_dict_from_module') as mock_load_module:

        mock_load_module.side_effect = [
            sample_config_with_categories,  # config_defaults.yaml
            {'cat1': 'id1'},  # categories.yaml
            {'cat2': 'id2'}  # categories_old.yaml
        ]

        test_bot.load_config()
        mock_warning.assert_called_once()
        mock_save.assert_called_once_with(str(config_path), sample_config_with_categories)

        # Verify categories were loaded
        assert test_bot.categories == {'cat1': 'id1', 'cat2': 'id2'}
        assert test_bot.config == sample_config_with_categories


def test_load_config_validates_required_fields(
    test_bot: KleinanzeigenBotProtocol,
    test_data_dir: str
) -> None:
    """Verify that config validation checks required fields."""
    config_path = Path(test_data_dir) / "config.yaml"
    config_content = """
login:
  username: testuser
  # Missing password
browser:
  arguments: []
"""
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(config_content)
    test_bot.config_file_path = str(config_path)

    with pytest.raises(AssertionError) as exc_info:
        test_bot.load_config()
    assert "[login.password] not specified" in str(exc_info.value)


def test_load_config_with_categories(
    test_bot: KleinanzeigenBotProtocol,
    tmp_path: Path
) -> None:
    """Test loading config with custom categories."""
    config_path = Path(tmp_path) / "config.yaml"
    with open(config_path, "w", encoding="utf-8") as f:
        f.write("""
login:
    username: test
    password: test
categories:
    custom_cat: custom_id
""")
    test_bot.config_file_path = str(config_path)
    test_bot.load_config()
    assert 'custom_cat' in test_bot.categories
    assert test_bot.categories['custom_cat'] == 'custom_id'


def test_get_config_file_path(test_bot: KleinanzeigenBotProtocol) -> None:
    """Test config file path handling."""
    # The test_bot fixture already sets the config_file_path
    expected_path = test_bot.config_file_path
    assert test_bot.config_file_path == expected_path


def test_get_categories(test_bot: KleinanzeigenBotProtocol) -> None:
    """Test categories handling."""
    test_categories = {"test_cat": "test_id"}
    test_bot.categories = test_categories
    assert test_bot.categories == test_categories


@pytest.mark.asyncio
async def test_load_config(test_bot: KleinanzeigenBotProtocol) -> None:
    """Test load_config method."""
    mock_config = {
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
        "ad_files": ["ads/*.yaml"]
    }

    with patch('kleinanzeigen_bot.utils.dicts.load_dict_from_module', return_value={}):
        with patch('kleinanzeigen_bot.utils.dicts.load_dict_if_exists', return_value=mock_config):
            with patch('kleinanzeigen_bot.utils.dicts.apply_defaults', return_value=mock_config):
                with patch.object(LOG, 'info'):
                    test_bot.load_config()
                    assert test_bot.config == mock_config


@pytest.mark.asyncio
async def test_verify_command(test_bot: KleinanzeigenBotProtocol, tmp_path: Path) -> None:
    """Test verify command with minimal config."""
    config_path = Path(tmp_path) / "config.yaml"
    with open(config_path, "w", encoding="utf-8") as f:
        f.write("""
login:
    username: test
    password: test
""")
    test_bot.config_file_path = str(config_path)
    await test_bot.run(['script.py', 'verify'])
    assert test_bot.config['login']['username'] == 'test'


def test_load_config_yaml(test_bot: KleinanzeigenBotProtocol, tmp_path: Path) -> None:
    """Test loading configuration from a YAML file."""
    # Create a test YAML config file
    config_file = tmp_path / "config.yaml"
    yaml = YAML()
    yaml.dump({"login": {"username": "test", "password": "test"}}, config_file)

    # Set the config file path
    test_bot.config_file_path = str(config_file)

    # Load the config
    test_bot.load_config()

    # Verify the config was loaded correctly
    assert test_bot.config["login"]["username"] == "test"
    assert test_bot.config["login"]["password"] == "test"


def test_load_config_json(test_bot: KleinanzeigenBotProtocol, tmp_path: Path) -> None:
    """Test loading configuration from a JSON file."""
    # Create a test JSON config file
    config_file = tmp_path / "config.json"
    with open(config_file, "w", encoding="utf-8") as f:
        json.dump({"login": {"username": "test", "password": "test"}}, f)

    # Set the config file path
    test_bot.config_file_path = str(config_file)

    # Load the config
    test_bot.load_config()

    # Verify the config was loaded correctly
    assert test_bot.config["login"]["username"] == "test"
    assert test_bot.config["login"]["password"] == "test"


def test_load_config_file_not_found(test_bot: KleinanzeigenBotProtocol) -> None:
    """Test handling of a missing config file."""
    # Set a non-existent config file path
    test_bot.config_file_path = "/path/to/nonexistent/config.yaml"

    # Mock the file operations to avoid actual file system access
    with patch('kleinanzeigen_bot.utils.dicts.load_dict_if_exists', return_value=None), \
            patch('kleinanzeigen_bot.utils.dicts.load_dict_from_module') as mock_load_module, \
            patch('kleinanzeigen_bot.utils.dicts.save_dict'), \
            patch.object(LOG, 'warning'):

        # Mock the default config and categories
        mock_load_module.side_effect = [
            {
                "login": {"username": "default", "password": "default"},
                "categories": {},  # Add categories key
                "publishing": {    # Add publishing key
                    "delete_old_ads": "BEFORE_PUBLISH",
                    "delete_old_ads_by_title": False
                },
                "browser": {       # Add browser key
                    "arguments": [],
                    "binary_location": "",
                    "extensions": [],
                    "use_private_window": True,
                    "user_data_dir": "",
                    "profile_name": ""
                }
            },  # Default config
            {},  # Empty categories
            {}   # Empty old categories
        ]

        # Now the test should pass without raising SystemExit
        test_bot.load_config()

        # Verify the default config was used
        assert "login" in test_bot.config
        assert "username" in test_bot.config["login"]


def test_load_config_invalid_yaml(test_bot: KleinanzeigenBotProtocol, tmp_path: Path) -> None:
    """Test handling of an invalid YAML config file."""
    # Create an invalid YAML config file
    config_file = tmp_path / "config.yaml"
    with open(config_file, "w", encoding="utf-8") as f:
        f.write("invalid: yaml: content:")

    # Set the config file path
    test_bot.config_file_path = str(config_file)

    # Attempt to load the config, which should raise a YAML parsing error
    with pytest.raises(ScannerError):
        test_bot.load_config()


def test_load_config_invalid_json(test_bot: KleinanzeigenBotProtocol, tmp_path: Path) -> None:
    """Test handling of an invalid JSON config file."""
    # Create an invalid JSON config file
    config_file = tmp_path / "config.json"
    with open(config_file, "w", encoding="utf-8") as f:
        f.write("{invalid: json, content}")

    # Set the config file path
    test_bot.config_file_path = str(config_file)

    # Attempt to load the config, which should raise a JSON parsing error
    with pytest.raises(json.JSONDecodeError):
        test_bot.load_config()


def test_load_config_with_valid_config(test_bot: KleinanzeigenBotProtocol, tmp_path: Path) -> None:
    """Test loading a valid configuration file."""
    # Create a valid YAML config file with all required fields
    config_file = tmp_path / "config.yaml"
    config = {
        "login": {
            "username": "test_user",
            "password": "test_pass"
        },
        "browser": {
            "arguments": [],
            "binary_location": "",
            "extensions": [],
            "use_private_window": True,
            "user_data_dir": "",
            "profile_name": ""
        },
        "publishing": {
            "delete_old_ads": "BEFORE_PUBLISH",
            "delete_old_ads_by_title": False
        }
    }
    yaml = YAML()
    yaml.dump(config, config_file)

    # Set the config file path
    test_bot.config_file_path = str(config_file)

    # Load the config
    test_bot.load_config()

    # Verify the config was loaded correctly
    assert test_bot.config["login"]["username"] == "test_user"
    assert test_bot.config["login"]["password"] == "test_pass"


def test_load_config_with_missing_required_fields(test_bot: KleinanzeigenBotProtocol, tmp_path: Path) -> None:
    """Test loading a configuration file with missing required fields."""
    # Create a YAML config file with missing required fields
    config_file = tmp_path / "config.yaml"
    config = {
        "login": {
            # Missing password
            "username": "test_user"
        },
        "browser": {
            "arguments": []
            # Missing other browser fields
        }
        # Missing publishing section
    }
    yaml = YAML()
    yaml.dump(config, config_file)

    # Set the config file path
    test_bot.config_file_path = str(config_file)

    # Attempt to load the config, which should raise an assertion error
    with pytest.raises(AssertionError):
        test_bot.load_config()
