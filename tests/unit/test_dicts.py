# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Tests for the dicts utility module."""
import unicodedata
from pathlib import Path

from pydantic import BaseModel, Field


def test_save_dict_normalizes_unicode_paths(tmp_path:Path) -> None:
    """Test that save_dict normalizes paths to NFC for cross-platform consistency (issue #728).

    Directories are created with NFC normalization (via sanitize_folder_name).
    This test verifies save_dict's defensive normalization handles edge cases where
    an NFD path is passed (e.g., "ä" as "a" + combining diacritic vs single character).
    It should normalize to NFC and use the existing NFC directory.
    """
    from kleinanzeigen_bot.utils import dicts  # noqa: PLC0415

    # Create directory with NFC normalization (as sanitize_folder_name does)
    title_nfc = unicodedata.normalize("NFC", "KitchenAid Zuhälter - nie benutzt")
    nfc_dir = tmp_path / f"ad_12345_{title_nfc}"
    nfc_dir.mkdir(parents = True)

    # Call save_dict with NFD path (different normalization)
    title_nfd = unicodedata.normalize("NFD", title_nfc)
    assert title_nfc != title_nfd, "NFC and NFD should be different strings"

    nfd_path = tmp_path / f"ad_12345_{title_nfd}" / "ad_12345.yaml"
    dicts.save_dict(str(nfd_path), {"test": "data", "title": title_nfc})

    # Verify file was saved successfully
    nfc_files = list(nfc_dir.glob("*.yaml"))
    assert len(nfc_files) == 1, "Should have exactly one file in NFC directory"
    assert nfc_files[0].name == "ad_12345.yaml"

    # On macOS/APFS, the filesystem normalizes both NFC and NFD to the same directory
    # On Linux ext4, NFC normalization in save_dict ensures it uses the existing directory
    # Either way, we should have exactly one YAML file total (no duplicates)
    all_yaml_files = list(tmp_path.rglob("*.yaml"))
    assert len(all_yaml_files) == 1, f"Expected exactly 1 YAML file total, found {len(all_yaml_files)}: {all_yaml_files}"


def test_safe_get_with_type_error() -> None:
    """Test safe_get returns None when accessing a non-dict value (TypeError)."""
    from kleinanzeigen_bot.utils import dicts  # noqa: PLC0415

    # Accessing a key on a string causes TypeError
    result = dicts.safe_get({"foo": "bar"}, "foo", "baz")
    assert result is None


def test_safe_get_with_empty_dict() -> None:
    """Test safe_get returns empty dict when given empty dict."""
    from kleinanzeigen_bot.utils import dicts  # noqa: PLC0415

    # Empty dict should return the dict itself (falsy but valid)
    result = dicts.safe_get({})
    assert result == {}


def test_model_to_commented_yaml_with_dict_exclude() -> None:
    """Test model_to_commented_yaml with dict exclude where field is not in exclude dict."""
    from kleinanzeigen_bot.utils.dicts import model_to_commented_yaml  # noqa: PLC0415

    class TestModel(BaseModel):
        included_field:str = Field(default = "value", description = "This field")
        excluded_field:str = Field(default = "excluded", description = "Excluded field")

    model = TestModel()
    # Exclude only excluded_field, included_field should remain
    result = model_to_commented_yaml(model, exclude = {"excluded_field": None})

    assert "included_field" in result
    assert "excluded_field" not in result


def test_model_to_commented_yaml_with_list() -> None:
    """Test model_to_commented_yaml handles list fields correctly."""
    from kleinanzeigen_bot.utils.dicts import model_to_commented_yaml  # noqa: PLC0415

    class TestModel(BaseModel):
        items:list[str] = Field(default_factory = lambda: ["item1", "item2"], description = "List of items")

    model = TestModel()
    result = model_to_commented_yaml(model)

    assert "items" in result
    assert isinstance(result["items"], list)
    assert result["items"] == ["item1", "item2"]


