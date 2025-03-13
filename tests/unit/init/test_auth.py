"""
SPDX-FileCopyrightText: © Jens Bergmann and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

Tests for authentication functionality in KleinanzeigenBot.
"""

from typing import Any, Awaitable, Callable, Optional
from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import KleinanzeigenBotProtocol


def create_awaitable_mock(return_value: Any = None, side_effect: Any = None) -> AsyncMock:
    """Create a mock that can be awaited."""
    mock = AsyncMock()
    if return_value is not None:
        mock.return_value = return_value
    if side_effect is not None:
        mock.side_effect = side_effect
    return mock


@pytest.fixture
def configured_bot(test_bot: KleinanzeigenBotProtocol, sample_config: dict[str, Any]) -> KleinanzeigenBotProtocol:
    """Return a configured bot for testing."""
    test_bot.config = sample_config
    return test_bot


@pytest.mark.asyncio
async def test_assert_free_ad_limit_not_reached_success(configured_bot: KleinanzeigenBotProtocol) -> None:
    """Test that assert_free_ad_limit_not_reached succeeds when no limit message is found."""
    # Setup
    configured_bot.page = AsyncMock()
    configured_bot.page.evaluate = AsyncMock(return_value={"statusCode": 200, "content": "{}"})

    # Mock web_find to simulate no limit message found
    with patch.object(configured_bot, 'web_find', side_effect=TimeoutError("Not found")):
        # Execute - should not raise an exception
        await configured_bot.assert_free_ad_limit_not_reached()


@pytest.mark.asyncio
async def test_assert_free_ad_limit_not_reached_limit_reached(configured_bot: KleinanzeigenBotProtocol) -> None:
    """Test that assert_free_ad_limit_not_reached raises SystemExit when limit message is found."""
    # Setup
    configured_bot.page = AsyncMock()
    configured_bot.page.evaluate = AsyncMock(return_value={"statusCode": 200, "content": "{}"})

    # Mock web_find to simulate limit message found
    with patch.object(configured_bot, 'web_find', return_value=AsyncMock()):
        # Execute - should raise AssertionError
        with pytest.raises(AssertionError):
            await configured_bot.assert_free_ad_limit_not_reached()


@pytest.mark.asyncio
async def test_is_logged_in_returns_true_when_logged_in(configured_bot: KleinanzeigenBotProtocol) -> None:
    """Test that is_logged_in returns True when user is logged in."""
    # Setup
    configured_bot.page = AsyncMock()
    configured_bot.page.evaluate = AsyncMock(return_value={"statusCode": 200, "content": "{}"})

    # Mock web_text to simulate logged-in state
    with patch.object(configured_bot, 'web_text', create_awaitable_mock(return_value="test_user")):
        # Execute
        result = await configured_bot.is_logged_in()

        # Verify
        assert result is True


@pytest.mark.asyncio
async def test_is_logged_in_returns_false_when_not_logged_in(configured_bot: KleinanzeigenBotProtocol) -> None:
    """Test that is_logged_in returns False when user is not logged in."""
    # Setup
    configured_bot.page = AsyncMock()
    configured_bot.page.evaluate = AsyncMock(return_value={"statusCode": 200, "content": "{}"})

    # Mock web_text to simulate not logged-in state
    with patch.object(configured_bot, 'web_text', side_effect=TimeoutError("Not found")):
        # Execute
        result = await configured_bot.is_logged_in()

        # Verify
        assert result is False


@pytest.mark.asyncio
async def test_login_flow_completes_successfully(configured_bot: KleinanzeigenBotProtocol) -> None:
    """Test that login flow completes successfully."""
    # Setup
    configured_bot.page = AsyncMock()
    configured_bot.page.evaluate = AsyncMock(return_value={"statusCode": 200, "content": "{}"})

    # Create properly typed mocks
    is_logged_in_mock = AsyncMock(side_effect=[False, True])
    web_open_mock = AsyncMock()
    web_find_mock = AsyncMock(side_effect=TimeoutError("Not found"))
    fill_login_data_mock = AsyncMock()
    handle_after_login_mock = AsyncMock()

    # Mock methods
    with patch.object(configured_bot, 'is_logged_in', is_logged_in_mock), \
            patch.object(configured_bot, 'web_open', web_open_mock), \
            patch.object(configured_bot, 'web_find', web_find_mock), \
            patch.object(configured_bot, 'fill_login_data_and_send', fill_login_data_mock), \
            patch.object(configured_bot, 'handle_after_login_logic', handle_after_login_mock):

        # Execute
        await configured_bot.login()

        # Verify
        assert web_open_mock.called
        assert fill_login_data_mock.called
        assert handle_after_login_mock.called


