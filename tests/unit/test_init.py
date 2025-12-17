# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import copy, io, json, logging, os, tempfile  # isort: skip
from collections.abc import Generator
from contextlib import redirect_stdout
from datetime import timedelta
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from kleinanzeigen_bot import LOG, AdUpdateStrategy, KleinanzeigenBot, misc
from kleinanzeigen_bot._version import __version__
from kleinanzeigen_bot.model.ad_model import Ad
from kleinanzeigen_bot.model.config_model import AdDefaults, Config, PublishingConfig
from kleinanzeigen_bot.utils import dicts, loggers
from kleinanzeigen_bot.utils.web_scraping_mixin import By, Element


@pytest.fixture
def mock_page() -> MagicMock:
    """Provide a mock page object for testing."""
    mock = MagicMock()
    mock.sleep = AsyncMock()
    mock.evaluate = AsyncMock()
    mock.click = AsyncMock()
    mock.type = AsyncMock()
    mock.select = AsyncMock()
    mock.wait_for_selector = AsyncMock()
    mock.wait_for_navigation = AsyncMock()
    mock.wait_for_load_state = AsyncMock()
    mock.content = AsyncMock(return_value = "<html></html>")
    mock.goto = AsyncMock()
    mock.close = AsyncMock()
    return mock


@pytest.fixture
def base_ad_config() -> dict[str, Any]:
    """Provide a base ad configuration that can be used across tests."""
    return {
        "id": None,
        "title": "Test Title",
        "description": "Test Description",
        "type": "OFFER",
        "price_type": "FIXED",
        "price": 100,
        "shipping_type": "SHIPPING",
        "shipping_options": [],
        "category": "160",
        "special_attributes": {},
        "sell_directly": False,
        "images": [],
        "active": True,
        "republication_interval": 7,
        "created_on": None,
        "contact": {
            "name": "Test User",
            "zipcode": "12345",
            "location": "Test City",
            "street": "",
            "phone": ""
        }
    }


def remove_fields(config:dict[str, Any], *fields:str) -> dict[str, Any]:
    """Create a new ad configuration with specified fields removed.

    Args:
        config: The configuration to remove fields from
        *fields: Field names to remove

    Returns:
        A new ad configuration dictionary with specified fields removed
    """
    result = copy.deepcopy(config)
    for field in fields:
        if "." in field:
            # Handle nested fields (e.g., "contact.phone")
            parts = field.split(".", maxsplit = 1)
            current = result
            for part in parts[:-1]:
                if part in current:
                    current = current[part]
            if parts[-1] in current:
                del current[parts[-1]]
        elif field in result:
            del result[field]
    return result


@pytest.fixture
def minimal_ad_config(base_ad_config:dict[str, Any]) -> dict[str, Any]:
    """Provide a minimal ad configuration with only required fields."""
    return remove_fields(
        base_ad_config,
        "id",
        "created_on",
        "shipping_options",
        "special_attributes",
        "contact.street",
        "contact.phone"
    )


@pytest.fixture
def mock_config_setup(test_bot:KleinanzeigenBot) -> Generator[None]:
    """Provide a centralized mock configuration setup for tests.
    This fixture mocks load_config and other essential configuration-related methods."""
    with patch.object(test_bot, "load_config"), \
            patch.object(test_bot, "create_browser_session", new_callable = AsyncMock), \
            patch.object(test_bot, "login", new_callable = AsyncMock), \
            patch.object(test_bot, "web_request", new_callable = AsyncMock) as mock_request:
        # Mock the web request for published ads
        mock_request.return_value = {"content": '{"ads": []}'}
        yield


class TestKleinanzeigenBotInitialization:
    """Tests for KleinanzeigenBot initialization and basic functionality."""

    def test_constructor_initializes_default_values(self, test_bot:KleinanzeigenBot) -> None:
        """Verify that constructor sets all default values correctly."""
        assert test_bot.root_url == "https://www.kleinanzeigen.de"
        assert isinstance(test_bot.config, Config)
        assert test_bot.command == "help"
        assert test_bot.ads_selector == "due"
        assert test_bot.keep_old_ads is False
        assert test_bot.log_file_path is not None
        assert test_bot.file_log is None

    def test_get_version_returns_correct_version(self, test_bot:KleinanzeigenBot) -> None:
        """Verify version retrieval works correctly."""
        with patch("kleinanzeigen_bot.__version__", "1.2.3"):
            assert test_bot.get_version() == "1.2.3"


class TestKleinanzeigenBotLogging:
    """Tests for logging functionality."""

    def test_configure_file_logging_adds_and_removes_handlers(
        self,
        test_bot:KleinanzeigenBot,
        tmp_path:Path
    ) -> None:
        """Ensure file logging registers a handler and cleans it up afterward."""
        log_path = tmp_path / "bot.log"
        test_bot.log_file_path = str(log_path)
        root_logger = logging.getLogger()
        initial_handlers = list(root_logger.handlers)

        test_bot.configure_file_logging()

        assert test_bot.file_log is not None
        assert log_path.exists()
        assert len(root_logger.handlers) == len(initial_handlers) + 1

        test_bot.file_log.close()
        assert test_bot.file_log.is_closed()
        assert len(root_logger.handlers) == len(initial_handlers)

    def test_configure_file_logging_skips_when_path_missing(self, test_bot:KleinanzeigenBot) -> None:
        """Ensure no handler is added when no log path is configured."""
        root_logger = logging.getLogger()
        initial_handlers = list(root_logger.handlers)

        test_bot.log_file_path = None
        test_bot.configure_file_logging()

        assert test_bot.file_log is None
        assert list(root_logger.handlers) == initial_handlers


class TestKleinanzeigenBotCommandLine:
    """Tests for command line argument parsing."""

    @pytest.mark.parametrize(("args", "expected_command", "expected_selector", "expected_keep_old"), [
        (["publish", "--ads=all"], "publish", "all", False),
        (["verify"], "verify", "due", False),
        (["download", "--ads=12345"], "download", "12345", False),
        (["publish", "--force"], "publish", "all", False),
        (["publish", "--keep-old"], "publish", "due", True),
        (["publish", "--ads=all", "--keep-old"], "publish", "all", True),
        (["download", "--ads=new"], "download", "new", False),
        (["publish", "--ads=changed"], "publish", "changed", False),
        (["publish", "--ads=changed,due"], "publish", "changed,due", False),
        (["publish", "--ads=changed,new"], "publish", "changed,new", False),
        (["version"], "version", "due", False),
    ])
    def test_parse_args_handles_valid_arguments(
        self,
        test_bot:KleinanzeigenBot,
        args:list[str],
        expected_command:str,
        expected_selector:str,
        expected_keep_old:bool
    ) -> None:
        """Verify that valid command line arguments are parsed correctly."""
        test_bot.parse_args(["dummy"] + args)  # Add dummy arg to simulate sys.argv[0]
        assert test_bot.command == expected_command
        assert test_bot.ads_selector == expected_selector
        assert test_bot.keep_old_ads == expected_keep_old

    def test_parse_args_handles_help_command(self, test_bot:KleinanzeigenBot) -> None:
        """Verify that help command is handled correctly."""
        buf = io.StringIO()
        with pytest.raises(SystemExit) as exc_info, redirect_stdout(buf):
            test_bot.parse_args(["dummy", "--help"])
        assert exc_info.value.code == 0
        stdout = buf.getvalue()
        assert "publish" in stdout
        assert "verify" in stdout
        assert "help" in stdout
        assert "version" in stdout
        assert "--verbose" in stdout

    def test_parse_args_handles_invalid_arguments(self, test_bot:KleinanzeigenBot, caplog:pytest.LogCaptureFixture) -> None:
        """Verify that invalid arguments are handled correctly."""
        caplog.set_level(logging.ERROR)
        with pytest.raises(SystemExit) as exc_info:
            test_bot.parse_args(["dummy", "--invalid-option"])
        assert exc_info.value.code == 2
        assert any(
            record.levelno == logging.ERROR
            and (
                "--invalid-option not recognized" in record.getMessage()
                or "Option --invalid-option unbekannt" in record.getMessage()
            )
            for record in caplog.records
        )

        assert any(
            ("--invalid-option not recognized" in m)
            or ("Option --invalid-option unbekannt" in m)
            for m in caplog.messages
        )

    def test_parse_args_handles_verbose_flag(self, test_bot:KleinanzeigenBot) -> None:
        """Verify that verbose flag sets correct log level."""
        test_bot.parse_args(["dummy", "--verbose"])
        assert loggers.is_debug(LOG)

    def test_parse_args_handles_config_path(self, test_bot:KleinanzeigenBot, test_data_dir:str) -> None:
        """Verify that config path is set correctly."""
        config_path = Path(test_data_dir) / "custom_config.yaml"
        test_bot.parse_args(["dummy", "--config", str(config_path)])
        assert test_bot.config_file_path == str(config_path.absolute())


