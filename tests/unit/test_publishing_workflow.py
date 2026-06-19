# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Tests for publishing workflow orchestration."""

import asyncio
import copy
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nodriver.core.connection import ProtocolException

from kleinanzeigen_bot.app import KleinanzeigenBot
from kleinanzeigen_bot.model.ad_model import Ad, AdUpdateStrategy
from kleinanzeigen_bot.publishing_workflow import SUBMISSION_MAX_RETRIES
from kleinanzeigen_bot.utils.exceptions import CategoryResolutionError, PublishSubmissionUncertainError


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
