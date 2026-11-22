# SPDX-FileCopyrightText: © Ollornog and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Tests for the sync-status command using real ad files and fake manage-ads API responses."""

from __future__ import annotations

import json
import logging
import os
from datetime import date
from typing import TYPE_CHECKING, Any

import pytest

from kleinanzeigen_bot import ad_loading, status_sync
from kleinanzeigen_bot.app import KleinanzeigenBot
from kleinanzeigen_bot.cli import help_text, parse_args
from kleinanzeigen_bot.model.ad_model import AdPartial
from kleinanzeigen_bot.model.config_model import Config
from kleinanzeigen_bot.published_ads import PublishedAdsFetchIncompleteError
from kleinanzeigen_bot.runtime_config import VALID_COMMANDS
from kleinanzeigen_bot.utils import dicts
from kleinanzeigen_bot.utils.exceptions import AdBatchError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from pathlib import Path

    from kleinanzeigen_bot.model.ad_model import Ad

pytestmark = pytest.mark.unit

type PageResponse = dict[str, Any] | TimeoutError

_CONFIG:Config = Config.model_validate({
    "ad_defaults": {"contact": {"name": "dummy_name", "zipcode": "12345"}},
    "login": {"username": "dummy_user", "password": "dummy_password"},
})


class FakeSyncBot(KleinanzeigenBot):
    """Run the real CLI dispatch and ad loading against canned manage-ads API pages instead of a browser."""

    def __init__(self, responses:list[PageResponse], *, during_login:Callable[[], None] | None = None) -> None:
        super().__init__()
        self.responses = responses
        self.during_login = during_login
        self.requests:list[str] = []
        self.bootstrapped = False
        self.logged_in = False
        self.closed = False

    def _bootstrap_runtime(self) -> None:
        self.config = _CONFIG
        self.bootstrapped = True

    def _check_for_updates(self) -> None:
        """No update-check request in tests."""

    async def _open_logged_in_browser(self) -> None:
        assert self.bootstrapped
        self.logged_in = True
        if self.during_login:  # simulates the user editing ad files while the bot logs in and fetches
            self.during_login()

    async def close_browser_session(self) -> None:
        self.closed = True
        self.logged_in = False

    async def web_request(
        self,
        url:str,
        method:str = "GET",
        valid_response_codes:int | Iterable[int] = 200,
        headers:dict[str, str] | None = None,
    ) -> Any:
        """Serve the canned pages and reject anything that could change the account."""
        assert self.logged_in, "sync-status requires an authenticated session"
        assert method == "GET", "sync-status must not change anything online"
        expected_page = len(self.requests) + 1
        assert url == f"{self.root_url}/m-meine-anzeigen-verwalten.json?sort=DEFAULT&pageNum={expected_page}"
        self.requests.append(url)
        response = self.responses[expected_page - 1]
        if isinstance(response, TimeoutError):
            raise response
        return response

    def load_ads(self, *, ignore_inactive:bool = True, exclude_ads_with_id:bool = True) -> list[tuple[str, Ad, dict[str, Any]]]:
        pytest.fail("sync-status must consider every ad file, not the selector-filtered load_ads()")


def _response(ads:list[dict[str, Any]], page:int = 1, last:int = 1) -> dict[str, Any]:
    paging:dict[str, int] = {"pageNum": page, "last": last}
    if page < last:
        paging["next"] = page + 1
    return {"content": json.dumps({"ads": ads, "paging": paging})}


def _rel(name:str) -> str:
    """The relative ad path as logged: as the ad_files glob reports it (OS separator), like other commands."""
    return os.path.join(".", name)


def _args(tmp_path:Path, *extra:str) -> list[str]:
    return ["app", "sync-status", "--config", str(tmp_path / "config.yaml"), "--workspace-mode", "portable", *extra]


def _write_ad(path:Path, body:str) -> str:
    """Write an ad file and append its correct content_hash, so the ad starts out as "unchanged"."""
    path.write_text(body, encoding = "utf-8")
    content_hash = AdPartial.model_validate(dicts.load_dict(str(path))).update_content_hash().content_hash
    text = f"{body}content_hash: {content_hash}\n"
    path.write_text(text, encoding = "utf-8")
    return text


