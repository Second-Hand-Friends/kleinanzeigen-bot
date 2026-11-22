# SPDX-FileCopyrightText: © Ollornog and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Tests for the read-only messages command using faked message box responses."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from kleinanzeigen_bot import messagebox
from kleinanzeigen_bot.app import KleinanzeigenBot
from kleinanzeigen_bot.cli import help_text, parse_args
from kleinanzeigen_bot.messagebox import Conversation, MessageboxError
from kleinanzeigen_bot.runtime_config import VALID_COMMANDS
from kleinanzeigen_bot.utils import i18n, page_scripts

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

pytestmark = pytest.mark.unit

_TOKEN = "Bearer fake-token"  # noqa: S105 fake value for tests
_UID = "4711"
_BASE = f"{messagebox.GATEWAY_URL}/messagebox/api/users/{_UID}"

_RAW_CONVERSATIONS:list[dict[str, Any]] = [
    {
        "id": "c-1", "role": "Seller", "adId": "111", "adTitle": "Audi A3", "buyerName": "Anna",
        "receivedDate": "2026-11-20T10:00:00.000+0100", "textShortTrimmed": "Is it   still available?", "unreadMessagesCount": 2,
    },
    {
        "id": "c-2", "role": "Buyer", "adId": 222, "adTitle": "VW Golf", "sellerName": "Ben",
        "receivedDate": "2026-11-19T09:00:00.000+0100", "textShortTrimmed": "Thanks!", "unreadMessagesCount": 0,
    },
    {"conversationId": "c-3", "unread": True},
]


def _gateway_script(path:str, method:str = "GET", body:str | None = None) -> str:
    return page_scripts.call("messagebox_request", f"{_BASE}{path}", method, _TOKEN, body)


class FakeMessagesBot(KleinanzeigenBot):
    """Real CLI dispatch with canned same-origin and gateway answers instead of a browser."""

    def __init__(self, gateway:dict[str, Any], posts:dict[tuple[str, str], Any] | None = None) -> None:
        super().__init__()
        self.gateway = gateway
        self.posts = posts or {}
        self.sent:list[tuple[str, str]] = []
        self.requests:list[tuple[str, str]] = []
        self.bootstrapped = False
        self.logged_in = False
        self.closed = False

    def _bootstrap_runtime(self) -> None:
        self.bootstrapped = True

    def _check_for_updates(self) -> None:
        pytest.fail("messages must not perform an update-check request")

    async def _open_logged_in_browser(self) -> None:
        assert self.bootstrapped
        self.logged_in = True

    async def close_browser_session(self) -> None:
        self.closed = True
        self.logged_in = False

    async def web_request(
        self, url:str, method:str = "GET", valid_response_codes:int | Iterable[int] = 200, headers:dict[str, str] | None = None,
    ) -> Any:
        assert self.logged_in
        assert method == "GET", "messages must not change anything online"
        self.requests.append((method, url))
        if url == f"{self.root_url}/m-access-token.json":
            return {"statusCode": 200, "statusMessage": "OK", "headers": {"authorization": _TOKEN}, "content": '{"expiration": 0}'}
        if url == f"{self.root_url}/messagebox-api/view":
            return {"statusCode": 200, "statusMessage": "OK", "headers": {}, "content": json.dumps({"user": {"id": _UID}})}
        raise AssertionError(f"unexpected request {url}")

    async def web_execute(self, jscode:str) -> Any:
        assert self.logged_in
        for path, response in self.gateway.items():
            if jscode == _gateway_script(path):
                self.requests.append(("GET", path))
                return response
        for (path, body), response in self.posts.items():
            if jscode == _gateway_script(path, "POST", body):
                self.sent.append((path, body))
                return response
        raise AssertionError(f"unexpected page script {jscode[-200:]}")


def _ok(data:Any) -> dict[str, Any]:
    return {"statusCode": 200, "statusMessage": "OK", "content": json.dumps(data)}


def _gateway(conversations:list[dict[str, Any]] | None = None, num_unread:int = 2) -> dict[str, Any]:
    return {
        "/conversations?include=_meta": _ok({"_meta": {"numUnread": num_unread, "lastModified": "2026-11-20T10:00:00.000+0100"}}),
        f"/conversations?page=0&size={messagebox.PAGE_SIZE}": _ok({"conversations": _RAW_CONVERSATIONS if conversations is None else conversations}),
    }


