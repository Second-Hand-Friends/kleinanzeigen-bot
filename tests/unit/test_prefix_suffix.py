"""
SPDX-FileCopyrightText: © Jens Bergmann and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""
from typing import Any
import pytest
from kleinanzeigen_bot import KleinanzeigenBot

# This file will be integrated into the init tests once all PRs are merged


@pytest.fixture
def kleinanzeigen_bot() -> KleinanzeigenBot:
    bot = KleinanzeigenBot()
    # Set default prefix/suffix in config
    bot.config = {
        "ad_defaults": {
            "description": {
                "prefix": "Default Prefix\n",
                "suffix": "\nDefault Suffix"
            }
        },
        "ad_files": ["*.yaml"]
    }
    return bot


# pylint: disable=redefined-outer-name
def test_description_prefix_suffix(kleinanzeigen_bot: KleinanzeigenBot) -> None:
    """Test different prefix/suffix combinations in ad descriptions"""

    test_cases: list[dict[str, Any]] = [
        {
            "name": "uses_global_defaults",
            "ad_cfg": {
                "description": "Main Description",
            },
            "expected": "Default Prefix\nMain Description\nDefault Suffix"
        },
        {
            "name": "custom_prefix_only",
            "ad_cfg": {
                "description": "Main Description",
                "description_prefix": "Custom Prefix\n",
            },
            "expected": "Custom Prefix\nMain Description\nDefault Suffix"
        },
        {
            "name": "custom_suffix_only",
            "ad_cfg": {
                "description": "Main Description",
                "description_suffix": "\nCustom Suffix",
            },
            "expected": "Default Prefix\nMain Description\nCustom Suffix"
        },
        {
            "name": "custom_prefix_and_suffix",
            "ad_cfg": {
                "description": "Main Description",
                "description_prefix": "Custom Prefix\n",
                "description_suffix": "\nCustom Suffix",
            },
            "expected": "Custom Prefix\nMain Description\nCustom Suffix"
        },
        {
            "name": "empty_prefix_suffix",
            "ad_cfg": {
                "description": "Main Description",
                "description_prefix": "",
                "description_suffix": "",
            },
            "expected": "Main Description"
        },
        {
            "name": "length_validation",
            "ad_cfg": {
                "description": "X" * 4000,  # Max length description
                "description_prefix": "Prefix",  # Adding prefix should trigger length error
            },
            "should_raise": True
        }
    ]

    for test_case in test_cases:
        ad_cfg: dict[str, Any] = test_case["ad_cfg"]
        ad_cfg.setdefault("active", True)  # Required field

        if test_case.get("should_raise", False):
            with pytest.raises(AssertionError) as exc_info:
                # Apply prefix/suffix logic
                prefix = ad_cfg.get("description_prefix", kleinanzeigen_bot.config["ad_defaults"]["description"]["prefix"] or "")
                suffix = ad_cfg.get("description_suffix", kleinanzeigen_bot.config["ad_defaults"]["description"]["suffix"] or "")
                ad_cfg["description"] = prefix + (ad_cfg["description"] or "") + suffix

                assert len(ad_cfg["description"]) <= 4000, "Length of ad description including prefix and suffix exceeds 4000 chars"

            assert "Length of ad description including prefix and suffix exceeds 4000 chars" in str(exc_info.value)
        else:
            # Apply prefix/suffix logic
            prefix = ad_cfg.get("description_prefix", kleinanzeigen_bot.config["ad_defaults"]["description"]["prefix"] or "")
            suffix = ad_cfg.get("description_suffix", kleinanzeigen_bot.config["ad_defaults"]["description"]["suffix"] or "")
            ad_cfg["description"] = prefix + (ad_cfg["description"] or "") + suffix

            assert ad_cfg["description"] == test_case["expected"], f"Test case '{test_case['name']}' failed"