def _ad_body(ad_id:int | None, *, extra:str = "") -> str:
    id_line = f"id: {ad_id}\n" if ad_id is not None else ""
    return (
        "# yaml-language-server: $schema=https://example.invalid/ad.schema.json\n"
        "active: false  # a local publishing flag that sync-status must not touch\n"
        "title: Local car with a long enough title\n"
        "description: |\n"
        "  First line\n"
        "  second line\n"
        "category: '160'\n"
        "price: 100\n"
        "price_type: FIXED\n"
        "republication_interval: 7\n"
        "repost_count: 3\n"
        f"{id_line}"
        "created_on: 2024-12-07T10:00:00\n"
        "updated_on: 2024-12-10T15:20:00\n"
        f"{extra}"
    )


def _is_changed(path:Path) -> bool:
    ad_cfg_orig = dicts.load_dict(str(path))
    ad_cfg = ad_loading.load_ad(ad_cfg_orig, _CONFIG.ad_defaults, str(path))
    return ad_loading.has_ad_content_changed(ad_cfg, ad_cfg_orig)


def test_sync_status_is_accepted_and_documented() -> None:
    assert "sync-status" in VALID_COMMANDS
    assert parse_args(["app", "sync-status"]).command == "sync-status"
    assert "sync-status - writes the online state and expiry date" in help_text(language = "en")
    assert "--ads=all|<id(s)> (sync-status)" in help_text(language = "en")
    assert "sync-status - Übernimmt Online-Status und Ablaufdatum" in help_text(language = "de")
    assert "--ads=all|<id(s)> (sync-status)" in help_text(language = "de")


@pytest.mark.asyncio
async def test_sync_status_writes_only_online_state_and_expiry(tmp_path:Path, caplog:pytest.LogCaptureFixture) -> None:
    """Update state/expiry, keep everything else byte-for-byte, and never mark the ad as changed."""
    changed_path = tmp_path / "ad_111.yaml"
    changed_text = _write_ad(changed_path, _ad_body(111))
    unchanged_path = tmp_path / "ad_222.yaml"
    unchanged_text = _write_ad(unchanged_path, _ad_body(222, extra = "online_state: active\nexpires_on: 2026-12-01\n"))
    offline_path = tmp_path / "ad_333.yaml"
    offline_text = _write_ad(offline_path, _ad_body(333, extra = "online_state: active\n"))
    draft_path = tmp_path / "ad_draft.yaml"
    draft_text = _write_ad(draft_path, _ad_body(None))
    assert not _is_changed(changed_path)

    online_ads:list[dict[str, Any]] = [
        {"id": 111, "title": "Local car", "state": "paused", "endDate": "24.12.2026"},
        {"id": "222", "title": "Other car", "state": "active", "endDate": "01.12.2026"},
        {"id": 444, "title": "Online only", "state": "active", "endDate": "02.12.2026"},
    ]
    bot = FakeSyncBot([_response(online_ads[:2], last = 2), _response(online_ads[2:], page = 2, last = 2)])

    with caplog.at_level(logging.INFO):
        await bot.run(_args(tmp_path))

    assert changed_path.read_text(encoding = "utf-8") == changed_text + "online_state: paused\nexpires_on: '2026-12-24'\n"
    assert not _is_changed(changed_path), "a status sync must not make the ad count as changed"
    assert ad_loading.load_ad(dicts.load_dict(str(changed_path)), _CONFIG.ad_defaults, "ad_111.yaml").expires_on == date(2026, 12, 24)
    assert unchanged_path.read_text(encoding = "utf-8") == unchanged_text
    assert offline_path.read_text(encoding = "utf-8") == offline_text, "ads missing online are reported, not modified"
    assert draft_path.read_text(encoding = "utf-8") == draft_text
    assert len(bot.requests) == 2
    assert bot.closed
    assert f"UPDATED: ad [{_rel('ad_111.yaml')}] (ID: 111): state - -> paused, expires - -> 2026-12-24" in caplog.text
    assert f"UNCHANGED: ad [{_rel('ad_222.yaml')}] (ID: 222)" in caplog.text
    assert f"NOT FOUND ONLINE: ad [{_rel('ad_333.yaml')}] (ID: 333)" in caplog.text
    assert "Online ad 444 ('Online only') has no local ad file." in caplog.text
    assert "DONE: Checked 3 ads: 1 updated, 1 unchanged, 1 not found online, 0 failed" in caplog.text


