# SPDX-FileCopyrightText: © Tomblarom and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Requests adapter for official Trading listings and Media picture uploads."""
from __future__ import annotations

import mimetypes
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from gettext import gettext as _
from http import HTTPStatus
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, unquote, urlsplit
from xml.etree import ElementTree as ET  # noqa: S405 - parsing rejects DTDs/entities

import requests

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from kleinanzeigen_bot.ebay_store import ListingRecord
    from kleinanzeigen_bot.model.ebay_model import EbayConfig

NS = "urn:ebay:apis:eBLBaseComponents"


class EbayAPIError(RuntimeError):
    """Trading API error with redacted details for correcting listing fields."""


class EbayRejectedError(EbayAPIError):
    """eBay returned a complete failure response with explicit error codes."""


class EbayUncertainError(EbayAPIError):
    """The response does not establish whether the operation completed."""


def child(parent:ET.Element, name:str, value:Any = None) -> ET.Element:
    node = ET.SubElement(parent, name)
    if value is not None:
        node.text = str(value)
    return node


def text_at(root:ET.Element, path:str) -> str:
    return root.findtext("/".join(f"{{{NS}}}{part}" for part in path.split("/")), "")


def nodes_at(root:ET.Element, path:str) -> list[ET.Element]:
    return root.findall("/".join(f"{{{NS}}}{part}" for part in path.split("/")))