class TestKleinanzeigenBotConfiguration:
    """Tests for configuration loading and validation."""

    def test_load_config_handles_missing_file(
        self,
        test_bot:KleinanzeigenBot,
        test_data_dir:str
    ) -> None:
        """Verify that loading a missing config file creates default config. No info log is expected anymore."""
        config_path = Path(test_data_dir) / "missing_config.yaml"
        config_path.unlink(missing_ok = True)
        test_bot.config_file_path = str(config_path)
        test_bot.load_config()
        assert config_path.exists()

    def test_load_config_validates_required_fields(self, test_bot:KleinanzeigenBot, test_data_dir:str) -> None:
        """Verify that config validation checks required fields."""
        config_path = Path(test_data_dir) / "config.yaml"
        config_content = """
login:
  username: dummy_user
  # Missing password
"""
        with open(config_path, "w", encoding = "utf-8") as f:
            f.write(config_content)
        test_bot.config_file_path = str(config_path)

        with pytest.raises(ValidationError) as exc_info:
            test_bot.load_config()
        assert "login.username" not in str(exc_info.value)
        assert "login.password" in str(exc_info.value)


class TestKleinanzeigenBotAuthentication:
    """Tests for login and authentication functionality."""

    @pytest.mark.asyncio
    async def test_is_logged_in_returns_true_when_logged_in(self, test_bot:KleinanzeigenBot) -> None:
        """Verify that login check returns true when logged in."""
        with patch.object(test_bot, "web_text", return_value = "Welcome dummy_user"):
            assert await test_bot.is_logged_in() is True

    @pytest.mark.asyncio
    async def test_is_logged_in_returns_true_with_alternative_element(self, test_bot:KleinanzeigenBot) -> None:
        """Verify that login check returns true when logged in with alternative element."""
        with patch.object(test_bot, "web_text", side_effect = [
            TimeoutError(),  # First try with mr-medium fails
            "angemeldet als: dummy_user"  # Second try with user-email succeeds
        ]):
            assert await test_bot.is_logged_in() is True

    @pytest.mark.asyncio
    async def test_is_logged_in_returns_false_when_not_logged_in(self, test_bot:KleinanzeigenBot) -> None:
        """Verify that login check returns false when not logged in."""
        with patch.object(test_bot, "web_text", side_effect = TimeoutError):
            assert await test_bot.is_logged_in() is False

    @pytest.mark.asyncio
    async def test_login_flow_completes_successfully(self, test_bot:KleinanzeigenBot) -> None:
        """Verify that normal login flow completes successfully."""
        with patch.object(test_bot, "web_open") as mock_open, \
                patch.object(test_bot, "is_logged_in", side_effect = [False, True]) as mock_logged_in, \
                patch.object(test_bot, "web_find", side_effect = TimeoutError), \
                patch.object(test_bot, "web_input") as mock_input, \
                patch.object(test_bot, "web_click") as mock_click:

            await test_bot.login()

            mock_open.assert_called()
            mock_logged_in.assert_called()
            mock_input.assert_called()
            mock_click.assert_called()

    @pytest.mark.asyncio
    async def test_login_flow_handles_captcha(self, test_bot:KleinanzeigenBot) -> None:
        """Verify that login flow handles captcha correctly."""
        with patch.object(test_bot, "web_open"), \
                patch.object(test_bot, "is_logged_in", side_effect = [False, False, True]), \
                patch.object(test_bot, "web_find") as mock_find, \
                patch.object(test_bot, "web_input") as mock_input, \
                patch.object(test_bot, "web_click") as mock_click, \
                patch("kleinanzeigen_bot.ainput", new_callable = AsyncMock) as mock_ainput:

            # Mock the sequence of web_find calls:
            # First login attempt:
            # 1. Captcha iframe found (in check_and_wait_for_captcha)
            # 2. Phone verification not found (in handle_after_login_logic)
            # 3. GDPR banner not found (in handle_after_login_logic)
            # Second login attempt:
            # 4. Captcha iframe found (in check_and_wait_for_captcha)
            # 5. Phone verification not found (in handle_after_login_logic)
            # 6. GDPR banner not found (in handle_after_login_logic)
            mock_find.side_effect = [
                AsyncMock(),  # Captcha iframe (first login)
                TimeoutError(),  # Phone verification (first login)
                TimeoutError(),  # GDPR banner (first login)
                AsyncMock(),  # Captcha iframe (second login)
                TimeoutError(),  # Phone verification (second login)
                TimeoutError(),  # GDPR banner (second login)
            ]
            mock_ainput.return_value = ""
            mock_input.return_value = AsyncMock()
            mock_click.return_value = AsyncMock()

            await test_bot.login()

            # Verify the complete flow
            assert mock_find.call_count == 6  # Exactly 6 web_find calls
            assert mock_ainput.call_count == 2  # Two captcha prompts
            assert mock_input.call_count == 6  # Two login attempts with username, clear password, and set password
            assert mock_click.call_count == 2  # Two submit button clicks

    @pytest.mark.asyncio
    async def test_check_and_wait_for_captcha(self, test_bot:KleinanzeigenBot) -> None:
        """Verify that captcha detection works correctly."""
        with patch.object(test_bot, "web_find") as mock_find, \
                patch("kleinanzeigen_bot.ainput", new_callable = AsyncMock) as mock_ainput:

            # Test case 1: Captcha found
            mock_find.return_value = AsyncMock()
            mock_ainput.return_value = ""

            await test_bot.check_and_wait_for_captcha(is_login_page = True)

            assert mock_find.call_count == 1
            assert mock_ainput.call_count == 1

            # Test case 2: No captcha
            mock_find.side_effect = TimeoutError()
            mock_ainput.reset_mock()

            await test_bot.check_and_wait_for_captcha(is_login_page = True)

            assert mock_find.call_count == 2
            assert mock_ainput.call_count == 0

    @pytest.mark.asyncio
    async def test_fill_login_data_and_send(self, test_bot:KleinanzeigenBot) -> None:
        """Verify that login form filling works correctly."""
        with patch.object(test_bot, "web_input") as mock_input, \
                patch.object(test_bot, "web_click") as mock_click, \
                patch.object(test_bot, "check_and_wait_for_captcha", new_callable = AsyncMock) as mock_captcha:

            # Mock successful login form interaction
            mock_input.return_value = AsyncMock()
            mock_click.return_value = AsyncMock()

            await test_bot.fill_login_data_and_send()

            assert mock_captcha.call_count == 1
            assert mock_input.call_count == 3  # Username, clear password, set password
            assert mock_click.call_count == 1  # Submit button

    @pytest.mark.asyncio
    async def test_handle_after_login_logic(self, test_bot:KleinanzeigenBot) -> None:
        """Verify that post-login handling works correctly."""
        with patch.object(test_bot, "web_find") as mock_find, \
                patch.object(test_bot, "web_click") as mock_click, \
                patch("kleinanzeigen_bot.ainput", new_callable = AsyncMock) as mock_ainput:

            # Test case 1: No special handling needed
            mock_find.side_effect = [TimeoutError(), TimeoutError()]  # No phone verification, no GDPR
            mock_click.return_value = AsyncMock()
            mock_ainput.return_value = ""

            await test_bot.handle_after_login_logic()

            assert mock_find.call_count == 2
            assert mock_click.call_count == 0
            assert mock_ainput.call_count == 0

            # Test case 2: Phone verification needed
            mock_find.reset_mock()
            mock_click.reset_mock()
            mock_ainput.reset_mock()
            mock_find.side_effect = [AsyncMock(), TimeoutError()]  # Phone verification found, no GDPR

            await test_bot.handle_after_login_logic()

            assert mock_find.call_count == 2
            assert mock_click.call_count == 0  # No click needed, just wait for user
            assert mock_ainput.call_count == 1  # Wait for user to complete verification

            # Test case 3: GDPR banner present
            mock_find.reset_mock()
            mock_click.reset_mock()
            mock_ainput.reset_mock()
            mock_find.side_effect = [TimeoutError(), AsyncMock()]  # No phone verification, GDPR found

            await test_bot.handle_after_login_logic()

            assert mock_find.call_count == 2
            assert mock_click.call_count == 2  # Click to accept GDPR and continue
            assert mock_ainput.call_count == 0


