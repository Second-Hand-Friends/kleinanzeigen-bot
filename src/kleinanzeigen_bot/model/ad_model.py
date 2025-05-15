# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
from __future__ import annotations

import hashlib, json  # isort: skip
from datetime import datetime  # noqa: TC003 Move import into a type-checking block
from typing import Any, Dict, Final, List, Literal, Mapping, Sequence

from pydantic import Field, model_validator, validator
from typing_extensions import Self

from kleinanzeigen_bot.model.config_model import AdDefaults  # noqa: TC001 Move application import into a type-checking block
from kleinanzeigen_bot.utils import dicts
from kleinanzeigen_bot.utils.misc import parse_datetime, parse_decimal
from kleinanzeigen_bot.utils.pydantics import ContextualModel

MAX_DESCRIPTION_LENGTH:Final[int] = 4000


def _OPTIONAL() -> Any:
    return Field(default = None)


def _ISO_DATETIME(default:datetime | None = None) -> Any:
    return Field(
        default = default,
        description = "ISO-8601 timestamp with optional timezone (e.g. 2024-12-25T00:00:00 or 2024-12-25T00:00:00Z)",
        json_schema_extra = {
            "anyOf": [
                {"type": "null"},
                {
                    "type": "string",
                    "pattern": (
                        r"^\d{4}-\d{2}-\d{2}T"  # date + 'T'
                        r"\d{2}:\d{2}:\d{2}"  # hh:mm:ss
                        r"(?:\.\d{1,6})?"  # optional .micro
                        r"(?:Z|[+-]\d{2}:\d{2})?$"  # optional Z or ±HH:MM
                    ),
                },
            ],
        },
    )


class ContactPartial(ContextualModel):
    name:str | None = _OPTIONAL()
    street:str | None = _OPTIONAL()
    zipcode:int | str | None = _OPTIONAL()
    location:str | None = _OPTIONAL()

    phone:str | None = _OPTIONAL()


class AdPartial(ContextualModel):
    active:bool | None = _OPTIONAL()
    type:Literal["OFFER", "WANTED"] | None = _OPTIONAL()
    title:str = Field(..., min_length = 10)
    description:str
    description_prefix:str | None = _OPTIONAL()
    description_suffix:str | None = _OPTIONAL()
    category:str
    special_attributes:Dict[str, str] | None = _OPTIONAL()
    price:int | None = _OPTIONAL()
    price_type:Literal["FIXED", "NEGOTIABLE", "GIVE_AWAY", "NOT_APPLICABLE"] | None = _OPTIONAL()
    shipping_type:Literal["PICKUP", "SHIPPING", "NOT_APPLICABLE"] | None = _OPTIONAL()
    shipping_costs:float | None = _OPTIONAL()
    shipping_options:List[str] | None = _OPTIONAL()
    sell_directly:bool | None = _OPTIONAL()
    images:List[str] | None = _OPTIONAL()
    contact:ContactPartial | None = _OPTIONAL()
    republication_interval:int | None = _OPTIONAL()

    id:int | None = _OPTIONAL()
    created_on:datetime | None = _ISO_DATETIME()
    updated_on:datetime | None = _ISO_DATETIME()
    content_hash:str | None = _OPTIONAL()

    @validator("created_on", "updated_on", pre = True)
    @classmethod
    def _parse_dates(cls, v:Any) -> Any:
        return parse_datetime(v)

    @validator("shipping_costs", pre = True)
    @classmethod
    def _parse_shipping_costs(cls, v:float | int | str) -> Any:
        if v:
            return round(parse_decimal(v), 2)
        return None

    @validator("description")
    @classmethod
    def _validate_description_length(cls, v:str) -> str:
        if len(v) > MAX_DESCRIPTION_LENGTH:
            raise ValueError(f"description length exceeds {MAX_DESCRIPTION_LENGTH} characters")
        return v

    @model_validator(mode = "before")
    @classmethod
    def _validate_price_and_price_type(cls, values:Dict[str, Any]) -> Dict[str, Any]:
        price_type = values.get("price_type")
        price = values.get("price")
        if price_type == "GIVE_AWAY" and price is not None:
            raise ValueError("price must not be specified when price_type is GIVE_AWAY")
        if price_type == "FIXED" and price is None:
            raise ValueError("price is required when price_type is FIXED")
        return values

    @validator("shipping_options", each_item = True)
    @classmethod
    def _validate_shipping_option(cls, v:str) -> str:
        if not v.strip():
            raise ValueError("shipping_options entries must be non-empty")
        return v

    def update_content_hash(self) -> Self:
        """Calculate and updates the content_hash value for user-modifiable fields of the ad."""

        # 1) Dump to a plain dict, excluding the metadata fields:
        raw = self.model_dump(
            exclude = {"id", "created_on", "updated_on", "content_hash"},
            exclude_none = True,
            exclude_unset = True,
        )

        # 2) Recursively prune any empty containers:
        def prune(obj:Any) -> Any:
            if isinstance(obj, Mapping):
                return {
                    k: prune(v)
                    for k, v in obj.items()
                    # drop keys whose values are empty list/dict/set
                    if not (isinstance(v, (Mapping, Sequence, set)) and not isinstance(v, (str, bytes)) and len(v) == 0)
                }
            if isinstance(obj, Sequence) and not isinstance(obj, (str, bytes)):
                return [
                    prune(v)
                    for v in obj
                    if not (isinstance(v, (Mapping, Sequence, set)) and not isinstance(v, (str, bytes)) and len(v) == 0)
                ]
            return obj

        pruned = prune(raw)

        # 3) Produce a canonical JSON string and hash it:
        json_string = json.dumps(pruned, sort_keys = True)
        self.content_hash = hashlib.sha256(json_string.encode()).hexdigest()
        return self

    def to_ad(self, ad_defaults:AdDefaults) -> Ad:
        """
        Returns a complete, validated Ad by merging this partial with values from ad_defaults.

        Any field that is `None` or `""` is filled from `ad_defaults`.

        Raises `ValidationError` when, after merging with `ad_defaults`, not all fields required by `Ad` are populated.
        """
        ad_cfg = self.model_dump()
        dicts.apply_defaults(
            target = ad_cfg,
            defaults = ad_defaults.model_dump(),
            ignore = lambda k, _: k == "description",  # ignore legacy global description config
            override = lambda _, v: v in {None, ""}  # noqa: PLC1901 can be simplified
        )
        return Ad.model_validate(ad_cfg)


# pyright: reportGeneralTypeIssues=false, reportIncompatibleVariableOverride=false
class Contact(ContactPartial):
    name:str
    zipcode:int | str


# pyright: reportGeneralTypeIssues=false, reportIncompatibleVariableOverride=false
class Ad(AdPartial):
    active:bool
    type:Literal["OFFER", "WANTED"]
    description:str
    price_type:Literal["FIXED", "NEGOTIABLE", "GIVE_AWAY", "NOT_APPLICABLE"]
    shipping_type:Literal["PICKUP", "SHIPPING", "NOT_APPLICABLE"]
    sell_directly:bool
    contact:Contact
    republication_interval:int
