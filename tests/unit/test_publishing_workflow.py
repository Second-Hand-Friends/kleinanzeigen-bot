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
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path, PureWindowsPath
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nodriver.core.connection import ProtocolException

from kleinanzeigen_bot.app import KleinanzeigenBot
from kleinanzeigen_bot.model.ad_model import Ad, AdUpdateStrategy
from kleinanzeigen_bot.model.config_model import (
    AutoPriceReductionConfig,
    DiagnosticsConfig,
)
from kleinanzeigen_bot.publishing_workflow import SUBMISSION_MAX_RETRIES, PostPublishPersistenceError
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

    @pytest.mark.asyncio
    async def test_publish_ads_treats_post_publish_persistence_error_as_fail_closed(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        mock_page:MagicMock,
        caplog:pytest.LogCaptureFixture,
    ) -> None:
        test_bot.page = mock_page
        test_bot.config.publishing.delete_old_ads = "AFTER_PUBLISH"
        test_bot.keep_old_ads = False

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
                side_effect = PostPublishPersistenceError(
                    ad_id = 12345,
                    ad_title = ad_cfg.title,
                    original = RuntimeError("disk full"),
                ),
            ) as publish_ad_mock,
            patch.object(
                test_bot,
                "_capture_publish_error_diagnostics_if_enabled",
                new_callable = AsyncMock,
            ) as capture_mock,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as sleep_mock,
            patch.object(test_bot, "web_await", new_callable = AsyncMock) as web_await_mock,
            patch("kleinanzeigen_bot.delete_flow.delete_ad", new_callable = AsyncMock) as delete_ad_mock,
            caplog.at_level("INFO"),
        ):
            await test_bot.publish_ads([(ad_file, ad_cfg, ad_cfg_orig)])

            assert web_await_mock.await_count == 0
            assert sleep_mock.await_count == 0
            assert publish_ad_mock.await_count == 1
            assert delete_ad_mock.await_count == 0
            capture_mock.assert_awaited_once()

            assert any("DONE: (Re-)published 0 ads (1 failed after retries)" in record.getMessage() for record in caplog.records)
            assert any("Persistence failed for 'Test Title' after ad submission" in record.getMessage() for record in caplog.records)
            assert any("Ad ID: 12345" in record.getMessage() for record in caplog.records)

    @pytest.mark.asyncio
    async def test_update_ads_persistence_failure_is_not_retried_and_still_processes_next_ad(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        ad_one = build_update_ad(base_ad_config, 401, "Persistence Error Update")
        ad_two = build_update_ad(base_ad_config, 402, "Healthy Update")

        async def publish_side_effect(
            _web:Any,
            _ad_file:str,
            ad_cfg:Ad,
            _ad_cfg_orig:dict[str, Any],
            _published_ads:list[dict[str, Any]],
            _mode:AdUpdateStrategy,
            **kwargs:Any,
        ) -> None:
            if ad_cfg.id == 401:
                raise PostPublishPersistenceError(
                    ad_id = 401,
                    ad_title = ad_cfg.title,
                    original = RuntimeError("disk full"),
                )

        with (
            patch(
                "kleinanzeigen_bot.published_ads.fetch_published_ads",
                new_callable = AsyncMock,
                return_value = build_published_ads((401, "active"), (402, "active")),
            ),
            patch(
                "kleinanzeigen_bot.publishing_workflow.publish_ad",
                new_callable = AsyncMock,
                side_effect = publish_side_effect,
            ) as publish_mock,
            patch.object(test_bot, "_capture_publish_error_diagnostics_if_enabled", new_callable = AsyncMock) as capture_mock,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as sleep_mock,
            patch.object(test_bot, "web_await", new_callable = AsyncMock, return_value = True) as web_await_mock,
        ):
            await test_bot.update_ads([ad_one, ad_two])

            assert publish_mock.await_count == 2
            sleep_mock.assert_not_awaited()
            web_await_mock.assert_awaited_once()
            capture_mock.assert_awaited_once()

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
            patch.object(test_bot, "dismiss_consent_banner", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.publishing_form.set_category", new_callable = AsyncMock, side_effect = TimeoutError("image upload timeout")),
            pytest.raises(TimeoutError, match = "image upload timeout"),
        ):
            await test_bot.publish_ad("ad.yaml", ad_cfg, ad_cfg_orig, [], mode)

        web_open_mock.assert_awaited_once_with(expected_url, reload_if_already_open = True)


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
            patch.object(
                test_bot,
                "web_request",
                new_callable = AsyncMock,
                return_value = {"content": json.dumps({"ads": [], "paging": {"pageNum": 1, "last": 1}})},
            ),
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


class TestPublishAdPostSubmitUncertainty:
    """Post-submit uncertainty tests for publish_ad, moved from TestKleinanzeigenBotBasics."""

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
            patch.object(test_bot, "dismiss_consent_banner", new_callable = AsyncMock),
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
            patch.object(test_bot, "web_open", new_callable = AsyncMock),
            patch.object(test_bot, "dismiss_consent_banner", new_callable = AsyncMock),
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


class TestAutoPriceReductionDispatch:
    """Price reduction dispatch tests across REPLACE and MODIFY modes."""

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

    @pytest.mark.asyncio
    async def test_cross_drive_path_fallback_windows(
        self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any]
    ) -> None:
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
        test_bot.config_file_path = str(PureWindowsPath("D:\\project\\config.yaml"))
        ad_file = str(PureWindowsPath("C:\\temp\\test_ad.yaml"))

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

        with (
            # Force PureWindowsPath so that the cross-drive relative_to raises
            # ValueError deterministically, even on non-Windows runners.
            patch("kleinanzeigen_bot.ad_state.Path", PureWindowsPath),
            patch("kleinanzeigen_bot.price_reduction.apply_auto_price_reduction", side_effect = mock_apply_auto_price_reduction),
            patch.object(test_bot, "web_open", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.delete_flow.delete_ad", new_callable = AsyncMock),
        ):
            # Call publish_ad and expect sentinel exception
            try:
                await test_bot.publish_ad(ad_file, ad_cfg, ad_cfg_orig, [], AdUpdateStrategy.REPLACE)
                pytest.fail("Expected _SentinelException to be raised")
            except _SentinelException:
                # SentinelException is raised by mock_apply_auto_price_reduction
                # to abort publish_ad early after recording the path argument.
                pass  # No cleanup needed — the real workflow would continue here.

        # Verify the path argument is the absolute path (fallback behavior)
        assert len(recorded_path) == 1
        assert recorded_path[0] == ad_file, f"Expected absolute path fallback, got: {recorded_path[0]}"