class TestKleinanzeigenBotLocalization:
    """Tests for localization and help text."""

    def test_show_help_displays_german_text(self, test_bot:KleinanzeigenBot) -> None:
        """Verify that help text is displayed in German when language is German."""
        with patch("kleinanzeigen_bot.get_current_locale") as mock_locale, \
                patch("builtins.print") as mock_print:
            mock_locale.return_value.language = "de"
            test_bot.show_help()
            printed_text = "".join(str(call.args[0]) for call in mock_print.call_args_list)
            assert "Verwendung:" in printed_text
            assert "Befehle:" in printed_text

    def test_show_help_displays_english_text(self, test_bot:KleinanzeigenBot) -> None:
        """Verify that help text is displayed in English when language is English."""
        with patch("kleinanzeigen_bot.get_current_locale") as mock_locale, \
                patch("builtins.print") as mock_print:
            mock_locale.return_value.language = "en"
            test_bot.show_help()
            printed_text = "".join(str(call.args[0]) for call in mock_print.call_args_list)
            assert "Usage:" in printed_text
            assert "Commands:" in printed_text


class TestKleinanzeigenBotBasics:
    """Basic tests for KleinanzeigenBot."""

    def test_get_version(self, test_bot:KleinanzeigenBot) -> None:
        """Test version retrieval."""
        assert test_bot.get_version() == __version__

    @pytest.mark.asyncio
    async def test_publish_ads_triggers_publish_and_cleanup(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        mock_page:MagicMock,
    ) -> None:
        """Simulate publish job wiring without hitting the live site."""
        test_bot.page = mock_page
        test_bot.config.publishing.delete_old_ads = "AFTER_PUBLISH"
        test_bot.keep_old_ads = False

        payload:dict[str, list[Any]] = {"ads": []}
        ad_cfgs:list[tuple[str, Ad, dict[str, Any]]] = [("ad.yaml", Ad.model_validate(base_ad_config), {})]

        with patch.object(test_bot, "web_request", new_callable = AsyncMock, return_value = {"content": json.dumps(payload)}) as web_request_mock, \
                patch.object(test_bot, "publish_ad", new_callable = AsyncMock) as publish_ad_mock, \
                patch.object(test_bot, "web_await", new_callable = AsyncMock, return_value = True) as web_await_mock, \
                patch.object(test_bot, "delete_ad", new_callable = AsyncMock) as delete_ad_mock:

            await test_bot.publish_ads(ad_cfgs)

            web_request_mock.assert_awaited_once_with(f"{test_bot.root_url}/m-meine-anzeigen-verwalten.json?sort=DEFAULT")
            publish_ad_mock.assert_awaited_once_with("ad.yaml", ad_cfgs[0][1], {}, [], AdUpdateStrategy.REPLACE)
            web_await_mock.assert_awaited_once()
            delete_ad_mock.assert_awaited_once_with(ad_cfgs[0][1], [], delete_old_ads_by_title = False)

    def test_get_root_url(self, test_bot:KleinanzeigenBot) -> None:
        """Test root URL retrieval."""
        assert test_bot.root_url == "https://www.kleinanzeigen.de"

    def test_get_config_defaults(self, test_bot:KleinanzeigenBot) -> None:
        """Test default configuration values."""
        assert isinstance(test_bot.config, Config)
        assert test_bot.command == "help"
        assert test_bot.ads_selector == "due"
        assert test_bot.keep_old_ads is False

    def test_get_log_level(self, test_bot:KleinanzeigenBot) -> None:
        """Test log level configuration."""
        # Reset log level to default
        LOG.setLevel(loggers.INFO)
        assert not loggers.is_debug(LOG)
        test_bot.parse_args(["script.py", "-v"])
        assert loggers.is_debug(LOG)

    def test_get_config_file_path(self, test_bot:KleinanzeigenBot) -> None:
        """Test config file path handling."""
        default_path = os.path.abspath("config.yaml")
        assert test_bot.config_file_path == default_path
        test_path = os.path.abspath("custom_config.yaml")
        test_bot.config_file_path = test_path
        assert test_bot.config_file_path == test_path

    def test_get_log_file_path(self, test_bot:KleinanzeigenBot) -> None:
        """Test log file path handling."""
        default_path = os.path.abspath("kleinanzeigen_bot.log")
        assert test_bot.log_file_path == default_path
        test_path = os.path.abspath("custom.log")
        test_bot.log_file_path = test_path
        assert test_bot.log_file_path == test_path

    def test_get_categories(self, test_bot:KleinanzeigenBot) -> None:
        """Test categories handling."""
        test_categories = {"test_cat": "test_id"}
        test_bot.categories = test_categories
        assert test_bot.categories == test_categories


class TestKleinanzeigenBotArgParsing:
    """Tests for command line argument parsing."""

    def test_parse_args_help(self, test_bot:KleinanzeigenBot) -> None:
        """Test parsing help command."""
        test_bot.parse_args(["script.py", "help"])
        assert test_bot.command == "help"

    def test_parse_args_version(self, test_bot:KleinanzeigenBot) -> None:
        """Test parsing version command."""
        test_bot.parse_args(["script.py", "version"])
        assert test_bot.command == "version"

    def test_parse_args_verbose(self, test_bot:KleinanzeigenBot) -> None:
        """Test parsing verbose flag."""
        test_bot.parse_args(["script.py", "-v", "help"])
        assert loggers.is_debug(loggers.get_logger("kleinanzeigen_bot"))

    def test_parse_args_config_path(self, test_bot:KleinanzeigenBot) -> None:
        """Test parsing config path."""
        test_bot.parse_args(["script.py", "--config=test.yaml", "help"])
        assert test_bot.config_file_path is not None
        assert test_bot.config_file_path.endswith("test.yaml")

    def test_parse_args_logfile(self, test_bot:KleinanzeigenBot) -> None:
        """Test parsing log file path."""
        test_bot.parse_args(["script.py", "--logfile=test.log", "help"])
        assert test_bot.log_file_path is not None
        assert "test.log" in test_bot.log_file_path

    def test_parse_args_ads_selector(self, test_bot:KleinanzeigenBot) -> None:
        """Test parsing ads selector."""
        test_bot.parse_args(["script.py", "--ads=all", "publish"])
        assert test_bot.ads_selector == "all"

    def test_parse_args_force(self, test_bot:KleinanzeigenBot) -> None:
        """Test parsing force flag."""
        test_bot.parse_args(["script.py", "--force", "publish"])
        assert test_bot.ads_selector == "all"

    def test_parse_args_keep_old(self, test_bot:KleinanzeigenBot) -> None:
        """Test parsing keep-old flag."""
        test_bot.parse_args(["script.py", "--keep-old", "publish"])
        assert test_bot.keep_old_ads is True

    def test_parse_args_logfile_empty(self, test_bot:KleinanzeigenBot) -> None:
        """Test parsing empty log file path."""
        test_bot.parse_args(["script.py", "--logfile=", "help"])
        assert test_bot.log_file_path is None

    def test_logfile_explicit_flag_set(self, test_bot:KleinanzeigenBot) -> None:
        """Test that log_file_explicitly_provided flag is set when --logfile is provided."""
        test_bot.parse_args(["script.py", "--logfile=custom.log", "help"])
        assert test_bot.log_file_explicitly_provided is True
        assert test_bot.log_file_path is not None
        assert "custom.log" in test_bot.log_file_path

    def test_logfile_explicit_flag_set_even_when_empty(self, test_bot:KleinanzeigenBot) -> None:
        """Test that log_file_explicitly_provided flag is set even when --logfile= is empty."""
        test_bot.parse_args(["script.py", "--logfile=", "help"])
        assert test_bot.log_file_explicitly_provided is True
        assert test_bot.log_file_path is None

    def test_logfile_explicit_flag_not_set_when_not_provided(self, test_bot:KleinanzeigenBot) -> None:
        """Test that log_file_explicitly_provided flag is False when --logfile is not provided."""
        test_bot.parse_args(["script.py", "help"])
        assert test_bot.log_file_explicitly_provided is False

    def test_parse_args_lang_option(self, test_bot:KleinanzeigenBot) -> None:
        """Test parsing language option."""
        test_bot.parse_args(["script.py", "--lang=en", "help"])
        assert test_bot.command == "help"

    def test_parse_args_no_arguments(self, test_bot:KleinanzeigenBot) -> None:
        """Test parsing no arguments defaults to help."""
        test_bot.parse_args(["script.py"])
        assert test_bot.command == "help"

    def test_parse_args_multiple_commands(self, test_bot:KleinanzeigenBot) -> None:
        """Test parsing multiple commands raises error."""
        with pytest.raises(SystemExit) as exc_info:
            test_bot.parse_args(["script.py", "help", "version"])
        assert exc_info.value.code == 2


