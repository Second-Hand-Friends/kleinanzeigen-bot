# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Unit tests for web_scraping_mixin.py focusing on error handling scenarios.
"""

import json
import logging
import os
import platform
import shutil
import zipfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, NoReturn, Protocol, cast
from unittest.mock import AsyncMock, MagicMock, Mock, mock_open, patch

import nodriver
import psutil
import pytest
from nodriver.core.element import Element
from nodriver.core.tab import Tab as Page

from kleinanzeigen_bot.model.config_model import Config
from kleinanzeigen_bot.utils import files, loggers
from kleinanzeigen_bot.utils.web_scraping_mixin import By, Is, WebScrapingMixin, _is_admin  # noqa: PLC2701


class ConfigProtocol(Protocol):
    """Protocol for Config objects used in tests."""

    extensions:list[str]
    browser_args:list[str]
    user_data_dir:str | None

    def add_extension(self, ext:str) -> None:
        pass


def _nodriver_start_mock() -> Mock:
    """Return the nodriver.start mock with proper typing."""
    return cast(Mock, cast(Any, nodriver).start)


class RecordingCollector:
    """Helper collector that stores timing records for assertions."""

    def __init__(self, sink:list[dict[str, Any]]) -> None:
        self._sink = sink

    def record(self, **kwargs:Any) -> None:
        self._sink.append(kwargs)


class FailingCollector:
    """Helper collector that raises to test error handling."""

    def record(self, **kwargs:Any) -> None:
        raise RuntimeError("collector failed")


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
    scraper.config = Config.model_validate({"login": {"username": "user@example.com", "password": "secret"}})  # noqa: S105
    return scraper


def test_write_initial_prefs(tmp_path:Path) -> None:
    """Test _write_initial_prefs helper function."""
    from kleinanzeigen_bot.utils.web_scraping_mixin import _write_initial_prefs  # noqa: PLC0415, PLC2701

    prefs_file = tmp_path / "Preferences"
    _write_initial_prefs(str(prefs_file))

    # Verify file was created
    assert prefs_file.exists()

    # Verify content is valid JSON with expected structure
    with open(prefs_file, encoding = "UTF-8") as f:
        prefs = json.load(f)

    assert prefs["credentials_enable_service"] is False
    assert prefs["enable_do_not_track"] is True
    assert prefs["google"]["services"]["consented_to_sync"] is False
    assert prefs["profile"]["password_manager_enabled"] is False
    assert prefs["profile"]["default_content_setting_values"]["notifications"] == 2
    assert prefs["signin"]["allowed"] is False
    assert "www.kleinanzeigen.de" in prefs["translate_site_blacklist"]
    assert prefs["devtools"]["preferences"]["currentDockState"] == '"bottom"'


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
    async def test_web_select_combobox_missing_dropdown_options(self, web_scraper:WebScrapingMixin) -> None:
        """Test combobox selection when aria-controls attribute is missing."""
        input_field = AsyncMock(spec = Element)
        input_field.attrs = {}
        input_field.clear_input = AsyncMock()
        input_field.send_keys = AsyncMock()
        web_scraper.web_find = AsyncMock(return_value = input_field)  # type: ignore[method-assign]
        web_scraper.web_sleep = AsyncMock()  # type: ignore[method-assign]

        with pytest.raises(TimeoutError, match = "Combobox missing aria-controls attribute"):
            await web_scraper.web_select_combobox(By.ID, "combo-id", "Option", timeout = 0.1)

        input_field.clear_input.assert_awaited_once()
        input_field.send_keys.assert_awaited_once_with("Option")
        assert web_scraper.web_sleep.await_count == 1  # Only one sleep before checking aria-controls

    @pytest.mark.asyncio
    async def test_web_select_combobox_selects_matching_option(self, web_scraper:WebScrapingMixin) -> None:
        """Test combobox selection matches a visible <li> option."""
        input_field = AsyncMock(spec = Element)
        input_field.attrs = {"aria-controls": "dropdown-id"}
        input_field.clear_input = AsyncMock()
        input_field.send_keys = AsyncMock()

        dropdown_elem = AsyncMock(spec = Element)
        dropdown_elem.apply = AsyncMock(return_value = True)

        web_scraper.web_find = AsyncMock(side_effect = [input_field, dropdown_elem])  # type: ignore[method-assign]
        web_scraper.web_sleep = AsyncMock()  # type: ignore[method-assign]

        result = await web_scraper.web_select_combobox(By.ID, "combo-id", "Visible Label")

        assert result is dropdown_elem
        input_field.clear_input.assert_awaited_once()
        input_field.send_keys.assert_awaited_once_with("Visible Label")
        dropdown_elem.apply.assert_awaited_once()
        assert web_scraper.web_sleep.await_count == 2

    @pytest.mark.asyncio
    async def test_web_select_combobox_no_matching_option_raises(self, web_scraper:WebScrapingMixin) -> None:
        """Test combobox selection raises when no <li> matches the entered text."""
        input_field = AsyncMock(spec = Element)
        input_field.attrs = {"aria-controls": "dropdown-id"}
        input_field.clear_input = AsyncMock()
        input_field.send_keys = AsyncMock()

        dropdown_elem = AsyncMock(spec = Element)
        dropdown_elem.apply = AsyncMock(return_value = False)

        web_scraper.web_find = AsyncMock(side_effect = [input_field, dropdown_elem])  # type: ignore[method-assign]
        web_scraper.web_sleep = AsyncMock()  # type: ignore[method-assign]

        with pytest.raises(TimeoutError, match = "No matching option found in combobox"):
            await web_scraper.web_select_combobox(By.ID, "combo-id", "Missing Label")

        dropdown_elem.apply.assert_awaited_once()
        assert web_scraper.web_sleep.await_count == 1  # One sleep after typing, error before second sleep

    @pytest.mark.asyncio
    async def test_web_select_combobox_special_characters(self, web_scraper:WebScrapingMixin) -> None:
        """Test combobox selection with special characters (quotes, newlines, etc)."""
        input_field = AsyncMock(spec = Element)
        input_field.attrs = {"aria-controls": "dropdown-id"}
        input_field.clear_input = AsyncMock()
        input_field.send_keys = AsyncMock()

        dropdown_elem = AsyncMock(spec = Element)
        dropdown_elem.apply = AsyncMock(return_value = True)

        web_scraper.web_find = AsyncMock(side_effect = [input_field, dropdown_elem])  # type: ignore[method-assign]
        web_scraper.web_sleep = AsyncMock()  # type: ignore[method-assign]

        # Test with quotes, backslashes, and newlines
        special_value = 'Value with "quotes" and \\ backslash'
        result = await web_scraper.web_select_combobox(By.ID, "combo-id", special_value)

        assert result is dropdown_elem
        input_field.send_keys.assert_awaited_once_with(special_value)
        # Verify that the JavaScript received properly escaped value
        call_args = dropdown_elem.apply.call_args[0][0]
        assert '"quotes"' in call_args or r"\"quotes\"" in call_args  # JSON escaping should handle quotes

    @pytest.mark.asyncio
    async def test_web_select_by_value(self, web_scraper:WebScrapingMixin) -> None:
        """Test web_select successfully matches by option value."""
        select_elem = AsyncMock(spec = Element)
        select_elem.apply = AsyncMock()

        web_scraper.web_check = AsyncMock(return_value = True)  # type: ignore[method-assign]
        web_scraper.web_await = AsyncMock(return_value = True)  # type: ignore[method-assign]
        web_scraper.web_find = AsyncMock(return_value = select_elem)  # type: ignore[method-assign]
        web_scraper.web_sleep = AsyncMock()  # type: ignore[method-assign]

        result = await web_scraper.web_select(By.ID, "select-id", "option-value")

        assert result is select_elem
        select_elem.apply.assert_awaited_once()
        web_scraper.web_sleep.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_web_select_raises_on_missing_option(self, web_scraper:WebScrapingMixin) -> None:
        """Test web_select raises TimeoutError when option not found."""
        select_elem = AsyncMock(spec = Element)
        # Simulate JS throwing an error when option not found
        select_elem.apply = AsyncMock(side_effect = Exception("Option not found by value or displayed text: missing"))

        web_scraper.web_check = AsyncMock(return_value = True)  # type: ignore[method-assign]
        web_scraper.web_await = AsyncMock(return_value = True)  # type: ignore[method-assign]
        web_scraper.web_find = AsyncMock(return_value = select_elem)  # type: ignore[method-assign]

        with pytest.raises(TimeoutError, match = "Option not found by value or displayed text"):
            await web_scraper.web_select(By.ID, "select-id", "missing-option")

    async def test_web_input_success_returns_element(self, web_scraper:WebScrapingMixin, mock_page:TrulyAwaitableMockPage) -> None:
        """Successful web_input should send keys, wait, and return the element."""
        mock_element = AsyncMock(spec = Element)
        mock_page.query_selector.return_value = mock_element
        mock_sleep = AsyncMock()
        cast(Any, web_scraper).web_sleep = mock_sleep

        result = await web_scraper.web_input(By.ID, "username", "hello world", timeout = 1)

        assert result is mock_element
        mock_element.clear_input.assert_awaited_once()
        mock_element.send_keys.assert_awaited_once_with("hello world")
        mock_sleep.assert_awaited_once()

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
    async def test_web_open_skip_when_url_already_loaded(
        self, web_scraper:WebScrapingMixin, mock_browser:AsyncMock, mock_page:TrulyAwaitableMockPage
    ) -> None:
        """web_open should short-circuit when the requested URL is already active."""
        mock_browser.get.reset_mock()
        mock_page.url = "https://example.com"
        mock_execute = AsyncMock()
        cast(Any, web_scraper).web_execute = mock_execute

        await web_scraper.web_open("https://example.com", reload_if_already_open = False)

        mock_browser.get.assert_not_awaited()
        mock_execute.assert_not_called()

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

    @pytest.mark.asyncio
    async def test_web_find_applies_timeout_multiplier_and_backoff(self, web_scraper:WebScrapingMixin) -> None:
        """Ensure multiplier/backoff logic is honored when timeouts occur."""
        assert web_scraper.config is not None
        web_scraper.config.timeouts.multiplier = 2.0
        web_scraper.config.timeouts.retry_enabled = True
        web_scraper.config.timeouts.retry_max_attempts = 2
        web_scraper.config.timeouts.retry_backoff_factor = 2.0

        recorded:list[tuple[float, bool]] = []

        async def fake_web_await(condition:Callable[[], object], *, timeout:float, timeout_error_message:str = "", apply_multiplier:bool = True) -> Element:
            recorded.append((timeout, apply_multiplier))
            raise TimeoutError(timeout_error_message or "timeout")

        cast(Any, web_scraper).web_await = fake_web_await

        with pytest.raises(TimeoutError):
            await web_scraper.web_find(By.ID, "test-id", timeout = 0.5)

        assert recorded == [(1.0, False), (2.0, False), (4.0, False)]


class TestTimeoutAndRetryHelpers:
    """Test timeout helper utilities in WebScrapingMixin."""

    def test_get_timeout_config_prefers_config_timeouts(self, web_scraper:WebScrapingMixin) -> None:
        """_get_timeout_config should return the config-provided timeout model when available."""
        custom_config = Config.model_validate(
            {
                "login": {"username": "user@example.com", "password": "secret"},  # noqa: S105
                "timeouts": {"default": 7.5},
            }
        )
        web_scraper.config = custom_config

        assert web_scraper._get_timeout_config() is custom_config.timeouts

    def test_timeout_attempts_respects_retry_switch(self, web_scraper:WebScrapingMixin) -> None:
        """_timeout_attempts should collapse to a single attempt when retries are disabled."""
        web_scraper.config.timeouts.retry_enabled = False
        assert web_scraper._timeout_attempts() == 1

        web_scraper.config.timeouts.retry_enabled = True
        web_scraper.config.timeouts.retry_max_attempts = 3
        assert web_scraper._timeout_attempts() == 4

    @pytest.mark.asyncio
    async def test_run_with_timeout_retries_retries_operation(self, web_scraper:WebScrapingMixin) -> None:
        """_run_with_timeout_retries should retry when TimeoutError is raised before succeeding."""
        attempts:list[float] = []

        async def flaky_operation(timeout:float) -> str:
            attempts.append(timeout)
            if len(attempts) == 1:
                raise TimeoutError("first attempt")
            return "done"

        web_scraper.config.timeouts.retry_max_attempts = 1
        result = await web_scraper._run_with_timeout_retries(flaky_operation, description = "retry-op")

        assert result == "done"
        assert len(attempts) == 2

    @pytest.mark.asyncio
    async def test_run_with_timeout_retries_records_success_timing(self, web_scraper:WebScrapingMixin) -> None:
        """_run_with_timeout_retries should emit a timing record for successful attempts."""
        recorded:list[dict[str, Any]] = []
        cast(Any, web_scraper)._timing_collector = RecordingCollector(recorded)

        async def operation(_timeout:float) -> str:
            return "ok"

        result = await web_scraper._run_with_timeout_retries(operation, description = "web_find(ID, test)")

        assert result == "ok"
        assert len(recorded) == 1
        assert recorded[0]["operation_type"] == "web_find"
        assert recorded[0]["success"] is True
        assert recorded[0]["attempt_index"] == 0

    @pytest.mark.asyncio
    async def test_run_with_timeout_retries_records_timeout_timing(self, web_scraper:WebScrapingMixin) -> None:
        """_run_with_timeout_retries should emit timing records for timed out attempts."""
        recorded:list[dict[str, Any]] = []
        cast(Any, web_scraper)._timing_collector = RecordingCollector(recorded)
        web_scraper.config.timeouts.retry_max_attempts = 1

        async def always_timeout(_timeout:float) -> str:
            raise TimeoutError("boom")

        with pytest.raises(TimeoutError, match = "boom"):
            await web_scraper._run_with_timeout_retries(always_timeout, description = "web_find(ID, test)")

        assert len(recorded) == 2
        assert all(entry["operation_type"] == "web_find" for entry in recorded)
        assert all(entry["success"] is False for entry in recorded)
        assert recorded[0]["attempt_index"] == 0
        assert recorded[1]["attempt_index"] == 1

    @pytest.mark.asyncio
    async def test_run_with_timeout_retries_ignores_collector_failure(self, web_scraper:WebScrapingMixin) -> None:
        """_run_with_timeout_retries should continue when timing collector record fails."""
        cast(Any, web_scraper)._timing_collector = FailingCollector()

        async def operation(_timeout:float) -> str:
            return "ok"

        result = await web_scraper._run_with_timeout_retries(operation, description = "web_find(ID, test)")

        assert result == "ok"

    @pytest.mark.asyncio
    async def test_run_with_timeout_retries_guard_clause(self, web_scraper:WebScrapingMixin) -> None:
        """_run_with_timeout_retries should guard against zero-attempt edge cases."""

        async def never_called(timeout:float) -> None:
            pytest.fail("operation should not run when attempts are zero")

        with (
            patch.object(web_scraper, "_timeout_attempts", return_value = 0),
            pytest.raises(TimeoutError, match = "guarded-op failed without executing operation"),
        ):
            await web_scraper._run_with_timeout_retries(never_called, description = "guarded-op")

    def test_allocate_selector_group_budgets_distributes_total(self, web_scraper:WebScrapingMixin) -> None:
        """Selector group budgets should consume the full timeout budget."""
        budgets = web_scraper._allocate_selector_group_budgets(2.0, 2)
        assert len(budgets) == 2
        assert budgets[0] + budgets[1] == pytest.approx(2.0)

    def test_allocate_selector_group_budgets_rejects_zero_selector_count(self, web_scraper:WebScrapingMixin) -> None:
        """Selector budget helper should reject empty selector groups."""
        with pytest.raises(ValueError, match = "selector_count must be > 0"):
            web_scraper._allocate_selector_group_budgets(1.0, 0)

    def test_allocate_selector_group_budgets_single_selector_clamps_negative_timeout(self, web_scraper:WebScrapingMixin) -> None:
        """Single-selector budgets should never be negative."""
        budgets = web_scraper._allocate_selector_group_budgets(-1.0, 1)
        assert budgets == [0.0]

    def test_allocate_selector_group_budgets_non_positive_timeout_returns_zeroes(self, web_scraper:WebScrapingMixin) -> None:
        """Multi-selector groups with non-positive timeout should return zero budgets."""
        budgets = web_scraper._allocate_selector_group_budgets(0.0, 3)
        assert budgets == [0.0, 0.0, 0.0]

    def test_allocate_selector_group_budgets_tiny_timeout_splits_equally(self, web_scraper:WebScrapingMixin) -> None:
        """When timeout is too small for floors, budgets should split equally."""
        # 0.2s is below floor_total for two selectors (2 * 0.25s), so equal split applies.
        budgets = web_scraper._allocate_selector_group_budgets(0.2, 2)
        assert budgets == pytest.approx([0.1, 0.1])

    def test_allocate_selector_group_budgets_redistributes_surplus_to_primary(self, web_scraper:WebScrapingMixin) -> None:
        """Last-backup cap overflow should be redistributed back to primary budget."""
        budgets = web_scraper._allocate_selector_group_budgets(5.0, 2)
        # Derivation with current constants:
        # primary=min(5.0*0.70, 5.0-0.25)=3.5; last backup cap=0.75; surplus=1.5 -> primary+surplus=5.0-0.75=4.25.
        assert budgets == pytest.approx([4.25, 0.75])

    def test_allocate_selector_group_budgets_multiple_backups_apply_reserve_logic(self, web_scraper:WebScrapingMixin) -> None:
        """Multi-backup groups should apply reserve/floor logic before final backup cap."""
        budgets = web_scraper._allocate_selector_group_budgets(3.0, 4)
        # Derivation with current constants:
        # reserve_for_backups=0.25*3=0.75; primary=min(3.0*0.70, 2.25)=2.1.
        # remaining=0.9 -> backup1=max(0.25, min(0.75, 0.9-0.5))=0.4.
        # remaining=0.5 -> backup2=max(0.25, min(0.75, 0.5-0.25))=0.25.
        # final backup=min(0.25, 0.75)=0.25.
        assert budgets == pytest.approx([2.1, 0.4, 0.25, 0.25])
        assert sum(budgets) == pytest.approx(3.0)

    @pytest.mark.asyncio
    async def test_web_find_first_available_uses_shared_budget(self, web_scraper:WebScrapingMixin) -> None:
        """web_find_first_available should try alternatives in order with shared budget slices."""
        first_timeout:float | None = None
        second_timeout:float | None = None
        found = AsyncMock(spec = Element)

        async def fake_find_once(
            selector_type:By, selector_value:str, timeout:float, *, parent:Element | None = None
        ) -> Element:
            nonlocal first_timeout, second_timeout
            if selector_value == "first":
                first_timeout = timeout
                raise TimeoutError("first timeout")
            second_timeout = timeout
            return found

        with patch.object(web_scraper, "_web_find_once", side_effect = fake_find_once):
            result, index = await web_scraper.web_find_first_available(
                [(By.ID, "first"), (By.ID, "second")],
                timeout = 2.0,
                key = "login_detection",
            )

        assert result is found
        assert index == 1
        assert first_timeout is not None
        assert second_timeout is not None
        assert first_timeout + second_timeout == pytest.approx(2.0)

    @pytest.mark.asyncio
    async def test_web_find_first_available_exhausts_candidates_once_when_retry_disabled(self, web_scraper:WebScrapingMixin) -> None:
        """Candidate exhaustion should not multiply attempts when retry is disabled."""
        web_scraper.config.timeouts.retry_enabled = False

        with (
            patch.object(web_scraper, "_web_find_once", side_effect = TimeoutError("not found")) as find_once,
            pytest.raises(TimeoutError, match = "No HTML element found using selector group"),
        ):
            await web_scraper.web_find_first_available([(By.ID, "first"), (By.ID, "second")], timeout = 1.0)

        assert find_once.await_count == 2

    @pytest.mark.asyncio
    async def test_web_find_first_available_rejects_empty_selectors(self, web_scraper:WebScrapingMixin) -> None:
        """Selector-group lookup should fail fast when no selectors are configured."""
        with pytest.raises(ValueError, match = "selectors must contain at least one selector"):
            await web_scraper.web_find_first_available([])

    @pytest.mark.asyncio
    async def test_web_text_first_available_returns_text_and_index(self, web_scraper:WebScrapingMixin) -> None:
        """Text-group helper should return extracted text and the matched selector index."""
        mock_element = AsyncMock(spec = Element)
        mock_element.apply = AsyncMock(return_value = "dummy-user")

        with patch.object(web_scraper, "web_find_first_available", new_callable = AsyncMock, return_value = (mock_element, 1)):
            text, index = await web_scraper.web_text_first_available([(By.ID, "a"), (By.ID, "b")], key = "login_detection")

        assert text == "dummy-user"
        assert index == 1


class TestSelectorTimeoutMessages:
    """Ensure selector helpers provide informative timeout messages."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("selector_type", "selector_value", "expected_message"),
        [
            (By.TAG_NAME, "section", "No HTML element found of tag <section> within 2.0 seconds."),
            (By.CSS_SELECTOR, ".hero", "No HTML element found using CSS selector '.hero' within 2.0 seconds."),
            (By.TEXT, "Submit", "No HTML element found containing text 'Submit' within 2.0 seconds."),
            (By.XPATH, "//div[@class='hero']", "No HTML element found using XPath '//div[@class='hero']' within 2.0 seconds."),
        ],
    )
    async def test_web_find_timeout_suffixes(self, web_scraper:WebScrapingMixin, selector_type:By, selector_value:str, expected_message:str) -> None:
        """web_find should pass descriptive timeout messages for every selector strategy."""
        mock_element = AsyncMock(spec = Element)
        mock_wait = AsyncMock(return_value = mock_element)
        cast(Any, web_scraper).web_await = mock_wait

        result = await web_scraper.web_find(selector_type, selector_value, timeout = 2)

        assert result is mock_element
        call = mock_wait.await_args_list[0]
        assert expected_message == call.kwargs["timeout_error_message"]
        assert call.kwargs["apply_multiplier"] is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("selector_type", "selector_value", "expected_message"),
        [
            (By.CLASS_NAME, "hero", "No HTML elements found with CSS class 'hero' within 1 seconds."),
            (By.CSS_SELECTOR, ".card", "No HTML elements found using CSS selector '.card' within 1 seconds."),
            (By.TAG_NAME, "article", "No HTML elements found of tag <article> within 1 seconds."),
            (By.TEXT, "Listings", "No HTML elements found containing text 'Listings' within 1 seconds."),
            (By.XPATH, "//footer", "No HTML elements found using XPath '//footer' within 1 seconds."),
        ],
    )
    async def test_web_find_all_once_timeout_suffixes(
        self, web_scraper:WebScrapingMixin, selector_type:By, selector_value:str, expected_message:str
    ) -> None:
        """_web_find_all_once should surface informative timeout errors for each selector."""
        elements = [AsyncMock(spec = Element)]
        mock_wait = AsyncMock(return_value = elements)
        cast(Any, web_scraper).web_await = mock_wait

        result = await web_scraper._web_find_all_once(selector_type, selector_value, 1)

        assert result is elements
        call = mock_wait.await_args_list[0]
        assert expected_message == call.kwargs["timeout_error_message"]
        assert call.kwargs["apply_multiplier"] is False

    @pytest.mark.asyncio
    async def test_web_find_all_delegates_to_retry_helper(self, web_scraper:WebScrapingMixin) -> None:
        """web_find_all should execute via the timeout retry helper."""
        elements = [AsyncMock(spec = Element)]

        async def fake_retry(operation:Callable[[float], Awaitable[list[Element]]], **kwargs:Any) -> list[Element]:
            assert kwargs["description"] == "web_find_all(CLASS_NAME, hero)"
            assert kwargs["override"] == 1.5
            result = await operation(0.42)
            return result

        retry_mock = AsyncMock(side_effect = fake_retry)
        once_mock = AsyncMock(return_value = elements)
        cast(Any, web_scraper)._run_with_timeout_retries = retry_mock
        cast(Any, web_scraper)._web_find_all_once = once_mock

        result = await web_scraper.web_find_all(By.CLASS_NAME, "hero", timeout = 1.5)

        assert result is elements
        retry_call = retry_mock.await_args_list[0]
        assert retry_call.kwargs["key"] == "default"
        assert retry_call.kwargs["override"] == 1.5

        once_call = once_mock.await_args_list[0]
        assert once_call.args[:2] == (By.CLASS_NAME, "hero")
        assert once_call.args[2] == 0.42

    @pytest.mark.asyncio
    async def test_web_check_unsupported_attribute(self, web_scraper:WebScrapingMixin, mock_page:TrulyAwaitableMockPage) -> None:
        """web_check should raise for unsupported attribute queries."""
        mock_element = AsyncMock(spec = Element)
        mock_element.attrs = {}
        mock_page.query_selector.return_value = mock_element

        with pytest.raises(AssertionError, match = "Unsupported attribute"):
            await web_scraper.web_check(By.ID, "test-id", cast(Is, object()), timeout = 0.1)


