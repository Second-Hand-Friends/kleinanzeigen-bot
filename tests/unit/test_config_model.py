# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import pytest

from kleinanzeigen_bot.model import config_model
from kleinanzeigen_bot.model.config_model import DEFAULT_DOWNLOAD_DIR, AdDefaults, Config, TimeoutConfig


@pytest.mark.parametrize("field", ["prefix", "suffix"])
def test_migrate_legacy_description(field:str) -> None:
    top_key = f"description_{field}"

    # empty default
    assert getattr(AdDefaults.model_validate({}), top_key) == ""  # noqa: PLC1901

    # top-level key used directly
    assert getattr(AdDefaults.model_validate({top_key: "Value"}), top_key) == "Value"

    # top-level key takes precedence over legacy nested key
    assert (
        getattr(
            AdDefaults.model_validate({top_key: "Value", "description": {field: "Legacy"}}),
            top_key,
        )
        == "Value"
    )

    # legacy nested key migrates when no top-level key present
    assert (
        getattr(
            AdDefaults.model_validate({"description": {field: "Legacy"}}),
            top_key,
        )
        == "Legacy"
    )

    # empty top-level falls back to legacy nested key
    assert (
        getattr(
            AdDefaults.model_validate({top_key: "", "description": {field: "Legacy"}}),
            top_key,
        )
        == "Legacy"
    )


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


def test_download_config_defaults_to_workspace_download_dir_literal() -> None:
    cfg = Config.model_validate(
        {
            "login": {"username": "dummy", "password": "dummy"},
        }
    )
    assert cfg.download.dir == DEFAULT_DOWNLOAD_DIR


def test_download_config_accepts_custom_dir_and_trims_whitespace() -> None:
    cfg = Config.model_validate(
        {
            "download": {"dir": "  ./ads  "},
            "login": {"username": "dummy", "password": "dummy"},
        }
    )
    assert cfg.download.dir == "./ads"


def test_download_config_accepts_custom_dir_and_templates() -> None:
    cfg = Config.model_validate(
        {
            "download": {
                "dir": "  ./ads  ",
                "folder_name_template": "  listing_{id}_{title}  ",
                "ad_file_name_template": "  listing_{id}_{title}  ",
            },
            "login": {"username": "dummy", "password": "dummy"},
        }
    )

    assert cfg.download.dir == "./ads"
    assert cfg.download.folder_name_template == "listing_{id}_{title}"
    assert cfg.download.ad_file_name_template == "listing_{id}_{title}"


def test_download_config_rejects_null_dir() -> None:
    with pytest.raises(ValueError, match = r"download\.dir\s+Input should be a valid string"):
        Config.model_validate(
            {
                "download": {"dir": None},
                "login": {"username": "dummy", "password": "dummy"},
            }
        )


def test_download_config_rejects_blank_dir() -> None:
    with pytest.raises(ValueError, match = "download.dir must be a non-empty path"):
        Config.model_validate(
            {
                "download": {"dir": "   "},
                "login": {"username": "dummy", "password": "dummy"},
            }
        )


def test_download_config_rejects_blank_folder_name_template() -> None:
    with pytest.raises(ValueError, match = "download.folder_name_template must be a non-empty template"):
        Config.model_validate(
            {
                "download": {"folder_name_template": "   "},
                "login": {"username": "dummy", "password": "dummy"},
            }
        )


def test_download_config_rejects_literal_only_folder_name_template() -> None:
    with pytest.raises(ValueError, match = r"download\.folder_name_template must include placeholder\(s\): \{id\}"):
        Config.model_validate(
            {
                "download": {"folder_name_template": "ads"},
                "login": {"username": "dummy", "password": "dummy"},
            }
        )


def test_download_config_rejects_title_only_folder_name_template() -> None:
    with pytest.raises(ValueError, match = r"download\.folder_name_template must include placeholder\(s\): \{id\}"):
        Config.model_validate(
            {
                "download": {"folder_name_template": "{title}"},
                "login": {"username": "dummy", "password": "dummy"},
            }
        )


def test_download_config_rejects_invalid_folder_name_template_placeholder() -> None:
    with pytest.raises(ValueError, match = r"download\.folder_name_template only supports placeholders: \{id\}, \{title\}"):
        Config.model_validate(
            {
                "download": {"folder_name_template": "{slug}_{id}"},
                "login": {"username": "dummy", "password": "dummy"},
            }
        )


