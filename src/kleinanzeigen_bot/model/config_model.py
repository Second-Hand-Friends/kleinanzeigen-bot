# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
from __future__ import annotations

import copy
from typing import Any, List, Literal

from pydantic import Field, model_validator, validator
from typing_extensions import deprecated

from kleinanzeigen_bot.utils import dicts
from kleinanzeigen_bot.utils.misc import get_attr
from kleinanzeigen_bot.utils.pydantics import ContextualModel


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
    shipping_type:Literal["PICKUP", "SHIPPING", "NOT_APPLICABLE"] = "SHIPPING"
    sell_directly:bool = Field(default = False, description = "requires shipping_type SHIPPING to take effect")
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


class Config(ContextualModel):
    ad_files:List[str] = Field(
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

    def with_values(self, values:dict[str, Any]) -> Config:
        return Config.model_validate(
            dicts.apply_defaults(copy.deepcopy(values), defaults = self.model_dump())
        )

    @validator("ad_files", each_item = True)
    @classmethod
    def _non_empty_glob_pattern(cls, v:str) -> str:
        if not v.strip():
            raise ValueError("ad_files entries must be non-empty glob patterns")
        return v
