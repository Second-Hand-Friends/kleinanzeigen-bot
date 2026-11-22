# SPDX-FileCopyrightText: © Ollornog and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Read the account's message box through the logged-in browser session (``messages`` command).

The web app's inbox talks to the messagebox API gateway. The bot does the same from inside the
logged-in page, so the requests carry the session like the web app's own:

- ``GET  /m-access-token.json`` returns a short-lived access token in the ``authorization`` response header
- ``GET  /messagebox-api/view`` names the account (``{"user": {"id": ...}}``)
- ``GET  <gateway>/messagebox/api/users/<uid>/conversations?page=<n>&size=<n>`` lists conversations
- ``GET  <gateway>/messagebox/api/users/<uid>/conversations?include=_meta`` returns ``numUnread`` and ``lastModified``

These endpoints and their JSON fields are not a documented API. Parsing is therefore tolerant:
missing fields become empty values, and unexpected shapes are logged at debug level.
Listing conversations is read-only; single conversations are never opened, so nothing is marked as read.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from gettext import gettext as _
from typing import TYPE_CHECKING, Any, Final

from .utils import loggers as _loggers
from .utils import page_scripts
from .utils.exceptions import KleinanzeigenBotError

if TYPE_CHECKING:
    from .utils.web_scraping_mixin import WebScrapingMixin

LOG:Final[_loggers.Logger] = _loggers.get_logger(__name__)

GATEWAY_URL:Final[str] = "https://gateway.kleinanzeigen.de"
PAGE_SIZE:Final[int] = 100
MAX_PAGES:Final[int] = 20  # safety limit: 2000 conversations
HTTP_OK:Final[int] = 200
_HTTP_UNAUTHORIZED:Final[frozenset[int]] = frozenset({401, 403})


class MessageboxError(KleinanzeigenBotError):
    """The message box could not be read."""


@dataclass(frozen = True, slots = True)
class Conversation:
    """One conversation of the message box, reduced to what the ``messages`` command shows."""

    id:str
    ad_id:int | None
    ad_title:str
    partner:str
    last_message_at:str
    snippet:str
    unread:bool


def _first(raw:dict[str, Any], *keys:str) -> Any:
    """Return the first non-empty value of ``keys``; the web app's field names are not a contract."""
    for key in keys:
        value = raw.get(key)
        if value is not None and value != "":  # noqa: PLC1901 empty string means missing
            return value
    return None


def _text(value:Any) -> str:
    return "" if value is None else " ".join(str(value).split())


def parse_conversation(raw:Any) -> Conversation | None:
    """Map one raw conversation to a :class:`Conversation`, or return ``None`` if it has no ID."""
    if not isinstance(raw, dict):
        LOG.debug("Skipping conversation with unexpected type: %s", type(raw).__name__)
        return None
    conversation_id = _first(raw, "id", "conversationId")
    if conversation_id is None:
        LOG.debug("Skipping conversation without id (fields: %s)", sorted(raw))
        return None

    # "role" is the account's own role in the conversation, so the partner is the other side.
    role = _text(_first(raw, "role")).lower()
    partner = _first(raw, "sellerName", "userNameSeller") if role == "buyer" else _first(raw, "buyerName", "userNameBuyer")
    partner = partner or _first(raw, "otherParticipantName", "counterPartyName")

    unread_count = _first(raw, "unreadMessagesCount", "numUnreadMessages", "numUnread")
    try:
        unread = int(unread_count) > 0 if unread_count is not None else bool(raw.get("unread"))
    except (TypeError, ValueError):
        unread = bool(raw.get("unread"))

    last_message = raw.get("lastMessage")
    snippet = _first(raw, "textShortTrimmed", "textShort", "lastMessagePreview")
    if snippet is None and isinstance(last_message, dict):
        snippet = _first(last_message, "textShortTrimmed", "textShort", "text")
    elif snippet is None and isinstance(last_message, str):
        snippet = last_message

    raw_ad_id = _first(raw, "adId", "ad_id")
    ad_id = int(raw_ad_id) if str(raw_ad_id or "").isdigit() else None

    conversation = Conversation(
        id = str(conversation_id),
        ad_id = ad_id,
        ad_title = _text(_first(raw, "adTitle", "title")),
        partner = _text(partner),
        last_message_at = _text(_first(raw, "receivedDate", "lastMessageDate", "lastModified")),
        snippet = _text(snippet),
        unread = unread,
    )
    missing = [name for name in ("ad_title", "partner", "last_message_at") if not getattr(conversation, name)]
    if missing:
        LOG.debug("Conversation %s: no value for %s (fields: %s)", conversation.id, ", ".join(missing), sorted(raw))
    return conversation


