# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import asyncio, copy, fnmatch, json, logging, os  # isort: skip
from collections.abc import Generator
from contextlib import ExitStack, contextmanager
from pathlib import Path, PureWindowsPath
from typing import Any, Iterator, cast
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from nodriver.core.connection import ProtocolException

from kleinanzeigen_bot import (
    KleinanzeigenBot,
    LoginDetectionReason,
    LoginDetectionResult,
    runtime_config,
)
from kleinanzeigen_bot._version import __version__
from kleinanzeigen_bot.model.ad_model import Ad, AdUpdateStrategy
from kleinanzeigen_bot.model.config_model import (
    AutoPriceReductionConfig,
    Config,
    DiagnosticsConfig,
    PublishingConfig,
)
from kleinanzeigen_bot.publishing_workflow import SUBMISSION_MAX_RETRIES
from kleinanzeigen_bot.utils import xdg_paths
from kleinanzeigen_bot.utils.exceptions import CategoryResolutionError, PublishSubmissionUncertainError
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
        "contact": {"name": "Test User", "zipcode": "12345", "location": "Test City", "street": "", "phone": ""},
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
    return remove_fields(base_ad_config, "id", "created_on", "shipping_options", "special_attributes", "contact.street", "contact.phone")


@pytest.fixture
def mock_config_setup(test_bot:KleinanzeigenBot, tmp_path:Path) -> Generator[None]:
    """Provide a centralized mock configuration setup for tests.
    This fixture mocks load_config and other essential configuration-related methods."""
    test_bot.config_file_path = str(tmp_path / "config.yaml")
    workspace = xdg_paths.Workspace.for_config(tmp_path / "config.yaml", "kleinanzeigen-bot")
    with (
        patch("kleinanzeigen_bot.runtime_config.resolve_workspace", return_value = workspace),
        patch(
            "kleinanzeigen_bot.runtime_config.load_config",
            return_value = runtime_config.RuntimeState(config = test_bot.config, categories = {}, timing_collector = None),
        ),
        patch("kleinanzeigen_bot.runtime_config.apply_browser_config"),
        patch("kleinanzeigen_bot.runtime_config.configure_file_logging", return_value = None),
        patch.object(test_bot, "create_browser_session", new_callable = AsyncMock),
        patch.object(test_bot, "login", new_callable = AsyncMock),
        patch.object(test_bot, "web_request", new_callable = AsyncMock) as mock_request,
    ):
        # Mock the web request for published ads
        mock_request.return_value = {"content": '{"ads": []}'}
        yield


def _login_detection_result(is_logged_in:bool, reason:LoginDetectionReason) -> LoginDetectionResult:
    return LoginDetectionResult(is_logged_in = is_logged_in, reason = reason)


