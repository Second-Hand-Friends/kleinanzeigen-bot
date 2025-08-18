# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import json
import subprocess  # noqa: S404
from unittest.mock import Mock, patch

import pytest

from kleinanzeigen_bot.utils.chrome_version_detector import (
    ChromeVersionInfo,
    detect_chrome_version_from_binary,
    detect_chrome_version_from_remote_debugging,
    get_chrome_version_diagnostic_info,
    parse_version_string,
    validate_chrome_136_configuration,
)


class TestParseVersionString:
    """Test version string parsing functionality."""

    def test_parse_version_string_basic(self) -> None:
        """Test parsing basic version string."""
        version = parse_version_string("136.0.6778.0")
        assert version == 136

    def test_parse_version_string_with_build_info(self) -> None:
        """Test parsing version string with build information."""
        version = parse_version_string("136.0.6778.0 (Developer Build)")
        assert version == 136

    def test_parse_version_string_with_architecture(self) -> None:
        """Test parsing version string with architecture information."""
        version = parse_version_string("136.0.6778.0 (Official Build) (x86_64)")
        assert version == 136

    def test_parse_version_string_older_version(self) -> None:
        """Test parsing older Chrome version."""
        version = parse_version_string("120.0.6099.109")
        assert version == 120

    def test_parse_version_string_invalid_format(self) -> None:
        """Test parsing invalid version string raises ValueError."""
        with pytest.raises(ValueError, match = "Could not parse version string"):
            parse_version_string("invalid-version")

    def test_parse_version_string_empty(self) -> None:
        """Test parsing empty version string raises ValueError."""
        with pytest.raises(ValueError, match = "Could not parse version string"):
            parse_version_string("")


class TestChromeVersionInfo:
    """Test ChromeVersionInfo class."""

    def test_chrome_version_info_creation(self) -> None:
        """Test creating ChromeVersionInfo instance."""
        version_info = ChromeVersionInfo("136.0.6778.0", 136, "Chrome")
        assert version_info.version_string == "136.0.6778.0"
        assert version_info.major_version == 136
        assert version_info.browser_name == "Chrome"

    def test_chrome_version_info_is_chrome_136_plus_true(self) -> None:
        """Test is_chrome_136_plus returns True for Chrome 136+."""
        version_info = ChromeVersionInfo("136.0.6778.0", 136, "Chrome")
        assert version_info.is_chrome_136_plus is True

    def test_chrome_version_info_is_chrome_136_plus_false(self) -> None:
        """Test is_chrome_136_plus returns False for Chrome < 136."""
        version_info = ChromeVersionInfo("120.0.6099.109", 120, "Chrome")
        assert version_info.is_chrome_136_plus is False

    def test_chrome_version_info_is_chrome_136_plus_edge_case(self) -> None:
        """Test is_chrome_136_plus edge case for version 136."""
        version_info = ChromeVersionInfo("136.0.0.0", 136, "Chrome")
        assert version_info.is_chrome_136_plus is True

    def test_chrome_version_info_str_representation(self) -> None:
        """Test string representation of ChromeVersionInfo."""
        version_info = ChromeVersionInfo("136.0.6778.0", 136, "Chrome")
        expected = "Chrome 136.0.6778.0 (major: 136)"
        assert str(version_info) == expected

    def test_chrome_version_info_edge_browser(self) -> None:
        """Test ChromeVersionInfo with Edge browser."""
        version_info = ChromeVersionInfo("136.0.6778.0", 136, "Edge")
        assert version_info.browser_name == "Edge"
        assert str(version_info) == "Edge 136.0.6778.0 (major: 136)"


