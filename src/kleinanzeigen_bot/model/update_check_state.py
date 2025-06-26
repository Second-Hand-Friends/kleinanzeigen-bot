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

    def _validate_update_interval(self, interval:str) -> tuple[datetime.timedelta, bool, str]:
        """
        Validate the update check interval string.
        Returns (timedelta, is_valid, reason).
        """
        td = misc.parse_duration(interval)
        # Accept explicit zero (e.g. "0d", "0h", "0m", "0s", "0") as invalid, but distinguish from typos
        if td.total_seconds() == 0:
            if interval.strip() in {"0d", "0h", "0m", "0s", "0"}:
                return td, False, "Interval is zero, which is not allowed."
            return td, False, "Invalid interval format or unsupported unit."
        if td.total_seconds() < 0:
            return td, False, "Negative interval is not allowed."
        return td, True, ""

    def should_check(self, interval:str, channel:str = "latest") -> bool:
        """
        Determine if an update check should be performed based on the provided interval.

        Args:
            interval: The interval string (e.g. '7d', '1d 12h', etc.)
            channel: The update channel ('latest' or 'preview') for fallback default interval.

        Returns:
            bool: True if an update check should be performed, False otherwise.

        Notes:
            - If interval is invalid, negative, zero, or above max, falls back to default interval for the channel.
            - Only returns True if more than the interval has passed since last_check.
            - Always compares in UTC.
        """
        fallback = False
        td = None
        reason = ""
        td, is_valid, reason = self._validate_update_interval(interval)
        total_days = td.total_seconds() / 86400 if td else 0
        epsilon = 1e-6
        if not is_valid:
            if reason == "Interval is zero, which is not allowed.":
                LOG.warning("Interval is zero: %s. Minimum interval is 1d. Using default interval for this run.", interval)
            elif reason == "Invalid interval format or unsupported unit.":
                LOG.warning("Invalid interval format or unsupported unit: %s. Using default interval for this run.", interval)
            elif reason == "Negative interval is not allowed.":
                LOG.warning("Negative interval: %s. Minimum interval is 1d. Using default interval for this run.", interval)
            fallback = True
        elif total_days > MAX_INTERVAL_DAYS + epsilon:
            LOG.warning("Interval too long: %s. Maximum interval is 30d. Using default interval for this run.", interval)
            fallback = True
        elif total_days < 1 - epsilon:
            LOG.warning("Interval too short: %s. Minimum interval is 1d. Using default interval for this run.", interval)
            fallback = True
        if fallback:
            # Fallback to default interval based on channel
            if channel == "preview":
                td = misc.parse_duration("1d")
                LOG.warning("Falling back to default interval: 1d (preview channel). Please fix your config to avoid this warning.")
            else:
                td = misc.parse_duration("7d")
                LOG.warning("Falling back to default interval: 7d (latest channel). Please fix your config to avoid this warning.")
        if not self.last_check:
            return True
        now = datetime.datetime.now(datetime.timezone.utc)
        elapsed = now - self.last_check
        # Compare using integer seconds to avoid microsecond-level flakiness
        return int(elapsed.total_seconds()) > int(td.total_seconds())
