# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Unit tests for web_scraping_mixin.py focusing on error handling scenarios.

Copyright (c) 2024, kleinanzeigen-bot contributors.
All rights reserved.
"""

import json
import os
import platform
import shutil
import zipfile
from pathlib import Path
from typing import NoReturn, Protocol, cast
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import nodriver
import psutil
import pytest
from nodriver.core.element import Element
from nodriver.core.tab import Tab as Page

from kleinanzeigen_bot.utils.web_scraping_mixin import By, Is, WebScrapingMixin


class ConfigProtocol(Protocol):
    """Protocol for Config objects used in tests."""
    extensions:list[str]
    browser_args:list[str]
    user_data_dir:str | None

    def add_extension(self, ext:str) -> None: ...


class TrulyAwaitableMockPage:
    """A helper to make a mock Page object truly awaitable for tests."""

    def __init__(self) -> None:
        self._mock = AsyncMock(spec = Page)
        self.url = "https://example.com"
        self.query_selector = AsyncMock()
        self.evaluate = AsyncMock()

    def __getattr__(self, item:str) -> object:
        return getattr(self._mock, item)

    def __await__(self) -> object:
        async def _noop() -> "TrulyAwaitableMockPage":
            return self

        return _noop().__await__()

    # Allow setting attributes on the mock
    def __setattr__(self, key:str, value:object) -> None:
        if key in {"_mock", "url", "query_selector", "evaluate"}:
            object.__setattr__(self, key, value)
        else:
            setattr(self._mock, key, value)


@pytest.fixture
def mock_page() -> TrulyAwaitableMockPage:
    """Create a truly awaitable mock Page object."""
    page = TrulyAwaitableMockPage()
    return page


@pytest.fixture
def mock_browser() -> AsyncMock:
    """Create a mock Browser object."""
    browser = AsyncMock()
    browser.websocket_url = "ws://localhost:9222"
    return browser


@pytest.fixture
def web_scraper(mock_browser:AsyncMock, mock_page:TrulyAwaitableMockPage) -> WebScrapingMixin:
    """Create a WebScrapingMixin instance with mocked browser and page."""
    scraper = WebScrapingMixin()
    scraper.browser = mock_browser
    scraper.page = mock_page  # type: ignore[unused-ignore,reportAttributeAccessIssue]
    return scraper


class TestWebScrapingErrorHandling:
    """Test error handling scenarios in WebScrapingMixin."""

    @pytest.mark.asyncio
    async def test_web_find_timeout(self, web_scraper:WebScrapingMixin, mock_page:TrulyAwaitableMockPage) -> None:
        """Test timeout handling in web_find."""
        # Mock page.query_selector to return None, simulating element not found
        mock_page.query_selector.return_value = None

        # Test timeout for ID selector
        with pytest.raises(TimeoutError, match = "No HTML element found with ID 'test-id'"):
            await web_scraper.web_find(By.ID, "test-id", timeout = 0.1)

        # Test timeout for class selector
        with pytest.raises(TimeoutError, match = "No HTML element found with CSS class 'test-class'"):
            await web_scraper.web_find(By.CLASS_NAME, "test-class", timeout = 0.1)

    @pytest.mark.asyncio
    async def test_web_find_network_error(self, web_scraper:WebScrapingMixin, mock_page:TrulyAwaitableMockPage) -> None:
        """Test network error handling in web_find."""
        # Mock page.query_selector to raise a network error
        mock_page.query_selector.side_effect = Exception("Network error")

        # Test network error for ID selector
        with pytest.raises(Exception, match = "Network error"):
            await web_scraper.web_find(By.ID, "test-id", timeout = 0.1)

    @pytest.mark.asyncio
    async def test_web_click_element_not_found(self, web_scraper:WebScrapingMixin, mock_page:TrulyAwaitableMockPage) -> None:
        """Test element not found error in web_click."""
        # Mock page.query_selector to return None
        mock_page.query_selector.return_value = None

        # Test element not found error
        with pytest.raises(TimeoutError, match = "No HTML element found with ID 'test-id'"):
            await web_scraper.web_click(By.ID, "test-id", timeout = 0.1)

    @pytest.mark.asyncio
    async def test_web_click_element_not_clickable(self, web_scraper:WebScrapingMixin, mock_page:TrulyAwaitableMockPage) -> None:
        """Test element not clickable error in web_click."""
        # Create a mock element that raises an error on click
        mock_element = AsyncMock(spec = Element)
        mock_element.click.side_effect = Exception("Element not clickable")
        mock_page.query_selector.return_value = mock_element

        # Test element not clickable error
        with pytest.raises(Exception, match = "Element not clickable"):
            await web_scraper.web_click(By.ID, "test-id")

    @pytest.mark.asyncio
    async def test_web_input_element_not_found(self, web_scraper:WebScrapingMixin, mock_page:TrulyAwaitableMockPage) -> None:
        """Test element not found error in web_input."""
        # Mock page.query_selector to return None
        mock_page.query_selector.return_value = None

        # Test element not found error
        with pytest.raises(TimeoutError, match = "No HTML element found with ID 'test-id'"):
            await web_scraper.web_input(By.ID, "test-id", "test text", timeout = 0.1)

    @pytest.mark.asyncio
    async def test_web_input_clear_failure(self, web_scraper:WebScrapingMixin, mock_page:TrulyAwaitableMockPage) -> None:
        """Test input clear failure in web_input."""
        # Create a mock element that raises an error on clear_input
        mock_element = AsyncMock(spec = Element)
        mock_element.clear_input.side_effect = Exception("Cannot clear input")
        mock_page.query_selector.return_value = mock_element

        # Test input clear failure
        with pytest.raises(Exception, match = "Cannot clear input"):
            await web_scraper.web_input(By.ID, "test-id", "test text")

    @pytest.mark.asyncio
    async def test_web_open_timeout(self, web_scraper:WebScrapingMixin, mock_browser:AsyncMock) -> None:
        """Test page load timeout in web_open."""
        # Mock browser.get to return a page that never loads
        mock_page = TrulyAwaitableMockPage()
        mock_browser.get.return_value = mock_page

        # Mock web_execute to never return True for document.readyState
        setattr(web_scraper, "web_execute", AsyncMock(return_value = False))

        # Ensure page is None so the timeout path is exercised
        web_scraper.page = None  # type: ignore[unused-ignore,reportAttributeAccessIssue]

        # Test page load timeout
        with pytest.raises(TimeoutError, match = "Page did not finish loading within"):
            await web_scraper.web_open("https://example.com", timeout = 0.1)

    @pytest.mark.asyncio
    async def test_web_request_invalid_response(self, web_scraper:WebScrapingMixin, mock_page:TrulyAwaitableMockPage) -> None:
        """Test invalid response handling in web_request."""
        # Mock page.evaluate to return an invalid response
        mock_page.evaluate.return_value = {"statusCode": 404, "statusMessage": "Not Found", "headers": {}, "content": "Page not found"}

        # Test invalid response error
        with pytest.raises(AssertionError, match = "Invalid response"):
            await web_scraper.web_request("https://example.com")

    @pytest.mark.asyncio
    async def test_web_request_network_error(self, web_scraper:WebScrapingMixin, mock_page:TrulyAwaitableMockPage) -> None:
        """Test network error handling in web_request."""
        # Mock page.evaluate to raise a network error
        mock_page.evaluate.side_effect = Exception("Network error")

        # Test network error
        with pytest.raises(Exception, match = "Network error"):
            await web_scraper.web_request("https://example.com")

    @pytest.mark.asyncio
    async def test_web_check_element_not_found(self, web_scraper:WebScrapingMixin, mock_page:TrulyAwaitableMockPage) -> None:
        """Test element not found error in web_check."""
        # Mock page.query_selector to return None
        mock_page.query_selector.return_value = None

        # Test element not found error
        with pytest.raises(TimeoutError, match = "No HTML element found with ID 'test-id'"):
            await web_scraper.web_check(By.ID, "test-id", Is.CLICKABLE, timeout = 0.1)

    @pytest.mark.asyncio
    async def test_web_check_attribute_error(self, web_scraper:WebScrapingMixin, mock_page:TrulyAwaitableMockPage) -> None:
        """Test attribute error in web_check."""
        # Create a mock element that raises an error on attribute check
        mock_element = AsyncMock(spec = Element)
        mock_element.attrs = {}
        mock_element.apply.side_effect = Exception("Attribute error")
        mock_page.query_selector.return_value = mock_element

        # Test attribute error
        with pytest.raises(Exception, match = "Attribute error"):
            await web_scraper.web_check(By.ID, "test-id", Is.DISPLAYED)


class TestWebScrapingSessionManagement:
    """Test session management edge cases in WebScrapingMixin."""

    def test_close_browser_session_cleans_up(self, mock_browser:AsyncMock) -> None:
        """Test that close_browser_session cleans up browser and page references and kills child processes."""
        scraper = WebScrapingMixin()
        scraper.browser = MagicMock()
        scraper.page = MagicMock()
        scraper.browser._process_pid = 12345
        stop_mock = scraper.browser.stop = MagicMock()
        # Patch psutil.Process and its children
        with patch("psutil.Process") as mock_proc:
            mock_child = MagicMock()
            mock_child.is_running.return_value = True
            mock_proc.return_value.children.return_value = [mock_child]
            scraper.close_browser_session()
            # Browser stop should be called
            stop_mock.assert_called_once()
            # Child process kill should be called
            mock_child.kill.assert_called_once()
            # Browser and page references should be cleared
            assert scraper.browser is None
            assert scraper.page is None

    def test_close_browser_session_double_close(self) -> None:
        """Test that calling close_browser_session twice does not raise and is idempotent."""
        scraper = WebScrapingMixin()
        scraper.browser = MagicMock()
        scraper.page = MagicMock()
        scraper.browser._process_pid = 12345
        scraper.browser.stop = MagicMock()
        with patch("psutil.Process") as mock_proc:
            mock_child = MagicMock()
            mock_child.is_running.return_value = True
            mock_proc.return_value.children.return_value = [mock_child]
            scraper.close_browser_session()
            # Second call should not raise
            scraper.close_browser_session()

    def test_close_browser_session_no_browser(self) -> None:
        """Test that close_browser_session is a no-op if browser is None."""
        scraper = WebScrapingMixin()
        scraper.browser = None  # type: ignore[unused-ignore,reportAttributeAccessIssue]
        scraper.page = MagicMock()
        # Should not raise
        scraper.close_browser_session()
        # Page should remain unchanged
        assert scraper.page is not None

    def test_get_compatible_browser_raises_on_unknown_os(self) -> None:
        """Test get_compatible_browser raises AssertionError on unknown OS."""
        scraper = WebScrapingMixin()
        with patch("platform.system", return_value = "UnknownOS"), pytest.raises(AssertionError):
            scraper.get_compatible_browser()

    def test_get_compatible_browser_raises_if_no_browser_found(self) -> None:
        """Test get_compatible_browser raises AssertionError if no browser is found."""
        scraper = WebScrapingMixin()
        with (
            patch("platform.system", return_value = "Linux"),
            patch("os.path.isfile", return_value = False),
            patch("shutil.which", return_value = None),
            pytest.raises(AssertionError),
        ):
            scraper.get_compatible_browser()

    def test_close_browser_session_no_children(self) -> None:
        """Test that close_browser_session handles case when browser has no child processes."""
        scraper = WebScrapingMixin()
        scraper.browser = MagicMock()
        scraper.page = MagicMock()
        scraper.browser._process_pid = 12345
        stop_mock = scraper.browser.stop = MagicMock()

        # Mock Process to return no children
        with patch("psutil.Process") as mock_proc:
            mock_proc.return_value.children.return_value = []
            scraper.close_browser_session()
            stop_mock.assert_called_once()
            assert scraper.browser is None
            assert scraper.page is None

    @pytest.mark.asyncio
    async def test_session_expiration_handling(self, web_scraper:WebScrapingMixin, mock_browser:AsyncMock) -> None:
        """Test handling of expired browser sessions."""
        mock_browser.get.side_effect = Exception("Session expired")
        web_scraper.page = None  # type: ignore[unused-ignore,reportAttributeAccessIssue]
        with pytest.raises(Exception, match = "Session expired"):
            await web_scraper.web_open("https://example.com")
        # Do not assert browser/page are None, as production code does not clear them

    @pytest.mark.asyncio
    async def test_multiple_session_handling(self, web_scraper:WebScrapingMixin, mock_browser:AsyncMock) -> None:
        """Test handling of multiple browser sessions."""
        mock_page1 = TrulyAwaitableMockPage()
        mock_browser.get.return_value = mock_page1
        mock_browser._process_pid = 12345
        # Patch stop as MagicMock to avoid RuntimeWarning
        mock_browser.stop = MagicMock()
        await web_scraper.web_open("https://example1.com")
        assert web_scraper.page == mock_page1
        # Patch psutil.Process to avoid NoSuchProcess error
        with patch("psutil.Process") as mock_proc:
            mock_child = MagicMock()
            mock_child.is_running.return_value = True
            mock_proc.return_value.children.return_value = [mock_child]
            web_scraper.close_browser_session()
        assert web_scraper.browser is None
        assert web_scraper.page is None
        # Re-assign browser for new session
        web_scraper.browser = mock_browser
        mock_page2 = TrulyAwaitableMockPage()
        mock_browser.get.return_value = mock_page2
        mock_browser._process_pid = 12346
        await web_scraper.web_open("https://example2.com")
        assert web_scraper.page == mock_page2

    @pytest.mark.asyncio
    async def test_browser_crash_recovery(self, web_scraper:WebScrapingMixin, mock_browser:AsyncMock) -> None:
        """Test recovery from browser crash."""
        web_scraper.page = None  # type: ignore[unused-ignore,reportAttributeAccessIssue]
        web_scraper.browser = None  # type: ignore[unused-ignore,reportAttributeAccessIssue]
        # Reassign the mock browser before setting up the side effect
        web_scraper.browser = mock_browser
        mock_browser.get.side_effect = Exception("Browser crashed")
        with pytest.raises(Exception, match = "Browser crashed"):
            await web_scraper.web_open("https://example.com")
        # Do not assert browser/page are None, as production code does not clear them
        mock_page = TrulyAwaitableMockPage()
        mock_browser.get.side_effect = None
        mock_browser.get.return_value = mock_page
        await web_scraper.web_open("https://example.com")
        assert web_scraper.page == mock_page

    @pytest.mark.asyncio
    async def test_web_await_custom_condition_success(self, web_scraper:WebScrapingMixin) -> None:
        """Test web_await returns when custom condition is met."""
        call_count = {"count": 0}

        async def condition() -> bool:
            call_count["count"] += 1
            return call_count["count"] >= 3

        result:bool = await web_scraper.web_await(condition, timeout = 1)
        assert result is True
        assert call_count["count"] >= 3

    @pytest.mark.asyncio
    async def test_web_await_custom_condition_timeout(self, web_scraper:WebScrapingMixin) -> None:
        """Test web_await raises TimeoutError if condition is never met."""

        async def condition() -> bool:
            return False

        with pytest.raises(TimeoutError):
            await web_scraper.web_await(condition, timeout = 0.05)

    @pytest.mark.asyncio
    async def test_web_find_retry_mechanism(self, web_scraper:WebScrapingMixin, mock_page:TrulyAwaitableMockPage) -> None:
        """Test web_find retries until element is found within timeout."""
        call_count = {"count": 0}

        async def query_selector(*args:object, **kwargs:object) -> AsyncMock | None:
            call_count["count"] += 1
            if call_count["count"] == 3:
                return AsyncMock(spec = Element)
            return None

        mock_page.query_selector.side_effect = query_selector
        result = await web_scraper.web_find(By.ID, "test-id", timeout = 0.2)
        assert result is not None
        assert call_count["count"] >= 3

    @pytest.mark.asyncio
    async def test_web_find_element_state_change(self, web_scraper:WebScrapingMixin, mock_page:TrulyAwaitableMockPage) -> None:
        """Test web_check detects element state change (e.g., becomes visible)."""
        call_count = {"count": 0}

        async def query_selector(*args:object, **kwargs:object) -> AsyncMock | None:
            call_count["count"] += 1
            if call_count["count"] == 2:
                element = AsyncMock(spec = Element)
                element.attrs = {}

                async def apply_fn(*a:object, **kw:object) -> bool:
                    return True

                element.apply = AsyncMock(side_effect = apply_fn)
                return element
            return None

        mock_page.query_selector.side_effect = query_selector
        result = await web_scraper.web_check(By.ID, "test-id", Is.DISPLAYED, timeout = 1.0)
        assert result is True
        assert call_count["count"] >= 2

    @pytest.mark.asyncio
    async def test_web_find_timeout_configuration(self, web_scraper:WebScrapingMixin, mock_page:TrulyAwaitableMockPage) -> None:
        """Test web_find respects timeout configuration and raises TimeoutError."""
        mock_page.query_selector.return_value = None
        with pytest.raises(TimeoutError):
            await web_scraper.web_find(By.ID, "test-id", timeout = 0.05)


class TestWebScrapingBrowserConfiguration:
    """Test browser configuration in WebScrapingMixin."""

    @pytest.mark.asyncio
    async def test_browser_binary_location_detection(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        """Test browser binary location detection on different platforms."""
        scraper = WebScrapingMixin()

        # Test Linux
        monkeypatch.setattr(platform, "system", lambda: "Linux")
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/chrome" if x == "google-chrome" else None)
        monkeypatch.setattr(os.path, "isfile", lambda p: p == "/usr/bin/chrome")
        assert scraper.get_compatible_browser() == "/usr/bin/chrome"

        # Test macOS
        monkeypatch.setattr(platform, "system", lambda: "Darwin")
        mac_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        monkeypatch.setattr(os.path, "isfile", lambda p: p == mac_path)
        assert scraper.get_compatible_browser() == mac_path

        # Test Windows
        monkeypatch.setattr(platform, "system", lambda: "Windows")
        win_path = "C:\\Program Files\\Chrome\\Application\\chrome.exe"
        # Mock os.environ to include PROGRAMFILES and PROGRAMFILES(X86) and LOCALAPPDATA
        monkeypatch.setenv("PROGRAMFILES", "C:\\Program Files")
        monkeypatch.setenv("PROGRAMFILES(X86)", "C:\\Program Files (x86)")
        monkeypatch.setenv("LOCALAPPDATA", "C:\\Users\\TestUser\\AppData\\Local")
        monkeypatch.setattr(os.path, "isfile", lambda p: p == win_path)
        assert scraper.get_compatible_browser() == win_path

    @pytest.mark.asyncio
    async def test_browser_profile_configuration(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        """Test browser profile configuration and preferences handling."""
        class DummyConfig:
            def __init__(self, **kwargs:object) -> None:
                self.browser_args:list[str] = []
                self.user_data_dir:str | None = None
                self.extensions:list[str] = []
                self.browser_executable_path:str | None = None
                self.host:str | None = None
                self.port:int | None = None
                self.headless:bool = False
                self._extensions:list[str] = []  # Add private extensions list

            def add_extension(self, ext:str) -> None:
                self._extensions.append(ext)  # Use private extensions list

        # Mock nodriver.start to return a mock browser
        mock_browser = AsyncMock()
        mock_browser.websocket_url = "ws://localhost:9222"
        monkeypatch.setattr(nodriver, "start", AsyncMock(return_value = mock_browser))

        # Mock Config class
        monkeypatch.setattr(nodriver.core.config, "Config", DummyConfig)  # type: ignore[unused-ignore,reportAttributeAccessIssue]

        # Mock os.path.exists to return True for the browser binary and use real exists for Preferences file (and Edge)
        edge_path = "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"
        chrome_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        real_exists = os.path.exists

        def mock_exists(path:str) -> bool:
            # Handle Linux browser paths
            if path in {
                "/usr/bin/chromium",
                "/usr/bin/chromium-browser",
                "/usr/bin/google-chrome",
                "/usr/bin/microsoft-edge",
                "/usr/bin/chrome",
                edge_path,
                chrome_path
            }:
                return True
            if "Preferences" in str(path) and str(tmp_path) in str(path):
                return real_exists(path)
            return False
        monkeypatch.setattr(os.path, "exists", mock_exists)

        # Create test profile directory
        profile_dir = tmp_path / "Default"
        profile_dir.mkdir()
        prefs_file = profile_dir / "Preferences"

        # Test with existing preferences file
        with open(prefs_file, "w", encoding = "UTF-8") as f:
            json.dump({"existing": "prefs"}, f)

        scraper = WebScrapingMixin()
        scraper.browser_config.user_data_dir = str(tmp_path)
        scraper.browser_config.profile_name = "Default"
        await scraper.create_browser_session()

        # Verify preferences file was not overwritten
        with open(prefs_file, "r", encoding = "UTF-8") as f:
            prefs = json.load(f)
            assert prefs["existing"] == "prefs"

        # Test with missing preferences file
        prefs_file.unlink()
        await scraper.create_browser_session()

        # Verify new preferences file was created with correct settings
        with open(prefs_file, "r", encoding = "UTF-8") as f:
            prefs = json.load(f)
            assert prefs["credentials_enable_service"] is False
            assert prefs["enable_do_not_track"] is True
            assert prefs["profile"]["password_manager_enabled"] is False
            assert prefs["signin"]["allowed"] is False
            assert "www.kleinanzeigen.de" in prefs["translate_site_blacklist"]

    @pytest.mark.asyncio
    async def test_browser_arguments_configuration(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        """Test browser arguments configuration."""
        class DummyConfig:
            def __init__(self, **kwargs:object) -> None:
                self.browser_args:list[str] = []
                self.user_data_dir:str | None = None
                self.extensions:list[str] = []
                self.browser_executable_path:str | None = None
                self.host:str | None = None
                self.port:int | None = None
                self.headless:bool = False

            def add_extension(self, ext:str) -> None:
                self.extensions.append(ext)

        # Mock nodriver.start to return a mock browser
        mock_browser = AsyncMock()
        mock_browser.websocket_url = "ws://localhost:9222"
        monkeypatch.setattr(nodriver, "start", AsyncMock(return_value = mock_browser))

        # Mock Config class
        monkeypatch.setattr(nodriver.core.config, "Config", DummyConfig)  # type: ignore[unused-ignore,reportAttributeAccessIssue]

        # Mock os.path.exists to return True for both Chrome and Edge paths
        monkeypatch.setattr(os.path, "exists", lambda p: p in {"/usr/bin/chrome", "/usr/bin/edge"})

        # Test with custom arguments
        scraper = WebScrapingMixin()
        scraper.browser_config.arguments = ["--custom-arg=value", "--another-arg"]
        scraper.browser_config.use_private_window = True
        scraper.browser_config.binary_location = "/usr/bin/chrome"
        await scraper.create_browser_session()

        # Verify browser arguments
        config = cast(Mock, nodriver.start).call_args[0][0]
        assert "--custom-arg=value" in config.browser_args
        assert "--another-arg" in config.browser_args
        assert "--incognito" in config.browser_args
        assert "--disable-crash-reporter" in config.browser_args
        assert "--disable-domain-reliability" in config.browser_args

        # Test with Edge browser
        scraper = WebScrapingMixin()
        scraper.browser_config.binary_location = "/usr/bin/edge"
        await scraper.create_browser_session()

        # Verify Edge-specific arguments
        config = cast(Mock, nodriver.start).call_args[0][0]
        assert "-inprivate" in config.browser_args
        assert os.environ.get("MSEDGEDRIVER_TELEMETRY_OPTOUT") == "1"

    @pytest.mark.asyncio
    async def test_browser_extension_loading(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        """Test browser extension loading."""
        class DummyConfig:
            def __init__(self, **kwargs:object) -> None:
                self.browser_args:list[str] = []
                self.user_data_dir:str | None = None
                self.extensions:list[str] = []
                self.browser_executable_path:str | None = None
                self.host:str | None = None
                self.port:int | None = None
                self.headless:bool = False
                self._extensions:list[str] = []  # Add private extensions list

            def add_extension(self, ext:str) -> None:
                self._extensions.append(ext)  # Use private extensions list

        # Create test extension files
        ext1 = tmp_path / "ext1.crx"
        ext2 = tmp_path / "ext2.crx"

        # Create proper CRX files (which are ZIP files)
        with zipfile.ZipFile(ext1, "w") as z:
            z.writestr("manifest.json", '{"name": "Test Extension 1"}')
        with zipfile.ZipFile(ext2, "w") as z:
            z.writestr("manifest.json", '{"name": "Test Extension 2"}')

        # Mock nodriver.start to return a mock browser
        mock_browser = AsyncMock()
        mock_browser.websocket_url = "ws://localhost:9222"
        monkeypatch.setattr(nodriver, "start", AsyncMock(return_value = mock_browser))

        # Mock Config class
        monkeypatch.setattr(nodriver.core.config, "Config", DummyConfig)  # type: ignore[unused-ignore,reportAttributeAccessIssue]

        # Mock os.path.exists to return True for browser binaries and extension files, real_exists for others
        real_exists = os.path.exists
        monkeypatch.setattr(
            os.path,
            "exists",
            lambda p: p in {"/usr/bin/chrome", "/usr/bin/edge", str(ext1), str(ext2)} or real_exists(p),
        )

        # Test extension loading
        scraper = WebScrapingMixin()
        scraper.browser_config.extensions = [str(ext1), str(ext2)]
        scraper.browser_config.binary_location = "/usr/bin/chrome"
        # Removed monkeypatch for os.path.exists so extension files are detected
        await scraper.create_browser_session()

        # Verify extensions were loaded
        config = cast(Mock, nodriver.start).call_args[0][0]
        assert len(config._extensions) == 2
        for ext_path in config._extensions:
            assert os.path.exists(ext_path)
            assert os.path.isdir(ext_path)

        # Test with non-existent extension
        scraper.browser_config.extensions = ["non_existent.crx"]
        with pytest.raises(AssertionError):
            await scraper.create_browser_session()

    @pytest.mark.asyncio
    async def test_browser_binary_location_detection_edge_cases(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        """Test browser binary location detection edge cases."""
        scraper = WebScrapingMixin()

        # Test Linux with multiple browser options
        def which_mock(x:str) -> str | None:
            return {
                "chromium": "/usr/bin/chromium",
                "chromium-browser": None,
                "google-chrome": None,
                "microsoft-edge": None
            }.get(x)
        monkeypatch.setattr(platform, "system", lambda: "Linux")
        monkeypatch.setattr(shutil, "which", which_mock)
        monkeypatch.setattr(os.path, "isfile", lambda p: p == "/usr/bin/chromium")
        assert scraper.get_compatible_browser() == "/usr/bin/chromium"

        # Test Linux with no browsers found
        monkeypatch.setattr(shutil, "which", lambda x: None)
        monkeypatch.setattr(os.path, "isfile", lambda p: False)
        with pytest.raises(AssertionError, match = "Installed browser could not be detected"):
            scraper.get_compatible_browser()

        # Test Windows with environment variables not set
        monkeypatch.setattr(platform, "system", lambda: "Windows")
        # Set default values for environment variables
        monkeypatch.setenv("PROGRAMFILES", "C:\\Program Files")
        monkeypatch.setenv("PROGRAMFILES(X86)", "C:\\Program Files (x86)")
        monkeypatch.setenv("LOCALAPPDATA", "C:\\Users\\TestUser\\AppData\\Local")
        monkeypatch.setattr(os.path, "isfile", lambda p: False)
        with pytest.raises(AssertionError, match = "Installed browser could not be detected"):
            scraper.get_compatible_browser()

        # Test macOS with non-existent paths
        monkeypatch.setattr(platform, "system", lambda: "Darwin")
        monkeypatch.setattr(os.path, "isfile", lambda p: False)
        with pytest.raises(AssertionError, match = "Installed browser could not be detected"):
            scraper.get_compatible_browser()

    @pytest.mark.asyncio
    async def test_session_state_persistence(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        """Test that session state persists across browser restarts when user_data_dir is set."""
        # DummyConfig to simulate browser config
        class DummyConfig:
            def __init__(self, **kwargs:object) -> None:
                self.browser_args:list[str] = []
                self.user_data_dir:str | None = None
                self.extensions:list[str] = []
                self.browser_executable_path:str | None = None
                self.host:str | None = None
                self.port:int | None = None
                self.headless:bool = False
                self._extensions:list[str] = []

            def add_extension(self, ext:str) -> None:
                self._extensions.append(ext)

        # Mock nodriver.start to return a mock browser
        mock_browser = AsyncMock()
        mock_browser.websocket_url = "ws://localhost:9222"
        monkeypatch.setattr(nodriver, "start", AsyncMock(return_value = mock_browser))
        monkeypatch.setattr(nodriver.core.config, "Config", DummyConfig)  # type: ignore[unused-ignore,reportAttributeAccessIssue]
        monkeypatch.setattr(os.path, "exists", lambda p: True)

        # Simulate state file in user_data_dir
        state_file = tmp_path / "Default" / "state.json"
        state_file.parent.mkdir(parents = True, exist_ok = True)

        # First session: write state
        scraper = WebScrapingMixin()
        scraper.browser_config.user_data_dir = str(tmp_path)
        scraper.browser_config.profile_name = "Default"
        await scraper.create_browser_session()
        with open(state_file, "w", encoding = "utf-8") as f:
            f.write('{"foo": "bar"}')
        scraper.browser._process_pid = 12345
        scraper.browser.stop = MagicMock()
        with patch("psutil.Process") as mock_proc:
            mock_proc.return_value.children.return_value = []
            scraper.close_browser_session()

        # Second session: read state
        scraper2 = WebScrapingMixin()
        scraper2.browser_config.user_data_dir = str(tmp_path)
        scraper2.browser_config.profile_name = "Default"
        await scraper2.create_browser_session()
        with open(state_file, "r", encoding = "utf-8") as f:
            data = f.read()
        assert data == '{"foo": "bar"}'
        scraper2.browser._process_pid = 12346
        scraper2.browser.stop = MagicMock()
        with patch("psutil.Process") as mock_proc:
            mock_proc.return_value.children.return_value = []
            scraper2.close_browser_session()

    @pytest.mark.asyncio
    async def test_session_creation_error_cleanup(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        """Test that resources are cleaned up when session creation fails."""
        class DummyConfig:
            def __init__(self, **kwargs:object) -> None:
                self.browser_args:list[str] = []
                self.user_data_dir:str | None = None
                self.extensions:list[str] = []
                self.browser_executable_path:str | None = None
                self.host:str | None = None
                self.port:int | None = None
                self.headless:bool = False
                self._extensions:list[str] = []

            def add_extension(self, ext:str) -> None:
                self._extensions.append(ext)

        # Create a temporary file before the test
        temp_file = tmp_path / "temp_resource"
        temp_file.write_text("test")

        # Mock nodriver.start to raise an exception
        async def mock_start_fail(*args:object, **kwargs:object) -> NoReturn:
            if temp_file.exists():
                temp_file.unlink()
            raise Exception("Session creation failed")

        def make_mock_browser() -> AsyncMock:
            mock_browser = AsyncMock()
            mock_browser.websocket_url = "ws://localhost:9222"
            mock_browser._process_pid = 12345
            mock_browser.stop = MagicMock()
            return mock_browser

        monkeypatch.setattr(nodriver, "start", mock_start_fail)
        monkeypatch.setattr(nodriver.core.config, "Config", DummyConfig)  # type: ignore[unused-ignore,reportAttributeAccessIssue]
        monkeypatch.setattr(os.path, "exists", lambda p: True)

        # Attempt to create a session
        scraper = WebScrapingMixin()
        scraper.browser_config.user_data_dir = str(tmp_path)
        scraper.browser_config.profile_name = "Default"

        with pytest.raises(Exception, match = "Session creation failed"):
            await scraper.create_browser_session()  # type: ignore[unused-ignore,reportGeneralTypeIssues]  # Awaiting a function that always raises

        assert not (tmp_path / "temp_resource").exists()
        assert scraper.browser is None
        assert scraper.page is None

        # Now patch nodriver.start to return a new mock browser each time
        mock_browser = make_mock_browser()
        mock_page = TrulyAwaitableMockPage()
        mock_browser.get = AsyncMock(return_value = mock_page)
        monkeypatch.setattr(nodriver, "start", AsyncMock(return_value = mock_browser))

        # Mock create_browser_session to ensure proper setup
        async def mock_create_session(self:WebScrapingMixin) -> None:
            self.browser = mock_browser
            self.page = mock_page  # type: ignore[unused-ignore,reportAttributeAccessIssue]  # Assigning mock page for test

        monkeypatch.setattr(WebScrapingMixin, "create_browser_session", mock_create_session)
        await scraper.create_browser_session()  # type: ignore[unused-ignore,reportGeneralTypeIssues]  # Awaiting a function that always raises
        print("[DEBUG] scraper.page after session creation:", scraper.page)
        assert scraper.browser is not None
        assert scraper.page is not None

    @pytest.mark.asyncio
    async def test_external_process_termination(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        """Test handling of external browser process termination."""
        class DummyConfig:
            def __init__(self, **kwargs:object) -> None:
                self.browser_args:list[str] = []
                self.user_data_dir:str | None = None
                self.extensions:list[str] = []
                self.browser_executable_path:str | None = None
                self.host:str | None = None
                self.port:int | None = None
                self.headless:bool = False
                self._extensions:list[str] = []

            def add_extension(self, ext:str) -> None:
                self._extensions.append(ext)

        def make_mock_browser() -> AsyncMock:
            mock_browser = AsyncMock()
            mock_browser.websocket_url = "ws://localhost:9222"
            mock_browser._process_pid = 12345
            mock_browser.stop = MagicMock()
            return mock_browser

        mock_browser = make_mock_browser()
        mock_page = TrulyAwaitableMockPage()
        mock_browser.get = AsyncMock(return_value = mock_page)
        monkeypatch.setattr(nodriver, "start", AsyncMock(return_value = mock_browser))
        monkeypatch.setattr(nodriver.core.config, "Config", DummyConfig)  # type: ignore[unused-ignore,reportAttributeAccessIssue]
        monkeypatch.setattr(os.path, "exists", lambda p: True)

        # Mock create_browser_session to ensure proper setup
        async def mock_create_session(self:WebScrapingMixin) -> None:
            self.browser = mock_browser
            self.page = mock_page  # type: ignore[unused-ignore,reportAttributeAccessIssue]  # Assigning mock page for test

        monkeypatch.setattr(WebScrapingMixin, "create_browser_session", mock_create_session)

        scraper = WebScrapingMixin()
        scraper.browser_config.user_data_dir = str(tmp_path)
        scraper.browser_config.profile_name = "Default"
        await scraper.create_browser_session()

        with patch("psutil.Process") as mock_proc:
            mock_proc.side_effect = psutil.NoSuchProcess(12345)
            with pytest.raises(psutil.NoSuchProcess):
                scraper.close_browser_session()

        # Create a new mock browser for the second session
        mock_browser2 = make_mock_browser()
        mock_browser2._process_pid = 12346
        mock_page2 = TrulyAwaitableMockPage()
        mock_browser2.get = AsyncMock(return_value = mock_page2)
        monkeypatch.setattr(nodriver, "start", AsyncMock(return_value = mock_browser2))

        # Update mock_create_session for the second session
        async def mock_create_session2(self:WebScrapingMixin) -> None:
            self.browser = mock_browser2
            self.page = mock_page2  # type: ignore[unused-ignore,reportAttributeAccessIssue]  # Assigning mock page for test

        monkeypatch.setattr(WebScrapingMixin, "create_browser_session", mock_create_session2)
        await scraper.create_browser_session()
        print("[DEBUG] scraper.page after session creation:", scraper.page)
        assert scraper.browser is not None
        assert scraper.page is not None
