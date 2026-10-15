# SPDX-FileCopyrightText: © Tomblarom and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Offline Trading API contract and publication journal regression tests."""
from __future__ import annotations

import json
import os
from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast
from xml.etree import ElementTree as ET  # noqa: S405 - test fixtures only

import pytest
import requests

from kleinanzeigen_bot import ebay_store, ebay_workflow
from kleinanzeigen_bot.ad_loading import load_ad
from kleinanzeigen_bot.cli import parse_args
from kleinanzeigen_bot.ebay_api import NS, EbayAPIError, EbayUncertainError, TradingAPI
from kleinanzeigen_bot.ebay_preparation import build_item, prepare
from kleinanzeigen_bot.ebay_store import EbayStore, restore_download_metadata
from kleinanzeigen_bot.model.ad_model import AdPartial, AdUpdateStrategy
from kleinanzeigen_bot.model.config_model import AdDefaults, Config
from kleinanzeigen_bot.model.ebay_model import EbayConfig
from kleinanzeigen_bot.publishing_persistence import persist_published_ad
from kleinanzeigen_bot.utils import dicts

if TYPE_CHECKING:
    from pathlib import Path

SOURCE = "A" * 32
UUID = "B" * 32


class OfflineSession:
    def __init__(self) -> None:
        self.calls:list[tuple[str, str]] = []
        self.items:list[tuple[str, str]] = []
        self.fee = "0.00"
        self.currency = "EUR"
        self.fail:str | None = None
        self.failure:Exception = requests.Timeout("sensitive request body")
        self.seller = "seller"
        self.more_pages:dict[int, str] = {}

    def post(self, _url:str, **kwargs:Any) -> requests.Response:
        if _url.endswith("/image/create_image_from_file"):
            assert "image" in kwargs["files"]
            assert "Content-Type" not in kwargs["headers"]
            assert kwargs["headers"]["Authorization"] == "Bearer offline-access"
            self.calls.append(("createImageFromFile", ""))
            if self.fail == "createImageFromFile":
                raise self.failure
            response = requests.Response()
            response.status_code = 201
            response._content = b'{"imageUrl":"https://i.ebayimg.com/photo.jpg"}'
            response.headers["Location"] = "https://api.ebay.com/commerce/media/v1_beta/image/media-resource"
            return response
        operation = kwargs["headers"]["X-EBAY-API-CALL-NAME"]
        payload = kwargs["data"]
        body = payload.decode()
        self.calls.append((operation, body))
        root = ET.fromstring(payload)  # noqa: S314 - generated local fixture
        if operation.startswith("Add"):
            source = root.findtext(f".//{{{NS}}}ApplicationData", "")
            self.items.append((source, "123456789"))
        if operation == self.fail:
            raise self.failure
        result = ""
        if operation == "GetUser":
            result = f"<User><UserID>{self.seller}</UserID></User>"
        elif operation.startswith("Verify"):
            result = f'<Fees><Fee><Name>InsertionFee</Name><Fee currencyID="{self.currency}">{self.fee}</Fee></Fee></Fees>'
        elif operation.startswith("Add"):
            result = "<ItemID>123456789</ItemID>"
        elif operation == "GetItem":
            result = "<Item><SellingStatus><ListingStatus>Completed</ListingStatus></SellingStatus></Item>"
        elif operation == "GetSellerList":
            page = int(root.findtext(f".//{{{NS}}}PageNumber", "1"))
            entries = "".join(f"<Item><ApplicationData>{source}</ApplicationData><ItemID>{item}</ItemID>"
                              "<SellingStatus><ListingStatus>Active</ListingStatus></SellingStatus></Item>" for source, item in self.items)
            result = self.more_pages.get(page, f"<HasMoreItems>false</HasMoreItems><ItemArray>{entries}</ItemArray>")
        response = requests.Response()
        response.status_code = 200
        response._content = f'<{operation}Response xmlns="{NS}"><Ack>Success</Ack>{result}</{operation}Response>'.encode()
        return response


