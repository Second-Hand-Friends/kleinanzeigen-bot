# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from kleinanzeigen_bot.utils import dicts
from kleinanzeigen_bot.utils.pydantics import ContextualModel


class UpdateCheckState(ContextualModel):
    """State for update checking functionality."""
    last_check:datetime | None = None

    @classmethod
    def load(cls, state_file:Path) -> UpdateCheckState:
        """Load the update check state from a file.

        Args:
            state_file: The path to the state file.

        Returns:
            The loaded state.
        """
        if state_file.exists():
            if state_file.stat().st_size == 0:
                return cls()
            try:
                data = dicts.load_dict(str(state_file))
                if data and "last_check" in data:
                    data["last_check"] = datetime.fromisoformat(data["last_check"]) if data["last_check"] else None
                return cls.model_validate(data)
            except (json.JSONDecodeError, ValueError):
                return cls()
        return cls()

    def save(self, state_file:Path) -> None:
        """Save the update check state to a file.

        Args:
            state_file: The path to the state file.
        """
        try:
            data = self.model_dump()
            if data["last_check"]:
                data["last_check"] = data["last_check"].isoformat()
            dicts.save_dict(str(state_file), data)
        except PermissionError:
            pass  # Silently ignore permission errors

    def update_last_check(self) -> None:
        """Update the last check time to now."""
        self.last_check = datetime.now(timezone.utc)

    def should_check(self, interval:str) -> bool:
        """Check if an update check should be performed based on the interval.

        Args:
            interval: The interval string (e.g. "7d" for 7 days).

        Returns:
            True if an update check should be performed.
        """
        if not self.last_check:
            return True

        # Parse interval
        try:
            value = int(interval[:-1])
            unit = interval[-1].lower()
        except (ValueError, IndexError):
            return True

        # Calculate time delta
        now = datetime.now(timezone.utc)
        delta = now - self.last_check

        match unit:
            case "s":
                return delta.total_seconds() >= value
            case "m":
                return delta.total_seconds() >= value * 60
            case "h":
                return delta.total_seconds() >= value * 3600
            case "d":
                return delta.days >= value
            case "w":
                return delta.days >= value * 7
            case _:
                return True