class MessageboxClient:
    """Read-only access to the message box via ``fetch()`` in the logged-in page."""

    def __init__(self, web:WebScrapingMixin, root_url:str) -> None:
        self._web = web
        self._root_url = root_url
        self._token:str | None = None
        self._user_id:str | None = None

    async def _same_origin_json(self, path:str) -> tuple[dict[str, Any], Any]:
        response = await self._web.web_request(
            f"{self._root_url}{path}", headers = {"Accept": "application/json"}, valid_response_codes = range(100, 600),
        )
        if response["statusCode"] in _HTTP_UNAUTHORIZED:
            raise MessageboxError(_("Kleinanzeigen refused %s (HTTP %s). Are you logged in?") % (path, response["statusCode"]))
        if response["statusCode"] != HTTP_OK:
            raise MessageboxError(_("%s failed with HTTP %s") % (path, response["statusCode"]))
        try:
            return response, json.loads(response.get("content") or "null")
        except (TypeError, ValueError):
            return response, None

    async def _access_token(self) -> str:
        if self._token is None:
            response, _data = await self._same_origin_json("/m-access-token.json")
            token = (response.get("headers") or {}).get("authorization")
            if not token:
                raise MessageboxError(_("Kleinanzeigen did not return an access token. Are you logged in?"))
            self._token = str(token)
        return self._token

    async def user_id(self) -> str:
        """Return the account's messagebox user ID."""
        if self._user_id is None:
            _response, data = await self._same_origin_json("/messagebox-api/view")
            user = data.get("user") if isinstance(data, dict) else None
            user_id = user.get("id") if isinstance(user, dict) else None
            if not user_id:
                LOG.debug("Unexpected messagebox view response: %s", data)
                raise MessageboxError(_("Kleinanzeigen did not return the account's messagebox user ID. Are you logged in?"))
            self._user_id = str(user_id)
        return self._user_id

    async def gateway(self, method:str, path:str, body:Any = None) -> Any:
        """Call the messagebox gateway and return the decoded JSON answer (``None`` if empty).

        :raises MessageboxError: on any status other than 2xx
        """
        token = await self._access_token()
        url = f"{GATEWAY_URL}/messagebox/api/users/{await self.user_id()}{path}"
        LOG.debug(" -> HTTP %s [%s]...", method, url)
        response = await self._web.web_execute(page_scripts.call(
            "messagebox_request", url, method, token, None if body is None else json.dumps(body),
        ))
        if not isinstance(response, dict) or "statusCode" not in response:
            raise MessageboxError(_("Unexpected messagebox response for %s: %r") % (path, response))
        status = response["statusCode"]
        if status in _HTTP_UNAUTHORIZED:
            raise MessageboxError(_("Kleinanzeigen refused %s (HTTP %s). Are you logged in?") % (path, status))
        if not 200 <= status < 300:  # noqa: PLR2004 any 2xx is a success
            raise MessageboxError(_("%s failed with HTTP %s") % (path, status))
        try:
            return json.loads(response.get("content") or "null")
        except (TypeError, ValueError):
            LOG.debug("Messagebox answer for %s is not JSON: %.200s", path, response.get("content"))
            return None

    async def meta(self) -> dict[str, Any]:
        """Return the inbox summary (``numUnread``, ``lastModified``), or an empty dict if the answer has none."""
        data = await self.gateway("GET", "/conversations?include=_meta")
        meta = data.get("_meta") if isinstance(data, dict) else None
        if not isinstance(meta, dict):
            LOG.debug("Unexpected messagebox meta response: %s", data)
            return {}
        return meta

    async def conversations(self) -> list[Conversation]:
        """Return all conversations, newest first as delivered by the gateway."""
        result:list[Conversation] = []
        seen:set[str] = set()
        for page in range(MAX_PAGES):
            data = await self.gateway("GET", f"/conversations?page={page}&size={PAGE_SIZE}")
            raw_conversations = data.get("conversations") if isinstance(data, dict) else None
            if not isinstance(raw_conversations, list):
                # An empty or partial result would look like a complete inbox, so this is an error, not "no conversations".
                LOG.debug("Unexpected messagebox conversations response on page %s: %.500s", page, data)
                raise MessageboxError(_("Unexpected messagebox conversations response on page %s") % page)
            new_on_page = 0
            for raw in raw_conversations:
                conversation = parse_conversation(raw)
                if conversation is not None and conversation.id not in seen:
                    seen.add(conversation.id)
                    result.append(conversation)
                    new_on_page += 1
            # A short page is the last one; a page without new IDs means the gateway ignored "page".
            if len(raw_conversations) < PAGE_SIZE or new_on_page == 0:
                break
        else:
            LOG.warning("Stopped after %s pages of conversations (safety limit); older conversations are not listed.", MAX_PAGES)
        return result


def filter_conversations(conversations:list[Conversation], *, unread_only:bool, ad_ids:set[int] | None) -> list[Conversation]:
    """Keep unread conversations and/or those about the given ads."""
    return [
        conversation for conversation in conversations
        if (not unread_only or conversation.unread) and (ad_ids is None or conversation.ad_id in ad_ids)
    ]


def render_conversations(conversations:list[Conversation], meta:dict[str, Any]) -> str:
    """Render conversations as text blocks, like the ``list`` command renders ads."""
    lines:list[str] = []
    if meta.get("numUnread") is not None:
        lines.extend([f"{_('Unread conversations')}: {meta['numUnread']}", ""])
    if not conversations:
        lines.append(_("No conversations found."))
        return "\n".join(lines)

    blocks = []
    for conversation in conversations:
        ad = conversation.ad_title or "-"
        if conversation.ad_id is not None:
            ad += f" (ID: {conversation.ad_id})"
        header = f"{_('Conversation')}: {conversation.id}"
        if conversation.unread:
            header += f" [{_('unread')}]"
        blocks.append("\n".join([
            header,
            f"  {_('ad')}: {ad}",
            f"  {_('partner')}: {conversation.partner or '-'}",
            f"  {_('last message')}: {conversation.last_message_at or '-'}",
            f"  {_('text')}: {conversation.snippet or '-'}",
        ]))
    lines.append("\n\n".join(blocks))
    return "\n".join(lines)


def conversations_to_json(conversations:list[Conversation], meta:dict[str, Any]) -> str:
    """Render conversations as JSON for scripts; keys stay English and stable."""
    return json.dumps({
        "num_unread": meta.get("numUnread"),
        "last_modified": meta.get("lastModified"),
        "conversations": [asdict(conversation) for conversation in conversations],
    }, ensure_ascii = False, indent = 2)
