# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

from __future__ import annotations

import datetime
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

from kleinanzeigen_bot.utils import dicts, loggers, misc
from kleinanzeigen_bot.utils.pydantics import ContextualModel

LOG = loggers.get_logger(__name__)

# Current version of the state file format
CURRENT_STATE_VERSION = 1
# Maximum allowed interval in days
MAX_INTERVAL_DAYS = 30


class UpdateCheckState(ContextualModel):
    """State for update checking functionality."""
    version:int = CURRENT_STATE_VERSION
    last_check:datetime.datetime | None = None

    @classmethod
    def _parse_timestamp(cls, timestamp_str:str) -> datetime.datetime | None:
        """Parse a timestamp string and ensure it's in UTC.

        Args:
            timestamp_str: The timestamp string to parse.

        Returns:
            The parsed timestamp in UTC, or None if parsing fails.
        """
        try:
            timestamp = datetime.datetime.fromisoformat(timestamp_str)
            if timestamp.tzinfo is None:
                # If no timezone info, assume UTC
                timestamp = timestamp.replace(tzinfo = datetime.timezone.utc)
            elif timestamp.tzinfo != datetime.timezone.utc:
                # Convert to UTC if in a different timezone
                timestamp = timestamp.astimezone(datetime.timezone.utc)
            return timestamp
        except ValueError as e:
            LOG.warning("Invalid timestamp format in state file: %s", e)
            return None

    @classmethod
    def load(cls, state_file:Path) -> UpdateCheckState:
        """Load the update check state from a file.

        Args:
            state_file: The path to the state file.

        Returns:
            The loaded state.
        """
        if not state_file.exists():
            return cls()

        if state_file.stat().st_size == 0:
            return cls()

        try:
            data = dicts.load_dict(str(state_file))
            if not data:
                return cls()

            # Handle version migration
            version = data.get("version", 0)
            if version < CURRENT_STATE_VERSION:
                LOG.info("Migrating update check state from version %d to %d", version, CURRENT_STATE_VERSION)
                data = cls._migrate_state(data, version)

            # Parse last_check timestamp
            if "last_check" in data:
                data["last_check"] = cls._parse_timestamp(data["last_check"])

            return cls.model_validate(data)
        except (json.JSONDecodeError, ValueError) as e:
            LOG.warning("Failed to load update check state: %s", e)
            return cls()

    @classmethod
    def _migrate_state(cls, data:dict[str, Any], from_version:int) -> dict[str, Any]:
        """Migrate state data from an older version to the current version.

        Args:
            data: The state data to migrate.
            from_version: The version of the state data.

        Returns:
            The migrated state data.
        """
        # Version 0 to 1: Add version field
        if from_version == 0:
            data["version"] = CURRENT_STATE_VERSION
            LOG.debug("Migrated state from version 0 to 1: Added version field")

        return data

    def save(self, state_file:Path) -> None:
        """Save the update check state to a file.

        Args:
            state_file: The path to the state file.
        """
        try:
            data = self.model_dump()
            if data["last_check"]:
                # Ensure timestamp is in UTC before saving
                if data["last_check"].tzinfo != datetime.timezone.utc:
                    data["last_check"] = data["last_check"].astimezone(datetime.timezone.utc)
                data["last_check"] = data["last_check"].isoformat()
            dicts.save_dict(str(state_file), data)
        except PermissionError:
            LOG.warning("Permission denied when saving update check state to %s", state_file)
        except Exception as e:
            LOG.warning("Failed to save update check state: %s", e)

    def update_last_check(self) -> None:
        """Update the last check time to now in UTC."""
        self.last_check = datetime.datetime.now(datetime.timezone.utc)

    def should_check(self, interval:str) -> bool:
        """
        Determine if an update check should be performed based on the provided interval.

        Args:
            interval: The interval string (e.g. '7d', '1d 12h', etc.)

        Returns:
            bool: True if an update check should be performed, False otherwise.

        Notes:
            - Returns True if interval is invalid, negative, zero, or above max.
            - Only returns True if more than the interval has passed since last_check.
            - Always compares in UTC.
        """
        try:
            td = misc.parse_duration(interval)
        except Exception as e:
            LOG.warning("Invalid interval format: %s. Error: %s", interval, e)
            return True
        total_days = td.total_seconds() / 86400
        epsilon = 1e-6
        if total_days > MAX_INTERVAL_DAYS + epsilon:
            LOG.warning("Interval too long: %s. Maximum interval is 30d.", interval)
            return True
        if total_days < 1 - epsilon:
            LOG.warning("Interval too short: %s. Minimum interval is 1d.", interval)
            return True
        if not self.last_check:
            return True
        now = datetime.datetime.now(datetime.timezone.utc)
        elapsed = now - self.last_check
        # Compare using integer seconds to avoid microsecond-level flakiness
        return int(elapsed.total_seconds()) > int(td.total_seconds())
