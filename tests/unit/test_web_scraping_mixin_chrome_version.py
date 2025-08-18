# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import asyncio
import os
from unittest.mock import Mock, patch

import pytest

from kleinanzeigen_bot.utils.chrome_version_detector import ChromeVersionInfo
from kleinanzeigen_bot.utils.web_scraping_mixin import WebScrapingMixin


class TestWebScrapingMixinChromeVersionValidation:
    """Test Chrome version validation in WebScrapingMixin."""

    @pytest.fixture
    def scraper(self) -> WebScrapingMixin:
        """Create a WebScrapingMixin instance for testing."""
        return WebScrapingMixin()

    @patch("kleinanzeigen_bot.utils.web_scraping_mixin.detect_chrome_version_from_binary")
    async def test_validate_chrome_version_configuration_chrome_136_plus_valid(
        self, mock_detect:Mock, scraper:WebScrapingMixin
    ) -> None:
        """Test Chrome 136+ validation with valid configuration."""
        # Setup mocks
        mock_detect.return_value = ChromeVersionInfo("136.0.6778.0", 136, "Chrome")

        # Configure scraper
        scraper.browser_config.binary_location = "/path/to/chrome"
        scraper.browser_config.arguments = ["--remote-debugging-port=9222", "--user-data-dir=/tmp/chrome-debug"]  # noqa: S108
        scraper.browser_config.user_data_dir = "/tmp/chrome-debug"  # noqa: S108

        # Temporarily unset PYTEST_CURRENT_TEST to allow validation to run
        original_env = os.environ.get("PYTEST_CURRENT_TEST")
        if "PYTEST_CURRENT_TEST" in os.environ:
            del os.environ["PYTEST_CURRENT_TEST"]

        try:
            # Test validation
            await scraper._validate_chrome_version_configuration()

            # Verify detection was called correctly
            mock_detect.assert_called_once_with("/path/to/chrome")

            # Verify validation passed (no exception raised)
            # The validation is now done internally in _validate_chrome_136_configuration
        finally:
            # Restore environment
            if original_env:
                os.environ["PYTEST_CURRENT_TEST"] = original_env

    @patch("kleinanzeigen_bot.utils.web_scraping_mixin.detect_chrome_version_from_binary")
    async def test_validate_chrome_version_configuration_chrome_136_plus_invalid(
        self, mock_detect:Mock, scraper:WebScrapingMixin, caplog:pytest.LogCaptureFixture
    ) -> None:
        """Test Chrome 136+ validation with invalid configuration."""
        # Setup mocks
        mock_detect.return_value = ChromeVersionInfo("136.0.6778.0", 136, "Chrome")

        # Configure scraper
        scraper.browser_config.binary_location = "/path/to/chrome"
        scraper.browser_config.arguments = ["--remote-debugging-port=9222"]
        scraper.browser_config.user_data_dir = None

        # Temporarily unset PYTEST_CURRENT_TEST to allow validation to run
        original_env = os.environ.get("PYTEST_CURRENT_TEST")
        if "PYTEST_CURRENT_TEST" in os.environ:
            del os.environ["PYTEST_CURRENT_TEST"]

        try:
            # Test validation should log error but not raise exception due to error handling
            await scraper._validate_chrome_version_configuration()

            # Verify error was logged
            assert "Chrome 136+ configuration validation failed" in caplog.text
            assert "Chrome 136+ requires --user-data-dir" in caplog.text
        finally:
            # Restore environment
            if original_env:
                os.environ["PYTEST_CURRENT_TEST"] = original_env

    @patch("kleinanzeigen_bot.utils.web_scraping_mixin.detect_chrome_version_from_binary")
    async def test_validate_chrome_version_configuration_chrome_pre_136(
        self, mock_detect:Mock, scraper:WebScrapingMixin
    ) -> None:
        """Test Chrome pre-136 validation (no special requirements)."""
        # Setup mocks
        mock_detect.return_value = ChromeVersionInfo("120.0.6099.109", 120, "Chrome")

        # Configure scraper
        scraper.browser_config.binary_location = "/path/to/chrome"
        scraper.browser_config.arguments = ["--remote-debugging-port=9222"]
        scraper.browser_config.user_data_dir = None

        # Temporarily unset PYTEST_CURRENT_TEST to allow validation to run
        original_env = os.environ.get("PYTEST_CURRENT_TEST")
        if "PYTEST_CURRENT_TEST" in os.environ:
            del os.environ["PYTEST_CURRENT_TEST"]

        try:
            # Test validation should pass without validation
            await scraper._validate_chrome_version_configuration()

            # Verify detection was called but no validation
            mock_detect.assert_called_once_with("/path/to/chrome")
        finally:
            # Restore environment
            if original_env:
                os.environ["PYTEST_CURRENT_TEST"] = original_env

    @patch("kleinanzeigen_bot.utils.chrome_version_detector.detect_chrome_version_from_binary")
    async def test_validate_chrome_version_configuration_no_binary_location(
        self, mock_detect:Mock, scraper:WebScrapingMixin
    ) -> None:
        """Test Chrome version validation when no binary location is set."""
        # Configure scraper without binary location
        scraper.browser_config.binary_location = None

        # Test validation should pass without detection
        await scraper._validate_chrome_version_configuration()

        # Verify detection was not called
        mock_detect.assert_not_called()

    @patch("kleinanzeigen_bot.utils.web_scraping_mixin.detect_chrome_version_from_binary")
    async def test_validate_chrome_version_configuration_detection_fails(
        self, mock_detect:Mock, scraper:WebScrapingMixin, caplog:pytest.LogCaptureFixture
    ) -> None:
        """Test Chrome version validation when detection fails."""
        # Setup mocks
        mock_detect.return_value = None

        # Configure scraper
        scraper.browser_config.binary_location = "/path/to/chrome"

        # Temporarily unset PYTEST_CURRENT_TEST to allow validation to run
        original_env = os.environ.get("PYTEST_CURRENT_TEST")
        if "PYTEST_CURRENT_TEST" in os.environ:
            del os.environ["PYTEST_CURRENT_TEST"]

        try:
            # Test validation should pass without validation
            await scraper._validate_chrome_version_configuration()

            # Verify detection was called
            mock_detect.assert_called_once_with("/path/to/chrome")

            # Verify debug log message (line 824)
            assert "Could not detect browser version, skipping validation" in caplog.text
        finally:
            # Restore environment
            if original_env:
                os.environ["PYTEST_CURRENT_TEST"] = original_env


