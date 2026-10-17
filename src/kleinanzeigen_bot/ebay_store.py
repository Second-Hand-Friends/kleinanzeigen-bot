# SPDX-FileCopyrightText: © Tomblarom and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Durable source identities and seller/environment-scoped publish journal.

A committed pending row is a publication barrier: only its creator may submit.
Other runs reconcile it, and never interpret an empty lookup as permission to retry.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from gettext import gettext as _
from pathlib import Path
from typing import Any
from uuid import uuid4

from platformdirs import user_state_path

from kleinanzeigen_bot.utils import dicts


def default_state_path() -> Path:
    return user_state_path("kleinanzeigen-bot") / "ebay" / "listings.sqlite3"


@dataclass(frozen = True)
class ListingRecord:
    source_id:str
    item_id:str | None
    status:str
    submitted_at:str
    listing_uuid:str


def save_source(path:Path, raw:dict[str, Any]) -> None:
    """Replace source data atomically, preserving YAML comments and JSON format."""
    fd, name = tempfile.mkstemp(prefix = ".ebay-source-", suffix = path.suffix, dir = path.parent)
    temporary = Path(name)
    os.close(fd)
    try:
        dicts.save_dict(temporary, raw)
        # Windows requires a writable handle for fsync/FlushFileBuffers.
        with temporary.open("r+b") as stream:
            os.fsync(stream.fileno())
        temporary.chmod(path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok = True)


