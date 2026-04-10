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

    def test_create_default_config_preserves_existing_file(self, tmp_path:pathlib.Path, bot:KleinanzeigenBot) -> None:
        """Test that create_default_config does not overwrite an existing config file."""
        config_path = tmp_path / "config.yaml"
        original_content = "dummy: value"
        config_path.write_text(original_content)
        bot.config_file_path = str(config_path)
        bot.create_default_config()
        assert config_path.read_text() == original_content

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
