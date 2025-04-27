# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import gc, pytest  # isort: skip

from kleinanzeigen_bot import KleinanzeigenBot


class TestKleinanzeigenBot:

    @pytest.fixture
    def bot(self) -> KleinanzeigenBot:
        return KleinanzeigenBot()

    def test_parse_args_help(self, bot:KleinanzeigenBot) -> None:
        """Test parsing of help command"""
        bot.parse_args(["app", "help"])
        assert bot.command == "help"
        assert bot.ads_selector == "due"
        assert not bot.keep_old_ads

    def test_parse_args_publish(self, bot:KleinanzeigenBot) -> None:
        """Test parsing of publish command with options"""
        bot.parse_args(["app", "publish", "--ads=all", "--keep-old"])
        assert bot.command == "publish"
        assert bot.ads_selector == "all"
        assert bot.keep_old_ads

    def test_get_version(self, bot:KleinanzeigenBot) -> None:
        """Test version retrieval"""
        version = bot.get_version()
        assert isinstance(version, str)
        assert len(version) > 0

    def test_file_log_closed_after_bot_shutdown(self) -> None:
        """Ensure the file log handler is properly closed after the bot is deleted"""

        # Directly instantiate the bot to control its lifecycle within the test
        bot = KleinanzeigenBot()

        bot.configure_file_logging()
        file_log = bot.file_log
        assert file_log is not None
        assert not file_log.is_closed()

        # Delete and garbage collect the bot instance to ensure the destructor (__del__) is called
        del bot
        gc.collect()

        assert file_log.is_closed()
