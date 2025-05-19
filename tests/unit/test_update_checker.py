# SPDX-FileCopyrightText: © jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path

import pytest
import requests

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

from kleinanzeigen_bot.model.config_model import Config
from kleinanzeigen_bot.model.update_check_state import UpdateCheckState
from kleinanzeigen_bot.update_checker import UpdateChecker


@pytest.fixture
def config() -> Config:
    return Config.model_validate({
        "update_check": {
            "enabled": True,
            "channel": "latest",
            "interval": "7d"
        }
    })


@pytest.fixture
def state_file(tmp_path:Path) -> Path:
    return tmp_path / "update_check_state.json"


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
        with patch("requests.get", return_value = MagicMock(json = lambda: {"target_commitish": "e7a3d46"})):
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
        caplog.set_level("INFO", logger = "kleinanzeigen_bot.update_checker")
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
        mocker.patch.object(UpdateCheckState, "should_check", return_value = True)

        checker = UpdateChecker(config)
        checker.check_for_updates()

        print("LOG RECORDS:")
        for r in caplog.records:
            print(f"{r.levelname}: {r.getMessage()}")

        expected = "You are ahead of the latest version: 2025+fb00f11 (compared to e7a3d46 in channel latest)"
        assert any(expected in r.getMessage() for r in caplog.records)

    def test_check_for_updates_preview(self, config:Config, mocker:"MockerFixture", caplog:pytest.LogCaptureFixture) -> None:
        """Test that the update checker correctly handles preview releases."""
        caplog.set_level("INFO", logger = "kleinanzeigen_bot.update_checker")
        config.update_check.channel = "preview"
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
            return_value = mocker.Mock(json = lambda: [{"tag_name": "preview", "prerelease": True}])
        )
        mocker.patch.object(UpdateCheckState, "should_check", return_value = True)

        checker = UpdateChecker(config)
        checker.check_for_updates()

        print("LOG RECORDS:")
        for r in caplog.records:
            print(f"{r.levelname}: {r.getMessage()}")

        expected = "A new version is available: e7a3d46 from 2025-05-18 00:00:00 (current: 2025+fb00f11 from 2025-05-16 00:00:00, channel: preview)"
        assert any(expected in r.getMessage() for r in caplog.records)

    def test_check_for_updates_behind(self, config:Config, mocker:"MockerFixture", caplog:pytest.LogCaptureFixture) -> None:
        """Test that the update checker correctly identifies when the local version is behind the latest release."""
        caplog.set_level("INFO", logger = "kleinanzeigen_bot.update_checker")
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
        mocker.patch.object(UpdateCheckState, "should_check", return_value = True)

        checker = UpdateChecker(config)
        checker.check_for_updates()

        print("LOG RECORDS:")
        for r in caplog.records:
            print(f"{r.levelname}: {r.getMessage()}")

        expected = "A new version is available: e7a3d46 from 2025-05-18 00:00:00 (current: 2025+fb00f11 from 2025-05-16 00:00:00, channel: latest)"
        assert any(expected in r.getMessage() for r in caplog.records)

    def test_check_for_updates_same(self, config:Config, mocker:"MockerFixture", caplog:pytest.LogCaptureFixture) -> None:
        """Test that the update checker correctly identifies when the local version is the same as the latest release."""
        caplog.set_level("INFO", logger = "kleinanzeigen_bot.update_checker")
        mocker.patch.object(UpdateChecker, "get_local_version", return_value = "2025+fb00f11")
        mocker.patch.object(UpdateChecker, "_get_commit_hash", return_value = "fb00f11")
        mocker.patch.object(UpdateChecker, "_get_release_commit", return_value = "fb00f11")
        mocker.patch.object(
            UpdateChecker,
            "_get_commit_date",
            return_value = datetime(2025, 5, 18, tzinfo = timezone.utc)
        )
        mocker.patch.object(
            requests,
            "get",
            return_value = mocker.Mock(json = lambda: [{"tag_name": "latest", "prerelease": False}])
        )
        mocker.patch.object(UpdateCheckState, "should_check", return_value = True)

        checker = UpdateChecker(config)
        checker.check_for_updates()

        print("LOG RECORDS:")
        for r in caplog.records:
            print(f"{r.levelname}: {r.getMessage()}")

        expected = "You are on the latest version: 2025+fb00f11 (compared to fb00f11 in channel latest)"
        assert any(expected in r.getMessage() for r in caplog.records)

    def test_interval_checking(self, config:Config, state_file:Path, mocker:"MockerFixture") -> None:
        """Test that the update checker respects the interval setting."""
        # Create a state with a recent check
        state = UpdateCheckState()
        state.last_check = datetime.now(timezone.utc) - timedelta(days = 3)  # 3 days ago
        state.save(state_file)

        # Mock the update check to verify it's not called
        mocker.patch.object(UpdateChecker, "get_local_version")
        mock_get = mocker.patch("requests.get")

        # Run the update check
        checker = UpdateChecker(config)
        checker.state_file = state_file  # Override the state file path
        checker.state = UpdateCheckState.load(state_file)  # Load the state
        checker.check_for_updates()

        # Verify that no API calls were made
        mock_get.assert_not_called()

    def test_interval_checking_expired(self, config:Config, state_file:Path, mocker:"MockerFixture") -> None:
        """Test that the update checker performs a check when the interval has expired."""
        # Create a state with an old check
        state = UpdateCheckState()
        state.last_check = datetime.now(timezone.utc) - timedelta(days = 8)  # 8 days ago
        state.save(state_file)

        # Mock the update check to verify it's called
        mocker.patch.object(UpdateChecker, "get_local_version", return_value = "2025+fb00f11")
        mocker.patch.object(UpdateChecker, "_get_commit_hash", return_value = "fb00f11")
        mocker.patch.object(UpdateChecker, "_get_release_commit", return_value = "e7a3d46")
        mock_get = mocker.patch.object(
            requests,
            "get",
            return_value = mocker.Mock(json = lambda: [{"tag_name": "latest", "prerelease": False}])
        )

        # Run the update check
        checker = UpdateChecker(config)
        checker.state_file = state_file  # Override the state file path
        checker.state = UpdateCheckState.load(state_file)  # Load the state
        checker.check_for_updates()

        # Verify that API calls were made
        assert mock_get.call_count > 0

    def test_interval_checking_invalid(self, config:Config, state_file:Path, mocker:"MockerFixture") -> None:
        """Test that the update checker performs a check when the interval is invalid."""
        # Create a state with a recent check
        state = UpdateCheckState()
        state.last_check = datetime.now(timezone.utc) - timedelta(days = 3)  # 3 days ago
        state.save(state_file)

        # Set an invalid interval
        config.update_check.interval = "invalid"

        # Mock the update check to verify it's called
        mocker.patch.object(UpdateChecker, "get_local_version", return_value = "2025+fb00f11")
        mocker.patch.object(UpdateChecker, "_get_commit_hash", return_value = "fb00f11")
        mocker.patch.object(UpdateChecker, "_get_release_commit", return_value = "e7a3d46")
        mock_get = mocker.patch.object(
            requests,
            "get",
            return_value = mocker.Mock(json = lambda: [{"tag_name": "latest", "prerelease": False}])
        )

        # Run the update check
        checker = UpdateChecker(config)
        checker.state_file = state_file  # Override the state file path
        checker.state = UpdateCheckState.load(state_file)  # Load the state
        checker.check_for_updates()

        # Verify that API calls were made
        assert mock_get.call_count > 0

    def test_update_check_state_empty_file(self, state_file:Path) -> None:
        """Test that loading an empty state file returns a new state."""
        state_file.touch()  # Create empty file
        state = UpdateCheckState.load(state_file)
        assert state.last_check is None

    def test_update_check_state_invalid_data(self, state_file:Path) -> None:
        """Test that loading invalid state data returns a new state."""
        state_file.write_text("invalid json")
        state = UpdateCheckState.load(state_file)
        assert state.last_check is None

    def test_update_check_state_missing_last_check(self, state_file:Path) -> None:
        """Test that loading state data without last_check returns a new state."""
        state_file.write_text("{}")
        state = UpdateCheckState.load(state_file)
        assert state.last_check is None

    def test_update_check_state_save_error(self, state_file:Path) -> None:
        """Test that saving state handles errors gracefully."""
        state = UpdateCheckState()
        state.last_check = datetime.now(timezone.utc)

        # Make the file read-only to cause a save error
        state_file.touch()
        state_file.chmod(0o444)

        # Should not raise an exception
        state.save(state_file)

    def test_update_check_state_interval_units(self) -> None:
        """Test that different interval units are handled correctly."""
        state = UpdateCheckState()
        now = datetime.now(timezone.utc)

        # Test seconds
        state.last_check = now - timedelta(seconds = 30)
        assert state.should_check("60s") is False
        assert state.should_check("20s") is True

        # Test minutes
        state.last_check = now - timedelta(minutes = 30)
        assert state.should_check("60m") is False
        assert state.should_check("20m") is True

        # Test hours
        state.last_check = now - timedelta(hours = 2)
        assert state.should_check("4h") is False
        assert state.should_check("1h") is True

        # Test days
        state.last_check = now - timedelta(days = 3)
        assert state.should_check("7d") is False
        assert state.should_check("2d") is True

        # Test weeks
        state.last_check = now - timedelta(weeks = 1)
        assert state.should_check("2w") is False
        assert state.should_check("3d") is True

        # Test invalid unit
        assert state.should_check("1x") is True
        # Test truly unknown unit (case _)
        assert state.should_check("1z") is True

    def test_update_check_state_invalid_date(self, state_file:Path) -> None:
        """Test that loading a state file with an invalid date string for last_check returns a new state (triggers ValueError)."""
        state_file.write_text(json.dumps({"last_check": "not-a-date"}))
        state = UpdateCheckState.load(state_file)
        assert state.last_check is None

    def test_update_check_state_save_permission_error(self, mocker:"MockerFixture", state_file:Path) -> None:
        """Test that save handles PermissionError from dicts.save_dict."""
        state = UpdateCheckState()
        state.last_check = datetime.now(timezone.utc)
        mocker.patch("kleinanzeigen_bot.utils.dicts.save_dict", side_effect = PermissionError)
        # Should not raise
        state.save(state_file)

    def test_get_release_commit_no_sha(self, config:Config, mocker:"MockerFixture") -> None:
        """Test _get_release_commit with API returning no sha key."""
        checker = UpdateChecker(config)
        mocker.patch("requests.get", return_value = mocker.Mock(json = dict))
        assert checker._get_release_commit("latest") is None

    def test_get_release_commit_list_instead_of_dict(self, config:Config, mocker:"MockerFixture") -> None:
        """Test _get_release_commit with API returning a list instead of dict."""
        checker = UpdateChecker(config)
        mocker.patch("requests.get", return_value = mocker.Mock(json = list))
        assert checker._get_release_commit("latest") is None

    def test_get_commit_date_no_commit(self, config:Config, mocker:"MockerFixture") -> None:
        """Test _get_commit_date with API returning no commit key."""
        checker = UpdateChecker(config)
        mocker.patch("requests.get", return_value = mocker.Mock(json = dict))
        assert checker._get_commit_date("sha") is None

    def test_get_commit_date_no_author(self, config:Config, mocker:"MockerFixture") -> None:
        """Test _get_commit_date with API returning no author key."""
        checker = UpdateChecker(config)
        mocker.patch("requests.get", return_value = mocker.Mock(json = lambda: {"commit": {}}))
        assert checker._get_commit_date("sha") is None

    def test_get_commit_date_no_date(self, config:Config, mocker:"MockerFixture") -> None:
        """Test _get_commit_date with API returning no date key."""
        checker = UpdateChecker(config)
        mocker.patch("requests.get", return_value = mocker.Mock(json = lambda: {"commit": {"author": {}}}))
        assert checker._get_commit_date("sha") is None

    def test_get_commit_date_list_instead_of_dict(self, config:Config, mocker:"MockerFixture") -> None:
        """Test _get_commit_date with API returning a list instead of dict."""
        checker = UpdateChecker(config)
        mocker.patch("requests.get", return_value = mocker.Mock(json = list))
        assert checker._get_commit_date("sha") is None

    def test_check_for_updates_release_commit_exception(self, config:Config, mocker:"MockerFixture") -> None:
        """Test check_for_updates handles exception in _get_release_commit."""
        checker = UpdateChecker(config)
        mocker.patch.object(UpdateChecker, "get_local_version", return_value = "2025+fb00f11")
        mocker.patch.object(UpdateChecker, "_get_commit_hash", return_value = "fb00f11")
        mocker.patch.object(UpdateChecker, "_get_release_commit", side_effect = Exception("fail"))
        mocker.patch.object(UpdateCheckState, "should_check", return_value = True)
        checker.check_for_updates()  # Should not raise

    def test_check_for_updates_commit_date_exception(self, config:Config, mocker:"MockerFixture") -> None:
        """Test check_for_updates handles exception in _get_commit_date."""
        checker = UpdateChecker(config)
        mocker.patch.object(UpdateChecker, "get_local_version", return_value = "2025+fb00f11")
        mocker.patch.object(UpdateChecker, "_get_commit_hash", return_value = "fb00f11")
        mocker.patch.object(UpdateChecker, "_get_release_commit", return_value = "e7a3d46")
        mocker.patch.object(UpdateChecker, "_get_commit_date", side_effect = Exception("fail"))
        mocker.patch.object(UpdateCheckState, "should_check", return_value = True)
        checker.check_for_updates()  # Should not raise

    def test_check_for_updates_no_releases_empty(self, config:Config, mocker:"MockerFixture") -> None:
        """Test check_for_updates handles no releases found (API returns empty list)."""
        checker = UpdateChecker(config)
        mocker.patch("requests.get", return_value = mocker.Mock(json = list))
        mocker.patch.object(UpdateCheckState, "should_check", return_value = True)
        checker.check_for_updates()  # Should not raise

    def test_check_for_updates_no_commit_hash_extracted(self, config:Config, mocker:"MockerFixture") -> None:
        """Test check_for_updates handles no commit hash extracted."""
        checker = UpdateChecker(config)
        mocker.patch.object(UpdateChecker, "get_local_version", return_value = "2025")
        mocker.patch.object(UpdateCheckState, "should_check", return_value = True)
        checker.check_for_updates()  # Should not raise

    def test_check_for_updates_no_commit_dates(self, config:Config, mocker:"MockerFixture", caplog:pytest.LogCaptureFixture) -> None:
        """Test check_for_updates logs warning if commit dates cannot be determined."""
        caplog.set_level("WARNING", logger = "kleinanzeigen_bot.update_checker")
        mocker.patch.object(UpdateChecker, "get_local_version", return_value = "2025+fb00f11")
        mocker.patch.object(UpdateChecker, "_get_commit_hash", return_value = "fb00f11")
        mocker.patch.object(UpdateChecker, "_get_release_commit", return_value = "e7a3d46")
        mocker.patch.object(UpdateChecker, "_get_commit_date", return_value = None)
        mocker.patch.object(UpdateCheckState, "should_check", return_value = True)
        checker = UpdateChecker(config)
        checker.check_for_updates()
        assert any("Could not determine commit dates for comparison." in r.getMessage() for r in caplog.records)