@pytest.fixture
def source_files(tmp_path:Path) -> tuple[Path, Path, dict[str, Any]]:
    image = tmp_path / "image.jpg"
    image.write_bytes(b"fake local image")
    raw:dict[str, Any] = {
        "id": 42, "active": True, "title": "A used camera for sale", "description": "Clean camera. https://example.com\nSecond paragraph.",
        "category": "161", "price": 80, "images": [image.name],
        "ebay": {"category_id": "31388", "condition_id": 3000},
    }
    ad_path = tmp_path / "ad_camera.yaml"
    dicts.save_dict(ad_path, raw)
    config_path = tmp_path / "config.yaml"
    dicts.save_dict(config_path, {
        "ad_files": ["ad_*.yaml"],
        "ad_defaults": {"description_prefix": "Prefix www.example.org\n", "description_suffix": "\nSee ebay.de"},
        "ebay": {
            "account": "seller", "client_id": "public", "redirect_uri": "RuName",
            "defaults": {"shipping_profile_id": "1", "return_profile_id": "2", "payment_profile_id": "3", "postal_code": "10115"},
        },
    })
    return config_path, ad_path, raw


@pytest.fixture
def store(tmp_path:Path) -> Any:
    result = EbayStore(tmp_path / "state" / "listings.sqlite3")
    yield result
    result.close()


def offline_api(config_path:Path) -> tuple[TradingAPI, OfflineSession]:
    session = OfflineSession()
    config = Config.model_validate(dicts.load_dict(str(config_path), "config"))
    return TradingAPI(config.ebay, lambda: "offline-access", cast(requests.Session, session)), session


@pytest.mark.parametrize("command", ["ebay-prepare", "ebay-verify", "ebay-publish", "ebay-status", "ebay-login"])
def test_cli_defaults_and_dry_run(command:str) -> None:
    parsed = parse_args(["app", command, "--dry-run"])
    assert parsed.command == command
    assert parsed.ads_selector == "new"
    assert parsed.dry_run


@pytest.mark.parametrize("amount", ["-1", "NaN", "Infinity", "invalid"])
def test_invalid_fee_limit(amount:str) -> None:
    with pytest.raises(SystemExit) as error:
        parse_args(["app", "ebay-publish", "--fee-limit=" + amount])
    assert error.value.code == 2


def test_existing_commands_reject_ebay_flags() -> None:
    with pytest.raises(SystemExit):
        parse_args(["app", "publish", "--dry-run"])


def test_local_prepare_sanitizes_affixes_and_preserves_source(source_files:tuple[Path, Path, dict[str, Any]], store:EbayStore,
                                                            monkeypatch:pytest.MonkeyPatch, caplog:pytest.LogCaptureFixture) -> None:
    config_path, ad_path, raw = source_files

    def forbidden(*args:Any, **kwargs:Any) -> Any:
        pytest.fail("local preparation attempted network access")
    monkeypatch.setattr(requests.Session, "post", forbidden)
    ebay_workflow.run(config_path, "ebay-publish", "new", dry_run = True, store = store)
    saved = dicts.load_dict(str(ad_path), "ad")
    assert saved["description"] == raw["description"]
    assert len(saved["ebay_source_id"]) == 32
    assert "3 links removed" in caplog.text
    assert "Prefix" in caplog.text
    assert "Second paragraph." in caplog.text
    assert "https://example.com" not in caplog.text
    assert store.records("sandbox", "seller") == []


