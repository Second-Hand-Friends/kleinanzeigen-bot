# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import asyncio
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from kleinanzeigen_bot import login_flow
from kleinanzeigen_bot.app import KleinanzeigenBot
from kleinanzeigen_bot.login_flow import (
    LoginDetectionReason,
    LoginDetectionResult,
    check_email_verification,
    check_sms_verification,
    click_gdpr_banner,
    current_page_url,
    fill_login_data_and_send,
    handle_identifier_captcha_state,
    has_logged_out_cta,
    is_valid_post_auth0_destination,
    wait_for_post_auth0_submit_transition,
)
from kleinanzeigen_bot.model.config_model import DiagnosticsConfig
from kleinanzeigen_bot.utils.web_scraping_mixin import By, Element


def _login_detection_result(is_logged_in:bool, reason:LoginDetectionReason) -> LoginDetectionResult:
    return LoginDetectionResult(is_logged_in = is_logged_in, reason = reason)


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
            patch("kleinanzeigen_bot.login_flow.has_logged_out_cta", new_callable = AsyncMock, return_value = False),
        ):
            assert await test_bot.is_logged_in() is False

    @pytest.mark.asyncio
    async def test_has_logged_out_cta_requires_visible_candidate(self, test_bot:KleinanzeigenBot) -> None:
        matched_element = MagicMock(spec = Element)
        with (
            patch.object(test_bot, "web_find_first_available", new_callable = AsyncMock, return_value = (matched_element, 0)),
            patch.object(test_bot, "extract_visible_text", new_callable = AsyncMock, return_value = ""),
        ):
            assert await has_logged_out_cta(test_bot) is False

    @pytest.mark.asyncio
    async def test_has_logged_out_cta_accepts_visible_candidate(self, test_bot:KleinanzeigenBot) -> None:
        matched_element = MagicMock(spec = Element)
        with (
            patch.object(test_bot, "web_find_first_available", new_callable = AsyncMock, return_value = (matched_element, 0)),
            patch.object(test_bot, "extract_visible_text", new_callable = AsyncMock, return_value = "Einloggen"),
        ):
            assert await has_logged_out_cta(test_bot) is True

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

        assert current_page_url(test_bot) == "https://login.kleinanzeigen.de/u/login/password"

    def test_is_valid_post_auth0_destination_filters_invalid_urls(self, test_bot:KleinanzeigenBot) -> None:
        assert is_valid_post_auth0_destination("https://www.kleinanzeigen.de/") is True
        assert is_valid_post_auth0_destination("https://www.kleinanzeigen.de/m-meine-anzeigen.html") is True
        assert is_valid_post_auth0_destination("https://foo.kleinanzeigen.de/") is True
        assert is_valid_post_auth0_destination("unknown") is False
        assert is_valid_post_auth0_destination("about:blank") is False
        assert is_valid_post_auth0_destination("https://evilkleinanzeigen.de/") is False
        assert is_valid_post_auth0_destination("https://kleinanzeigen.de.evil.com/") is False
        assert is_valid_post_auth0_destination("https://login.kleinanzeigen.de/u/login/password") is False
        assert is_valid_post_auth0_destination("https://www.kleinanzeigen.de/login-error-500") is False

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
            patch.object(test_bot, "extract_visible_text", new_callable = AsyncMock, return_value = "Hier einloggen"),
        ):
            result = await test_bot.get_login_state()
            assert result.is_logged_in is False
            assert result.reason == LoginDetectionReason.CTA_MATCH
            assert web_text.await_count == 2

    @pytest.mark.asyncio
    async def test_get_login_state_checks_logged_out_cta_only_once(self, test_bot:KleinanzeigenBot) -> None:
        with (
            patch("kleinanzeigen_bot.login_flow.has_logged_in_marker", new_callable = AsyncMock, return_value = False),
            patch("kleinanzeigen_bot.login_flow.has_logged_out_cta", new_callable = AsyncMock, return_value = False) as cta_check,
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
            patch("kleinanzeigen_bot.login_flow.sys.stdin", stdin_mock),
            patch("kleinanzeigen_bot.login_flow.ainput", new_callable = AsyncMock) as mock_ainput,
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
            patch("kleinanzeigen_bot.login_flow.sys.stdin", stdin_mock),
            patch("kleinanzeigen_bot.login_flow.ainput", new_callable = AsyncMock) as mock_ainput,
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
            patch(
                "kleinanzeigen_bot.login_flow.get_login_state",
                new_callable = AsyncMock,
                side_effect = [
                    _login_detection_result(False, LoginDetectionReason.CTA_MATCH),
                    _login_detection_result(True, LoginDetectionReason.USER_INFO_MATCH),
                ],
            ) as mock_logged_in,
            patch("kleinanzeigen_bot.login_flow.click_gdpr_banner", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.login_flow.fill_login_data_and_send", new_callable = AsyncMock) as mock_fill,
            patch("kleinanzeigen_bot.login_flow.handle_after_login_logic", new_callable = AsyncMock) as mock_after_login,
            patch.object(test_bot, "dismiss_consent_banner", new_callable = AsyncMock),
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
            patch(
                "kleinanzeigen_bot.login_flow.get_login_state",
                new_callable = AsyncMock,
                return_value = _login_detection_result(True, LoginDetectionReason.USER_INFO_MATCH),
            ) as mock_state,
            patch("kleinanzeigen_bot.login_flow.click_gdpr_banner", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.login_flow.fill_login_data_and_send", new_callable = AsyncMock) as mock_fill,
            patch("kleinanzeigen_bot.login_flow.handle_after_login_logic", new_callable = AsyncMock) as mock_after_login,
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
            patch(
                "kleinanzeigen_bot.login_flow.get_login_state",
                new_callable = AsyncMock,
                side_effect = [
                    _login_detection_result(False, LoginDetectionReason.CTA_MATCH),
                    _login_detection_result(False, LoginDetectionReason.SELECTOR_TIMEOUT),
                ],
            ) as mock_state,
            patch("kleinanzeigen_bot.login_flow.click_gdpr_banner", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.login_flow.fill_login_data_and_send", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.login_flow.handle_after_login_logic", new_callable = AsyncMock),
            patch.object(test_bot, "dismiss_consent_banner", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.login_flow.capture_login_detection_diagnostics_if_enabled", new_callable = AsyncMock) as mock_diagnostics,
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
            await login_flow.capture_login_detection_diagnostics_if_enabled(
                web = test_bot,
                base_prefix = "login_detection_test",
                diagnostics_config = test_bot.config.diagnostics,
                diagnostics_output_dir_fn = test_bot._diagnostics_output_dir,
                log_file_path = test_bot.log_file_path,
            )

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
            await login_flow.capture_login_detection_diagnostics_if_enabled(
                web = test_bot,
                base_prefix = "login_detection_test",
                diagnostics_config = test_bot.config.diagnostics,
                diagnostics_output_dir_fn = test_bot._diagnostics_output_dir,
                log_file_path = test_bot.log_file_path,
            )

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
            patch("kleinanzeigen_bot.utils.diagnostics.capture_diagnostics", new_callable = AsyncMock) as mock_capture,
        ):
            await login_flow.capture_login_detection_diagnostics_if_enabled(
                web = test_bot,
                base_prefix = "login_detection_test",
                diagnostics_config = test_bot.config.diagnostics,
                diagnostics_output_dir_fn = MagicMock(side_effect = RuntimeError("dir error")),
                log_file_path = test_bot.log_file_path,
            )

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
            await login_flow.capture_login_detection_diagnostics_if_enabled(
                web = test_bot,
                base_prefix = "login_detection_test",
                diagnostics_config = test_bot.config.diagnostics,
                diagnostics_output_dir_fn = test_bot._diagnostics_output_dir,
                log_file_path = test_bot.log_file_path,
            )

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
            patch(
                "kleinanzeigen_bot.login_flow.get_login_state",
                new_callable = AsyncMock,
                return_value = _login_detection_result(False, LoginDetectionReason.CTA_MATCH),
            ) as mock_state,
            patch("kleinanzeigen_bot.login_flow.click_gdpr_banner", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.login_flow.capture_login_detection_diagnostics_if_enabled", new_callable = AsyncMock) as mock_diagnostics,
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
            patch("kleinanzeigen_bot.login_flow.wait_for_auth0_login_context", new_callable = AsyncMock) as wait_context,
            patch("kleinanzeigen_bot.login_flow.handle_identifier_captcha_state", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.login_flow.wait_for_auth0_password_step", new_callable = AsyncMock) as wait_password,
            patch("kleinanzeigen_bot.login_flow.wait_for_post_auth0_submit_transition", new_callable = AsyncMock) as wait_transition,
            patch.object(test_bot, "web_input") as mock_input,
            patch("kleinanzeigen_bot.login_flow._click_auth0_submit", new_callable = AsyncMock) as mock_submit,
            patch("kleinanzeigen_bot.captcha_flow.check_and_wait_for_captcha", new_callable = AsyncMock) as mock_captcha,
        ):
            await fill_login_data_and_send(
                test_bot,
                username = test_bot.config.login.username,
                password = test_bot.config.login.password,
                captcha_config = test_bot.config.captcha,
            )

            wait_context.assert_awaited_once()
            wait_password.assert_awaited_once()
            wait_transition.assert_awaited_once()
            mock_captcha.assert_awaited_once_with(test_bot, test_bot.config.captcha, is_login_page = True)
            assert mock_input.call_args_list == [
                call(By.ID, "username", test_bot.config.login.username),
                call(By.CSS_SELECTOR, "input[type='password']", test_bot.config.login.password),
            ]
            assert mock_submit.await_count == 2  # identifier submit + password submit

    @pytest.mark.asyncio
    async def test_fill_login_data_and_send_fails_when_password_step_missing(self, test_bot:KleinanzeigenBot) -> None:
        """Missing Auth0 password step should fail fast."""
        with (
            patch("kleinanzeigen_bot.login_flow.wait_for_auth0_login_context", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.login_flow.handle_identifier_captcha_state", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.login_flow.wait_for_auth0_password_step", new_callable = AsyncMock, side_effect = AssertionError("missing password")),
            patch.object(test_bot, "web_input") as mock_input,
            patch("kleinanzeigen_bot.login_flow._click_auth0_submit", new_callable = AsyncMock) as mock_submit,
        ):
            with pytest.raises(AssertionError, match = "missing password"):
                await fill_login_data_and_send(
                    test_bot,
                    username = test_bot.config.login.username,
                    password = test_bot.config.login.password,
                    captcha_config = test_bot.config.captcha,
                )

            assert mock_input.call_count == 1
            assert mock_submit.await_count == 1

    @pytest.mark.asyncio
    async def test_wait_for_post_auth0_submit_transition_url_branch(self, test_bot:KleinanzeigenBot) -> None:
        """URL transition success should return without fallback checks."""
        with (
            patch.object(test_bot, "web_await", new_callable = AsyncMock, return_value = True) as mock_wait,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as mock_sleep,
        ):
            await wait_for_post_auth0_submit_transition(test_bot, username = test_bot.config.login.username)

            mock_wait.assert_awaited_once()
            mock_sleep.assert_not_called()

    @pytest.mark.asyncio
    async def test_wait_for_post_auth0_submit_transition_dom_fallback_branch(self, test_bot:KleinanzeigenBot) -> None:
        """DOM fallback should run when URL transition is inconclusive."""
        with (
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = [TimeoutError()]) as mock_wait,
            patch("kleinanzeigen_bot.login_flow.is_logged_in", new_callable = AsyncMock, return_value = True) as mock_is_logged_in,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as mock_sleep,
        ):
            await wait_for_post_auth0_submit_transition(test_bot, username = test_bot.config.login.username)

            mock_wait.assert_awaited_once()
            mock_is_logged_in.assert_awaited_once()
            mock_sleep.assert_not_called()

    @pytest.mark.asyncio
    async def test_wait_for_post_auth0_submit_transition_sleep_fallback_branch(self, test_bot:KleinanzeigenBot) -> None:
        """Sleep fallback should run when bounded login check times out."""
        with (
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = [TimeoutError()]) as mock_wait,
            patch("kleinanzeigen_bot.login_flow.is_logged_in", new_callable = AsyncMock, side_effect = asyncio.TimeoutError) as mock_is_logged_in,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as mock_sleep,
            patch(
                "kleinanzeigen_bot.login_flow._classify_post_submit_state",
                new_callable = AsyncMock,
                return_value = "UNKNOWN (url=unknown)",
            ) as mock_classify,
            pytest.raises(TimeoutError) as exc_info,
        ):
            await wait_for_post_auth0_submit_transition(test_bot, username = test_bot.config.login.username)

        # The classified state must surface in the exception message
        assert "Auth0 post-submit verification remained inconclusive" in str(exc_info.value)
        assert "UNKNOWN (url=unknown)" in str(exc_info.value)

        mock_wait.assert_awaited_once()
        assert mock_is_logged_in.await_count == 2
        mock_sleep.assert_awaited_once()
        assert mock_sleep.await_args is not None
        sleep_kwargs = cast(Any, mock_sleep.await_args).kwargs
        assert sleep_kwargs["min_ms"] < sleep_kwargs["max_ms"]
        mock_classify.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_click_gdpr_banner_uses_quick_dom_timeout_and_clicks_found_element(self, test_bot:KleinanzeigenBot) -> None:
        mock_element = AsyncMock()
        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = mock_element) as mock_probe,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as mock_sleep,
        ):
            await click_gdpr_banner(test_bot)

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
            await click_gdpr_banner(test_bot)

            mock_probe.assert_awaited_once()
            mock_sleep.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_check_sms_verification_prompts_user_when_detected(self, test_bot:KleinanzeigenBot) -> None:
        mock_element = MagicMock()
        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = mock_element) as mock_probe,
            patch("kleinanzeigen_bot.login_flow.ainput", new_callable = AsyncMock) as mock_ainput,
        ):
            await check_sms_verification(test_bot)

            mock_probe.assert_awaited_once()
            assert mock_probe.await_args is not None
            assert mock_probe.await_args.args == (By.TEXT, "Wir haben dir gerade einen 6-stelligen Code für die Telefonnummer")
            mock_ainput.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_check_sms_verification_returns_silently_when_absent(self, test_bot:KleinanzeigenBot) -> None:
        with (
            patch.object(test_bot, "timeout", return_value = 3.0),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = None) as mock_probe,
            patch("kleinanzeigen_bot.login_flow.ainput", new_callable = AsyncMock) as mock_ainput,
        ):
            await check_sms_verification(test_bot)

            mock_probe.assert_awaited_once()
            mock_ainput.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_check_email_verification_prompts_user_when_detected(self, test_bot:KleinanzeigenBot) -> None:
        mock_element = MagicMock()
        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = mock_element) as mock_probe,
            patch("kleinanzeigen_bot.login_flow.ainput", new_callable = AsyncMock) as mock_ainput,
        ):
            await check_email_verification(test_bot)

            mock_probe.assert_awaited_once()
            assert mock_probe.await_args is not None
            assert mock_probe.await_args.args == (By.TEXT, "Um dein Konto zu schützen haben wir dir eine E-Mail geschickt")
            mock_ainput.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_check_email_verification_returns_silently_when_absent(self, test_bot:KleinanzeigenBot) -> None:
        with (
            patch.object(test_bot, "timeout", return_value = 4.0),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = None) as mock_probe,
            patch("kleinanzeigen_bot.login_flow.ainput", new_callable = AsyncMock) as mock_ainput,
        ):
            await check_email_verification(test_bot)

            mock_probe.assert_awaited_once()
            mock_ainput.assert_not_awaited()

    # ------------------------------------------------------------------
    # _detect_auth0_identifier_captcha
    # ------------------------------------------------------------------

    @pytest.mark.parametrize(
        ("detect_result", "probe_result", "expected"),
        [
            pytest.param(True, None, True, id = "iframe_detected"),
            pytest.param(False, MagicMock(spec = Element), True, id = "container_detected"),
            pytest.param(False, None, False, id = "nothing_found"),
        ],
    )
    @pytest.mark.asyncio
    async def test_detect_auth0_identifier_captcha(
        self, test_bot:KleinanzeigenBot, detect_result:bool, probe_result:Any, expected:bool
    ) -> None:
        """Returns True when captcha is detected (iframe or container), False otherwise."""
        with (
            patch("kleinanzeigen_bot.login_flow.captcha_flow.detect_captcha", new_callable = AsyncMock, return_value = detect_result),
            patch.object(test_bot, "timeout", return_value = 2.0),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = probe_result) as mock_probe,
        ):
            result = await login_flow._detect_auth0_identifier_captcha(test_bot)

        assert result is expected
        if detect_result:
            mock_probe.assert_not_called()
        else:
            mock_probe.assert_awaited_once()
            if probe_result is not None:
                selector = mock_probe.call_args.args[1]
                assert "[data-captcha-provider]" in selector
                assert "[class*='auth0-v2']" in selector

    # ------------------------------------------------------------------
    # _click_auth0_submit
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_click_auth0_submit(self, test_bot:KleinanzeigenBot) -> None:
        """Clicks the Auth0 primary submit button with the correct selector."""
        expected_selector = "button[type='submit'][data-action-button-primary='true']:not([disabled]):not([aria-disabled='true'])"

        with patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click:
            await login_flow._click_auth0_submit(test_bot)

        mock_click.assert_awaited_once()
        assert mock_click.call_args is not None
        assert mock_click.call_args.args[0] == By.CSS_SELECTOR
        assert mock_click.call_args.args[1] == expected_selector

    # ------------------------------------------------------------------
    # handle_identifier_captcha_state
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_handle_identifier_already_on_password_page(self, test_bot:KleinanzeigenBot) -> None:
        """Early return when already on the password page — no sleep, no detection, no submit."""
        with (
            patch("kleinanzeigen_bot.login_flow.current_page_url", return_value = "https://auth.example.com/u/login/password"),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as mock_sleep,
            patch("kleinanzeigen_bot.login_flow._detect_auth0_identifier_captcha", new_callable = AsyncMock) as mock_detect,
            patch("kleinanzeigen_bot.login_flow._click_auth0_submit", new_callable = AsyncMock) as mock_submit,
            patch("kleinanzeigen_bot.login_flow.ainput", new_callable = AsyncMock) as mock_ainput,
        ):
            await handle_identifier_captcha_state(test_bot)

        mock_sleep.assert_not_called()
        mock_detect.assert_not_called()
        mock_submit.assert_not_called()
        mock_ainput.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_identifier_navigates_during_grace_period(self, test_bot:KleinanzeigenBot) -> None:
        """Navigation completes during grace-period sleep — returns early, no detection/submit."""
        with (
            patch(
                "kleinanzeigen_bot.login_flow.current_page_url",
                side_effect = [
                    "https://auth.example.com/u/identifier",
                    "https://auth.example.com/u/login/password",
                ],
            ),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as mock_sleep,
            patch("kleinanzeigen_bot.login_flow._detect_auth0_identifier_captcha", new_callable = AsyncMock) as mock_detect,
            patch("kleinanzeigen_bot.login_flow._click_auth0_submit", new_callable = AsyncMock) as mock_submit,
            patch("kleinanzeigen_bot.login_flow.ainput", new_callable = AsyncMock) as mock_ainput,
        ):
            await handle_identifier_captcha_state(test_bot)

        mock_sleep.assert_awaited_once()
        mock_detect.assert_not_called()
        mock_submit.assert_not_called()
        mock_ainput.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_identifier_captcha_token_arrives_submit_succeeds(self, test_bot:KleinanzeigenBot) -> None:
        """Captcha detected, token arrives, submit click advances to password page."""
        with (
            patch(
                "kleinanzeigen_bot.login_flow.current_page_url",
                side_effect = [
                    "https://auth.example.com/u/identifier",
                    "https://auth.example.com/u/identifier",
                    "https://auth.example.com/u/identifier",
                    "https://auth.example.com/u/identifier",
                    "https://auth.example.com/u/login/password",
                ],
            ),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as mock_sleep,
            patch.object(test_bot, "web_await", new_callable = AsyncMock, return_value = True) as mock_await,
            patch("kleinanzeigen_bot.login_flow._detect_auth0_identifier_captcha", new_callable = AsyncMock, return_value = True),
            patch("kleinanzeigen_bot.login_flow._click_auth0_submit", new_callable = AsyncMock) as mock_submit,
            patch("kleinanzeigen_bot.login_flow.ainput", new_callable = AsyncMock) as mock_ainput,
        ):
            await handle_identifier_captcha_state(test_bot)

        mock_sleep.assert_awaited()
        assert mock_sleep.await_count == 2  # grace period + after submit
        mock_await.assert_awaited_once()  # token was waited for
        mock_submit.assert_awaited_once()  # submit was clicked
        mock_ainput.assert_not_called()  # no prompt shown

    @pytest.mark.asyncio
    async def test_handle_identifier_captcha_token_times_out_prompt_solves(self, test_bot:KleinanzeigenBot) -> None:
        """Captcha detected, token wait times out, user prompted, then navigates to password."""
        with (
            patch(
                "kleinanzeigen_bot.login_flow.current_page_url",
                side_effect = [
                    "https://auth.example.com/u/identifier",
                    "https://auth.example.com/u/identifier",
                    "https://auth.example.com/u/identifier",
                    "https://auth.example.com/u/login/password",
                ],
            ),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as mock_sleep,
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = TimeoutError) as mock_await,
            patch("kleinanzeigen_bot.login_flow._detect_auth0_identifier_captcha", new_callable = AsyncMock, return_value = True),
            patch("kleinanzeigen_bot.login_flow._click_auth0_submit", new_callable = AsyncMock) as mock_submit,
            patch("kleinanzeigen_bot.login_flow.ainput", new_callable = AsyncMock) as mock_ainput,
        ):
            await handle_identifier_captcha_state(test_bot)

        mock_sleep.assert_awaited_once()  # grace period only
        mock_await.assert_awaited_once()  # token wait was attempted (timed out)
        mock_submit.assert_not_called()  # no submit in the token branch
        mock_ainput.assert_awaited_once()  # prompt was shown

    @pytest.mark.asyncio
    async def test_handle_identifier_no_captcha_submit_succeeds(self, test_bot:KleinanzeigenBot) -> None:
        """No captcha detected, submit click advances to password page."""
        with (
            patch(
                "kleinanzeigen_bot.login_flow.current_page_url",
                side_effect = [
                    "https://auth.example.com/u/identifier",
                    "https://auth.example.com/u/identifier",
                    "https://auth.example.com/u/identifier",
                    "https://auth.example.com/u/identifier",
                    "https://auth.example.com/u/login/password",
                ],
            ),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as mock_sleep,
            patch("kleinanzeigen_bot.login_flow._detect_auth0_identifier_captcha", new_callable = AsyncMock, return_value = False),
            patch("kleinanzeigen_bot.login_flow._click_auth0_submit", new_callable = AsyncMock) as mock_submit,
            patch("kleinanzeigen_bot.login_flow.ainput", new_callable = AsyncMock) as mock_ainput,
        ):
            await handle_identifier_captcha_state(test_bot)

        mock_sleep.assert_awaited()
        assert mock_sleep.await_count == 2  # grace period + after submit
        mock_submit.assert_awaited_once()  # submit was clicked
        mock_ainput.assert_not_called()  # no prompt shown

    @pytest.mark.asyncio
    async def test_handle_identifier_no_captcha_submit_fails_prompt_solves(self, test_bot:KleinanzeigenBot) -> None:
        """No captcha detected, submit fails, user prompted, then navigates to password."""
        with (
            patch(
                "kleinanzeigen_bot.login_flow.current_page_url",
                side_effect = [
                    "https://auth.example.com/u/identifier",
                    "https://auth.example.com/u/identifier",
                    "https://auth.example.com/u/identifier",
                    "https://auth.example.com/u/identifier",
                    "https://auth.example.com/u/login/password",
                ],
            ),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as mock_sleep,
            patch("kleinanzeigen_bot.login_flow._detect_auth0_identifier_captcha", new_callable = AsyncMock, return_value = False),
            patch(
                "kleinanzeigen_bot.login_flow._click_auth0_submit",
                new_callable = AsyncMock,
                side_effect = TimeoutError,
            ) as mock_submit,
            patch("kleinanzeigen_bot.login_flow.ainput", new_callable = AsyncMock) as mock_ainput,
        ):
            await handle_identifier_captcha_state(test_bot)

        mock_sleep.assert_awaited_once()  # grace period only (submit failed, no post-submit sleep)
        mock_submit.assert_awaited_once()  # submit was attempted
        mock_ainput.assert_awaited_once()  # prompt was shown

    @pytest.mark.asyncio
    async def test_handle_identifier_url_changes_during_detection(self, test_bot:KleinanzeigenBot) -> None:
        """URL switches to password during captcha detection — returns early, no submit/prompt."""
        with (
            patch(
                "kleinanzeigen_bot.login_flow.current_page_url",
                side_effect = [
                    "https://auth.example.com/u/identifier",
                    "https://auth.example.com/u/identifier",
                    "https://auth.example.com/u/login/password",
                ],
            ),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as mock_sleep,
            patch("kleinanzeigen_bot.login_flow._detect_auth0_identifier_captcha", new_callable = AsyncMock) as mock_detect,
            patch("kleinanzeigen_bot.login_flow._click_auth0_submit", new_callable = AsyncMock) as mock_submit,
            patch("kleinanzeigen_bot.login_flow.ainput", new_callable = AsyncMock) as mock_ainput,
        ):
            await handle_identifier_captcha_state(test_bot)

        mock_sleep.assert_awaited_once()  # grace period
        mock_detect.assert_awaited_once()  # detection was called
        mock_submit.assert_not_called()  # no submit
        mock_ainput.assert_not_called()  # no prompt

    @pytest.mark.asyncio
    async def test_handle_identifier_prompt_final_attempt_succeeds(self, test_bot:KleinanzeigenBot) -> None:
        """No captcha, submit fails, user prompted, final attempt navigates to password."""
        with (
            patch(
                "kleinanzeigen_bot.login_flow.current_page_url",
                side_effect = [
                    "https://auth.example.com/u/identifier",     # initial check
                    "https://auth.example.com/u/identifier",     # after grace period
                    "https://auth.example.com/u/identifier",     # after detection
                    "https://auth.example.com/u/identifier",     # before no-captcha submit
                    "https://auth.example.com/u/identifier",     # after prompt — still stuck
                    "https://auth.example.com/u/login/password",  # after final attempt
                ],
            ),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as mock_sleep,
            patch("kleinanzeigen_bot.login_flow._detect_auth0_identifier_captcha", new_callable = AsyncMock, return_value = False),
            patch(
                "kleinanzeigen_bot.login_flow._click_auth0_submit",
                new_callable = AsyncMock,
                side_effect = [TimeoutError, None],  # first submit times out, final succeeds
            ) as mock_submit,
            patch("kleinanzeigen_bot.login_flow.ainput", new_callable = AsyncMock) as mock_ainput,
        ):
            await handle_identifier_captcha_state(test_bot)

        assert mock_submit.await_count == 2  # first submit + final attempt
        mock_ainput.assert_awaited_once()  # prompt was shown
        mock_sleep.assert_awaited()  # grace period sleep happened