class TestKleinanzeigenBotInitialization:
    """Tests for run-path initialization and download plumbing."""
    @pytest.mark.asyncio
    @pytest.mark.parametrize("command", ["verify", "update-check", "update-content-hash", "publish", "delete", "download"])
    async def test_run_uses_workspace_state_file_for_update_checker(self, test_bot:KleinanzeigenBot, command:str, tmp_path:Path) -> None:
        """Ensure UpdateChecker is initialized with the workspace state file."""
        update_checker_calls:list[tuple[Config, Path]] = []
        workspace = xdg_paths.Workspace.for_config(tmp_path / "config.yaml", "kleinanzeigen-bot")

        class DummyUpdateChecker:
            def __init__(self, config:Config, state_file:Path) -> None:
                update_checker_calls.append((config, state_file))

            def check_for_updates(self, *_args:Any, **_kwargs:Any) -> None:
                return None

        with (
            patch("kleinanzeigen_bot.runtime_config.resolve_workspace", return_value = workspace),
            patch(
                "kleinanzeigen_bot.runtime_config.load_config",
                return_value = runtime_config.RuntimeState(config = test_bot.config, categories = {}, timing_collector = None),
            ),
            patch("kleinanzeigen_bot.runtime_config.configure_file_logging", return_value = None),
            patch("kleinanzeigen_bot.runtime_config.apply_browser_config"),
            patch.object(test_bot, "load_ads", return_value = []),
            patch.object(test_bot, "create_browser_session", new_callable = AsyncMock),
            patch.object(test_bot, "login", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.download_flow.download_ads", new_callable = AsyncMock),


            patch.object(test_bot, "close_browser_session"),
            patch("kleinanzeigen_bot.UpdateChecker", DummyUpdateChecker),
        ):
            await test_bot.run(["app", command])

        expected_state_path = (tmp_path / "config.yaml").resolve().parent / ".temp" / "update_check_state.json"
        assert update_checker_calls == [(test_bot.config, expected_state_path)]


class TestKleinanzeigenBotAuthentication:
    """Tests for login and authentication functionality."""

    @pytest.mark.asyncio
    async def test_is_logged_in_returns_true_when_logged_in(self, test_bot:KleinanzeigenBot) -> None:
        """Verify that login check returns true when logged in."""
        with patch.object(
            test_bot,
            "web_text_first_available",
            new_callable = AsyncMock,
            return_value = ("Welcome dummy_user", 0),
        ):
            assert await test_bot.is_logged_in() is True

    @pytest.mark.asyncio
    async def test_is_logged_in_returns_false_when_not_logged_in(self, test_bot:KleinanzeigenBot) -> None:
        """Verify that login check returns false when not logged in."""
        with (
            patch.object(
                test_bot,
                "web_text_first_available",
                new_callable = AsyncMock,
                side_effect = [("nicht-eingeloggt", 0), ("kein user signal", 0)],
            ),
            patch.object(test_bot, "_has_logged_out_cta", new_callable = AsyncMock, return_value = False),
        ):
            assert await test_bot.is_logged_in() is False

    @pytest.mark.asyncio
    async def test_has_logged_out_cta_requires_visible_candidate(self, test_bot:KleinanzeigenBot) -> None:
        matched_element = MagicMock(spec = Element)
        with (
            patch.object(test_bot, "web_find_first_available", new_callable = AsyncMock, return_value = (matched_element, 0)),
            patch.object(test_bot, "_extract_visible_text", new_callable = AsyncMock, return_value = ""),
        ):
            assert await test_bot._has_logged_out_cta() is False

    @pytest.mark.asyncio
    async def test_has_logged_out_cta_accepts_visible_candidate(self, test_bot:KleinanzeigenBot) -> None:
        matched_element = MagicMock(spec = Element)
        with (
            patch.object(test_bot, "web_find_first_available", new_callable = AsyncMock, return_value = (matched_element, 0)),
            patch.object(test_bot, "_extract_visible_text", new_callable = AsyncMock, return_value = "Einloggen"),
        ):
            assert await test_bot._has_logged_out_cta() is True

    @pytest.mark.asyncio
    async def test_is_logged_in_uses_selector_group_timeout_key(self, test_bot:KleinanzeigenBot) -> None:
        """Verify login detection uses selector-group lookup with login_detection timeout key."""
        with patch.object(
            test_bot,
            "web_text_first_available",
            new_callable = AsyncMock,
            side_effect = [TimeoutError(), ("Welcome dummy_user", 0)],
        ) as group_text:
            assert await test_bot.is_logged_in() is True

        group_text.assert_awaited()
        assert any(call.kwargs.get("timeout") == test_bot.timeout("login_detection") for call in group_text.await_args_list)

    @pytest.mark.asyncio
    async def test_is_logged_in_runs_full_selector_group_before_cta_precheck(self, test_bot:KleinanzeigenBot) -> None:
        """Quick CTA checks must not short-circuit before full logged-in selector checks."""
        with patch.object(
            test_bot,
            "web_text_first_available",
            new_callable = AsyncMock,
            side_effect = [TimeoutError(), ("Welcome dummy_user", 0)],
        ) as group_text:
            assert await test_bot.is_logged_in() is True

        group_text.assert_awaited()
        assert group_text.await_count >= 1

    @pytest.mark.asyncio
    async def test_is_logged_in_short_circuits_before_cta_check_when_quick_user_signal_matches(self, test_bot:KleinanzeigenBot) -> None:
        """Logged-in quick pre-check should win even if incidental login links exist elsewhere."""
        with patch.object(
            test_bot,
            "web_text_first_available",
            new_callable = AsyncMock,
            return_value = ("angemeldet als: dummy_user", 0),
        ) as group_text:
            assert await test_bot.is_logged_in() is True

        group_text.assert_awaited()
        assert group_text.await_count >= 1

    @pytest.mark.asyncio
    async def test_get_login_state_prefers_dom_checks(self, test_bot:KleinanzeigenBot) -> None:
        with (
            patch.object(
                test_bot,
                "web_text_first_available",
                new_callable = AsyncMock,
                return_value = ("Welcome dummy_user", 0),
            ) as web_text,
        ):
            result = await test_bot.get_login_state()
            assert result.is_logged_in is True
            assert result.reason == LoginDetectionReason.USER_INFO_MATCH
            web_text.assert_awaited_once()

    def test_current_page_url_strips_query_and_fragment(self, test_bot:KleinanzeigenBot) -> None:
        page = MagicMock()
        page.url = "https://login.kleinanzeigen.de/u/login/password?state=secret&code=abc#frag"
        test_bot.page = page

        assert test_bot._current_page_url() == "https://login.kleinanzeigen.de/u/login/password"

    def test_is_valid_post_auth0_destination_filters_invalid_urls(self, test_bot:KleinanzeigenBot) -> None:
        assert test_bot._is_valid_post_auth0_destination("https://www.kleinanzeigen.de/") is True
        assert test_bot._is_valid_post_auth0_destination("https://www.kleinanzeigen.de/m-meine-anzeigen.html") is True
        assert test_bot._is_valid_post_auth0_destination("https://foo.kleinanzeigen.de/") is True
        assert test_bot._is_valid_post_auth0_destination("unknown") is False
        assert test_bot._is_valid_post_auth0_destination("about:blank") is False
        assert test_bot._is_valid_post_auth0_destination("https://evilkleinanzeigen.de/") is False
        assert test_bot._is_valid_post_auth0_destination("https://kleinanzeigen.de.evil.com/") is False
        assert test_bot._is_valid_post_auth0_destination("https://login.kleinanzeigen.de/u/login/password") is False
        assert test_bot._is_valid_post_auth0_destination("https://www.kleinanzeigen.de/login-error-500") is False

    @pytest.mark.asyncio
    async def test_get_login_state_returns_selector_timeout_when_dom_checks_are_inconclusive(self, test_bot:KleinanzeigenBot) -> None:
        with (
            patch.object(test_bot, "web_text_first_available", side_effect = [TimeoutError(), TimeoutError()]) as web_text,
            patch.object(test_bot, "web_find_first_available", side_effect = TimeoutError()) as cta_find,
        ):
            result = await test_bot.get_login_state()
            assert result.is_logged_in is False
            assert result.reason == LoginDetectionReason.SELECTOR_TIMEOUT
            assert web_text.await_count == 2
            assert cta_find.await_count == 1

    @pytest.mark.asyncio
    async def test_get_login_state_returns_logged_out_when_cta_detected(self, test_bot:KleinanzeigenBot) -> None:
        matched_element = MagicMock(spec = Element)
        with (
            patch.object(
                test_bot,
                "web_text_first_available",
                side_effect = [TimeoutError(), TimeoutError()],
            ) as web_text,
            patch.object(test_bot, "web_find_first_available", new_callable = AsyncMock, return_value = (matched_element, 0)),
            patch.object(test_bot, "_extract_visible_text", new_callable = AsyncMock, return_value = "Hier einloggen"),
        ):
            result = await test_bot.get_login_state()
            assert result.is_logged_in is False
            assert result.reason == LoginDetectionReason.CTA_MATCH
            assert web_text.await_count == 2

    @pytest.mark.asyncio
    async def test_get_login_state_checks_logged_out_cta_only_once(self, test_bot:KleinanzeigenBot) -> None:
        with (
            patch.object(test_bot, "_has_logged_in_marker", new_callable = AsyncMock, return_value = False),
            patch.object(test_bot, "_has_logged_out_cta", new_callable = AsyncMock, return_value = False) as cta_check,
        ):
            result = await test_bot.get_login_state(capture_diagnostics = False)
            assert result.is_logged_in is False
            assert result.reason == LoginDetectionReason.SELECTOR_TIMEOUT
            cta_check.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_login_state_selector_timeout_captures_diagnostics_when_enabled(self, test_bot:KleinanzeigenBot, tmp_path:Path) -> None:
        test_bot.config.diagnostics = DiagnosticsConfig.model_validate({"capture_on": {"login_detection": True}, "output_dir": str(tmp_path)})

        page = MagicMock()
        page.save_screenshot = AsyncMock()
        page.get_content = AsyncMock(return_value = "<html></html>")
        test_bot.page = page

        with (
            patch.object(test_bot, "web_text_first_available", side_effect = [TimeoutError(), TimeoutError(), TimeoutError(), TimeoutError()]),
            patch.object(test_bot, "web_find_first_available", side_effect = TimeoutError()),
        ):
            result = await test_bot.get_login_state()
            assert result.is_logged_in is False
            assert result.reason == LoginDetectionReason.SELECTOR_TIMEOUT

        page.save_screenshot.assert_awaited_once()
        page.get_content.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_login_state_selector_timeout_does_not_capture_diagnostics_when_disabled(self, test_bot:KleinanzeigenBot, tmp_path:Path) -> None:
        test_bot.config.diagnostics = DiagnosticsConfig.model_validate({"capture_on": {"login_detection": False}, "output_dir": str(tmp_path)})

        page = MagicMock()
        page.save_screenshot = AsyncMock()
        page.get_content = AsyncMock(return_value = "<html></html>")
        test_bot.page = page

        with (
            patch.object(test_bot, "web_text_first_available", side_effect = [TimeoutError(), TimeoutError(), TimeoutError(), TimeoutError()]),
            patch.object(test_bot, "web_find_first_available", side_effect = TimeoutError()),
        ):
            result = await test_bot.get_login_state()
            assert result.is_logged_in is False
            assert result.reason == LoginDetectionReason.SELECTOR_TIMEOUT

        page.save_screenshot.assert_not_called()
        page.get_content.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_login_state_selector_timeout_pauses_for_inspection_when_enabled_and_interactive(
        self, test_bot:KleinanzeigenBot, tmp_path:Path
    ) -> None:
        test_bot.config.diagnostics = DiagnosticsConfig.model_validate(
            {"capture_on": {"login_detection": True}, "pause_on_login_detection_failure": True, "output_dir": str(tmp_path)}
        )

        page = MagicMock()
        page.save_screenshot = AsyncMock()
        page.get_content = AsyncMock(return_value = "<html></html>")
        test_bot.page = page

        stdin_mock = MagicMock()
        stdin_mock.isatty.return_value = True

        with (
            patch.object(
                test_bot,
                "web_text_first_available",
                side_effect = [
                    TimeoutError(),
                    TimeoutError(),
                    TimeoutError(),
                    TimeoutError(),
                    TimeoutError(),
                    TimeoutError(),
                    TimeoutError(),
                    TimeoutError(),
                ],
            ),
            patch.object(test_bot, "web_find_first_available", side_effect = TimeoutError()),
            patch("kleinanzeigen_bot.sys.stdin", stdin_mock),
            patch("kleinanzeigen_bot.ainput", new_callable = AsyncMock) as mock_ainput,
        ):
            first_result = await test_bot.get_login_state()
            assert first_result.is_logged_in is False
            assert first_result.reason == LoginDetectionReason.SELECTOR_TIMEOUT
            # Call twice to ensure the capture/pause guard triggers only once per process.
            second_result = await test_bot.get_login_state()
            assert second_result.is_logged_in is False
            assert second_result.reason == LoginDetectionReason.SELECTOR_TIMEOUT

        page.save_screenshot.assert_awaited_once()
        page.get_content.assert_awaited_once()
        mock_ainput.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_login_state_selector_timeout_does_not_pause_when_non_interactive(self, test_bot:KleinanzeigenBot, tmp_path:Path) -> None:
        test_bot.config.diagnostics = DiagnosticsConfig.model_validate(
            {"capture_on": {"login_detection": True}, "pause_on_login_detection_failure": True, "output_dir": str(tmp_path)}
        )

        page = MagicMock()
        page.save_screenshot = AsyncMock()
        page.get_content = AsyncMock(return_value = "<html></html>")
        test_bot.page = page

        stdin_mock = MagicMock()
        stdin_mock.isatty.return_value = False

        with (
            patch.object(test_bot, "web_text_first_available", side_effect = [TimeoutError(), TimeoutError(), TimeoutError(), TimeoutError()]),
            patch.object(test_bot, "web_find_first_available", side_effect = TimeoutError()),
            patch("kleinanzeigen_bot.sys.stdin", stdin_mock),
            patch("kleinanzeigen_bot.ainput", new_callable = AsyncMock) as mock_ainput,
        ):
            result = await test_bot.get_login_state()
            assert result.is_logged_in is False
            assert result.reason == LoginDetectionReason.SELECTOR_TIMEOUT

        mock_ainput.assert_not_called()

    @pytest.mark.asyncio
    async def test_login_flow_completes_successfully(self, test_bot:KleinanzeigenBot) -> None:
        """Verify that normal login flow completes successfully."""
        with (
            patch.object(test_bot, "web_open") as mock_open,
            patch.object(
                test_bot,
                "get_login_state",
                new_callable = AsyncMock,
                side_effect = [
                    _login_detection_result(False, LoginDetectionReason.CTA_MATCH),
                    _login_detection_result(True, LoginDetectionReason.USER_INFO_MATCH),
                ],
            ) as mock_logged_in,
            patch.object(test_bot, "_click_gdpr_banner", new_callable = AsyncMock),
            patch.object(test_bot, "fill_login_data_and_send", new_callable = AsyncMock) as mock_fill,
            patch.object(test_bot, "handle_after_login_logic", new_callable = AsyncMock) as mock_after_login,
            patch.object(test_bot, "_dismiss_consent_banner", new_callable = AsyncMock),
        ):
            await test_bot.login()

            opened_urls = [call.args[0] for call in mock_open.call_args_list]
            assert any(url.startswith(test_bot.root_url) for url in opened_urls)
            assert any(url.endswith("/m-einloggen-sso.html") for url in opened_urls)
            mock_logged_in.assert_awaited()
            mock_fill.assert_awaited_once()
            mock_after_login.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_login_flow_returns_early_when_already_logged_in(self, test_bot:KleinanzeigenBot) -> None:
        """Login should return early when state is already LOGGED_IN."""
        with (
            patch.object(test_bot, "web_open") as mock_open,
            patch.object(
                test_bot,
                "get_login_state",
                new_callable = AsyncMock,
                return_value = _login_detection_result(True, LoginDetectionReason.USER_INFO_MATCH),
            ) as mock_state,
            patch.object(test_bot, "_click_gdpr_banner", new_callable = AsyncMock),
            patch.object(test_bot, "fill_login_data_and_send", new_callable = AsyncMock) as mock_fill,
            patch.object(test_bot, "handle_after_login_logic", new_callable = AsyncMock) as mock_after_login,
        ):
            await test_bot.login()

            mock_open.assert_awaited_once()
            assert mock_open.await_args is not None
            assert mock_open.await_args.args[0] == test_bot.root_url
            mock_state.assert_awaited_once()
            mock_fill.assert_not_called()
            mock_after_login.assert_not_called()

    @pytest.mark.asyncio
    async def test_login_flow_raises_when_state_remains_inconclusive(self, test_bot:KleinanzeigenBot) -> None:
        """Post-login inconclusive state should fail fast with diagnostics."""
        with (
            patch.object(test_bot, "web_open"),
            patch.object(
                test_bot,
                "get_login_state",
                new_callable = AsyncMock,
                side_effect = [
                    _login_detection_result(False, LoginDetectionReason.CTA_MATCH),
                    _login_detection_result(False, LoginDetectionReason.SELECTOR_TIMEOUT),
                ],
            ) as mock_state,
            patch.object(test_bot, "_click_gdpr_banner", new_callable = AsyncMock),
            patch.object(test_bot, "fill_login_data_and_send", new_callable = AsyncMock),
            patch.object(test_bot, "handle_after_login_logic", new_callable = AsyncMock),
            patch.object(test_bot, "_dismiss_consent_banner", new_callable = AsyncMock),
            patch.object(test_bot, "_capture_login_detection_diagnostics_if_enabled", new_callable = AsyncMock) as mock_diagnostics,
        ):
            with pytest.raises(AssertionError, match = "reason=SELECTOR_TIMEOUT"):
                await test_bot.login()

            mock_diagnostics.assert_awaited_once()
            assert mock_diagnostics.await_args is not None
            assert mock_diagnostics.await_args.kwargs.get("base_prefix") == "login_detection_selector_timeout"
            mock_state.assert_awaited()
            assert mock_state.await_count == 2
            assert mock_state.await_args_list[1].kwargs.get("capture_diagnostics") is False

    @pytest.mark.asyncio
    async def test_capture_login_detection_diagnostics_honors_capture_log_copy(self, test_bot:KleinanzeigenBot, tmp_path:Path) -> None:
        test_bot.config.diagnostics = DiagnosticsConfig.model_validate(
            {
                "capture_on": {"login_detection": True},
                "capture_log_copy": True,
                "output_dir": str(tmp_path),
            }
        )
        test_bot.log_file_path = str(tmp_path / "bot.log")
        test_bot._login_detection_diagnostics_captured = False

        page = MagicMock()
        test_bot.page = page

        with patch("kleinanzeigen_bot.utils.diagnostics.capture_diagnostics", new_callable = AsyncMock) as mock_capture:
            await test_bot._capture_login_detection_diagnostics_if_enabled(base_prefix = "login_detection_test")

            mock_capture.assert_awaited_once()
            assert mock_capture.await_args is not None
            assert mock_capture.await_args.kwargs.get("base_prefix") == "login_detection_test"
            assert mock_capture.await_args.kwargs.get("copy_log") is True
            assert mock_capture.await_args.kwargs.get("log_file_path") == test_bot.log_file_path
            assert test_bot._login_detection_diagnostics_captured is True

    @pytest.mark.asyncio
    async def test_capture_login_detection_diagnostics_with_no_page_still_invokes_capture(self, test_bot:KleinanzeigenBot, tmp_path:Path) -> None:
        test_bot.config.diagnostics = DiagnosticsConfig.model_validate(
            {
                "capture_on": {"login_detection": True},
                "capture_log_copy": True,
                "output_dir": str(tmp_path),
            }
        )
        test_bot.log_file_path = str(tmp_path / "bot.log")
        test_bot._login_detection_diagnostics_captured = False
        test_bot.page = cast(Any, None)

        with patch("kleinanzeigen_bot.utils.diagnostics.capture_diagnostics", new_callable = AsyncMock) as mock_capture:
            await test_bot._capture_login_detection_diagnostics_if_enabled(base_prefix = "login_detection_test")

            mock_capture.assert_awaited_once()
            assert mock_capture.await_args is not None
            assert mock_capture.await_args.kwargs.get("page") is None
            assert mock_capture.await_args.kwargs.get("copy_log") is True
            assert mock_capture.await_args.kwargs.get("log_file_path") == test_bot.log_file_path
            assert test_bot._login_detection_diagnostics_captured is True

    @pytest.mark.asyncio
    async def test_capture_login_detection_diagnostics_does_not_mark_captured_on_output_dir_error(self, test_bot:KleinanzeigenBot, tmp_path:Path) -> None:
        test_bot.config.diagnostics = DiagnosticsConfig.model_validate(
            {
                "capture_on": {"login_detection": True},
                "capture_log_copy": True,
                "output_dir": str(tmp_path),
            }
        )
        test_bot._login_detection_diagnostics_captured = False
        test_bot.page = MagicMock()

        with (
            patch.object(test_bot, "_diagnostics_output_dir", side_effect = RuntimeError("dir error")),
            patch("kleinanzeigen_bot.utils.diagnostics.capture_diagnostics", new_callable = AsyncMock) as mock_capture,
        ):
            await test_bot._capture_login_detection_diagnostics_if_enabled(base_prefix = "login_detection_test")

            mock_capture.assert_not_awaited()
            assert test_bot._login_detection_diagnostics_captured is False

    @pytest.mark.asyncio
    async def test_capture_login_detection_diagnostics_does_not_mark_captured_on_capture_error(self, test_bot:KleinanzeigenBot, tmp_path:Path) -> None:
        test_bot.config.diagnostics = DiagnosticsConfig.model_validate(
            {
                "capture_on": {"login_detection": True},
                "capture_log_copy": True,
                "output_dir": str(tmp_path),
            }
        )
        test_bot._login_detection_diagnostics_captured = False
        test_bot.page = MagicMock()

        with patch(
            "kleinanzeigen_bot.utils.diagnostics.capture_diagnostics",
            new_callable = AsyncMock,
            side_effect = RuntimeError("capture error"),
        ):
            await test_bot._capture_login_detection_diagnostics_if_enabled(base_prefix = "login_detection_test")

            assert test_bot._login_detection_diagnostics_captured is False

    def test_login_detection_result_accepts_logged_in_user_info_match(self) -> None:
        result = LoginDetectionResult(is_logged_in = True, reason = LoginDetectionReason.USER_INFO_MATCH)
        assert result.is_logged_in is True
        assert result.reason == LoginDetectionReason.USER_INFO_MATCH

    @pytest.mark.parametrize("reason", [LoginDetectionReason.CTA_MATCH, LoginDetectionReason.SELECTOR_TIMEOUT])
    def test_login_detection_result_rejects_invalid_logged_in_reason(self, reason:LoginDetectionReason) -> None:
        with pytest.raises(ValueError, match = "USER_INFO_MATCH"):
            LoginDetectionResult(is_logged_in = True, reason = reason)

    def test_login_detection_result_rejects_invalid_logged_out_user_info_match(self) -> None:
        with pytest.raises(ValueError, match = "CTA_MATCH or SELECTOR_TIMEOUT"):
            LoginDetectionResult(is_logged_in = False, reason = LoginDetectionReason.USER_INFO_MATCH)

    def test_login_detection_result_rejects_non_bool_is_logged_in(self) -> None:
        with pytest.raises(TypeError, match = "is_logged_in must be a bool"):
            LoginDetectionResult(is_logged_in = "yes", reason = LoginDetectionReason.CTA_MATCH)  # type: ignore[arg-type]

    def test_login_detection_result_rejects_non_enum_reason(self) -> None:
        with pytest.raises(TypeError, match = "reason must be a LoginDetectionReason"):
            LoginDetectionResult(is_logged_in = False, reason = "bogus")  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_login_flow_raises_when_sso_navigation_times_out(self, test_bot:KleinanzeigenBot) -> None:
        """SSO navigation timeout should trigger diagnostics and re-raise."""
        with (
            patch.object(test_bot, "web_open", new_callable = AsyncMock, side_effect = [None, TimeoutError("sso timeout")]),
            patch.object(
                test_bot,
                "get_login_state",
                new_callable = AsyncMock,
                return_value = _login_detection_result(False, LoginDetectionReason.CTA_MATCH),
            ) as mock_state,
            patch.object(test_bot, "_click_gdpr_banner", new_callable = AsyncMock),
            patch.object(test_bot, "_capture_login_detection_diagnostics_if_enabled", new_callable = AsyncMock) as mock_diagnostics,
        ):
            with pytest.raises(TimeoutError, match = "sso timeout"):
                await test_bot.login()

            mock_diagnostics.assert_awaited_once()
            assert mock_diagnostics.await_args is not None
            assert mock_diagnostics.await_args.kwargs.get("base_prefix") == "login_detection_sso_navigation_timeout"
            mock_state.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fill_login_data_and_send(self, test_bot:KleinanzeigenBot) -> None:
        """Verify that login form filling works correctly."""
        with (
            patch.object(test_bot, "_wait_for_auth0_login_context", new_callable = AsyncMock) as wait_context,
            patch.object(test_bot, "_handle_identifier_captcha_state", new_callable = AsyncMock),
            patch.object(test_bot, "_wait_for_auth0_password_step", new_callable = AsyncMock) as wait_password,
            patch.object(test_bot, "_wait_for_post_auth0_submit_transition", new_callable = AsyncMock) as wait_transition,
            patch.object(test_bot, "web_input") as mock_input,
            patch.object(test_bot, "web_click") as mock_click,
            patch("kleinanzeigen_bot.captcha_flow.check_and_wait_for_captcha", new_callable = AsyncMock) as mock_captcha,
        ):
            await test_bot.fill_login_data_and_send()

            wait_context.assert_awaited_once()
            wait_password.assert_awaited_once()
            wait_transition.assert_awaited_once()
            mock_captcha.assert_awaited_once_with(test_bot, test_bot.config.captcha, is_login_page = True)
            assert mock_input.call_args_list == [
                call(By.ID, "username", test_bot.config.login.username),
                call(By.CSS_SELECTOR, "input[type='password']", test_bot.config.login.password),
            ]
            assert mock_click.call_args_list == [
                call(By.CSS_SELECTOR, "button[type='submit']"),
                call(By.CSS_SELECTOR, "button[type='submit']"),
            ]

    @pytest.mark.asyncio
    async def test_fill_login_data_and_send_fails_when_password_step_missing(self, test_bot:KleinanzeigenBot) -> None:
        """Missing Auth0 password step should fail fast."""
        with (
            patch.object(test_bot, "_wait_for_auth0_login_context", new_callable = AsyncMock),
            patch.object(test_bot, "_handle_identifier_captcha_state", new_callable = AsyncMock),
            patch.object(test_bot, "_wait_for_auth0_password_step", new_callable = AsyncMock, side_effect = AssertionError("missing password")),
            patch.object(test_bot, "web_input") as mock_input,
            patch.object(test_bot, "web_click") as mock_click,
        ):
            with pytest.raises(AssertionError, match = "missing password"):
                await test_bot.fill_login_data_and_send()

            assert mock_input.call_count == 1
            assert mock_click.call_count == 1

    @pytest.mark.asyncio
    async def test_wait_for_post_auth0_submit_transition_url_branch(self, test_bot:KleinanzeigenBot) -> None:
        """URL transition success should return without fallback checks."""
        with (
            patch.object(test_bot, "web_await", new_callable = AsyncMock, return_value = True) as mock_wait,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as mock_sleep,
        ):
            await test_bot._wait_for_post_auth0_submit_transition()

            mock_wait.assert_awaited_once()
            mock_sleep.assert_not_called()

    @pytest.mark.asyncio
    async def test_wait_for_post_auth0_submit_transition_dom_fallback_branch(self, test_bot:KleinanzeigenBot) -> None:
        """DOM fallback should run when URL transition is inconclusive."""
        with (
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = [TimeoutError()]) as mock_wait,
            patch.object(test_bot, "is_logged_in", new_callable = AsyncMock, return_value = True) as mock_is_logged_in,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as mock_sleep,
        ):
            await test_bot._wait_for_post_auth0_submit_transition()

            mock_wait.assert_awaited_once()
            mock_is_logged_in.assert_awaited_once()
            mock_sleep.assert_not_called()

    @pytest.mark.asyncio
    async def test_wait_for_post_auth0_submit_transition_sleep_fallback_branch(self, test_bot:KleinanzeigenBot) -> None:
        """Sleep fallback should run when bounded login check times out."""
        with (
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = [TimeoutError()]) as mock_wait,
            patch.object(test_bot, "is_logged_in", new_callable = AsyncMock, side_effect = asyncio.TimeoutError) as mock_is_logged_in,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as mock_sleep,
        ):
            with pytest.raises(TimeoutError, match = "Auth0 post-submit verification remained inconclusive"):
                await test_bot._wait_for_post_auth0_submit_transition()

            mock_wait.assert_awaited_once()
            assert mock_is_logged_in.await_count == 2
            mock_sleep.assert_awaited_once()
            assert mock_sleep.await_args is not None
            sleep_kwargs = cast(Any, mock_sleep.await_args).kwargs
            assert sleep_kwargs["min_ms"] < sleep_kwargs["max_ms"]

    @pytest.mark.asyncio
    async def test_click_gdpr_banner_uses_quick_dom_timeout_and_clicks_found_element(self, test_bot:KleinanzeigenBot) -> None:
        mock_element = AsyncMock()
        with (
            patch.object(test_bot, "timeout", return_value = 1.25) as mock_timeout,
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = mock_element) as mock_probe,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as mock_sleep,
        ):
            await test_bot._click_gdpr_banner()

            mock_timeout.assert_called_once_with("quick_dom")
            mock_probe.assert_awaited_once()
            assert mock_probe.await_args is not None
            assert mock_probe.await_args.args == (By.ID, "gdpr-banner-accept")
            mock_element.click.assert_awaited_once()
            mock_sleep.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_click_gdpr_banner_does_nothing_when_banner_absent(self, test_bot:KleinanzeigenBot) -> None:
        with (
            patch.object(test_bot, "timeout", return_value = 1.25),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = None) as mock_probe,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as mock_sleep,
        ):
            await test_bot._click_gdpr_banner()

            mock_probe.assert_awaited_once()
            mock_sleep.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_check_sms_verification_prompts_user_when_detected(self, test_bot:KleinanzeigenBot) -> None:
        mock_element = MagicMock()
        with (
            patch.object(test_bot, "timeout", return_value = 3.0) as mock_timeout,
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = mock_element) as mock_probe,
            patch("kleinanzeigen_bot.ainput", new_callable = AsyncMock) as mock_ainput,
        ):
            await test_bot._check_sms_verification()

            mock_timeout.assert_called_once_with("sms_verification")
            mock_probe.assert_awaited_once()
            assert mock_probe.await_args is not None
            assert mock_probe.await_args.args == (By.TEXT, "Wir haben dir gerade einen 6-stelligen Code für die Telefonnummer")
            mock_ainput.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_check_sms_verification_returns_silently_when_absent(self, test_bot:KleinanzeigenBot) -> None:
        with (
            patch.object(test_bot, "timeout", return_value = 3.0),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = None) as mock_probe,
            patch("kleinanzeigen_bot.ainput", new_callable = AsyncMock) as mock_ainput,
        ):
            await test_bot._check_sms_verification()

            mock_probe.assert_awaited_once()
            mock_ainput.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_check_email_verification_prompts_user_when_detected(self, test_bot:KleinanzeigenBot) -> None:
        mock_element = MagicMock()
        with (
            patch.object(test_bot, "timeout", return_value = 4.0) as mock_timeout,
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = mock_element) as mock_probe,
            patch("kleinanzeigen_bot.ainput", new_callable = AsyncMock) as mock_ainput,
        ):
            await test_bot._check_email_verification()

            mock_timeout.assert_called_once_with("email_verification")
            mock_probe.assert_awaited_once()
            assert mock_probe.await_args is not None
            assert mock_probe.await_args.args == (By.TEXT, "Um dein Konto zu schützen haben wir dir eine E-Mail geschickt")
            mock_ainput.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_check_email_verification_returns_silently_when_absent(self, test_bot:KleinanzeigenBot) -> None:
        with (
            patch.object(test_bot, "timeout", return_value = 4.0),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = None) as mock_probe,
            patch("kleinanzeigen_bot.ainput", new_callable = AsyncMock) as mock_ainput,
        ):
            await test_bot._check_email_verification()

            mock_probe.assert_awaited_once()
            mock_ainput.assert_not_awaited()