@pytest.mark.parametrize("auction", [False, True])
def test_publish_rerun_never_duplicates_including_ended(source_files:tuple[Path, Path, dict[str, Any]], store:EbayStore, auction:bool) -> None:
    config_path, ad_path, raw = source_files
    if auction:
        raw["ebay"].update(listing_format = "auction", auction_start_price = "10.50", auction_duration = "Days_7")
        dicts.save_dict(ad_path, raw)
    api, session = offline_api(config_path)
    ebay_workflow.run(config_path, "ebay-publish", "new", store = store, api = api)
    ebay_workflow.run(config_path, "ebay-status", "all", refresh = True, store = store, api = api)
    ebay_workflow.run(config_path, "ebay-publish", "all", store = store, api = api)
    adds = [(op, body) for op, body in session.calls if op.startswith("Add")]
    assert len(adds) == 1
    assert adds[0][0] == ("AddItem" if auction else "AddFixedPriceItem")
    payload = ET.fromstring(adds[0][1])  # noqa: S314 - local fixture
    assert payload.findtext(f".//{{{NS}}}ListingType") == ("Chinese" if auction else "FixedPriceItem")
    assert payload.findtext(f".//{{{NS}}}StartPrice") == ("10.50" if auction else "80")
    record = store.records("sandbox", "seller")[0]
    assert payload.findtext(f".//{{{NS}}}UUID") == record.listing_uuid
    assert payload.findtext(f".//{{{NS}}}ApplicationData") == record.source_id
    assert record.status == "Completed"
    assert dicts.load_dict(str(ad_path), "ad")["id"] == 42


def test_verify_uploads_separately_without_publishing(source_files:tuple[Path, Path, dict[str, Any]], store:EbayStore) -> None:
    config_path, _, _ = source_files
    api, session = offline_api(config_path)
    ebay_workflow.run(config_path, "ebay-verify", "all", store = store, api = api)
    operations = [op for op, _ in session.calls]
    assert operations == ["GetUser", "createImageFromFile", "VerifyAddFixedPriceItem"]
    assert store.records("sandbox", "seller") == []
    payload = session.calls[-1][1]
    assert "https://i.ebayimg.com/photo.jpg" in payload
    assert "example.com" not in payload
    assert "GalleryType" not in payload
    assert "ListingEnhancement" not in payload


@pytest.mark.parametrize(("limit", "publishes"), [("0", False), ("0.34", False), ("0.35", True), ("1", True)])
def test_fee_cap(source_files:tuple[Path, Path, dict[str, Any]], store:EbayStore, limit:str, publishes:bool) -> None:
    config_path, _, _ = source_files
    api, session = offline_api(config_path)
    session.fee = "0.35"
    if publishes:
        ebay_workflow.run(config_path, "ebay-publish", "all", fee_limit = Decimal(limit), store = store, api = api)
    else:
        with pytest.raises(EbayAPIError):
            ebay_workflow.run(config_path, "ebay-publish", "all", fee_limit = Decimal(limit), store = store, api = api)
    assert len(session.items) == int(publishes)
    assert len(store.records("sandbox", "seller")) == int(publishes)


@pytest.mark.parametrize("failure", ["AddFixedPriceItem", "createImageFromFile", "VerifyAddFixedPriceItem"])
def test_partial_failures_reconcile_without_duplicate_add(source_files:tuple[Path, Path, dict[str, Any]], store:EbayStore, failure:str) -> None:
    config_path, _, _ = source_files
    api, session = offline_api(config_path)
    session.fail = failure
    with pytest.raises(EbayAPIError):
        ebay_workflow.run(config_path, "ebay-publish", "all", store = store, api = api)
    session.fail = None
    ebay_workflow.run(config_path, "ebay-publish", "all", store = store, api = api)
    assert len(session.items) == 1
    assert store.records("sandbox", "seller")[0].item_id == "123456789"


def test_uncertain_auction_reconciles_using_seller_list(source_files:tuple[Path, Path, dict[str, Any]], store:EbayStore) -> None:
    config_path, ad_path, raw = source_files
    raw["ebay"].update(listing_format = "auction", auction_start_price = 1, auction_duration = "Days_7")
    dicts.save_dict(ad_path, raw)
    api, session = offline_api(config_path)
    session.fail = "AddItem"
    with pytest.raises(EbayAPIError):
        ebay_workflow.run(config_path, "ebay-publish", "new", store = store, api = api)
    session.fail = None
    ebay_workflow.run(config_path, "ebay-publish", "new", store = store, api = api)
    assert [op for op, _ in session.calls].count("AddItem") == 1
    assert [op for op, _ in session.calls].count("GetSellerList") == 1
    assert "GetItem" not in [op for op, _ in session.calls]


