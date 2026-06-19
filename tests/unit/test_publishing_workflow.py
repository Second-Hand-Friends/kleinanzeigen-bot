# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Tests for publishing workflow orchestration."""

import asyncio
import copy
import fnmatch
import json
import logging
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nodriver.core.connection import ProtocolException

from kleinanzeigen_bot.app import KleinanzeigenBot
from kleinanzeigen_bot.model.ad_model import Ad, AdUpdateStrategy
from kleinanzeigen_bot.model.config_model import DiagnosticsConfig
from kleinanzeigen_bot.publishing_workflow import SUBMISSION_MAX_RETRIES
from kleinanzeigen_bot.utils.exceptions import CategoryResolutionError, PublishSubmissionUncertainError
from tests.conftest import build_published_ads, build_update_ad


@pytest.fixture
def mock_page() -> MagicMock:
    """Provide a mock page object for testing."""
    mock = MagicMock()
    mock.sleep = AsyncMock()
    mock.evaluate = AsyncMock()
    mock.click = AsyncMock()
    mock.type = AsyncMock()
    return mock


class TestKleinanzeigenBotUpdateAdsResilience:
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
        ad_one = build_update_ad(base_ad_config, 101, first_title)
        ad_two = build_update_ad(base_ad_config, 102, "Success Ad")

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
            patch(
                "kleinanzeigen_bot.published_ads.fetch_published_ads",
                new_callable = AsyncMock,
                return_value = build_published_ads((101, "active"), (102, "active")),
            ),
            patch("kleinanzeigen_bot.publishing_workflow.publish_ad", new_callable = AsyncMock, side_effect = publish_side_effect) as publish_mock,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as sleep_mock,
            patch.object(test_bot, "web_await", new_callable = AsyncMock, return_value = True),
        ):
            await test_bot.update_ads([ad_one, ad_two])

        assert publish_mock.await_count == SUBMISSION_MAX_RETRIES + 1
        call_ids = [call.args[2].id for call in publish_mock.await_args_list]
        assert call_ids.count(101) == SUBMISSION_MAX_RETRIES
        assert call_ids.count(102) == 1
        assert all(call.args[5] == AdUpdateStrategy.MODIFY for call in publish_mock.await_args_list)
        assert sleep_mock.await_count == SUBMISSION_MAX_RETRIES - 1

    @pytest.mark.asyncio
    async def test_update_ads_publish_submission_uncertain_is_not_retried(self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any]) -> None:
        ad_one = build_update_ad(base_ad_config, 301, "Uncertain Update")
        ad_two = build_update_ad(base_ad_config, 302, "Second Update")

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
            patch(
                "kleinanzeigen_bot.published_ads.fetch_published_ads",
                new_callable = AsyncMock,
                return_value = build_published_ads((301, "active"), (302, "active")),
            ),
            patch("kleinanzeigen_bot.publishing_workflow.publish_ad", new_callable = AsyncMock, side_effect = publish_side_effect) as publish_mock,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as sleep_mock,
            patch.object(test_bot, "web_await", new_callable = AsyncMock, return_value = True),
        ):
            await test_bot.update_ads([ad_one, ad_two])

        assert publish_mock.await_count == 2
        sleep_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_update_ads_category_resolution_error_is_not_retried(self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any]) -> None:
        ad_one = build_update_ad(base_ad_config, 303, "Category Error Update")
        ad_two = build_update_ad(base_ad_config, 304, "Second Update")

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
            patch(
                "kleinanzeigen_bot.published_ads.fetch_published_ads",
                new_callable = AsyncMock,
                return_value = build_published_ads((303, "active"), (304, "active")),
            ),
            patch("kleinanzeigen_bot.publishing_workflow.publish_ad", new_callable = AsyncMock, side_effect = publish_side_effect) as publish_mock,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as sleep_mock,
            patch.object(test_bot, "web_await", new_callable = AsyncMock, return_value = True),
        ):
            await test_bot.update_ads([ad_one, ad_two])

        assert [call.args[2].id for call in publish_mock.await_args_list] == [303, 304]
        sleep_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_update_ads_cancelled_error_propagates_immediately(self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any]) -> None:
        ad_one = build_update_ad(base_ad_config, 401, "Cancelled Ad")
        ad_two = build_update_ad(base_ad_config, 402, "Should Not Run")

        with (
            patch(
                "kleinanzeigen_bot.published_ads.fetch_published_ads",
                new_callable = AsyncMock,
                return_value = build_published_ads((401, "active"), (402, "active")),
            ),
            patch("kleinanzeigen_bot.publishing_workflow.publish_ad", new_callable = AsyncMock, side_effect = asyncio.CancelledError()) as publish_mock,
            patch.object(test_bot, "web_await", new_callable = AsyncMock, return_value = True),
            pytest.raises(asyncio.CancelledError),
        ):
            await test_bot.update_ads([ad_one, ad_two])

        assert publish_mock.await_count == 1

    @pytest.mark.asyncio
    async def test_update_ads_publishing_result_timeout_is_non_fatal(self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any]) -> None:
        ad_one = build_update_ad(base_ad_config, 501, "Result Timeout")

        with (
            patch(
                "kleinanzeigen_bot.published_ads.fetch_published_ads",
                new_callable = AsyncMock,
                return_value = build_published_ads((501, "active")),
            ),
            patch("kleinanzeigen_bot.publishing_workflow.publish_ad", new_callable = AsyncMock) as publish_mock,
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = TimeoutError("result timeout")),
        ):
            await test_bot.update_ads([ad_one])

        publish_mock.assert_awaited_once()


class TestKleinanzeigenBotPublishAdsBasics:
    """Publish-ads orchestration tests moved from TestKleinanzeigenBotBasics."""

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

        payload:dict[str, Any] = {"ads": [], "paging": {"pageNum": 1, "last": 1}}
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


class TestDisplayCounterProgression:
    """Regression tests for issue #977: progress counter must increment for every ad, including skipped ones."""

    @pytest.mark.asyncio
    async def test_publish_ads_counter_progression_with_paused_ads(
        self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any], caplog:pytest.LogCaptureFixture
    ) -> None:
        """Display counter must advance for paused ads, and only non-paused ads are published."""
        ad_cfgs = [
            build_update_ad(base_ad_config, 101, "Paused Ad 1"),
            build_update_ad(base_ad_config, 102, "Active Ad 102"),
            build_update_ad(base_ad_config, 103, "Paused Ad 2"),
        ]
        published_ads = build_published_ads((101, "paused"), (102, "active"), (103, "paused"))

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
            build_update_ad(base_ad_config, 201, "Paused Ad 1"),
            build_update_ad(base_ad_config, 202, "Active Ad 202"),
            build_update_ad(base_ad_config, 203, "Paused Ad 2"),
        ]
        published_ads = build_published_ads((201, "paused"), (202, "active"), (203, "paused"))

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
            build_update_ad(base_ad_config, 301, "Not Found Ad"),
            build_update_ad(base_ad_config, 302, "Active Ad 302"),
        ]
        published_ads = build_published_ads((302, "active"))

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
