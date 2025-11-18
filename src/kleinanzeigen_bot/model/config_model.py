# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
from __future__ import annotations

import copy
from typing import Annotated, Any, Final, List, Literal

from pydantic import AfterValidator, Field, model_validator
from typing_extensions import deprecated

from kleinanzeigen_bot.model.update_check_model import UpdateCheckConfig
from kleinanzeigen_bot.utils import dicts
from kleinanzeigen_bot.utils.misc import get_attr
from kleinanzeigen_bot.utils.pydantics import ContextualModel

_MAX_PERCENTAGE:Final[int] = 100


class AutoPriceReductionConfig(ContextualModel):
    enabled:bool = Field(
        default = False,
        description = "automatically lower the price of reposted ads"
    )
    strategy:Literal["FIXED", "PERCENTAGE"] | None = Field(
        default = None,
        description = "PERCENTAGE reduces by a percentage of the previous price, FIXED reduces by a fixed amount"
    )
    amount:float | None = Field(
        default = None,
        gt = 0,
        description = "magnitude of the reduction; interpreted as percent for PERCENTAGE or currency units for FIXED"
    )
    min_price:float | None = Field(
        default = None,
        ge = 0,
        description = "required when enabled is true; minimum price floor (use 0 for no lower bound)"
    )
    delay_reposts:int = Field(
        default = 0,
        ge = 0,
        description = "number of reposts to wait before applying the first automatic price reduction"
    )
    delay_days:int = Field(
        default = 0,
        ge = 0,
        description = "number of days to wait after publication before applying automatic price reductions"
    )

    @model_validator(mode = "after")
    def _validate_config(self) -> "AutoPriceReductionConfig":
        if self.enabled:
            if self.strategy is None:
                raise ValueError("strategy must be specified when auto_price_reduction is enabled")
            if self.amount is None:
                raise ValueError("amount must be specified when auto_price_reduction is enabled")
            if self.min_price is None:
                raise ValueError("min_price must be specified when auto_price_reduction is enabled")
            if self.strategy == "PERCENTAGE" and self.amount > _MAX_PERCENTAGE:
                raise ValueError(f"Percentage reduction amount must not exceed {_MAX_PERCENTAGE}")
        return self


class ContactDefaults(ContextualModel):
    name:str | None = None
    street:str | None = None
    zipcode:int | str | None = None
    phone:str | None = None


@deprecated("Use description_prefix/description_suffix instead")
class DescriptionAffixes(ContextualModel):
    prefix:str | None = None
    suffix:str | None = None


class AdDefaults(ContextualModel):
    active:bool = True
    type:Literal["OFFER", "WANTED"] = "OFFER"
    description:DescriptionAffixes | None = None
    description_prefix:str | None = Field(default = None, description = "prefix for the ad description")
    description_suffix:str | None = Field(default = None, description = " suffix for the ad description")
    price_type:Literal["FIXED", "NEGOTIABLE", "GIVE_AWAY", "NOT_APPLICABLE"] = "NEGOTIABLE"
    auto_price_reduction:AutoPriceReductionConfig = Field(
        default_factory = AutoPriceReductionConfig,
        description = "automatic price reduction configuration"
    )
    shipping_type:Literal["PICKUP", "SHIPPING", "NOT_APPLICABLE"] = "SHIPPING"
    sell_directly:bool = Field(default = False, description = "requires shipping_type SHIPPING to take effect")
    images:List[str] | None = Field(default = None)
    contact:ContactDefaults = Field(default_factory = ContactDefaults)
    republication_interval:int = 7

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
    include_all_matching_shipping_options:bool = Field(
        default = False,
        description = "if true, all shipping options matching the package size will be included"
    )
    excluded_shipping_options:List[str] = Field(
        default_factory = list,
        description = "list of shipping options to exclude, e.g. ['DHL_2', 'DHL_5']"
    )
    folder_name_max_length:int = Field(
        default = 100,
        ge = 10,
        le = 255,
        description = "maximum length for folder names when downloading ads (default: 100)"
    )
    rename_existing_folders:bool = Field(
        default = False,
        description = "if true, rename existing folders without titles to include titles (default: false)"
    )