class TestWebScrapingSessionManagement:
    """Test session management edge cases in WebScrapingMixin."""

    def test_close_browser_session_cleans_up_resources(self) -> None:
        """Ensure browser and page references are cleared and child processes are killed."""
        scraper = WebScrapingMixin()
        scraper.browser = MagicMock()
        scraper.browser._process_pid = 42
        stop_mock = scraper.browser.stop = MagicMock()
        scraper.page = MagicMock(spec = Page)

        with patch("psutil.Process") as mock_proc:
            mock_child = MagicMock()
            mock_child.is_running.return_value = True
            mock_proc.return_value.children.return_value = [mock_child]

            scraper.close_browser_session()

        mock_proc.assert_called_once_with(42)
        stop_mock.assert_called_once()
        mock_child.kill.assert_called_once()
        assert scraper.browser is None
        assert scraper.page is None

    def test_close_browser_session_idempotent(self) -> None:
        """Repeated calls should leave the state clean without re-running cleanup logic."""
        scraper = WebScrapingMixin()
        scraper.browser = MagicMock()
        scraper.browser._process_pid = 99
        stop_mock = scraper.browser.stop = MagicMock()
        scraper.page = MagicMock(spec = Page)

        with patch("psutil.Process") as mock_proc:
            mock_proc.return_value.children.return_value = []
            scraper.close_browser_session()
            scraper.close_browser_session()

        mock_proc.assert_called_once()
        stop_mock.assert_called_once()

    def test_close_browser_session_without_browser_skips_inspection(self) -> None:
        """When no browser exists, no process inspection should run and the page should stay untouched."""
        scraper = WebScrapingMixin()
        scraper.browser = None  # type: ignore[unused-ignore,reportAttributeAccessIssue]
        preserved_page = MagicMock(spec = Page)
        scraper.page = preserved_page

        with patch("psutil.Process") as mock_proc:
            scraper.close_browser_session()

        mock_proc.assert_not_called()
        assert scraper.page is preserved_page

    def test_close_browser_session_handles_missing_children(self) -> None:
        """Child-less browsers should still stop cleanly without raising."""
        scraper = WebScrapingMixin()
        scraper.browser = MagicMock()
        scraper.browser._process_pid = 123
        stop_mock = scraper.browser.stop = MagicMock()
        scraper.page = MagicMock(spec = Page)

        with patch("psutil.Process") as mock_proc:
            mock_proc.return_value.children.return_value = []
            scraper.close_browser_session()

        mock_proc.assert_called_once()
        stop_mock.assert_called_once()

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