class TestWebScrapingMixinChromeVersionDiagnostics:
    """Test Chrome version diagnostics in WebScrapingMixin."""

    @pytest.fixture
    def scraper(self) -> WebScrapingMixin:
        """Create a WebScrapingMixin instance for testing."""
        return WebScrapingMixin()

    @patch("kleinanzeigen_bot.utils.web_scraping_mixin.get_chrome_version_diagnostic_info")
    @patch("kleinanzeigen_bot.utils.web_scraping_mixin.validate_chrome_136_configuration")
    def test_diagnose_chrome_version_issues_binary_detection(
        self, mock_validate:Mock, mock_get_diagnostic:Mock, scraper:WebScrapingMixin, caplog:pytest.LogCaptureFixture
    ) -> None:
        """Test Chrome version diagnostics with binary detection."""
        # Setup mocks
        mock_get_diagnostic.return_value = {
            "binary_detection": {
                "version_string": "136.0.6778.0",
                "major_version": 136,
                "browser_name": "Chrome",
                "is_chrome_136_plus": True
            },
            "remote_detection": None,
            "chrome_136_plus_detected": True,
            "recommendations": []
        }
        mock_validate.return_value = (True, "")

        # Configure scraper
        scraper.browser_config.binary_location = "/path/to/chrome"
        scraper.browser_config.arguments = ["--remote-debugging-port=9222", "--user-data-dir=/tmp/chrome-debug"]

        # Temporarily unset PYTEST_CURRENT_TEST to allow diagnostics to run
        original_env = os.environ.get("PYTEST_CURRENT_TEST")
        if "PYTEST_CURRENT_TEST" in os.environ:
            del os.environ["PYTEST_CURRENT_TEST"]

        try:
            # Test diagnostics
            scraper._diagnose_chrome_version_issues(9222)

            # Verify logs
            assert "Chrome version from binary: Chrome 136.0.6778.0 (major: 136)" in caplog.text
            assert "Chrome 136+ detected - security validation required" in caplog.text

            # Verify mocks were called
            mock_get_diagnostic.assert_called_once_with(
                binary_path = "/path/to/chrome",
                remote_port = 9222
            )
        finally:
            # Restore environment
            if original_env:
                os.environ["PYTEST_CURRENT_TEST"] = original_env

    @patch("kleinanzeigen_bot.utils.web_scraping_mixin.get_chrome_version_diagnostic_info")
    @patch("kleinanzeigen_bot.utils.web_scraping_mixin.validate_chrome_136_configuration")
    def test_diagnose_chrome_version_issues_remote_detection(
        self, mock_validate:Mock, mock_get_diagnostic:Mock, scraper:WebScrapingMixin, caplog:pytest.LogCaptureFixture
    ) -> None:
        """Test Chrome version diagnostics with remote detection."""
        # Setup mocks
        mock_get_diagnostic.return_value = {
            "binary_detection": None,
            "remote_detection": {
                "version_string": "136.0.6778.0",
                "major_version": 136,
                "browser_name": "Chrome",
                "is_chrome_136_plus": True
            },
            "chrome_136_plus_detected": True,
            "recommendations": []
        }
        mock_validate.return_value = (False, "Chrome 136+ requires --user-data-dir")

        # Configure scraper
        scraper.browser_config.binary_location = "/path/to/chrome"
        scraper.browser_config.arguments = ["--remote-debugging-port=9222"]

        # Temporarily unset PYTEST_CURRENT_TEST to allow diagnostics to run
        original_env = os.environ.get("PYTEST_CURRENT_TEST")
        if "PYTEST_CURRENT_TEST" in os.environ:
            del os.environ["PYTEST_CURRENT_TEST"]

        try:
            # Test diagnostics
            scraper._diagnose_chrome_version_issues(9222)

            # Verify logs
            assert "Chrome version from remote debugging: Chrome 136.0.6778.0 (major: 136)" in caplog.text
            assert "Remote Chrome 136+ detected - validating configuration" in caplog.text
            assert "Chrome 136+ configuration validation failed" in caplog.text

            # Verify validation was called
            mock_validate.assert_called_once_with(
                ["--remote-debugging-port=9222"],
                None
            )
        finally:
            # Restore environment
            if original_env:
                os.environ["PYTEST_CURRENT_TEST"] = original_env

    @patch("kleinanzeigen_bot.utils.web_scraping_mixin.get_chrome_version_diagnostic_info")
    def test_diagnose_chrome_version_issues_no_detection(
        self, mock_get_diagnostic:Mock, scraper:WebScrapingMixin, caplog:pytest.LogCaptureFixture
    ) -> None:
        """Test Chrome version diagnostics with no detection."""
        # Setup mocks
        mock_get_diagnostic.return_value = {
            "binary_detection": None,
            "remote_detection": None,
            "chrome_136_plus_detected": False,
            "recommendations": []
        }

        # Configure scraper
        scraper.browser_config.binary_location = "/path/to/chrome"

        # Temporarily unset PYTEST_CURRENT_TEST to allow diagnostics to run
        original_env = os.environ.get("PYTEST_CURRENT_TEST")
        if "PYTEST_CURRENT_TEST" in os.environ:
            del os.environ["PYTEST_CURRENT_TEST"]

        try:
            # Test diagnostics
            scraper._diagnose_chrome_version_issues(0)

            # Verify no Chrome version logs
            assert "Chrome version from binary" not in caplog.text
            assert "Chrome version from remote debugging" not in caplog.text
        finally:
            # Restore environment
            if original_env:
                os.environ["PYTEST_CURRENT_TEST"] = original_env

    @patch("kleinanzeigen_bot.utils.web_scraping_mixin.get_chrome_version_diagnostic_info")
    def test_diagnose_chrome_version_issues_chrome_136_plus_recommendations(
        self, mock_get_diagnostic:Mock, scraper:WebScrapingMixin, caplog:pytest.LogCaptureFixture
    ) -> None:
        """Test Chrome version diagnostics with Chrome 136+ recommendations."""
        # Setup mocks
        mock_get_diagnostic.return_value = {
            "binary_detection": {
                "version_string": "136.0.6778.0",
                "major_version": 136,
                "browser_name": "Chrome",
                "is_chrome_136_plus": True
            },
            "remote_detection": None,
            "chrome_136_plus_detected": True,
            "recommendations": []
        }

        # Configure scraper
        scraper.browser_config.binary_location = "/path/to/chrome"

        # Temporarily unset PYTEST_CURRENT_TEST to allow diagnostics to run
        original_env = os.environ.get("PYTEST_CURRENT_TEST")
        if "PYTEST_CURRENT_TEST" in os.environ:
            del os.environ["PYTEST_CURRENT_TEST"]

        try:
            # Test diagnostics
            scraper._diagnose_chrome_version_issues(0)

            # Verify recommendations
            assert "Chrome/Edge 136+ security changes require --user-data-dir for remote debugging" in caplog.text
            assert "https://developer.chrome.com/blog/remote-debugging-port" in caplog.text
        finally:
            # Restore environment
            if original_env:
                os.environ["PYTEST_CURRENT_TEST"] = original_env

    @patch("kleinanzeigen_bot.utils.web_scraping_mixin.get_chrome_version_diagnostic_info")
    @patch("kleinanzeigen_bot.utils.web_scraping_mixin.validate_chrome_136_configuration")
    def test_diagnose_chrome_version_issues_binary_pre_136(
        self, mock_validate:Mock, mock_get_diagnostic:Mock, scraper:WebScrapingMixin, caplog:pytest.LogCaptureFixture
    ) -> None:
        """Test Chrome version diagnostics with pre-136 binary detection (lines 832-849)."""
        # Setup mocks to ensure exact branch coverage
        mock_get_diagnostic.return_value = {
            "binary_detection": {
                "version_string": "120.0.6099.109",
                "major_version": 120,
                "browser_name": "Chrome",
                "is_chrome_136_plus": False  # This triggers the else branch (lines 832-849)
            },
            "remote_detection": None,  # Ensure this is None to avoid other branches
            "chrome_136_plus_detected": False,  # Ensure this is False to avoid recommendations
            "recommendations": []
        }

        # Configure scraper
        scraper.browser_config.binary_location = "/path/to/chrome"

        # Temporarily unset PYTEST_CURRENT_TEST to allow diagnostics to run
        original_env = os.environ.get("PYTEST_CURRENT_TEST")
        if "PYTEST_CURRENT_TEST" in os.environ:
            del os.environ["PYTEST_CURRENT_TEST"]

        try:
            # Test diagnostics
            scraper._diagnose_chrome_version_issues(0)

            # Verify pre-136 log message (lines 832-849)
            assert "Chrome pre-136 detected - no special security requirements" in caplog.text

            # Verify that the diagnostic function was called with correct parameters
            mock_get_diagnostic.assert_called_once_with(
                binary_path = "/path/to/chrome",
                remote_port = None
            )
        finally:
            # Restore environment
            if original_env:
                os.environ["PYTEST_CURRENT_TEST"] = original_env

    @patch("kleinanzeigen_bot.utils.web_scraping_mixin.get_chrome_version_diagnostic_info")
    @patch("kleinanzeigen_bot.utils.web_scraping_mixin.validate_chrome_136_configuration")
    def test_diagnose_chrome_version_issues_remote_validation_passes(
        self, mock_validate:Mock, mock_get_diagnostic:Mock, scraper:WebScrapingMixin, caplog:pytest.LogCaptureFixture
    ) -> None:
        """Test Chrome version diagnostics with remote validation passing (line 846)."""
        # Setup mocks
        mock_get_diagnostic.return_value = {
            "binary_detection": None,
            "remote_detection": {
                "version_string": "136.0.6778.0",
                "major_version": 136,
                "browser_name": "Chrome",
                "is_chrome_136_plus": True
            },
            "chrome_136_plus_detected": True,
            "recommendations": []
        }
        mock_validate.return_value = (True, "")  # This triggers the else branch (line 846)

        # Configure scraper
        scraper.browser_config.binary_location = "/path/to/chrome"
        scraper.browser_config.arguments = ["--remote-debugging-port=9222", "--user-data-dir=/tmp/chrome-debug"]  # noqa: S108
        scraper.browser_config.user_data_dir = "/tmp/chrome-debug"  # noqa: S108

        # Temporarily unset PYTEST_CURRENT_TEST to allow diagnostics to run
        original_env = os.environ.get("PYTEST_CURRENT_TEST")
        if "PYTEST_CURRENT_TEST" in os.environ:
            del os.environ["PYTEST_CURRENT_TEST"]

        try:
            # Test diagnostics
            scraper._diagnose_chrome_version_issues(9222)

            # Verify validation passed log message (line 846)
            assert "Chrome 136+ configuration validation passed" in caplog.text

            # Verify validation was called with correct arguments
            mock_validate.assert_called_once_with(
                ["--remote-debugging-port=9222", "--user-data-dir=/tmp/chrome-debug"],  # noqa: S108
                "/tmp/chrome-debug"  # noqa: S108
            )
        finally:
            # Restore environment
            if original_env:
                os.environ["PYTEST_CURRENT_TEST"] = original_env


