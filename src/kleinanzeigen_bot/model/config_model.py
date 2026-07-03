# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
from __future__ import annotations

import copy
from gettext import gettext as _
from string import Formatter
from typing import Annotated, Any, Final, Literal

from pydantic import AfterValidator, Field, field_validator, model_validator
from typing_extensions import deprecated

from kleinanzeigen_bot.model.update_check_model import UpdateCheckConfig
from kleinanzeigen_bot.utils import dicts, loggers
from kleinanzeigen_bot.utils.misc import get_attr
from kleinanzeigen_bot.utils.pydantics import ContextualModel

LOG:Final[loggers.Logger] = loggers.get_logger(__name__)

_MAX_PERCENTAGE:Final[int] = 100
_FIELD_NAME_PREFIX:Final[str] = "download."
_DOWNLOAD_TEMPLATE_ALLOWED_FIELDS:Final[frozenset[str]] = frozenset({"id", "title"})
DEFAULT_DOWNLOAD_DIR:Final[str] = "downloaded-ads"


class AutoPriceReductionConfig(ContextualModel):
    enabled:bool = Field(default = False, description = "automatically lower the price of reposted ads")
    strategy:Literal["FIXED", "PERCENTAGE"] | None = Field(
        default = None,
        description = "reduction strategy (required when enabled: true). PERCENTAGE = % of price, FIXED = absolute amount",
        examples = ["PERCENTAGE", "FIXED"],
    )
    amount:float | None = Field(
        default = None,
        gt = 0,
        description = "reduction amount (required when enabled: true). For PERCENTAGE: use percent value (e.g., 10 = 10%%). For FIXED: use currency amount",
        examples = [10.0, 5.0, 20.0],
    )
    min_price:float | None = Field(
        default = None, ge = 0, description = "minimum price floor (required when enabled: true). Use 0 for no minimum", examples = [1.0, 5.0, 10.0]
    )
    delay_reposts:int = Field(default = 0, ge = 0, description = "number of reposts to wait before applying the first automatic price reduction")
    delay_days:int = Field(default = 0, ge = 0, description = "number of days to wait after publication before applying automatic price reductions")
    on_update:bool = Field(
        default = False,
        description = "also apply automatic price reduction during update runs (MODIFY mode). delay_days applies, delay_reposts is ignored",
    )

    @model_validator(mode = "after")
    def _validate_config(self) -> "AutoPriceReductionConfig":
        if self.enabled:
            if self.strategy is None:
                raise ValueError(_("strategy must be specified when auto_price_reduction is enabled"))
            if self.amount is None:
                raise ValueError(_("amount must be specified when auto_price_reduction is enabled"))
            if self.min_price is None:
                raise ValueError(_("min_price must be specified when auto_price_reduction is enabled"))
            if self.strategy == "PERCENTAGE" and self.amount > _MAX_PERCENTAGE:
                raise ValueError(_("Percentage reduction amount must not exceed %s") % _MAX_PERCENTAGE)
        return self


class ContactDefaults(ContextualModel):
    name:str = Field(default = "", description = "contact name displayed on the ad")
    street:str = Field(default = "", description = "street address for the listing")
    zipcode:int | str = Field(default = "", description = "postal/ZIP code for the listing location")
    location:str = Field(
        default = "",
        description = "city or locality of the listing (can include multiple districts)",
        examples = ["Sample Town - District One"],
    )
    phone:str = Field(
        default = "",
        description = "phone number for contact - only available for commercial accounts, personal accounts no longer support this",
        examples = ['"01234 567890"'],
    )


@deprecated("Use description_prefix/description_suffix instead")
class DescriptionAffixes(ContextualModel):
    prefix:str | None = Field(default = None, description = "text to prepend to the ad description (deprecated, use description_prefix)")
    suffix:str | None = Field(default = None, description = "text to append to the ad description (deprecated, use description_suffix)")


