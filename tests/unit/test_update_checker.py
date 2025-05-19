# SPDX-FileCopyrightText: © jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
import requests

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

from kleinanzeigen_bot.model.config_model import Config
from kleinanzeigen_bot.update_checker import UpdateChecker


@pytest.fixture
def config() -> Config:
    return Config.model_validate({
        "update_check": {
            "enabled": True,
            "channel": "latest"
        }
    })


@pytest.fixture
def caplog(caplog:pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    return caplog


class TestUpdateChecker:
    """Tests for the update checker functionality."""

    def test_get_local_version(self, config:Config) -> None:
        """Test that the local version is correctly retrieved."""
        checker = UpdateChecker(config)
        assert checker.get_local_version() is not None

    def test_get_commit_hash(self, config:Config) -> None:
        """Test that the commit hash is correctly extracted from the version string."""
        checker = UpdateChecker(config)
        assert checker._get_commit_hash("2025+fb00f11") == "fb00f11"
        assert checker._get_commit_hash("2025") is None

    def test_get_release_commit(self, config:Config) -> None:
        """Test that the release commit hash is correctly retrieved from the GitHub API."""
        checker = UpdateChecker(config)
        with patch("requests.get", return_value = MagicMock(json = lambda: {"sha": "e7a3d46"})):
            assert checker._get_release_commit("latest") == "e7a3d46"

    def test_get_commit_date(self, config:Config) -> None:
        """Test that the commit date is correctly retrieved from the GitHub API."""
        checker = UpdateChecker(config)
        with patch("requests.get", return_value = MagicMock(json = lambda: {"commit": {"author": {"date": "2025-05-18T00:00:00Z"}}})):
            assert checker._get_commit_date("e7a3d46") == datetime(2025, 5, 18, tzinfo = timezone.utc)

    def test_check_for_updates_disabled(self, config:Config) -> None:
        """Test that the update checker does not check for updates if disabled."""
        config.update_check.enabled = False
        checker = UpdateChecker(config)
        with patch("requests.get") as mock_get:
            checker.check_for_updates()
            mock_get.assert_not_called()

    def test_check_for_updates_no_local_version(self, config:Config) -> None:
        """Test that the update checker handles the case where the local version cannot be determined."""
        checker = UpdateChecker(config)
        with patch.object(UpdateChecker, "get_local_version", return_value = None):
            checker.check_for_updates()  # Should not raise exception

    def test_check_for_updates_no_commit_hash(self, config:Config) -> None:
        """Test that the update checker handles the case where the commit hash cannot be extracted."""
        checker = UpdateChecker(config)
        with patch.object(UpdateChecker, "get_local_version", return_value = "2025"):
            checker.check_for_updates()  # Should not raise exception

    def test_check_for_updates_no_releases(self, config:Config) -> None:
        """Test that the update checker handles the case where no releases are found."""
        checker = UpdateChecker(config)
        with patch("requests.get", return_value = MagicMock(json = list)):
            checker.check_for_updates()  # Should not raise exception

    def test_check_for_updates_api_error(self, config:Config) -> None:
        """Test that the update checker handles API errors gracefully."""
        checker = UpdateChecker(config)
        with patch("requests.get", side_effect = Exception("API Error")):
            checker.check_for_updates()  # Should not raise exception

    def test_check_for_updates_ahead(self, config:Config, mocker:"MockerFixture", caplog:pytest.LogCaptureFixture) -> None:
        """Test that the update checker correctly identifies when the local version is ahead of the latest release."""
        mocker.patch.object(UpdateChecker, "get_local_version", return_value = "2025+fb00f11")
        mocker.patch.object(UpdateChecker, "_get_commit_hash", return_value = "fb00f11")
        mocker.patch.object(UpdateChecker, "_get_release_commit", return_value = "e7a3d46")
        mocker.patch.object(
            UpdateChecker,
            "_get_commit_date",
            side_effect = [
                datetime(2025, 5, 18, tzinfo = timezone.utc),
                datetime(2025, 5, 16, tzinfo = timezone.utc)
            ]
        )
        mocker.patch.object(
            requests,
            "get",
            return_value = mocker.Mock(json = lambda: [{"tag_name": "latest", "prerelease": False}])
        )

        checker = UpdateChecker(config)
        checker.check_for_updates()

        assert "You are ahead of the latest version: 2025+fb00f11 (compared to e7a3d46 in channel latest)" in caplog.text

    def test_check_for_updates_behind(self, config:Config, mocker:"MockerFixture", caplog:pytest.LogCaptureFixture) -> None:
        """Test that the update checker correctly identifies when the local version is behind the latest release."""
        mocker.patch.object(UpdateChecker, "get_local_version", return_value = "2025+fb00f11")
        mocker.patch.object(UpdateChecker, "_get_commit_hash", return_value = "fb00f11")
        mocker.patch.object(UpdateChecker, "_get_release_commit", return_value = "e7a3d46")
        mocker.patch.object(
            UpdateChecker,
            "_get_commit_date",
            side_effect = [
                datetime(2025, 5, 16, tzinfo = timezone.utc),
                datetime(2025, 5, 18, tzinfo = timezone.utc)
            ]
        )
        mocker.patch.object(
            requests,
            "get",
            return_value = mocker.Mock(json = lambda: [{"tag_name": "latest", "prerelease": False}])
        )

        checker = UpdateChecker(config)
        checker.check_for_updates()

        assert "A new version is available: e7a3d46 from 2025-05-18 00:00:00 (current: 2025+fb00f11 from 2025-05-16 00:00:00, channel: latest)" in caplog.text

    def test_check_for_updates_same(self, config:Config, mocker:"MockerFixture", caplog:pytest.LogCaptureFixture) -> None:
        """Test that the update checker correctly identifies when the local version is the same as the latest release."""
        mocker.patch.object(UpdateChecker, "get_local_version", return_value = "2025+fb00f11")
        mocker.patch.object(UpdateChecker, "_get_commit_hash", return_value = "fb00f11")
        mocker.patch.object(UpdateChecker, "_get_release_commit", return_value = "fb00f11")
        mocker.patch.object(
            requests,
            "get",
            return_value = mocker.Mock(json = lambda: [{"tag_name": "latest", "prerelease": False}])
        )

        checker = UpdateChecker(config)
        checker.check_for_updates()

        assert "You are on the latest version: 2025+fb00f11 (compared to fb00f11 in channel latest)" in caplog.text
