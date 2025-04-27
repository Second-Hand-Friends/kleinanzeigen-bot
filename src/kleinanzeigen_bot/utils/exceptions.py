# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
from datetime import timedelta


class KleinanzeigenBotError(RuntimeError):
    """Base class for all custom bot-related exceptions."""


class CaptchaEncountered(KleinanzeigenBotError):
    """Raised when a Captcha was detected and auto-restart is enabled."""

    def __init__(self, restart_delay:timedelta) -> None:
        super().__init__()
        self.restart_delay = restart_delay