class TestKleinanzeigenBotDiagnostics:
    @pytest.fixture
    def diagnostics_ad_config(self) -> dict[str, Any]:
        return {
            "active": True,
            "type": "OFFER",
            "title": "Test ad title",
            "description": "Test description",
            "category": "161/176/sonstige",
            "price_type": "NEGOTIABLE",
            "shipping_type": "PICKUP",
            "sell_directly": False,
            "contact": {
                "name": "Tester",
                "zipcode": "12345",
            },
            "republication_interval": 7,
        }

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_publish_ads_captures_diagnostics_on_failures(
        self,
        test_bot:KleinanzeigenBot,
        tmp_path:Path,
        diagnostics_ad_config:dict[str, Any],
    ) -> None:
        """Ensure publish failures capture diagnostics artifacts."""
        log_file_path = tmp_path / "test.log"
        log_file_path.write_text("Test log content\n", encoding = "utf-8")
        test_bot.log_file_path = str(log_file_path)

        test_bot.config.diagnostics = DiagnosticsConfig.model_validate({"capture_on": {"publish": True}, "output_dir": str(tmp_path)})

        page = MagicMock()
        page.save_screenshot = AsyncMock()
        page.get_content = AsyncMock(return_value = "<html></html>")
        page.sleep = AsyncMock()
        page.url = "https://example.com/fail"
        test_bot.page = page

        ad_cfg = Ad.model_validate(diagnostics_ad_config)
        ad_cfg_orig = copy.deepcopy(diagnostics_ad_config)
        ad_file = str(tmp_path / "ad_000001_Test.yml")
        ads_response = {"content": json.dumps({"ads": [], "paging": {"pageNum": 1, "last": 1}})}

        with (
            patch.object(test_bot, "web_request", new_callable = AsyncMock, return_value = ads_response),
            patch("kleinanzeigen_bot.publishing_workflow.publish_ad", new_callable = AsyncMock, side_effect = TimeoutError("boom")),
        ):
            await test_bot.publish_ads([(ad_file, ad_cfg, ad_cfg_orig)])

        expected_retries = SUBMISSION_MAX_RETRIES
        assert page.save_screenshot.await_count == expected_retries
        assert page.get_content.await_count == expected_retries
        entries = os.listdir(tmp_path)
        html_files = [name for name in entries if fnmatch.fnmatch(name, "publish_error_*_attempt*_ad_000001_Test.html")]
        json_files = [name for name in entries if fnmatch.fnmatch(name, "publish_error_*_attempt*_ad_000001_Test.json")]
        assert len(html_files) == expected_retries
        assert len(json_files) == expected_retries

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_publish_ads_captures_log_copy_when_enabled(
        self,
        test_bot:KleinanzeigenBot,
        tmp_path:Path,
        diagnostics_ad_config:dict[str, Any],
    ) -> None:
        """Ensure publish failures copy log file when capture_log_copy is enabled."""
        log_file_path = tmp_path / "test.log"
        log_file_path.write_text("Test log content\n", encoding = "utf-8")
        test_bot.log_file_path = str(log_file_path)

        test_bot.config.diagnostics = DiagnosticsConfig.model_validate({"capture_on": {"publish": True}, "capture_log_copy": True, "output_dir": str(tmp_path)})

        page = MagicMock()
        page.save_screenshot = AsyncMock()
        page.get_content = AsyncMock(return_value = "<html></html>")
        page.sleep = AsyncMock()
        page.url = "https://example.com/fail"
        test_bot.page = page

        ad_cfg = Ad.model_validate(diagnostics_ad_config)
        ad_cfg_orig = copy.deepcopy(diagnostics_ad_config)
        ad_file = str(tmp_path / "ad_000001_Test.yml")
        ads_response = {"content": json.dumps({"ads": [], "paging": {"pageNum": 1, "last": 1}})}

        with (
            patch.object(test_bot, "web_request", new_callable = AsyncMock, return_value = ads_response),
            patch("kleinanzeigen_bot.publishing_workflow.publish_ad", new_callable = AsyncMock, side_effect = TimeoutError("boom")),
        ):
            await test_bot.publish_ads([(ad_file, ad_cfg, ad_cfg_orig)])

        entries = os.listdir(tmp_path)
        log_files = [name for name in entries if fnmatch.fnmatch(name, "publish_error_*_attempt*_ad_000001_Test.log")]
        assert len(log_files) == SUBMISSION_MAX_RETRIES

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_publish_ads_does_not_capture_diagnostics_when_disabled(
        self,
        test_bot:KleinanzeigenBot,
        tmp_path:Path,
        diagnostics_ad_config:dict[str, Any],
    ) -> None:
        """Ensure diagnostics are not captured when disabled."""
        test_bot.config.diagnostics = DiagnosticsConfig.model_validate({"capture_on": {"publish": False}, "output_dir": str(tmp_path)})

        page = MagicMock()
        page.save_screenshot = AsyncMock()
        page.get_content = AsyncMock(return_value = "<html></html>")
        page.sleep = AsyncMock()
        page.url = "https://example.com/fail"
        test_bot.page = page

        ad_cfg = Ad.model_validate(diagnostics_ad_config)
        ad_cfg_orig = copy.deepcopy(diagnostics_ad_config)
        ad_file = str(tmp_path / "ad_000001_Test.yml")

        with (
            patch.object(test_bot, "web_request", new_callable = AsyncMock, return_value = {"content": json.dumps({"ads": []})}),
            patch("kleinanzeigen_bot.publishing_workflow.publish_ad", new_callable = AsyncMock, side_effect = TimeoutError("boom")),
        ):
            await test_bot.publish_ads([(ad_file, ad_cfg, ad_cfg_orig)])

        page.save_screenshot.assert_not_called()
        page.get_content.assert_not_called()
        entries = os.listdir(tmp_path)
        html_files = [name for name in entries if fnmatch.fnmatch(name, "publish_error_*_attempt*_ad_000001_Test.html")]
        json_files = [name for name in entries if fnmatch.fnmatch(name, "publish_error_*_attempt*_ad_000001_Test.json")]
        assert not html_files
        assert not json_files


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

        with (
            patch.object(test_bot, "web_request", new_callable = AsyncMock, return_value = {"content": json.dumps(payload)}) as web_request_mock,
            patch("kleinanzeigen_bot.publishing_workflow.publish_ad", new_callable = AsyncMock) as publish_ad_mock,
            patch.object(test_bot, "web_await", new_callable = AsyncMock, return_value = True) as web_await_mock,
            patch("kleinanzeigen_bot.delete_flow.delete_ad", new_callable = AsyncMock) as delete_ad_mock,
        ):
            await test_bot.publish_ads(ad_cfgs)

            # web_request is called once for initial published-ads snapshot
            expected_url = f"{test_bot.root_url}/m-meine-anzeigen-verwalten.json?sort=DEFAULT&pageNum=1"
            web_request_mock.assert_awaited_once_with(expected_url)
            publish_ad_mock.assert_awaited_once()
            call_args = publish_ad_mock.call_args
            assert call_args is not None
            assert call_args.args[1] == "ad.yaml"
            assert call_args.args[2] is ad_cfgs[0][1]
            assert call_args.args[5] == AdUpdateStrategy.REPLACE
            web_await_mock.assert_awaited_once()
            delete_ad_mock.assert_awaited_once_with(
                web = test_bot, root_url = test_bot.root_url,
                ad_cfg = ad_cfgs[0][1],
                published_ads_list = [],
                delete_old_ads_by_title = False,
            )

    @pytest.mark.asyncio
    async def test_publish_ads_uses_millisecond_retry_delay_on_retryable_failure(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        mock_page:MagicMock,
    ) -> None:
        """Retry branch should sleep with explicit millisecond delay and reset price-reduction mutations."""
        test_bot.page = mock_page
        test_bot.keep_old_ads = True

        ad_cfg = Ad.model_validate(base_ad_config | {"price": 100, "price_reduction_count": 0, "repost_count": 1})
        ad_cfg_orig = copy.deepcopy(base_ad_config)
        ad_file = "ad.yaml"
        ads_response = {"content": json.dumps({"ads": [], "paging": {"pageNum": 1, "last": 1}})}
        seen_prices:list[tuple[int | None, int | None]] = []

        async def publish_side_effect(
            _web:Any,
            _ad_file:str,
            candidate_cfg:Ad,
            _candidate_orig:dict[str, Any],
            _published_ads:list[dict[str, Any]],
            _mode:AdUpdateStrategy,
            **kwargs:Any,
        ) -> None:
            seen_prices.append((candidate_cfg.price, candidate_cfg.price_reduction_count))
            if len(seen_prices) == 1:
                # Simulate in-memory mutation done by apply_auto_price_reduction before a failed attempt.
                candidate_cfg.price = 90
                candidate_cfg.price_reduction_count = 1
                raise TimeoutError("transient")

        with (
            patch.object(test_bot, "web_request", new_callable = AsyncMock, return_value = ads_response),
            patch("kleinanzeigen_bot.publishing_workflow.publish_ad", new_callable = AsyncMock, side_effect = publish_side_effect) as publish_mock,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as sleep_mock,
            patch.object(test_bot, "web_await", new_callable = AsyncMock, return_value = True),
        ):
            await test_bot.publish_ads([(ad_file, ad_cfg, ad_cfg_orig)])

            assert publish_mock.await_count == 2
            assert seen_prices == [(100, 0), (100, 0)]
            sleep_mock.assert_awaited_once_with(2_000)

    @pytest.mark.asyncio
    async def test_publish_ads_does_not_retry_when_submission_state_is_uncertain(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        mock_page:MagicMock,
    ) -> None:
        """Post-submit uncertainty must fail closed and skip retries."""
        test_bot.page = mock_page
        test_bot.keep_old_ads = True

        ad_cfg = Ad.model_validate(base_ad_config)
        ad_cfg_orig = copy.deepcopy(base_ad_config)
        ad_file = "ad.yaml"

        with (
            patch.object(
                test_bot,
                "web_request",
                new_callable = AsyncMock,
                return_value = {"content": json.dumps({"ads": [], "paging": {"pageNum": 1, "last": 1}})},
            ),
            patch(
                "kleinanzeigen_bot.publishing_workflow.publish_ad",
                new_callable = AsyncMock,
                side_effect = PublishSubmissionUncertainError("submission may have succeeded before failure"),
            ) as publish_mock,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as sleep_mock,
        ):
            await test_bot.publish_ads([(ad_file, ad_cfg, ad_cfg_orig)])

            assert publish_mock.await_count == 1
            sleep_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_publish_ads_does_not_retry_on_category_resolution_error(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        mock_page:MagicMock,
    ) -> None:
        """CategoryResolutionError is deterministic configuration failure -> no retry, fail fast."""
        test_bot.page = mock_page
        test_bot.keep_old_ads = True

        ad_cfg = Ad.model_validate(base_ad_config)
        ad_cfg_orig = copy.deepcopy(base_ad_config)

        with (
            patch.object(
                test_bot,
                "web_request",
                new_callable = AsyncMock,
                return_value = {"content": json.dumps({"ads": [], "paging": {"pageNum": 1, "last": 1}})},
            ),
            patch(
                "kleinanzeigen_bot.publishing_workflow.publish_ad",
                new_callable = AsyncMock,
                side_effect = CategoryResolutionError("no suggestion matched"),
            ) as publish_mock,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as sleep_mock,
        ):
            await test_bot.publish_ads([("ad.yaml", ad_cfg, ad_cfg_orig)])

            assert publish_mock.await_count == 1
            sleep_mock.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("mode", "expected_path"),
        [
            (AdUpdateStrategy.REPLACE, "/p-anzeige-aufgeben-schritt2.html"),
            (AdUpdateStrategy.MODIFY, "/p-anzeige-bearbeiten.html?adId=12345"),
        ],
        ids = ["replace", "modify"],
    )
    async def test_publish_ad_keeps_pre_submit_timeouts_retryable(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        mode:AdUpdateStrategy,
        expected_path:str,
    ) -> None:
        """Timeouts before submit boundary should remain plain retryable failures and force reload."""
        ad_cfg = Ad.model_validate(base_ad_config | {"id": 12345, "shipping_type": "NOT_APPLICABLE", "price_type": "NOT_APPLICABLE"})
        ad_cfg_orig = copy.deepcopy(base_ad_config)
        expected_url = f"{test_bot.root_url}{expected_path}"
        test_bot.keep_old_ads = True

        with (
            patch.object(test_bot, "web_open", new_callable = AsyncMock) as web_open_mock,
            patch.object(test_bot, "_dismiss_consent_banner", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.publishing_form.set_category", new_callable = AsyncMock, side_effect = TimeoutError("image upload timeout")),
            pytest.raises(TimeoutError, match = "image upload timeout"),
        ):
            await test_bot.publish_ad("ad.yaml", ad_cfg, ad_cfg_orig, [], mode)

        web_open_mock.assert_awaited_once_with(expected_url, reload_if_already_open = True)

    @staticmethod
    def _build_publish_ad_cfg(base_ad_config:dict[str, Any]) -> tuple[Ad, dict[str, Any]]:
        """Build ad config and original dict for publish_ad tests."""
        ad_cfg = Ad.model_validate(base_ad_config | {"id": 12345, "shipping_type": "NOT_APPLICABLE", "price_type": "NOT_APPLICABLE"})
        ad_cfg_orig = copy.deepcopy(base_ad_config)
        return ad_cfg, ad_cfg_orig

    @contextmanager
    def _mock_post_submit_dependencies(
        self,
        test_bot:KleinanzeigenBot,
        mock_page:MagicMock,
        *,
        web_await_side_effect:BaseException | None = None,
        web_execute_side_effect:list[Any] | None = None,
        redirect_recovery_return:int | None = None,
        redirect_recovery_side_effect:BaseException | None = None,
        mock_redirect_recovery:bool = True,
        include_success_mocks:bool = False,
    ) -> Iterator[None]:
        """Mock all post-submit publish_ad dependencies for confirmation fallback tests.

        Parameters
        ----------
            web_await_side_effect: Exception to raise from web_await (simulates confirmation timeout).
            redirect_recovery_return: Return value for _try_recover_ad_id_from_redirect.
            redirect_recovery_side_effect: Exception to raise from _try_recover_ad_id_from_redirect.
            include_success_mocks: If True, also mock dicts.save_dict (for success-path tests).
        """
        test_bot.page = mock_page

        common_patches:list[Any] = [
            patch.object(test_bot, "web_open", new_callable = AsyncMock),
            patch.object(test_bot, "_dismiss_consent_banner", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.publishing_form.set_category", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.publishing_form.set_pricing_fields", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.publishing_form.set_special_attributes", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.publishing_form.set_contact_fields", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.captcha_flow.check_and_wait_for_captcha", new_callable = AsyncMock),
            patch.object(test_bot, "web_input", new_callable = AsyncMock),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = None),
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_check", new_callable = AsyncMock, return_value = False),
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, side_effect = web_execute_side_effect),
            patch.object(test_bot, "web_find", new_callable = AsyncMock),
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, return_value = []),
            patch.object(test_bot, "_web_find_all_once", new_callable = AsyncMock, return_value = []),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = web_await_side_effect),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
        ]

        if mock_redirect_recovery:
            common_patches.append(
                patch(
                    "kleinanzeigen_bot.publishing_submission._try_recover_ad_id_from_redirect",
                    new_callable = AsyncMock,
                    return_value = redirect_recovery_return,
                    side_effect = redirect_recovery_side_effect,
                ),
            )

        if include_success_mocks:
            common_patches.append(patch("kleinanzeigen_bot.utils.dicts.save_dict"))

        with ExitStack() as stack:
            for p in common_patches:
                stack.enter_context(p)
            yield

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "web_await_error",
        [TimeoutError("confirmation timeout"), ProtocolException(MagicMock(), "connection lost", 0)],
        ids = ["timeout", "protocol-exception"],
    )
    async def test_publish_ad_marks_post_submit_errors_as_uncertain(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        mock_page:MagicMock,
        web_await_error:Exception,
    ) -> None:
        """Post-submit exceptions (TimeoutError, ProtocolException) should be converted to non-retryable uncertainty."""
        ad_cfg, ad_cfg_orig = self._build_publish_ad_cfg(base_ad_config)

        with (
            self._mock_post_submit_dependencies(test_bot, mock_page,
                                                web_await_side_effect = web_await_error,
                                                redirect_recovery_return = None),
            pytest.raises(PublishSubmissionUncertainError, match = "submission may have succeeded before failure"),
        ):
            await test_bot.publish_ad("ad.yaml", ad_cfg, ad_cfg_orig, [], AdUpdateStrategy.MODIFY)

    @pytest.mark.asyncio
    async def test_publish_ad_confirmation_fallback_from_referrer(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        mock_page:MagicMock,
    ) -> None:
        """When confirmation URL polling times out, ad ID should be recovered from document.referrer."""
        ad_cfg, ad_cfg_orig = self._build_publish_ad_cfg(base_ad_config)

        with self._mock_post_submit_dependencies(
            test_bot, mock_page,
            web_await_side_effect = TimeoutError("confirmation timeout"),
            redirect_recovery_return = 99887766,
            include_success_mocks = True,
        ):
            await test_bot.publish_ad("ad.yaml", ad_cfg, ad_cfg_orig, [], AdUpdateStrategy.MODIFY)

        assert ad_cfg_orig["id"] == 99887766

    @pytest.mark.asyncio
    async def test_publish_ad_ignores_stale_referrer_after_timeout(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        mock_page:MagicMock,
    ) -> None:
        """A stale pre-submit referrer must not recover the previous ad ID for a new publish attempt."""
        ad_cfg, ad_cfg_orig = self._build_publish_ad_cfg(base_ad_config)
        stale_confirmation_url = "https://www.kleinanzeigen.de/p-anzeige-aufgeben-bestaetigung.html?adId=99887766"

        with (
            self._mock_post_submit_dependencies(
                test_bot,
                mock_page,
                web_await_side_effect = TimeoutError("confirmation timeout"),
                web_execute_side_effect = [stale_confirmation_url, stale_confirmation_url, "var x = 42;"],
                mock_redirect_recovery = False,
            ),
            patch("kleinanzeigen_bot.publishing_persistence.persist_published_ad") as mock_persist,
            pytest.raises(PublishSubmissionUncertainError, match = "submission may have succeeded before failure"),
        ):
            await test_bot.publish_ad("ad.yaml", ad_cfg, ad_cfg_orig, [], AdUpdateStrategy.MODIFY)

        mock_persist.assert_not_called()
        assert ad_cfg_orig["id"] is None

    @pytest.mark.asyncio
    async def test_publish_ad_confirmation_fallback_when_redirect_happens_after_url_poll(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        mock_page:MagicMock,
    ) -> None:
        """When the confirmation URL was observed by polling but the page redirected before extraction, the fallback should recover the ad ID."""
        ad_cfg, ad_cfg_orig = self._build_publish_ad_cfg(base_ad_config)

        # web_await succeeds (confirmation URL was seen during polling), but the page
        # redirects before submit_and_confirm_ad can extract the URL, causing IndexError
        # in the extraction which falls into the except block and triggers the fallback.
        with self._mock_post_submit_dependencies(
            test_bot, mock_page,
            redirect_recovery_return = 55667788,
            include_success_mocks = True,
        ):
            await test_bot.publish_ad("ad.yaml", ad_cfg, ad_cfg_orig, [], AdUpdateStrategy.MODIFY)

        assert ad_cfg_orig["id"] == 55667788

    @pytest.mark.asyncio
    async def test_publish_ad_confirmation_fallback_from_tracking_raises_uncertain_when_not_found(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        mock_page:MagicMock,
    ) -> None:
        """When both confirmation URL polling and tracking fallback fail, PublishSubmissionUncertainError should be raised."""
        ad_cfg, ad_cfg_orig = self._build_publish_ad_cfg(base_ad_config)

        with (
            self._mock_post_submit_dependencies(test_bot, mock_page,
                                                web_await_side_effect = TimeoutError("confirmation timeout"),
                                                redirect_recovery_return = None),
            pytest.raises(PublishSubmissionUncertainError, match = "submission may have succeeded before failure"),
        ):
            await test_bot.publish_ad("ad.yaml", ad_cfg, ad_cfg_orig, [], AdUpdateStrategy.MODIFY)

    @pytest.mark.asyncio
    async def test_publish_ad_confirmation_fallback_helper_failure_still_raises_uncertain(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        mock_page:MagicMock,
    ) -> None:
        """When the tracking fallback helper itself raises, it should not change retry behavior — PublishSubmissionUncertainError must still be raised."""
        ad_cfg, ad_cfg_orig = self._build_publish_ad_cfg(base_ad_config)

        with (
            self._mock_post_submit_dependencies(test_bot, mock_page,
                                                web_await_side_effect = TimeoutError("confirmation timeout"),
                                                redirect_recovery_side_effect = RuntimeError("browser disconnected")),
            pytest.raises(PublishSubmissionUncertainError, match = "submission may have succeeded before failure"),
        ):
            await test_bot.publish_ad("ad.yaml", ad_cfg, ad_cfg_orig, [], AdUpdateStrategy.MODIFY)

    def test_get_config_file_path(self, test_bot:KleinanzeigenBot, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        """Test config file path handling."""
        monkeypatch.chdir(tmp_path)
        test_bot.config_file_path = os.path.abspath("config.yaml")
        default_path = os.path.abspath("config.yaml")
        assert test_bot.config_file_path == default_path
        test_path = os.path.abspath("custom_config.yaml")
        test_bot.config_file_path = test_path
        assert test_bot.config_file_path == test_path

    def test_get_log_file_path(self, test_bot:KleinanzeigenBot, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        """Test log file path handling."""
        monkeypatch.chdir(tmp_path)
        test_bot.log_file_path = os.path.abspath("kleinanzeigen_bot.log")
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


class TestKleinanzeigenBotUpdateAdsResilience:
    @staticmethod
    def _build_update_ad(base_ad_config:dict[str, Any], ad_id:int, title:str) -> tuple[str, Ad, dict[str, Any]]:
        ad_payload = copy.deepcopy(base_ad_config) | {"id": ad_id, "title": title}
        return (f"{ad_id}.yaml", Ad.model_validate(ad_payload), ad_payload)

    @staticmethod
    def _build_published_ads(*ad_ids:int) -> list[dict[str, Any]]:
        return [{"id": ad_id, "state": "active"} for ad_id in ad_ids]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("first_failure", "first_title"),
        [
            (TimeoutError("transient timeout"), "Timeout Ad"),
            (ProtocolException(MagicMock(), "connection lost", 0), "Protocol Failing"),
        ],
        ids = ["timeout_error", "protocol_exception"],
    )
    async def test_update_ads_continues_after_retryable_first_ad_failure(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        first_failure:Exception,
        first_title:str,
    ) -> None:
        ad_one = self._build_update_ad(base_ad_config, 101, first_title)
        ad_two = self._build_update_ad(base_ad_config, 102, "Success Ad")

        async def publish_side_effect(
            _web:Any,
            _ad_file:str,
            ad_cfg:Ad,
            _ad_cfg_orig:dict[str, Any],
            _published_ads:list[dict[str, Any]],
            _mode:AdUpdateStrategy,
            **kwargs:Any,
        ) -> None:
            if ad_cfg.id == 101:
                raise first_failure

        with (
            patch("kleinanzeigen_bot.published_ads.fetch_published_ads", new_callable = AsyncMock, return_value = self._build_published_ads(101, 102)),
            patch("kleinanzeigen_bot.publishing_workflow.publish_ad", new_callable = AsyncMock, side_effect = publish_side_effect) as publish_mock,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as sleep_mock,
            patch.object(test_bot, "web_await", new_callable = AsyncMock, return_value = True),
        ):
            await test_bot.update_ads([ad_one, ad_two])

        assert publish_mock.await_count == SUBMISSION_MAX_RETRIES + 1
        assert any(call.args[2].id == 102 for call in publish_mock.await_args_list)
        assert all(call.args[5] == AdUpdateStrategy.MODIFY for call in publish_mock.await_args_list)
        assert sleep_mock.await_count == SUBMISSION_MAX_RETRIES - 1

    @pytest.mark.asyncio
    async def test_update_ads_publish_submission_uncertain_is_not_retried(self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any]) -> None:
        ad_one = self._build_update_ad(base_ad_config, 301, "Uncertain Update")
        ad_two = self._build_update_ad(base_ad_config, 302, "Second Update")

        async def publish_side_effect(
            _web:Any,
            _ad_file:str,
            ad_cfg:Ad,
            _ad_cfg_orig:dict[str, Any],
            _published_ads:list[dict[str, Any]],
            _mode:AdUpdateStrategy,
            **kwargs:Any,
        ) -> None:
            if ad_cfg.id == 301:
                raise PublishSubmissionUncertainError("submission may have succeeded before failure")

        with (
            patch("kleinanzeigen_bot.published_ads.fetch_published_ads", new_callable = AsyncMock, return_value = self._build_published_ads(301, 302)),
            patch("kleinanzeigen_bot.publishing_workflow.publish_ad", new_callable = AsyncMock, side_effect = publish_side_effect) as publish_mock,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as sleep_mock,
            patch.object(test_bot, "web_await", new_callable = AsyncMock, return_value = True),
        ):
            await test_bot.update_ads([ad_one, ad_two])

        assert publish_mock.await_count == 2
        sleep_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_update_ads_category_resolution_error_is_not_retried(self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any]) -> None:
        ad_one = self._build_update_ad(base_ad_config, 303, "Category Error Update")
        ad_two = self._build_update_ad(base_ad_config, 304, "Second Update")

        async def publish_side_effect(
            _web:Any,
            _ad_file:str,
            ad_cfg:Ad,
            _ad_cfg_orig:dict[str, Any],
            _published_ads:list[dict[str, Any]],
            _mode:AdUpdateStrategy,
            **kwargs:Any,
        ) -> None:
            if ad_cfg.id == 303:
                raise CategoryResolutionError("no suggestion matched")

        with (
            patch("kleinanzeigen_bot.published_ads.fetch_published_ads", new_callable = AsyncMock, return_value = self._build_published_ads(303, 304)),
            patch("kleinanzeigen_bot.publishing_workflow.publish_ad", new_callable = AsyncMock, side_effect = publish_side_effect) as publish_mock,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as sleep_mock,
            patch.object(test_bot, "web_await", new_callable = AsyncMock, return_value = True),
        ):
            await test_bot.update_ads([ad_one, ad_two])

        assert [call.args[2].id for call in publish_mock.await_args_list] == [303, 304]
        sleep_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_update_ads_cancelled_error_propagates_immediately(self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any]) -> None:
        ad_one = self._build_update_ad(base_ad_config, 401, "Cancelled Ad")
        ad_two = self._build_update_ad(base_ad_config, 402, "Should Not Run")

        with (
            patch("kleinanzeigen_bot.published_ads.fetch_published_ads", new_callable = AsyncMock, return_value = self._build_published_ads(401, 402)),
            patch("kleinanzeigen_bot.publishing_workflow.publish_ad", new_callable = AsyncMock, side_effect = asyncio.CancelledError()) as publish_mock,
            patch.object(test_bot, "web_await", new_callable = AsyncMock, return_value = True),
            pytest.raises(asyncio.CancelledError),
        ):
            await test_bot.update_ads([ad_one, ad_two])

        assert publish_mock.await_count == 1

    @pytest.mark.asyncio
    async def test_update_ads_publishing_result_timeout_is_non_fatal(self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any]) -> None:
        ad_one = self._build_update_ad(base_ad_config, 501, "Result Timeout")

        with (
            patch("kleinanzeigen_bot.published_ads.fetch_published_ads", new_callable = AsyncMock, return_value = self._build_published_ads(501)),
            patch("kleinanzeigen_bot.publishing_workflow.publish_ad", new_callable = AsyncMock) as publish_mock,
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = TimeoutError("result timeout")),
        ):
            await test_bot.update_ads([ad_one])

        publish_mock.assert_awaited_once()


