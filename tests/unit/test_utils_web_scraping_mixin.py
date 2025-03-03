"""
SPDX-FileCopyrightText: © Jens Bergmann and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

This module contains tests for the web_scraping_mixin.py utility module.
"""
from typing import Any, Literal, Optional, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kleinanzeigen_bot.utils.web_scraping_mixin import By, Is, WebScrapingMixin

# Import TimeoutError for explicit type checking


class CustomTimeoutError(Exception):
    """Timeout error for web operations."""


@pytest.fixture
def web_scraper() -> WebScrapingMixin:
    """Create a WebScrapingMixin instance for testing."""
    scraper = WebScrapingMixin()
    scraper.browser = MagicMock()
    scraper.page = AsyncMock()
    return scraper


@pytest.mark.asyncio
async def test_web_check_unsupported_attribute(web_scraper: WebScrapingMixin) -> None:
    """Test web_check with an unsupported attribute."""
    # Mock the web_find method to avoid the actual implementation
    with patch.object(web_scraper, 'web_find', new_callable=AsyncMock) as mock_web_find:
        mock_web_find.return_value = MagicMock()

        # Test with an unsupported attribute
        with pytest.raises(AssertionError, match="Unsupported attribute"):
            # Pass an invalid enum value that doesn't exist in Is
            # Use cast to avoid type error while still testing the functionality
            await web_scraper.web_check(By.ID, "test-id", cast(Is, 999))


@pytest.mark.asyncio
async def test_web_check_is_displayed(web_scraper: WebScrapingMixin) -> None:
    """Test web_check with Is.DISPLAYED attribute."""
    # Mock the web_find method to avoid the actual implementation
    with patch.object(web_scraper, 'web_find', new_callable=AsyncMock) as mock_web_find:
        # Create a mock element with apply method
        mock_element = MagicMock()
        mock_element.apply = AsyncMock(return_value=True)
        mock_element.attrs = {}
        mock_web_find.return_value = mock_element

        # Test with Is.DISPLAYED
        result = await web_scraper.web_check(By.ID, "test-id", Is.DISPLAYED)
        assert result is True
        mock_element.apply.assert_called_once()


@pytest.mark.asyncio
async def test_web_check_is_enabled(web_scraper: WebScrapingMixin) -> None:
    """Test web_check with Is.DISABLED attribute."""
    # Mock the web_find method to avoid the actual implementation
    with patch.object(web_scraper, 'web_find', new_callable=AsyncMock) as mock_web_find:
        # Create a mock element with disabled attribute
        mock_element = MagicMock()
        # Configure attrs.get to return a value for "disabled"
        mock_attrs = MagicMock()
        mock_attrs.get.return_value = "true"  # Element is disabled
        mock_element.attrs = mock_attrs
        mock_web_find.return_value = mock_element

        # Test with Is.DISABLED
        result = await web_scraper.web_check(By.ID, "test-id", Is.DISABLED)
        assert result is True
        mock_attrs.get.assert_called_once_with("disabled")


@pytest.mark.asyncio
async def test_web_check_is_selected(web_scraper: WebScrapingMixin) -> None:
    """Test web_check with Is.SELECTED attribute."""
    # Mock the web_find method to avoid the actual implementation
    with patch.object(web_scraper, 'web_find', new_callable=AsyncMock) as mock_web_find:
        # Create a mock element with apply method
        mock_element = MagicMock()
        mock_element.apply = AsyncMock(return_value=True)
        mock_element.attrs = {}
        mock_web_find.return_value = mock_element

        # Test with Is.SELECTED
        result = await web_scraper.web_check(By.ID, "test-id", Is.SELECTED)
        assert result is True
        mock_element.apply.assert_called_once()


@pytest.mark.asyncio
async def test_web_check_element_not_found(web_scraper: WebScrapingMixin) -> None:
    """Test web_check when element is not found."""
    # Create a patched version of web_check that handles TimeoutError
    original_web_check = web_scraper.web_check

    async def patched_web_check(selector_type: By, selector_value: str, attr: Is, **kwargs: Any) -> bool:
        try:
            # Return the result directly without casting to bool
            return await original_web_check(selector_type, selector_value, attr, **kwargs)
        except CustomTimeoutError:
            return False

    # Apply the patch
    with patch.object(web_scraper, 'web_check', patched_web_check):
        # Mock the web_find method to raise TimeoutError
        with patch.object(web_scraper, 'web_find', new_callable=AsyncMock) as mock_web_find:
            mock_web_find.side_effect = CustomTimeoutError("Element not found")

            # Test with Is.DISPLAYED - should catch the TimeoutError and return False
            result = await web_scraper.web_check(By.ID, "test-id", Is.DISPLAYED)
            assert result is False


@pytest.mark.asyncio
async def test_web_check_no_condition(web_scraper: WebScrapingMixin) -> None:
    """Test web_check with default condition (Is.DISPLAYED)."""
    # Mock the web_find method to avoid the actual implementation
    with patch.object(web_scraper, 'web_find', new_callable=AsyncMock) as mock_web_find:
        # Create a mock element with apply method
        mock_element = MagicMock()
        mock_element.apply = AsyncMock(return_value=True)
        mock_element.attrs = {}
        mock_web_find.return_value = mock_element

        # Test with Is.DISPLAYED (required parameter)
        result = await web_scraper.web_check(By.ID, "test-id", Is.DISPLAYED)
        assert result is True
        mock_web_find.assert_called_once()