class TradingAPI:
    def __init__(self, config:EbayConfig, token:Callable[[], str], session:requests.Session | None = None) -> None:
        self.config = config
        self.token = token
        self.session = session if session is not None else requests.Session()
        self.endpoint = "https://api.sandbox.ebay.com/ws/api.dll" if config.environment == "sandbox" else "https://api.ebay.com/ws/api.dll"

    def call(self, operation:str, fields:ET.Element | None = None) -> ET.Element:
        request = ET.Element(f"{operation}Request", xmlns = NS)
        child(request, "ErrorLanguage", "en_US")
        if fields is not None:
            request.extend(list(fields))
        payload = ET.tostring(request, encoding = "utf-8", xml_declaration = True)
        headers = {
            "X-EBAY-API-CALL-NAME": operation,
            "X-EBAY-API-SITEID": str(self.config.site_id),
            "X-EBAY-API-COMPATIBILITY-LEVEL": "1487",
            "X-EBAY-API-IAF-TOKEN": self.token(),
        }
        try:
            headers["Content-Type"] = "text/xml; charset=utf-8"
            response = self.session.post(self.endpoint, data = payload, headers = headers, timeout = (15, 90), allow_redirects = False)
            response.raise_for_status()
        except (requests.RequestException, TimeoutError):
            raise EbayUncertainError(_("eBay request failed or timed out; publication may need reconciliation")) from None
        try:
            # Trading responses need no DTD. Reject declarations before parsing.
            content = response.content.decode("utf-8")
            if "<!DOCTYPE" in content.upper() or "<!ENTITY" in content.upper():
                raise ET.ParseError
            root = ET.fromstring(content)  # noqa: S314 - DTD/entity declarations rejected above
            if root.tag != f"{{{NS}}}{operation}Response":
                raise ET.ParseError
        except (ET.ParseError, UnicodeDecodeError):
            raise EbayUncertainError(_("eBay returned an unreadable response; publication may need reconciliation")) from None
        ack = text_at(root, "Ack")
        if operation == "GetSellerList" and ack == "Warning":
            raise EbayUncertainError(_("eBay seller listing lookup was incomplete; publication remains blocked"))
        if ack not in {"Success", "Warning"}:
            codes = [text_at(error, "ErrorCode") for error in nodes_at(root, "Errors")]
            safe_codes = ", ".join(code for code in codes if code.isdigit())
            if ack == "Failure" and safe_codes:
                messages = [text_at(error, "LongMessage") or text_at(error, "ShortMessage") for error in nodes_at(root, "Errors")]
                detail = " ".join(" ".join(messages).split())
                token = headers["X-EBAY-API-IAF-TOKEN"]
                for sensitive in (token, quote(token, safe = "")):
                    if sensitive:
                        detail = detail.replace(sensitive, "[redacted]")
                raise EbayRejectedError(_("eBay rejected the request (error codes: %s): %s") % (safe_codes, detail[:500]))
            raise EbayUncertainError(_("eBay returned an incomplete acknowledgement; publication may need reconciliation"))
        return root

    def seller(self) -> str:
        root = self.call("GetUser")
        seller = text_at(root, "User/UserID")
        if not seller or seller.casefold() != self.config.account.casefold():
            raise EbayAPIError(_("Authorized eBay seller does not match ebay.account; check the account and environment"))
        return seller.casefold()

    def upload_picture(self, path:Path) -> str:
        host = "https://api.sandbox.ebay.com" if self.config.environment == "sandbox" else "https://api.ebay.com"
        headers = {"Authorization": "Bearer " + self.token(), "Accept": "application/json"}
        try:
            with path.open("rb") as stream:
                response = self.session.post(
                    host + "/commerce/media/v1_beta/image/create_image_from_file",
                    files = {"image": (path.name, stream, mimetypes.guess_type(path.name)[0] or "application/octet-stream")},
                    headers = headers, timeout = (15, 90), allow_redirects = False,
                )
            if response.status_code != HTTPStatus.CREATED:
                raise EbayAPIError(_("eBay picture upload failed; no listing was submitted"))
            data = response.json() if response.content.strip() else {}
            if isinstance(data, dict) and not data.get("imageUrl"):
                location = urlsplit(response.headers.get("Location", ""))
                namespace = "sandbox.ebay.com" if self.config.environment == "sandbox" else "ebay.com"
                allowed_hosts = {f"{prefix}.{namespace}" for prefix in ("api", "apim", "apiz")}
                prefix = "/commerce/media/v1_beta/image/"
                image_id = unquote(location.path.removeprefix(prefix))
                if location.scheme != "https" or location.netloc not in allowed_hosts or not location.path.startswith(prefix):
                    raise ValueError
                if not image_id or image_id in {".", ".."} or "/" in image_id or location.query or location.fragment:
                    raise ValueError
                # Reconstruct on the configured host; never forward credentials
                # to a response-supplied URL or use Location as an EPS PictureURL.
                response = self.session.get(host + prefix + quote(image_id, safe = ""), headers = headers,
                                            timeout = (15, 90), allow_redirects = False)
                if response.status_code != HTTPStatus.OK:
                    raise ValueError
                data = response.json()
        except (requests.RequestException, TimeoutError, ValueError):
            raise EbayAPIError(_("eBay picture upload failed; no listing was submitted")) from None
        url = data.get("imageUrl") if isinstance(data, dict) else None
        if not isinstance(url, str) or not url.startswith("https://"):
            # Location is a Media resource URI, not an EPS image URL. Never use
            # it as PictureURL or forward a bearer token to an arbitrary host.
            raise EbayAPIError(_("eBay did not return a hosted picture URL"))
        return url

    def verify(self, item:ET.Element, *, auction:bool) -> Decimal:
        fields = ET.Element("Fields")
        fields.append(item)
        root = self.call("VerifyAddItem" if auction else "VerifyAddFixedPriceItem", fields)
        fees = nodes_at(root, "Fees/Fee")
        total = Decimal(0)
        insertion_seen = False
        try:
            for fee in fees:
                amounts = nodes_at(fee, "Fee")
                if len(amounts) != 1 or amounts[0].get("currencyID") != self.config.currency:
                    raise ValueError
                amount = Decimal(amounts[0].text or "")
                if not amount.is_finite() or amount < 0:
                    raise ValueError
                total += amount
                insertion_seen |= text_at(fee, "Name") == "InsertionFee"
            if not insertion_seen:
                raise ValueError
        except (ValueError, InvalidOperation):
            raise EbayAPIError(_("eBay did not return complete listing fees in the configured currency")) from None
        return total

    def publish(self, item:ET.Element, *, auction:bool) -> str:
        fields = ET.Element("Fields")
        fields.append(item)
        root = self.call("AddItem" if auction else "AddFixedPriceItem", fields)
        item_id = text_at(root, "ItemID")
        if not item_id.isdigit():
            raise EbayUncertainError(_("eBay did not return a listing ID; reconcile before continuing"))
        return item_id

    def status(self, item_id:str) -> str:
        fields = ET.Element("Fields")
        child(fields, "ItemID", item_id)
        root = self.call("GetItem", fields)
        status = text_at(root, "Item/SellingStatus/ListingStatus")
        if not status:
            raise EbayAPIError(_("eBay did not return a listing status"))
        return status

    def reconcile(self, record:ListingRecord) -> tuple[str, str] | None:
        """Search every seller-list page around the durable submission time.

        ApplicationData works for auctions too. An empty or unavailable result
        never permits another Add call. Old unresolved intents remain blocked.
        """
        submitted = datetime.fromisoformat(record.submitted_at)
        fields = ET.Element("Fields")
        child(fields, "DetailLevel", "ReturnAll")
        child(fields, "StartTimeFrom", (submitted - timedelta(hours = 1)).astimezone(UTC).isoformat())
        child(fields, "StartTimeTo", (submitted + timedelta(days = 1)).astimezone(UTC).isoformat())
        pagination = child(fields, "Pagination")
        child(pagination, "EntriesPerPage", 200)
        page_node = child(pagination, "PageNumber", 1)
        found:dict[str, str] = {}
        page = 1
        while True:
            page_node.text = str(page)
            root = self.call("GetSellerList", fields)
            for item in nodes_at(root, "ItemArray/Item"):
                if text_at(item, "ApplicationData").upper() == record.source_id.upper():
                    item_id = text_at(item, "ItemID")
                    if not item_id.isdigit():
                        raise EbayAPIError(_("eBay returned an invalid listing ID"))
                    found[item_id] = text_at(item, "SellingStatus/ListingStatus") or "unknown"
            more = text_at(root, "HasMoreItems")
            if more == "false":
                break
            total_pages = text_at(root, "PaginationResult/TotalNumberOfPages")
            if (more != "true" or not nodes_at(root, "ItemArray/Item")
                    or not total_pages.isdigit() or page >= int(total_pages)):
                raise EbayAPIError(_("eBay seller listing lookup was incomplete; publication remains blocked"))
            page += 1
        if len(found) > 1:
            raise EbayAPIError(_("Multiple eBay listings match this source; publication remains blocked"))
        return next(iter(found.items())) if found else None
