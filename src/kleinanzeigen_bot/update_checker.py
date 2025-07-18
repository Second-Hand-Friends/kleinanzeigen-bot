# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import colorama
import requests

if TYPE_CHECKING:
    from kleinanzeigen_bot.model.config_model import Config

try:
    from kleinanzeigen_bot._version import __version__
except ImportError:
    __version__ = "unknown"

from kleinanzeigen_bot.model.update_check_state import UpdateCheckState

logger = logging.getLogger(__name__)

colorama.init()


class UpdateChecker:
    """Checks for updates to the bot."""

    def __init__(self, config:"Config") -> None:
        """Initialize the update checker.

        Args:
            config: The bot configuration.
        """
        self.config = config
        self.state_file = Path(".temp") / "update_check_state.json"
        self.state_file.parent.mkdir(exist_ok = True)  # Ensure .temp directory exists
        self.state = UpdateCheckState.load(self.state_file)

    def get_local_version(self) -> str | None:
        """Get the local version of the bot.

        Returns:
            The local version string, or None if it cannot be determined.
        """
        return __version__

    def _get_commit_hash(self, version:str) -> str | None:
        """Extract the commit hash from a version string.

        Args:
            version: The version string to extract the commit hash from.

        Returns:
            The commit hash, or None if it cannot be extracted.
        """
        if "+" in version:
            return version.split("+")[1]
        return None

    def _get_release_commit(self, tag_name:str) -> str | None:
        """Get the commit hash for a release tag.

        Args:
            tag_name: The release tag name (e.g. 'latest').

        Returns:
            The commit hash, or None if it cannot be determined.
        """
        try:
            response = requests.get(
                f"https://api.github.com/repos/Second-Hand-Friends/kleinanzeigen-bot/releases/tags/{tag_name}",
                timeout = 10
            )
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict) and "target_commitish" in data:
                return str(data["target_commitish"])
            return None
        except Exception as e:
            logger.warning("Could not get release commit: %s", e)
            return None

    def _get_commit_date(self, commit:str) -> datetime | None:
        """Get the commit date for a commit hash.

        Args:
            commit: The commit hash.

        Returns:
            The commit date, or None if it cannot be determined.
        """
        try:
            response = requests.get(
                f"https://api.github.com/repos/Second-Hand-Friends/kleinanzeigen-bot/commits/{commit}",
                timeout = 10
            )
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict) and "commit" in data and "author" in data["commit"] and "date" in data["commit"]["author"]:
                return datetime.fromisoformat(data["commit"]["author"]["date"].replace("Z", "+00:00"))
            return None
        except Exception as e:
            logger.warning("Could not get commit date: %s", e)
            return None

    def _get_short_commit_hash(self, commit:str) -> str:
        """Get the short version of a commit hash.

        Args:
            commit: The full commit hash.

        Returns:
            The short commit hash (first 7 characters).
        """
        return commit[:7]

    def check_for_updates(self, *, skip_interval_check:bool = False) -> None:
        """Check for updates to the bot.

        Args:
            skip_interval_check: If True, bypass the interval check and force an update check.
        """
        if not self.config.update_check.enabled:
            return

        # Check if we should perform an update check based on the interval
        if not skip_interval_check and not self.state.should_check(self.config.update_check.interval, self.config.update_check.channel):
            return

        local_version = self.get_local_version()
        if not local_version:
            logger.warning("Could not determine local version.")
            return

        local_commit = self._get_commit_hash(local_version)
        if not local_commit:
            logger.warning("Could not determine local commit hash.")
            return

        # --- Fetch release info from GitHub using correct endpoint per channel ---
        try:
            if self.config.update_check.channel == "latest":
                # Use /releases/latest endpoint for stable releases
                response = requests.get(
                    "https://api.github.com/repos/Second-Hand-Friends/kleinanzeigen-bot/releases/latest",
                    timeout = 10
                )
                response.raise_for_status()
                release = response.json()
                # Defensive: ensure it's not a prerelease
                if release.get("prerelease", False):
                    logger.warning("Latest release from GitHub is a prerelease, but 'latest' channel expects a stable release.")
                    return
            elif self.config.update_check.channel == "preview":
                # Use /releases endpoint and select the most recent prerelease
                response = requests.get(
                    "https://api.github.com/repos/Second-Hand-Friends/kleinanzeigen-bot/releases",
                    timeout = 10
                )
                response.raise_for_status()
                releases = response.json()
                # Find the most recent prerelease
                release = next((r for r in releases if r.get("prerelease", False)), None)
                if not release:
                    logger.warning("No prerelease found for 'preview' channel.")
                    return
            else:
                logger.warning("Unknown update channel: %s", self.config.update_check.channel)
                return
        except Exception as e:
            logger.warning("Could not get releases: %s", e)
            return

        # Get release commit
        try:
            release_commit = self._get_release_commit(release["tag_name"])
        except Exception as e:
            logger.warning("Failed to get release commit: %s", e)
            return
        if not release_commit:
            logger.warning("Could not determine release commit hash.")
            return

        # Get commit dates
        try:
            local_commit_date = self._get_commit_date(local_commit)
            release_commit_date = self._get_commit_date(release_commit)
        except Exception as e:
            logger.warning("Failed to get commit dates: %s", e)
            return
        if not local_commit_date or not release_commit_date:
            logger.warning("Could not determine commit dates for comparison.")
            return

        if local_commit == release_commit:
            # If the commit hashes are identical, we are on the latest version. Do not proceed to other checks.
            logger.info(
                "You are on the latest version: %s (compared to %s in channel %s)",
                local_version,
                self._get_short_commit_hash(release_commit),
                self.config.update_check.channel
            )
            return
        # All commit dates are in UTC; append ' UTC' to timestamps in logs for clarity.
        if local_commit_date < release_commit_date:
            logger.warning(
                "A new version is available: %s from %s UTC (current: %s from %s UTC, channel: %s)",
                self._get_short_commit_hash(release_commit),
                release_commit_date.strftime("%Y-%m-%d %H:%M:%S"),
                local_version,
                local_commit_date.strftime("%Y-%m-%d %H:%M:%S"),
                self.config.update_check.channel
            )
            if release.get("body"):
                logger.info("Release notes:\n%s", release["body"])
        else:
            logger.info(
                "You are on a different commit than the release for channel '%s' (tag: %s). This may mean you are ahead, behind, or on a different branch. "
                "Local commit: %s (%s UTC), Release commit: %s (%s UTC)",
                self.config.update_check.channel,
                release.get("tag_name", "unknown"),
                self._get_short_commit_hash(local_commit),
                local_commit_date.strftime("%Y-%m-%d %H:%M:%S"),
                self._get_short_commit_hash(release_commit),
                release_commit_date.strftime("%Y-%m-%d %H:%M:%S")
            )

        # Update the last check time
        self.state.update_last_check()
        self.state.save(self.state_file)
