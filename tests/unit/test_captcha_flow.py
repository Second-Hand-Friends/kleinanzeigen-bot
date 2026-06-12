# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Tests for captcha flow functionality."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kleinanzeigen_bot import KleinanzeigenBot, captcha_flow
from kleinanzeigen_bot.model.config_model import CaptchaConfig
from kleinanzeigen_bot.utils.exceptions import CaptchaEncountered


class TestCheckAndWaitForCaptcha:
    """Tests for captcha detection and waiting."""

    @pytest.mark.asyncio
    async def test_check_and_wait_for_captcha(self, test_bot:KleinanzeigenBot) -> None:
        """Verify that captcha detection works correctly."""
        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock) as mock_probe,
            patch("kleinanzeigen_bot.captcha_flow.ainput", new_callable = AsyncMock) as mock_ainput,
        ):
            # Test case 1: Captcha found
            mock_probe.return_value = MagicMock()
            mock_ainput.return_value = ""

            await captcha_flow.check_and_wait_for_captcha(test_bot, test_bot.config.captcha, is_login_page = True)

            mock_ainput.assert_awaited_once()

            # Test case 2: No captcha
            mock_probe.return_value = None
            mock_ainput.reset_mock()

            await captcha_flow.check_and_wait_for_captcha(test_bot, test_bot.config.captcha, is_login_page = True)

            mock_ainput.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_captcha_found_non_login_with_auto_restart(self, test_bot:KleinanzeigenBot) -> None:
        """Auto-restart raises CaptchaEncountered on non-login page captcha."""
        captcha_config = CaptchaConfig.model_validate({"auto_restart": True, "restart_delay": "6h"})
        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock) as mock_probe,
            patch("kleinanzeigen_bot.captcha_flow.ainput", new_callable = AsyncMock) as mock_ainput,
        ):
            mock_probe.return_value = MagicMock()
            mock_ainput.return_value = ""

            with pytest.raises(CaptchaEncountered):
                await captcha_flow.check_and_wait_for_captcha(
                    test_bot, captcha_config, is_login_page = False,
                )

            mock_ainput.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_captcha_found_non_login_without_auto_restart(self, test_bot:KleinanzeigenBot) -> None:
        """Without auto-restart, non-login captcha scrolls and prompts."""
        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock) as mock_probe,
            patch("kleinanzeigen_bot.captcha_flow.ainput", new_callable = AsyncMock) as mock_ainput,
            patch.object(test_bot, "web_scroll_page_down", new_callable = AsyncMock) as mock_scroll,
        ):
            mock_probe.return_value = MagicMock()
            mock_ainput.return_value = ""

            await captcha_flow.check_and_wait_for_captcha(
                test_bot, test_bot.config.captcha, is_login_page = False,
            )

            mock_scroll.assert_awaited_once()
            mock_ainput.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_captcha_found_login_page_prompts_without_scroll(self, test_bot:KleinanzeigenBot) -> None:
        """Login page captcha prompts user but does not scroll."""
        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock) as mock_probe,
            patch("kleinanzeigen_bot.captcha_flow.ainput", new_callable = AsyncMock) as mock_ainput,
            patch.object(test_bot, "web_scroll_page_down", new_callable = AsyncMock) as mock_scroll,
        ):
            mock_probe.return_value = MagicMock()
            mock_ainput.return_value = ""

            await captcha_flow.check_and_wait_for_captcha(
                test_bot, test_bot.config.captcha, is_login_page = True,
            )

            mock_ainput.assert_awaited_once()
            mock_scroll.assert_not_awaited()
