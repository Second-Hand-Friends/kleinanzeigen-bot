# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import pytest

from kleinanzeigen_bot.model.config_model import AdDefaults, Config, TimeoutConfig


def test_migrate_legacy_description_prefix() -> None:
    assert AdDefaults.model_validate({}).description_prefix == ""  # noqa: PLC1901 explicit empty check is clearer

    assert AdDefaults.model_validate({"description_prefix": "Prefix"}).description_prefix == "Prefix"

    assert AdDefaults.model_validate({"description_prefix": "Prefix", "description": {"prefix": "Legacy Prefix"}}).description_prefix == "Prefix"

    assert AdDefaults.model_validate({"description": {"prefix": "Legacy Prefix"}}).description_prefix == "Legacy Prefix"

    assert AdDefaults.model_validate({"description_prefix": "", "description": {"prefix": "Legacy Prefix"}}).description_prefix == "Legacy Prefix"


def test_migrate_legacy_description_suffix() -> None:
    assert AdDefaults.model_validate({}).description_suffix == ""  # noqa: PLC1901 explicit empty check is clearer

    assert AdDefaults.model_validate({"description_suffix": "Suffix"}).description_suffix == "Suffix"

    assert AdDefaults.model_validate({"description_suffix": "Suffix", "description": {"suffix": "Legacy Suffix"}}).description_suffix == "Suffix"

    assert AdDefaults.model_validate({"description": {"suffix": "Legacy Suffix"}}).description_suffix == "Legacy Suffix"

    assert AdDefaults.model_validate({"description_suffix": "", "description": {"suffix": "Legacy Suffix"}}).description_suffix == "Legacy Suffix"


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


def test_timeout_config_defaults_and_effective_values() -> None:
    cfg = Config.model_validate(
        {
            "login": {"username": "dummy", "password": "dummy"},  # noqa: S105
            "timeouts": {"multiplier": 2.0, "pagination_initial": 12.0, "retry_max_attempts": 3, "retry_backoff_factor": 2.0},
        }
    )

    timeouts = cfg.timeouts
    base = timeouts.resolve("pagination_initial")
    multiplier = timeouts.multiplier
    backoff = timeouts.retry_backoff_factor
    assert base == 12.0
    assert timeouts.effective("pagination_initial") == base * multiplier * (backoff**0)
    # attempt 1 should apply backoff factor once in addition to multiplier
    assert timeouts.effective("pagination_initial", attempt = 1) == base * multiplier * (backoff**1)


def test_validate_glob_pattern_rejects_blank_strings() -> None:
    with pytest.raises(ValueError, match = "must be a non-empty, non-blank glob pattern"):
        Config.model_validate(
            {"ad_files": ["   "], "ad_defaults": {"contact": {"name": "dummy", "zipcode": "12345"}}, "login": {"username": "dummy", "password": "dummy"}}
        )

    cfg = Config.model_validate(
        {"ad_files": ["*.yaml"], "ad_defaults": {"contact": {"name": "dummy", "zipcode": "12345"}}, "login": {"username": "dummy", "password": "dummy"}}
    )
    assert cfg.ad_files == ["*.yaml"]


def test_timeout_config_resolve_returns_specific_value() -> None:
    timeouts = TimeoutConfig(default = 4.0, page_load = 12.5)
    assert timeouts.resolve("page_load") == 12.5


def test_timeout_config_resolve_falls_back_to_default() -> None:
    timeouts = TimeoutConfig(default = 3.0)
    assert timeouts.resolve("nonexistent_key") == 3.0