class TestDisplayCounterProgression:
    """Regression tests for issue #977: progress counter must increment for every ad, including skipped ones."""

    @staticmethod
    def _build_ad(base_ad_config:dict[str, Any], ad_id:int | None, title:str) -> tuple[str, Ad, dict[str, Any]]:
        ad_payload = copy.deepcopy(base_ad_config) | {"id": ad_id, "title": title}
        return (f"{ad_id}.yaml", Ad.model_validate(ad_payload), ad_payload)

    @staticmethod
    def _build_published_ads(*ad_specs:tuple[int, str]) -> list[dict[str, Any]]:
        return [{"id": ad_id, "state": state} for ad_id, state in ad_specs]

    @pytest.mark.asyncio
    async def test_publish_ads_counter_progression_with_paused_ads(
        self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any], caplog:pytest.LogCaptureFixture
    ) -> None:
        """Display counter must advance for paused ads, and only non-paused ads are published."""
        ad_cfgs = [
            self._build_ad(base_ad_config, 101, "Paused Ad 1"),
            self._build_ad(base_ad_config, 102, "Active Ad 102"),
            self._build_ad(base_ad_config, 103, "Paused Ad 2"),
        ]
        published_ads = self._build_published_ads((101, "paused"), (102, "active"), (103, "paused"))

        with (
            caplog.at_level(logging.INFO),
            patch("kleinanzeigen_bot.published_ads.fetch_published_ads", new_callable = AsyncMock, return_value = published_ads),
            patch("kleinanzeigen_bot.publishing_workflow.publish_ad", new_callable = AsyncMock) as publish_mock,
            patch.object(test_bot, "web_await", new_callable = AsyncMock, return_value = True),
        ):
            await test_bot.publish_ads(ad_cfgs)

        processing = [r for r in caplog.records if r.message.startswith("Processing")]
        assert len(processing) == 3
        assert "1/3" in processing[0].message
        assert "2/3" in processing[1].message
        assert "3/3" in processing[2].message

        skip_msgs = [r for r in caplog.records if "Skipping because ad is reserved" in r.message]
        assert len(skip_msgs) == 2

        publish_mock.assert_awaited_once()
        assert publish_mock.call_args.args[2].id == 102

        summary = [r for r in caplog.records if "DONE:" in r.message]
        assert any("1 ad" in r.message for r in summary)

    @pytest.mark.asyncio
    async def test_update_ads_counter_progression_with_paused_ads(
        self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any], caplog:pytest.LogCaptureFixture
    ) -> None:
        """Display counter must advance for paused ads, and only non-paused ads are updated."""
        ad_cfgs = [
            self._build_ad(base_ad_config, 201, "Paused Ad 1"),
            self._build_ad(base_ad_config, 202, "Active Ad 202"),
            self._build_ad(base_ad_config, 203, "Paused Ad 2"),
        ]
        published_ads = self._build_published_ads((201, "paused"), (202, "active"), (203, "paused"))

        with (
            caplog.at_level(logging.INFO),
            patch("kleinanzeigen_bot.published_ads.fetch_published_ads", new_callable = AsyncMock, return_value = published_ads),
            patch("kleinanzeigen_bot.publishing_workflow.publish_ad", new_callable = AsyncMock) as publish_mock,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, return_value = True),
        ):
            await test_bot.update_ads(ad_cfgs)

        processing = [r for r in caplog.records if r.message.startswith("Processing")]
        assert len(processing) == 3
        assert "1/3" in processing[0].message
        assert "2/3" in processing[1].message
        assert "3/3" in processing[2].message

        skip_msgs = [r for r in caplog.records if "Skipping because ad is reserved" in r.message]
        assert len(skip_msgs) == 2

        publish_mock.assert_awaited_once()
        assert publish_mock.call_args.args[2].id == 202

        summary = [r for r in caplog.records if "DONE:" in r.message]
        assert any("1 ad" in r.message for r in summary)

    @pytest.mark.asyncio
    async def test_update_ads_counter_includes_not_found_ads(
        self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any], caplog:pytest.LogCaptureFixture
    ) -> None:
        """Display counter must advance even for ads not found in published ads."""
        ad_cfgs = [
            self._build_ad(base_ad_config, 301, "Not Found Ad"),
            self._build_ad(base_ad_config, 302, "Active Ad 302"),
        ]
        published_ads = self._build_published_ads((302, "active"))

        with (
            caplog.at_level(logging.INFO),
            patch("kleinanzeigen_bot.published_ads.fetch_published_ads", new_callable = AsyncMock, return_value = published_ads),
            patch("kleinanzeigen_bot.publishing_workflow.publish_ad", new_callable = AsyncMock) as publish_mock,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, return_value = True),
        ):
            await test_bot.update_ads(ad_cfgs)

        processing = [r for r in caplog.records if r.message.startswith("Processing")]
        assert len(processing) == 2
        assert "1/2" in processing[0].message
        assert "2/2" in processing[1].message
        assert "Not Found Ad" in processing[0].message

        skip_msgs = [r for r in caplog.records if "SKIPPED" in r.message and "not found" in r.message]
        assert len(skip_msgs) == 1

        publish_mock.assert_awaited_once()
        assert publish_mock.call_args.args[2].id == 302


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
        config_path.write_text(
            """
login:
    username: test
    password: test
""",
            encoding = "utf-8",
        )
        test_bot.config_file_path = str(config_path)
        await test_bot.run(["script.py", "verify", "--config", str(config_path), "--workspace-mode", "portable"])
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
        with patch("kleinanzeigen_bot.download_flow.download_ads", new_callable = AsyncMock) as mock_download:
            await test_bot.run(["script.py", "download"])
            assert test_bot.ads_selector == "new"
            mock_download.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_update_default_selector(self, test_bot:KleinanzeigenBot, mock_config_setup:None) -> None:  # pylint: disable=unused-argument
        """Test running update command with default selector falls back to changed."""
        with patch.object(test_bot, "load_ads", return_value = []):
            await test_bot.run(["script.py", "update"])
            assert test_bot.ads_selector == "changed"

    @pytest.mark.asyncio
    async def test_run_extend_default_selector(self, test_bot:KleinanzeigenBot, mock_config_setup:None) -> None:  # pylint: disable=unused-argument
        """Test running extend command with default selector falls back to all."""
        with patch.object(test_bot, "load_ads", return_value = []):
            await test_bot.run(["script.py", "extend"])
            assert test_bot.ads_selector == "all"


