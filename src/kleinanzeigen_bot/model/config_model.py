# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
from __future__ import annotations

import copy
from gettext import gettext as _
from typing import Annotated, Any, Final, Literal

from pydantic import AfterValidator, Field, model_validator
from typing_extensions import deprecated

from kleinanzeigen_bot.model.update_check_model import UpdateCheckConfig
from kleinanzeigen_bot.utils import dicts, loggers
from kleinanzeigen_bot.utils.misc import get_attr
from kleinanzeigen_bot.utils.pydantics import ContextualModel

LOG: Final[loggers.Logger] = loggers.get_logger(__name__)

_MAX_PERCENTAGE: Final[int] = 100


class AutoPriceReductionConfig(ContextualModel):
    enabled: bool = Field(default=False, description="automatically lower the price of reposted ads")
    strategy: Literal["FIXED", "PERCENTAGE"] | None = Field(
        default=None, description="PERCENTAGE reduces by a percentage of the previous price, FIXED reduces by a fixed amount"
    )
    amount: float | None = Field(
        default=None, gt=0, description="magnitude of the reduction; interpreted as percent for PERCENTAGE or currency units for FIXED"
    )
    min_price: float | None = Field(default=None, ge=0, description="required when enabled is true; minimum price floor (use 0 for no lower bound)")
    delay_reposts: int = Field(default=0, ge=0, description="number of reposts to wait before applying the first automatic price reduction")
    delay_days: int = Field(default=0, ge=0, description="number of days to wait after publication before applying automatic price reductions")

    @model_validator(mode="after")
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
    name: str = Field(default="", description="contact name displayed on the ad")
    street: str = Field(default="", description="street address for the listing")
    zipcode: int | str = Field(default="", description="postal/ZIP code for the listing location")
    location: str = Field(
        default="",
        description="city or locality of the listing (can include multiple districts)",
        examples=["Sample Town - District One"],
    )
    phone: str = Field(
        default="",
        description="phone number for contact - only available for commercial accounts, personal accounts no longer support this",
        examples=['"01234 567890"'],
    )


@deprecated("Use description_prefix/description_suffix instead")
class DescriptionAffixes(ContextualModel):
    prefix: str | None = None
    suffix: str | None = None


class AdDefaults(ContextualModel):
    active: bool = Field(default=True, description="whether the ad should be published (false = skip this ad)")
    type: Literal["OFFER", "WANTED"] = Field(default="OFFER", description="type of the ad listing", examples=["OFFER", "WANTED"])
    description: DescriptionAffixes | None = Field(default=None, description="DEPRECATED: Use description_prefix/description_suffix instead")
    description_prefix: str | None = Field(default=None, description="text to prepend to the ad description")
    description_suffix: str | None = Field(default=None, description="text to append to the ad description")
    price_type: Literal["FIXED", "NEGOTIABLE", "GIVE_AWAY", "NOT_APPLICABLE"] = Field(
        default="NEGOTIABLE", description="pricing strategy for the listing", examples=["FIXED", "NEGOTIABLE", "GIVE_AWAY", "NOT_APPLICABLE"]
    )
    auto_price_reduction: AutoPriceReductionConfig = Field(
        default_factory=AutoPriceReductionConfig, description="automatic price reduction configuration for reposted ads"
    )
    shipping_type: Literal["PICKUP", "SHIPPING", "NOT_APPLICABLE"] = Field(
        default="SHIPPING", description="shipping method for the item", examples=["PICKUP", "SHIPPING", "NOT_APPLICABLE"]
    )
    sell_directly: bool = Field(default=False, description="enable direct purchase option (only works when shipping_type is SHIPPING)")
    images: list[str] | None = Field(
        default=None, description="glob patterns for ad images (relative to ad file location)", examples=['["images/*.jpg", "photos/*.{png,jpg}"]']
    )
    contact: ContactDefaults = Field(default_factory=ContactDefaults, description="default contact information for ads")
    republication_interval: int = Field(default=7, description="number of days between automatic republication of ads")

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_description(cls, values: dict[str, Any]) -> dict[str, Any]:
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
    include_all_matching_shipping_options: bool = Field(
        default=False,
        description="if true, all shipping options matching the package size will be included",
    )
    excluded_shipping_options: list[str] = Field(
        default_factory=list,
        description="list of shipping options to exclude, e.g. ['DHL_2', 'DHL_5']",
    )
    folder_name_max_length: int = Field(
        default=100,
        ge=10,
        le=255,
        description="maximum length for folder names when downloading ads (default: 100)",
    )
    rename_existing_folders: bool = Field(
        default=False,
        description="if true, rename existing folders without titles to include titles (default: false)",
    )