def test_validate_download_template_rejects_missing_required_placeholder() -> None:
    with pytest.raises(ValueError, match = r"download\.ad_file_name_template must include placeholder\(s\): \{id\}"):
        Config.model_validate(
            {
                "download": {"ad_file_name_template": "{title}"},
                "login": {"username": "dummy", "password": "dummy"},
            }
        )


def test_validate_download_template_rejects_literal_without_required_placeholder() -> None:
    with pytest.raises(ValueError, match = r"download\.ad_file_name_template must include placeholder\(s\): \{id\}"):
        Config.model_validate(
            {
                "download": {"ad_file_name_template": "listing"},
                "login": {"username": "dummy", "password": "dummy"},
            }
        )


def test_download_config_rejects_path_separators_in_ad_file_name_template() -> None:
    with pytest.raises(ValueError, match = "download.ad_file_name_template must not contain path separators"):
        Config.model_validate(
            {
                "download": {"ad_file_name_template": "nested/{id}"},
                "login": {"username": "dummy", "password": "dummy"},
            }
        )


def test_download_config_rejects_path_separators_in_folder_name_template() -> None:
    with pytest.raises(ValueError, match = "download.folder_name_template must not contain path separators"):
        Config.model_validate(
            {
                "download": {"folder_name_template": "nested/{id}"},
                "login": {"username": "dummy", "password": "dummy"},
            }
        )


def test_download_config_rejects_format_spec_in_template() -> None:
    with pytest.raises(ValueError, match = "download.ad_file_name_template placeholders must not use format specifiers"):
        Config.model_validate(
            {
                "download": {"ad_file_name_template": "listing_{id:10}"},
                "login": {"username": "dummy", "password": "dummy"},
            }
        )


def test_download_config_rejects_conversion_in_template() -> None:
    with pytest.raises(ValueError, match = "download.folder_name_template placeholders must not use conversion flags"):
        Config.model_validate(
            {
                "download": {"folder_name_template": "{title!r}"},
                "login": {"username": "dummy", "password": "dummy"},
            }
        )


def test_download_config_rejects_invalid_template_syntax() -> None:
    with pytest.raises(ValueError, match = "download.ad_file_name_template contains invalid template syntax"):
        Config.model_validate(
            {
                "download": {"ad_file_name_template": "listing_{id"},
                "login": {"username": "dummy", "password": "dummy"},
            }
        )


def test_download_config_rejects_empty_placeholder() -> None:
    with pytest.raises(ValueError, match = "download.folder_name_template contains an empty placeholder"):
        Config.model_validate(
            {
                "download": {"folder_name_template": "ad_{}"},
                "login": {"username": "dummy", "password": "dummy"},
            }
        )


def test_download_config_rejects_repeated_id_in_folder_template() -> None:
    """Template with repeated {id} should be rejected."""
    with pytest.raises(ValueError, match = r"download\.folder_name_template may contain at most one \{id\} placeholder"):
        Config.model_validate(
            {
                "download": {"folder_name_template": "{id}_{id}"},
                "login": {"username": "dummy", "password": "dummy"},
            }
        )


def test_download_config_rejects_repeated_title_in_folder_template() -> None:
    """Template with repeated {title} should be rejected."""
    with pytest.raises(ValueError, match = r"download\.folder_name_template may contain at most one \{title\} placeholder"):
        Config.model_validate(
            {
                "download": {"folder_name_template": "{id}_{title}_{title}"},
                "login": {"username": "dummy", "password": "dummy"},
            }
        )


def test_download_config_rejects_repeated_id_in_ad_file_template() -> None:
    """Ad file template with repeated {id} should be rejected."""
    with pytest.raises(ValueError, match = r"download\.ad_file_name_template may contain at most one \{id\} placeholder"):
        Config.model_validate(
            {
                "download": {"ad_file_name_template": "{id}_{id}"},
                "login": {"username": "dummy", "password": "dummy"},
            }
        )


def test_download_config_rejects_repeated_title_in_ad_file_template() -> None:
    """Ad file template with repeated {title} should be rejected."""
    with pytest.raises(ValueError, match = r"download\.ad_file_name_template may contain at most one \{title\} placeholder"):
        Config.model_validate(
            {
                "download": {"ad_file_name_template": "{id}_{title}_{title}"},
                "login": {"username": "dummy", "password": "dummy"},
            }
        )


