"""
SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""
from typing import Any

import pytest

from kleinanzeigen_bot import ads


def test_calculate_content_hash_with_none_values() -> None:
    """Test calculate_content_hash with None values in the ad configuration."""
    ad_cfg = {
        # Minimal configuration with None values as described in bug report
        "id": "123456789",
        "created_on": "2022-07-19T07:30:20.489289",
        "updated_on": "2025-01-22T19:46:46.735896",
        "title": "Test Ad",
        "description": "Test Description",
        "images": [None, "/path/to/image.jpg", None],  # List containing None values
        "shipping_options": None,  # None instead of list
        "special_attributes": None,  # None instead of dictionary
        "contact": {
            "street": None  # None value in contact
        }
    }

    # Should not raise TypeError
    hash_value = ads.calculate_content_hash(ad_cfg)
    assert isinstance(hash_value, str)
    assert len(hash_value) == 64  # SHA-256 hash is 64 characters long


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
    result = ads.get_description_affixes(config, prefix)
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
    assert ads.get_description_affixes(config, prefix) == expected


@pytest.mark.parametrize("config,expected", [
    (None, ""),  # Test with None
    ([], ""),    # Test with an empty list
    ("string", ""),  # Test with a string
    (123, ""),   # Test with an integer
    (3.14, ""),  # Test with a float
    (set(), ""),  # Test with an empty set
])
def test_get_description_affixes_edge_cases_non_dict(config: Any, expected: str) -> None:
    """Test get_description_affixes function with non-dict inputs."""
    result = ads.get_description_affixes(config, prefix=True)
    assert result == expected