def _args(tmp_path:Path, *extra:str) -> list[str]:
    return ["app", "messages", "--config", str(tmp_path / "config.yaml"), "--workspace-mode", "portable", *extra]


def test_messages_is_accepted_and_documented() -> None:
    assert "messages" in VALID_COMMANDS
    parsed = parse_args(["app", "messages", "--unread", "--json", "--ads=111"])
    assert (parsed.command, parsed.unread_only, parsed.json_output, parsed.ads_selector) == ("messages", True, True, "111")
    assert "messages - lists the conversations in your message box" in help_text(language = "en")
    assert "messages - Zeigt die Unterhaltungen im Postfach an" in help_text(language = "de")
    for language in ("en", "de"):
        assert "--unread" in help_text(language = language)
        assert "--json" in help_text(language = language)


@pytest.mark.asyncio
async def test_messages_lists_conversations_read_only(tmp_path:Path, capsys:pytest.CaptureFixture[str]) -> None:
    bot = FakeMessagesBot(_gateway())

    await bot.run(_args(tmp_path))

    assert capsys.readouterr().out == (
        "Unread conversations: 2\n\n"
        "Conversation: c-1 [unread]\n  ad: Audi A3 (ID: 111)\n  partner: Anna\n"
        "  last message: 2026-11-20T10:00:00.000+0100\n  text: Is it still available?\n\n"
        "Conversation: c-2\n  ad: VW Golf (ID: 222)\n  partner: Ben\n"
        "  last message: 2026-11-19T09:00:00.000+0100\n  text: Thanks!\n\n"
        "Conversation: c-3 [unread]\n  ad: -\n  partner: -\n  last message: -\n  text: -\n"
    )
    assert all(method == "GET" for method, _ in bot.requests)
    assert bot.closed


@pytest.mark.asyncio
async def test_messages_filters_unread_and_ads_and_prints_json(tmp_path:Path, capsys:pytest.CaptureFixture[str]) -> None:
    bot = FakeMessagesBot(_gateway())

    await bot.run(_args(tmp_path, "--unread", "--ads=111,333", "--json"))

    output = json.loads(capsys.readouterr().out)
    assert output["num_unread"] == 2
    assert output["last_modified"] == "2026-11-20T10:00:00.000+0100"
    assert output["conversations"] == [{
        "id": "c-1", "ad_id": 111, "ad_title": "Audi A3", "partner": "Anna",
        "last_message_at": "2026-11-20T10:00:00.000+0100", "snippet": "Is it still available?", "unread": True,
    }]


@pytest.mark.asyncio
async def test_messages_unread_shows_only_unread_conversations(tmp_path:Path, capsys:pytest.CaptureFixture[str]) -> None:
    bot = FakeMessagesBot(_gateway())

    await bot.run(_args(tmp_path, "--unread", "--json"))

    assert [conversation["id"] for conversation in json.loads(capsys.readouterr().out)["conversations"]] == ["c-1", "c-3"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "message"), [(401, "Are you logged in"), (503, "failed with HTTP 503")])
async def test_messages_fails_clearly_when_the_site_refuses_the_token(tmp_path:Path, status:int, message:str) -> None:
    class RefusingBot(FakeMessagesBot):
        async def web_request(self, url:str, method:str = "GET", valid_response_codes:int | Iterable[int] = 200,
                              headers:dict[str, str] | None = None) -> Any:
            assert status in valid_response_codes  # type: ignore[operator]  # the status is checked by the client
            return {"statusCode": status, "statusMessage": "x", "headers": {"authorization": _TOKEN}, "content": ""}

    bot = RefusingBot(_gateway())
    with pytest.raises(MessageboxError, match = message):
        await bot.run(_args(tmp_path))


@pytest.mark.asyncio
async def test_messages_reports_an_empty_inbox(tmp_path:Path, capsys:pytest.CaptureFixture[str]) -> None:
    bot = FakeMessagesBot(_gateway([], num_unread = 0))

    await bot.run(_args(tmp_path))

    assert capsys.readouterr().out == "Unread conversations: 0\n\nNo conversations found.\n"