def test_empty_reconciliation_never_retries(source_files:tuple[Path, Path, dict[str, Any]], store:EbayStore) -> None:
    config_path, ad_path, raw = source_files
    source_id = store.identify(ad_path, raw)
    assert store.begin_publish("sandbox", "seller", source_id)
    api, session = offline_api(config_path)
    with pytest.raises(EbayAPIError):
        ebay_workflow.run(config_path, "ebay-publish", "new", store = store, api = api)
    assert session.items == []
    assert store.records("sandbox", "seller")[0].status == "pending"


def test_post_publish_write_failure_is_recoverable(source_files:tuple[Path, Path, dict[str, Any]], store:EbayStore,
                                                  monkeypatch:pytest.MonkeyPatch) -> None:
    config_path, _, _ = source_files
    api, session = offline_api(config_path)
    original = store.record_listing

    def cannot_save(*args:Any, **kwargs:Any) -> None:
        raise OSError("simulated full disk")
    monkeypatch.setattr(store, "record_listing", cannot_save)
    with pytest.raises(EbayAPIError):
        ebay_workflow.run(config_path, "ebay-publish", "all", store = store, api = api)
    assert store.records("sandbox", "seller")[0].item_id is None
    monkeypatch.setattr(store, "record_listing", original)
    ebay_workflow.run(config_path, "ebay-publish", "all", store = store, api = api)
    assert len(session.items) == 1
    assert store.records("sandbox", "seller")[0].item_id == "123456789"


def test_environment_and_seller_isolation(store:EbayStore) -> None:
    assert store.begin_publish("sandbox", "seller", SOURCE)
    assert store.begin_publish("production", "seller", SOURCE)
    assert store.begin_publish("sandbox", "other", SOURCE)
    assert not store.begin_publish("sandbox", "SELLER", SOURCE)
    store.record_listing("sandbox", "seller", SOURCE, "123", "Active")
    assert store.get("production", "seller", SOURCE).item_id is None  # type: ignore[union-attr]
    assert store.get("sandbox", "other", SOURCE).item_id is None  # type: ignore[union-attr]


def test_concurrent_intent_has_single_owner(store:EbayStore) -> None:
    other = EbayStore(store.path)
    try:
        assert store.begin_publish("sandbox", "seller", SOURCE)
        assert not other.begin_publish("sandbox", "seller", SOURCE)
    finally:
        other.close()


def test_identity_survives_rename_republication_and_redownload(source_files:tuple[Path, Path, dict[str, Any]], store:EbayStore) -> None:
    _, path, raw = source_files
    source_id = store.identify(path, raw)
    assert store.begin_publish("sandbox", "seller", source_id)
    store.record_listing("sandbox", "seller", source_id, "123", "Active")
    renamed = path.with_name("renamed.json")
    raw["id"] = 99
    renamed.write_text(json.dumps(raw))
    path.unlink()
    assert store.identify(renamed, raw) == source_id
    restored = restore_download_metadata(99, renamed.parent, state_path = store.path)
    assert restored["ebay_source_id"] == source_id
    renamed.unlink()
    restored = restore_download_metadata(99, renamed.parent, state_path = store.path)
    assert restored["ebay_source_id"] == source_id
    raw.pop("ebay_source_id")
    dicts.save_dict(path, raw)
    assert store.identify(path, raw) == source_id
    assert store.get("sandbox", "seller", source_id).item_id == "123"  # type: ignore[union-attr]


