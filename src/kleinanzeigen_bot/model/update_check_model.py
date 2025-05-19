# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

from __future__ import annotations

from typing import Literal

from kleinanzeigen_bot.utils.pydantics import ContextualModel


class UpdateCheckConfig(ContextualModel):
    """Configuration for update checking functionality."""
    enabled:bool = True
    channel:Literal["latest", "prerelease"] = "latest"