class TestKleinanzeigenBotAdManagement:
    """Tests for ad management functionality."""

    @pytest.mark.asyncio
    async def test_download_ads_with_specific_ids(self, test_bot:KleinanzeigenBot, mock_config_setup:None) -> None:  # pylint: disable=unused-argument
        """Test downloading ads with specific IDs."""
        test_bot.ads_selector = "123,456"
        with patch("kleinanzeigen_bot.download_flow.download_ads", new_callable = AsyncMock) as mock_download:
            await test_bot.run(["script.py", "download", "--ads=123,456"])
            assert test_bot.ads_selector == "123,456"
            mock_download.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_publish_invalid_selector(self, test_bot:KleinanzeigenBot, mock_config_setup:None) -> None:  # pylint: disable=unused-argument
        """Test running publish with invalid selector exits with error."""
        with pytest.raises(SystemExit) as exc_info:
            await test_bot.run(["script.py", "publish", "--ads=invalid"])
        assert exc_info.value.code == 2

    @pytest.mark.asyncio
    async def test_run_download_invalid_selector(self, test_bot:KleinanzeigenBot, mock_config_setup:None) -> None:  # pylint: disable=unused-argument
        """Test running download with invalid selector exits with error."""
        with pytest.raises(SystemExit) as exc_info:
            await test_bot.run(["script.py", "download", "--ads=invalid"])
        assert exc_info.value.code == 2

    @pytest.mark.asyncio
    async def test_run_update_invalid_selector(self, test_bot:KleinanzeigenBot, mock_config_setup:None) -> None:  # pylint: disable=unused-argument
        """Test running update with invalid selector exits with error."""
        with pytest.raises(SystemExit) as exc_info:
            await test_bot.run(["script.py", "update", "--ads=invalid"])
        assert exc_info.value.code == 2

    @pytest.mark.asyncio
    async def test_run_extend_invalid_selector(self, test_bot:KleinanzeigenBot, mock_config_setup:None) -> None:  # pylint: disable=unused-argument
        """Test running extend with invalid selector exits with error."""
        with pytest.raises(SystemExit) as exc_info:
            await test_bot.run(["script.py", "extend", "--ads=invalid"])
        assert exc_info.value.code == 2


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
        ad_cfg = Ad.model_validate(
            base_ad_config
            | {
                "shipping_options": ["DHL_2", "Hermes_Päckchen"],
                "updated_on": "2024-01-01T00:00:00",  # Add created_on to prevent KeyError
                "created_on": "2024-01-01T00:00:00",  # Add updated_on for consistency
            }
        )

        # Create the original ad config and published ads list
        ad_cfg.update_content_hash()  # Add content hash to prevent republication
        ad_cfg_orig = ad_cfg.model_dump()
        published_ads:list[dict[str, Any]] = []

        # Set up default config values needed for the test
        test_bot.config.publishing = PublishingConfig.model_validate({"delete_old_ads": "BEFORE_PUBLISH", "delete_old_ads_by_title": False})

        # Create temporary file path
        ad_file = Path(tmp_path) / "test_ad.yaml"

        # Mock web_execute to handle all JavaScript calls
        async def mock_web_execute(script:str) -> Any:
            if script == "document.body.scrollHeight":
                return 0  # Return integer to prevent scrolling loop
            if "window.location.href" in script:
                return test_bot.page.url  # Return confirmation URL for ad_id extraction
            return None

        # Create mock elements
        csrf_token_elem = MagicMock()
        csrf_token_elem.attrs = {"content": "csrf-token-123"}

        shipping_form_elem = MagicMock()
        shipping_form_elem.attrs = {}

        shipping_size_radio = MagicMock()
        shipping_size_radio.attrs = {"checked": ""}  # SMALL radio is pre-checked

        shipping_checkbox = MagicMock()
        shipping_checkbox.attrs = {"checked": ""}  # Simulate pre-checked carriers for SMALL

        category_path_elem = MagicMock()
        category_path_elem.apply = AsyncMock(return_value = "Test Category")

        # Mock the necessary web interaction methods
        with (
            patch.object(test_bot, "web_execute", side_effect = mock_web_execute),
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            patch.object(test_bot, "web_find", new_callable = AsyncMock) as mock_find,
            patch.object(test_bot, "web_probe", new_callable = AsyncMock) as mock_probe,
            patch.object(test_bot, "web_select", new_callable = AsyncMock),
            patch.object(test_bot, "web_input", new_callable = AsyncMock),
            patch.object(test_bot, "web_open", new_callable = AsyncMock),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch.object(test_bot, "web_check", new_callable = AsyncMock, return_value = True),
            patch.object(test_bot, "web_request", new_callable = AsyncMock),
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock),
            patch.object(test_bot, "web_await", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.publishing_form.set_contact_fields", new_callable = AsyncMock),
            patch("builtins.input", return_value = ""),
            patch.object(test_bot, "web_scroll_page_down", new_callable = AsyncMock),
        ):

            async def probe_side_effect(selector_type:By, selector_value:str, **_:Any) -> Element | None:
                if selector_type == By.ID and selector_value == "ad-category-path":
                    return category_path_elem
                return None

            mock_probe.side_effect = probe_side_effect

            # Mock web_find to simulate element detection
            async def mock_find_side_effect(selector_type:By, selector_value:str, **_:Any) -> Element | None:
                if selector_value == "meta[name=_csrf]":
                    return csrf_token_elem
                if selector_value == "myftr-shppngcrt-frm":
                    return shipping_form_elem
                # New shipping dialog: size radio via XPath with value attribute
                if selector_type == By.XPATH and '@type="radio"' in selector_value and "@value=" in selector_value:
                    return shipping_size_radio
                if selector_type == By.XPATH and '@type="checkbox"' in selector_value and "@value=" in selector_value:
                    return shipping_checkbox
                return None

            mock_find.side_effect = mock_find_side_effect

            # Mock web_check to return True for radio button checked state
            with patch.object(test_bot, "web_check", new_callable = AsyncMock) as mock_check:
                mock_check.return_value = True

                # Test through the public interface by publishing an ad
                await test_bot.publish_ad(str(ad_file), ad_cfg, ad_cfg_orig, published_ads)

            # Verify that the shipping dialog was interacted with:
            # - web_find should have been called for the size radio (XPath with @type="radio")
            # - web_click should have been called to deselect unwanted carriers and close dialog
            radio_find_calls = [c for c in mock_find.await_args_list if len(c.args) >= 2 and '@type="radio"' in str(c.args[1])]
            assert len(radio_find_calls) >= 1, "Expected at least one web_find for size radio"

            click_xpath_values = [str(c.args[1]) for c in mock_click.await_args_list if len(c.args) >= 2 and c.args[0] == By.XPATH]
            # Should click Weiter, deselect HERMES_002 (unwanted), and click Fertig
            assert any("Weiter" in v for v in click_xpath_values), "Expected click on Weiter button"
            assert any("HERMES_002" in v for v in click_xpath_values), "Expected click to deselect HERMES_002"
            assert not any("DHL_001" in v for v in click_xpath_values), "Did not expect click for wanted DHL_001"
            assert not any("HERMES_001" in v for v in click_xpath_values), "Did not expect click for wanted HERMES_001"
            assert any("Fertig" in v for v in click_xpath_values), "Expected click on Fertig button"

            # Verify the file was created in the temporary directory
            assert ad_file.exists()

    @pytest.mark.asyncio
    async def test_cross_drive_path_fallback_windows(self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any]) -> None:
        """Test that cross-drive path handling falls back to absolute path on Windows."""
        # Create ad config
        ad_cfg = Ad.model_validate(
            base_ad_config
            | {
                "updated_on": "2024-01-01T00:00:00",
                "created_on": "2024-01-01T00:00:00",
                "auto_price_reduction": {"enabled": True, "strategy": "FIXED", "amount": 10, "min_price": 50, "delay_reposts": 0, "delay_days": 0},
                "price": 100,
                "repost_count": 1,
                "price_reduction_count": 0,
            }
        )
        ad_cfg.update_content_hash()
        ad_cfg_orig = ad_cfg.model_dump()

        # Simulate Windows cross-drive scenario
        # Config on D:, ad file on C:
        test_bot.config_file_path = "D:\\project\\config.yaml"
        ad_file = "C:\\temp\\test_ad.yaml"

        # Create a sentinel exception to abort publish_ad early
        class _SentinelException(Exception):
            pass

        # Track what path argument __apply_auto_price_reduction receives
        recorded_path:list[str] = []

        def mock_apply_auto_price_reduction(
            ad_cfg:Ad,
            ad_cfg_orig:dict[str, Any],
            ad_file_relative:str,
            *,
            mode:AdUpdateStrategy = AdUpdateStrategy.REPLACE,
        ) -> None:
            _ = mode
            recorded_path.append(ad_file_relative)
            raise _SentinelException("Abort early for test")

        # Mock Path to use PureWindowsPath for testing cross-drive behavior
        with (
            patch("kleinanzeigen_bot.Path", PureWindowsPath),
            patch("kleinanzeigen_bot.price_reduction.apply_auto_price_reduction", side_effect = mock_apply_auto_price_reduction),
            patch.object(test_bot, "web_open", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.delete_flow.delete_ad", new_callable = AsyncMock),
        ):
            # Call publish_ad and expect sentinel exception
            try:
                await test_bot.publish_ad(ad_file, ad_cfg, ad_cfg_orig, [], AdUpdateStrategy.REPLACE)
                pytest.fail("Expected _SentinelException to be raised")
            except _SentinelException:
                # This is expected - the test aborts early
                pass

        # Verify the path argument is the absolute path (fallback behavior)
        assert len(recorded_path) == 1
        assert recorded_path[0] == ad_file, f"Expected absolute path fallback, got: {recorded_path[0]}"

    @pytest.mark.asyncio
    async def test_auto_price_reduction_conditional_on_mode_and_on_update(
        self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any], tmp_path:Path
    ) -> None:
        """Test price reduction dispatch across REPLACE and MODIFY modes.

        - REPLACE mode always calls apply_auto_price_reduction.
        - MODIFY mode with on_update=false still calls it for restore-first (no cycle advance).
        - MODIFY mode with on_update=true calls it with full evaluation and cycle advance.
        """
        # Shared ad config with auto price reduction enabled
        ad_cfg = Ad.model_validate(
            base_ad_config
            | {
                "id": 12345,
                "price": 200,
                "auto_price_reduction": {"enabled": True, "strategy": "FIXED", "amount": 50, "min_price": 50, "delay_reposts": 0, "delay_days": 0},
                "repost_count": 1,
                "price_reduction_count": 0,
                "updated_on": "2024-01-01T00:00:00",
                "created_on": "2024-01-01T00:00:00",
            }
        )
        ad_cfg.update_content_hash()
        ad_cfg_orig = ad_cfg.model_dump()

        mock_response = {"statusCode": 200, "statusMessage": "OK", "content": "{}"}

        async def mock_web_execute_price_reduction(script:str) -> Any:
            if "window.location.href" in script:
                return "https://www.kleinanzeigen.de/p-anzeige-aufgeben-bestaetigung.html?adId=12345"
            return mock_response

        async def mock_web_probe(selector_type:Any, selector_value:str, **_kwargs:Any) -> Any:
            if selector_value == "ad-category-path":
                marker = MagicMock()
                marker.apply = AsyncMock(return_value = "")
                return marker
            return None

        with (
            patch("kleinanzeigen_bot.price_reduction.apply_auto_price_reduction") as mock_apply,
            patch("kleinanzeigen_bot.delete_flow.delete_ad", new_callable = AsyncMock),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = mock_web_probe),
            patch.object(test_bot, "web_find", new_callable = AsyncMock),
            patch.object(test_bot, "web_input", new_callable = AsyncMock),
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_open", new_callable = AsyncMock),
            patch.object(test_bot, "web_select", new_callable = AsyncMock),
            patch.object(test_bot, "web_check", new_callable = AsyncMock, return_value = False),
            patch.object(test_bot, "web_await", new_callable = AsyncMock),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch.object(test_bot, "web_execute", side_effect = mock_web_execute_price_reduction),
            patch.object(test_bot, "web_request", new_callable = AsyncMock, return_value = mock_response),
            patch.object(test_bot, "web_scroll_page_down", new_callable = AsyncMock),
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, return_value = []),
            patch.object(test_bot, "_web_find_all_once", new_callable = AsyncMock, return_value = []),
            patch("kleinanzeigen_bot.captcha_flow.check_and_wait_for_captcha", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.publishing_form.set_contact_fields", new_callable = AsyncMock),
            patch("builtins.input", return_value = ""),
            patch("kleinanzeigen_bot.utils.misc.ainput", new_callable = AsyncMock, return_value = ""),
        ):
            test_bot.page = MagicMock()
            test_bot.page.url = "https://www.kleinanzeigen.de/p-anzeige-aufgeben-bestaetigung.html?adId=12345"
            test_bot.config.publishing.delete_old_ads = "BEFORE_PUBLISH"

            # --- REPLACE mode: always calls apply_auto_price_reduction ---
            await test_bot.publish_ad(str(tmp_path / "ad.yaml"), ad_cfg, ad_cfg_orig, [], AdUpdateStrategy.REPLACE)
            mock_apply.assert_called_once()
            assert mock_apply.call_args.kwargs["mode"] == AdUpdateStrategy.REPLACE

            # --- MODIFY mode with default config (on_update=false): still calls for restore-first ---
            mock_apply.reset_mock()
            await test_bot.publish_ad(str(tmp_path / "ad.yaml"), ad_cfg, ad_cfg_orig, [], AdUpdateStrategy.MODIFY)
            mock_apply.assert_called_once()
            assert mock_apply.call_args.kwargs["mode"] == AdUpdateStrategy.MODIFY

            # --- MODIFY mode with on_update=true: SHOULD call (new conditional behavior) ---
            mock_apply.reset_mock()
            ad_cfg.auto_price_reduction = AutoPriceReductionConfig(
                enabled = True,
                strategy = "FIXED",
                amount = 50,
                min_price = 50,
                delay_reposts = 0,
                delay_days = 0,
                on_update = True,
            )
            await test_bot.publish_ad(str(tmp_path / "ad.yaml"), ad_cfg, ad_cfg_orig, [], AdUpdateStrategy.MODIFY)
            mock_apply.assert_called_once()
            assert mock_apply.call_args.kwargs["mode"] == AdUpdateStrategy.MODIFY