class TestKleinanzeigenBotCommands:
    """Tests for command execution."""

    @pytest.mark.asyncio
    async def test_run_version_command(self, test_bot:KleinanzeigenBot, capsys:Any) -> None:
        """Test running version command."""
        await test_bot.run(["script.py", "version"])
        captured = capsys.readouterr()
        assert __version__ in captured.out

    @pytest.mark.asyncio
    async def test_run_help_command(self, test_bot:KleinanzeigenBot, capsys:Any) -> None:
        """Test running help command."""
        await test_bot.run(["script.py", "help"])
        captured = capsys.readouterr()
        assert "Usage:" in captured.out

    @pytest.mark.asyncio
    async def test_run_unknown_command(self, test_bot:KleinanzeigenBot) -> None:
        """Test running unknown command."""
        with pytest.raises(SystemExit) as exc_info:
            await test_bot.run(["script.py", "unknown"])
        assert exc_info.value.code == 2

    @pytest.mark.asyncio
    async def test_verify_command(self, test_bot:KleinanzeigenBot, tmp_path:Any) -> None:
        """Test verify command with minimal config."""
        config_path = Path(tmp_path) / "config.yaml"
        config_path.write_text("""
login:
    username: test
    password: test
""", encoding = "utf-8")
        test_bot.config_file_path = str(config_path)
        await test_bot.run(["script.py", "verify"])
        assert test_bot.config.login.username == "test"


class TestKleinanzeigenBotAdOperations:
    """Tests for ad-related operations."""

    @pytest.mark.asyncio
    async def test_run_delete_command_no_ads(self, test_bot:KleinanzeigenBot, mock_config_setup:None) -> None:  # pylint: disable=unused-argument
        """Test running delete command with no ads."""
        with patch.object(test_bot, "load_ads", return_value = []):
            await test_bot.run(["script.py", "delete"])
            assert test_bot.command == "delete"

    @pytest.mark.asyncio
    async def test_run_publish_command_no_ads(self, test_bot:KleinanzeigenBot, mock_config_setup:None) -> None:  # pylint: disable=unused-argument
        """Test running publish command with no ads."""
        with patch.object(test_bot, "load_ads", return_value = []):
            await test_bot.run(["script.py", "publish"])
            assert test_bot.command == "publish"

    @pytest.mark.asyncio
    async def test_run_download_command_default_selector(self, test_bot:KleinanzeigenBot, mock_config_setup:None) -> None:  # pylint: disable=unused-argument
        """Test running download command with default selector."""
        with patch.object(test_bot, "download_ads", new_callable = AsyncMock):
            await test_bot.run(["script.py", "download"])
            assert test_bot.ads_selector == "new"

    def test_load_ads_no_files(self, test_bot:KleinanzeigenBot) -> None:
        """Test loading ads with no files."""
        test_bot.config.ad_files = ["nonexistent/*.yaml"]
        ads = test_bot.load_ads()
        assert len(ads) == 0


class TestKleinanzeigenBotAdManagement:
    """Tests for ad management functionality."""

    @pytest.mark.asyncio
    async def test_download_ads_with_specific_ids(self, test_bot:KleinanzeigenBot, mock_config_setup:None) -> None:  # pylint: disable=unused-argument
        """Test downloading ads with specific IDs."""
        test_bot.ads_selector = "123,456"
        with patch.object(test_bot, "download_ads", new_callable = AsyncMock):
            await test_bot.run(["script.py", "download", "--ads=123,456"])
            assert test_bot.ads_selector == "123,456"

    @pytest.mark.asyncio
    async def test_run_publish_invalid_selector(self, test_bot:KleinanzeigenBot, mock_config_setup:None) -> None:  # pylint: disable=unused-argument
        """Test running publish with invalid selector."""
        with patch.object(test_bot, "load_ads", return_value = []):
            await test_bot.run(["script.py", "publish", "--ads=invalid"])
            assert test_bot.ads_selector == "due"

    @pytest.mark.asyncio
    async def test_run_download_invalid_selector(self, test_bot:KleinanzeigenBot, mock_config_setup:None) -> None:  # pylint: disable=unused-argument
        """Test running download with invalid selector."""
        with patch.object(test_bot, "download_ads", new_callable = AsyncMock):
            await test_bot.run(["script.py", "download", "--ads=invalid"])
            assert test_bot.ads_selector == "new"


class TestKleinanzeigenBotAdConfiguration:
    """Tests for ad configuration functionality."""

    def test_load_config_with_categories(self, test_bot:KleinanzeigenBot, tmp_path:Any) -> None:
        """Test loading config with custom categories."""
        config_path = Path(tmp_path) / "config.yaml"
        with open(config_path, "w", encoding = "utf-8") as f:
            f.write("""
login:
    username: test
    password: test
categories:
    custom_cat: custom_id
""")
        test_bot.config_file_path = str(config_path)
        test_bot.load_config()
        assert "custom_cat" in test_bot.categories
        assert test_bot.categories["custom_cat"] == "custom_id"

    def test_load_ads_with_missing_title(self, test_bot:KleinanzeigenBot, tmp_path:Any, minimal_ad_config:dict[str, Any]) -> None:
        """Test loading ads with missing title."""
        temp_path = Path(tmp_path)
        ad_dir = temp_path / "ads"
        ad_dir.mkdir()
        ad_file = ad_dir / "test_ad.yaml"

        # Create a minimal config with empty title to trigger validation
        ad_cfg = minimal_ad_config | {
            "title": ""
        }
        dicts.save_dict(ad_file, ad_cfg)

        # Set config file path to tmp_path and use relative path for ad_files
        test_bot.config_file_path = str(temp_path / "config.yaml")
        test_bot.config.ad_files = ["ads/*.yaml"]
        with pytest.raises(ValidationError) as exc_info:
            test_bot.load_ads()
        assert "title" in str(exc_info.value)

    def test_load_ads_with_invalid_price_type(self, test_bot:KleinanzeigenBot, tmp_path:Any, minimal_ad_config:dict[str, Any]) -> None:
        """Test loading ads with invalid price type."""
        temp_path = Path(tmp_path)
        ad_dir = temp_path / "ads"
        ad_dir.mkdir()
        ad_file = ad_dir / "test_ad.yaml"

        # Create config with invalid price type
        ad_cfg = minimal_ad_config | {
            "price_type": "INVALID_TYPE"
        }
        dicts.save_dict(ad_file, ad_cfg)

        # Set config file path to tmp_path and use relative path for ad_files
        test_bot.config_file_path = str(temp_path / "config.yaml")
        test_bot.config.ad_files = ["ads/*.yaml"]
        with pytest.raises(ValidationError) as exc_info:
            test_bot.load_ads()
        assert "price_type" in str(exc_info.value)

    def test_load_ads_with_invalid_shipping_type(self, test_bot:KleinanzeigenBot, tmp_path:Any, minimal_ad_config:dict[str, Any]) -> None:
        """Test loading ads with invalid shipping type."""
        temp_path = Path(tmp_path)
        ad_dir = temp_path / "ads"
        ad_dir.mkdir()
        ad_file = ad_dir / "test_ad.yaml"

        # Create config with invalid shipping type
        ad_cfg = minimal_ad_config | {
            "shipping_type": "INVALID_TYPE"
        }
        dicts.save_dict(ad_file, ad_cfg)

        # Set config file path to tmp_path and use relative path for ad_files
        test_bot.config_file_path = str(temp_path / "config.yaml")
        test_bot.config.ad_files = ["ads/*.yaml"]
        with pytest.raises(ValidationError) as exc_info:
            test_bot.load_ads()
        assert "shipping_type" in str(exc_info.value)

    def test_load_ads_with_invalid_price_config(self, test_bot:KleinanzeigenBot, tmp_path:Any, minimal_ad_config:dict[str, Any]) -> None:
        """Test loading ads with invalid price configuration."""
        temp_path = Path(tmp_path)
        ad_dir = temp_path / "ads"
        ad_dir.mkdir()
        ad_file = ad_dir / "test_ad.yaml"

        # Create config with price for GIVE_AWAY type
        ad_cfg = minimal_ad_config | {
            "price_type": "GIVE_AWAY",
            "price": 100  # Price should not be set for GIVE_AWAY
        }
        dicts.save_dict(ad_file, ad_cfg)

        # Set config file path to tmp_path and use relative path for ad_files
        test_bot.config_file_path = str(temp_path / "config.yaml")
        test_bot.config.ad_files = ["ads/*.yaml"]
        with pytest.raises(ValidationError) as exc_info:
            test_bot.load_ads()
        assert "price" in str(exc_info.value)

    def test_load_ads_with_missing_price(self, test_bot:KleinanzeigenBot, tmp_path:Any, minimal_ad_config:dict[str, Any]) -> None:
        """Test loading ads with missing price for FIXED price type."""
        temp_path = Path(tmp_path)
        ad_dir = temp_path / "ads"
        ad_dir.mkdir()
        ad_file = ad_dir / "test_ad.yaml"

        # Create config with FIXED price type but no price
        ad_cfg = minimal_ad_config | {
            "price_type": "FIXED",
            "price": None  # Missing required price for FIXED type
        }
        dicts.save_dict(ad_file, ad_cfg)

        # Set config file path to tmp_path and use relative path for ad_files
        test_bot.config_file_path = str(temp_path / "config.yaml")
        test_bot.config.ad_files = ["ads/*.yaml"]
        with pytest.raises(ValidationError) as exc_info:
            test_bot.load_ads()
        assert "price is required when price_type is FIXED" in str(exc_info.value)


