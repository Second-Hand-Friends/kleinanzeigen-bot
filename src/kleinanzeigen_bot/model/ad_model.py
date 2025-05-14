# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
from __future__ import annotations

from datetime import datetime  # noqa: TC003 Move import into a type-checking block
from typing import Any, Dict, Final, List, Literal

from pydantic import Field, model_validator, validator

from kleinanzeigen_bot.utils.misc import parse_datetime, parse_decimal
from kleinanzeigen_bot.utils.pydantics import ContextualModel

MAX_DESCRIPTION_LENGTH:Final[int] = 4000


def _iso_datetime_field() -> Any:
    return Field(
        default = None,
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
    name:str | None = None
    street:str | None = None
    zipcode:int | str | None = None
    location:str | None = None

    phone:str | None = None


class AdPartial(ContextualModel):
    active:bool = True
    type:Literal["OFFER", "WANTED"] = "OFFER"
    title:str = Field(..., min_length = 10)
    description:str
    description_prefix:str | None = None
    description_suffix:str | None = None
    category:str
    special_attributes:Dict[str, str] | None = Field(default = None)
    price:int | None = None
    price_type:Literal["FIXED", "NEGOTIABLE", "GIVE_AWAY", "NOT_APPLICABLE"] = "NEGOTIABLE"
    shipping_type:Literal["PICKUP", "SHIPPING", "NOT_APPLICABLE"] = "SHIPPING"
    shipping_costs:float | None = None
    shipping_options:List[str] | None = Field(default = None)
    sell_directly:bool | None = False
    images:List[str] | None = Field(default = None)
    contact:ContactPartial | None = None
    republication_interval:int = 7

    id:int | None = None
    created_on:datetime | None = _iso_datetime_field()
    updated_on:datetime | None = _iso_datetime_field()
    content_hash:str | None = None

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


class Contact(ContactPartial):
    name:str  # pyright: ignore[reportGeneralTypeIssues, reportIncompatibleVariableOverride]
    zipcode:int | str  # pyright: ignore[reportGeneralTypeIssues, reportIncompatibleVariableOverride]


class Ad(AdPartial):
    contact:Contact  # pyright: ignore[reportGeneralTypeIssues, reportIncompatibleVariableOverride]
