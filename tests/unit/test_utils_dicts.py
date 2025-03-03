"""
SPDX-FileCopyrightText: © Jens Bergmann and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

This module contains tests for the dicts.py utility module.
"""
import json, types
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
from ruamel.yaml import YAML

from kleinanzeigen_bot.utils import dicts


class TestApplyDefaults:
    """Tests for the apply_defaults function."""

    def test_apply_defaults_empty_target(self) -> None:
        """Test applying defaults to an empty target dictionary."""
        target: dict[str, Any] = {}
        defaults = {"key1": "value1", "key2": "value2"}

        result = dicts.apply_defaults(target, defaults)
        assert result == defaults
        assert target == defaults  # Target should be modified in-place

    def test_apply_defaults_existing_values(self) -> None:
        """Test that existing values in the target are not overwritten."""
        target: dict[str, Any] = {"key1": "existing_value"}
        defaults = {"key1": "default_value", "key2": "value2"}

        result = dicts.apply_defaults(target, defaults)
        assert result["key1"] == "existing_value"
        assert result["key2"] == "value2"

    def test_apply_defaults_nested_dicts(self) -> None:
        """Test applying defaults with nested dictionaries."""
        target: dict[str, Any] = {"level1": {"key1": "existing_value"}}
        defaults = {"level1": {"key1": "default_value", "key2": "value2"}}

        result = dicts.apply_defaults(target, defaults)
        assert result["level1"]["key1"] == "existing_value"
        assert result["level1"]["key2"] == "value2"

    def test_apply_defaults_with_ignore(self) -> None:
        """Test applying defaults with an ignore function."""
        target: dict[str, Any] = {"key1": "existing_value", "key2": ""}
        defaults = {"key1": "default_value", "key2": "default_value", "key3": "value3"}

        # Ignore key1 and any empty values
        def ignore_func(key: str, value: Any) -> bool:
            return bool(key == "key1" or value == "")

        result = dicts.apply_defaults(target, defaults, ignore=ignore_func)
        assert result["key1"] == "existing_value"
        assert result["key2"] == ""  # Should be ignored because it's empty
        assert result["key3"] == "value3"

    def test_apply_defaults_with_override(self) -> None:
        """Test applying defaults with an override function."""
        target: dict[str, Any] = {"key1": "existing_value", "key2": ""}
        defaults = {"key1": "default_value", "key2": "default_value", "key3": "value3"}

        # Override empty values - key parameter is required by the function signature but not used in this test
        def override_func(key: Any, value: Any) -> bool:
            return bool(value == "")

        result = dicts.apply_defaults(target, defaults, override=override_func)
        assert result["key1"] == "existing_value"
        assert result["key2"] == "default_value"  # Should be overridden because it's empty
        assert result["key3"] == "value3"

    def test_apply_defaults_complex_nested_case(self) -> None:
        """Test a complex case with nested dictionaries and both ignore and override functions."""
        target: dict[str, Any] = {
            "level1": {
                "key1": "existing_value",
                "key2": "",
                "level2": {
                    "key3": "existing_value",
                    "key4": ""
                }
            }
        }
        defaults = {
            "level1": {
                "key1": "default_value",
                "key2": "default_value",
                "level2": {
                    "key3": "default_value",
                    "key4": "default_value",
                    "key5": "value5"
                }
            }
        }

        # Ignore key1 at any level - value parameter is required by the function signature but not used in this test
        def ignore_func(key: str, value: Any) -> bool:
            return bool(key == "key1")

        # Override empty values - key parameter is required by the function signature but not used in this test
        def override_func(key: str, value: Any) -> bool:
            return bool(value == "")

        result = dicts.apply_defaults(target, defaults, ignore=ignore_func, override=override_func)
        assert result["level1"]["key1"] == "existing_value"  # Should be ignored
        assert result["level1"]["key2"] == ""
        assert result["level1"]["level2"]["key3"] == "existing_value"
        assert result["level1"]["level2"]["key4"] == ""
        assert result["level1"]["level2"]["key5"] == "value5"


