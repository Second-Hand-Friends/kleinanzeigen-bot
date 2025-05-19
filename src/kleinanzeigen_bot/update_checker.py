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
                f"https://api.github.com/repos/Second-Hand-Friends/kleinanzeigen-bot/git/refs/tags/{tag_name}",
                timeout = 10
            )
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict) and "sha" in data:
                return str(data["sha"])
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

    def check_for_updates(self) -> None:
        """Check for updates to the bot."""
        if not self.config.update_check.enabled:
            return

        local_version = self.get_local_version()
        if not local_version:
            logger.warning("Could not determine local version.")
            return

        local_commit = self._get_commit_hash(local_version)
        if not local_commit:
            logger.warning("Could not determine local commit hash.")
            return

        try:
            response = requests.get(
                "https://api.github.com/repos/Second-Hand-Friends/kleinanzeigen-bot/releases",
                timeout = 10
            )
            response.raise_for_status()
            releases = response.json()
        except Exception as e:
            logger.warning("Could not get releases: %s", e)
            return

        if not releases:
            logger.warning("No releases found.")
            return

        release = next(
            (r for r in releases if r["tag_name"] == self.config.update_check.channel and not r["prerelease"]),
            None,
        )
        if not release:
            logger.warning("No release found for channel %s.", self.config.update_check.channel)
            return

        release_commit = self._get_release_commit(release["tag_name"])
        if not release_commit:
            logger.warning("Could not determine release commit hash.")
            return

        if local_commit != release_commit:
            local_commit_date = self._get_commit_date(local_commit)
            release_commit_date = self._get_commit_date(release_commit)

            if local_commit_date and release_commit_date:
                if local_commit_date > release_commit_date:
                    logger.info(
                        "You are ahead of the latest version: %s (compared to %s in channel %s)",
                        local_version,
                        self._get_short_commit_hash(release_commit),
                        self.config.update_check.channel
                    )
                else:
                    logger.warning(
                        "A new version is available: %s from %s (current: %s from %s, channel: %s)",
                        self._get_short_commit_hash(release_commit),
                        release_commit_date.strftime("%Y-%m-%d %H:%M:%S"),
                        local_version,
                        local_commit_date.strftime("%Y-%m-%d %H:%M:%S"),
                        self.config.update_check.channel
                    )
                    if release.get("body"):
                        logger.info("Release notes:\n%s", release["body"])
            else:
                logger.warning("Could not determine commit dates for comparison.")
        else:
            logger.info(
                "You are on the latest version: %s (compared to %s in channel %s)",
                local_version,
                self._get_short_commit_hash(release_commit),
                self.config.update_check.channel
            )