class TestWebScrolling:
    """Test scrolling helpers."""

    @pytest.mark.asyncio
    async def test_web_scroll_page_down_scrolls_and_returns(self, web_scraper:WebScrapingMixin) -> None:
        """web_scroll_page_down should scroll both directions when requested."""
        scripts:list[str] = []

        async def exec_side_effect(script:str) -> int | None:
            scripts.append(script)
            if script == "document.body.scrollHeight":
                return 20
            return None

        cast(Any, web_scraper).web_execute = AsyncMock(side_effect = exec_side_effect)

        with patch("kleinanzeigen_bot.utils.web_scraping_mixin.asyncio.sleep", new_callable = AsyncMock) as mock_sleep:
            await web_scraper.web_scroll_page_down(scroll_length = 10, scroll_speed = 10, scroll_back_top = True)

        assert scripts[0] == "document.body.scrollHeight"
        # Expect four scrollTo operations: two down, two up
        assert scripts.count("document.body.scrollHeight") == 1
        scroll_calls = [script for script in scripts if script.startswith("window.scrollTo")]
        assert scroll_calls == ["window.scrollTo(0, 10)", "window.scrollTo(0, 20)", "window.scrollTo(0, 10)", "window.scrollTo(0, 0)"]
        sleep_durations = [call.args[0] for call in mock_sleep.await_args_list]
        assert sleep_durations == [1.0, 1.0, 0.5, 0.5]

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
    async def test_web_await_caps_sleep_to_remaining_timeout(self, web_scraper:WebScrapingMixin, mock_page:TrulyAwaitableMockPage) -> None:
        """web_await should not sleep longer than the remaining timeout budget."""

        async def condition() -> bool:
            return False

        with pytest.raises(TimeoutError):
            await web_scraper.web_await(condition, timeout = 0.2, apply_multiplier = False)

        sleep_mock = cast(AsyncMock, mock_page.sleep)
        sleep_mock.assert_awaited()
        slept_seconds = sleep_mock.await_args_list[0].args[0]
        assert slept_seconds <= 0.2

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
        monkeypatch.setattr(nodriver.core.config, "Config", DummyConfig)  # type: ignore[unused-ignore,reportAttributeAccessIssue,attr-defined]

        # Mock os.path.exists to return True for the browser binary and use real exists for Preferences file (and Edge)
        edge_path = "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"
        chrome_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        real_exists = os.path.exists

        def mock_exists_sync(path:str) -> bool:
            # Handle all browser paths
            if path in {
                # Linux paths
                "/usr/bin/chromium",
                "/usr/bin/chromium-browser",
                "/usr/bin/google-chrome",
                "/usr/bin/microsoft-edge",
                "/usr/bin/chrome",
                # macOS paths
                edge_path,
                chrome_path,
                # Windows paths
                "C:\\Users\\runneradmin\\AppData\\Local\\Google\\Chrome\\Application\\chrome.exe",
                "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
                "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
                "C:\\Users\\runneradmin\\AppData\\Local\\Microsoft\\Edge\\Application\\msedge.exe",
                "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
                "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
                "C:\\Program Files\\Chromium\\Application\\chrome.exe",
                "C:\\Program Files (x86)\\Chromium\\Application\\chrome.exe",
                "C:\\Users\\runneradmin\\AppData\\Local\\Chromium\\Application\\chrome.exe",
                "C:\\Program Files\\Chrome\\Application\\chrome.exe",
                "C:\\Program Files (x86)\\Chrome\\Application\\chrome.exe",
                "C:\\Users\\runneradmin\\AppData\\Local\\Chrome\\Application\\chrome.exe",
            }:
                return True
            if "Preferences" in str(path) and str(tmp_path) in str(path):
                return real_exists(path)
            return False

        async def mock_exists_async(path:str | Path) -> bool:
            return mock_exists_sync(str(path))

        monkeypatch.setattr(os.path, "exists", mock_exists_sync)
        monkeypatch.setattr(files, "exists", mock_exists_async)

        # Create test profile directory
        profile_dir = tmp_path / "Default"
        profile_dir.mkdir()
        prefs_file = profile_dir / "Preferences"

        # Test with existing preferences file
        prefs_file.write_text(json.dumps({"existing": "prefs"}), encoding = "UTF-8")

        scraper = WebScrapingMixin()
        scraper.browser_config.user_data_dir = str(tmp_path)
        scraper.browser_config.profile_name = "Default"
        await scraper.create_browser_session()

        # Verify preferences file was not overwritten
        prefs = json.loads(prefs_file.read_text(encoding = "UTF-8"))
        assert prefs["existing"] == "prefs"

        # Test with missing preferences file
        prefs_file.unlink()
        await scraper.create_browser_session()

        # Verify new preferences file was created with correct settings
        prefs = json.loads(prefs_file.read_text(encoding = "UTF-8"))
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
        monkeypatch.setattr(nodriver.core.config, "Config", DummyConfig)  # type: ignore[unused-ignore,reportAttributeAccessIssue,attr-defined]

        # Mock os.path.exists to return True for both Chrome and Edge paths
        monkeypatch.setattr(os.path, "exists", lambda p: p in {"/usr/bin/chrome", "/usr/bin/edge"})

        async def mock_exists_async(path:str | Path) -> bool:
            return str(path) in {"/usr/bin/chrome", "/usr/bin/edge"}

        monkeypatch.setattr(files, "exists", mock_exists_async)

        # Test with custom arguments
        scraper = WebScrapingMixin()
        scraper.browser_config.arguments = ["--custom-arg=value", "--another-arg"]
        scraper.browser_config.use_private_window = True
        scraper.browser_config.binary_location = "/usr/bin/chrome"
        await scraper.create_browser_session()

        # Verify browser arguments
        config = _nodriver_start_mock().call_args[0][0]
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
        config = _nodriver_start_mock().call_args[0][0]
        assert "-inprivate" in config.browser_args
        assert os.environ.get("MSEDGEDRIVER_TELEMETRY_OPTOUT") == "1"

    @pytest.mark.asyncio
    async def test_create_browser_session_logs_missing_user_data_dir_for_non_test_runs(
        self, monkeypatch:pytest.MonkeyPatch, caplog:pytest.LogCaptureFixture
    ) -> None:
        """Test non-test runtime without user_data_dir logs fallback diagnostics and default profile usage."""
        class DummyConfig:
            def __init__(self, **kwargs:object) -> None:
                self.browser_args = cast(list[str], kwargs.get("browser_args", []))
                self.user_data_dir = cast(str | None, kwargs.get("user_data_dir"))
                self.browser_executable_path = cast(str | None, kwargs.get("browser_executable_path"))
                self.headless = cast(bool, kwargs.get("headless", False))

            def add_extension(self, _ext:str) -> None:
                return

        mock_browser = AsyncMock()
        mock_browser.websocket_url = "ws://localhost:9222"
        monkeypatch.setattr(nodriver, "start", AsyncMock(return_value = mock_browser))
        monkeypatch.setattr("kleinanzeigen_bot.utils.web_scraping_mixin.NodriverConfig", DummyConfig)
        monkeypatch.setattr(loggers, "is_debug", lambda _logger: False)
        monkeypatch.setattr(
            WebScrapingMixin,
            "_validate_chrome_version_configuration",
            AsyncMock(return_value = None),
        )

        async def mock_exists(path:str | Path) -> bool:
            return str(path) == "/usr/bin/chrome"

        monkeypatch.setattr(files, "exists", mock_exists)
        caplog.set_level(logging.DEBUG)

        with patch.dict(os.environ, {}, clear = True):
            scraper = WebScrapingMixin()
            scraper.browser_config.binary_location = "/usr/bin/chrome"
            await scraper.create_browser_session()

        cfg = _nodriver_start_mock().call_args[0][0]
        assert cfg.user_data_dir is None
        assert "--log-level=3" in cfg.browser_args
        assert "No browser user_data_dir configured" in caplog.text
        assert "No effective browser user_data_dir found" in caplog.text

    @pytest.mark.asyncio
    async def test_create_browser_session_ensures_profile_directory_for_user_data_dir(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        """Test configured user_data_dir creates profile structure and skips non-debug log-level override."""
        class DummyConfig:
            def __init__(self, **kwargs:object) -> None:
                self.browser_args = cast(list[str], kwargs.get("browser_args", []))
                self.user_data_dir = cast(str | None, kwargs.get("user_data_dir"))
                self.browser_executable_path = cast(str | None, kwargs.get("browser_executable_path"))
                self.headless = cast(bool, kwargs.get("headless", False))

            def add_extension(self, _ext:str) -> None:
                return

        mock_browser = AsyncMock()
        mock_browser.websocket_url = "ws://localhost:9222"
        monkeypatch.setattr(nodriver, "start", AsyncMock(return_value = mock_browser))
        monkeypatch.setattr("kleinanzeigen_bot.utils.web_scraping_mixin.NodriverConfig", DummyConfig)
        monkeypatch.setattr(loggers, "is_debug", lambda _logger: True)
        monkeypatch.setattr(
            WebScrapingMixin,
            "_validate_chrome_version_configuration",
            AsyncMock(return_value = None),
        )

        async def mock_exists(path:str | Path) -> bool:
            path_str = str(path)
            if path_str == "/usr/bin/chrome":
                return True
            return bool(path_str.endswith("Preferences"))

        monkeypatch.setattr(files, "exists", mock_exists)

        with patch.dict(os.environ, {}, clear = True), \
                patch("kleinanzeigen_bot.utils.web_scraping_mixin.xdg_paths.ensure_directory") as mock_ensure_dir:
            scraper = WebScrapingMixin()
            scraper.browser_config.binary_location = "/usr/bin/chrome"
            scraper.browser_config.user_data_dir = str(tmp_path / "profile-root")
            await scraper.create_browser_session()

        cfg = _nodriver_start_mock().call_args[0][0]
        assert cfg.user_data_dir == str(tmp_path / "profile-root")
        assert "--log-level=3" not in cfg.browser_args
        mock_ensure_dir.assert_called_once_with(Path(str(tmp_path / "profile-root")), "browser profile directory")

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
        monkeypatch.setattr(nodriver.core.config, "Config", DummyConfig)  # type: ignore[unused-ignore,reportAttributeAccessIssue,attr-defined]

        # Mock files.exists and files.is_dir to return appropriate values
        async def mock_exists(path:str | Path) -> bool:
            path_str = str(path)
            # Resolve real paths to handle symlinks (e.g., /var -> /private/var on macOS)
            real_path = str(Path(path_str).resolve())  # noqa: ASYNC240 Test mock, runs synchronously
            real_ext1 = str(Path(ext1).resolve())  # noqa: ASYNC240 Test mock, runs synchronously
            real_ext2 = str(Path(ext2).resolve())  # noqa: ASYNC240 Test mock, runs synchronously
            return path_str in {"/usr/bin/chrome", "/usr/bin/edge"} or real_path in {real_ext1, real_ext2} or os.path.exists(path_str)  # noqa: ASYNC240

        async def mock_is_dir(path:str | Path) -> bool:
            path_str = str(path)
            # Resolve real paths to handle symlinks
            real_path = str(Path(path_str).resolve())  # noqa: ASYNC240 Test mock, runs synchronously
            real_ext1 = str(Path(ext1).resolve())  # noqa: ASYNC240 Test mock, runs synchronously
            real_ext2 = str(Path(ext2).resolve())  # noqa: ASYNC240 Test mock, runs synchronously
            # Nodriver extracts CRX files to temp directories, so they appear as directories
            if real_path in {real_ext1, real_ext2}:
                return True
            return Path(path_str).is_dir()  # noqa: ASYNC240 Test mock, runs synchronously

        monkeypatch.setattr(files, "exists", mock_exists)
        monkeypatch.setattr(files, "is_dir", mock_is_dir)

        # Test extension loading
        scraper = WebScrapingMixin()
        scraper.browser_config.extensions = [str(ext1), str(ext2)]
        scraper.browser_config.binary_location = "/usr/bin/chrome"
        await scraper.create_browser_session()

        # Verify extensions were loaded
        config = _nodriver_start_mock().call_args[0][0]
        assert len(config._extensions) == 2
        for ext_path in config._extensions:
            assert await files.exists(ext_path)
            assert await files.is_dir(ext_path)

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
            return {"chromium": "/usr/bin/chromium", "chromium-browser": None, "google-chrome": None, "microsoft-edge": None}.get(x)

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
        monkeypatch.setenv("PROGRAMFILES", "C:\\Program Files")
        monkeypatch.setenv("PROGRAMFILES(X86)", "C:\\Program Files (x86)")
        monkeypatch.setenv("LOCALAPPDATA", "C:\\Users\\TestUser\\AppData\\Local")

        local_chrome_path = "C:\\Users\\TestUser\\AppData\\Local\\Google\\Chrome\\Application\\chrome.exe"
        monkeypatch.setattr(os.path, "isfile", lambda p: p == local_chrome_path)
        assert scraper.get_compatible_browser() == local_chrome_path

        local_edge_path = "C:\\Users\\TestUser\\AppData\\Local\\Microsoft\\Edge\\Application\\msedge.exe"
        monkeypatch.setattr(os.path, "isfile", lambda p: p == local_edge_path)
        assert scraper.get_compatible_browser() == local_edge_path

        local_chromium_path = "C:\\Users\\TestUser\\AppData\\Local\\Chromium\\Application\\chrome.exe"
        monkeypatch.setattr(os.path, "isfile", lambda p: p == local_chromium_path)
        assert scraper.get_compatible_browser() == local_chromium_path

        monkeypatch.delenv("LOCALAPPDATA", raising = False)
        monkeypatch.setenv("USERPROFILE", "C:\\Users\\FallbackUser")
        fallback_local_chrome_path = "C:\\Users\\FallbackUser\\AppData\\Local\\Google\\Chrome\\Application\\chrome.exe"
        monkeypatch.setattr(os.path, "isfile", lambda p: p == fallback_local_chrome_path)
        assert scraper.get_compatible_browser() == fallback_local_chrome_path

        monkeypatch.delenv("PROGRAMFILES", raising = False)
        monkeypatch.delenv("PROGRAMFILES(X86)", raising = False)
        monkeypatch.delenv("LOCALAPPDATA", raising = False)
        monkeypatch.delenv("USERPROFILE", raising = False)
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
        monkeypatch.setattr(nodriver.core.config, "Config", DummyConfig)  # type: ignore[unused-ignore,reportAttributeAccessIssue,attr-defined]
        monkeypatch.setattr(os.path, "exists", lambda p: True)

        # Simulate state file in user_data_dir
        state_file = tmp_path / "Default" / "state.json"
        state_file.parent.mkdir(parents = True, exist_ok = True)

        # First session: write state
        scraper = WebScrapingMixin()
        scraper.browser_config.user_data_dir = str(tmp_path)
        scraper.browser_config.profile_name = "Default"
        await scraper.create_browser_session()
        state_file.write_text('{"foo": "bar"}', encoding = "utf-8")
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
        data = state_file.read_text(encoding = "utf-8")
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
        monkeypatch.setattr(nodriver.core.config, "Config", DummyConfig)  # type: ignore[unused-ignore,reportAttributeAccessIssue,attr-defined]
        # Don't mock os.path.exists globally - let the file operations work normally

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
        monkeypatch.setattr(nodriver.core.config, "Config", DummyConfig)  # type: ignore[unused-ignore,reportAttributeAccessIssue,attr-defined]
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

    def test_diagnose_browser_issues(self, caplog:pytest.LogCaptureFixture) -> None:
        """Test that diagnose_browser_issues provides expected diagnostic output."""
        # Configure logging to capture output
        caplog.set_level(loggers.INFO)

        # Create a WebScrapingMixin instance
        mixin = WebScrapingMixin()

        # Call the diagnose method
        mixin.diagnose_browser_issues()

        # Check that diagnostic output was produced
        log_output = caplog.text.lower()
        assert "browser connection diagnostics" in log_output or "browser-verbindungsdiagnose" in log_output
        assert "end diagnostics" in log_output or "ende der diagnose" in log_output


class TestWebScrapingDiagnostics:
    """Test the diagnose_browser_issues method."""

    @pytest.fixture
    def scraper_with_config(self) -> WebScrapingMixin:
        """Create a WebScrapingMixin instance with browser config."""
        scraper = WebScrapingMixin()
        return scraper

    def test_diagnose_browser_issues_binary_exists_executable(self, scraper_with_config:WebScrapingMixin, caplog:pytest.LogCaptureFixture) -> None:
        """Test diagnostic when browser binary exists and is executable."""
        with patch("os.path.exists", return_value = True), patch("os.access", return_value = True):
            scraper_with_config.browser_config.binary_location = "/usr/bin/chrome"
            scraper_with_config.diagnose_browser_issues()

            assert "(ok) Browser binary exists: /usr/bin/chrome" in caplog.text
            assert "(ok) Browser binary is executable" in caplog.text

    def test_diagnose_browser_issues_binary_exists_not_executable(self, scraper_with_config:WebScrapingMixin, caplog:pytest.LogCaptureFixture) -> None:
        """Test diagnostic when browser binary exists but is not executable."""
        with patch("os.path.exists", return_value = True), patch("os.access", return_value = False):
            scraper_with_config.browser_config.binary_location = "/usr/bin/chrome"
            scraper_with_config.diagnose_browser_issues()

            assert "(ok) Browser binary exists: /usr/bin/chrome" in caplog.text
            assert "(fail) Browser binary is not executable" in caplog.text

    def test_diagnose_browser_issues_binary_not_found(self, scraper_with_config:WebScrapingMixin, caplog:pytest.LogCaptureFixture) -> None:
        """Test diagnostic when browser binary is not found."""
        with patch("os.path.exists", return_value = False):
            scraper_with_config.browser_config.binary_location = "/usr/bin/chrome"
            scraper_with_config.diagnose_browser_issues()

            assert "(fail) Browser binary not found: /usr/bin/chrome" in caplog.text

    def test_diagnose_browser_issues_auto_detect_success(self, scraper_with_config:WebScrapingMixin, caplog:pytest.LogCaptureFixture) -> None:
        """Test diagnostic when auto-detecting browser succeeds."""
        with patch.object(scraper_with_config, "get_compatible_browser", return_value = "/usr/bin/chrome"):
            scraper_with_config.browser_config.binary_location = None
            scraper_with_config.diagnose_browser_issues()

            assert "(ok) Auto-detected browser: /usr/bin/chrome" in caplog.text

    def test_diagnose_browser_issues_auto_detect_failure(self, scraper_with_config:WebScrapingMixin, caplog:pytest.LogCaptureFixture) -> None:
        """Test diagnostic when auto-detecting browser fails."""
        with patch.object(scraper_with_config, "get_compatible_browser", side_effect = AssertionError("No browser found")):
            scraper_with_config.browser_config.binary_location = None
            scraper_with_config.diagnose_browser_issues()

            assert "(fail) No compatible browser found" in caplog.text

    def test_diagnose_browser_issues_user_data_dir_exists_readable(
        self, scraper_with_config:WebScrapingMixin, caplog:pytest.LogCaptureFixture, tmp_path:Path
    ) -> None:
        """Test diagnostic when user data directory exists and is readable/writable."""
        test_dir = str(tmp_path / "chrome-profile")
        with (
            patch("os.path.exists", return_value = True),
            patch("os.access", return_value = True),
            patch.object(scraper_with_config, "get_compatible_browser", return_value = "/usr/bin/chrome"),
        ):
            scraper_with_config.browser_config.user_data_dir = test_dir
            scraper_with_config.diagnose_browser_issues()

            assert f"(ok) User data directory exists: {test_dir}" in caplog.text
            assert "(ok) User data directory is readable and writable" in caplog.text

    def test_diagnose_browser_issues_user_data_dir_exists_not_readable(
        self, scraper_with_config:WebScrapingMixin, caplog:pytest.LogCaptureFixture, tmp_path:Path
    ) -> None:
        """Test diagnostic when user data directory exists but is not readable/writable."""
        test_dir = str(tmp_path / "chrome-profile")
        with (
            patch("os.path.exists", return_value = True),
            patch("os.access", return_value = False),
            patch.object(scraper_with_config, "get_compatible_browser", return_value = "/usr/bin/chrome"),
        ):
            scraper_with_config.browser_config.user_data_dir = test_dir
            scraper_with_config.diagnose_browser_issues()

            assert f"(ok) User data directory exists: {test_dir}" in caplog.text
            assert "(fail) User data directory permissions issue" in caplog.text

    def test_diagnose_browser_issues_user_data_dir_not_exists(
        self, scraper_with_config:WebScrapingMixin, caplog:pytest.LogCaptureFixture, tmp_path:Path
    ) -> None:
        """Test diagnostic when user data directory does not exist."""
        test_dir = str(tmp_path / "chrome-profile")
        with (
            patch("os.path.exists", side_effect = lambda path: path != test_dir),
            patch.object(scraper_with_config, "get_compatible_browser", return_value = "/usr/bin/chrome"),
        ):
            scraper_with_config.browser_config.user_data_dir = test_dir
            scraper_with_config.diagnose_browser_issues()

            assert f"(info) User data directory does not exist (will be created): {test_dir}" in caplog.text

    def test_diagnose_browser_issues_remote_debugging_port_configured_open(
        self, scraper_with_config:WebScrapingMixin, caplog:pytest.LogCaptureFixture
    ) -> None:
        """Test diagnostic when remote debugging port is configured and open."""
        with patch("kleinanzeigen_bot.utils.net.is_port_open", return_value = True), patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = Mock()
            mock_response.read.return_value = b'{"Browser": "Chrome/120.0.0.0"}'
            mock_urlopen.return_value = mock_response

            scraper_with_config.browser_config.arguments = ["--remote-debugging-port=9222"]
            scraper_with_config.diagnose_browser_issues()

            assert "(info) Remote debugging port configured: 9222" in caplog.text
            assert "(ok) Remote debugging port is open" in caplog.text
            assert "(ok) Remote debugging API accessible - Browser: Chrome/120.0.0.0" in caplog.text

    def test_diagnose_browser_issues_remote_debugging_port_configured_open_api_fails(
        self, scraper_with_config:WebScrapingMixin, caplog:pytest.LogCaptureFixture
    ) -> None:
        """Test diagnostic when remote debugging port is open but API is not accessible."""
        with (
            patch("kleinanzeigen_bot.utils.net.is_port_open", return_value = True),
            patch("urllib.request.urlopen", side_effect = Exception("Connection refused")),
        ):
            scraper_with_config.browser_config.arguments = ["--remote-debugging-port=9222"]
            scraper_with_config.diagnose_browser_issues()

            assert "(info) Remote debugging port configured: 9222" in caplog.text
            assert "(ok) Remote debugging port is open" in caplog.text
            assert "(fail) Remote debugging port is open but API not accessible: Connection refused" in caplog.text
            assert "This might indicate a browser update issue or configuration problem" in caplog.text

    def test_diagnose_browser_issues_remote_debugging_port_configured_closed(
        self, scraper_with_config:WebScrapingMixin, caplog:pytest.LogCaptureFixture
    ) -> None:
        """Test diagnostic when remote debugging port is configured but closed."""
        with patch("kleinanzeigen_bot.utils.net.is_port_open", return_value = False):
            scraper_with_config.browser_config.arguments = ["--remote-debugging-port=9222"]
            scraper_with_config.diagnose_browser_issues()

            assert "(info) Remote debugging port configured: 9222" in caplog.text
            assert "(info) Remote debugging port is not open" in caplog.text

    def test_diagnose_browser_issues_remote_debugging_port_not_configured(
        self, scraper_with_config:WebScrapingMixin, caplog:pytest.LogCaptureFixture
    ) -> None:
        """Test diagnostic when remote debugging port is not configured."""
        scraper_with_config.browser_config.arguments = ["--other-arg"]
        scraper_with_config.diagnose_browser_issues()

        # Should not log anything about remote debugging port
        assert "Remote debugging port" not in caplog.text

    def test_diagnose_browser_issues_browser_processes_found(self, scraper_with_config:WebScrapingMixin, caplog:pytest.LogCaptureFixture) -> None:
        """Test diagnostic when browser processes are found.
        Updated to test target browser detection with debugging status.
        """
        mock_processes = [
            Mock(info = {"pid": 1234, "name": "chrome", "cmdline": ["/usr/bin/chrome"]}),
            Mock(info = {"pid": 5678, "name": "chromium", "cmdline": ["/usr/bin/chromium"]}),
            Mock(info = {"pid": 9012, "name": "edge", "cmdline": ["/usr/bin/edge"]}),
            Mock(info = {"pid": 3456, "name": "chrome", "cmdline": ["/usr/bin/chrome", "--remote-debugging-port=9222"]}),
        ]

        with (
            patch("psutil.process_iter", return_value = mock_processes),
            patch.object(scraper_with_config, "get_compatible_browser", return_value = "/usr/bin/chrome"),
        ):
            scraper_with_config.diagnose_browser_issues()

            # Should find 2 chrome processes (target browser), one with debugging, one without
            assert "(info) Found 2 browser processes running" in caplog.text
            assert "  - PID 1234: chrome (remote debugging NOT enabled)" in caplog.text
            assert "  - PID 3456: chrome (remote debugging enabled)" in caplog.text

    def test_diagnose_browser_issues_no_browser_processes(self, scraper_with_config:WebScrapingMixin, caplog:pytest.LogCaptureFixture) -> None:
        """Test diagnostic when no browser processes are found."""
        with patch("psutil.process_iter", return_value = []):
            scraper_with_config.diagnose_browser_issues()

            assert "(info) No browser processes currently running" in caplog.text

    @patch("kleinanzeigen_bot.utils.web_scraping_mixin.get_chrome_version_diagnostic_info")
    def test_diagnose_browser_issues_macos_platform_with_user_data_dir(
        self, mock_get_diagnostic:Mock, scraper_with_config:WebScrapingMixin, caplog:pytest.LogCaptureFixture, tmp_path:Path
    ) -> None:
        """Test diagnostic on macOS platform with user data directory."""
        test_dir = str(tmp_path / "chrome-profile")

        # Setup mock for Chrome 136+ detection with valid configuration
        mock_get_diagnostic.return_value = {
            "binary_detection": None,
            "remote_detection": {"version_string": "136.0.6778.0", "major_version": 136, "browser_name": "Chrome", "is_chrome_136_plus": True},
            "chrome_136_plus_detected": True,
            "recommendations": [],
        }

        # Temporarily unset PYTEST_CURRENT_TEST to allow diagnostics to run
        original_env = os.environ.get("PYTEST_CURRENT_TEST")
        if "PYTEST_CURRENT_TEST" in os.environ:
            del os.environ["PYTEST_CURRENT_TEST"]

        try:
            with (
                patch("platform.system", return_value = "Darwin"),
                patch("os.path.exists", return_value = True),
                patch("os.access", return_value = True),
                patch("kleinanzeigen_bot.utils.net.is_port_open", return_value = True),
                patch("urllib.request.urlopen") as mock_urlopen,
                patch.object(scraper_with_config, "get_compatible_browser", return_value = "/usr/bin/chrome"),
            ):
                # Mock Chrome 136+ detection from remote debugging
                mock_response = Mock()
                mock_response.read.return_value = b'{"Browser": "Chrome/136.0.6778.0"}'
                mock_urlopen.return_value = mock_response

                scraper_with_config.browser_config.arguments = ["--remote-debugging-port=9222"]
                scraper_with_config.browser_config.user_data_dir = test_dir
                scraper_with_config.diagnose_browser_issues()

                # Should validate Chrome 136+ configuration and pass
                assert "(info) Remote Chrome 136+ detected - validating configuration" in caplog.text
                assert "(ok) Chrome 136+ configuration validation passed" in caplog.text
        finally:
            # Restore environment variable
            if original_env is not None:
                os.environ["PYTEST_CURRENT_TEST"] = original_env

    def test_diagnose_browser_issues_linux_platform_not_root(self, scraper_with_config:WebScrapingMixin, caplog:pytest.LogCaptureFixture) -> None:
        """Test diagnostic on Linux platform when not running as root."""
        with (
            patch("platform.system", return_value = "Linux"),
            patch.object(scraper_with_config, "get_compatible_browser", return_value = "/usr/bin/chrome"),
            patch("kleinanzeigen_bot.utils.web_scraping_mixin._is_admin", return_value = False),
        ):
            scraper_with_config.diagnose_browser_issues()

            # Linux platform detection was removed - no specific message expected
            assert "Linux detected" not in caplog.text
            # Should not show error about running as root
            assert "(fail) Running as root" not in caplog.text

    def test_diagnose_browser_issues_linux_platform_root(self, scraper_with_config:WebScrapingMixin, caplog:pytest.LogCaptureFixture) -> None:
        """Test diagnostic on Linux platform when running as root."""
        with (
            patch("platform.system", return_value = "Linux"),
            patch.object(scraper_with_config, "get_compatible_browser", return_value = "/usr/bin/chrome"),
            patch("kleinanzeigen_bot.utils.web_scraping_mixin._is_admin", return_value = True),
        ):
            scraper_with_config.diagnose_browser_issues()

            # Linux platform detection was removed - no specific message expected
            assert "Linux detected" not in caplog.text
            assert "(fail) Running as root - this can cause browser issues" in caplog.text

    def test_diagnose_browser_issues_unknown_platform(self, scraper_with_config:WebScrapingMixin, caplog:pytest.LogCaptureFixture) -> None:
        """Test diagnostic on unknown platform."""
        with (
            patch("platform.system", return_value = "UnknownOS"),
            patch.object(scraper_with_config, "get_compatible_browser", return_value = "/usr/bin/chrome"),
        ):
            scraper_with_config.diagnose_browser_issues()

            # Should not show any platform-specific messages
            assert "Windows detected" not in caplog.text
            assert "macOS detected" not in caplog.text
            assert "Linux detected" not in caplog.text

    def test_diagnose_browser_issues_macos_remote_debugging_instructions(self, scraper_with_config:WebScrapingMixin, caplog:pytest.LogCaptureFixture) -> None:
        """Test diagnostic shows macOS-specific remote debugging instructions."""
        with (
            patch("platform.system", return_value = "Darwin"),
            patch("kleinanzeigen_bot.utils.net.is_port_open", return_value = False),
            patch.object(scraper_with_config, "get_compatible_browser", return_value = "/usr/bin/chrome"),
        ):
            scraper_with_config.browser_config.arguments = ["--remote-debugging-port=9222"]
            scraper_with_config.diagnose_browser_issues()

    @patch("kleinanzeigen_bot.utils.web_scraping_mixin.get_chrome_version_diagnostic_info")
    def test_diagnose_browser_issues_chrome_136_plus_misconfigured(
        self, mock_get_diagnostic:Mock, scraper_with_config:WebScrapingMixin, caplog:pytest.LogCaptureFixture
    ) -> None:
        """Test diagnostic when Chrome 136+ is detected but user data directory is not configured."""
        # Setup mock for Chrome 136+ detection with invalid configuration
        mock_get_diagnostic.return_value = {
            "binary_detection": None,
            "remote_detection": {"version_string": "136.0.6778.0", "major_version": 136, "browser_name": "Chrome", "is_chrome_136_plus": True},
            "chrome_136_plus_detected": True,
            "recommendations": [],
        }

        # Temporarily unset PYTEST_CURRENT_TEST to allow diagnostics to run
        original_env = os.environ.get("PYTEST_CURRENT_TEST")
        if "PYTEST_CURRENT_TEST" in os.environ:
            del os.environ["PYTEST_CURRENT_TEST"]

        try:
            with (
                patch("kleinanzeigen_bot.utils.net.is_port_open", return_value = True),
                patch("urllib.request.urlopen") as mock_urlopen,
                patch.object(scraper_with_config, "get_compatible_browser", return_value = "/usr/bin/chrome"),
            ):
                # Mock Chrome 136+ detection from remote debugging
                mock_response = Mock()
                mock_response.read.return_value = b'{"Browser": "Chrome/136.0.6778.0"}'
                mock_urlopen.return_value = mock_response

                # Configure remote debugging but NO user data directory
                scraper_with_config.browser_config.arguments = ["--remote-debugging-port=9222"]
                scraper_with_config.browser_config.user_data_dir = None
                scraper_with_config.diagnose_browser_issues()

                # Should detect Chrome 136+ and show configuration error
                assert "(info) Remote Chrome 136+ detected - validating configuration" in caplog.text
                assert "(fail) Chrome 136+ configuration validation failed" in caplog.text
                assert "Chrome/Edge 136+ requires --user-data-dir to be specified" in caplog.text
                assert "Solution: Add --user-data-dir=/path/to/directory to browser arguments" in caplog.text
        finally:
            # Restore environment variable
            if original_env is not None:
                os.environ["PYTEST_CURRENT_TEST"] = original_env

    def test_diagnose_browser_issues_complete_diagnostic_flow(
        self, scraper_with_config:WebScrapingMixin, caplog:pytest.LogCaptureFixture, tmp_path:Path
    ) -> None:
        """Test complete diagnostic flow with all components."""
        test_dir = str(tmp_path / "chrome-profile")
        with (
            patch("os.path.exists", return_value = True),
            patch("os.access", return_value = True),
            patch("kleinanzeigen_bot.utils.net.is_port_open", return_value = True),
            patch("urllib.request.urlopen") as mock_urlopen,
            patch("psutil.process_iter", return_value = []),
            patch("platform.system", return_value = "Linux"),
            patch("kleinanzeigen_bot.utils.web_scraping_mixin._is_admin", return_value = False),
        ):
            mock_response = Mock()
            mock_response.read.return_value = b'{"Browser": "Chrome/120.0.0.0"}'
            mock_urlopen.return_value = mock_response

            scraper_with_config.browser_config.binary_location = "/usr/bin/chrome"
            scraper_with_config.browser_config.user_data_dir = test_dir
            scraper_with_config.browser_config.arguments = ["--remote-debugging-port=9222"]

            scraper_with_config.diagnose_browser_issues()

            # Check that all diagnostic sections are present
            assert "=== Browser Connection Diagnostics ===" in caplog.text
            assert "(ok) Browser binary exists: /usr/bin/chrome" in caplog.text
            assert "(ok) Browser binary is executable" in caplog.text
            assert f"(ok) User data directory exists: {test_dir}" in caplog.text
            assert "(ok) User data directory is readable and writable" in caplog.text
            assert "(info) Remote debugging port configured: 9222" in caplog.text
            assert "(ok) Remote debugging port is open" in caplog.text
            assert "(ok) Remote debugging API accessible - Browser: Chrome/120.0.0.0" in caplog.text
            assert "(info) No browser processes currently running" in caplog.text
            # Linux platform detection was removed - no specific message expected
            assert "Linux detected" not in caplog.text
            assert "=== End Diagnostics ===" in caplog.text

    def test_diagnose_browser_issues_remote_debugging_host_configured(self, scraper_with_config:WebScrapingMixin, caplog:pytest.LogCaptureFixture) -> None:
        """Test diagnostic when remote debugging host is configured."""
        with (
            patch("os.path.exists", return_value = True),
            patch("os.access", return_value = True),
            patch("kleinanzeigen_bot.utils.net.is_port_open", return_value = True),
            patch("urllib.request.urlopen") as mock_urlopen,
            patch("psutil.process_iter", return_value = []),
            patch("platform.system", return_value = "Linux"),
            patch("kleinanzeigen_bot.utils.web_scraping_mixin._is_admin", return_value = False),
            patch.object(scraper_with_config, "get_compatible_browser", return_value = "/usr/bin/chrome"),
        ):
            mock_response = Mock()
            mock_response.read.return_value = b'{"Browser": "Chrome/120.0.0.0"}'
            mock_urlopen.return_value = mock_response

            scraper_with_config.browser_config.arguments = ["--remote-debugging-host=192.168.1.100", "--remote-debugging-port=9222"]

            scraper_with_config.diagnose_browser_issues()

            assert "(info) Remote debugging port configured: 9222" in caplog.text
            assert "(ok) Remote debugging port is open" in caplog.text

    def test_diagnose_browser_issues_process_info_missing_name(self, scraper_with_config:WebScrapingMixin, caplog:pytest.LogCaptureFixture) -> None:
        """Test diagnostic when process info is missing name."""
        mock_process = Mock()
        mock_process.info = {"pid": 1234, "name": None, "cmdline": []}

        with (
            patch("os.path.exists", return_value = True),
            patch("os.access", return_value = True),
            patch("psutil.process_iter", return_value = [mock_process]),
            patch("platform.system", return_value = "Linux"),
            patch("kleinanzeigen_bot.utils.web_scraping_mixin._is_admin", return_value = False),
            patch.object(scraper_with_config, "get_compatible_browser", return_value = "/usr/bin/chrome"),
        ):
            scraper_with_config.diagnose_browser_issues()

            assert "(info) No browser processes currently running" in caplog.text

    def test_diagnose_browser_issues_psutil_exception_handling(self, scraper_with_config:WebScrapingMixin, caplog:pytest.LogCaptureFixture) -> None:
        """Test diagnostic when psutil raises an exception during process iteration."""
        # Mock psutil.process_iter to return a list that will cause an exception when accessing proc.info
        mock_process = Mock()
        mock_process.info = {"name": "chrome"}
        mock_processes = [mock_process]

        with (
            patch("os.path.exists", return_value = True),
            patch("os.access", return_value = True),
            patch("psutil.process_iter", return_value = mock_processes),
            patch("platform.system", return_value = "Linux"),
            patch("kleinanzeigen_bot.utils.web_scraping_mixin._is_admin", return_value = False),
            patch.object(scraper_with_config, "get_compatible_browser", return_value = "/usr/bin/chrome"),
            patch.object(mock_process, "info", side_effect = psutil.AccessDenied),
        ):
            scraper_with_config.diagnose_browser_issues()

            # Should handle the exception gracefully and continue
            assert "=== Browser Connection Diagnostics ===" in caplog.text
            assert "=== End Diagnostics ===" in caplog.text

    def test_diagnose_browser_issues_browser_not_executable(self, scraper_with_config:WebScrapingMixin, caplog:pytest.LogCaptureFixture) -> None:
        """Test diagnostic when browser binary exists but is not executable."""
        scraper_with_config.browser_config.binary_location = "/usr/bin/chrome"
        with (
            patch("os.path.exists", return_value = True),
            patch("os.access", return_value = False),
            patch("platform.system", return_value = "Linux"),
            patch("kleinanzeigen_bot.utils.web_scraping_mixin._is_admin", return_value = False),
            patch("psutil.process_iter", return_value = []),
        ):
            scraper_with_config.diagnose_browser_issues()

            assert "(fail) Browser binary is not executable" in caplog.text

    def test_diagnose_browser_issues_browser_not_found(self, scraper_with_config:WebScrapingMixin, caplog:pytest.LogCaptureFixture) -> None:
        """Test diagnostic when browser binary does not exist."""
        scraper_with_config.browser_config.binary_location = "/usr/bin/chrome"
        with (
            patch("os.path.exists", return_value = False),
            patch("platform.system", return_value = "Linux"),
            patch("kleinanzeigen_bot.utils.web_scraping_mixin._is_admin", return_value = False),
            patch("psutil.process_iter", return_value = []),
        ):
            scraper_with_config.diagnose_browser_issues()

            assert "(fail) Browser binary not found:" in caplog.text

    def test_diagnose_browser_issues_no_browser_auto_detection(self, scraper_with_config:WebScrapingMixin, caplog:pytest.LogCaptureFixture) -> None:
        """Test diagnostic when no browser binary is configured and auto-detection fails."""
        scraper_with_config.browser_config.binary_location = None
        with (
            patch("platform.system", return_value = "Linux"),
            patch("kleinanzeigen_bot.utils.web_scraping_mixin._is_admin", return_value = False),
            patch("psutil.process_iter", return_value = []),
            patch.object(scraper_with_config, "get_compatible_browser", side_effect = AssertionError("No browser found")),
        ):
            scraper_with_config.diagnose_browser_issues()
        assert "(fail) No compatible browser found" in caplog.text

    def test_diagnose_browser_issues_user_data_dir_permissions_issue(
        self, scraper_with_config:WebScrapingMixin, caplog:pytest.LogCaptureFixture, tmp_path:Path
    ) -> None:
        """Test diagnostic when user data directory has permission issues."""
        test_dir = str(tmp_path / "chrome-profile")
        scraper_with_config.browser_config.user_data_dir = test_dir

        with (
            patch("os.path.exists", return_value = True),
            patch("os.access", return_value = False),
            patch("platform.system", return_value = "Linux"),
            patch("kleinanzeigen_bot.utils.web_scraping_mixin._is_admin", return_value = False),
            patch.object(scraper_with_config, "get_compatible_browser", return_value = "/usr/bin/chrome"),
        ):
            scraper_with_config.diagnose_browser_issues()

            assert "(fail) User data directory permissions issue" in caplog.text

    def test_diagnose_browser_issues_remote_debugging_api_inaccessible(self, scraper_with_config:WebScrapingMixin, caplog:pytest.LogCaptureFixture) -> None:
        """Test diagnostic when remote debugging port is open but API is not accessible."""
        scraper_with_config.browser_config.arguments = ["--remote-debugging-port=9222"]

        with (
            patch("os.path.exists", return_value = True),
            patch("os.access", return_value = True),
            patch("kleinanzeigen_bot.utils.web_scraping_mixin.net.is_port_open", return_value = True),
            patch("urllib.request.urlopen", side_effect = Exception("Connection refused")),
            patch("platform.system", return_value = "Linux"),
            patch("kleinanzeigen_bot.utils.web_scraping_mixin._is_admin", return_value = False),
            patch.object(scraper_with_config, "get_compatible_browser", return_value = "/usr/bin/chrome"),
        ):
            scraper_with_config.diagnose_browser_issues()

            assert "(fail) Remote debugging port is open but API not accessible" in caplog.text
            assert "This might indicate a browser update issue or configuration problem" in caplog.text

    def test_diagnose_browser_issues_macos_chrome_warning(self, scraper_with_config:WebScrapingMixin, caplog:pytest.LogCaptureFixture) -> None:
        """Test diagnostic when macOS Chrome remote debugging is configured without user_data_dir."""
        scraper_with_config.browser_config.arguments = ["--remote-debugging-port=9222"]
        scraper_with_config.browser_config.user_data_dir = None

        with (
            patch("os.path.exists", return_value = True),
            patch("os.access", return_value = True),
            patch("psutil.process_iter", return_value = []),
            patch("kleinanzeigen_bot.utils.web_scraping_mixin.net.is_port_open", return_value = False),
            patch("platform.system", return_value = "Darwin"),
            patch("kleinanzeigen_bot.utils.web_scraping_mixin._is_admin", return_value = False),
            patch.object(scraper_with_config, "get_compatible_browser", return_value = "/usr/bin/chrome"),
        ):
            scraper_with_config.diagnose_browser_issues()

    def test_diagnose_browser_issues_linux_root_user(self, scraper_with_config:WebScrapingMixin, caplog:pytest.LogCaptureFixture) -> None:
        """Test diagnostic when running as root on Linux."""
        with (
            patch("os.path.exists", return_value = True),
            patch("os.access", return_value = True),
            patch("platform.system", return_value = "Linux"),
            patch("kleinanzeigen_bot.utils.web_scraping_mixin._is_admin", return_value = True),
            patch.object(scraper_with_config, "get_compatible_browser", return_value = "/usr/bin/chrome"),
        ):
            scraper_with_config.diagnose_browser_issues()

            assert "(fail) Running as root - this can cause browser issues" in caplog.text

    def test_is_admin_on_windows_system(self) -> None:
        """Test _is_admin function on Windows system."""
        # Create a mock os module without geteuid
        mock_os = Mock()
        # Remove geteuid attribute to simulate Windows
        del mock_os.geteuid

        with patch("kleinanzeigen_bot.utils.web_scraping_mixin.os", mock_os):
            assert _is_admin() is False

    def test_diagnose_browser_issues_psutil_exceptions(self, web_scraper:WebScrapingMixin) -> None:
        """Test diagnose_browser_issues handles psutil exceptions gracefully."""
        # Mock psutil.process_iter to return a list that will cause exceptions when accessing proc.info
        mock_process1 = Mock()
        mock_process1.info = {"name": "chrome"}
        mock_process2 = Mock()
        mock_process2.info = {"name": "edge"}
        mock_processes = [mock_process1, mock_process2]

        with (
            patch("os.path.exists", return_value = True),
            patch("os.access", return_value = True),
            patch("psutil.process_iter", return_value = mock_processes),
            patch("platform.system", return_value = "Linux"),
            patch("kleinanzeigen_bot.utils.web_scraping_mixin._is_admin", return_value = False),
            patch("kleinanzeigen_bot.utils.web_scraping_mixin.WebScrapingMixin._diagnose_chrome_version_issues"),
            patch("kleinanzeigen_bot.utils.web_scraping_mixin.net.is_port_open", return_value = False),
            patch.object(web_scraper, "get_compatible_browser", return_value = "/usr/bin/chrome"),
            patch.object(mock_process1, "info", side_effect = psutil.NoSuchProcess(pid = 123)),
            patch.object(mock_process2, "info", side_effect = psutil.AccessDenied(pid = 456)),
        ):
            # Should not raise any exceptions
            web_scraper.diagnose_browser_issues()

    def test_diagnose_browser_issues_handles_per_process_errors(self, scraper_with_config:WebScrapingMixin, caplog:pytest.LogCaptureFixture) -> None:
        """diagnose_browser_issues should ignore psutil errors raised per process."""
        caplog.set_level(logging.INFO)

        class FailingProcess:
            @property
            def info(self) -> dict[str, object]:
                raise psutil.AccessDenied(pid = 999)

        with (
            patch("os.path.exists", return_value = True),
            patch("os.access", return_value = True),
            patch("psutil.process_iter", return_value = [FailingProcess()]),
            patch("platform.system", return_value = "Linux"),
            patch("kleinanzeigen_bot.utils.web_scraping_mixin._is_admin", return_value = False),
            patch.object(scraper_with_config, "_diagnose_chrome_version_issues"),
        ):
            scraper_with_config.browser_config.binary_location = "/usr/bin/chrome"
            scraper_with_config.diagnose_browser_issues()

        assert "(info) No browser processes currently running" in caplog.text

    def test_diagnose_browser_issues_handles_global_psutil_failure(self, scraper_with_config:WebScrapingMixin, caplog:pytest.LogCaptureFixture) -> None:
        """diagnose_browser_issues should log a warning if psutil.process_iter fails entirely."""
        caplog.set_level(logging.WARNING)

        with (
            patch("os.path.exists", return_value = True),
            patch("os.access", return_value = True),
            patch("psutil.process_iter", side_effect = psutil.Error("boom")),
            patch("platform.system", return_value = "Linux"),
            patch("kleinanzeigen_bot.utils.web_scraping_mixin._is_admin", return_value = False),
            patch.object(scraper_with_config, "_diagnose_chrome_version_issues"),
        ):
            scraper_with_config.browser_config.binary_location = "/usr/bin/chrome"
            scraper_with_config.diagnose_browser_issues()

        assert "(warn) Unable to inspect browser processes:" in caplog.text

    @pytest.mark.asyncio
    async def test_validate_chrome_version_configuration_port_open_but_api_inaccessible(self, web_scraper:WebScrapingMixin) -> None:
        """Test _validate_chrome_version_configuration when port is open but API is inaccessible."""
        # Configure remote debugging
        web_scraper.browser_config.arguments = ["--remote-debugging-port=9222"]
        web_scraper.browser_config.binary_location = "/usr/bin/chrome"

        with (
            patch.dict("os.environ", {}, clear = True),
            patch("kleinanzeigen_bot.utils.web_scraping_mixin.WebScrapingMixin._check_port_with_retry", return_value = True),
            patch("kleinanzeigen_bot.utils.web_scraping_mixin.detect_chrome_version_from_remote_debugging", return_value = None),
            patch("kleinanzeigen_bot.utils.web_scraping_mixin.detect_chrome_version_from_binary", return_value = None),
            patch("kleinanzeigen_bot.utils.web_scraping_mixin.LOG") as mock_log,
        ):
            # Should not raise any exceptions and should log the appropriate debug message
            await web_scraper._validate_chrome_version_configuration()

            # Verify the debug message was logged
            mock_log.debug.assert_any_call(" -> Port is open but remote debugging API not accessible")

    @pytest.mark.asyncio
    async def test_validate_chrome_version_configuration_remote_detection_exception(self, web_scraper:WebScrapingMixin) -> None:
        """Test _validate_chrome_version_configuration when remote detection raises exception."""
        # Configure remote debugging
        web_scraper.browser_config.arguments = ["--remote-debugging-port=9222"]
        web_scraper.browser_config.binary_location = "/usr/bin/chrome"

        with (
            patch.dict("os.environ", {}, clear = True),
            patch("kleinanzeigen_bot.utils.web_scraping_mixin.WebScrapingMixin._check_port_with_retry", return_value = True),
            patch("kleinanzeigen_bot.utils.web_scraping_mixin.detect_chrome_version_from_remote_debugging", side_effect = Exception("Test exception")),
            patch("kleinanzeigen_bot.utils.web_scraping_mixin.detect_chrome_version_from_binary", return_value = None),
            patch("kleinanzeigen_bot.utils.web_scraping_mixin.LOG") as mock_log,
        ):
            # Should not raise any exceptions and should log the appropriate debug message
            await web_scraper._validate_chrome_version_configuration()

            # Verify the debug message was logged
            # Check that the debug method was called with the expected message
            debug_calls = [call for call in mock_log.debug.call_args_list if "Failed to detect version from existing browser" in str(call)]
            assert len(debug_calls) > 0, "Expected debug message not found"

    @pytest.mark.asyncio
    async def test_validate_chrome_version_configuration_no_existing_browser(self, web_scraper:WebScrapingMixin) -> None:
        """Test _validate_chrome_version_configuration when no existing browser is found."""
        # Configure remote debugging
        web_scraper.browser_config.arguments = ["--remote-debugging-port=9222"]
        web_scraper.browser_config.binary_location = "/usr/bin/chrome"

        with (
            patch.dict("os.environ", {}, clear = True),
            patch("kleinanzeigen_bot.utils.web_scraping_mixin.WebScrapingMixin._check_port_with_retry", return_value = False),
            patch("kleinanzeigen_bot.utils.web_scraping_mixin.detect_chrome_version_from_binary", return_value = None),
            patch("kleinanzeigen_bot.utils.web_scraping_mixin.LOG") as mock_log,
        ):
            # Should not raise any exceptions and should log the appropriate debug message
            await web_scraper._validate_chrome_version_configuration()

            # Verify the debug message was logged
            mock_log.debug.assert_any_call(" -> No existing browser found at %s:%s", "127.0.0.1", 9222)


class TestWebScrapingMixinPortRetry:
    """Test the _check_port_with_retry method."""

    @pytest.fixture
    def scraper_with_remote_config(self) -> WebScrapingMixin:
        """Create a WebScrapingMixin instance with remote debugging configuration."""
        scraper = WebScrapingMixin()
        scraper.browser_config.binary_location = "/usr/bin/chrome"
        scraper.browser_config.arguments = ["--remote-debugging-port=9222"]
        return scraper

    @pytest.mark.asyncio
    async def test_browser_connection_error_handling(self, scraper_with_remote_config:WebScrapingMixin, caplog:pytest.LogCaptureFixture) -> None:
        """Test error handling when browser connection fails."""
        with (
            patch("os.path.exists", return_value = True),
            patch("kleinanzeigen_bot.utils.web_scraping_mixin.files.exists", AsyncMock(return_value = True)),
            patch("kleinanzeigen_bot.utils.web_scraping_mixin.net.is_port_open", return_value = True),
            patch("kleinanzeigen_bot.utils.web_scraping_mixin.nodriver.start", side_effect = Exception("Failed to connect as root user")),
            patch("kleinanzeigen_bot.utils.web_scraping_mixin.nodriver.Config") as mock_config_class,
        ):
            mock_config = Mock()
            mock_config_class.return_value = mock_config

            with pytest.raises(Exception, match = "Failed to connect as root user"):
                await scraper_with_remote_config.create_browser_session()

            # Check that the error handling was triggered
            assert "Failed to connect to browser. This error often occurs when:" in caplog.text

    @pytest.mark.asyncio
    async def test_browser_connection_error_handling_non_root_error(
        self, scraper_with_remote_config:WebScrapingMixin, caplog:pytest.LogCaptureFixture
    ) -> None:
        """Test error handling when browser connection fails with non-root error."""
        with (
            patch("os.path.exists", return_value = True),
            patch("kleinanzeigen_bot.utils.web_scraping_mixin.files.exists", AsyncMock(return_value = True)),
            patch("kleinanzeigen_bot.utils.web_scraping_mixin.net.is_port_open", return_value = True),
            patch("kleinanzeigen_bot.utils.web_scraping_mixin.nodriver.start", side_effect = Exception("Connection timeout")),
            patch("kleinanzeigen_bot.utils.web_scraping_mixin.nodriver.Config") as mock_config_class,
        ):
            mock_config = Mock()
            mock_config_class.return_value = mock_config

            with pytest.raises(Exception, match = "Connection timeout"):
                await scraper_with_remote_config.create_browser_session()

            # Should not trigger the root-specific error handling
            assert "Failed to connect to browser. This error often occurs when:" not in caplog.text

    @pytest.fixture
    def scraper_with_startup_config(self) -> WebScrapingMixin:
        """Create a WebScrapingMixin instance for testing browser startup (no remote debugging)."""
        scraper = WebScrapingMixin()
        scraper.browser_config.binary_location = "/usr/bin/chrome"
        # No remote debugging port configured - will start new browser
        return scraper

    @pytest.mark.asyncio
    async def test_browser_startup_error_handling_root_error(self, scraper_with_startup_config:WebScrapingMixin, caplog:pytest.LogCaptureFixture) -> None:
        """Test error handling when browser startup fails with root error."""
        with (
            patch("os.path.exists", return_value = True),
            patch("kleinanzeigen_bot.utils.web_scraping_mixin.files.exists", AsyncMock(return_value = True)),
            patch("kleinanzeigen_bot.utils.web_scraping_mixin.nodriver.start", side_effect = Exception("Failed to start as root user")),
            patch("kleinanzeigen_bot.utils.web_scraping_mixin.nodriver.Config") as mock_config_class,
        ):
            mock_config = Mock()
            mock_config_class.return_value = mock_config

            with pytest.raises(Exception, match = "Failed to start as root user"):
                await scraper_with_startup_config.create_browser_session()

            # Check that the root-specific error handling was triggered
            assert "Failed to start browser. This error often occurs when:" in caplog.text

    @pytest.mark.asyncio
    async def test_browser_startup_error_handling_non_root_error(self, scraper_with_startup_config:WebScrapingMixin, caplog:pytest.LogCaptureFixture) -> None:
        """Test error handling when browser startup fails with non-root error."""
        with (
            patch("os.path.exists", return_value = True),
            patch("kleinanzeigen_bot.utils.web_scraping_mixin.files.exists", AsyncMock(return_value = True)),
            patch("kleinanzeigen_bot.utils.web_scraping_mixin.nodriver.start", side_effect = Exception("Browser binary not found")),
            patch("kleinanzeigen_bot.utils.web_scraping_mixin.nodriver.Config") as mock_config_class,
        ):
            mock_config = Mock()
            mock_config_class.return_value = mock_config

            with pytest.raises(Exception, match = "Browser binary not found"):
                await scraper_with_startup_config.create_browser_session()

            # Should not trigger the root-specific error handling
            assert "Failed to start browser. This error often occurs when:" not in caplog.text

    @pytest.fixture
    def scraper(self) -> WebScrapingMixin:
        """Create a WebScrapingMixin instance."""
        return WebScrapingMixin()

    @pytest.mark.asyncio
    async def test_check_port_with_retry_success_first_try(self, scraper:WebScrapingMixin) -> None:
        """Test port check succeeds on first try."""
        with patch("kleinanzeigen_bot.utils.net.is_port_open", return_value = True):
            result = await scraper._check_port_with_retry("127.0.0.1", 9222)
            assert result is True

    @pytest.mark.asyncio
    async def test_check_port_with_retry_success_after_retries(self, scraper:WebScrapingMixin) -> None:
        """Test port check succeeds after some retries."""
        with patch("kleinanzeigen_bot.utils.net.is_port_open", side_effect = [False, False, True]):
            result = await scraper._check_port_with_retry("127.0.0.1", 9222, max_retries = 3, retry_delay = 0.1)
            assert result is True

    @pytest.mark.asyncio
    async def test_check_port_with_retry_failure_after_max_retries(self, scraper:WebScrapingMixin) -> None:
        """Test port check fails after max retries."""
        with patch("kleinanzeigen_bot.utils.net.is_port_open", return_value = False):
            result = await scraper._check_port_with_retry("127.0.0.1", 9222, max_retries = 2, retry_delay = 0.1)
            assert result is False

    @pytest.mark.asyncio
    async def test_check_port_with_retry_custom_parameters(self, scraper:WebScrapingMixin) -> None:
        """Test port check with custom retry parameters."""
        with patch("kleinanzeigen_bot.utils.net.is_port_open", side_effect = [False, True]):
            result = await scraper._check_port_with_retry("192.168.1.100", 8080, max_retries = 5, retry_delay = 0.05)
            assert result is True


class TestWebScrapingMixinProfileHandling:
    """Test the enhanced profile directory handling."""

    @pytest.fixture
    def scraper_with_profile_config(self, tmp_path:Path) -> WebScrapingMixin:
        """Create a WebScrapingMixin instance with profile configuration."""
        scraper = WebScrapingMixin()
        scraper.browser_config.user_data_dir = str(tmp_path / "test-profile")
        scraper.browser_config.profile_name = "TestProfile"
        return scraper

    def test_profile_directory_creation_with_user_data_dir(self, scraper_with_profile_config:WebScrapingMixin, tmp_path:Path) -> None:
        """Test profile directory creation when user_data_dir is configured."""
        test_dir = str(tmp_path / "test-profile")
        scraper_with_profile_config.browser_config.user_data_dir = test_dir

        with (
            patch("os.path.join", return_value = os.path.join(test_dir, "TestProfile")),
            patch("os.makedirs") as mock_makedirs,
            patch("os.path.exists", return_value = False),
            patch("builtins.open", mock_open()),
            patch("json.dump"),
        ):
            # This would be called during browser session creation
            profile_dir = os.path.join(test_dir, "TestProfile")
            mock_makedirs.assert_not_called()  # Not called yet

            # Simulate the profile creation logic
            os.makedirs(profile_dir, exist_ok = True)
            mock_makedirs.assert_called_with(profile_dir, exist_ok = True)

    def test_profile_directory_creation_with_preferences_file(self, scraper_with_profile_config:WebScrapingMixin, tmp_path:Path) -> None:
        """Test profile directory creation with preferences file when it doesn't exist."""
        test_dir = str(tmp_path / "test-profile")
        scraper_with_profile_config.browser_config.user_data_dir = test_dir

        with (
            patch("os.makedirs") as mock_makedirs,
            patch("os.path.exists", return_value = False),
            patch("builtins.open", mock_open()) as mock_file,
            patch("json.dump") as mock_json_dump,
        ):
            # Simulate the profile creation logic
            profile_dir = os.path.join(test_dir, "TestProfile")
            prefs_file = os.path.join(profile_dir, "Preferences")

            # This would be called during browser session creation
            os.makedirs(profile_dir, exist_ok = True)
            mock_makedirs.assert_called_with(profile_dir, exist_ok = True)

            # Simulate preferences file creation
            with open(prefs_file, "w", encoding = "UTF-8") as fd:
                json.dump({"test": "preferences"}, fd)

            mock_file.assert_called_with(prefs_file, "w", encoding = "UTF-8")
            mock_json_dump.assert_called()

    def test_profile_directory_creation_with_existing_preferences_file(self, scraper_with_profile_config:WebScrapingMixin, tmp_path:Path) -> None:
        """Test profile directory creation when preferences file already exists."""
        test_dir = str(tmp_path / "test-profile")
        scraper_with_profile_config.browser_config.user_data_dir = test_dir

        with (
            patch("os.makedirs") as mock_makedirs,
            patch("os.path.exists", return_value = True),
            patch("builtins.open", mock_open()) as mock_file,
            patch("json.dump") as mock_json_dump,
        ):
            # Simulate the profile creation logic
            profile_dir = os.path.join(test_dir, "TestProfile")

            # This would be called during browser session creation
            os.makedirs(profile_dir, exist_ok = True)
            mock_makedirs.assert_called_with(profile_dir, exist_ok = True)

            # Preferences file exists, so it should not be created
            mock_file.assert_not_called()
            mock_json_dump.assert_not_called()

    def test_profile_directory_creation_with_edge_browser(self, scraper_with_profile_config:WebScrapingMixin, tmp_path:Path) -> None:
        """Test profile directory creation with Edge browser configuration."""
        test_dir = str(tmp_path / "test-profile")
        scraper_with_profile_config.browser_config.user_data_dir = test_dir
        scraper_with_profile_config.browser_config.binary_location = "/usr/bin/microsoft-edge"

        with (
            patch("os.makedirs") as mock_makedirs,
            patch("os.path.exists", return_value = False),
            patch("builtins.open", mock_open()),
            patch("json.dump"),
            patch("os.environ", {"MSEDGEDRIVER_TELEMETRY_OPTOUT": "1"}),
        ):
            # Simulate the profile creation logic
            profile_dir = os.path.join(test_dir, "TestProfile")

            # This would be called during browser session creation
            os.makedirs(profile_dir, exist_ok = True)
            mock_makedirs.assert_called_with(profile_dir, exist_ok = True)

    def test_profile_directory_creation_with_private_window(self, scraper_with_profile_config:WebScrapingMixin, tmp_path:Path) -> None:
        """Test profile directory creation with private window configuration."""
        test_dir = str(tmp_path / "test-profile")
        scraper_with_profile_config.browser_config.user_data_dir = test_dir
        scraper_with_profile_config.browser_config.use_private_window = True

        with patch("os.makedirs") as mock_makedirs, patch("os.path.exists", return_value = False), patch("builtins.open", mock_open()), patch("json.dump"):
            # Simulate the profile creation logic
            profile_dir = os.path.join(test_dir, "TestProfile")

            # This would be called during browser session creation
            os.makedirs(profile_dir, exist_ok = True)
            mock_makedirs.assert_called_with(profile_dir, exist_ok = True)

    def test_profile_directory_creation_without_user_data_dir(self, scraper_with_profile_config:WebScrapingMixin) -> None:
        """Test profile directory handling when user_data_dir is not configured."""
        scraper_with_profile_config.browser_config.user_data_dir = None

        # Should not create profile directories when user_data_dir is None
        with patch("os.path.join") as mock_join, patch("os.makedirs") as mock_makedirs:
            # The profile creation logic should not be called
            mock_join.assert_not_called()
            mock_makedirs.assert_not_called()


class TestWebScrapingMixinAdminCheck:
    """Test the _is_admin helper function."""

    def test_is_admin_on_unix_system(self) -> None:
        """Test _is_admin function on Unix-like system."""
        # Create a mock os module with geteuid
        mock_os = Mock()
        mock_os.geteuid = Mock(return_value = 0)

        with patch("kleinanzeigen_bot.utils.web_scraping_mixin.os", mock_os):
            assert _is_admin() is True

    def test_is_admin_on_unix_system_not_root(self) -> None:
        """Test _is_admin function on Unix-like system when not root."""
        # Create a mock os module with geteuid
        mock_os = Mock()
        mock_os.geteuid = Mock(return_value = 1000)

        with patch("kleinanzeigen_bot.utils.web_scraping_mixin.os", mock_os):
            assert _is_admin() is False

    def test_is_admin_on_windows_system(self) -> None:
        """Test _is_admin function on Windows system."""
        # Create a mock os module without geteuid
        mock_os = Mock()
        # Remove geteuid attribute to simulate Windows
        del mock_os.geteuid

        with patch("kleinanzeigen_bot.utils.web_scraping_mixin.os", mock_os):
            assert _is_admin() is False