class TestWantedShippingSelection:
    """Orchestration seam test for WANTED shipping delegation.

    Verifies that ``publish_ad`` / ``fill_ad_form`` delegates to
    ``kleinanzeigen_bot.publishing_form.set_shipping_form(self, ad_cfg, mode)``
    with the expected bot/ad/mode arguments. Does not validate selector labels
    or error behavior — those are covered by ``tests/unit/test_publishing_form.py``.
    """

    @pytest.mark.asyncio
    async def test_publish_ad_delegates_to_set_shipping_form(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        mock_page:MagicMock,
        tmp_path:Path,
    ) -> None:
        """``publish_ad`` must delegate the shipping step to ``set_shipping_form``."""
        test_bot.page = mock_page
        test_bot.page.url = "https://www.kleinanzeigen.de/p-anzeige-aufgeben-bestaetigung.html?adId=12345"

        ad_cfg = Ad.model_validate(
            base_ad_config
            | {
                "type": "WANTED",
                "shipping_type": "SHIPPING",
                "shipping_options": [],
                "price_type": "NOT_APPLICABLE",
                "price": None,
            }
        )
        ad_cfg_orig = ad_cfg.model_dump()
        ad_file = str(tmp_path / "ad.yaml")

        async def execute_side_effect(script:str) -> Any:
            if "window.location.href" in script:
                return test_bot.page.url
            return None

        with (
            patch("kleinanzeigen_bot.ainput", new_callable = AsyncMock, return_value = ""),
            patch.object(test_bot, "web_open", new_callable = AsyncMock),
            patch.object(test_bot, "_dismiss_consent_banner", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.publishing_form.set_category", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.publishing_form.set_pricing_fields", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.publishing_form.set_special_attributes", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.publishing_form.set_shipping_form", new_callable = AsyncMock) as mock_set_shipping_form,
            patch("kleinanzeigen_bot.publishing_form.set_contact_fields", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.publishing_form.fill_image_section", new_callable = AsyncMock),
            patch.object(test_bot, "web_set_input_value", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.captcha_flow.check_and_wait_for_captcha", new_callable = AsyncMock),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = None),
            patch.object(test_bot, "_web_find_all_once", new_callable = AsyncMock, return_value = []),
            patch.object(test_bot, "web_scroll_page_down", new_callable = AsyncMock),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, return_value = True),
            patch.object(test_bot, "web_execute", side_effect = execute_side_effect),
            patch.object(test_bot, "web_check", new_callable = AsyncMock),
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_find", new_callable = AsyncMock),
        ):
            await test_bot.publish_ad(ad_file, ad_cfg, ad_cfg_orig, [], AdUpdateStrategy.REPLACE)

        mock_set_shipping_form.assert_awaited_once_with(test_bot, ad_cfg, AdUpdateStrategy.REPLACE)