class TestKleinanzeigenBotAdDeletion:
    """Tests for ad deletion functionality."""

    @pytest.mark.asyncio
    async def test_delete_ad_by_title(self, test_bot:KleinanzeigenBot, minimal_ad_config:dict[str, Any]) -> None:
        """Test deleting an ad by title."""
        test_bot.page = MagicMock()
        test_bot.page.evaluate = AsyncMock(return_value = {"statusCode": 200, "content": "{}"})
        test_bot.page.sleep = AsyncMock()

        # Use minimal config since we only need title for deletion by title
        ad_cfg = Ad.model_validate(minimal_ad_config | {
            "title": "Test Title",
            "id": None  # Explicitly set id to None for title-based deletion
        })

        published_ads = [
            {"title": "Test Title", "id": "67890"},
            {"title": "Other Title", "id": "11111"}
        ]

        with patch.object(test_bot, "web_open", new_callable = AsyncMock), \
                patch.object(test_bot, "web_find", new_callable = AsyncMock) as mock_find, \
                patch.object(test_bot, "web_click", new_callable = AsyncMock), \
                patch.object(test_bot, "web_check", new_callable = AsyncMock, return_value = True):
            mock_find.return_value.attrs = {"content": "some-token"}
            result = await test_bot.delete_ad(ad_cfg, published_ads, delete_old_ads_by_title = True)
            assert result is True

    @pytest.mark.asyncio
    async def test_delete_ad_by_id(self, test_bot:KleinanzeigenBot, minimal_ad_config:dict[str, Any]) -> None:
        """Test deleting an ad by ID."""
        test_bot.page = MagicMock()
        test_bot.page.evaluate = AsyncMock(return_value = {"statusCode": 200, "content": "{}"})
        test_bot.page.sleep = AsyncMock()

        # Create config with ID for deletion by ID
        ad_cfg = Ad.model_validate(minimal_ad_config | {
            "id": "12345"  # Fixed: use proper dict key syntax
        })

        published_ads = [
            {"title": "Different Title", "id": "12345"},
            {"title": "Other Title", "id": "11111"}
        ]

        with patch.object(test_bot, "web_open", new_callable = AsyncMock), \
                patch.object(test_bot, "web_find", new_callable = AsyncMock) as mock_find, \
                patch.object(test_bot, "web_click", new_callable = AsyncMock), \
                patch.object(test_bot, "web_check", new_callable = AsyncMock, return_value = True):
            mock_find.return_value.attrs = {"content": "some-token"}
            result = await test_bot.delete_ad(ad_cfg, published_ads, delete_old_ads_by_title = False)
            assert result is True

    @pytest.mark.asyncio
    async def test_delete_ad_by_id_with_non_string_csrf_token(self, test_bot:KleinanzeigenBot, minimal_ad_config:dict[str, Any]) -> None:
        """Test deleting an ad by ID with non-string CSRF token to cover str() conversion."""
        test_bot.page = MagicMock()
        test_bot.page.evaluate = AsyncMock(return_value = {"statusCode": 200, "content": "{}"})
        test_bot.page.sleep = AsyncMock()

        # Create config with ID for deletion by ID
        ad_cfg = Ad.model_validate(minimal_ad_config | {
            "id": "12345"
        })

        published_ads = [
            {"title": "Different Title", "id": "12345"},
            {"title": "Other Title", "id": "11111"}
        ]

        with patch.object(test_bot, "web_open", new_callable = AsyncMock), \
                patch.object(test_bot, "web_find", new_callable = AsyncMock) as mock_find, \
                patch.object(test_bot, "web_click", new_callable = AsyncMock), \
                patch.object(test_bot, "web_check", new_callable = AsyncMock, return_value = True), \
                patch.object(test_bot, "web_request", new_callable = AsyncMock) as mock_request:
            # Mock non-string CSRF token to test str() conversion
            mock_find.return_value.attrs = {"content": 12345}  # Non-string token
            result = await test_bot.delete_ad(ad_cfg, published_ads, delete_old_ads_by_title = False)
            assert result is True

            # Verify that str() was called on the CSRF token
            mock_request.assert_called_once()
            call_args = mock_request.call_args
            assert call_args[1]["headers"]["x-csrf-token"] == "12345"  # Should be converted to string


class TestKleinanzeigenBotAdRepublication:
    """Tests for ad republication functionality."""

    def test_check_ad_republication_with_changes(self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any]) -> None:
        """Test that ads with changes are marked for republication."""
        # Mock the description config to prevent modification of the description
        test_bot.config.ad_defaults = AdDefaults.model_validate({
            "description": {
                "prefix": "",
                "suffix": ""
            }
        })

        # Create ad config with all necessary fields for republication
        ad_cfg = Ad.model_validate(base_ad_config | {
            "id": "12345",
            "updated_on": "2024-01-01T00:00:01",
            "created_on": "2024-01-01T00:00:01",
            "description": "Changed description"
        })

        # Create a temporary directory and file
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            ad_dir = temp_path / "ads"
            ad_dir.mkdir()
            ad_file = ad_dir / "test_ad.yaml"

            dicts.save_dict(ad_file, ad_cfg.model_dump())

            # Set config file path and use relative path for ad_files
            test_bot.config_file_path = str(temp_path / "config.yaml")
            test_bot.config.ad_files = ["ads/*.yaml"]

            ads_to_publish = test_bot.load_ads()
            assert len(ads_to_publish) == 1

    def test_check_ad_republication_no_changes(self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any]) -> None:
        """Test that unchanged ads within interval are not marked for republication."""
        current_time = misc.now()
        three_days_ago = (current_time - timedelta(days = 3)).isoformat()

        # Create ad config with timestamps for republication check
        ad_cfg = Ad.model_validate(base_ad_config | {
            "id": "12345",
            "updated_on": three_days_ago,
            "created_on": three_days_ago
        })

        # Calculate hash before making the copy to ensure they match
        ad_cfg_orig = ad_cfg.model_dump()
        current_hash = ad_cfg.update_content_hash().content_hash
        ad_cfg_orig["content_hash"] = current_hash

        # Mock the config to prevent actual file operations
        test_bot.config.ad_files = ["test.yaml"]
        with patch("kleinanzeigen_bot.utils.dicts.load_dict_if_exists", return_value = ad_cfg_orig), \
                patch("kleinanzeigen_bot.utils.dicts.load_dict", return_value = {}):  # Mock ad_fields.yaml
            ads_to_publish = test_bot.load_ads()
            assert len(ads_to_publish) == 0  # No ads should be marked for republication