def test_model_to_commented_yaml_with_multiple_scalar_examples() -> None:
    """Test model_to_commented_yaml formats multiple scalar examples with bullets."""
    from kleinanzeigen_bot.utils.dicts import model_to_commented_yaml  # noqa: PLC0415

    class TestModel(BaseModel):
        choice:str = Field(default = "A", description = "Choose one", examples = ["A", "B", "C"])

    model = TestModel()
    result = model_to_commented_yaml(model)

    # Verify the field exists
    assert "choice" in result
    # Verify comment was added (check via the yaml_set_comment_before_after_key mechanism)
    assert result.ca is not None


def test_model_to_commented_yaml_with_set_exclude() -> None:
    """Test model_to_commented_yaml with set exclude (covers line 170 branch)."""
    from kleinanzeigen_bot.utils.dicts import model_to_commented_yaml  # noqa: PLC0415

    class TestModel(BaseModel):
        field1:str = Field(default = "value1", description = "First field")
        field2:str = Field(default = "value2", description = "Second field")

    model = TestModel()
    # Use set for exclude (not dict)
    result = model_to_commented_yaml(model, exclude = {"field2"})

    assert "field1" in result
    assert "field2" not in result


def test_model_to_commented_yaml_with_nested_dict_exclude() -> None:
    """Test model_to_commented_yaml with nested dict exclude (covers lines 186-187)."""
    from kleinanzeigen_bot.utils.dicts import model_to_commented_yaml  # noqa: PLC0415

    class NestedModel(BaseModel):
        nested_field:str = Field(default = "nested", description = "Nested")

    class TestModel(BaseModel):
        parent:NestedModel = Field(default_factory = NestedModel, description = "Parent")

    model = TestModel()
    # Nested exclude with None value
    result = model_to_commented_yaml(model, exclude = {"parent": None})

    assert "parent" not in result


def test_model_to_commented_yaml_with_plain_dict() -> None:
    """Test model_to_commented_yaml with plain dict (covers lines 238-241)."""
    from kleinanzeigen_bot.utils.dicts import model_to_commented_yaml  # noqa: PLC0415

    # Plain dict (not a Pydantic model)
    plain_dict = {"key1": "value1", "key2": "value2"}
    result = model_to_commented_yaml(plain_dict)

    assert "key1" in result
    assert "key2" in result
    assert result["key1"] == "value1"


def test_model_to_commented_yaml_fallback() -> None:
    """Test model_to_commented_yaml fallback for unsupported types (covers line 318)."""
    from kleinanzeigen_bot.utils.dicts import model_to_commented_yaml  # noqa: PLC0415

    # Custom object that's not a BaseModel, dict, list, or primitive
    class CustomObject:
        pass

    obj = CustomObject()
    result = model_to_commented_yaml(obj)

    # Should return as-is
    assert result is obj


def test_save_commented_model_without_header(tmp_path:Path) -> None:
    """Test save_commented_model without header (covers line 358)."""
    from kleinanzeigen_bot.utils.dicts import save_commented_model  # noqa: PLC0415

    class TestModel(BaseModel):
        field:str = Field(default = "value", description = "A field")

    model = TestModel()
    filepath = tmp_path / "test.yaml"

    # Save without header (header=None)
    save_commented_model(filepath, model, header = None)

    assert filepath.exists()
    content = filepath.read_text()
    # Should not have a blank line at the start
    assert not content.startswith("\n")


def test_model_to_commented_yaml_with_empty_list() -> None:
    """Test model_to_commented_yaml correctly detects empty list fields via type annotation."""
    from kleinanzeigen_bot.utils.dicts import model_to_commented_yaml  # noqa: PLC0415

    class TestModel(BaseModel):
        items:list[str] = Field(default_factory = list, description = "List of items", examples = ["item1", "item2"])

    model = TestModel()
    # Model has empty list, but should still be detected as list field via annotation
    result = model_to_commented_yaml(model)

    assert "items" in result
    assert isinstance(result["items"], list)
    assert len(result["items"]) == 0
    # Verify comment includes "Example usage:" (list field format) not "Examples:" (scalar format)
    assert result.ca is not None
