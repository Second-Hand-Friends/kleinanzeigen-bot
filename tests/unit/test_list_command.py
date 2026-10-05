# SPDX-FileCopyrightText: © Pommel4711 and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Read-only online listing tests using fake browser responses."""

from __future__ import annotations

import copy
import json
from typing import TYPE_CHECKING, Any

import pytest

from kleinanzeigen_bot import published_ads
from kleinanzeigen_bot.app import KleinanzeigenBot
from kleinanzeigen_bot.cli import help_text, parse_args
from kleinanzeigen_bot.published_ads import PublishedAdsFetchIncompleteError
from kleinanzeigen_bot.runtime_config import VALID_COMMANDS
from kleinanzeigen_bot.utils import i18n

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from kleinanzeigen_bot.model.ad_model import Ad

pytestmark = pytest.mark.unit

type PageResponse = dict[str, Any] | TimeoutError


class FakeListBot(KleinanzeigenBot):
    """Exercise the real CLI dispatch and pagination without real browser or file loading."""

    def __init__(self, responses:list[PageResponse]) -> None:
        """Initialize canned API responses and track the read-only session lifecycle."""
        super().__init__()
        self.responses = responses
        self.requests:list[str] = []
        self.bootstrapped = False
        self.logged_in = False
        self.closed = False

    def _bootstrap_runtime(self) -> None:
        """Record runtime initialization before simulated account access."""
        self.bootstrapped = True

    def _check_for_updates(self) -> None:
        """Fail if listing attempts an unrelated update-check request."""
        pytest.fail("Online listing must not perform an update-check request")

    async def _open_logged_in_browser(self) -> None:
        """Record simulated login only after runtime initialization."""
        assert self.bootstrapped
        self.logged_in = True

    async def close_browser_session(self) -> None:
        """Record browser cleanup and clear the simulated login state."""
        self.closed = True
        self.logged_in = False

    async def web_request(
        self,
        url:str,
        method:str = "GET",
        valid_response_codes:int | Iterable[int] = 200,
        headers:dict[str, str] | None = None,
    ) -> Any:
        """Return canned pages while rejecting unexpected requests or account mutations."""
        assert self.logged_in, "Online listing requires an authenticated session"
        assert method == "GET", "Online listing must not mutate account data"
        expected_page = len(self.requests) + 1
        assert url == f"{self.root_url}/m-meine-anzeigen-verwalten.json?sort=DEFAULT&pageNum={expected_page}"
        self.requests.append(url)
        response = self.responses[expected_page - 1]
        if isinstance(response, TimeoutError):
            raise response
        return response

    def load_ads(self, *, ignore_inactive:bool = True, exclude_ads_with_id:bool = True) -> list[tuple[str, Ad, dict[str, Any]]]:
        """Fail if online listing reads local ad definitions."""
        pytest.fail("Online listing must not load local ads")

    def _handle_status(self) -> None:
        """Fail if online listing dispatches the local status command."""
        pytest.fail("Online listing must not invoke the local status command")

    async def _handle_download(self) -> None:
        """Fail if online listing starts downloading ad content."""
        pytest.fail("Online listing must not download ads")

    async def _handle_publish(self) -> None:
        """Fail if online listing attempts to publish an ad."""
        pytest.fail("Online listing must not publish ads")

    async def _handle_update(self) -> None:
        """Fail if online listing attempts to update an ad."""
        pytest.fail("Online listing must not update ads")

    async def _handle_delete(self) -> None:
        """Fail if online listing attempts to delete an ad."""
        pytest.fail("Online listing must not delete ads")

    async def _handle_extend(self) -> None:
        """Fail if online listing attempts to extend an ad."""
        pytest.fail("Online listing must not extend ads")

    async def _handle_reserve_or_activate(self, action:str) -> None:
        """Fail if online listing attempts to change an ad reservation state."""
        pytest.fail("Online listing must not change reservation state")


def _response(ads:list[published_ads.PublishedAd], page:int = 1, last:int = 1) -> dict[str, Any]:
    """Encode one account page with API-style pagination metadata."""
    paging:dict[str, int] = {"pageNum": page, "last": last}
    if page < last:
        paging["next"] = page + 1
    return {"content": json.dumps({"ads": ads, "paging": paging})}


def _snapshot_files(directory:Path) -> dict[Path, bytes]:
    """Capture workspace contents to detect accidental local changes."""
    return {path.relative_to(directory): path.read_bytes() for path in directory.rglob("*") if path.is_file()}


def _list_args(tmp_path:Path) -> list[str]:
    """Build CLI arguments using an isolated portable workspace."""
    return ["app", "list", "--config", str(tmp_path / "config.yaml"), "--workspace-mode", "portable"]


def test_list_is_accepted_and_documented() -> None:
    """Expose the list command in argument parsing and both supported help languages."""
    assert "list" in VALID_COMMANDS
    assert parse_args(["app", "list"]).command == "list"
    assert "list     -" in help_text(language = "en")
    assert "list     -" in help_text(language = "de")