class TestDetectChromeVersionFromBinary:
    """Test Chrome version detection from binary."""

    @patch("subprocess.run")
    def test_detect_chrome_version_from_binary_success(self, mock_run:Mock) -> None:
        """Test successful Chrome version detection from binary."""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "Google Chrome 136.0.6778.0\n"
        mock_run.return_value = mock_result

        version_info = detect_chrome_version_from_binary("/path/to/chrome")

        assert version_info is not None
        assert version_info.version_string == "136.0.6778.0"
        assert version_info.major_version == 136
        assert version_info.browser_name == "Chrome"
        mock_run.assert_called_once_with(
            ["/path/to/chrome", "--version"],
            check = False,
            capture_output = True,
            text = True,
            timeout = 10
        )

    @patch("subprocess.run")
    def test_detect_chrome_version_from_binary_edge(self, mock_run:Mock) -> None:
        """Test Chrome version detection for Edge browser."""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "Microsoft Edge 136.0.6778.0\n"
        mock_run.return_value = mock_result

        version_info = detect_chrome_version_from_binary("/path/to/edge")

        assert version_info is not None
        assert version_info.browser_name == "Edge"
        assert version_info.major_version == 136

    @patch("subprocess.run")
    def test_detect_chrome_version_from_binary_chromium(self, mock_run:Mock) -> None:
        """Test Chrome version detection for Chromium browser."""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "Chromium 136.0.6778.0\n"
        mock_run.return_value = mock_result

        version_info = detect_chrome_version_from_binary("/path/to/chromium")

        assert version_info is not None
        assert version_info.browser_name == "Chromium"
        assert version_info.major_version == 136

    @patch("subprocess.run")
    def test_detect_chrome_version_from_binary_failure(self, mock_run:Mock) -> None:
        """Test Chrome version detection failure."""
        mock_result = Mock()
        mock_result.returncode = 1
        mock_result.stderr = "Command not found"
        mock_run.return_value = mock_result

        version_info = detect_chrome_version_from_binary("/path/to/chrome")
        assert version_info is None

    @patch("subprocess.run")
    def test_detect_chrome_version_from_binary_timeout(self, mock_run:Mock) -> None:
        """Test Chrome version detection timeout."""
        mock_run.side_effect = subprocess.TimeoutExpired("chrome", 10)

        version_info = detect_chrome_version_from_binary("/path/to/chrome")
        assert version_info is None

    @patch("subprocess.run")
    def test_detect_chrome_version_from_binary_invalid_output(self, mock_run:Mock) -> None:
        """Test Chrome version detection with invalid output."""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "Invalid version string"
        mock_run.return_value = mock_result

        version_info = detect_chrome_version_from_binary("/path/to/chrome")
        assert version_info is None


class TestDetectChromeVersionFromRemoteDebugging:
    """Test Chrome version detection from remote debugging API."""

    @patch("urllib.request.urlopen")
    def test_detect_chrome_version_from_remote_debugging_success(self, mock_urlopen:Mock) -> None:
        """Test successful Chrome version detection from remote debugging."""
        mock_response = Mock()
        mock_response.read.return_value = json.dumps({
            "Browser": "Chrome/136.0.6778.0",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.6778.0 Safari/537.36"
        }).encode()
        mock_urlopen.return_value = mock_response

        version_info = detect_chrome_version_from_remote_debugging("127.0.0.1", 9222)

        assert version_info is not None
        assert version_info.version_string == "136.0.6778.0"
        assert version_info.major_version == 136
        assert version_info.browser_name == "Chrome"
        mock_urlopen.assert_called_once_with("http://127.0.0.1:9222/json/version", timeout = 5)

    @patch("urllib.request.urlopen")
    def test_detect_chrome_version_from_remote_debugging_edge(self, mock_urlopen:Mock) -> None:
        """Test Chrome version detection for Edge from remote debugging."""
        mock_response = Mock()
        mock_response.read.return_value = json.dumps({
            "Browser": "Edg/136.0.6778.0",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.6778.0 Safari/537.36 Edg/136.0.6778.0"
        }).encode()
        mock_urlopen.return_value = mock_response

        version_info = detect_chrome_version_from_remote_debugging("127.0.0.1", 9222)

        assert version_info is not None
        assert version_info.major_version == 136
        assert version_info.browser_name == "Edge"

    @patch("urllib.request.urlopen")
    def test_detect_chrome_version_from_remote_debugging_no_chrome_in_user_agent(self, mock_urlopen:Mock) -> None:
        """Test Chrome version detection with no Chrome in User-Agent."""
        mock_response = Mock()
        mock_response.read.return_value = json.dumps({
            "Browser": "Unknown",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        }).encode()
        mock_urlopen.return_value = mock_response

        version_info = detect_chrome_version_from_remote_debugging("127.0.0.1", 9222)
        assert version_info is None

    @patch("urllib.request.urlopen")
    def test_detect_chrome_version_from_remote_debugging_connection_error(self, mock_urlopen:Mock) -> None:
        """Test Chrome version detection with connection error."""
        mock_urlopen.side_effect = Exception("Connection refused")

        version_info = detect_chrome_version_from_remote_debugging("127.0.0.1", 9222)
        assert version_info is None

    @patch("urllib.request.urlopen")
    def test_detect_chrome_version_from_remote_debugging_invalid_json(self, mock_urlopen:Mock) -> None:
        """Test Chrome version detection with invalid JSON response."""
        mock_response = Mock()
        mock_response.read.return_value = b"Invalid JSON"
        mock_urlopen.return_value = mock_response

        version_info = detect_chrome_version_from_remote_debugging("127.0.0.1", 9222)
        assert version_info is None