class AdDefaults(ContextualModel):
    active:bool = Field(default = True, description = "whether the ad should be published (false = skip this ad)")
    type:Literal["OFFER", "WANTED"] = Field(default = "OFFER", description = "type of the ad listing", examples = ["OFFER", "WANTED"])
    description:DescriptionAffixes | None = Field(default = None, description = "DEPRECATED: Use description_prefix/description_suffix instead")
    description_prefix:str | None = Field(default = "", description = "text to prepend to each ad (optional)")
    description_suffix:str | None = Field(default = "", description = "text to append to each ad (optional)")
    price_type:Literal["FIXED", "NEGOTIABLE", "GIVE_AWAY", "NOT_APPLICABLE"] = Field(
        default = "NEGOTIABLE", description = "pricing strategy for the listing", examples = ["FIXED", "NEGOTIABLE", "GIVE_AWAY", "NOT_APPLICABLE"]
    )
    auto_price_reduction:AutoPriceReductionConfig = Field(
        default_factory = AutoPriceReductionConfig, description = "automatic price reduction configuration for reposted ads"
    )
    shipping_type:Literal["PICKUP", "SHIPPING", "NOT_APPLICABLE"] = Field(
        default = "SHIPPING", description = "shipping method for the item", examples = ["PICKUP", "SHIPPING", "NOT_APPLICABLE"]
    )
    sell_directly:bool = Field(
        default = False,
        description = "enable direct purchase option (requires shipping_type: SHIPPING, non-empty shipping_options, and price_type FIXED or NEGOTIABLE)",
    )
    images:list[str] | None = Field(
        default_factory = list,
        description = "default image glob patterns (optional). Leave empty for no default images",
        examples = ['"images/*.jpg"', '"photos/*.{png,jpg}"'],
    )
    contact:ContactDefaults = Field(default_factory = ContactDefaults, description = "default contact information for ads")
    republication_interval:int = Field(default = 7, description = "number of days between automatic republication of ads")

    @model_validator(mode = "before")
    @classmethod
    def migrate_legacy_description(cls, values:dict[str, Any]) -> dict[str, Any]:
        # Ensure flat prefix/suffix take precedence over deprecated nested "description"
        description_prefix = values.get("description_prefix")
        description_suffix = values.get("description_suffix")
        legacy_prefix = get_attr(values, "description.prefix")
        legacy_suffix = get_attr(values, "description.suffix")

        if not description_prefix and legacy_prefix is not None:
            values["description_prefix"] = legacy_prefix
        if not description_suffix and legacy_suffix is not None:
            values["description_suffix"] = legacy_suffix
        return values