@pytest.mark.asyncio
async def test_login_flow_handles_captcha(configured_bot: KleinanzeigenBotProtocol) -> None:
    """Test that login flow handles captcha correctly."""
    # Setup
    configured_bot.page = AsyncMock()
    configured_bot.page.evaluate = AsyncMock(return_value={"statusCode": 200, "content": "{}"})

    # Create properly typed mocks
    is_logged_in_mock = AsyncMock(side_effect=[False, True])
    web_open_mock = AsyncMock()
    web_find_mock = AsyncMock()
    web_await_mock = AsyncMock()
    fill_login_data_mock = AsyncMock()
    handle_after_login_mock = AsyncMock()

    # Mock methods
    with patch.object(configured_bot, 'is_logged_in', is_logged_in_mock), \
            patch.object(configured_bot, 'web_open', web_open_mock), \
            patch.object(configured_bot, 'web_find', web_find_mock), \
            patch.object(configured_bot, 'web_await', web_await_mock), \
            patch.object(configured_bot, 'fill_login_data_and_send', fill_login_data_mock), \
            patch.object(configured_bot, 'handle_after_login_logic', handle_after_login_mock):

        # Execute
        await configured_bot.login()

        # Verify
        assert web_open_mock.called
        assert web_await_mock.called
        assert fill_login_data_mock.called
        assert handle_after_login_mock.called


@pytest.mark.asyncio
async def test_login_with_valid_credentials(test_bot: KleinanzeigenBotProtocol) -> None:
    """Test login with valid credentials."""
    # Setup
    test_bot.config = {
        "login": {
            "username": "test_user",
            "password": "test_pass"
        }
    }

    # Create properly typed mocks
    is_logged_in_mock = AsyncMock(return_value=True)
    web_open_mock = AsyncMock()

    # Mock is_logged_in to return True to simulate already logged in
    with patch.object(test_bot, 'is_logged_in', is_logged_in_mock), \
            patch.object(test_bot, 'web_open', web_open_mock):

        # Execute
        await test_bot.login()

        # Verify - should not call fill_login_data_and_send since already logged in
        assert web_open_mock.called


@pytest.mark.asyncio
async def test_login_with_invalid_credentials(test_bot: KleinanzeigenBotProtocol) -> None:
    """Test login with invalid credentials.

    This test verifies that the login flow correctly handles invalid credentials
    by checking that a second login attempt is made.
    """
    # Setup
    test_bot.config = {
        "login": {
            "username": "test_user",
            "password": "wrong_pass"
        }
    }

    # Create a more controlled test environment
    # First call to is_logged_in returns False (not logged in initially)
    # Second call also returns False (login failed)
    is_logged_in_mock = AsyncMock(side_effect=[False, False])

    # Track calls to fill_login_data_and_send to verify it's called twice
    fill_login_data_mock = AsyncMock()

    # Other required mocks
    web_open_mock = AsyncMock()
    handle_after_login_mock = AsyncMock()
    web_find_mock = AsyncMock(side_effect=TimeoutError("Not found"))  # No captcha

    with patch.object(test_bot, 'is_logged_in', is_logged_in_mock), \
            patch.object(test_bot, 'web_open', web_open_mock), \
            patch.object(test_bot, 'web_find', web_find_mock), \
            patch.object(test_bot, 'fill_login_data_and_send', fill_login_data_mock), \
            patch.object(test_bot, 'handle_after_login_logic', handle_after_login_mock):

        # Execute
        await test_bot.login()

        # Verify that fill_login_data_and_send was called twice (retry attempt)
        assert fill_login_data_mock.await_count == 2
        assert handle_after_login_mock.await_count == 2
        assert is_logged_in_mock.await_count == 2


@pytest.mark.asyncio
async def test_fill_login_data_and_send(test_bot: KleinanzeigenBotProtocol) -> None:
    """Test filling login data and submitting the form."""
    # Setup
    test_bot.config = {
        "login": {
            "username": "test_user",
            "password": "test_pass"
        }
    }

    # Mock web methods
    with patch.object(test_bot, 'web_input', create_awaitable_mock()) as mock_input, \
            patch.object(test_bot, 'web_click', create_awaitable_mock()) as mock_click:

        # Execute
        await test_bot.fill_login_data_and_send()

        # Verify
        assert mock_input.await_count == 2
        mock_click.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_after_login_logic(test_bot: KleinanzeigenBotProtocol) -> None:
    """Test handling post-login logic."""
    # Create properly typed mocks
    ainput_mock = AsyncMock()
    web_find_mock = AsyncMock()
    web_click_mock = AsyncMock()

    # Setup - patch the ainput function to avoid waiting for user input
    with patch('kleinanzeigen_bot.ainput', ainput_mock), \
            patch.object(test_bot, 'web_find', web_find_mock), \
            patch.object(test_bot, 'web_click', web_click_mock):

        # Execute
        await test_bot.handle_after_login_logic()

        # Verify
        assert web_click_mock.called


@pytest.mark.asyncio
async def test_handle_after_login_logic_no_cookie_banner(test_bot: KleinanzeigenBotProtocol) -> None:
    """Test handling post-login logic when no cookie banner is present."""
    # Setup - patch the ainput function to avoid waiting for user input
    with patch('kleinanzeigen_bot.ainput', create_awaitable_mock()), \
            patch.object(test_bot, 'web_find', side_effect=TimeoutError("Not found")):

        # Execute
        await test_bot.handle_after_login_logic()

        # No assertions needed - test passes if no exception is raised
