# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

from __future__ import annotations

import logging
from datetime import datetime
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
from kleinanzeigen_bot.utils import xdg_paths

logger = logging.getLogger(__name__)

colorama.init()


class UpdateChecker:
    """Checks for updates to the bot."""

    def __init__(self, config:"Config", installation_mode:str | xdg_paths.InstallationMode = "portable") -> None:
        """Initialize the update checker.

        Args:
            config: The bot configuration.
            installation_mode: Installation mode (portable/xdg).
        """
        self.config = config
        self.state_file = xdg_paths.get_update_check_state_path(installation_mode)
        # Note: xdg_paths handles directory creation
        self.state = UpdateCheckState.load(self.state_file)

    def get_local_version(self) -> str | None:
        """Get the local version of the bot.

        Returns:
            The local version string, or None if it cannot be determined.
        """
        return __version__

    def _request_timeout(self) -> float:
        """Return the effective timeout for HTTP calls."""
        return self.config.timeouts.effective("update_check")

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

    def _resolve_commitish(self, commitish:str) -> tuple[str | None, datetime | None]:
        """Resolve a commit-ish to a full commit hash and date.

        Args:
            commitish: The commit hash, tag, or branch.

        Returns:
            Tuple of (full commit hash, commit date), or (None, None) if it cannot be determined.
        """
        try:
            response = requests.get(
                f"https://api.github.com/repos/Second-Hand-Friends/kleinanzeigen-bot/commits/{commitish}",
                timeout = self._request_timeout(),
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                return None, None
            commit_date = None
            if "commit" in data and "author" in data["commit"] and "date" in data["commit"]["author"]:
                commit_date = datetime.fromisoformat(data["commit"]["author"]["date"].replace("Z", "+00:00"))
            sha = data.get("sha")
            commit_hash = str(sha) if sha else None
            return commit_hash, commit_date
        except Exception as e:
            logger.warning("Could not resolve commit '%s': %s", commitish, e)
            return None, None

    def _get_short_commit_hash(self, commit:str) -> str:
        """Get the short version of a commit hash.

        Args:
            commit: The full commit hash.

        Returns:
            The short commit hash (first 7 characters).
        """
        return commit[:7]

    def _commits_match(self, local_commit:str, release_commit:str) -> bool:
        """Determine whether two commits refer to the same hash.

        This accounts for short vs. full hashes (e.g. 7 chars vs. 40 chars).
        """
        local_commit = local_commit.strip()
        release_commit = release_commit.strip()
        if local_commit == release_commit:
            return True
        if len(local_commit) < len(release_commit) and release_commit.startswith(local_commit):
            return True
        return len(release_commit) < len(local_commit) and local_commit.startswith(release_commit)

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

        local_commitish = self._get_commit_hash(local_version)
        if not local_commitish:
            logger.warning("Could not determine local commit hash.")
            return

        # --- Fetch release info from GitHub using correct endpoint per channel ---
        try:
            if self.config.update_check.channel == "latest":
                # Use /releases/latest endpoint for stable releases
                response = requests.get("https://api.github.com/repos/Second-Hand-Friends/kleinanzeigen-bot/releases/latest", timeout = self._request_timeout())
                response.raise_for_status()
                release = response.json()
                # Defensive: ensure it's not a prerelease
                if release.get("prerelease", False):
                    logger.warning("Latest release from GitHub is a prerelease, but 'latest' channel expects a stable release.")
                    return
            elif self.config.update_check.channel == "preview":
                # Use /releases endpoint and select the most recent prerelease
                response = requests.get("https://api.github.com/repos/Second-Hand-Friends/kleinanzeigen-bot/releases", timeout = self._request_timeout())
                response.raise_for_status()
                releases = response.json()
                # Find the most recent prerelease
                release = next((r for r in releases if r.get("prerelease", False) and not r.get("draft", False)), None)
                if not release:
                    logger.warning("No prerelease found for 'preview' channel.")
                    return
            else:
                logger.warning("Unknown update channel: %s", self.config.update_check.channel)
                return
        except Exception as e:
            logger.warning("Could not get releases: %s", e)
            return

        # Get release commit-ish (use tag name to avoid branch tip drift)
        release_commitish = release.get("tag_name")
        if not release_commitish:
            release_commitish = release.get("target_commitish")
        if not release_commitish:
            logger.warning("Could not determine release commit hash.")
            return

        # Resolve commit hashes and dates for comparison
        local_commit, local_commit_date = self._resolve_commitish(local_commitish)
        release_commit, release_commit_date = self._resolve_commitish(str(release_commitish))
        if not local_commit or not release_commit or not local_commit_date or not release_commit_date:
            logger.warning("Could not determine commit dates for comparison.")
            return

        if self._commits_match(local_commit, release_commit):
            # If the commit hashes are identical, we are on the latest version. Do not proceed to other checks.
            logger.info(
                "You are on the latest version: %s (compared to %s in channel %s)",
                local_version,
                self._get_short_commit_hash(release_commit),
                self.config.update_check.channel,
            )
            self.state.update_last_check()
            self.state.save(self.state_file)
            return
        # All commit dates are in UTC; append ' UTC' to timestamps in logs for clarity.
        if local_commit_date < release_commit_date:
            logger.warning(
                "A new version is available: %s from %s UTC (current: %s from %s UTC, channel: %s)",
                self._get_short_commit_hash(release_commit),
                release_commit_date.strftime("%Y-%m-%d %H:%M:%S"),
                local_version,
                local_commit_date.strftime("%Y-%m-%d %H:%M:%S"),
                self.config.update_check.channel,
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
                release_commit_date.strftime("%Y-%m-%d %H:%M:%S"),
            )

        # Update the last check time
        self.state.update_last_check()
        self.state.save(self.state_file)