@pytest.mark.asyncio
async def test_messages_rejects_invalid_selector_before_logging_in(tmp_path:Path) -> None:
    bot = FakeMessagesBot(_gateway())

    with pytest.raises(SystemExit) as exc_info:
        await bot.run(_args(tmp_path, "--ads=new"))

    assert exc_info.value.code == 2
    assert not bot.bootstrapped


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "message"), [(401, "Are you logged in"), (500, "failed with HTTP 500")])
async def test_messages_fails_clearly_on_gateway_errors(tmp_path:Path, status:int, message:str) -> None:
    gateway = _gateway()
    gateway["/conversations?include=_meta"] = {"statusCode": status, "statusMessage": "x", "content": ""}
    bot = FakeMessagesBot(gateway)

    with pytest.raises(MessageboxError, match = message):
        await bot.run(_args(tmp_path))

    assert bot.closed


@pytest.mark.asyncio
async def test_missing_access_token_is_reported(tmp_path:Path) -> None:
    class NoTokenBot(FakeMessagesBot):
        async def web_request(self, url:str, method:str = "GET", valid_response_codes:int | Iterable[int] = 200,
                              headers:dict[str, str] | None = None) -> Any:
            return {"statusCode": 200, "statusMessage": "OK", "headers": {}, "content": "{}"}

    bot = NoTokenBot(_gateway())
    with pytest.raises(MessageboxError, match = "did not return an access token"):
        await bot.run(_args(tmp_path))


@pytest.mark.asyncio
async def test_conversations_follow_pages_until_a_short_page() -> None:
    full_page = [{"id": f"c-{n}"} for n in range(messagebox.PAGE_SIZE)]
    gateway = {
        f"/conversations?page=0&size={messagebox.PAGE_SIZE}": _ok({"conversations": full_page}),
        f"/conversations?page=1&size={messagebox.PAGE_SIZE}": _ok({"conversations": [{"id": "c-last"}]}),
    }
    bot = FakeMessagesBot(gateway)
    bot.logged_in = True

    conversations = await messagebox.MessageboxClient(bot, bot.root_url).conversations()

    assert len(conversations) == messagebox.PAGE_SIZE + 1
    assert conversations[-1].id == "c-last"


@pytest.mark.asyncio
async def test_conversations_stop_when_the_gateway_ignores_the_page() -> None:
    full_page = [{"id": f"c-{n}"} for n in range(messagebox.PAGE_SIZE)]
    gateway = {
        f"/conversations?page=0&size={messagebox.PAGE_SIZE}": _ok({"conversations": full_page}),
        f"/conversations?page=1&size={messagebox.PAGE_SIZE}": _ok({"conversations": full_page}),
    }
    bot = FakeMessagesBot(gateway)
    bot.logged_in = True

    conversations = await messagebox.MessageboxClient(bot, bot.root_url).conversations()

    assert len(conversations) == messagebox.PAGE_SIZE
    assert bot.requests[-1] == ("GET", f"/conversations?page=1&size={messagebox.PAGE_SIZE}")


@pytest.mark.parametrize(("raw", "expected"), [
    pytest.param(
        {"id": 5, "role": "buyer", "sellerName": "Seller", "buyerName": "Me", "unreadMessagesCount": "1",
         "lastMessage": {"textShort": "Hi"}, "lastModified": "x", "adId": "12a", "title": "Fallback title"},
        Conversation(id = "5", ad_id = None, ad_title = "Fallback title", partner = "Seller", last_message_at = "x", snippet = "Hi", unread = True),
        id = "buyer-role-and-fallback-fields",
    ),
    pytest.param(
        {"id": "6", "otherParticipantName": "Other", "lastMessage": "Plain text", "unreadMessagesCount": "n/a", "unread": False},
        Conversation(id = "6", ad_id = None, ad_title = "", partner = "Other", last_message_at = "", snippet = "Plain text", unread = False),
        id = "unusual-shapes",
    ),
])
def test_parse_conversation_is_tolerant(raw:dict[str, Any], expected:Conversation) -> None:
    assert messagebox.parse_conversation(raw) == expected