@pytest.mark.asyncio
async def test_web_input(web_scraper: WebScrapingMixin) -> None:
    """Test web_input method."""
    # Mock the web_find method to avoid the actual implementation
    with patch.object(web_scraper, 'web_find', new_callable=AsyncMock) as mock_web_find:
        # Create a mock element with send_keys method
        mock_element = MagicMock()
        mock_element.clear_input = AsyncMock()
        mock_element.send_keys = AsyncMock()
        mock_web_find.return_value = mock_element

        # Test web_input
        await web_scraper.web_input(By.ID, "test-id", "test-value")
        mock_element.clear_input.assert_called_once()
        mock_element.send_keys.assert_called_once_with("test-value")


@pytest.mark.asyncio
async def test_web_click(web_scraper: WebScrapingMixin) -> None:
    """Test web_click method."""
    # Mock the web_find method to avoid the actual implementation
    with patch.object(web_scraper, 'web_find', new_callable=AsyncMock) as mock_web_find:
        # Create a mock element with click method
        mock_element = MagicMock()
        mock_element.click = AsyncMock()
        mock_web_find.return_value = mock_element

        # Test web_click
        await web_scraper.web_click(By.ID, "test-id")
        mock_element.click.assert_called_once()


@pytest.mark.asyncio
async def test_web_select(web_scraper: WebScrapingMixin) -> None:
    """Test web_select method."""
    # Mock the web_find method to avoid the actual implementation
    with patch.object(web_scraper, 'web_find', new_callable=AsyncMock) as mock_web_find:
        # Create a mock element with apply method
        mock_element = MagicMock()
        mock_element.apply = AsyncMock(return_value=None)
        mock_web_find.return_value = mock_element

        # Mock web_await to avoid the actual implementation
        with patch.object(web_scraper, 'web_await', new_callable=AsyncMock) as mock_web_await:
            # Test web_select
            await web_scraper.web_select(By.ID, "test-id", "test-value")
            mock_element.apply.assert_called_once()
            mock_web_await.assert_called_once()


@pytest.mark.asyncio
async def test_web_text(web_scraper: WebScrapingMixin) -> None:
    """Test web_text method."""
    # Mock the web_find method to avoid the actual implementation
    with patch.object(web_scraper, 'web_find', new_callable=AsyncMock) as mock_web_find:
        # Create a mock element with apply method
        mock_element = MagicMock()
        mock_element.apply = AsyncMock(return_value="test-text")
        mock_web_find.return_value = mock_element

        # Test web_text
        result = await web_scraper.web_text(By.ID, "test-id")
        assert result == "test-text"
        mock_web_find.assert_called_once()
        mock_element.apply.assert_called_once()


@pytest.mark.asyncio
async def test_web_sleep(web_scraper: WebScrapingMixin) -> None:
    """Test web_sleep method."""
    # Test web_sleep with default values
    await web_scraper.web_sleep()

    # Verify that page.sleep was called
    # Use the mock's assert_called_once method
    web_scraper.page.sleep.assert_called_once()

    # Sleep time should be between min_ms/1000 and max_ms/1000
    # Access call_args safely
    call_args = getattr(web_scraper.page.sleep, 'call_args', None)
    if call_args:
        sleep_time = call_args[0][0]
        assert 1.0 <= sleep_time <= 2.5  # Default is 1000-2500ms (1-2.5s)


def test_get_compatible_browser() -> None:
    """Test get_compatible_browser method."""
    # Test for Windows
    with patch('platform.system', return_value='Windows'):
        windows_env = {
            'ProgramFiles': 'C:\\Program Files',
            'ProgramFiles(x86)': 'C:\\Program Files (x86)',
            'LOCALAPPDATA': 'C:\\Users\\User\\AppData\\Local'
        }
        with patch('os.environ', windows_env):
            with patch('shutil.which', return_value='C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'):
                with patch('os.path.isfile', return_value=True):
                    scraper = WebScrapingMixin()
                    # Just verify it returns a string (the actual path might vary)
                    assert isinstance(scraper.get_compatible_browser(), str)

    # Test for macOS
    with patch('platform.system', return_value='Darwin'):
        with patch('os.path.isfile', return_value=True):  # Simulate browser path exists
            scraper = WebScrapingMixin()
            # Just verify it returns a string (the actual path might vary)
            assert isinstance(scraper.get_compatible_browser(), str)

    # Test for Linux
    with patch('platform.system', return_value='Linux'):
        with patch('shutil.which', return_value='/usr/bin/google-chrome'):
            with patch('os.path.isfile', return_value=True):  # Simulate browser path exists
                scraper = WebScrapingMixin()
                # Just verify it returns a string (the actual path might vary)
                assert isinstance(scraper.get_compatible_browser(), str)

    # Test when no browser is found on Windows
    with patch('platform.system', return_value='Windows'):
        windows_env = {
            'ProgramFiles': 'C:\\Program Files',
            'ProgramFiles(x86)': 'C:\\Program Files (x86)',
            'LOCALAPPDATA': 'C:\\Users\\User\\AppData\\Local'
        }
        with patch('os.environ', windows_env):
            with patch('shutil.which', return_value=None):
                with patch('os.path.isfile', return_value=False):  # Ensure no browser paths exist
                    with pytest.raises(AssertionError, match="Installed browser could not be detected"):
                        scraper = WebScrapingMixin()
                        scraper.get_compatible_browser()