class BrowserConfig(ContextualModel):
    arguments: list[str] = Field(
        default_factory=list,
        description=(
            "Chromium command line switches. See https://peter.sh/experiments/chromium-command-line-switches/ "
            "Common options: --headless (run without GUI), --disable-dev-shm-usage (fix Docker issues), "
            "--user-data-dir=/path (custom profile location). Browser profile path is auto-configured based on installation mode."
        ),
        examples=['["--headless"", ""--disable-dev-shm-usage"", ""--user-data-dir=/path/to/profile"]'],
    )
    binary_location: str | None = Field(default=None, description="path to custom browser executable, if not specified will be looked up on PATH")
    extensions: list[str] = Field(
        default_factory=list,
        description="list of Chrome extension .crx files to load on startup (paths relative to config file)",
        examples=['[""extensions/adblock.crx"", ""/absolute/path/to/extension.crx"]'],
    )
    use_private_window: bool = Field(default=True, description="open browser in private/incognito mode (recommended to avoid cookie conflicts)")
    user_data_dir: str | None = Field(
        default=None,
        description=(
            "See https://github.com/chromium/chromium/blob/main/docs/user_data_dir.md. "
            "If not specified, defaults to XDG cache directory in XDG mode or .temp/browser-profile in portable mode."
        ),
    )
    profile_name: str | None = Field(
        default=None,
        description="name of the browser profile to use (leave empty for default profile)",
        examples=['"Profile 1"'],
    )


class LoginConfig(ContextualModel):
    username: str = Field(..., min_length=1, description="kleinanzeigen.de login email or username")
    password: str = Field(..., min_length=1, description="kleinanzeigen.de login password")


class PublishingConfig(ContextualModel):
    delete_old_ads: Literal["BEFORE_PUBLISH", "AFTER_PUBLISH", "NEVER"] | None = Field(
        default="AFTER_PUBLISH", description="when to delete old versions of republished ads", examples=["BEFORE_PUBLISH", "AFTER_PUBLISH", "NEVER"]
    )
    delete_old_ads_by_title: bool = Field(default=True, description="match old ads by title when deleting (only works with BEFORE_PUBLISH)")


class CaptchaConfig(ContextualModel):
    auto_restart: bool = Field(
        default=False, description="if true, abort when captcha is detected and auto-retry after restart_delay (if false, wait for manual solving)"
    )
    restart_delay: str = Field(
        default="6h", description="duration to wait before retrying after captcha detection (e.g., 1h30m, 6h, 30m)", examples=["6h", "1h30m", "30m"]
    )


class TimeoutConfig(ContextualModel):
    multiplier: float = Field(default=1.0, ge=0.1, description="Global multiplier applied to all timeout values.")
    default: float = Field(default=5.0, ge=0.0, description="Baseline timeout for DOM interactions.")
    page_load: float = Field(default=15.0, ge=1.0, description="Page load timeout for web_open.")
    captcha_detection: float = Field(default=2.0, ge=0.1, description="Timeout for captcha iframe detection.")
    sms_verification: float = Field(default=4.0, ge=0.1, description="Timeout for SMS verification prompts.")
    email_verification: float = Field(default=4.0, ge=0.1, description="Timeout for email verification prompts.")
    gdpr_prompt: float = Field(default=10.0, ge=1.0, description="Timeout for GDPR/consent dialogs.")
    login_detection: float = Field(default=10.0, ge=1.0, description="Timeout for detecting existing login session via DOM elements.")
    publishing_result: float = Field(default=300.0, ge=10.0, description="Timeout for publishing result checks.")
    publishing_confirmation: float = Field(default=20.0, ge=1.0, description="Timeout for publish confirmation redirect.")
    image_upload: float = Field(default=30.0, ge=5.0, description="Timeout for image upload and server-side processing.")
    pagination_initial: float = Field(default=10.0, ge=1.0, description="Timeout for initial pagination lookup.")
    pagination_follow_up: float = Field(default=5.0, ge=1.0, description="Timeout for subsequent pagination navigation.")
    quick_dom: float = Field(default=2.0, ge=0.1, description="Generic short timeout for transient UI.")
    update_check: float = Field(default=10.0, ge=1.0, description="Timeout for GitHub update checks.")
    chrome_remote_probe: float = Field(default=2.0, ge=0.1, description="Timeout for local remote-debugging probes.")
    chrome_remote_debugging: float = Field(default=5.0, ge=1.0, description="Timeout for remote debugging API calls.")
    chrome_binary_detection: float = Field(default=10.0, ge=1.0, description="Timeout for chrome --version subprocesses.")
    retry_enabled: bool = Field(default=True, description="Enable built-in retry/backoff for DOM operations.")
    retry_max_attempts: int = Field(default=2, ge=1, description="Max retry attempts when retry is enabled.")
    retry_backoff_factor: float = Field(default=1.5, ge=1.0, description="Exponential factor applied per retry attempt.")

    def resolve(self, key: str = "default", override: float | None = None) -> float:
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

    def effective(self, key: str = "default", override: float | None = None, *, attempt: int = 0) -> float:
        """
        Return the effective timeout (seconds) with multiplier/backoff applied.
        """
        base = self.resolve(key, override)
        backoff = self.retry_backoff_factor**attempt if attempt > 0 else 1.0
        return base * self.multiplier * backoff