@pytest.mark.parametrize("raw", [{"adTitle": "no id"}, "not a dict", None])
def test_parse_conversation_skips_entries_without_id(raw:Any) -> None:
    assert messagebox.parse_conversation(raw) is None


def test_render_conversations_in_german() -> None:
    i18n.set_current_locale(i18n.Locale("de"))
    conversation = Conversation(id = "c-1", ad_id = 1, ad_title = "Rad", partner = "Anna", last_message_at = "t", snippet = "Hallo", unread = True)

    assert messagebox.render_conversations([conversation], {}) == (
        "Unterhaltung: c-1 [ungelesen]\n  Anzeige: Rad (ID: 1)\n  Gesprächspartner: Anna\n  letzte Nachricht: t\n  Text: Hallo"
    )
    assert messagebox.render_conversations([], {"numUnread": 0}) == "Ungelesene Unterhaltungen: 0\n\nKeine Unterhaltungen gefunden."


# --------------------------------------------------------------------------- #
# reply
# --------------------------------------------------------------------------- #


def _reply_args(tmp_path:Path, *extra:str) -> list[str]:
    return ["app", "reply", "--config", str(tmp_path / "config.yaml"), "--workspace-mode", "portable", *extra]


def _post(conversation_id:str, text:str) -> tuple[str, str]:
    return f"/conversations/{conversation_id}", json.dumps({"message": text})


def test_reply_is_accepted_and_documented() -> None:
    assert "reply" in VALID_COMMANDS
    parsed = parse_args(["app", "reply", "--conversation", " c-1 ", "--text", "Hallo"])
    assert (parsed.command, parsed.conversation_id, parsed.reply_text) == ("reply", "c-1", "Hallo")
    assert parse_args(["app", "reply", "--text-file=msg.txt"]).reply_text_file == "msg.txt"
    assert "reply    - sends one message to an existing conversation" in help_text(language = "en")
    assert "reply    - Sendet eine Nachricht in eine bestehende Unterhaltung" in help_text(language = "de")


@pytest.mark.asyncio
async def test_reply_sends_exactly_one_message(tmp_path:Path, caplog:pytest.LogCaptureFixture) -> None:
    bot = FakeMessagesBot(_gateway(), posts = {_post("c-2", "Yes, still available."): {"statusCode": 201, "statusMessage": "Created", "content": ""}})

    with caplog.at_level("INFO"):
        await bot.run(_reply_args(tmp_path, "--conversation=c-2", "--text", "  Yes, still available.\n"))

    assert bot.sent == [_post("c-2", "Yes, still available.")]
    assert "Sending message to conversation c-2 (ad: VW Golf, partner: Ben)" in caplog.text
    assert "Message sent to conversation c-2." in caplog.text
    assert bot.closed


@pytest.mark.asyncio
async def test_reply_reads_the_text_from_a_file(tmp_path:Path) -> None:
    text_file = tmp_path / "reply.txt"
    text_file.write_text("Hallo Anna,\n\ngerne.\n", encoding = "utf-8")
    bot = FakeMessagesBot(_gateway(), posts = {_post("c-1", "Hallo Anna,\n\ngerne."): {"statusCode": 200, "statusMessage": "OK", "content": "{}"}})

    await bot.run(_reply_args(tmp_path, "--conversation=c-1", f"--text-file={text_file}"))

    assert bot.sent == [_post("c-1", "Hallo Anna,\n\ngerne.")]


@pytest.mark.asyncio
async def test_reply_to_unknown_conversation_sends_nothing(tmp_path:Path) -> None:
    bot = FakeMessagesBot(_gateway())

    with pytest.raises(MessageboxError, match = "Conversation c-9 was not found in your message box; nothing was sent"):
        await bot.run(_reply_args(tmp_path, "--conversation=c-9", "--text=Hallo"))

    assert bot.sent == []


