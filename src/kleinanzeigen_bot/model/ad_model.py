# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
from __future__ import annotations

import hashlib, json  # isort: skip
from collections.abc import Mapping, Sequence
from datetime import datetime  # noqa: TC003 Move import into a type-checking block
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any, Final, Literal

from pydantic import AfterValidator, Field, field_validator, model_validator
from typing_extensions import Self

from kleinanzeigen_bot.model.config_model import AdDefaults, AutoPriceReductionConfig  # noqa: TC001 Move application import into a type-checking block
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


def _validate_shipping_option_item(v:str) -> str:
    if not v.strip():
        raise ValueError("must be non-empty and non-blank")
    return v


ShippingOption = Annotated[str, AfterValidator(_validate_shipping_option_item)]


class AdPartial(ContextualModel):
    active:bool | None = _OPTIONAL()
    type:Literal["OFFER", "WANTED"] | None = _OPTIONAL()
    title:str = Field(..., min_length = 10)
    description:str
    description_prefix:str | None = _OPTIONAL()
    description_suffix:str | None = _OPTIONAL()
    category:str
    special_attributes:dict[str, str] | None = _OPTIONAL()
    price:int | None = _OPTIONAL()
    price_type:Literal["FIXED", "NEGOTIABLE", "GIVE_AWAY", "NOT_APPLICABLE"] | None = _OPTIONAL()
    auto_price_reduction:AutoPriceReductionConfig | None = Field(
        default = None,
        description = "automatic price reduction configuration"
    )
    repost_count:int = Field(
        default = 0,
        ge = 0,
        description = "number of successful publications for this ad (persisted between runs)"
    )
    price_reduction_count:int = Field(
        default = 0,
        ge = 0,
        description = "internal counter: number of automatic price reductions already applied"
    )
    shipping_type:Literal["PICKUP", "SHIPPING", "NOT_APPLICABLE"] | None = _OPTIONAL()
    shipping_costs:float | None = _OPTIONAL()
    shipping_options:list[ShippingOption] | None = _OPTIONAL()
    sell_directly:bool | None = _OPTIONAL()
    images:list[str] | None = _OPTIONAL()
    contact:ContactPartial | None = _OPTIONAL()
    republication_interval:int | None = _OPTIONAL()

    id:int | None = _OPTIONAL()
    created_on:datetime | None = _ISO_DATETIME()
    updated_on:datetime | None = _ISO_DATETIME()
    content_hash:str | None = _OPTIONAL()

    @field_validator("created_on", "updated_on", mode = "before")
    @classmethod
    def _parse_dates(cls, v:Any) -> Any:
        return parse_datetime(v)

    @field_validator("shipping_costs", mode = "before")
    @classmethod
    def _parse_shipping_costs(cls, v:float | int | str) -> Any:
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        return round(parse_decimal(v), 2)

    @field_validator("description")
    @classmethod
    def _validate_description_length(cls, v:str) -> str:
        if len(v) > MAX_DESCRIPTION_LENGTH:
            raise ValueError(f"description length exceeds {MAX_DESCRIPTION_LENGTH} characters")
        return v

    @model_validator(mode = "before")
    @classmethod
    def _validate_price_and_price_type(cls, values:dict[str, Any]) -> dict[str, Any]:
        price_type = values.get("price_type")
        price = values.get("price")
        auto_price_reduction = values.get("auto_price_reduction")

        if price_type == "GIVE_AWAY" and price is not None:
            raise ValueError("price must not be specified when price_type is GIVE_AWAY")
        if price_type == "FIXED" and price is None:
            raise ValueError("price is required when price_type is FIXED")

        # Validate auto_price_reduction configuration
        if auto_price_reduction and isinstance(auto_price_reduction, dict):
            if auto_price_reduction.get("enabled"):
                if price is None:
                    raise ValueError("price must be specified when auto_price_reduction is enabled")
                min_price = auto_price_reduction.get("min_price")
                if min_price is not None and price is not None and min_price > price:
                    raise ValueError("min_price must not exceed price")

        return values

    def update_content_hash(self) -> Self:
        """Calculate and updates the content_hash value for user-modifiable fields of the ad."""

        # 1) Dump to a plain dict, excluding the metadata fields:
        raw = self.model_dump(
            exclude = {
                "id",
                "created_on",
                "updated_on",
                "content_hash",
                "repost_count",
                "price_reduction_count",
            },
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

        Any field that is `None` or `""` is filled from `ad_defaults` when it's not a list.

        Raises `ValidationError` when, after merging with `ad_defaults`, not all fields required by `Ad` are populated.
        """
        ad_cfg = self.model_dump()
        dicts.apply_defaults(
            target = ad_cfg,
            defaults = ad_defaults.model_dump(),
            ignore = lambda k, _: k == "description",  # ignore legacy global description config
            override = lambda _, v: (
                not isinstance(v, list) and (v is None or (isinstance(v, str) and v == ""))  # noqa: PLC1901
            )
        )
        # Ensure price_reduction_count is an integer (internal counter, not user-configurable)
        if not isinstance(ad_cfg.get("price_reduction_count"), int):
            ad_cfg["price_reduction_count"] = 0
        return Ad.model_validate(ad_cfg)


def calculate_auto_price(
    *,
    base_price:int | float | None,
    auto_price_reduction:AutoPriceReductionConfig | None,
    target_reduction_cycle:int
) -> int | None:
    """
    Calculate the effective price for the current run.

    Args:
        base_price: original configured price used as the starting point.
        auto_price_reduction: reduction configuration (enabled, strategy, amount, min_price, delays).
        target_reduction_cycle: which reduction cycle to calculate the price for (0 = no reduction, 1 = first reduction, etc.).

    Percentage reductions apply to the current price each cycle (compounded). Returns an int rounded via ROUND_HALF_UP, or None when base_price is None.
    """
    if base_price is None:
        return None

    price = Decimal(str(base_price))

    if not auto_price_reduction or not auto_price_reduction.enabled or target_reduction_cycle <= 0:
        return int(price.quantize(Decimal("1"), rounding = ROUND_HALF_UP))

    if auto_price_reduction.strategy is None or auto_price_reduction.amount is None:
        return int(price.quantize(Decimal("1"), rounding = ROUND_HALF_UP))

    if auto_price_reduction.min_price is None:
        raise ValueError("min_price must be specified when auto_price_reduction is enabled")

    price_floor = Decimal(str(auto_price_reduction.min_price))
    repost_cycles = target_reduction_cycle

    for _ in range(repost_cycles):
        reduction_value = (
            price * Decimal(str(auto_price_reduction.amount)) / Decimal("100")
            if auto_price_reduction.strategy == "PERCENTAGE"
            else Decimal(str(auto_price_reduction.amount))
        )
        price -= reduction_value
        if price <= price_floor:
            price = price_floor
            break

    return int(price.quantize(Decimal("1"), rounding = ROUND_HALF_UP))


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
    auto_price_reduction:AutoPriceReductionConfig = Field(default_factory = AutoPriceReductionConfig)
    price_reduction_count:int = 0

    @model_validator(mode = "after")
    def _validate_auto_price_config(self) -> "Ad":
        # Note: This validation duplicates checks from AdPartial._validate_price_and_price_type
        # This is intentional: AdPartial validates raw YAML (with optional None values),
        # while this validator ensures the final Ad object (after merging defaults) is valid
        if self.auto_price_reduction.enabled:
            # Type narrowing: AutoPriceReductionConfig validator ensures min_price is not None when enabled
            # AdPartial validator ensures price is not None when auto_price_reduction is enabled
            if self.price is not None and self.auto_price_reduction.min_price is not None:
                if self.auto_price_reduction.min_price > self.price:
                    raise ValueError("min_price must not exceed price")
        return self