def test_validate_download_template_rejects_literal_only_when_no_required_fields() -> None:
    with pytest.raises(ValueError, match = r"TestField must include at least one placeholder: \{id\}, \{title\}"):
        config_model._validate_download_template(
            "literal_only",
            allowed_fields = frozenset({"id", "title"}),
            required_fields = frozenset(),
            field_name = "TestField",
        )


def test_timeout_config_resolve_returns_specific_value() -> None:
    timeouts = TimeoutConfig(default = 4.0, page_load = 12.5)
    assert timeouts.resolve("page_load") == 12.5


def test_timeout_config_resolve_falls_back_to_default() -> None:
    timeouts = TimeoutConfig(default = 3.0)
    assert timeouts.resolve("nonexistent_key") == 3.0


@pytest.fixture
def minimal_config() -> dict[str, object]:
    """Minimal valid config dict for diagnostics-related tests."""
    return {
        "ad_defaults": {"contact": {"name": "dummy", "zipcode": "12345"}},
        "login": {"username": "dummy", "password": "dummy"},  # noqa: S105
    }


def test_diagnostics_pause_requires_capture_validation(minimal_config:dict[str, object]) -> None:
    """
    Unit: DiagnosticsConfig validator ensures pause_on_login_detection_failure
    requires capture_on.login_detection to be enabled.
    """
    valid_cfg = {**minimal_config, "diagnostics": {"capture_on": {"login_detection": True}, "pause_on_login_detection_failure": True}}
    config = Config.model_validate(valid_cfg)
    assert config.diagnostics is not None
    assert config.diagnostics.pause_on_login_detection_failure is True
    assert config.diagnostics.capture_on.login_detection is True

    invalid_cfg = {**minimal_config, "diagnostics": {"capture_on": {"login_detection": False}, "pause_on_login_detection_failure": True}}
    with pytest.raises(ValueError, match = "pause_on_login_detection_failure requires capture_on.login_detection to be enabled"):
        Config.model_validate(invalid_cfg)


@pytest.mark.parametrize(
    ("legacy_key", "capture_attr", "capture_on_value", "expected"),
    [
        pytest.param(
            "login_detection_capture",
            "login_detection",
            {"login_detection": False},
            False,
            id = "login_detection-ignored-when-capture_on-present",
        ),
        pytest.param(
            "publish_error_capture",
            "publish",
            {"publish": False},
            False,
            id = "publish-ignored-when-capture_on-present",
        ),
        pytest.param(
            "login_detection_capture",
            "login_detection",
            None,
            True,
            id = "login_detection-migrated-when-capture_on-none",
        ),
        pytest.param(
            "publish_error_capture",
            "publish",
            None,
            True,
            id = "publish-migrated-when-capture_on-none",
        ),
    ],
)
def test_diagnostics_legacy_capture_migration(
    minimal_config:dict[str, object],
    legacy_key:str,
    capture_attr:str,
    capture_on_value:dict[str, bool] | None,
    expected:bool,
) -> None:
    cfg = {
        **minimal_config,
        "diagnostics": {
            legacy_key: True,
            "capture_on": capture_on_value,
        },
    }
    config = Config.model_validate(cfg)
    assert config.diagnostics is not None
    assert getattr(config.diagnostics.capture_on, capture_attr) == expected


def test_deleting_config_defaults_to_none(minimal_config:dict[str, object]) -> None:
    config = Config.model_validate(minimal_config)
    assert config.deleting.after_delete == "NONE"


@pytest.mark.parametrize("value", ["NONE", "RESET", "DISABLE"])
def test_deleting_config_accepts_valid_values(minimal_config:dict[str, object], value:str) -> None:
    cfg = {**minimal_config, "deleting": {"after_delete": value}}
    config = Config.model_validate(cfg)
    assert config.deleting.after_delete == value


def test_deleting_config_rejects_invalid_value(minimal_config:dict[str, object]) -> None:
    cfg = {**minimal_config, "deleting": {"after_delete": "INVALID"}}
    with pytest.raises(Exception, match = "after_delete"):
        Config.model_validate(cfg)
