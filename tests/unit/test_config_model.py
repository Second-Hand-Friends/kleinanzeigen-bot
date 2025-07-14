# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
from kleinanzeigen_bot.model.config_model import AdDefaults, Config


def test_migrate_legacy_description_prefix() -> None:
    assert AdDefaults.model_validate({
    }).description_prefix is None

    assert AdDefaults.model_validate({
        "description_prefix": "Prefix"
    }).description_prefix == "Prefix"

    assert AdDefaults.model_validate({
        "description_prefix": "Prefix",
        "description": {
            "prefix": "Legacy Prefix"
        }
    }).description_prefix == "Prefix"

    assert AdDefaults.model_validate({
        "description": {
            "prefix": "Legacy Prefix"
        }
    }).description_prefix == "Legacy Prefix"

    assert AdDefaults.model_validate({
        "description_prefix": "",
        "description": {
            "prefix": "Legacy Prefix"
        }
    }).description_prefix == "Legacy Prefix"


def test_migrate_legacy_description_suffix() -> None:
    assert AdDefaults.model_validate({
    }).description_suffix is None

    assert AdDefaults.model_validate({
        "description_suffix": "Suffix"
    }).description_suffix == "Suffix"

    assert AdDefaults.model_validate({
        "description_suffix": "Suffix",
        "description": {
            "suffix": "Legacy Suffix"
        }
    }).description_suffix == "Suffix"

    assert AdDefaults.model_validate({
        "description": {
            "suffix": "Legacy Suffix"
        }
    }).description_suffix == "Legacy Suffix"

    assert AdDefaults.model_validate({
        "description_suffix": "",
        "description": {
            "suffix": "Legacy Suffix"
        }
    }).description_suffix == "Legacy Suffix"


def test_minimal_config_validation() -> None:
    """
    Unit: Minimal config validation.
    """
    minimal_cfg = {
        "ad_defaults": {"contact": {"name": "dummy", "zipcode": "12345"}},
        "login": {"username": "dummy", "password": "dummy"},
        "publishing": {"delete_old_ads": "BEFORE_PUBLISH", "delete_old_ads_by_title": False},
    }
    config = Config.model_validate(minimal_cfg)
    assert config.login.username == "dummy"
    assert config.login.password == "dummy"  # noqa: S105
