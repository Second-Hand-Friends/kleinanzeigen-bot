# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

from __future__ import annotations

from typing import Literal

from kleinanzeigen_bot.utils.pydantics import ContextualModel


class UpdateCheckConfig(ContextualModel):
    """Configuration for update checking functionality.

    Attributes:
        enabled: Whether update checking is enabled.
        channel: Which release channel to check ('latest' for stable, 'preview' for prereleases).
        interval: How often to check for updates (e.g. '7d', '1d').
            If the interval is invalid, too short (<1d), or too long (>30d),
            the bot will log a warning and use a default interval for this run:
                - 1d for 'preview' channel
                - 7d for 'latest' channel
            The config file is not changed automatically; please fix your config to avoid repeated warnings.
    """
    enabled:bool = True
    channel:Literal["latest", "preview"] = "latest"
    interval:str = "7d"  # Default interval of 7 days