class TestValidateChrome136Configuration:
    """Test Chrome 136+ configuration validation."""

    def test_validate_chrome_136_configuration_no_remote_debugging(self) -> None:
        """Test validation when no remote debugging is configured."""
        # Chrome 136+ requires --user-data-dir regardless of remote debugging
        is_valid, error_message = validate_chrome_136_configuration([], None)
        assert is_valid is False
        assert "Chrome/Edge 136+ requires --user-data-dir" in error_message

    def test_validate_chrome_136_configuration_with_user_data_dir_arg(self) -> None:
        """Test validation with --user-data-dir in arguments."""
        args = ["--remote-debugging-port=9222", "--user-data-dir=/tmp/chrome-debug"]
        is_valid, error_message = validate_chrome_136_configuration(args, None)
        assert is_valid is True
        assert not error_message

    def test_validate_chrome_136_configuration_with_user_data_dir_config(self) -> None:
        """Test validation with user_data_dir in configuration."""
        args = ["--remote-debugging-port=9222"]
        is_valid, error_message = validate_chrome_136_configuration(args, "/tmp/chrome-debug")  # noqa: S108
        assert is_valid is True
        assert not error_message

    def test_validate_chrome_136_configuration_with_both(self) -> None:
        """Test validation with both user_data_dir argument and config."""
        args = ["--remote-debugging-port=9222", "--user-data-dir=/tmp/chrome-debug"]
        is_valid, error_message = validate_chrome_136_configuration(args, "/tmp/chrome-debug")  # noqa: S108
        assert is_valid is True
        assert not error_message

    def test_validate_chrome_136_configuration_missing_user_data_dir(self) -> None:
        """Test validation failure when user_data_dir is missing."""
        args = ["--remote-debugging-port=9222"]
        is_valid, error_message = validate_chrome_136_configuration(args, None)
        assert is_valid is False
        assert "Chrome/Edge 136+ requires --user-data-dir" in error_message
        assert "Add --user-data-dir=/path/to/directory to your browser arguments" in error_message

    def test_validate_chrome_136_configuration_empty_user_data_dir_config(self) -> None:
        """Test validation failure when user_data_dir config is empty."""
        args = ["--remote-debugging-port=9222"]
        is_valid, error_message = validate_chrome_136_configuration(args, "")
        assert is_valid is False
        assert "Chrome/Edge 136+ requires --user-data-dir" in error_message

    def test_validate_chrome_136_configuration_whitespace_user_data_dir_config(self) -> None:
        """Test validation failure when user_data_dir config is whitespace."""
        args = ["--remote-debugging-port=9222"]
        is_valid, error_message = validate_chrome_136_configuration(args, "   ")
        assert is_valid is False
        assert "Chrome/Edge 136+ requires --user-data-dir" in error_message