class TestKleinanzeigenBotShippingOptions:
    """Tests for shipping options functionality."""

    @pytest.mark.asyncio
    async def test_shipping_options_mapping(self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any], tmp_path:Any) -> None:
        """Test that shipping options are mapped correctly."""
        # Create a mock page to simulate browser context
        test_bot.page = MagicMock()
        test_bot.page.url = "https://www.kleinanzeigen.de/p-anzeige-aufgeben-bestaetigung.html?adId=12345"
        test_bot.page.evaluate = AsyncMock()

        # Create ad config with specific shipping options
        ad_cfg = Ad.model_validate(base_ad_config | {
            "shipping_options": ["DHL_2", "Hermes_Päckchen"],
            "updated_on": "2024-01-01T00:00:00",  # Add created_on to prevent KeyError
            "created_on": "2024-01-01T00:00:00"  # Add updated_on for consistency
        })

        # Create the original ad config and published ads list
        ad_cfg.update_content_hash()  # Add content hash to prevent republication
        ad_cfg_orig = ad_cfg.model_dump()
        published_ads:list[dict[str, Any]] = []

        # Set up default config values needed for the test
        test_bot.config.publishing = PublishingConfig.model_validate({
            "delete_old_ads": "BEFORE_PUBLISH",
            "delete_old_ads_by_title": False
        })

        # Create temporary file path
        ad_file = Path(tmp_path) / "test_ad.yaml"

        # Mock web_execute to handle all JavaScript calls
        async def mock_web_execute(script:str) -> Any:
            if script == "document.body.scrollHeight":
                return 0  # Return integer to prevent scrolling loop
            return None

        # Create mock elements
        csrf_token_elem = MagicMock()
        csrf_token_elem.attrs = {"content": "csrf-token-123"}

        shipping_form_elem = MagicMock()
        shipping_form_elem.attrs = {}

        shipping_size_radio = MagicMock()
        shipping_size_radio.attrs = {"checked": False}

        category_path_elem = MagicMock()
        category_path_elem.apply = AsyncMock(return_value = "Test Category")

        # Mock the necessary web interaction methods
        with patch.object(test_bot, "web_execute", side_effect = mock_web_execute), \
                patch.object(test_bot, "web_click", new_callable = AsyncMock), \
                patch.object(test_bot, "web_find", new_callable = AsyncMock) as mock_find, \
                patch.object(test_bot, "web_select", new_callable = AsyncMock), \
                patch.object(test_bot, "web_input", new_callable = AsyncMock), \
                patch.object(test_bot, "web_open", new_callable = AsyncMock), \
                patch.object(test_bot, "web_sleep", new_callable = AsyncMock), \
                patch.object(test_bot, "web_check", new_callable = AsyncMock, return_value = True), \
                patch.object(test_bot, "web_request", new_callable = AsyncMock), \
                patch.object(test_bot, "web_find_all", new_callable = AsyncMock), \
                patch.object(test_bot, "web_await", new_callable = AsyncMock), \
                patch("builtins.input", return_value = ""), \
                patch.object(test_bot, "web_scroll_page_down", new_callable = AsyncMock):

            # Mock web_find to simulate element detection
            async def mock_find_side_effect(selector_type:By, selector_value:str, **_:Any) -> Element | None:
                if selector_value == "meta[name=_csrf]":
                    return csrf_token_elem
                if selector_value == "myftr-shppngcrt-frm":
                    return shipping_form_elem
                if selector_type == By.ID and selector_value.startswith("radio-button-"):
                    return shipping_size_radio
                if selector_value == "postad-category-path":
                    return category_path_elem
                return None

            mock_find.side_effect = mock_find_side_effect

            # Mock web_check to return True for radio button checked state
            with patch.object(test_bot, "web_check", new_callable = AsyncMock) as mock_check:
                mock_check.return_value = True

                # Test through the public interface by publishing an ad
                await test_bot.publish_ad(str(ad_file), ad_cfg, ad_cfg_orig, published_ads)

            # Verify that web_find was called the expected number of times
            assert mock_find.await_count >= 3

            # Verify the file was created in the temporary directory
            assert ad_file.exists()

    @pytest.mark.asyncio
    async def test_special_attributes_with_non_string_values(self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any]) -> None:
        """Test that special attributes with non-string values are converted to strings."""
        # Create ad config with string special attributes first (to pass validation)
        ad_cfg = Ad.model_validate(base_ad_config | {
            "special_attributes": {
                "art_s": "12345",  # String value initially
                "condition_s": "67890",  # String value initially
                "color_s": "red"  # String value
            },
            "updated_on": "2024-01-01T00:00:00",
            "created_on": "2024-01-01T00:00:00"
        })

        # Now modify the special attributes to non-string values to test str() conversion
        # This simulates the scenario where the values come from external sources as non-strings
        # We need to cast to Any to bypass type checking for this test
        special_attrs = cast(Any, ad_cfg.special_attributes)
        special_attrs["art_s"] = 12345  # Non-string value
        special_attrs["condition_s"] = 67890  # Non-string value

        # Mock special attribute elements
        art_s_elem = MagicMock()
        art_s_attrs = MagicMock()
        art_s_attrs.id = "art_s"
        art_s_attrs.name = "art_s"
        art_s_elem.attrs = art_s_attrs
        art_s_elem.local_name = "select"

        condition_s_elem = MagicMock()
        condition_s_attrs = MagicMock()
        condition_s_attrs.id = "condition_s"
        condition_s_attrs.name = "condition_s"
        condition_s_elem.attrs = condition_s_attrs
        condition_s_elem.local_name = "select"

        color_s_elem = MagicMock()
        color_s_attrs = MagicMock()
        color_s_attrs.id = "color_s"
        color_s_attrs.name = "color_s"
        color_s_elem.attrs = color_s_attrs
        color_s_elem.local_name = "select"

        # Mock the necessary web interaction methods
        with patch.object(test_bot, "web_find", new_callable = AsyncMock) as mock_find, \
                patch.object(test_bot, "web_select", new_callable = AsyncMock) as mock_select, \
                patch.object(test_bot, "web_check", new_callable = AsyncMock, return_value = True), \
                patch.object(test_bot, "_KleinanzeigenBot__set_condition", new_callable = AsyncMock) as mock_set_condition:

            # Mock web_find to simulate element detection
            async def mock_find_side_effect(selector_type:By, selector_value:str, **_:Any) -> Element | None:
                # Handle XPath queries for special attributes
                if selector_type == By.XPATH and "contains(@name" in selector_value:
                    if "art_s" in selector_value:
                        return art_s_elem
                    if "condition_s" in selector_value:
                        return condition_s_elem
                    if "color_s" in selector_value:
                        return color_s_elem
                return None

            mock_find.side_effect = mock_find_side_effect

            # Test the __set_special_attributes method directly
            await getattr(test_bot, "_KleinanzeigenBot__set_special_attributes")(ad_cfg)

            # Verify that web_select was called with string values (str() conversion)
            mock_select.assert_any_call(By.ID, "art_s", "12345")  # Converted to string
            mock_select.assert_any_call(By.ID, "color_s", "red")  # Already string

            # Verify that __set_condition was called with string value
            mock_set_condition.assert_called_once_with("67890")  # Converted to string


class TestKleinanzeigenBotUrlConstruction:
    """Tests for URL construction functionality."""

    def test_url_construction(self, test_bot:KleinanzeigenBot) -> None:
        """Test that URLs are constructed correctly."""
        # Test login URL
        expected_login_url = "https://www.kleinanzeigen.de/m-einloggen.html?targetUrl=/"
        assert f"{test_bot.root_url}/m-einloggen.html?targetUrl=/" == expected_login_url

        # Test ad management URL
        expected_manage_url = "https://www.kleinanzeigen.de/m-meine-anzeigen.html"
        assert f"{test_bot.root_url}/m-meine-anzeigen.html" == expected_manage_url

        # Test ad publishing URL
        expected_publish_url = "https://www.kleinanzeigen.de/p-anzeige-aufgeben-schritt2.html"
        assert f"{test_bot.root_url}/p-anzeige-aufgeben-schritt2.html" == expected_publish_url


