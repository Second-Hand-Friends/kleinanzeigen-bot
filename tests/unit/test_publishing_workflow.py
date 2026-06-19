# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Tests for publishing workflow orchestration."""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nodriver.core.connection import ProtocolException

from kleinanzeigen_bot.app import KleinanzeigenBot
from kleinanzeigen_bot.model.ad_model import Ad, AdUpdateStrategy
from kleinanzeigen_bot.publishing_workflow import SUBMISSION_MAX_RETRIES
from kleinanzeigen_bot.utils.exceptions import CategoryResolutionError, PublishSubmissionUncertainError
from tests.conftest import build_published_ads, build_update_ad


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
