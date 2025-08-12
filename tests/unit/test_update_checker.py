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
            return_value = mocker.Mock(json = lambda: {"tag_name": "latest", "prerelease": False})
        )
        mocker.patch.object(UpdateCheckState, "should_check", return_value = True)

        checker = UpdateChecker(config)
        checker.check_for_updates()

        print("LOG RECORDS:")
        for r in caplog.records:
            print(f"{r.levelname}: {r.getMessage()}")

        expected = (
            "You are on a different commit than the release for channel 'latest' (tag: latest). This may mean you are ahead, behind, or on a different branch. "
            "Local commit: fb00f11 (2025-05-18 00:00:00 UTC), Release commit: e7a3d46 (2025-05-16 00:00:00 UTC)"
        )
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
                datetime(2025, 5, 18, tzinfo = timezone.utc),
                datetime(2025, 5, 16, tzinfo = timezone.utc)
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

        expected = (
            "You are on a different commit than the release for channel 'preview' (tag: preview). "
            "This may mean you are ahead, behind, or on a different branch. "
            "Local commit: fb00f11 (2025-05-18 00:00:00 UTC), Release commit: e7a3d46 (2025-05-16 00:00:00 UTC)"
        )
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
            return_value = mocker.Mock(json = lambda: {"tag_name": "latest", "prerelease": False})
        )
        mocker.patch.object(UpdateCheckState, "should_check", return_value = True)

        checker = UpdateChecker(config)
        checker.check_for_updates()

        print("LOG RECORDS:")
        for r in caplog.records:
            print(f"{r.levelname}: {r.getMessage()}")

        expected = "A new version is available: e7a3d46 from 2025-05-18 00:00:00 UTC (current: 2025+fb00f11 from 2025-05-16 00:00:00 UTC, channel: latest)"
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
            return_value = mocker.Mock(json = lambda: {"tag_name": "latest", "prerelease": False})
        )
        mocker.patch.object(UpdateCheckState, "should_check", return_value = True)

        checker = UpdateChecker(config)
        checker.check_for_updates()

        print("LOG RECORDS:")
        for r in caplog.records:
            print(f"{r.levelname}: {r.getMessage()}")

        expected = "You are on the latest version: 2025+fb00f11 (compared to fb00f11 in channel latest)"
        assert any(expected in r.getMessage() for r in caplog.records)

    def test_update_check_state_empty_file(self, state_file:Path) -> None:
        """Test that loading an empty state file returns a new state."""
        state_file.touch()  # Create empty file
        state = UpdateCheckState.load(state_file)
        assert state.last_check is None

    def test_update_check_state_invalid_data(self, state_file:Path) -> None:
        """Test that loading invalid state data returns a new state."""
        state_file.write_text("invalid json", encoding = "utf-8")
        state = UpdateCheckState.load(state_file)
        assert state.last_check is None

    def test_update_check_state_missing_last_check(self, state_file:Path) -> None:
        """Test that loading state data without last_check returns a new state."""
        state_file.write_text("{}", encoding = "utf-8")
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

        # Test seconds (should always be too short, fallback to 7d, only 2 days elapsed, so should_check is False)
        state.last_check = now - timedelta(seconds = 30)
        assert state.should_check("60s") is False
        assert state.should_check("20s") is False

        # Test minutes (should always be too short)
        state.last_check = now - timedelta(minutes = 30)
        assert state.should_check("60m") is False
        assert state.should_check("20m") is False

        # Test hours (should always be too short)
        state.last_check = now - timedelta(hours = 2)
        assert state.should_check("4h") is False
        assert state.should_check("1h") is False

        # Test days
        state.last_check = now - timedelta(days = 3)
        assert state.should_check("7d") is False
        assert state.should_check("2d") is True
        state.last_check = now - timedelta(days = 3)
        assert state.should_check("3d") is False
        state.last_check = now - timedelta(days = 3, seconds = 1)
        assert state.should_check("3d") is True

        # Test multi-day intervals (was weeks)
        state.last_check = now - timedelta(days = 14)
        assert state.should_check("14d") is False
        state.last_check = now - timedelta(days = 14, seconds = 1)
        assert state.should_check("14d") is True

        # Test invalid unit (should fallback to 7d, 14 days elapsed, so should_check is True)
        state.last_check = now - timedelta(days = 14)
        assert state.should_check("1x") is True
        # If fallback interval has not elapsed, should_check is False
        state.last_check = now - timedelta(days = 6)
        assert state.should_check("1x") is False
        # Test truly unknown unit (case _)
        state.last_check = now - timedelta(days = 14)
        assert state.should_check("1z") is True
        state.last_check = now - timedelta(days = 6)
        assert state.should_check("1z") is False

    def test_update_check_state_interval_validation(self) -> None:
        """Test that interval validation works correctly."""
        state = UpdateCheckState()
        now = datetime.now(timezone.utc)
        state.last_check = now - timedelta(days = 1)

        # Test minimum value (1d)
        assert state.should_check("12h") is False  # Too short, fallback to 7d, only 1 day elapsed
        assert state.should_check("1d") is False  # Minimum allowed
        assert state.should_check("2d") is False  # Valid, but only 1 day elapsed

        # Test maximum value (30d)
        assert state.should_check("31d") is False   # Too long, fallback to 7d, only 1 day elapsed
        assert state.should_check("60d") is False   # Too long, fallback to 7d, only 1 day elapsed
        state.last_check = now - timedelta(days = 30)
        assert state.should_check("30d") is False  # Exactly 30 days, should_check is False
        state.last_check = now - timedelta(days = 30, seconds = 1)
        assert state.should_check("30d") is True   # Should check if just over interval
        state.last_check = now - timedelta(days = 21)
        assert state.should_check("21d") is False  # Exactly 21 days, should_check is False
        state.last_check = now - timedelta(days = 21, seconds = 1)
        assert state.should_check("21d") is True   # Should check if just over interval
        state.last_check = now - timedelta(days = 7)
        assert state.should_check("7d") is False   # 7 days, should_check is False
        state.last_check = now - timedelta(days = 7, seconds = 1)
        assert state.should_check("7d") is True    # Should check if just over interval

        # Test negative values
        state.last_check = now - timedelta(days = 1)
        assert state.should_check("-1d") is False  # Negative value, fallback to 7d, only 1 day elapsed
        state.last_check = now - timedelta(days = 8)
        assert state.should_check("-1d") is True   # Negative value, fallback to 7d, 8 days elapsed
        # Test zero value
        state.last_check = now - timedelta(days = 1)
        assert state.should_check("0d") is False   # Zero value, fallback to 7d, only 1 day elapsed
        state.last_check = now - timedelta(days = 8)
        assert state.should_check("0d") is True    # Zero value, fallback to 7d, 8 days elapsed

        # Test invalid formats
        state.last_check = now - timedelta(days = 1)
        assert state.should_check("invalid") is False  # Invalid format, fallback to 7d, only 1 day elapsed
        state.last_check = now - timedelta(days = 8)
        assert state.should_check("invalid") is True   # Invalid format, fallback to 7d, 8 days elapsed
        state.last_check = now - timedelta(days = 1)
        assert state.should_check("1") is False       # Missing unit, fallback to 7d, only 1 day elapsed
        state.last_check = now - timedelta(days = 8)
        assert state.should_check("1") is True        # Missing unit, fallback to 7d, 8 days elapsed
        state.last_check = now - timedelta(days = 1)
        assert state.should_check("d") is False       # Missing value, fallback to 7d, only 1 day elapsed
        state.last_check = now - timedelta(days = 8)
        assert state.should_check("d") is True        # Missing value, fallback to 7d, 8 days elapsed

        # Test unit conversions (all sub-day intervals are too short)
        state.last_check = now - timedelta(days = 1)
        assert state.should_check("24h") is False    # 1 day in hours, fallback to 7d, only 1 day elapsed
        state.last_check = now - timedelta(days = 8)
        assert state.should_check("24h") is True     # 1 day in hours, fallback to 7d, 8 days elapsed
        state.last_check = now - timedelta(days = 1)
        assert state.should_check("1440m") is False  # 1 day in minutes, fallback to 7d, only 1 day elapsed
        state.last_check = now - timedelta(days = 8)
        assert state.should_check("1440m") is True   # 1 day in minutes, fallback to 7d, 8 days elapsed
        state.last_check = now - timedelta(days = 1)
        assert state.should_check("86400s") is False  # 1 day in seconds, fallback to 7d, only 1 day elapsed
        state.last_check = now - timedelta(days = 8)
        assert state.should_check("86400s") is True   # 1 day in seconds, fallback to 7d, 8 days elapsed

    def test_update_check_state_invalid_date(self, state_file:Path) -> None:
        """Test that loading a state file with an invalid date string for last_check returns a new state (triggers ValueError)."""
        state_file.write_text(json.dumps({"last_check": "not-a-date"}), encoding = "utf-8")
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
        # Patch requests.get to avoid any real HTTP requests
        mocker.patch("requests.get", return_value = mocker.Mock(json = lambda: {"tag_name": "latest", "prerelease": False}))
        checker = UpdateChecker(config)
        checker.check_for_updates()
        assert any("Could not determine commit dates for comparison." in r.getMessage() for r in caplog.records)

    def test_update_check_state_version_tracking(self, state_file:Path) -> None:
        """Test that version tracking works correctly."""
        # Create a state with version 0 (old format)
        state_file.write_text(json.dumps({
            "last_check": datetime.now(timezone.utc).isoformat()
        }), encoding = "utf-8")

        # Load the state - should migrate to version 1
        state = UpdateCheckState.load(state_file)
        assert state.version == 1

        # Save the state
        state.save(state_file)

        # Load again - should keep version 1
        state = UpdateCheckState.load(state_file)
        assert state.version == 1

    def test_update_check_state_migration(self, state_file:Path) -> None:
        """Test that state migration works correctly."""
        # Create a state with version 0 (old format)
        old_time = datetime.now(timezone.utc)
        state_file.write_text(json.dumps({
            "last_check": old_time.isoformat()
        }), encoding = "utf-8")

        # Load the state - should migrate to version 1
        state = UpdateCheckState.load(state_file)
        assert state.version == 1
        assert state.last_check == old_time

        # Save the state
        state.save(state_file)

        # Verify the saved file has the new version
        with open(state_file, "r", encoding = "utf-8") as f:
            data = json.load(f)
            assert data["version"] == 1
            assert data["last_check"] == old_time.isoformat()

    def test_update_check_state_save_errors(self, state_file:Path, mocker:"MockerFixture") -> None:
        """Test that save errors are handled gracefully."""
        state = UpdateCheckState()
        state.last_check = datetime.now(timezone.utc)

        # Test permission error
        mocker.patch("kleinanzeigen_bot.utils.dicts.save_dict", side_effect = PermissionError)
        state.save(state_file)  # Should not raise

        # Test other errors
        mocker.patch("kleinanzeigen_bot.utils.dicts.save_dict", side_effect = Exception("Test error"))
        state.save(state_file)  # Should not raise

    def test_update_check_state_load_errors(self, state_file:Path) -> None:
        """Test that load errors are handled gracefully."""
        # Test invalid JSON
        state_file.write_text("invalid json", encoding = "utf-8")
        state = UpdateCheckState.load(state_file)
        assert state.version == 1
        assert state.last_check is None

        # Test invalid date format
        state_file.write_text(json.dumps({
            "version": 1,
            "last_check": "invalid-date"
        }), encoding = "utf-8")
        state = UpdateCheckState.load(state_file)
        assert state.version == 1
        assert state.last_check is None

    def test_update_check_state_timezone_handling(self, state_file:Path) -> None:
        """Test that timezone handling works correctly."""
        # Test loading timestamp without timezone (should assume UTC)
        state_file.write_text(json.dumps({
            "version": 1,
            "last_check": "2024-03-20T12:00:00"
        }), encoding = "utf-8")
        state = UpdateCheckState.load(state_file)
        assert state.last_check is not None
        assert state.last_check.tzinfo == timezone.utc
        assert state.last_check.hour == 12

        # Test loading timestamp with different timezone (should convert to UTC)
        state_file.write_text(json.dumps({
            "version": 1,
            "last_check": "2024-03-20T12:00:00+02:00"  # 2 hours ahead of UTC
        }), encoding = "utf-8")
        state = UpdateCheckState.load(state_file)
        assert state.last_check is not None
        assert state.last_check.tzinfo == timezone.utc
        assert state.last_check.hour == 10  # Converted to UTC

        # Test saving timestamp (should always be in UTC)
        state = UpdateCheckState()
        state.last_check = datetime(2024, 3, 20, 12, 0, tzinfo = timezone(timedelta(hours = 2)))
        state.save(state_file)
        with open(state_file, "r", encoding = "utf-8") as f:
            data = json.load(f)
            assert data["last_check"] == "2024-03-20T10:00:00+00:00"  # Converted to UTC

    def test_update_check_state_missing_file(self, state_file:Path) -> None:
        """Test that loading a missing state file returns a new state and should_check returns True."""
        # Ensure the file doesn't exist
        if state_file.exists():
            state_file.unlink()

        # Load state from non-existent file
        state = UpdateCheckState.load(state_file)
        assert state.last_check is None
        assert state.version == 1

        # Verify should_check returns True for any interval
        assert state.should_check("7d") is True
        assert state.should_check("1d") is True
        assert state.should_check("4w") is True

        # No longer check _time_since_last_check (method removed)

    def test_should_check_fallback_to_default_interval(self, caplog:pytest.LogCaptureFixture) -> None:
        """Test that should_check falls back to default interval and logs a warning for invalid/too short/too long/zero intervals and unsupported units."""
        state = UpdateCheckState()
        now = datetime.now(timezone.utc)
        state.last_check = now - timedelta(days = 2)

        # Invalid format (unsupported unit)
        caplog.clear()
        assert state.should_check("notaninterval", channel = "latest") is False  # 2 days since last check, default 7d
        assert any("Invalid interval format or unsupported unit" in r.getMessage() for r in caplog.records)
        assert any("Falling back to default interval: 7d" in r.getMessage() for r in caplog.records)

        caplog.clear()
        assert state.should_check("notaninterval", channel = "preview") is True  # 2 days since last check, default 1d
        assert any("Invalid interval format or unsupported unit" in r.getMessage() for r in caplog.records)
        assert any("Falling back to default interval: 1d" in r.getMessage() for r in caplog.records)

        # Explicit zero interval
        for zero in ["0d", "0h", "0m", "0s", "0"]:
            caplog.clear()
            assert state.should_check(zero, channel = "latest") is False
            assert any("Interval is zero" in r.getMessage() for r in caplog.records)
            assert any("Falling back to default interval: 7d" in r.getMessage() for r in caplog.records)

            caplog.clear()
            assert state.should_check(zero, channel = "preview") is True
            assert any("Interval is zero" in r.getMessage() for r in caplog.records)
            assert any("Falling back to default interval: 1d" in r.getMessage() for r in caplog.records)

        # Too short
        caplog.clear()
        assert state.should_check("12h", channel = "latest") is False  # 2 days since last check, default 7d
        assert any("Interval too short" in r.getMessage() for r in caplog.records)
        assert any("Falling back to default interval: 7d" in r.getMessage() for r in caplog.records)

        caplog.clear()
        assert state.should_check("12h", channel = "preview") is True  # 2 days since last check, default 1d
        assert any("Interval too short" in r.getMessage() for r in caplog.records)
        assert any("Falling back to default interval: 1d" in r.getMessage() for r in caplog.records)

        # Too long
        caplog.clear()
        assert state.should_check("60d", channel = "latest") is False  # 2 days since last check, default 7d
        assert any("Interval too long" in r.getMessage() for r in caplog.records)
        assert any("Falling back to default interval: 7d" in r.getMessage() for r in caplog.records)

        caplog.clear()
        assert state.should_check("60d", channel = "preview") is True  # 2 days since last check, default 1d
        assert any("Interval too long" in r.getMessage() for r in caplog.records)
        assert any("Falling back to default interval: 1d" in r.getMessage() for r in caplog.records)

        # Valid interval, no fallback
        caplog.clear()
        assert state.should_check("7d", channel = "latest") is False
        assert not any("Falling back to default interval" in r.getMessage() for r in caplog.records)
        caplog.clear()
        assert state.should_check("1d", channel = "preview") is True
        assert not any("Falling back to default interval" in r.getMessage() for r in caplog.records)