def test_ebay_metadata_does_not_change_kleinanzeigen_hash(source_files:tuple[Path, Path, dict[str, Any]]) -> None:
    _, _, raw = source_files
    before = AdPartial.model_validate({key: value for key, value in raw.items() if key != "ebay"}).update_content_hash().content_hash
    raw["ebay_source_id"] = SOURCE
    raw["ebay"]["price"] = "99.95"
    assert AdPartial.model_validate(raw).update_content_hash().content_hash == before


@pytest.mark.parametrize(("changes", "missing"), [
    ({"price": None}, "override"), ({"price": 0}, "override"), ({"type": "WANTED"}, "override"),
    ({"price_type": "GIVE_AWAY", "price": None}, "override"), ({"images": []}, "picture"),
    ({"ebay": {"listing_format": "auction"}}, "auction_start_price"),
])
def test_missing_details_are_explained(source_files:tuple[Path, Path, dict[str, Any]], changes:dict[str, Any], missing:str) -> None:
    config_path, path, raw = source_files
    config = Config.model_validate(dicts.load_dict(str(config_path), "config"))
    raw.update(changes)
    ad = load_ad(raw, config.ad_defaults, str(path))
    result = prepare(ad, path, config.ad_defaults, config.ebay)
    assert any(missing in value for value in result.missing)


def test_best_offer_and_price_override(source_files:tuple[Path, Path, dict[str, Any]]) -> None:
    config_path, path, raw = source_files
    config = Config.model_validate(dicts.load_dict(str(config_path), "config"))
    raw.update(type = "WANTED", price_type = "GIVE_AWAY", price = None)
    raw["ebay"].update(price = "11.20", best_offer = True, aspects = {"Brand": ["Example"]})
    ad = load_ad(raw, config.ad_defaults, str(path))
    result = prepare(ad, path, config.ad_defaults, config.ebay)
    assert not result.missing
    item = build_item(result, config.ebay, SOURCE, UUID, ["https://i.ebayimg.com/photo.jpg"])
    assert item.findtext("BestOfferDetails/BestOfferEnabled") == "true"
    assert item.findtext("StartPrice") == "11.20"
    assert item.findtext("ItemSpecifics/NameValueList/Value") == "Example"
    assert item.findtext("SellerProfiles/SellerShippingProfile/ShippingProfileID") == "1"


def test_api_hides_network_error_details() -> None:
    session = OfflineSession()
    session.fail = "GetUser"
    api = TradingAPI(EbayConfig(account = "seller"), lambda: "not-a-real-token", cast(requests.Session, session))
    with pytest.raises(EbayUncertainError) as error:
        api.seller()
    assert "sensitive" not in str(error.value)


def test_wrong_seller_cannot_publish(source_files:tuple[Path, Path, dict[str, Any]], store:EbayStore) -> None:
    config_path, _, _ = source_files
    api, session = offline_api(config_path)
    session.seller = "someone_else"
    with pytest.raises(EbayAPIError, match = "seller"):
        ebay_workflow.run(config_path, "ebay-publish", "all", store = store, api = api)
    assert session.items == []


@pytest.mark.parametrize(("selector", "expected"), [("new", True), ("all", True), ("42,43", True), ("43", False)])
def test_selectors(source_files:tuple[Path, Path, dict[str, Any]], selector:str, expected:bool) -> None:
    _, path, raw = source_files
    ad = load_ad(raw, AdDefaults(), str(path))
    assert ebay_workflow.selected(selector, ad, None) == expected


@pytest.mark.parametrize("selector", ["due", "changed", "new,42", "", "-1"])
def test_invalid_selectors_fail_before_network(source_files:tuple[Path, Path, dict[str, Any]], store:EbayStore, selector:str) -> None:
    config_path, _, _ = source_files
    api, session = offline_api(config_path)
    with pytest.raises(ValueError, match = "--ads"):
        ebay_workflow.run(config_path, "ebay-publish", selector, store = store, api = api)
    assert session.calls == []


