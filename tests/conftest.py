"""
SPDX-FileCopyrightText: © Jens Bergmann and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Dict, Final, List, Optional, Protocol, Type, Union, cast
from unittest.mock import AsyncMock, MagicMock, patch

import psutil, pytest

from kleinanzeigen_bot.extract import AdExtractor
from kleinanzeigen_bot.utils import loggers
from kleinanzeigen_bot.utils.web_scraping_mixin import Browser

# Import the actual KleinanzeigenBot class for type checking
if TYPE_CHECKING:
    from kleinanzeigen_bot import KleinanzeigenBot

# Define a Protocol for KleinanzeigenBot for type checking


class KleinanzeigenBotProtocol(Protocol):
    """Protocol for KleinanzeigenBot class."""

    # Properties
    config: dict[str, Any]
    page: Any
    browser: Any
    browser_context: Any
    browser_session_created: bool
    logged_in: bool
    ads_loaded: bool
    ads: list[dict[str, Any]]
    command: str | None
    config_file_path: str
    log_file_path: str | None
    ads_selector: Any  # Using Any to avoid complex Callable type
    keep_old_ads: bool
    limit: int | None
    force: bool
    root_url: str
    categories: dict[str, Any]
    ad_extractor: Any
    file_log: Any

    # Core methods
    def configure_file_logging(self) -> None: ...
    def load_config(self) -> None: ...
    def load_ads(self, ignore_inactive: bool = True) -> list[Any]: ...
    def close_browser_session(self) -> None: ...
    async def create_browser_session(self) -> None: ...
    async def run(self, args: list[str]) -> None: ...
    async def login(self) -> None: ...
    async def is_logged_in(self) -> bool: ...
    async def publish_ad(self, ad_file: str, ad_cfg: dict[str, Any], ad_cfg_orig: dict[str, Any], published_ads: list[Any]) -> None: ...
    async def publish_ads(self, ads: list[Any]) -> None: ...
    async def delete_ad(self, ad_cfg: dict[str, Any], delete_old_ads_by_title: bool = False, published_ads: list[dict[str, Any]] | None = None) -> bool: ...
    async def delete_ads(self, ads: list[Any]) -> None: ...
    async def download_ads(self) -> None: ...
    async def assert_free_ad_limit_not_reached(self) -> None: ...

    # Web methods
    async def web_await(self, selector: str, timeout: float | None = None) -> None: ...
    async def web_check(self, by: Any, selector: str, timeout: float | None = None) -> bool: ...
    async def web_click(self, by: Any, selector: str, timeout: float | None = None) -> None: ...
    async def web_execute(self, script: str) -> Any: ...
    async def web_find(self, by: Any, selector: str, timeout: float | None = None) -> Any: ...
    async def web_input(self, by: Any, selector: str, text: str, timeout: float | None = None) -> None: ...
    async def web_open(self, url: str, timeout: float | None = None, reload_if_already_open: bool = False) -> None: ...
    async def web_request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]: ...
    async def web_select(self, by: Any, selector: str, value: str) -> None: ...
    async def web_sleep(self, seconds: float | None = None) -> None: ...
    async def web_text(self, by: Any, selector: str, timeout: float | None = None) -> str: ...

    # Helper methods
    def _map_shipping_option(self, option: str) -> str: ...

    # Private methods that are accessed in tests
    # pylint: disable=invalid-name
    def _KleinanzeigenBot__check_ad_republication(self, ad_cfg: dict[str, Any], ad_file_relative: str) -> bool: ...
    async def _KleinanzeigenBot__set_condition(self, condition: str) -> None: ...
    async def _KleinanzeigenBot__set_category(self, category: str, ad_file: str = "") -> None: ...
    async def _KleinanzeigenBot__set_special_attributes(self, ad_config: dict[str, Any]) -> None: ...
    async def _KleinanzeigenBot__set_shipping(self, shipping_type: str) -> None: ...
    async def _KleinanzeigenBot__set_shipping_options(self, ad_config: dict[str, Any]) -> None: ...
    async def _KleinanzeigenBot__upload_images(self, images: list[str]) -> None: ...
    def _KleinanzeigenBot__get_description_with_affixes(self, description: str) -> str: ...
    # pylint: enable=invalid-name

    # Other methods
    def parse_args(self, args: list[str]) -> None: ...
    def get_version(self) -> str: ...
    async def fill_login_data_and_send(self) -> None: ...
    async def handle_after_login_logic(self) -> None: ...


loggers.configure_console_logging()

LOG: Final[loggers.Logger] = loggers.get_logger("kleinanzeigen_bot")
LOG.setLevel(loggers.DEBUG)


# Create a properly configured AsyncMock that can be awaited
def create_awaitable_mock(return_value: Any = None, side_effect: Any = None) -> AsyncMock:
    """Create an AsyncMock that can be properly awaited in tests.

    Args:
        return_value: The value to return when the mock is called
        side_effect: The side effect to apply when the mock is called

    Returns:
        An AsyncMock that can be awaited
    """
    mock = AsyncMock()
    if return_value is not None:
        mock.return_value = return_value
    if side_effect is not None:
        mock.side_effect = side_effect

    # Add attributes to the mock's __dict__ directly to avoid method assignment errors
    mock.__dict__['call_count'] = 0
    mock.__dict__['called'] = False

    return mock


@pytest.fixture
def test_data_dir(tmp_path: pytest.TempPathFactory) -> str:
    """Provides a temporary directory for test data.

    This fixture uses pytest's built-in tmp_path fixture to create a temporary
    directory that is automatically cleaned up after each test.
    """
    return str(tmp_path)


@pytest.fixture
def sample_config() -> dict[str, Any]:
    """Provides a basic sample configuration for testing.

    This configuration includes all required fields for the bot to function:
    - Login credentials (username/password)
    - Browser settings
    - Ad defaults (description prefix/suffix)
    - Publishing settings
    """
    return {
        'login': {
            'username': 'testuser',
            'password': 'testpass'
        },
        'browser': {
            'arguments': [],
            'binary_location': None,
            'extensions': [],
            'use_private_window': True,
            'user_data_dir': None,
            'profile_name': None
        },
        'ad_defaults': {
            'description': {
                'prefix': 'Test Prefix',
                'suffix': 'Test Suffix'
            }
        },
        'publishing': {
            'delete_old_ads': 'BEFORE_PUBLISH',
            'delete_old_ads_by_title': False
        }
    }


@pytest.fixture
def test_bot(sample_config: dict[str, Any], request: pytest.FixtureRequest) -> KleinanzeigenBotProtocol:
    """Create a mock KleinanzeigenBot instance for testing."""
    bot_mock = MagicMock()
    bot_mock.config = sample_config
    bot_mock.command = "publish"
    bot_mock.ads = []
    bot_mock.ads_selector = "due"
    bot_mock.log_file_path = "test.log"
    bot_mock.file_log = None
    bot_mock.root_url = "https://www.kleinanzeigen.de"
    bot_mock.keep_old_ads = False

    # Create a mock browser with a process_pid attribute
    # pylint: disable=protected-access
    browser_mock = MagicMock()
    browser_mock._process_pid = 12345  # Add a mock process ID
    # pylint: enable=protected-access
    bot_mock.browser = browser_mock

    # Create a mock Process object
    process_mock = MagicMock()
    process_mock.children.return_value = []  # No child processes

    # Patch psutil.Process to return our mock
    process_patcher = patch('psutil.Process', return_value=process_mock)
    process_patcher.start()

    # Explicitly override the __del__ method to do nothing
    # This is crucial to prevent the warning
    def noop_del(self: Any) -> None:
        """No-op __del__ method to prevent warnings."""
        # No operation needed

    bot_mock.__del__ = noop_del

    # Add a finalizer to stop the patch after the test
    def cleanup() -> None:
        process_patcher.stop()
        bot_mock.browser = None

    # Register the cleanup function with pytest
    request.addfinalizer(cleanup)

    # Override the cleanup_browser_session method with a proper async implementation
    async def mock_cleanup_browser_session() -> None:
        # This implementation avoids calling close_browser_session
        pass

    bot_mock.cleanup_browser_session = mock_cleanup_browser_session

    return cast(KleinanzeigenBotProtocol, bot_mock)


@pytest.fixture
def browser_mock() -> MagicMock:
    """Provides a mock browser instance for testing.

    This mock is configured with the Browser spec to ensure it has all
    the required methods and attributes of a real Browser instance.
    """
    return MagicMock(spec=Browser)


@pytest.fixture
def log_file_path(test_data_dir: str) -> str:
    """Provides a temporary path for log files.

    Dependencies:
        - test_data_dir: Used to create the log file in the temporary test directory
    """
    return os.path.join(str(test_data_dir), "test.log")


@pytest.fixture
def test_extractor(browser_mock: MagicMock, sample_config: dict[str, Any]) -> AdExtractor:
    """Provides a fresh AdExtractor instance for testing.

    Dependencies:
        - browser_mock: Used to mock browser interactions
        - sample_config: Used to initialize the extractor with a valid configuration
    """
    return AdExtractor(browser_mock, sample_config)


@pytest.fixture
def description_test_cases() -> list[tuple[dict[str, Any], str, str]]:
    """Provides test cases for description prefix/suffix handling.

    Returns tuples of (config, raw_description, expected_description)
    """
    return [
        # Test case 1: New flattened format
        (
            {
                "ad_defaults": {
                    "description_prefix": "Global Prefix\n",
                    "description_suffix": "\nGlobal Suffix"
                }
            },
            "Original Description",  # Raw description without affixes
            "Global Prefix\nOriginal Description\nGlobal Suffix"  # Expected with affixes
        ),
        # Test case 2: Legacy nested format
        (
            {
                "ad_defaults": {
                    "description": {
                        "prefix": "Legacy Prefix\n",
                        "suffix": "\nLegacy Suffix"
                    }
                }
            },
            "Original Description",
            "Legacy Prefix\nOriginal Description\nLegacy Suffix"
        ),
        # Test case 3: Both formats - new format takes precedence
        (
            {
                "ad_defaults": {
                    "description_prefix": "New Prefix\n",
                    "description_suffix": "\nNew Suffix",
                    "description": {
                        "prefix": "Legacy Prefix\n",
                        "suffix": "\nLegacy Suffix"
                    }
                }
            },
            "Original Description",
            "New Prefix\nOriginal Description\nNew Suffix"
        ),
        # Test case 4: Empty config
        (
            {"ad_defaults": {}},
            "Original Description",
            "Original Description"
        ),
        # Test case 5: None values in config
        (
            {
                "ad_defaults": {
                    "description_prefix": None,
                    "description_suffix": None,
                    "description": {
                        "prefix": None,
                        "suffix": None
                    }
                }
            },
            "Original Description",
            "Original Description"
        ),
        # Test case 6: Non-string values in config
        (
            {
                "ad_defaults": {
                    "description_prefix": 123,
                    "description_suffix": True,
                    "description": {
                        "prefix": [],
                        "suffix": {}
                    }
                }
            },
            "Original Description",
            "Original Description"
        )
    ]


@pytest.fixture
def mock_web_text_responses() -> list[str]:
    """Provides common mock responses for web_text calls."""
    return [
        "Test Title",  # Title
        "Test Description",  # Description
        "03.02.2025"  # Creation date
    ]


@pytest.fixture(autouse=True)
def patch_kleinanzeigen_bot_del(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch the __del__ method in KleinanzeigenBot to prevent warnings during test teardown.

    This fixture is automatically used in all tests (autouse=True) to prevent the warning
    about comparing AsyncMock and int when the __del__ method is called during test teardown.
    """
    # Import the KleinanzeigenBot class
    # We need to import it here to avoid circular imports
    # pylint: disable=import-outside-toplevel
    if not TYPE_CHECKING:
        from kleinanzeigen_bot import KleinanzeigenBot

        # Create a mock Process class that handles AsyncMock objects
        original_process_init = psutil.Process.__init__

        def mock_process_init(self: Any, pid: Any) -> None:
            """Mock Process.__init__ to handle AsyncMock objects."""
            if isinstance(pid, AsyncMock):
                pid = 12345
            original_process_init(self, pid)

        # Patch the Process.__init__ method
        monkeypatch.setattr(psutil.Process, "__init__", mock_process_init)

        # Define a no-op __del__ method
        def noop_del(self: Any) -> None:
            """No-op __del__ method to prevent warnings."""
            # No operation needed

        # Patch the KleinanzeigenBot.__del__ method
        monkeypatch.setattr(KleinanzeigenBot, "__del__", noop_del)
