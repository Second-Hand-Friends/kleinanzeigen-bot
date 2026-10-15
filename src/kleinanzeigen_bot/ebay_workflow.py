# SPDX-FileCopyrightText: © Tomblarom and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Independent eBay CLI workflow. Preparation and dry runs are strictly local."""
from __future__ import annotations

from decimal import Decimal
from gettext import gettext as _
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from kleinanzeigen_bot import ad_loading
from kleinanzeigen_bot.ebay_api import EbayAPIError, EbayRejectedError, TradingAPI
from kleinanzeigen_bot.ebay_auth import OAuthClient
from kleinanzeigen_bot.ebay_preparation import PreparedListing, build_item, prepare
from kleinanzeigen_bot.ebay_store import EbayStore, ListingRecord
from kleinanzeigen_bot.model.config_model import Config
from kleinanzeigen_bot.utils import dicts, loggers

if TYPE_CHECKING:
    from kleinanzeigen_bot.model.ad_model import Ad
    from kleinanzeigen_bot.model.ebay_model import EbayConfig

LOG = loggers.get_logger(__name__)


def selected(selector:str, ad:Ad, record:ListingRecord | None) -> bool:
    if selector == "all":
        return True
    if selector == "new":
        return record is None or record.item_id is None
    return ad.id is not None and str(ad.id) in {part.strip() for part in selector.split(",")}


def show_prepared(path:str, prepared:PreparedListing) -> None:
    LOG.info("eBay preparation: %s", path)
    LOG.info("Title: %s | Price: %s | Format: %s | Quantity: %s", prepared.title, prepared.price, prepared.settings.listing_format, prepared.settings.quantity)
    LOG.info("Cleaned description (%s links removed):\n%s", prepared.removed_links, prepared.description)
    LOG.info("Pictures: %s", ", ".join(str(path) for path in prepared.pictures))
    if prepared.missing:
        LOG.warning("Missing eBay details: %s", ", ".join(prepared.missing))


def reconcile(store:EbayStore, api:TradingAPI, config:EbayConfig, record:ListingRecord) -> None:
    if record.item_id is not None:
        store.record_listing(config.environment, config.account, record.source_id, record.item_id, api.status(record.item_id))
    elif found := api.reconcile(record):
        store.record_listing(config.environment, config.account, record.source_id, *found)
    elif record.status != "rejected":
        raise EbayAPIError(
            _("Publication outcome is unresolved; source %s remains blocked. Run ebay-status --ads=all --refresh later; do not delete its journal.") %
            record.source_id)


def process_listing(
    prepared:PreparedListing, source_id:str, command:str, config:EbayConfig,
    api:TradingAPI, store:EbayStore, *, fee_limit:Decimal,
) -> None:
    pictures = [api.upload_picture(path) for path in prepared.pictures]
    item = build_item(prepared, config, source_id, uuid4().hex.upper(), pictures)
    fee = api.verify(item, auction = prepared.auction)
    LOG.info("Estimated eBay listing fees: %s %s (limit: %s)", fee, config.currency, fee_limit)
    if command == "ebay-verify":
        return
    if fee > fee_limit:
        raise EbayAPIError(_("Estimated eBay listing fees exceed --fee-limit; publication skipped"))
    if not store.begin_publish(config.environment, config.account, source_id):
        LOG.info("eBay source %s already has a publish journal; skipping submission", source_id)
        return
    record = store.get(config.environment, config.account, source_id)
    if record is None:
        raise EbayAPIError(_("Publish intent was not persisted; publication stopped"))
    item = build_item(prepared, config, source_id, record.listing_uuid, pictures)
    try:
        item_id = api.publish(item, auction = prepared.auction)
        store.record_listing(config.environment, config.account, source_id, item_id, "Active")
    except EbayRejectedError:
        store.record_rejection(config.environment, config.account, source_id)
        raise
    except Exception:
        # An unreadable response and a local write failure all leave
        # the durable barrier intact. Reconciliation on the next run is safe.
        LOG.error("eBay publication requires reconciliation for source %s; no automatic retry", source_id)
        raise
    LOG.info("Published eBay listing %s for source %s", item_id, source_id)


