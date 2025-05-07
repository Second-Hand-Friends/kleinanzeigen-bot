# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import pytest  # isort: skip
from typing import Any

from kleinanzeigen_bot import KleinanzeigenBot


class TestConfig:

    @pytest.fixture
    def bot(self) -> KleinanzeigenBot:
        return KleinanzeigenBot()

    def test_find_new_config_fields(self, bot:KleinanzeigenBot) -> None:
        # Test case 1: Simple new field
        current_config:dict[str, Any] = {"existing": "value"}
        default_config:dict[str, Any] = {"existing": "value", "new_field": "default"}
        result = bot._find_new_config_fields(current_config, default_config)
        assert result == ["new_field"]

        # Test case 2: New field with subfields
        current_config = {"existing": "value"}
        default_config = {
            "existing": "value",
            "new_field": {
                "subfield1": "default1",
                "subfield2": "default2"
            }
        }
        result = bot._find_new_config_fields(current_config, default_config)
        assert sorted(result) == ["new_field", "new_field.subfield1", "new_field.subfield2"]

        # Test case 3: Nested new fields
        current_config = {
            "existing": {
                "field": "value"
            }
        }
        default_config = {
            "existing": {
                "field": "value",
                "new_field": "default"
            },
            "new_section": {
                "field1": "default1",
                "field2": "default2"
            }
        }
        result = bot._find_new_config_fields(current_config, default_config)
        assert sorted(result) == [
            "existing.new_field",
            "new_section",
            "new_section.field1",
            "new_section.field2"
        ]

    def test_add_new_config_fields(self, bot:KleinanzeigenBot) -> None:
        # Test case 1: Simple new field
        current_config:dict[str, Any] = {"existing": "value"}
        default_config:dict[str, Any] = {"existing": "value", "new_field": "default"}
        bot._add_new_config_fields(current_config, default_config)
        assert current_config == {"existing": "value", "new_field": "default"}

        # Test case 2: New field with subfields
        current_config = {"existing": "value"}
        default_config = {
            "existing": "value",
            "new_field": {
                "subfield1": "default1",
                "subfield2": "default2"
            }
        }
        bot._add_new_config_fields(current_config, default_config)
        assert current_config == {
            "existing": "value",
            "new_field": {
                "subfield1": "default1",
                "subfield2": "default2"
            }
        }

        # Test case 3: Nested new fields
        current_config = {
            "existing": {
                "field": "value"
            }
        }
        default_config = {
            "existing": {
                "field": "value",
                "new_field": "default"
            },
            "new_section": {
                "field1": "default1",
                "field2": "default2"
            }
        }
        bot._add_new_config_fields(current_config, default_config)
        assert current_config == {
            "existing": {
                "field": "value",
                "new_field": "default"
            },
            "new_section": {
                "field1": "default1",
                "field2": "default2"
            }
        }

    def test_add_new_config_fields_preserve_user_modified(self, bot:KleinanzeigenBot) -> None:
        # Test case: Preserve user-modified values
        current_config:dict[str, Any] = {
            "existing": {
                "field": "user_modified_value",
                "nested": {
                    "field": "user_modified_nested_value"
                }
            }
        }
        default_config:dict[str, Any] = {
            "existing": {
                "field": "default_value",
                "new_field": "default",
                "nested": {
                    "field": "default_nested_value",
                    "new_nested_field": "default_nested"
                }
            }
        }
        bot._add_new_config_fields(current_config, default_config)
        assert current_config == {
            "existing": {
                "field": "user_modified_value",  # Should preserve user value
                "new_field": "default",  # Should add new field
                "nested": {
                    "field": "user_modified_nested_value",  # Should preserve user value
                    "new_nested_field": "default_nested"  # Should add new nested field
                }
            }
        }