class TestClassifyPostSubmitState:
    """Tests for _classify_post_submit_state()."""

    @pytest.mark.asyncio
    async def test_classify_still_on_password_page(self, test_bot:KleinanzeigenBot) -> None:
        """Should classify STILL_ON_PASSWORD_PAGE when URL contains /u/login/password."""
        with (
            patch("kleinanzeigen_bot.login_flow.current_page_url", return_value = "https://kleinanzeigen.de/u/login/password"),
            patch.object(test_bot, "timeout", return_value = 5.0),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = None),
        ):
            result = await login_flow._classify_post_submit_state(test_bot)

        assert "STILL_ON_PASSWORD_PAGE" in result
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_classify_auth0_error_selector_alert(self, test_bot:KleinanzeigenBot) -> None:
        """Should report auth0_error when [role='alert'] element has visible text."""
        mock_element = MagicMock(spec = Element)
        with (
            patch("kleinanzeigen_bot.login_flow.current_page_url", return_value = "https://kleinanzeigen.de/u/login/password"),
            patch.object(test_bot, "timeout", return_value = 5.0),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock) as mock_probe,
            patch.object(test_bot, "extract_visible_text", new_callable = AsyncMock, return_value = "Falsches Passwort"),
        ):
            # Return the mock element only for the FIRST probe ([role='alert'])
            mock_probe.side_effect = [mock_element, None, None, None]

            result = await login_flow._classify_post_submit_state(test_bot)

        assert "STILL_ON_PASSWORD_PAGE" in result
        assert "auth0_error='Falsches Passwort'" in result

    @pytest.mark.asyncio
    async def test_classify_auth0_error_selector_error_element_password(self, test_bot:KleinanzeigenBot) -> None:
        """Should report auth0_error when #error-element-password has visible text."""
        mock_element = MagicMock(spec = Element)
        with (
            patch("kleinanzeigen_bot.login_flow.current_page_url", return_value = "https://kleinanzeigen.de/u/login/password"),
            patch.object(test_bot, "timeout", return_value = 5.0),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock) as mock_probe,
            patch.object(test_bot, "extract_visible_text", new_callable = AsyncMock, return_value = "Password is required"),
        ):
            # Error probes 1-3 miss, 4th (#error-element-password) hits.
            # MFA probes (calls 5-7) all miss.
            mock_probe.side_effect = [None, None, None, mock_element, None, None, None]

            result = await login_flow._classify_post_submit_state(test_bot)

        assert "STILL_ON_PASSWORD_PAGE" in result
        assert "auth0_error='Password is required'" in result

    @pytest.mark.asyncio
    async def test_classify_mfa_one_time_code_input(self, test_bot:KleinanzeigenBot) -> None:
        """Should report MFA_DETECTED when input[autocomplete='one-time-code'] is present."""
        mock_element = MagicMock(spec = Element)
        with (
            patch("kleinanzeigen_bot.login_flow.current_page_url", return_value = "https://login.kleinanzeigen.de/u/mfa"),
            patch.object(test_bot, "timeout", return_value = 5.0),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock) as mock_probe,
        ):
            # Error selectors are gated to password page only, so only MFA
            # probes run. One-time-code probe (1st) hits; SMS and email miss.
            mock_probe.side_effect = [mock_element, None, None]

            result = await login_flow._classify_post_submit_state(test_bot)

        assert "MFA_DETECTED" in result
        assert "ONE_TIME_CODE_INPUT" in result

    @pytest.mark.asyncio
    async def test_classify_mfa_sms_prompt(self, test_bot:KleinanzeigenBot) -> None:
        """Should report SMS_VERIFICATION when German SMS prompt text is present."""
        mock_element = MagicMock(spec = Element)
        with (
            patch("kleinanzeigen_bot.login_flow.current_page_url", return_value = "https://login.kleinanzeigen.de/u/mfa-sms"),
            patch.object(test_bot, "timeout", return_value = 5.0),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock) as mock_probe,
        ):
            # Error selectors gated to password page. One-time-code probe (1st)
            # misses; SMS text probe (2nd) hits; email probe (3rd) misses.
            mock_probe.side_effect = [None, mock_element, None]

            result = await login_flow._classify_post_submit_state(test_bot)

        assert "MFA_DETECTED" in result
        assert "SMS_VERIFICATION" in result

    @pytest.mark.asyncio
    async def test_classify_unknown_fallback(self, test_bot:KleinanzeigenBot) -> None:
        """Should return UNKNOWN when no probe matches and URL is non-password."""
        with (
            patch("kleinanzeigen_bot.login_flow.current_page_url", return_value = "https://kleinanzeigen.de/meine-anzeigen"),
            patch.object(test_bot, "timeout", return_value = 5.0),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = None),
        ):
            result = await login_flow._classify_post_submit_state(test_bot)

        assert result.startswith("UNKNOWN (url=")
        assert "kleinanzeigen.de/meine-anzeigen" in result

    @pytest.mark.asyncio
    async def test_classify_combined_password_page_and_error(self, test_bot:KleinanzeigenBot) -> None:
        """Should preserve multiple facts: URL classification + auth0_error."""
        mock_element = MagicMock(spec = Element)
        with (
            patch("kleinanzeigen_bot.login_flow.current_page_url", return_value = "https://kleinanzeigen.de/u/login/password"),
            patch.object(test_bot, "timeout", return_value = 5.0),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock) as mock_probe,
            patch.object(test_bot, "extract_visible_text", new_callable = AsyncMock, return_value = "Invalid password"),
        ):
            mock_probe.side_effect = [mock_element, None, None, None]

            result = await login_flow._classify_post_submit_state(test_bot)

        assert "STILL_ON_PASSWORD_PAGE" in result
        assert "auth0_error='Invalid password'" in result
        # Verify both are present, not just one
        assert " + " in result

    @pytest.mark.asyncio
    async def test_classify_never_raises(self, test_bot:KleinanzeigenBot) -> None:
        """Should never raise, even when dependencies throw."""
        with (
            patch("kleinanzeigen_bot.login_flow.current_page_url", side_effect = RuntimeError("boom")),
        ):
            # Must not propagate the exception
            result = await login_flow._classify_post_submit_state(test_bot)

        assert result == "UNKNOWN (classification_error)"

    @pytest.mark.asyncio
    async def test_classify_snippet_sanitization(self, test_bot:KleinanzeigenBot) -> None:
        """Should sanitize error text: cap at 120 chars, collapse whitespace, remove quotes."""
        mock_element = MagicMock(spec = Element)
        long_text = (
            "  Falsches   Passwort oder   ungültige   Eingabe.   "
            "Bitte versuche es erneut.   "
            "Dies ist ein sehr langer Fehlertext der über 120 Zeichen lang sein wird. "
            "Man kann sich kaum vorstellen wie lang der ist. "
            "Er enthält auch 'Zitate' und mehrere Leerzeichen."
        )
        with (
            patch("kleinanzeigen_bot.login_flow.current_page_url", return_value = "https://kleinanzeigen.de/u/login/password"),
            patch.object(test_bot, "timeout", return_value = 5.0),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock) as mock_probe,
            patch.object(test_bot, "extract_visible_text", new_callable = AsyncMock, return_value = long_text),
        ):
            mock_probe.side_effect = [mock_element, None, None, None]

            result = await login_flow._classify_post_submit_state(test_bot)

        # Extract the snippet content (between formatting quotes of auth0_error='...')
        snippet_start = result.index("auth0_error='") + len("auth0_error='")
        snippet_end = result.index("'", snippet_start)
        snippet = result[snippet_start:snippet_end]

        # Verify single quotes were replaced with spaces in the snippet content
        assert "'" not in snippet, "Single quotes in error text should be replaced with spaces"

        # Verify no consecutive whitespace in the snippet content
        assert "  " not in snippet, "Consecutive whitespace should be collapsed"

        # Verify the snippet is at most 120 chars
        assert len(snippet) <= 120, f"Snippet should be capped at 120 chars, got {len(snippet)}"

        assert "auth0_error=" in result

    # ------------------------------------------------------------------ #
    #  MFA email verification test
    # ------------------------------------------------------------------ #

    @pytest.mark.asyncio
    async def test_classify_mfa_email_prompt(self, test_bot:KleinanzeigenBot) -> None:
        """Should report EMAIL_VERIFICATION when German email prompt text is present."""
        mock_element = MagicMock(spec = Element)
        with (
            patch("kleinanzeigen_bot.login_flow.current_page_url", return_value = "https://login.kleinanzeigen.de/u/mfa-email"),
            patch.object(test_bot, "timeout", return_value = 5.0),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock) as mock_probe,
        ):
            # Error selectors gated to password page. OTC probe (1st) misses,
            # SMS probe (2nd) misses, email prompt probe (3rd) hits.
            mock_probe.side_effect = [None, None, mock_element]

            result = await login_flow._classify_post_submit_state(test_bot)

        assert "MFA_DETECTED" in result
        assert "EMAIL_VERIFICATION" in result

    # ------------------------------------------------------------------ #
    #  Auth0 error text edge cases
    # ------------------------------------------------------------------ #

    @pytest.mark.asyncio
    async def test_classify_auth0_error_blank_text_not_reported(self, test_bot:KleinanzeigenBot) -> None:
        """Auth0 error element with whitespace-only text should not be reported."""
        mock_element = MagicMock(spec = Element)
        with (
            patch("kleinanzeigen_bot.login_flow.current_page_url", return_value = "https://kleinanzeigen.de/u/login/password"),
            patch.object(test_bot, "timeout", return_value = 5.0),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock) as mock_probe,
            patch.object(test_bot, "extract_visible_text", new_callable = AsyncMock, return_value = "   \n  "),
        ):
            mock_probe.side_effect = [mock_element, None, None, None]

            result = await login_flow._classify_post_submit_state(test_bot)

        assert "STILL_ON_PASSWORD_PAGE" in result
        assert "auth0_error" not in result

    # ------------------------------------------------------------------ #
    #  Behavior tests replacing low-value direct helper tests
    # ------------------------------------------------------------------ #

    @pytest.mark.asyncio
    async def test_classify_probe_exception_preserves_facts(self, test_bot:KleinanzeigenBot) -> None:
        """When an error probe raises, password-page fact is preserved."""
        with (
            patch("kleinanzeigen_bot.login_flow.current_page_url", return_value = "https://kleinanzeigen.de/u/login/password"),
            patch.object(test_bot, "timeout", return_value = 5.0),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock) as mock_probe,
        ):
            # First error probe raises; remaining error probes miss.
            # MFA probes all miss.
            mock_probe.side_effect = [RuntimeError("boom"), None, None, None, None, None, None]

            result = await login_flow._classify_post_submit_state(test_bot)

        assert "STILL_ON_PASSWORD_PAGE" in result
        assert "auth0_error" not in result

    @pytest.mark.asyncio
    async def test_classify_role_alert_gated_from_non_password_page(self, test_bot:KleinanzeigenBot) -> None:
        """[role='alert'] on non-password URL must not produce auth0_error."""
        mock_element = MagicMock(spec = Element)
        with (
            patch("kleinanzeigen_bot.login_flow.current_page_url", return_value = "https://kleinanzeigen.de/meine-anzeigen"),
            patch.object(test_bot, "timeout", return_value = 5.0),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock) as mock_probe,
        ):
            # Error selectors are gated to password page only, so even though
            # mock_element is returned for any probe, no auth0_error appears.
            # MFA probes are gated to non-destination URLs — meine-anzeigen is
            # a valid destination, so no MFA probes run either.
            mock_probe.return_value = mock_element

            result = await login_flow._classify_post_submit_state(test_bot)

        # Verify no probes ran at all — both error and MFA probes are gated
        mock_probe.assert_not_awaited()
        assert "auth0_error" not in result
        assert result.startswith("UNKNOWN (url=")

    @pytest.mark.asyncio
    async def test_wait_for_post_auth0_submit_transition_redacts_sensitive_text(
        self, test_bot:KleinanzeigenBot, caplog:pytest.LogCaptureFixture,
    ) -> None:
        """LOG.warning must not include variable classification text; TimeoutError
        must redact auth0_error snippets to avoid exposure via error handlers."""
        with (
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = [TimeoutError()]),
            patch("kleinanzeigen_bot.login_flow.is_logged_in", new_callable = AsyncMock, side_effect = asyncio.TimeoutError),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch(
                "kleinanzeigen_bot.login_flow._classify_post_submit_state",
                new_callable = AsyncMock,
                return_value = "STILL_ON_PASSWORD_PAGE + auth0_error='secret password-ish text'",
            ),
            patch("kleinanzeigen_bot.login_flow._safe_current_page_url", return_value = "https://kleinanzeigen.de/u/login/password"),
            pytest.raises(TimeoutError) as exc_info,
        ):
            await wait_for_post_auth0_submit_transition(test_bot, username = test_bot.config.login.username)

        # Exception must not contain the raw secret
        assert "secret password" not in str(exc_info.value)
        assert "auth0_error=<redacted>" in str(exc_info.value)
        assert "Auth0 post-submit verification remained inconclusive" in str(exc_info.value)

        # Warning log is a static string — no variable classification text
        assert "secret password" not in caplog.text
        assert "auth0_error=<redacted>" not in caplog.text
        assert "Auth0 post-submit verification remained inconclusive" in caplog.text

    @pytest.mark.asyncio
    async def test_wait_for_post_auth0_submit_transition_redacts_url(
        self, test_bot:KleinanzeigenBot, caplog:pytest.LogCaptureFixture,
    ) -> None:
        """URL query/fragment/userinfo must be redacted from TimeoutError and logs."""
        raw_url = "https://user:secret@login.kleinanzeigen.de/u/login/password?state=abc&code=secret#frag"
        with (
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = [TimeoutError()]),
            patch("kleinanzeigen_bot.login_flow.is_logged_in", new_callable = AsyncMock, side_effect = asyncio.TimeoutError),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch(
                "kleinanzeigen_bot.login_flow._classify_post_submit_state",
                new_callable = AsyncMock,
                return_value = "STILL_ON_PASSWORD_PAGE",
            ),
            patch("kleinanzeigen_bot.login_flow._safe_current_page_url", return_value = raw_url),
            pytest.raises(TimeoutError) as exc_info,
        ):
            await wait_for_post_auth0_submit_transition(test_bot, username = test_bot.config.login.username)

        exc_text = str(exc_info.value)

        # Sensitive data must not appear in the exception
        assert "state=" not in exc_text
        assert "code=" not in exc_text
        assert "#frag" not in exc_text
        assert "user:" not in exc_text

        # Sanitised diagnostic path must remain
        assert "login.kleinanzeigen.de/u/login/password" in exc_text

        # Same for log output
        log_text = caplog.text
        assert "state=" not in log_text
        assert "code=" not in log_text
        assert "#frag" not in log_text
        assert "user:" not in log_text
        assert "login.kleinanzeigen.de/u/login/password" in log_text

    @pytest.mark.asyncio
    async def test_wait_for_post_auth0_submit_transition_url_failure(
        self, test_bot:KleinanzeigenBot,
    ) -> None:
        """TimeoutError prefix preserved when URL retrieval fails."""
        with (
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = [TimeoutError()]),
            patch("kleinanzeigen_bot.login_flow.is_logged_in", new_callable = AsyncMock, side_effect = asyncio.TimeoutError),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch(
                "kleinanzeigen_bot.login_flow._classify_post_submit_state",
                new_callable = AsyncMock,
                return_value = "STILL_ON_PASSWORD_PAGE",
            ),
            patch("kleinanzeigen_bot.login_flow._safe_current_page_url", return_value = "unknown"),
            pytest.raises(TimeoutError, match = "Auth0 post-submit verification remained inconclusive"),
        ):
            await wait_for_post_auth0_submit_transition(test_bot, username = test_bot.config.login.username)