class CaptureOnConfig(ContextualModel):
    """Configuration for which operations should trigger diagnostics capture."""

    login_detection: bool = Field(
        default=False,
        description="Capture screenshot and HTML when login state detection fails",
    )
    publish: bool = Field(
        default=False,
        description="Capture screenshot, HTML, and JSON on publish failures",
    )


class DiagnosticsConfig(ContextualModel):
    capture_on: CaptureOnConfig = Field(
        default_factory=CaptureOnConfig,
        description="Enable diagnostics capture for specific operations.",
    )
    capture_log_copy: bool = Field(
        default=False,
        description="If true, copy the entire bot log file when diagnostics are captured (may duplicate log content).",
    )
    pause_on_login_detection_failure: bool = Field(
        default=False,
        description="If true, pause (interactive runs only) after capturing login detection diagnostics "
        "so that user can inspect the browser. Requires capture_on.login_detection to be enabled.",
    )
    output_dir: str | None = Field(
        default=None,
        description="Optional output directory for diagnostics artifacts. If omitted, a safe default is used based on installation mode.",
    )

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_diagnostics_keys(cls, data: dict[str, Any]) -> dict[str, Any]:
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

    @model_validator(mode="after")
    def _validate_pause_requires_capture(self) -> "DiagnosticsConfig":
        if self.pause_on_login_detection_failure and not self.capture_on.login_detection:
            raise ValueError(_("pause_on_login_detection_failure requires capture_on.login_detection to be enabled"))
        return self


def _validate_glob_pattern(v: str) -> str:
    if not v.strip():
        raise ValueError(_("must be a non-empty, non-blank glob pattern"))
    return v


GlobPattern = Annotated[str, AfterValidator(_validate_glob_pattern)]


class Config(ContextualModel):
    ad_files: list[GlobPattern] = Field(
        default_factory=lambda: ["./**/ad_*.{json,yml,yaml}"],
        json_schema_extra={"default": ["./**/ad_*.{json,yml,yaml}"]},
        min_length=1,
        description="""
glob (wildcard) patterns to select ad configuration files
if relative paths are specified, then they are relative to this configuration file
""",
    )

    ad_defaults: AdDefaults = Field(default_factory=AdDefaults, description="Default values for ads, can be overwritten in each ad configuration file")

    categories: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "additional name to category ID mappings. See full list at: "
            "https://github.com/Second-Hand-Friends/kleinanzeigen-bot/blob/main/src/kleinanzeigen_bot/resources/categories.yaml "
            "Format: 'Category > Subcategory': 'ID'. Example: {\"Elektronik > Notebooks\": \"161/278\"}"
        ),
        examples=['{"Elektronik > Notebooks": "161/278", "Jobs > Praktika": "102/125"}'],
    )

    download: DownloadConfig = Field(default_factory=DownloadConfig)
    publishing: PublishingConfig = Field(default_factory=PublishingConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig, description="Browser configuration")
    login: LoginConfig = Field(default_factory=LoginConfig.model_construct, description="Login credentials")
    captcha: CaptchaConfig = Field(default_factory=CaptchaConfig)
    update_check: UpdateCheckConfig = Field(default_factory=UpdateCheckConfig, description="Update check configuration")
    timeouts: TimeoutConfig = Field(default_factory=TimeoutConfig, description="Centralized timeout configuration.")
    diagnostics: DiagnosticsConfig = Field(default_factory=DiagnosticsConfig, description="diagnostics capture configuration for troubleshooting")

    def with_values(self, values: dict[str, Any]) -> Config:
        return Config.model_validate(dicts.apply_defaults(copy.deepcopy(values), defaults=self.model_dump()))