class DownloadConfig(ContextualModel):
    dir:str = Field(
        default = DEFAULT_DOWNLOAD_DIR,
        description=(
            "directory where downloaded ads are written. "
            "The default literal 'downloaded-ads' uses workspace-specific resolution; "
            "custom relative paths are resolved against the config file location"
        ),
        examples = ['"downloaded-ads"', '"./ads"'],
    )
    include_all_matching_shipping_options:bool = Field(
        default = False,
        description = "if true, all shipping options matching the package size will be included",
    )
    excluded_shipping_options:list[str] = Field(
        default_factory = list,
        description = ("shipping options to exclude (optional). Leave as [] to include all. Add items like 'DHL_2' to exclude specific carriers"),
        examples = ['"DHL_2"', '"DHL_5"', '"Hermes"'],
    )
    folder_name_max_length:int = Field(
        default = 100,
        ge = 10,
        le = 255,
        description = "maximum length for downloaded folder names (default: 100). does not limit downloaded file base names",
    )
    folder_name_template:str = Field(
        default = "ad_{id}_{title}",
        description=(
            'template for downloaded ad folder names. Default: "ad_{id}_{title}". '
            "Text outside {id} and {title} is copied literally. "
            "Allowed placeholders: {id}, {title}. "
            "Each placeholder may appear at most once. "
            "Template must include {id}; {title} is optional"
        ),
        examples = ['"ad_{id}_{title}"', '"ad_{id} {title}"', '"{title} ({id})"', '"{id}"'],
    )
    ad_file_name_template:str = Field(
        default = "ad_{id}",
        description=(
            'template for the downloaded ad YAML stem and image prefix. Default: "ad_{id}". '
            "The bot writes the ad config as <base>.yaml and downloaded images as <base>__imgN.<ext>. "
            "Text outside {id} and {title} is copied literally. "
            "Supported placeholders: {id}, {title}. "
            "Each placeholder may appear at most once. "
            "Template must include {id}; {title} is optional. "
            "Long titles may be truncated to keep filename limits"
        ),
        examples = ['"ad_{id}"', '"ad_{id} {title}"', '"{title} ({id})"', '"{id}"'],
    )
    rename_existing_folders:bool = Field(
        default = False,
        description = "if true, rename existing folders without titles to include titles (default: false)",
    )
    preserve_local_settings:bool = Field(
        default = True,
        description = (
            "if true, preserves local-only settings (auto_price_reduction, republication_interval, "
            "repost_count, price_reduction_count) when re-downloading an already saved ad. "
            "Useful for picking up live changes without losing local configuration."
        ),
    )

    @field_validator("dir")
    @classmethod
    def _validate_dir(cls, value:str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError(_("download.dir must be a non-empty path"))
        return trimmed

    @model_validator(mode = "after")
    def _validate_templates(self) -> "DownloadConfig":
        self.folder_name_template = _validate_download_template(
            self.folder_name_template,
            allowed_fields = _DOWNLOAD_TEMPLATE_ALLOWED_FIELDS,
            required_fields = frozenset({"id"}),
            field_name = f"{_FIELD_NAME_PREFIX}folder_name_template",
        )
        self.ad_file_name_template = _validate_download_template(
            self.ad_file_name_template,
            allowed_fields = _DOWNLOAD_TEMPLATE_ALLOWED_FIELDS,
            required_fields = frozenset({"id"}),
            field_name = f"{_FIELD_NAME_PREFIX}ad_file_name_template",
        )
        return self


class BrowserConfig(ContextualModel):
    arguments:list[str] = Field(
        default_factory = list,
        description=(
            "additional Chromium command line switches (optional). Leave as [] for default behavior. "
            "See https://peter.sh/experiments/chromium-command-line-switches/ "
            "Common: --headless (no GUI), --disable-dev-shm-usage (Docker fix), --user-data-dir=/path"
        ),
        examples = ['"--headless"', '"--disable-dev-shm-usage"', '"--user-data-dir=/path/to/profile"'],
    )
    binary_location:str | None = Field(default = "", description = "path to custom browser executable (optional). Leave empty to use system default")
    extensions:list[str] = Field(
        default_factory = list,
        description = "Chrome extensions to load (optional). Leave as [] for no extensions. Add .crx file paths relative to config file",
        examples = ['"extensions/adblock.crx"', '"/absolute/path/to/extension.crx"'],
    )
    use_private_window:bool = Field(default = True, description = "open browser in private/incognito mode (recommended to avoid cookie conflicts)")
    user_data_dir:str | None = Field(
        default = "",
        description = "custom browser profile directory (optional). Leave empty for auto-configured default",
    )
    profile_name:str | None = Field(
        default = "",
        description = "browser profile name (optional). Leave empty for default profile",
        examples = ['"Profile 1"'],
    )


class LoginConfig(ContextualModel):
    username:str = Field(..., min_length = 1, description = "kleinanzeigen.de login email or username")
    password:str = Field(..., min_length = 1, description = "kleinanzeigen.de login password")


class LocalPathRenamingConfig(ContextualModel):
    mode:Literal["OFF", "TEMPLATE_MATCH"] = Field(
        default = "OFF",
        description=(
            "rename local ad files/folders after a successful publish changes the ad ID. "
            "OFF keeps existing paths unchanged. "
            "TEMPLATE_MATCH only renames paths whose names match the structure defined by "
            "download.folder_name_template and download.ad_file_name_template. "
            "It replaces any numeric value inside the {id} slot and preserves all other text "
            "(including user-edited or previously truncated titles), "
            "so changing the download templates also controls which local paths are eligible for renaming."
        ),
        examples = ["OFF", "TEMPLATE_MATCH"],
    )


class PublishingConfig(ContextualModel):
    delete_old_ads:Literal["BEFORE_PUBLISH", "AFTER_PUBLISH", "NEVER"] | None = Field(
        default = "AFTER_PUBLISH", description = "when to delete old versions of republished ads", examples = ["BEFORE_PUBLISH", "AFTER_PUBLISH", "NEVER"]
    )
    delete_old_ads_by_title:bool = Field(default = True, description = "match old ads by title when deleting (only works with BEFORE_PUBLISH)")
    local_path_renaming:LocalPathRenamingConfig = Field(
        default_factory = LocalPathRenamingConfig,
        description = (
            "local file and folder rename behavior after a successful publish changes the ad ID. "
            "When TEMPLATE_MATCH is enabled, the download.folder_name_template and "
            "download.ad_file_name_template are used to determine which paths qualify for renaming — "
            "only paths whose names match the template structure are updated."
        ),
    )


class DeletingConfig(ContextualModel):
    after_delete:Literal["NONE", "RESET", "DISABLE"] = Field(
        default = "NONE",
        description = "what to do with the local ad YAML after a delete attempt (applies to both 200 and 404 responses)",
        examples = ["NONE", "RESET", "DISABLE"],
    )


class CaptchaConfig(ContextualModel):
    auto_restart:bool = Field(
        default = False, description = "if true, abort when captcha is detected and auto-retry after restart_delay (if false, wait for manual solving)"
    )
    restart_delay:str = Field(
        default = "6h", description = "duration to wait before retrying after captcha detection (e.g., 1h30m, 6h, 30m)", examples = ["6h", "1h30m", "30m"]
    )


class TimeoutConfig(ContextualModel):
    multiplier:float = Field(default = 1.0, ge = 0.1, description = "Global multiplier applied to all timeout values.")
    default:float = Field(default = 5.0, ge = 0.0, description = "Baseline timeout for DOM interactions.")
    page_load:float = Field(default = 15.0, ge = 1.0, description = "Page load timeout for web_open.")
    captcha_detection:float = Field(default = 2.0, ge = 0.1, description = "Timeout for captcha iframe detection.")
    sms_verification:float = Field(default = 5.0, ge = 0.1, description = "Timeout for SMS verification prompts.")
    email_verification:float = Field(default = 5.0, ge = 0.1, description = "Timeout for email verification prompts.")
    login_detection:float = Field(default = 12.0, ge = 1.0, description = "Timeout for detecting existing login session via DOM elements.")
    publishing_result:float = Field(default = 300.0, ge = 10.0, description = "Timeout for publishing result checks.")
    publishing_confirmation:float = Field(default = 20.0, ge = 1.0, description = "Timeout for publish confirmation redirect.")
    image_upload:float = Field(default = 30.0, ge = 5.0, description = "Timeout for image upload and server-side processing.")
    pagination_initial:float = Field(default = 10.0, ge = 1.0, description = "Timeout for initial pagination lookup.")
    pagination_follow_up:float = Field(default = 5.0, ge = 1.0, description = "Timeout for subsequent pagination navigation.")
    quick_dom:float = Field(default = 2.0, ge = 0.1, description = "Generic short timeout for transient UI.")
    update_check:float = Field(default = 10.0, ge = 1.0, description = "Timeout for GitHub update checks.")
    chrome_remote_probe:float = Field(default = 2.0, ge = 0.1, description = "Timeout for local remote-debugging probes.")
    chrome_remote_debugging:float = Field(default = 5.0, ge = 1.0, description = "Timeout for remote debugging API calls.")
    chrome_binary_detection:float = Field(default = 10.0, ge = 1.0, description = "Timeout for chrome --version subprocesses.")
    retry_enabled:bool = Field(default = True, description = "Enable built-in retry/backoff for DOM operations.")
    retry_max_attempts:int = Field(default = 2, ge = 1, description = "Max retry attempts when retry is enabled.")
    retry_backoff_factor:float = Field(default = 1.5, ge = 1.0, description = "Exponential factor applied per retry attempt.")

    def resolve(self, key:str = "default", override:float | None = None) -> float:
        """
        Return the base timeout (seconds) for the given key without applying modifiers.
        """
        if override is not None:
            return float(override)

        if key == "default":
            return float(self.default)

        attr = getattr(self, key, None)
        if isinstance(attr, (int, float)):
            return float(attr)

        return float(self.default)

    def effective(self, key:str = "default", override:float | None = None, *, attempt:int = 0) -> float:
        """
        Return the effective timeout (seconds) with multiplier/backoff applied.
        """
        base = self.resolve(key, override)
        backoff = self.retry_backoff_factor**attempt if attempt > 0 else 1.0
        return base * self.multiplier * backoff


class HumanizationConfig(ContextualModel):
    """Controls human-like browser interaction to reduce automation fingerprinting.

    When enabled (the default), clicks are performed via real mouse movement + press/release,
    text is typed character-by-character with variable delays, pauses vary, occasional idle
    micro-actions (small scrolls, mouse wiggles) are injected, and the browser window size is
    randomized at launch. All behavior is opt-out via ``enabled: false`` and individually tunable.
    """

    enabled:bool = Field(default = True, description = "master switch for all human-like interaction behavior")
    mouse_movement:bool = Field(
        default = True, description = "click via real CDP mouse move + press/release instead of an instant element click (auto-falls back on failure)"
    )
    typing_jitter:bool = Field(default = True, description = "type text character-by-character with variable per-keystroke delays instead of a single burst")
    typing_delay_min_ms:int = Field(default = 40, ge = 0, description = "minimum delay between individual keystrokes (ms)")
    typing_delay_max_ms:int = Field(default = 140, ge = 0, description = "maximum delay between individual keystrokes (ms)")
    action_delay_min_ms:int = Field(default = 1_000, ge = 0, description = "minimum pause after interactions (ms); matches the historical web_sleep band")
    action_delay_max_ms:int = Field(default = 2_500, ge = 0, description = "maximum pause after interactions (ms); matches the historical web_sleep band")
    long_pause_probability:float = Field(default = 0.1, ge = 0.0, le = 1.0, description = "probability of inserting a longer 'thinking' pause at boundaries")
    long_pause_min_ms:int = Field(default = 1_500, ge = 0, description = "minimum duration of a 'thinking' pause (ms)")
    long_pause_max_ms:int = Field(default = 4_000, ge = 0, description = "maximum duration of a 'thinking' pause (ms)")
    idle_action_probability:float = Field(
        default = 0.3, ge = 0.0, le = 1.0, description = "chance to run a random subset of idle micro-actions (scroll / mouse wiggle) at a page boundary"
    )
    randomize_viewport:bool = Field(
        default = True, description = "pick a random window size from viewport_sizes at launch (ignored if --window-size is set manually)"
    )
    viewport_sizes:list[str] = Field(
        default_factory = lambda: ["1920x1080", "1680x1050", "1600x900", "1536x864", "1440x900", "1366x768"],
        description = "whitelist of WxH desktop window sizes to randomly choose from when randomize_viewport is enabled",
        examples = ['"1920x1080"', '"1366x768"'],
    )

    @field_validator("viewport_sizes")
    @classmethod
    def _validate_viewport_sizes(cls, value:list[str]) -> list[str]:
        expected_parts = 2
        for size in value:
            parts = size.lower().split("x")
            if (
                len(parts) != expected_parts
                or not (parts[0].strip().isdigit() and parts[1].strip().isdigit())
                or int(parts[0].strip()) <= 0
                or int(parts[1].strip()) <= 0
            ):
                raise ValueError(_("Invalid viewport size '%(size)s'. Width and height must be positive integers, e.g. '1920x1080'.") % {"size": size})
        return value

    @model_validator(mode = "after")
    def _validate_ranges(self) -> HumanizationConfig:
        for lo_name, hi_name in (
            ("typing_delay_min_ms", "typing_delay_max_ms"),
            ("action_delay_min_ms", "action_delay_max_ms"),
            ("long_pause_min_ms", "long_pause_max_ms"),
        ):
            lo_value = getattr(self, lo_name)
            hi_value = getattr(self, hi_name)
            if hi_value < lo_value:
                raise ValueError(
                    _("%(hi_name)s (%(hi_value)d) must be >= %(lo_name)s (%(lo_value)d).") % {
                        "hi_name": hi_name, "hi_value": hi_value,
                        "lo_name": lo_name, "lo_value": lo_value,
                    }
                )
        return self


class CaptureOnConfig(ContextualModel):
    """Configuration for which operations should trigger diagnostics capture."""

    login_detection:bool = Field(
        default = False,
        description = "Capture screenshot and HTML when login state detection fails",
    )
    publish:bool = Field(
        default = False,
        description = "Capture screenshot, HTML, and JSON on publish failures",
    )


class DiagnosticsConfig(ContextualModel):
    capture_on:CaptureOnConfig = Field(
        default_factory = CaptureOnConfig,
        description = "Enable diagnostics capture for specific operations.",
    )
    capture_log_copy:bool = Field(
        default = False,
        description = "If true, copy the entire bot log file when diagnostics are captured (may duplicate log content).",
    )
    pause_on_login_detection_failure:bool = Field(
        default = False,
        description = "If true, pause (interactive runs only) after capturing login detection diagnostics "
        "so that user can inspect the browser. Requires capture_on.login_detection to be enabled.",
    )
    output_dir:str | None = Field(
        default = None,
        description = "Optional output directory for diagnostics artifacts. If omitted, a safe default is used based on installation mode.",
    )
    timing_collection:bool = Field(
        default = False,
        description = "If true, collect local timeout timing data and write it to diagnostics JSON for troubleshooting and tuning.",
    )

    @model_validator(mode = "before")
    @classmethod
    def migrate_legacy_diagnostics_keys(cls, data:dict[str, Any]) -> dict[str, Any]:
        """Migrate legacy login_detection_capture and publish_error_capture keys."""

        # Migrate legacy login_detection_capture -> capture_on.login_detection
        # Only migrate if the new key is not already explicitly set
        if "login_detection_capture" in data:
            LOG.warning("Deprecated: 'login_detection_capture' is replaced by 'capture_on.login_detection'. Please update your config.")
            if "capture_on" not in data or data["capture_on"] is None:
                data["capture_on"] = {}
            if isinstance(data["capture_on"], dict) and "login_detection" not in data["capture_on"]:
                data["capture_on"]["login_detection"] = data.pop("login_detection_capture")
            else:
                # Remove legacy key but don't overwrite explicit new value
                data.pop("login_detection_capture")

        # Migrate legacy publish_error_capture -> capture_on.publish
        # Only migrate if the new key is not already explicitly set
        if "publish_error_capture" in data:
            LOG.warning("Deprecated: 'publish_error_capture' is replaced by 'capture_on.publish'. Please update your config.")
            if "capture_on" not in data or data["capture_on"] is None:
                data["capture_on"] = {}
            if isinstance(data["capture_on"], dict) and "publish" not in data["capture_on"]:
                data["capture_on"]["publish"] = data.pop("publish_error_capture")
            else:
                # Remove legacy key but don't overwrite explicit new value
                data.pop("publish_error_capture")

        return data

    @model_validator(mode = "after")
    def _validate_pause_requires_capture(self) -> "DiagnosticsConfig":
        if self.pause_on_login_detection_failure and not self.capture_on.login_detection:
            raise ValueError(_("pause_on_login_detection_failure requires capture_on.login_detection to be enabled"))
        return self


def _validate_glob_pattern(v:str) -> str:
    if not v.strip():
        raise ValueError(_("must be a non-empty, non-blank glob pattern"))
    return v


def _validate_download_template(
    template:str,
    *,
    allowed_fields:frozenset[str],
    required_fields:frozenset[str],
    field_name:str,
) -> str:
    trimmed_template = template.strip()
    if not trimmed_template:
        raise ValueError(_("%s must be a non-empty template") % field_name)
    if "/" in trimmed_template or "\\" in trimmed_template:
        raise ValueError(_("%s must not contain path separators") % field_name)

    formatter = Formatter()
    used_fields:set[str] = set()
    field_counts:dict[str, int] = {}
    try:
        parsed = list(formatter.parse(trimmed_template))
    except ValueError as exc:
        raise ValueError(_("%s contains invalid template syntax: %s") % (field_name, exc)) from exc

    for _literal_text, field_name_part, format_spec, conversion in parsed:
        if field_name_part is None:
            continue
        if not field_name_part:
            raise ValueError(_("%s contains an empty placeholder") % field_name)
        if conversion is not None:
            raise ValueError(_("%s placeholders must not use conversion flags") % field_name)
        if format_spec:
            raise ValueError(_("%s placeholders must not use format specifiers") % field_name)
        if field_name_part not in allowed_fields:
            allowed = ", ".join(sorted(f"{{{name}}}" for name in allowed_fields))
            raise ValueError(_("%s only supports placeholders: %s") % (field_name, allowed))
        used_fields.add(field_name_part)
        field_counts[field_name_part] = field_counts.get(field_name_part, 0) + 1

    # Reject repeated placeholders - each placeholder may appear at most once
    for field, count in field_counts.items():
        if count > 1:
            raise ValueError(_("%s may contain at most one {%s} placeholder") % (field_name, field))

    missing_fields = required_fields - used_fields
    if missing_fields:
        required = ", ".join(sorted(f"{{{name}}}" for name in missing_fields))
        raise ValueError(_("%s must include placeholder(s): %s") % (field_name, required))
    if not used_fields:
        allowed = ", ".join(sorted(f"{{{name}}}" for name in allowed_fields))
        raise ValueError(_("%s must include at least one placeholder: %s") % (field_name, allowed))

    return trimmed_template


GlobPattern = Annotated[str, AfterValidator(_validate_glob_pattern)]


class Config(ContextualModel):
    ad_files:list[GlobPattern] = Field(
        default_factory = lambda: ["./**/ad_*.{json,yml,yaml}"],
        json_schema_extra = {"default": ["./**/ad_*.{json,yml,yaml}"]},
        min_length = 1,
        description = (
            "glob (wildcard) patterns to select local ad configuration files. "
            "This only controls which files are loaded; it does not rename downloaded files. "
            "If relative paths are specified, they are relative to this configuration file"
        ),
        examples = ['"./downloaded-ads/**/*.yaml"', '"./**/ad_*.{json,yml,yaml}"'],
    )

    ad_defaults:AdDefaults = Field(default_factory = AdDefaults, description = "Default values for ads, can be overwritten in each ad configuration file")

    categories:dict[str, str] = Field(
        default_factory = dict,
        description=(
            "additional name to category ID mappings (optional). Leave as {} if not needed. "
            "See full list at: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/blob/main/src/kleinanzeigen_bot/resources/categories.yaml "
            "To add: use format 'Category > Subcategory': 'ID'"
        ),
        examples = ['"Elektronik > Notebooks": "161/278"', '"Jobs > Praktika": "102/125"'],
    )

    download:DownloadConfig = Field(default_factory = DownloadConfig)
    publishing:PublishingConfig = Field(default_factory = PublishingConfig)
    deleting:DeletingConfig = Field(default_factory = DeletingConfig, description = "post-delete YAML cleanup configuration")
    browser:BrowserConfig = Field(default_factory = BrowserConfig, description = "Browser configuration")
    login:LoginConfig = Field(default_factory = LoginConfig.model_construct, description = "Login credentials")
    captcha:CaptchaConfig = Field(default_factory = CaptchaConfig)
    update_check:UpdateCheckConfig = Field(default_factory = UpdateCheckConfig, description = "Update check configuration")
    timeouts:TimeoutConfig = Field(default_factory = TimeoutConfig, description = "Centralized timeout configuration.")
    humanization:HumanizationConfig = Field(
        default_factory = HumanizationConfig, description = "Human-like browser interaction settings to reduce automation detection."
    )
    diagnostics:DiagnosticsConfig = Field(default_factory = DiagnosticsConfig, description = "diagnostics capture configuration for troubleshooting")

    def with_values(self, values:dict[str, Any]) -> Config:
        return Config.model_validate(dicts.apply_defaults(copy.deepcopy(values), defaults = self.model_dump()))