@pytest.mark.asyncio
async def test_sync_status_with_ids_syncs_only_selected_ads(tmp_path:Path, caplog:pytest.LogCaptureFixture) -> None:
    selected_path = tmp_path / "ad_111.yaml"
    _write_ad(selected_path, _ad_body(111))
    other_path = tmp_path / "ad_222.yaml"
    other_text = _write_ad(other_path, _ad_body(222))
    online_ads = [
        {"id": 111, "title": "Local car", "state": "active", "endDate": "24.12.2026"},
        {"id": 222, "title": "Other car", "state": "active", "endDate": "25.12.2026"},
        {"id": 444, "title": "Online only", "state": "active", "endDate": "26.12.2026"},
    ]
    bot = FakeSyncBot([_response(online_ads)])

    with caplog.at_level(logging.INFO):
        await bot.run(_args(tmp_path, "--ads=111,999"))

    assert dicts.load_dict(str(selected_path))["online_state"] == "active"
    assert other_path.read_text(encoding = "utf-8") == other_text
    assert "No local ad file found for ID 999" in caplog.text
    assert "has no local ad file" not in caplog.text, "online-only ads are only reported for --ads=all"


@pytest.mark.asyncio
async def test_sync_status_rejects_invalid_selector_before_loading_anything(tmp_path:Path) -> None:
    bot = FakeSyncBot([])

    with pytest.raises(SystemExit) as exc_info:
        await bot.run(_args(tmp_path, "--ads=due"))

    assert exc_info.value.code == 2
    assert not bot.bootstrapped
    assert not bot.requests


@pytest.mark.asyncio
async def test_sync_status_with_unknown_ids_does_not_log_in(tmp_path:Path, caplog:pytest.LogCaptureFixture) -> None:
    _write_ad(tmp_path / "ad_draft.yaml", _ad_body(None))
    bot = FakeSyncBot([])

    with caplog.at_level(logging.INFO):
        await bot.run(_args(tmp_path, "--ads=999"))

    assert not bot.requests
    assert not bot.logged_in
    assert "DONE: No local ads with the given IDs found." in caplog.text


@pytest.mark.asyncio
async def test_sync_status_all_reports_online_ads_even_without_published_local_ads(
    tmp_path:Path, caplog:pytest.LogCaptureFixture,
) -> None:
    draft_path = tmp_path / "ad_draft.yaml"
    draft_text = _write_ad(draft_path, _ad_body(None))
    bot = FakeSyncBot([_response([{"id": 444, "title": "Online only", "state": "active"}])])

    with caplog.at_level(logging.INFO):
        await bot.run(_args(tmp_path))

    assert len(bot.requests) == 1
    assert "Online ad 444 ('Online only') has no local ad file." in caplog.text
    assert "DONE: Checked 0 ads" in caplog.text
    assert draft_path.read_text(encoding = "utf-8") == draft_text


@pytest.mark.asyncio
async def test_sync_status_keeps_edits_made_after_loading(tmp_path:Path) -> None:
    """The file is re-read before writing, so an edit made during login and fetching survives."""
    ad_path = tmp_path / "ad_111.yaml"
    _write_ad(ad_path, _ad_body(111))

    def edit_price() -> None:
        ad_path.write_text(ad_path.read_text(encoding = "utf-8").replace("price: 100\n", "price: 150  # edited meanwhile\n"), encoding = "utf-8")

    bot = FakeSyncBot([_response([{"id": 111, "state": "active", "endDate": "24.12.2026"}])], during_login = edit_price)

    await bot.run(_args(tmp_path))

    text = ad_path.read_text(encoding = "utf-8")
    assert "price: 150  # edited meanwhile\n" in text
    assert text.endswith("online_state: active\nexpires_on: '2026-12-24'\n")