class TestLoadDict:
    """Tests for the load_dict and related functions."""

    def test_load_dict_json(self, tmp_path: Path) -> None:
        """Test loading a dictionary from a JSON file."""
        test_dict = {"key1": "value1", "key2": "value2"}
        json_file = tmp_path / "test.json"
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(test_dict, f)

        result = dicts.load_dict(str(json_file))
        assert result == test_dict

    def test_load_dict_yaml(self, tmp_path: Path) -> None:
        """Test loading a dictionary from a YAML file."""
        test_dict = {"key1": "value1", "key2": "value2"}
        yaml_file = tmp_path / "test.yaml"
        with open(yaml_file, "w", encoding="utf-8") as f:
            YAML().dump(test_dict, f)

        result = dicts.load_dict(str(yaml_file))
        assert result == test_dict

    def test_load_dict_file_not_found(self) -> None:
        """Test loading a dictionary from a non-existent file."""
        with pytest.raises(FileNotFoundError):
            dicts.load_dict("non_existent_file.json")

    def test_load_dict_unsupported_extension(self, tmp_path: Path) -> None:
        """Test loading a dictionary from a file with an unsupported extension."""
        unsupported_file = tmp_path / "test.txt"
        unsupported_file.touch()

        with pytest.raises(ValueError, match="Unsupported file type"):
            dicts.load_dict(str(unsupported_file))

    def test_load_dict_if_exists_existing_file(self, tmp_path: Path) -> None:
        """Test loading a dictionary from an existing file using load_dict_if_exists."""
        test_dict = {"key1": "value1", "key2": "value2"}
        json_file = tmp_path / "test.json"
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(test_dict, f)

        result = dicts.load_dict_if_exists(str(json_file))
        assert result == test_dict

    def test_load_dict_if_exists_non_existent_file(self) -> None:
        """Test loading a dictionary from a non-existent file using load_dict_if_exists."""
        result = dicts.load_dict_if_exists("non_existent_file.json")
        assert result is None

    @patch("kleinanzeigen_bot.utils.dicts.get_resource_as_string")
    def test_load_dict_from_module_json(self, mock_get_resource: MagicMock) -> None:
        """Test loading a dictionary from a JSON resource in a module."""
        test_dict = {"key1": "value1", "key2": "value2"}
        mock_get_resource.return_value = json.dumps(test_dict)

        # Create a mock module
        mock_module = types.ModuleType("mock_module")
        result = dicts.load_dict_from_module(mock_module, "resource.json")
        assert result == test_dict
        mock_get_resource.assert_called_once_with(mock_module, "resource.json")

    @patch("kleinanzeigen_bot.utils.dicts.get_resource_as_string")
    def test_load_dict_from_module_yaml(self, mock_get_resource: MagicMock) -> None:
        """Test loading a dictionary from a YAML resource in a module."""
        test_dict = {"key1": "value1", "key2": "value2"}
        yaml_str = "key1: value1\nkey2: value2\n"
        mock_get_resource.return_value = yaml_str

        # Create a mock module
        mock_module = types.ModuleType("mock_module")
        result = dicts.load_dict_from_module(mock_module, "resource.yaml")
        assert result == test_dict
        mock_get_resource.assert_called_once_with(mock_module, "resource.yaml")

    def test_load_dict_from_module_unsupported_extension(self) -> None:
        """Test loading a dictionary from a resource with an unsupported extension."""
        # Create a mock module
        mock_module = types.ModuleType("mock_module")
        with pytest.raises(ValueError, match="Unsupported file type"):
            dicts.load_dict_from_module(mock_module, "resource.txt")


class TestSaveDict:
    """Tests for the save_dict function."""

    def test_save_dict_json(self, tmp_path: Path) -> None:
        """Test saving a dictionary to a JSON file."""
        test_dict = {"key1": "value1", "key2": "value2"}
        json_file = tmp_path / "test.json"

        dicts.save_dict(str(json_file), test_dict)

        with open(json_file, "r", encoding="utf-8") as f:
            loaded_dict = json.load(f)
        assert loaded_dict == test_dict

    def test_save_dict_yaml(self, tmp_path: Path) -> None:
        """Test saving a dictionary to a YAML file."""
        test_dict = {"key1": "value1", "key2": "value2"}
        yaml_file = tmp_path / "test.yaml"

        dicts.save_dict(str(yaml_file), test_dict)

        with open(yaml_file, "r", encoding="utf-8") as f:
            loaded_dict = YAML().load(f)
        assert loaded_dict == test_dict

    def test_save_dict_yaml_multiline_string(self, tmp_path: Path) -> None:
        """Test saving a dictionary with multiline strings to a YAML file."""
        test_dict = {
            "key1": "line1\nline2\nline3",
            "key2": "value2"
        }
        yaml_file = tmp_path / "test.yaml"

        dicts.save_dict(str(yaml_file), test_dict)

        with open(yaml_file, "r", encoding="utf-8") as f:
            loaded_dict = YAML().load(f)
        assert loaded_dict == test_dict


class TestSafeGet:
    """Tests for the safe_get function."""

    def test_safe_get_existing_keys(self) -> None:
        """Test getting a value from existing keys."""
        test_dict: dict[str, Any] = {"level1": {"level2": "value"}}

        result = dicts.safe_get(test_dict, "level1", "level2")
        assert result == "value"

    def test_safe_get_missing_key(self) -> None:
        """Test getting a value from a missing key."""
        test_dict: dict[str, Any] = {"level1": {}}

        result = dicts.safe_get(test_dict, "level1", "level2")
        assert result is None

    def test_safe_get_empty_dict(self) -> None:
        """Test getting a value from an empty dictionary."""
        test_dict: dict[str, Any] = {}

        result = dicts.safe_get(test_dict, "level1", "level2")
        assert result == {}

    def test_safe_get_none_dict(self) -> None:
        """Test getting a value from a None dictionary."""
        test_dict = None

        # Use cast to handle the type mismatch
        result = dicts.safe_get(cast(dict[str, Any], test_dict), "level1", "level2")
        assert result is None

    def test_safe_get_non_dict_value(self) -> None:
        """Test getting a value from a non-dict value."""
        test_dict: dict[str, Any] = {"level1": "not_a_dict"}

        result = dicts.safe_get(test_dict, "level1", "level2")
        assert result is None
