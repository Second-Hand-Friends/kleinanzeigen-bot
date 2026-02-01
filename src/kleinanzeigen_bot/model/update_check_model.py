# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

from __future__ import annotations

from typing import Literal

from pydantic import Field

from kleinanzeigen_bot.utils.pydantics import ContextualModel


class UpdateCheckConfig(ContextualModel):
    enabled:bool = Field(default = True, description = "whether to check for updates on startup")
    channel:Literal["latest", "preview"] = Field(
        default = "latest", description = "which release channel to check (latest = stable, preview = prereleases)", examples = ["latest", "preview"]
    )
    interval:str = Field(
        default = "7d",
        description=(
            "how often to check for updates (e.g., 7d, 1d). "
            "If invalid, too short (<1d), or too long (>30d), "
            "uses defaults: 1d for 'preview' channel, 7d for 'latest' channel"
        ),
        examples = ["7d", "1d", "14d"],
    )