@pytest.mark.asyncio
async def test_sync_status_skips_files_whose_id_changed_after_loading(tmp_path:Path, caplog:pytest.LogCaptureFixture) -> None:
    ad_path = tmp_path / "ad_111.yaml"
    _write_ad(ad_path, _ad_body(111))
    edited:list[str] = []

    def change_id() -> None:
        ad_path.write_text(ad_path.read_text(encoding = "utf-8").replace("id: 111\n", "id: 112\n"), encoding = "utf-8")
        edited.append(ad_path.read_text(encoding = "utf-8"))

    bot = FakeSyncBot([_response([{"id": 111, "state": "active"}])], during_login = change_id)

    with caplog.at_level(logging.INFO), pytest.raises(AdBatchError, match = "Failed to save 1 ad"):
        await bot.run(_args(tmp_path))

    assert ad_path.read_text(encoding = "utf-8") == edited[0]
    assert "changed its ID since it was loaded" in caplog.text


@pytest.mark.asyncio
async def test_sync_status_reports_files_removed_after_loading(tmp_path:Path, caplog:pytest.LogCaptureFixture) -> None:
    ad_path = tmp_path / "ad_111.yaml"
    _write_ad(ad_path, _ad_body(111))
    bot = FakeSyncBot([_response([{"id": 111, "state": "active"}])], during_login = ad_path.unlink)

    with caplog.at_level(logging.INFO), pytest.raises(AdBatchError, match = "Failed to save 1 ad"):
        await bot.run(_args(tmp_path))

    assert not ad_path.exists()
    assert "could not re-read ad" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("responses", [
    [_response([{"id": 111, "title": "Missing state"}])],
    [_response([{"id": 111, "state": "active"}], last = 2), TimeoutError("fake second page timeout")],
])
async def test_sync_status_fails_without_changes_on_incomplete_online_list(responses:list[PageResponse], tmp_path:Path) -> None:
    """An incomplete online list would wrongly report ads as missing online, so nothing is written."""
    ad_path = tmp_path / "ad_111.yaml"
    ad_text = _write_ad(ad_path, _ad_body(111))
    bot = FakeSyncBot(responses)

    with pytest.raises(PublishedAdsFetchIncompleteError):
        await bot.run(_args(tmp_path))

    assert ad_path.read_text(encoding = "utf-8") == ad_text
    assert bot.closed


def _loaded(path:Path) -> status_sync.LoadedAd:
    ad_cfg_orig = dicts.load_dict(str(path))
    return (str(path), path.name, ad_loading.load_ad(ad_cfg_orig, _CONFIG.ad_defaults, str(path)), ad_cfg_orig)


def test_missing_expiry_date_removes_the_stale_local_value(tmp_path:Path) -> None:
    ad_path = tmp_path / "ad_111.yaml"
    _write_ad(ad_path, _ad_body(111, extra = "online_state: active\nexpires_on: 2026-12-01\n"))

    result = status_sync.sync_ad_status([_loaded(ad_path)], [{"id": 111, "state": "inactive"}])

    assert result.updated == 1
    saved = dicts.load_dict(str(ad_path))
    assert saved["online_state"] == "inactive"
    assert "expires_on" not in saved


def test_invalid_expiry_date_keeps_the_local_value(tmp_path:Path, caplog:pytest.LogCaptureFixture) -> None:
    ad_path = tmp_path / "ad_111.yaml"
    _write_ad(ad_path, _ad_body(111, extra = "online_state: active\nexpires_on: 2026-12-01\n"))

    with caplog.at_level(logging.WARNING):
        result = status_sync.sync_ad_status([_loaded(ad_path)], [{"id": 111, "state": "paused", "endDate": "soon"}])

    assert result.updated == 1
    saved = dicts.load_dict(str(ad_path))
    assert saved["online_state"] == "paused"
    assert saved["expires_on"] == date(2026, 12, 1)
    assert "invalid expiry date from kleinanzeigen.de: soon" in caplog.text