@pytest.mark.parametrize(("command", "option"), [
    ("ebay-prepare", "--refresh"), ("ebay-publish", "--refresh"), ("ebay-login", "--fee-limit=1"), ("ebay-verify", "--fee-limit=0"),
])
def test_ebay_options_are_command_scoped(command:str, option:str) -> None:
    with pytest.raises(SystemExit) as error:
        parse_args(["app", command, option])
    assert error.value.code == 2


def test_numeric_selector_leaves_other_files_untouched(source_files:tuple[Path, Path, dict[str, Any]], store:EbayStore) -> None:
    config_path, path, raw = source_files
    other_path = path.with_name("ad_other.yaml")
    other = {**raw, "id": 43}
    dicts.save_dict(other_path, other)
    original = other_path.read_bytes()
    ebay_workflow.run(config_path, "ebay-prepare", "42", store = store)
    assert other_path.read_bytes() == original
    assert "ebay_source_id" in dicts.load_dict(str(path), "ad")


def test_status_does_not_mutate_source_files(source_files:tuple[Path, Path, dict[str, Any]], store:EbayStore) -> None:
    config_path, path, _ = source_files
    original = path.read_bytes()
    ebay_workflow.run(config_path, "ebay-status", "all", store = store)
    assert path.read_bytes() == original


def test_unrelated_download_files_are_ignored(tmp_path:Path) -> None:
    (tmp_path / "not_an_ad.json").write_text("[1, 2, 3]")
    (tmp_path / "broken.yaml").write_text("ad: [invalid")
    assert restore_download_metadata(42, tmp_path, state_path = tmp_path / "absent.sqlite3") == {}


def test_republish_registers_alias_before_redownload_outside_old_folder(
    source_files:tuple[Path, Path, dict[str, Any]], store:EbayStore, monkeypatch:pytest.MonkeyPatch,
) -> None:
    config_path, path, raw = source_files
    source_id = store.identify(path, raw)
    monkeypatch.setattr(ebay_store, "default_state_path", lambda: store.path)
    config = Config.model_validate(dicts.load_dict(str(config_path), "config"))
    ad = load_ad(raw, config.ad_defaults, str(path))
    persist_published_ad(str(path), ad, raw, 42, 99, AdUpdateStrategy.REPLACE, config = config)
    elsewhere = path.parent / "elsewhere"
    elsewhere.mkdir()
    path.rename(elsewhere / "moved.yaml")
    download_dir = path.parent / "new-downloads"
    download_dir.mkdir()
    restored = restore_download_metadata(99, download_dir, state_path = store.path)
    assert restored["ebay_source_id"] == source_id


def test_complete_rejection_can_retry_with_same_uuid(source_files:tuple[Path, Path, dict[str, Any]], store:EbayStore,
                                                   monkeypatch:pytest.MonkeyPatch) -> None:
    config_path, _, _ = source_files
    api, session = offline_api(config_path)
    original = session.post
    rejected = False

    def first_rejected(url:str, **kwargs:Any) -> requests.Response:
        nonlocal rejected
        if kwargs["headers"].get("X-EBAY-API-CALL-NAME", "").startswith("Add") and not rejected:
            rejected = True
            response = requests.Response()
            response.status_code = 200
            response._content = (f'<AddFixedPriceItemResponse xmlns="{NS}"><Ack>Failure</Ack><Errors>'
                                 '<ErrorCode>123</ErrorCode><LongMessage>sensitive input</LongMessage>'
                                 '</Errors></AddFixedPriceItemResponse>').encode()
            return response
        return original(url, **kwargs)

    monkeypatch.setattr(session, "post", first_rejected)
    with pytest.raises(EbayAPIError):
        ebay_workflow.run(config_path, "ebay-publish", "all", store = store, api = api)
    failed_record = store.records("sandbox", "seller")[0]
    assert failed_record.status == "rejected"
    ebay_workflow.run(config_path, "ebay-publish", "all", store = store, api = api)
    record = store.records("sandbox", "seller")[0]
    assert record.item_id == "123456789"
    assert record.listing_uuid == failed_record.listing_uuid
    assert len(session.items) == 1
    assert any(op == "GetSellerList" for op, _ in session.calls)