class TestKleinanzeigenBotPrefixSuffix:
    """Tests for description prefix and suffix functionality."""
    # pylint: disable=protected-access

    def test_description_prefix_suffix_handling(
        self,
        test_bot_config:Config,
        description_test_cases:list[tuple[dict[str, Any], str, str]]
    ) -> None:
        """Test handling of description prefix/suffix in various configurations."""
        for config, raw_description, expected_description in description_test_cases:
            test_bot = KleinanzeigenBot()
            test_bot.config = test_bot_config.with_values(config)
            ad_cfg = test_bot.load_ad({
                "description": raw_description,
                "active": True,
                "title": "0123456789",
                "category": "whatever",
            })

            # Access private method using the correct name mangling
            description = getattr(test_bot, "_KleinanzeigenBot__get_description")(ad_cfg, with_affixes = True)
            assert description == expected_description

    def test_description_length_validation(self, test_bot_config:Config) -> None:
        """Test that long descriptions with affixes raise appropriate error."""
        test_bot = KleinanzeigenBot()
        test_bot.config = test_bot_config.with_values({
            "ad_defaults": {
                "description_prefix": "P" * 1000,
                "description_suffix": "S" * 1000
            }
        })
        ad_cfg = test_bot.load_ad({
            "description": "D" * 2001,  # This plus affixes will exceed 4000 chars
            "active": True,
            "title": "0123456789",
            "category": "whatever",
        })

        with pytest.raises(AssertionError) as exc_info:
            getattr(test_bot, "_KleinanzeigenBot__get_description")(ad_cfg, with_affixes = True)

        assert "Length of ad description including prefix and suffix exceeds 4000 chars" in str(exc_info.value)
        assert "Description length: 4001" in str(exc_info.value)


class TestKleinanzeigenBotDescriptionHandling:
    """Tests for description handling functionality."""

    def test_description_without_main_config_description(self, test_bot_config:Config) -> None:
        """Test that description works correctly when description is missing from main config."""
        test_bot = KleinanzeigenBot()
        test_bot.config = test_bot_config

        # Test with a simple ad config
        ad_cfg = test_bot.load_ad({
            "description": "Test Description",
            "active": True,
            "title": "0123456789",
            "category": "whatever",
        })

        # The description should be returned as-is without any prefix/suffix
        description = getattr(test_bot, "_KleinanzeigenBot__get_description")(ad_cfg, with_affixes = True)
        assert description == "Test Description"

    def test_description_with_only_new_format_affixes(self, test_bot_config:Config) -> None:
        """Test that description works with only new format affixes in config."""
        test_bot = KleinanzeigenBot()
        test_bot.config = test_bot_config.with_values({
            "ad_defaults": {
                "description_prefix": "Prefix: ",
                "description_suffix": " :Suffix"
            }
        })

        ad_cfg = test_bot.load_ad({
            "description": "Test Description",
            "active": True,
            "title": "0123456789",
            "category": "whatever",
        })

        description = getattr(test_bot, "_KleinanzeigenBot__get_description")(ad_cfg, with_affixes = True)
        assert description == "Prefix: Test Description :Suffix"

    def test_description_with_mixed_config_formats(self, test_bot_config:Config) -> None:
        """Test that description works with both old and new format affixes in config."""
        test_bot = KleinanzeigenBot()
        test_bot.config = test_bot_config.with_values({
            "ad_defaults": {
                "description_prefix": "New Prefix: ",
                "description_suffix": " :New Suffix",
                "description": {
                    "prefix": "Old Prefix: ",
                    "suffix": " :Old Suffix"
                }
            }
        })

        ad_cfg = test_bot.load_ad({
            "description": "Test Description",
            "active": True,
            "title": "0123456789",
            "category": "whatever",
        })

        description = getattr(test_bot, "_KleinanzeigenBot__get_description")(ad_cfg, with_affixes = True)
        assert description == "New Prefix: Test Description :New Suffix"

    def test_description_with_ad_level_affixes(self, test_bot_config:Config) -> None:
        """Test that ad-level affixes take precedence over config affixes."""
        test_bot = KleinanzeigenBot()
        test_bot.config = test_bot_config.with_values({
            "ad_defaults": {
                "description_prefix": "Config Prefix: ",
                "description_suffix": " :Config Suffix"
            }
        })

        ad_cfg = test_bot.load_ad({
            "description": "Test Description",
            "description_prefix": "Ad Prefix: ",
            "description_suffix": " :Ad Suffix",
            "active": True,
            "title": "0123456789",
            "category": "whatever",
        })

        description = getattr(test_bot, "_KleinanzeigenBot__get_description")(ad_cfg, with_affixes = True)
        assert description == "Ad Prefix: Test Description :Ad Suffix"

    def test_description_with_none_values(self, test_bot_config:Config) -> None:
        """Test that None values in affixes are handled correctly."""
        test_bot = KleinanzeigenBot()
        test_bot.config = test_bot_config.with_values({
            "ad_defaults": {
                "description_prefix": None,
                "description_suffix": None,
                "description": {
                    "prefix": None,
                    "suffix": None
                }
            }
        })

        ad_cfg = test_bot.load_ad({
            "description": "Test Description",
            "active": True,
            "title": "0123456789",
            "category": "whatever",
        })

        description = getattr(test_bot, "_KleinanzeigenBot__get_description")(ad_cfg, with_affixes = True)
        assert description == "Test Description"

    def test_description_with_email_replacement(self, test_bot_config:Config) -> None:
        """Test that @ symbols in description are replaced with (at)."""
        test_bot = KleinanzeigenBot()
        test_bot.config = test_bot_config

        ad_cfg = test_bot.load_ad({
            "description": "Contact: test@example.com",
            "active": True,
            "title": "0123456789",
            "category": "whatever",
        })

        description = getattr(test_bot, "_KleinanzeigenBot__get_description")(ad_cfg, with_affixes = True)
        assert description == "Contact: test(at)example.com"


class TestKleinanzeigenBotChangedAds:
    """Tests for the 'changed' ads selector functionality."""

    def test_load_ads_with_changed_selector(self, test_bot_config:Config, base_ad_config:dict[str, Any]) -> None:
        """Test that only changed ads are loaded when using the 'changed' selector."""
        # Set up the bot with the 'changed' selector
        test_bot = KleinanzeigenBot()
        test_bot.ads_selector = "changed"
        test_bot.config = test_bot_config.with_values({
            "ad_defaults": {
                "description": {
                    "prefix": "",
                    "suffix": ""
                }
            }
        })

        # Create a changed ad
        ad_cfg = Ad.model_validate(base_ad_config | {
            "id": "12345",
            "title": "Changed Ad",
            "updated_on": "2024-01-01T00:00:00",
            "created_on": "2024-01-01T00:00:00",
            "active": True
        })

        # Calculate hash for changed_ad and add it to the config
        # Then modify the ad to simulate a change
        changed_ad = ad_cfg.model_dump()
        changed_hash = ad_cfg.update_content_hash().content_hash
        changed_ad["content_hash"] = changed_hash
        # Now modify the ad to make it "changed"
        changed_ad["title"] = "Changed Ad - Modified"

        # Create temporary directory and file
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            ad_dir = temp_path / "ads"
            ad_dir.mkdir()

            # Write the ad file
            dicts.save_dict(ad_dir / "changed_ad.yaml", changed_ad)

            # Set config file path and use relative path for ad_files
            test_bot.config_file_path = str(temp_path / "config.yaml")
            test_bot.config.ad_files = ["ads/*.yaml"]

            # Mock the loading of the ad configuration
            with patch("kleinanzeigen_bot.utils.dicts.load_dict", side_effect = [
                changed_ad,  # First call returns the changed ad
                {}  # Second call for ad_fields.yaml
            ]):
                ads_to_publish = test_bot.load_ads()

                # The changed ad should be loaded
                assert len(ads_to_publish) == 1
                assert ads_to_publish[0][1].title == "Changed Ad - Modified"

    def test_load_ads_with_due_selector_includes_all_due_ads(self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any]) -> None:
        """Test that 'due' selector includes all ads that are due for republication, regardless of changes."""
        # Set up the bot with the 'due' selector
        test_bot.ads_selector = "due"

        # Create a changed ad that is also due for republication
        current_time = misc.now()
        old_date = (current_time - timedelta(days = 10)).isoformat()  # Past republication interval

        ad_cfg = Ad.model_validate(base_ad_config | {
            "id": "12345",
            "title": "Changed Ad",
            "updated_on": old_date,
            "created_on": old_date,
            "republication_interval": 7,  # Due for republication after 7 days
            "active": True
        })
        changed_ad = ad_cfg.model_dump()

        # Create temporary directory and file
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            ad_dir = temp_path / "ads"
            ad_dir.mkdir()

            # Write the ad file
            dicts.save_dict(ad_dir / "changed_ad.yaml", changed_ad)

            # Set config file path and use relative path for ad_files
            test_bot.config_file_path = str(temp_path / "config.yaml")
            test_bot.config.ad_files = ["ads/*.yaml"]

            # Mock the loading of the ad configuration
            with patch("kleinanzeigen_bot.utils.dicts.load_dict", side_effect = [
                changed_ad,  # First call returns the changed ad
                {}  # Second call for ad_fields.yaml
            ]):
                ads_to_publish = test_bot.load_ads()

                # The changed ad should be loaded with 'due' selector because it's due for republication
                assert len(ads_to_publish) == 1