class EbayStore:
    def __init__(self, path:Path | None = None) -> None:
        self.path = path if path is not None else default_state_path()
        self.path.parent.mkdir(parents = True, exist_ok = True, mode = 0o700)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        self.connection = sqlite3.connect(self.path, timeout = 30)
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS source_aliases (
                ad_id INTEGER PRIMARY KEY, source_id TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS listings (
                environment TEXT NOT NULL, seller TEXT NOT NULL, source_id TEXT NOT NULL,
                item_id TEXT, status TEXT NOT NULL, submitted_at TEXT NOT NULL,
                listing_uuid TEXT NOT NULL,
                PRIMARY KEY (environment, seller, source_id)
            );
        """)

    def close(self) -> None:
        self.connection.close()

    def identify(self, path:Path, raw:dict[str, Any]) -> str:
        """Register identity before publication and retain aliases across republication.

        Re-read under the SQLite writer lock so concurrent initial preparations
        cannot assign different IDs. Aliases recover identity after redownload
        into a different folder with the same Kleinanzeigen ID.
        """
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            current = dicts.load_dict(str(path), "ad")
            ad_id = current.get("id")
            alias = self.connection.execute("SELECT source_id FROM source_aliases WHERE ad_id=?", (ad_id,)).fetchone()
            source_id = str(current.get("ebay_source_id") or (alias[0] if alias else uuid4().hex)).upper()
            if alias and alias[0] != source_id:
                raise ValueError(_("Conflicting eBay source identities; restore the original source ID before continuing"))
            if ad_id:
                self.connection.execute("INSERT OR IGNORE INTO source_aliases VALUES (?, ?)", (ad_id, source_id))
            if current.get("ebay_source_id") != source_id:
                current["ebay_source_id"] = source_id
                save_source(path, current)
            self.connection.commit()
            raw["ebay_source_id"] = source_id
            return source_id
        except BaseException:
            self.connection.rollback()
            raise

    def lookup_identity(self, raw:dict[str, Any]) -> str | None:
        if raw.get("ebay_source_id"):
            return str(raw["ebay_source_id"]).upper()
        row = self.connection.execute("SELECT source_id FROM source_aliases WHERE ad_id=?", (raw.get("id"),)).fetchone()
        return str(row[0]) if row else None

    def remember_alias(self, ad_id:int, source_id:str) -> None:
        with self.connection:
            row = self.connection.execute("SELECT source_id FROM source_aliases WHERE ad_id=?", (ad_id,)).fetchone()
            if row and row[0] != source_id.upper():
                raise ValueError(_("Conflicting eBay source identities; restore the original source ID before continuing"))
            self.connection.execute("INSERT OR IGNORE INTO source_aliases VALUES (?, ?)", (ad_id, source_id.upper()))

    def get(self, environment:str, seller:str, source_id:str) -> ListingRecord | None:
        row = self.connection.execute(
            "SELECT source_id, item_id, status, submitted_at, listing_uuid FROM listings WHERE environment=? AND seller=? AND source_id=?",
            (environment, seller.casefold(), source_id.upper()),
        ).fetchone()
        return ListingRecord(*row) if row else None

    def begin_publish(self, environment:str, seller:str, source_id:str) -> bool:
        """Atomically reserve publication; any existing row blocks another Add call."""
        with self.connection:
            result = self.connection.execute(
                "INSERT OR IGNORE INTO listings VALUES (?, ?, ?, NULL, 'pending', ?, ?)",
                (environment, seller.casefold(), source_id.upper(), datetime.now(UTC).isoformat(), uuid4().hex.upper()),
            )
            if result.rowcount == 1:
                return True
            result = self.connection.execute(
                "UPDATE listings SET status='pending', submitted_at=? "
                "WHERE environment=? AND seller=? AND source_id=? AND status='rejected' AND item_id IS NULL",
                (datetime.now(UTC).isoformat(), environment, seller.casefold(), source_id.upper()),
            )
        return result.rowcount == 1

    def record_rejection(self, environment:str, seller:str, source_id:str) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE listings SET status='rejected' WHERE environment=? AND seller=? AND source_id=? AND item_id IS NULL",
                (environment, seller.casefold(), source_id.upper()),
            )

    def record_listing(self, environment:str, seller:str, source_id:str, item_id:str, status:str) -> None:
        if not item_id.isdigit():
            raise ValueError(_("eBay returned an invalid listing ID"))
        with self.connection:
            self.connection.execute(
                "UPDATE listings SET item_id=?, status=? WHERE environment=? AND seller=? AND source_id=?",
                (item_id, status, environment, seller.casefold(), source_id.upper()),
            )

    def records(self, environment:str, seller:str) -> list[ListingRecord]:
        return [ListingRecord(*row) for row in self.connection.execute(
            "SELECT source_id, item_id, status, submitted_at, listing_uuid FROM listings WHERE environment=? AND seller=? ORDER BY submitted_at",
            (environment, seller.casefold()),
        )]


def restore_download_metadata(
    ad_id:int, directory:Path, *, state_path:Path | None = None, existing_directory:Path | None = None,
) -> dict[str, Any]:
    """Recover metadata independently of unrelated download reset settings.

    Ordinary downloads inspect only their own destination folder. For a known
    eBay source, search renamed folders by ID and fall back to the durable alias.
    """
    metadata:dict[str, Any] = {}
    database = state_path if state_path is not None else default_state_path()
    if database.is_file():
        connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri = True)
        try:
            row = connection.execute("SELECT source_id FROM source_aliases WHERE ad_id=?", (ad_id,)).fetchone()
            if row:
                metadata["ebay_source_id"] = row[0]
        finally:
            connection.close()
    local_directory = existing_directory if existing_directory is not None else directory
    candidates = directory.rglob("*") if metadata else local_directory.glob("*")
    for path in sorted(candidates):
        if path.suffix not in {".yaml", ".yml", ".json"} or not path.is_file():
            continue
        try:
            data = dicts.load_dict(str(path), "ad")
        except Exception:  # noqa: S112 - metadata discovery ignores unrelated files
            continue
        if not isinstance(data, dict) or data.get("id") != ad_id:
            continue
        if data.get("ebay_source_id"):
            source_id = str(data["ebay_source_id"]).upper()
            if metadata.get("ebay_source_id", source_id) != source_id:
                raise ValueError(_("Conflicting eBay source identities; restore the original source ID before continuing"))
            metadata["ebay_source_id"] = source_id
        if data.get("ebay") is not None:
            metadata["ebay"] = data["ebay"]
    return metadata


def remember_republished_source(ad_id:int, source_id:str) -> None:
    store = EbayStore()
    try:
        store.remember_alias(ad_id, source_id)
    finally:
        store.close()
