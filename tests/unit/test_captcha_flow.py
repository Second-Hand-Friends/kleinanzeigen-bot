# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Tests for captcha flow functionality."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kleinanzeigen_bot import KleinanzeigenBot, captcha_flow
from kleinanzeigen_bot.model.config_model import CaptchaConfig
from kleinanzeigen_bot.utils.exceptions import CaptchaEncountered


class TestCheckAndWaitForCaptcha:
    """Tests for captcha detection and waiting."""

    @pytest.mark.asyncio
    async def test_captcha_found_prompts_user_on_login_page(self, test_bot:KleinanzeigenBot) -> None:
        """Captcha found on login page prompts user for manual solving."""
        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock) as mock_probe,
            patch("kleinanzeigen_bot.captcha_flow.ainput", new_callable = AsyncMock) as mock_ainput,
        ):
            mock_probe.return_value = MagicMock()
            mock_ainput.return_value = ""

            await captcha_flow.check_and_wait_for_captcha(test_bot, test_bot.config.captcha, is_login_page = True)

            mock_ainput.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_captcha_returns_silently(self, test_bot:KleinanzeigenBot) -> None:
        """No captcha detected skips prompt and returns silently."""
        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock) as mock_probe,
            patch("kleinanzeigen_bot.captcha_flow.ainput", new_callable = AsyncMock) as mock_ainput,
        ):
            mock_probe.return_value = None

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

            with pytest.raises(CaptchaEncountered) as exc:
                await captcha_flow.check_and_wait_for_captcha(
                    test_bot, captcha_config, is_login_page = False,
                )

            assert exc.value.restart_delay == timedelta(hours = 6)
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