def test_json_ad_files_are_supported(tmp_path:Path) -> None:
    ad_path = tmp_path / "ad_111.json"
    ad_path.write_text(json.dumps({
        "id": 111, "title": "Local car with a long enough title", "description": "Text", "category": "160",
        "price": 100, "price_type": "FIXED",
    }), encoding = "utf-8")

    status_sync.sync_ad_status([_loaded(ad_path)], [{"id": 111, "state": "active", "endDate": "24.12.2026"}])

    saved = json.loads(ad_path.read_text(encoding = "utf-8"))
    assert saved["online_state"] == "active"
    assert saved["expires_on"] == "2026-12-24"


def test_save_failure_is_reported_after_processing_the_other_ads(tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
    failing_path = tmp_path / "ad_111.yaml"
    failing_text = _write_ad(failing_path, _ad_body(111))
    other_path = tmp_path / "ad_222.yaml"
    _write_ad(other_path, _ad_body(222))
    real_save_dict = dicts.save_dict

    def save_dict(filepath:str, content:dict[str, Any], *, header:str | None = None) -> None:
        if filepath == str(failing_path):
            raise PermissionError("read-only")
        real_save_dict(filepath, content, header = header)

    monkeypatch.setattr(dicts, "save_dict", save_dict)
    online_ads = [{"id": 111, "state": "active"}, {"id": 222, "state": "active"}]

    with pytest.raises(AdBatchError, match = "Failed to save 1 ad"):
        status_sync.sync_ad_status([_loaded(failing_path), _loaded(other_path)], online_ads)

    assert failing_path.read_text(encoding = "utf-8") == failing_text
    assert dicts.load_dict(str(other_path))["online_state"] == "active"


@pytest.mark.parametrize(("value", "expected"), [
    ("05.10.2026", date(2026, 10, 5)),
    (" 05.10.2026 ", date(2026, 10, 5)),
    (None, None),
    ("", None),
])
def test_parse_expiry_date(value:Any, expected:date | None) -> None:
    assert status_sync.parse_expiry_date(value) == expected


@pytest.mark.parametrize("value", ["2026-10-05", "31.02.2026", 20261005])
def test_parse_expiry_date_rejects_invalid_values(value:Any) -> None:
    with pytest.raises(ValueError, match = ".+"):
        status_sync.parse_expiry_date(value)


def test_online_ad_with_non_numeric_id_is_skipped(tmp_path:Path, caplog:pytest.LogCaptureFixture) -> None:
    ad_path = tmp_path / "ad_111.yaml"
    ad_text = _write_ad(ad_path, _ad_body(111))

    with caplog.at_level(logging.WARNING):
        result = status_sync.sync_ad_status([_loaded(ad_path)], [{"id": "not-a-number", "state": "active"}])

    assert result.not_found_online == 1
    assert ad_path.read_text(encoding = "utf-8") == ad_text
    assert "Skipping online ad with non-numeric id: not-a-number" in caplog.text


def test_expiry_written_by_a_previous_sync_counts_as_unchanged(tmp_path:Path) -> None:
    """sync-status writes the date as a quoted ISO string; reading it back must compare equal, not rewrite the file."""
    ad_path = tmp_path / "ad_111.yaml"
    ad_text = _write_ad(ad_path, _ad_body(111, extra = "online_state: active\nexpires_on: '2026-12-01'\n"))

    result = status_sync.sync_ad_status([_loaded(ad_path)], [{"id": 111, "state": "active", "endDate": "01.12.2026"}])

    assert result.unchanged == 1
    assert ad_path.read_text(encoding = "utf-8") == ad_text


def test_unreadable_stored_expiry_is_replaced(tmp_path:Path) -> None:
    """An invalid value edited into the file after loading is replaced by the online date."""
    ad_path = tmp_path / "ad_111.yaml"
    _write_ad(ad_path, _ad_body(111, extra = "online_state: active\n"))
    loaded = _loaded(ad_path)
    ad_path.write_text(ad_path.read_text(encoding = "utf-8") + "expires_on: soon\n", encoding = "utf-8")

    result = status_sync.sync_ad_status([loaded], [{"id": 111, "state": "active", "endDate": "01.12.2026"}])

    assert result.updated == 1
    assert dicts.load_dict(str(ad_path))["expires_on"] == "2026-12-01"