@pytest.mark.parametrize("initial_language", ["en", "de"])
@pytest.mark.parametrize(("language", "description"), [
    ("en", "lists current online ads with ID, title, state, and expiry date"),
    ("de", "Zeigt aktuelle Online-Anzeigen mit ID, Titel, Status und Ablaufdatum an"),
])
def test_list_help_honors_explicit_language_and_preserves_locale(initial_language:str, language:str, description:str) -> None:
    """Honor the requested help language without changing the caller locale."""
    locale = i18n.Locale(initial_language, "TEST")
    i18n.set_current_locale(locale)

    assert f"list     - {description}" in help_text(language = language)
    assert i18n.get_current_locale() == locale


@pytest.mark.asyncio
async def test_list_shows_every_online_page_without_local_or_account_mutations(tmp_path:Path, capsys:pytest.CaptureFixture[str]) -> None:
    """List all account pages while preserving local files and fetched account data."""
    (tmp_path / "config.yaml").write_text("configuration sentinel\n", encoding = "utf-8")
    (tmp_path / "car.yaml").write_text("id: 999\ntitle: Local car sentinel\n", encoding = "utf-8")
    (tmp_path / "car.jpg").write_bytes(b"image sentinel")
    files_before = _snapshot_files(tmp_path)
    ads:list[published_ads.PublishedAd] = [
        {"id": 123, "title": "Audi A3", "state": "active", "endDate": "05.10.2026"},
        {"id": "456", "title": "VW Golf", "state": "paused", "endDate": "06.10.2026"},
        {"id": 789, "title": "Opel Corsa", "state": "inactive", "endDate": "07.10.2026"},
    ]
    responses:list[PageResponse] = [_response(ads[:2], last = 2), _response(ads[2:], page = 2, last = 2)]
    responses_before = copy.deepcopy(responses)
    bot = FakeListBot(responses)

    await bot.run(_list_args(tmp_path))

    output = capsys.readouterr().out
    assert output == (
        "ID: 123\n  title: Audi A3\n  status: active\n  expires: 05.10.2026\n\n"
        "ID: 456\n  title: VW Golf\n  status: reserved\n  expires: 06.10.2026\n\n"
        "ID: 789\n  title: Opel Corsa\n  status: inactive\n  expires: 07.10.2026\n"
    )
    assert len(bot.requests) == 2
    assert bot.bootstrapped
    assert bot.closed
    assert not bot.logged_in
    assert responses == responses_before
    assert _snapshot_files(tmp_path) == files_before


@pytest.mark.asyncio
async def test_list_reports_empty_account_and_closes_browser(tmp_path:Path, capsys:pytest.CaptureFixture[str]) -> None:
    """Report an empty account and clean up the simulated browser session."""
    bot = FakeListBot([_response([])])

    await bot.run(_list_args(tmp_path))

    assert capsys.readouterr().out == "No online ads found.\n"
    assert len(bot.requests) == 1
    assert bot.closed
    assert not bot.logged_in


@pytest.mark.asyncio
@pytest.mark.parametrize("responses", [
    [_response([{"id": 123, "title": "Missing state"}])],
    [{"content": json.dumps({"ads": [{"id": 123, "state": "active"}]})}],
    [_response([{"id": 123, "state": "active", "title": "Incomplete car"}], last = 2), {"content": "invalid JSON"}],
    [_response([{"id": 123, "state": "active", "title": "Incomplete car"}], last = 2), TimeoutError("fake second page timeout")],
])
async def test_list_rejects_malformed_or_partial_results_and_closes_browser(
    responses:list[PageResponse], tmp_path:Path, capsys:pytest.CaptureFixture[str],
) -> None:
    """Reject incomplete account responses without emitting a misleading partial list."""
    bot = FakeListBot(responses)

    with pytest.raises(PublishedAdsFetchIncompleteError):
        await bot.run(_list_args(tmp_path))

    assert not capsys.readouterr().out, "Partial account results must not look like a successful complete listing"
    assert bot.closed
    assert not bot.logged_in


@pytest.mark.parametrize("optional_fields", [{}, {"title": None, "endDate": None}, {"title": "", "endDate": ""}])
@pytest.mark.parametrize(("state", "state_label"), [("under_review", "under_review"), (None, "-")])
def test_render_list_preserves_unknown_status_and_handles_missing_optional_fields(
    optional_fields:dict[str, Any], state:str | None, state_label:str,
) -> None:
    """Render missing fields and unknown statuses without modifying the supplied ad."""
    ad:published_ads.PublishedAd = {"id": 123, "state": state, **optional_fields}
    ad_before = copy.deepcopy(ad)

    assert published_ads.render_published_ads([ad]) == f"ID: 123\n  title: -\n  status: {state_label}\n  expires: -"
    assert ad == ad_before


@pytest.mark.parametrize(("language", "expected", "empty_expected"), [
    ("en", "ID: 123\n  title: Audi A3\n  status: reserved\n  expires: 05.10.2026", "No online ads found."),
    ("de", "ID: 123\n  Titel: Audi A3\n  Status: reserviert\n  Ablaufdatum: 05.10.2026", "Keine Online-Anzeigen gefunden."),
])
def test_render_list_translates_labels_status_and_empty_account(language:str, expected:str, empty_expected:str) -> None:
    """Translate account-list labels, reservation status, and the empty-account message."""
    i18n.set_current_locale(i18n.Locale(language))
    ads:list[published_ads.PublishedAd] = [{"id": 123, "title": "Audi A3", "state": "paused", "endDate": "05.10.2026"}]

    assert published_ads.render_published_ads(ads) == expected
    assert published_ads.render_published_ads([]) == empty_expected