class BrowserConfig(ContextualModel):
    arguments:List[str] = Field(
        default_factory = list,
        description = "See https://peter.sh/experiments/chromium-command-line-switches/"
    )
    binary_location:str | None = Field(
        default = None,
        description = "path to custom browser executable, if not specified will be looked up on PATH"
    )
    extensions:List[str] = Field(
        default_factory = list,
        description = "a list of .crx extension files to be loaded"
    )
    use_private_window:bool = True
    user_data_dir:str | None = Field(
        default = None,
        description = "See https://github.com/chromium/chromium/blob/main/docs/user_data_dir.md"
    )
    profile_name:str | None = None


class LoginConfig(ContextualModel):
    username:str = Field(..., min_length = 1)
    password:str = Field(..., min_length = 1)


class PublishingConfig(ContextualModel):
    delete_old_ads:Literal["BEFORE_PUBLISH", "AFTER_PUBLISH", "NEVER"] | None = "AFTER_PUBLISH"
    delete_old_ads_by_title:bool = Field(default = True, description = "only works if delete_old_ads is set to BEFORE_PUBLISH")


class CaptchaConfig(ContextualModel):
    auto_restart:bool = False
    restart_delay:str = "6h"


class TimeoutConfig(ContextualModel):
    multiplier:float = Field(
        default = 1.0,
        ge = 0.1,
        description = "Global multiplier applied to all timeout values."
    )
    default:float = Field(default = 5.0, ge = 0.0, description = "Baseline timeout for DOM interactions.")
    page_load:float = Field(default = 15.0, ge = 1.0, description = "Page load timeout for web_open.")
    captcha_detection:float = Field(default = 2.0, ge = 0.1, description = "Timeout for captcha iframe detection.")
    sms_verification:float = Field(default = 4.0, ge = 0.1, description = "Timeout for SMS verification prompts.")
    gdpr_prompt:float = Field(default = 10.0, ge = 1.0, description = "Timeout for GDPR/consent dialogs.")
    publishing_result:float = Field(default = 300.0, ge = 10.0, description = "Timeout for publishing result checks.")
    publishing_confirmation:float = Field(default = 20.0, ge = 1.0, description = "Timeout for publish confirmation redirect.")
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
        backoff = self.retry_backoff_factor ** attempt if attempt > 0 else 1.0
        return base * self.multiplier * backoff


def _validate_glob_pattern(v:str) -> str:
    if not v.strip():
        raise ValueError("must be a non-empty, non-blank glob pattern")
    return v


GlobPattern = Annotated[str, AfterValidator(_validate_glob_pattern)]


class Config(ContextualModel):
    ad_files:List[GlobPattern] = Field(
        default_factory = lambda: ["./**/ad_*.{json,yml,yaml}"],
        min_items = 1,
        description = """
glob (wildcard) patterns to select ad configuration files
if relative paths are specified, then they are relative to this configuration file
"""
    )  # type: ignore[call-overload]

    ad_defaults:AdDefaults = Field(
        default_factory = AdDefaults,
        description = "Default values for ads, can be overwritten in each ad configuration file"
    )

    categories:dict[str, str] = Field(default_factory = dict, description = """
additional name to category ID mappings, see default list at
https://github.com/Second-Hand-Friends/kleinanzeigen-bot/blob/main/src/kleinanzeigen_bot/resources/categories.yaml

Example:
    categories:
       Elektronik > Notebooks: 161/278
       Jobs > Praktika: 102/125
    """)

    download:DownloadConfig = Field(default_factory = DownloadConfig)
    publishing:PublishingConfig = Field(default_factory = PublishingConfig)
    browser:BrowserConfig = Field(default_factory = BrowserConfig, description = "Browser configuration")
    login:LoginConfig = Field(default_factory = LoginConfig.model_construct, description = "Login credentials")
    captcha:CaptchaConfig = Field(default_factory = CaptchaConfig)
    update_check:UpdateCheckConfig = Field(default_factory = UpdateCheckConfig, description = "Update check configuration")
    timeouts:TimeoutConfig = Field(default_factory = TimeoutConfig, description = "Centralized timeout configuration.")

    def with_values(self, values:dict[str, Any]) -> Config:
        return Config.model_validate(
            dicts.apply_defaults(copy.deepcopy(values), defaults = self.model_dump())
        )