def test_reconciliation_reads_all_pages(store:EbayStore) -> None:
    store.begin_publish("sandbox", "seller", SOURCE)
    record = store.records("sandbox", "seller")[0]
    session = OfflineSession()
    session.more_pages[1] = ("<PaginationResult><TotalNumberOfPages>2</TotalNumberOfPages></PaginationResult>"
                             "<HasMoreItems>true</HasMoreItems><ItemArray><Item><ItemID>1</ItemID></Item></ItemArray>")
    session.items.append((SOURCE, "123"))
    api = TradingAPI(EbayConfig(account = "seller"), lambda: "offline", cast(requests.Session, session))
    assert api.reconcile(record) == ("123", "Active")
    assert len(session.calls) == 2


@pytest.mark.parametrize(("currency", "fee"), [("USD", "0"), ("EUR", "NaN"), ("EUR", "-1"), ("EUR", "Infinity")])
def test_unknown_fee_currency_or_amount_blocks_verification(source_files:tuple[Path, Path, dict[str, Any]], store:EbayStore, currency:str, fee:str) -> None:
    config_path, _, _ = source_files
    api, session = offline_api(config_path)
    session.currency, session.fee = currency, fee
    with pytest.raises(EbayAPIError):
        ebay_workflow.run(config_path, "ebay-publish", "all", store = store, api = api)
    assert session.items == []


def test_media_location_is_resolved_on_configured_host(source_files:tuple[Path, Path, dict[str, Any]], monkeypatch:pytest.MonkeyPatch) -> None:
    config_path, path, _ = source_files
    api, session = offline_api(config_path)
    response = requests.Response()
    response.status_code = 201
    response._content = b""
    response.headers["Location"] = "https://apim.sandbox.ebay.com/commerce/media/v1_beta/image/resource-123"
    monkeypatch.setattr(session, "post", lambda *_args, **_kwargs: response)
    captured:list[str] = []

    def resolve(url:str, **kwargs:Any) -> requests.Response:
        captured.append(url)
        assert kwargs["headers"]["Authorization"] == "Bearer offline-access"
        assert kwargs["allow_redirects"] is False
        result = requests.Response()
        result.status_code = 200
        result._content = b'{"imageUrl":"https://i.ebayimg.com/returned.jpg"}'
        return result

    monkeypatch.setattr(session, "get", resolve, raising = False)
    assert api.upload_picture(path.parent / "image.jpg") == "https://i.ebayimg.com/returned.jpg"
    assert captured == ["https://api.sandbox.ebay.com/commerce/media/v1_beta/image/resource-123"]


@pytest.mark.parametrize("location", [
    "https://evil.example/commerce/media/v1_beta/image/123", "http://api.sandbox.ebay.com/commerce/media/v1_beta/image/123",
    "https://api.ebay.com/commerce/media/v1_beta/image/123", "https://api.sandbox.ebay.com/commerce/media/v1_beta/image/%2e%2e",
    "https://api.sandbox.ebay.com/commerce/media/v1_beta/image/123?redirect=secret",
])
def test_media_untrusted_location_is_never_requested(source_files:tuple[Path, Path, dict[str, Any]], monkeypatch:pytest.MonkeyPatch, location:str) -> None:
    config_path, path, _ = source_files
    api, session = offline_api(config_path)
    response = requests.Response()
    response.status_code = 201
    response._content = b"{}"
    response.headers["Location"] = location
    monkeypatch.setattr(session, "post", lambda *_args, **_kwargs: response)

    def forbidden(*args:Any, **kwargs:Any) -> Any:
        pytest.fail("Untrusted Media resource URL was requested")

    monkeypatch.setattr(session, "get", forbidden, raising = False)
    with pytest.raises(EbayAPIError, match = "picture upload"):
        api.upload_picture(path.parent / "image.jpg")