class TestWebScrapingMixinIntegration:
    """Test integration of Chrome version detection in WebScrapingMixin."""

    @pytest.fixture
    def scraper(self) -> WebScrapingMixin:
        """Create a WebScrapingMixin instance for testing."""
        return WebScrapingMixin()

    @patch.object(WebScrapingMixin, "_validate_chrome_version_configuration")
    @patch.object(WebScrapingMixin, "get_compatible_browser")
    async def test_create_browser_session_calls_chrome_validation(
        self, mock_get_browser:Mock, mock_validate:Mock, scraper:WebScrapingMixin
    ) -> None:
        """Test that create_browser_session calls Chrome version validation."""
        # Setup mocks
        mock_get_browser.return_value = "/path/to/chrome"
        mock_validate.return_value = None

        # Configure scraper
        scraper.browser_config.binary_location = None

        # Test that validation is called
        try:
            await scraper.create_browser_session()
        except Exception:  # noqa: S110
            # We expect it to fail later, but validation should be called first
            # This is expected behavior in the test - we're testing that validation runs before failure
            pass

        # Verify validation was called
        mock_validate.assert_called_once()

    @patch.object(WebScrapingMixin, "_diagnose_chrome_version_issues")
    @patch.object(WebScrapingMixin, "get_compatible_browser")
    def test_diagnose_browser_issues_calls_chrome_diagnostics(
        self, mock_get_browser:Mock, mock_diagnose:Mock, scraper:WebScrapingMixin
    ) -> None:
        """Test that diagnose_browser_issues calls Chrome version diagnostics."""
        # Setup mocks
        mock_get_browser.return_value = "/path/to/chrome"

        # Configure scraper
        scraper.browser_config.binary_location = None
        scraper.browser_config.arguments = ["--remote-debugging-port=9222"]

        # Test diagnostics
        scraper.diagnose_browser_issues()

        # Verify Chrome diagnostics was called
        mock_diagnose.assert_called_once_with(9222)

    def test_backward_compatibility_old_configs_still_work(self) -> None:
        """Test that old configurations without Chrome 136+ validation still work."""
        # Create a scraper with old-style config (no user_data_dir)
        scraper = WebScrapingMixin()

        # Set up old-style config (pre-Chrome 136+)
        scraper.browser_config.arguments = ["--remote-debugging-port=9222"]
        scraper.browser_config.user_data_dir = None  # Old configs didn't have this

        # Mock Chrome version detection to return pre-136 version
        with patch("kleinanzeigen_bot.utils.web_scraping_mixin.detect_chrome_version_from_binary") as mock_detect:
            mock_detect.return_value = ChromeVersionInfo(
                "120.0.6099.109", 120, "Chrome"
            )

            # Temporarily unset PYTEST_CURRENT_TEST to allow validation to run
            original_env = os.environ.get("PYTEST_CURRENT_TEST")
            if "PYTEST_CURRENT_TEST" in os.environ:
                del os.environ["PYTEST_CURRENT_TEST"]

            try:
                # This should not raise an exception for pre-136 Chrome
                asyncio.run(scraper._validate_chrome_version_configuration())

                # Verify that the validation passed (no exception raised)
                # The method should log that pre-136 Chrome was detected
                # and no special validation is required
            finally:
                # Restore environment
                if original_env:
                    os.environ["PYTEST_CURRENT_TEST"] = original_env
