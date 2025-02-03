"""
SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""
import pytest
from kleinanzeigen_bot import KleinanzeigenBot

class TestKleinanzeigenBot:
    @pytest.fixture
    def bot(self) -> KleinanzeigenBot:
        return KleinanzeigenBot()

    def test_parse_args_help(self, bot: KleinanzeigenBot) -> None:
        """Test parsing of help command"""
        bot.parse_args(["app", "help"])
        assert bot.command == "help"
        assert bot.ads_selector == "due"
        assert not bot.keep_old_ads

    def test_parse_args_publish(self, bot: KleinanzeigenBot) -> None:
        """Test parsing of publish command with options"""
        bot.parse_args(["app", "publish", "--ads=all", "--keep-old"])
        assert bot.command == "publish"
        assert bot.ads_selector == "all"
        assert bot.keep_old_ads

    def test_get_version(self, bot: KleinanzeigenBot) -> None:
        """Test version retrieval"""
        version = bot.get_version()
        assert isinstance(version, str)
        assert len(version) > 0