@pytest.mark.asyncio
async def test_reply_reports_a_failed_send(tmp_path:Path) -> None:
    bot = FakeMessagesBot(_gateway(), posts = {_post("c-1", "Hallo"): {"statusCode": 500, "statusMessage": "Error", "content": ""}})

    with pytest.raises(MessageboxError, match = "failed with HTTP 500"):
        await bot.run(_reply_args(tmp_path, "--conversation=c-1", "--text=Hallo"))

    assert bot.sent == [_post("c-1", "Hallo")], "a failed send is not retried"


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [
    pytest.param(["--text=Hallo"], id = "missing-conversation"),
    pytest.param(["--conversation=../users/1", "--text=Hallo"], id = "unsafe-conversation-id"),
    pytest.param(["--conversation=c-1"], id = "missing-text"),
    pytest.param(["--conversation=c-1", "--text=   "], id = "blank-text"),
    pytest.param(["--conversation=c-1", "--text-file=does-not-exist.txt"], id = "unreadable-file"),
])
async def test_reply_rejects_invalid_input_before_logging_in(tmp_path:Path, extra:list[str]) -> None:
    bot = FakeMessagesBot(_gateway())

    with pytest.raises(SystemExit) as exc_info:
        await bot.run(_reply_args(tmp_path, *extra))

    assert exc_info.value.code == 2
    assert not bot.bootstrapped
    assert bot.sent == []


@pytest.mark.asyncio
async def test_reply_rejects_text_and_text_file_together(tmp_path:Path) -> None:
    text_file = tmp_path / "reply.txt"
    text_file.write_text("From the file", encoding = "utf-8")
    bot = FakeMessagesBot(_gateway())

    with pytest.raises(SystemExit) as exc_info:
        await bot.run(_reply_args(tmp_path, "--conversation=c-1", "--text=Hallo", f"--text-file={text_file}"))

    assert exc_info.value.code == 2
    assert bot.sent == []


@pytest.mark.asyncio
async def test_missing_messagebox_user_id_is_reported(tmp_path:Path) -> None:
    class NoUserBot(FakeMessagesBot):
        async def web_request(self, url:str, method:str = "GET", valid_response_codes:int | Iterable[int] = 200,
                              headers:dict[str, str] | None = None) -> Any:
            if url.endswith("/messagebox-api/view"):
                return {"statusCode": 200, "statusMessage": "OK", "headers": {}, "content": "<html>login</html>"}
            return await super().web_request(url, method, valid_response_codes, headers)

    bot = NoUserBot(_gateway())
    with pytest.raises(MessageboxError, match = "did not return the account's messagebox user ID"):
        await bot.run(_args(tmp_path))


@pytest.mark.asyncio
async def test_unexpected_page_script_result_is_reported(tmp_path:Path) -> None:
    """A torn-down page context returns no response dict; that must not pass as an empty inbox."""
    gateway = _gateway()
    gateway["/conversations?include=_meta"] = None
    bot = FakeMessagesBot(gateway)

    with pytest.raises(MessageboxError, match = "Unexpected messagebox response"):
        await bot.run(_args(tmp_path))


@pytest.mark.asyncio
async def test_answers_without_the_expected_json_are_tolerated(tmp_path:Path, capsys:pytest.CaptureFixture[str]) -> None:
    """Non-JSON or differently shaped 2xx answers show an empty inbox instead of crashing."""
    gateway = {
        "/conversations?include=_meta": {"statusCode": 200, "statusMessage": "OK", "content": "not json"},
        f"/conversations?page=0&size={messagebox.PAGE_SIZE}": _ok({"items": []}),
    }
    bot = FakeMessagesBot(gateway)

    await bot.run(_args(tmp_path))

    assert capsys.readouterr().out == "No conversations found.\n"


@pytest.mark.asyncio
async def test_conversations_stop_at_the_page_safety_limit(caplog:pytest.LogCaptureFixture) -> None:
    gateway = {
        f"/conversations?page={page}&size={messagebox.PAGE_SIZE}": _ok({"conversations": [{"id": f"p{page}-{n}"} for n in range(messagebox.PAGE_SIZE)]})
        for page in range(messagebox.MAX_PAGES + 1)
    }
    bot = FakeMessagesBot(gateway)
    bot.logged_in = True

    with caplog.at_level("WARNING"):
        conversations = await messagebox.MessageboxClient(bot, bot.root_url).conversations()

    assert len(conversations) == messagebox.MAX_PAGES * messagebox.PAGE_SIZE
    assert f"Stopped after {messagebox.MAX_PAGES} pages of conversations" in caplog.text
