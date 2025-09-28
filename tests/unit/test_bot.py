# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import gc, pytest  # isort: skip
import pathlib

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

    def test_parse_args_create_config(self, bot:KleinanzeigenBot) -> None:
        """Test parsing of create-config command"""
        bot.parse_args(["app", "create-config"])
        assert bot.command == "create-config"

    def test_parse_args_message(self, bot:KleinanzeigenBot) -> None:
        """Test parsing of message command with required options"""
        bot.parse_args([
            "app",
            "message",
            "--url",
            "https://example.com/listing",
            "--text",
            "Hello there",
        ])
        assert bot.command == "message"
        assert bot.message_url == "https://example.com/listing"
        assert bot.message_text == "Hello there"

    def test_parse_args_message_requires_url_and_text(self, bot:KleinanzeigenBot) -> None:
        """Ensure message command exits when required options are missing"""
        with pytest.raises(SystemExit):
            bot.parse_args(["app", "message"])

    def test_parse_args_fetch_conversations_with_limit(self, bot:KleinanzeigenBot) -> None:
        """Ensure fetch-conversations applies the provided limit"""
        bot.parse_args(["app", "fetch-conversations", "--limit=7"])
        assert bot.command == "fetch-conversations"
        assert bot.conversation_limit == 7

    def test_parse_args_fetch_conversations_invalid_limit(self, bot:KleinanzeigenBot) -> None:
        """Invalid limits should exit"""
        with pytest.raises(SystemExit):
            bot.parse_args(["app", "fetch-conversations", "--limit=0"])

    def test_parse_args_fetch_conversation(self, bot:KleinanzeigenBot) -> None:
        """Ensure fetch-conversation command records the conversation id"""
        bot.parse_args(["app", "fetch-conversation", "--conversation-id", "abc123"])
        assert bot.command == "fetch-conversation"
        assert bot.conversation_id == "abc123"

    def test_parse_args_fetch_conversation_requires_id(self, bot:KleinanzeigenBot) -> None:
        """Missing conversation id should exit"""
        with pytest.raises(SystemExit):
            bot.parse_args(["app", "fetch-conversation"])

    def test_parse_args_fetch_conversations(self, bot:KleinanzeigenBot) -> None:
        """Test parsing of fetch-conversations command with custom limit"""
        bot.parse_args(["app", "fetch-conversations", "--limit=5"])
        assert bot.command == "fetch-conversations"
        assert bot.conversation_limit == 5

    def test_parse_args_fetch_conversations_requires_positive_limit(self, bot:KleinanzeigenBot) -> None:
        """Ensure fetch-conversations command validates positive limit"""
        with pytest.raises(SystemExit):
            bot.parse_args(["app", "fetch-conversations", "--limit=0"])

    def test_create_default_config_logs_error_if_exists(self, tmp_path:pathlib.Path, bot:KleinanzeigenBot, caplog:pytest.LogCaptureFixture) -> None:
        """Test that create_default_config logs an error if the config file already exists."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text("dummy: value")
        bot.config_file_path = str(config_path)
        with caplog.at_level("ERROR"):
            bot.create_default_config()
        assert any("already exists" in m for m in caplog.messages)

    def test_create_default_config_creates_file(self, tmp_path:pathlib.Path, bot:KleinanzeigenBot) -> None:
        """Test that create_default_config creates a config file if it does not exist."""
        config_path = tmp_path / "config.yaml"
        bot.config_file_path = str(config_path)
        assert not config_path.exists()
        bot.create_default_config()
        assert config_path.exists()
        content = config_path.read_text()
        assert "username: changeme" in content

    def test_load_config_handles_missing_file(self, tmp_path:pathlib.Path, bot:KleinanzeigenBot) -> None:
        """Test that load_config creates a default config file if missing. No info log is expected anymore."""
        config_path = tmp_path / "config.yaml"
        bot.config_file_path = str(config_path)
        bot.load_config()
        assert config_path.exists()

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