def run(
    config_path:Path, command:str, selector:str, *, dry_run:bool = False,
    refresh:bool = False, fee_limit:Decimal = Decimal(0),
    store:EbayStore | None = None, api:TradingAPI | None = None,
) -> None:
    if refresh and command != "ebay-status":
        raise ValueError(_("--refresh is available only for ebay-status."))
    if not fee_limit.is_finite() or fee_limit < 0:
        raise ValueError(_("Invalid --fee-limit: use a finite nonnegative amount in the configured currency."))
    if selector not in {"new", "all"} and not all(part.strip().isdigit() for part in selector.split(",")):
        raise ValueError(_("eBay --ads must be new, all, or comma-separated Kleinanzeigen IDs"))
    config = Config.model_validate(dicts.load_dict(str(config_path), "config"), context = str(config_path))
    settings = config.ebay
    owned_store = store is None
    store = store if store is not None else EbayStore()
    try:
        online = not dry_run and (command in {"ebay-login", "ebay-verify", "ebay-publish"} or refresh)
        if online:
            if not settings.account or not settings.client_id or not settings.redirect_uri:
                raise ValueError(_("Configure ebay.account, ebay.client_id and ebay.redirect_uri before connecting to eBay"))
            if api is None:
                auth = OAuthClient(settings.environment, settings.account, settings.client_id, settings.redirect_uri)
                if command == "ebay-login":
                    auth.login()
                api = TradingAPI(settings, auth.access_token)
            api.seller()
            if command == "ebay-login":
                LOG.info("eBay seller authorization confirmed for %s (%s)", settings.account, settings.environment)
                return
        loaded = ad_loading.load_ad_configs(config_file_path = str(config_path), ad_file_patterns = config.ad_files, ad_defaults = config.ad_defaults)
        failed = False
        seen:set[str] = set()
        for path, relative, ad, raw in loaded:
            try:
                _process_source(Path(path), relative, ad, raw, config, command, selector = selector, dry_run = dry_run, refresh = refresh,
                                api = api, store = store, fee_limit = fee_limit, seen = seen)
            except Exception as exc:
                LOG.error("eBay processing failed for %s: %s", relative, str(exc))
                failed = True
        if command == "ebay-status" and selector == "all":
            # Include journal entries whose source file is no longer discoverable.
            for record in store.records(settings.environment, settings.account):
                if record.source_id not in seen:
                    if refresh and not dry_run and api is not None:
                        reconcile(store, api, settings, record)
                    updated_record = store.get(settings.environment, settings.account, record.source_id) or record
                    show_record(record.source_id, updated_record)
        if failed:
            raise EbayAPIError(_("One or more eBay sources failed; correct their details before retrying"))
    finally:
        if owned_store:
            store.close()


def show_record(source_id:str, record:ListingRecord | None) -> None:
    LOG.info("eBay source %s | Listing: %s | Status: %s", source_id, record.item_id if record else "-", record.status if record else _("not published"))


def _process_source(
    path:Path, relative:str, ad:Ad, raw:dict[str, Any], config:Config, command:str,
    *, selector:str, dry_run:bool, refresh:bool, api:TradingAPI | None,
    store:EbayStore, fee_limit:Decimal, seen:set[str],
) -> None:
    settings = config.ebay
    if command != "ebay-status" and not ad.active:
        return
    if selector not in {"new", "all"} and (ad.id is None or str(ad.id) not in {part.strip() for part in selector.split(",")}):
        return
    source_id = store.lookup_identity(raw) if command == "ebay-status" and not dry_run else store.identify(path, raw)
    if source_id is not None:
        seen.add(source_id)
    record = store.get(settings.environment, settings.account, source_id) if source_id is not None else None
    if not selected(selector, ad, record):
        return
    if dry_run or command == "ebay-prepare":
        show_prepared(relative, prepare(ad, path, config.ad_defaults, settings))
        return
    if command == "ebay-status":
        if refresh and record is not None and api is not None:
            reconcile(store, api, settings, record)
            record = store.get(settings.environment, settings.account, record.source_id)
        show_record(source_id or "-", record)
        return
    if source_id is None:
        raise ValueError(_("Publish intent was not persisted; publication stopped"))
    if record is not None:
        if record.item_id is None and api is not None:
            reconcile(store, api, settings, record)
        record = store.get(settings.environment, settings.account, source_id)
        if record is not None and (record.item_id is not None or record.status != "rejected"):
            LOG.info("Skipping eBay source %s: an existing mapping or intent prevents relisting", source_id)
            return
    prepared = prepare(ad, path, config.ad_defaults, settings)
    show_prepared(relative, prepared)
    if prepared.missing:
        raise ValueError(_("Complete the missing eBay details before verification or publication"))
    if api is None:
        raise ValueError(_("eBay authorization is required"))
    process_listing(prepared, source_id, command, settings, api, store, fee_limit = fee_limit)