@pytest.mark.parametrize("body", [
    b"<broken", b'<!DOCTYPE response [<!ENTITY secret "sensitive">]><response>&secret;</response>',
    f'<GetUserResponse xmlns="{NS}"><Ack>PartialFailure</Ack></GetUserResponse>'.encode(),
    f'<OtherResponse xmlns="{NS}"><Ack>Success</Ack></OtherResponse>'.encode(),
])
def test_unreadable_api_responses_remain_uncertain(source_files:tuple[Path, Path, dict[str, Any]], monkeypatch:pytest.MonkeyPatch, body:bytes) -> None:
    config_path, _, _ = source_files
    api, session = offline_api(config_path)
    response = requests.Response()
    response.status_code = 200
    response._content = body
    monkeypatch.setattr(session, "post", lambda *_args, **_kwargs: response)
    with pytest.raises(EbayUncertainError) as error:
        api.seller()
    assert "sensitive" not in str(error.value)


def test_api_rejection_explains_fields_and_redacts_access_token(source_files:tuple[Path, Path, dict[str, Any]], monkeypatch:pytest.MonkeyPatch) -> None:
    config_path, _, _ = source_files
    api, session = offline_api(config_path)
    response = requests.Response()
    response.status_code = 200
    response._content = (f'<GetUserResponse xmlns="{NS}"><Ack>Failure</Ack><Errors><ErrorCode>123</ErrorCode>'
                         '<LongMessage>Missing Brand. Token offline-access.</LongMessage></Errors></GetUserResponse>').encode()
    monkeypatch.setattr(session, "post", lambda *_args, **_kwargs: response)
    with pytest.raises(EbayAPIError, match = "Missing Brand") as error:
        api.seller()
    assert "offline-access" not in str(error.value)
    assert "[redacted]" in str(error.value)


def test_failed_intent_write_never_submits(source_files:tuple[Path, Path, dict[str, Any]], store:EbayStore, monkeypatch:pytest.MonkeyPatch) -> None:
    config_path, _, _ = source_files
    api, session = offline_api(config_path)

    def cannot_reserve(*args:Any, **kwargs:Any) -> Any:
        raise OSError("journal write failed")

    monkeypatch.setattr(store, "begin_publish", cannot_reserve)
    with pytest.raises(EbayAPIError):
        ebay_workflow.run(config_path, "ebay-publish", "all", store = store, api = api)
    assert session.items == []


def test_source_sync_uses_writable_handle(
    source_files:tuple[Path, Path, dict[str, Any]], store:EbayStore, monkeypatch:pytest.MonkeyPatch,
) -> None:
    _, path, raw = source_files
    sync = os.fsync

    def require_writable_handle(fd:int) -> None:
        # A zero-byte write reproduces Windows' writable-handle requirement
        # without changing the saved source data.
        os.write(fd, b"")
        sync(fd)

    monkeypatch.setattr(os, "fsync", require_writable_handle)
    source_id = store.identify(path, raw)
    saved = dicts.load_dict(str(path), "ad")
    assert saved["ebay_source_id"] == source_id
    assert saved["description"] == raw["description"]


def test_failed_source_sync_preserves_original_and_rolls_back_identity(
    source_files:tuple[Path, Path, dict[str, Any]], store:EbayStore, monkeypatch:pytest.MonkeyPatch,
) -> None:
    _, path, raw = source_files
    original = path.read_bytes()

    def fail_sync(fd:int) -> None:
        raise OSError("simulated sync failure")

    monkeypatch.setattr(os, "fsync", fail_sync)
    with pytest.raises(OSError, match = "sync failure"):
        store.identify(path, raw)
    assert path.read_bytes() == original
    assert store.lookup_identity(raw) is None
    assert not list(path.parent.glob(".ebay-source-*"))
