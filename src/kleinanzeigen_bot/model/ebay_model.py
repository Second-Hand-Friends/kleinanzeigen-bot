# SPDX-FileCopyrightText: © Tomblarom and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Optional Trading API settings, independent of Kleinanzeigen publication."""
from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator

from kleinanzeigen_bot.utils.pydantics import ContextualModel


class EbayListing(ContextualModel):
    category_id:str | None = Field(default = None, description = "eBay leaf category ID; never inferred from the Kleinanzeigen category")
    condition_id:int | None = Field(default = None, gt = 0, description = "eBay condition ID supported by the selected category")
    aspects:dict[str, list[str]] = Field(default_factory = dict, description = "Category-specific item specifics, with one or more values per name")
    listing_format:Literal["fixed", "auction"] = Field(default = "fixed", description = "Buy It Now (fixed) or auction")
    best_offer:bool = Field(default = False, description = "Enable Best Offer for fixed-price listings")
    price:Decimal | None = Field(default = None, gt = 0, description = "Explicit eBay price override; required for wanted/giveaway/nonpositive-price sources")
    auction_start_price:Decimal | None = Field(default = None, gt = 0, description = "Explicit auction starting price; source price is never used for auctions")
    auction_duration:Literal["Days_3", "Days_5", "Days_7", "Days_10"] | None = Field(default = None, description = "Explicit auction duration")
    quantity:int = Field(default = 1, ge = 1, description = "Number of identical items; auctions require one")
    shipping_profile_id:str | None = Field(default = None, description = "Seller's eBay shipping business policy ID")
    return_profile_id:str | None = Field(default = None, description = "Seller's eBay return business policy ID")
    payment_profile_id:str | None = Field(default = None, description = "Seller's eBay payment business policy ID")
    postal_code:str | None = Field(default = None, description = "Item location postal code")
    country:str = Field(default = "DE", min_length = 2, max_length = 2, description = "Item location country code")

    @field_validator("price", "auction_start_price", mode = "before")
    @classmethod
    def parse_price(cls, value:object) -> object:
        if isinstance(value, (str, int, float)):
            return Decimal(str(value))
        return value


class EbayConfig(ContextualModel):
    environment:Literal["sandbox", "production"] = Field(default = "sandbox", description = "Separate eBay API environment; sandbox is the default")
    account:str = Field(default = "", description = "Actual eBay seller username, checked against the authorized account before network commands")
    client_id:str = Field(default = "", description = "Public eBay application client ID for this environment; never put the client secret here")
    redirect_uri:str = Field(default = "", description = "eBay developer application's redirect URL name (RuName)")
    site_id:int = Field(default = 77, ge = 0, description = "Trading API site ID (77 = Germany)")
    currency:str = Field(default = "EUR", min_length = 3, max_length = 3, description = "Source and fee currency; no currency conversion is performed")
    defaults:EbayListing = Field(default_factory = EbayListing, description = "Default eBay listing settings, overridden by an ad's ebay section")