def test_diagnostics_pause_requires_capture_validation() -> None:
    """
    Unit: DiagnosticsConfig validator ensures pause_on_login_detection_failure
    requires capture_on.login_detection to be enabled.
    """
    minimal_cfg = {
        "ad_defaults": {"contact": {"name": "dummy", "zipcode": "12345"}},
        "login": {"username": "dummy", "password": "dummy"},  # noqa: S105
        "publishing": {"delete_old_ads": "BEFORE_PUBLISH", "delete_old_ads_by_title": False},
    }

    valid_cfg = {**minimal_cfg, "diagnostics": {"capture_on": {"login_detection": True}, "pause_on_login_detection_failure": True}}
    config = Config.model_validate(valid_cfg)
    assert config.diagnostics is not None
    assert config.diagnostics.pause_on_login_detection_failure is True
    assert config.diagnostics.capture_on.login_detection is True

    invalid_cfg = {**minimal_cfg, "diagnostics": {"capture_on": {"login_detection": False}, "pause_on_login_detection_failure": True}}
    with pytest.raises(ValueError, match = "pause_on_login_detection_failure requires capture_on.login_detection to be enabled"):
        Config.model_validate(invalid_cfg)


def test_diagnostics_legacy_login_detection_capture_migration_when_capture_on_exists() -> None:
    """
    Unit: Test that legacy login_detection_capture is removed but doesn't overwrite explicit capture_on.login_detection.
    """
    minimal_cfg = {
        "ad_defaults": {"contact": {"name": "dummy", "zipcode": "12345"}},
        "login": {"username": "dummy", "password": "dummy"},  # noqa: S105
    }

    # When capture_on.login_detection is explicitly set to False, legacy True should be ignored
    cfg_with_explicit = {
        **minimal_cfg,
        "diagnostics": {
            "login_detection_capture": True,  # legacy key
            "capture_on": {"login_detection": False},  # explicit new key set to False
        },
    }
    config = Config.model_validate(cfg_with_explicit)
    assert config.diagnostics is not None
    assert config.diagnostics.capture_on.login_detection is False  # explicit value preserved


def test_diagnostics_legacy_publish_error_capture_migration_when_capture_on_exists() -> None:
    """
    Unit: Test that legacy publish_error_capture is removed but doesn't overwrite explicit capture_on.publish.
    """
    minimal_cfg = {
        "ad_defaults": {"contact": {"name": "dummy", "zipcode": "12345"}},
        "login": {"username": "dummy", "password": "dummy"},  # noqa: S105
    }

    # When capture_on.publish is explicitly set to False, legacy True should be ignored
    cfg_with_explicit = {
        **minimal_cfg,
        "diagnostics": {
            "publish_error_capture": True,  # legacy key
            "capture_on": {"publish": False},  # explicit new key set to False
        },
    }
    config = Config.model_validate(cfg_with_explicit)
    assert config.diagnostics is not None
    assert config.diagnostics.capture_on.publish is False  # explicit value preserved


def test_diagnostics_legacy_login_detection_capture_migration_when_capture_on_is_none() -> None:
    """
    Unit: Test that legacy login_detection_capture is migrated when capture_on is None.
    """
    minimal_cfg = {
        "ad_defaults": {"contact": {"name": "dummy", "zipcode": "12345"}},
        "login": {"username": "dummy", "password": "dummy"},  # noqa: S105
    }

    cfg_with_null_capture_on = {
        **minimal_cfg,
        "diagnostics": {
            "login_detection_capture": True,  # legacy key
            "capture_on": None,  # capture_on is explicitly None
        },
    }
    config = Config.model_validate(cfg_with_null_capture_on)
    assert config.diagnostics is not None
    assert config.diagnostics.capture_on.login_detection is True  # legacy value migrated


def test_diagnostics_legacy_publish_error_capture_migration_when_capture_on_is_none() -> None:
    """
    Unit: Test that legacy publish_error_capture is migrated when capture_on is None.
    """
    minimal_cfg = {
        "ad_defaults": {"contact": {"name": "dummy", "zipcode": "12345"}},
        "login": {"username": "dummy", "password": "dummy"},  # noqa: S105
    }

    cfg_with_null_capture_on = {
        **minimal_cfg,
        "diagnostics": {
            "publish_error_capture": True,  # legacy key
            "capture_on": None,  # capture_on is explicitly None
        },
    }
    config = Config.model_validate(cfg_with_null_capture_on)
    assert config.diagnostics is not None
    assert config.diagnostics.capture_on.publish is True  # legacy value migrated
