"""Tests for description utility functions."""
from typing import Any

import pytest

from kleinanzeigen_bot.utils import description


@pytest.mark.parametrize("config,prefix,expected", [
    # Test new flattened format - prefix
    (
        {"ad_defaults": {"description_prefix": "Hello"}},
        True,
        "Hello"
    ),
    # Test new flattened format - suffix
    (
        {"ad_defaults": {"description_suffix": "Bye"}},
        False,
        "Bye"
    ),
    # Test legacy nested format - prefix
    (
        {"ad_defaults": {"description": {"prefix": "Hi"}}},
        True,
        "Hi"
    ),
    # Test legacy nested format - suffix
    (
        {"ad_defaults": {"description": {"suffix": "Ciao"}}},
        False,
        "Ciao"
    ),
    # Test precedence (new format over legacy) - prefix
    (
        {
            "ad_defaults": {
                "description_prefix": "Hello",
                "description": {"prefix": "Hi"}
            }
        },
        True,
        "Hello"
    ),
    # Test precedence (new format over legacy) - suffix
    (
        {
            "ad_defaults": {
                "description_suffix": "Bye",
                "description": {"suffix": "Ciao"}
            }
        },
        False,
        "Bye"
    ),
    # Test empty config
    (
        {"ad_defaults": {}},
        True,
        ""
    ),
    # Test None values
    (
        {"ad_defaults": {"description_prefix": None, "description_suffix": None}},
        True,
        ""
    ),
    # Test non-string values
    (
        {"ad_defaults": {"description_prefix": 123, "description_suffix": True}},
        True,
        ""
    ),
    # Add test for malformed config
    (
        {},  # Empty config
        True,
        ""
    ),
    # Test for missing ad_defaults
    (
        {"some_other_key": {}},
        True,
        ""
    ),
    # Test for non-dict ad_defaults
    (
        {"ad_defaults": "invalid"},
        True,
        ""
    ),
    # Test for invalid type in description field
    (
        {"ad_defaults": {"description": 123}},
        True,
        ""
    )
])
def test_get_description_affixes(
    config: dict[str, Any],
    prefix: bool,
    expected: str
) -> None:
    """Test get_description_affixes function with various inputs."""
    result = description.get_description_affixes(config, prefix)
    assert result == expected


@pytest.mark.parametrize("config,prefix,expected", [
    # Add test for malformed config
    (
        {},  # Empty config
        True,
        ""
    ),
    # Test for missing ad_defaults
    (
        {"some_other_key": {}},
        True,
        ""
    ),
    # Test for non-dict ad_defaults
    (
        {"ad_defaults": "invalid"},
        True,
        ""
    ),
    # Test for invalid type in description field
    (
        {"ad_defaults": {"description": 123}},
        True,
        ""
    )
])
def test_get_description_affixes_edge_cases(config: dict[str, Any], prefix: bool, expected: str) -> None:
    """Test edge cases for description affix handling."""
    assert description.get_description_affixes(config, prefix) == expected
