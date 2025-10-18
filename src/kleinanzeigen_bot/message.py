# src/kleinanzeigen_bot/message.py
# SPDX-FileCopyrightText: © Contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Final, Iterable

from .utils import loggers
from .utils.exceptions import KleinanzeigenBotError
from .utils.web_scraping_mixin import Browser, By, Element, WebScrapingMixin

if TYPE_CHECKING:
    from nodriver.core.tab import Tab as Page

    from .model.config_model import Config


LOG:Final[loggers.Logger] = loggers.get_logger(__name__)


class Messenger(WebScrapingMixin):
    """Send a message to a single Kleinanzeigen listing using only WebScrapingMixin APIs."""

    def __init__(
        self, browser:Browser, page:Page, config:Config, mein_profil:dict[str, Any]
    ) -> None:
        super().__init__()
        self.config = config
        self.browser = browser
        self.page = page
        self.mein_profil = mein_profil

    # ---------------------------
    # public API
    # ---------------------------
    async def send_message_to_listing(self, listing_url:str, message_text:str) -> bool:
        # LOG.info(i18n.gettext("Opening ad page: %s"), listing_url)
        await self.web_open(listing_url, timeout = 15_000)

        # Kleiner Scroll, damit „Kontakt“-Button gerendert ist
        try:
            await self.web_execute("window.scrollBy(0, 400)")
        except Exception as exc:  # noqa: BLE001
            LOG.debug("Scroll preloading of message button failed", exc_info = exc)

        # 1) „Nachricht“/„Kontakt“-Button öffnen
        open_btn_candidates = [
            (By.ID, "viewad-contact-button"),
            (By.CSS_SELECTOR, "#viewad-contact-button"),
            (By.CSS_SELECTOR, "[data-testid='contact-seller']"),
            (By.TEXT, "Nachricht"),  # best-match Textsuche des Mixins
            (By.TEXT, "Kontakt"),
            (
                By.CSS_SELECTOR,
                "a[href*='nachricht'], a[href*='message'], button[data-testid*='message']",
            ),
        ]
        await self._try_click(
            open_btn_candidates, desc = "message open button", timeout = 6
        )

        # 2) Textarea finden & Text eingeben
        textarea_candidates = [
            (By.CSS_SELECTOR, "textarea[name='message']"),
            (By.CSS_SELECTOR, "#message"),
            (By.CSS_SELECTOR, "[data-testid='message-textarea']"),
            (By.TAG_NAME, "textarea"),
        ]
        textarea = await self._try_find(
            textarea_candidates, desc = "message textarea", timeout = 8
        )
        await textarea.clear_input()
        await textarea.send_keys(message_text)
        await self.web_sleep(300, 600)

        # 3) „Senden“-Button
        send_btn_candidates = [
            (By.TEXT, "Nachricht senden"),
            (By.TEXT, "Senden"),
            (By.CSS_SELECTOR, "[data-testid='send-message']"),
            (By.CSS_SELECTOR, "button[type='submit']"),
        ]
        await self._try_click(
            send_btn_candidates, desc = "send message button", timeout = 6
        )

        # 4) kurze Heuristik/Abschluss
        await self.web_sleep(700, 1200)
        LOG.info("Message flow finished (no error detected).")
        return True

    # ---------------------------
    # local helpers (tiny)
    # ---------------------------
    async def _try_click(
        self, candidates:Iterable[tuple[By, str]], *, desc:str, timeout:int = 5
    ) -> Element:
        last_err:Exception | None = None
        for by, sel in candidates:
            try:
                elem = await self.web_find(by, sel, timeout = timeout)
                await elem.click()
                await self.web_sleep(150, 300)
                return elem
            except Exception as ex:  # noqa: BLE001
                last_err = ex
        raise KleinanzeigenBotError(
            f"Could not locate element for: {desc}"
        ) from last_err

    async def _try_find(
        self, candidates:Iterable[tuple[By, str]], *, desc:str, timeout:int = 5
    ) -> Element:
        last_err:Exception | None = None
        for by, sel in candidates:
            try:
                return await self.web_find(by, sel, timeout = timeout)
            except Exception as ex:  # noqa: BLE001
                last_err = ex
        raise KleinanzeigenBotError(
            f"Could not locate element for: {desc}"
        ) from last_err

    async def fetch_conversations(self, limit:int = 10) -> list[dict[str, str]]:
        page = 0
        conversations:list[dict[str, str]] = []
        while len(conversations) < limit:
            remaining = max(limit - len(conversations), 1)
            page_size = min(remaining, 10)
            convo_url = (
                f"{self.api_root_url}/messagebox/api/users/{self.mein_profil['userId']}"
                f"/conversations?page={page}&size={page_size}"
            )

            data = json.loads((await self.web_request(
                convo_url, headers = self.mein_profil.get("authorization_headers", {})
            ))["content"])
            conversations.extend(data.get("conversations", []))
            if not data.get("_links", {}).get("next"):
                break
            page += 1
        return conversations

    async def fetch_conversation(self, conversation_id:str) -> dict[str, Any]:
        convo_url = (
            f"{self.api_root_url}/messagebox/api/users/{self.mein_profil['userId']}"
            f"/conversations/{conversation_id}?contentWarnings=true"
        )

        data = json.loads((await self.web_request(
            convo_url, headers = self.mein_profil.get("authorization_headers", {})
        ))["content"])
        return data