def test_file_logger_writes_message(tmp_path:Path, caplog:pytest.LogCaptureFixture) -> None:
    """
    Unit: Logger can be initialized and used, robust to pytest log capture.
    """
    log_path = tmp_path / "logger_test.log"
    logger_name = "logger_test_logger_unique"
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    for h in list(logger.handlers):
        logger.removeHandler(h)
    handle = logging.FileHandler(str(log_path), encoding = "utf-8")
    handle.setLevel(logging.DEBUG)
    formatter = logging.Formatter("%(levelname)s:%(name)s:%(message)s")
    handle.setFormatter(formatter)
    logger.addHandler(handle)
    logger.info("Logger test log message")
    handle.flush()
    handle.close()
    logger.removeHandler(handle)
    assert log_path.exists()
    with open(log_path, "r", encoding = "utf-8") as f:
        contents = f.read()
    assert "Logger test log message" in contents


class TestFinalizeInstallationMode:
    """Tests for finalize_installation_mode() logfile override behavior."""

    def test_explicit_logfile_respected_in_xdg_mode(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        """Test that explicit --logfile overrides XDG mode default."""
        # Setup XDG mode by creating config in XDG location
        xdg_config = tmp_path / "config" / "kleinanzeigen-bot"
        xdg_config.mkdir(parents = True)
        (xdg_config / "config.yaml").write_text("login:\n  username: test\n  password: test\n")

        monkeypatch.setattr("platformdirs.user_config_dir", lambda app_name: str(tmp_path / "config" / app_name))
        monkeypatch.setattr("platformdirs.user_state_dir", lambda app_name: str(tmp_path / "state" / app_name))

        # Change to different directory
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        monkeypatch.chdir(cwd)

        # Create bot and explicitly set logfile to portable default value
        bot = KleinanzeigenBot()
        explicit_logfile = str(cwd / "kleinanzeigen_bot.log")
        bot.parse_args(["script.py", f"--logfile={explicit_logfile}", "help"])

        # Verify flag is set
        assert bot.log_file_explicitly_provided is True
        assert bot.log_file_path == explicit_logfile

        # This should NOT override the explicit logfile even though we're in XDG mode
        bot.finalize_installation_mode()

        # Verify logfile path was NOT changed to XDG state directory
        assert bot.installation_mode == "xdg"
        assert bot.log_file_path == explicit_logfile
        assert bot.log_file_path is not None
        assert not bot.log_file_path.startswith(str(tmp_path / "state"))

    def test_default_logfile_uses_xdg_in_xdg_mode(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        """Test that default logfile uses XDG state directory in XDG mode."""
        # Setup XDG mode
        xdg_config = tmp_path / "config" / "kleinanzeigen-bot"
        xdg_config.mkdir(parents = True)
        (xdg_config / "config.yaml").write_text("login:\n  username: test\n  password: test\n")

        monkeypatch.setattr("platformdirs.user_config_dir", lambda app_name: str(tmp_path / "config" / app_name))
        monkeypatch.setattr("platformdirs.user_state_dir", lambda app_name: str(tmp_path / "state" / app_name))

        cwd = tmp_path / "cwd"
        cwd.mkdir()
        monkeypatch.chdir(cwd)

        # Create bot without explicit logfile
        bot = KleinanzeigenBot()
        bot.parse_args(["script.py", "help"])

        # Verify flag is NOT set
        assert bot.log_file_explicitly_provided is False

        # Finalize should use XDG state directory
        bot.finalize_installation_mode()

        assert bot.installation_mode == "xdg"
        assert bot.log_file_path is not None
        assert bot.log_file_path.startswith(str(tmp_path / "state" / "kleinanzeigen-bot"))
        assert "kleinanzeigen_bot.log" in bot.log_file_path

    def test_explicit_logfile_respected_in_portable_mode(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        """Test that explicit --logfile is respected in portable mode."""
        # Setup portable mode
        (tmp_path / "config.yaml").write_text("login:\n  username: test\n  password: test\n")
        monkeypatch.chdir(tmp_path)

        # Create bot with custom logfile
        bot = KleinanzeigenBot()
        custom_logfile = str(tmp_path / "custom.log")
        bot.parse_args(["script.py", f"--logfile={custom_logfile}", "help"])

        assert bot.log_file_explicitly_provided is True

        bot.finalize_installation_mode()

        assert bot.installation_mode == "portable"
        assert bot.log_file_path == custom_logfile


class TestCreateConfigCommand:
    """Tests for create-config CLI command with XDG support."""

    @pytest.mark.asyncio
    async def test_create_config_in_portable_mode(
        self,
        tmp_path:Path,
        monkeypatch:pytest.MonkeyPatch
    ) -> None:
        """Test that create-config creates config.yaml in CWD (portable mode)."""
        # Setup: clean directory, make it CWD
        monkeypatch.chdir(tmp_path)

        # Create bot and run create-config command
        bot = KleinanzeigenBot()
        await bot.run(["script.py", "create-config"])

        # Verify config was created in CWD (portable mode)
        config_file = tmp_path / "config.yaml"
        assert config_file.exists()
        assert bot.installation_mode == "portable"
        assert bot.config_file_path == str(config_file)

    @pytest.mark.asyncio
    async def test_create_config_in_xdg_mode(
        self,
        tmp_path:Path,
        monkeypatch:pytest.MonkeyPatch
    ) -> None:
        """Test that create-config prompts for XDG mode on first run."""
        # Setup: clean directories for CWD and XDG
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        monkeypatch.chdir(cwd)

        xdg_config = tmp_path / "config" / "kleinanzeigen-bot"
        xdg_config.mkdir(parents = True)

        # Mock platformdirs to return test XDG path
        monkeypatch.setattr("platformdirs.user_config_dir",
                           lambda app_name: str(tmp_path / "config" / app_name))

        # Mock user selecting XDG mode (choice "2")
        mock_stdin = io.StringIO()
        mock_stdin.isatty = lambda: True  # type: ignore[method-assign]
        monkeypatch.setattr("sys.stdin", mock_stdin)
        monkeypatch.setattr("builtins.input", lambda _: "2")

        # Create bot and run create-config command
        bot = KleinanzeigenBot()
        await bot.run(["script.py", "create-config"])

        # Verify config was created in XDG directory
        config_file = xdg_config / "config.yaml"
        assert config_file.exists()
        assert bot.installation_mode == "xdg"
        assert bot.config_file_path == str(config_file)

        # Verify CWD does NOT have config.yaml
        assert not (cwd / "config.yaml").exists()

    @pytest.mark.asyncio
    async def test_create_config_respects_existing_config(
        self,
        tmp_path:Path,
        monkeypatch:pytest.MonkeyPatch
    ) -> None:
        """Test that create-config doesn't overwrite existing config.yaml."""
        # Setup: create existing config.yaml
        monkeypatch.chdir(tmp_path)
        existing_config = tmp_path / "config.yaml"
        original_content = "login:\n  username: existing\n  password: existing\n"
        existing_config.write_text(original_content)

        # Create bot and run create-config command
        bot = KleinanzeigenBot()
        await bot.run(["script.py", "create-config"])

        # Verify config was NOT overwritten
        config_content = existing_config.read_text()
        assert config_content == original_content
        assert "existing" in config_content
        assert "changeme" not in config_content

    @pytest.mark.asyncio
    async def test_create_config_with_explicit_path(
        self,
        tmp_path:Path,
        monkeypatch:pytest.MonkeyPatch
    ) -> None:
        """Test that create-config respects --config flag."""
        monkeypatch.chdir(tmp_path)

        # Create custom config directory
        custom_dir = tmp_path / "custom"
        custom_dir.mkdir()
        custom_config = custom_dir / "my-config.yaml"

        # Create bot with --config flag
        bot = KleinanzeigenBot()
        await bot.run(["script.py", f"--config={custom_config}", "create-config"])

        # Verify config was created at custom location
        assert custom_config.exists()
        assert bot.config_file_path == str(custom_config)

        # Verify default location does NOT have config
        assert not (tmp_path / "config.yaml").exists()