class TestGetChromeVersionDiagnosticInfo:
    """Test Chrome version diagnostic information gathering."""

    @patch("kleinanzeigen_bot.utils.chrome_version_detector.detect_chrome_version_from_binary")
    @patch("kleinanzeigen_bot.utils.chrome_version_detector.detect_chrome_version_from_remote_debugging")
    def test_get_chrome_version_diagnostic_info_binary_only(
        self, mock_remote_detect:Mock, mock_binary_detect:Mock
    ) -> None:
        """Test diagnostic info with binary detection only."""
        mock_binary_detect.return_value = ChromeVersionInfo("136.0.6778.0", 136, "Chrome")
        mock_remote_detect.return_value = None

        diagnostic_info = get_chrome_version_diagnostic_info(
            binary_path = "/path/to/chrome",
            remote_port = None
        )

        assert diagnostic_info["binary_detection"] is not None
        assert diagnostic_info["binary_detection"]["major_version"] == 136
        assert diagnostic_info["binary_detection"]["is_chrome_136_plus"] is True
        assert diagnostic_info["remote_detection"] is None
        assert diagnostic_info["chrome_136_plus_detected"] is True
        assert len(diagnostic_info["recommendations"]) == 1

    @patch("kleinanzeigen_bot.utils.chrome_version_detector.detect_chrome_version_from_binary")
    @patch("kleinanzeigen_bot.utils.chrome_version_detector.detect_chrome_version_from_remote_debugging")
    def test_get_chrome_version_diagnostic_info_remote_only(
        self, mock_remote_detect:Mock, mock_binary_detect:Mock
    ) -> None:
        """Test diagnostic info with remote detection only."""
        mock_binary_detect.return_value = None
        mock_remote_detect.return_value = ChromeVersionInfo("120.0.6099.109", 120, "Chrome")

        diagnostic_info = get_chrome_version_diagnostic_info(
            binary_path = None,
            remote_port = 9222
        )

        assert diagnostic_info["binary_detection"] is None
        assert diagnostic_info["remote_detection"] is not None
        assert diagnostic_info["remote_detection"]["major_version"] == 120
        assert diagnostic_info["remote_detection"]["is_chrome_136_plus"] is False
        assert diagnostic_info["chrome_136_plus_detected"] is False
        assert len(diagnostic_info["recommendations"]) == 0

    @patch("kleinanzeigen_bot.utils.chrome_version_detector.detect_chrome_version_from_binary")
    @patch("kleinanzeigen_bot.utils.chrome_version_detector.detect_chrome_version_from_remote_debugging")
    def test_get_chrome_version_diagnostic_info_both_detections(
        self, mock_remote_detect:Mock, mock_binary_detect:Mock
    ) -> None:
        """Test diagnostic info with both binary and remote detection."""
        mock_binary_detect.return_value = ChromeVersionInfo("136.0.6778.0", 136, "Chrome")
        mock_remote_detect.return_value = ChromeVersionInfo("136.0.6778.0", 136, "Chrome")

        diagnostic_info = get_chrome_version_diagnostic_info(
            binary_path = "/path/to/chrome",
            remote_port = 9222
        )

        assert diagnostic_info["binary_detection"] is not None
        assert diagnostic_info["remote_detection"] is not None
        assert diagnostic_info["chrome_136_plus_detected"] is True
        assert len(diagnostic_info["recommendations"]) == 1

    @patch("kleinanzeigen_bot.utils.chrome_version_detector.detect_chrome_version_from_binary")
    @patch("kleinanzeigen_bot.utils.chrome_version_detector.detect_chrome_version_from_remote_debugging")
    def test_get_chrome_version_diagnostic_info_no_detection(
        self, mock_remote_detect:Mock, mock_binary_detect:Mock
    ) -> None:
        """Test diagnostic info with no detection."""
        mock_binary_detect.return_value = None
        mock_remote_detect.return_value = None

        diagnostic_info = get_chrome_version_diagnostic_info(
            binary_path = None,
            remote_port = None
        )

        assert diagnostic_info["binary_detection"] is None
        assert diagnostic_info["remote_detection"] is None
        assert diagnostic_info["chrome_136_plus_detected"] is False
        assert len(diagnostic_info["recommendations"]) == 0

    def test_get_chrome_version_diagnostic_info_default_values(self) -> None:
        """Test diagnostic info with default values."""
        diagnostic_info = get_chrome_version_diagnostic_info()

        assert diagnostic_info["binary_detection"] is None
        assert diagnostic_info["remote_detection"] is None
        assert diagnostic_info["chrome_136_plus_detected"] is False
        assert diagnostic_info["configuration_valid"] is True
        assert diagnostic_info["recommendations"] == []

    @patch("urllib.request.urlopen")
    def test_detect_chrome_version_from_remote_debugging_json_decode_error(
        self, mock_urlopen:Mock
    ) -> None:
        """Test detect_chrome_version_from_remote_debugging handles JSONDecodeError gracefully."""
        # Mock urlopen to return invalid JSON
        mock_response = Mock()
        mock_response.read.return_value = b"invalid json content"
        mock_urlopen.return_value = mock_response

        # Should return None when JSON decode fails
        result = detect_chrome_version_from_remote_debugging("127.0.0.1", 9222)
        assert result is None
