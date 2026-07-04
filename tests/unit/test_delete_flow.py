# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Tests for ad deletion functionality."""

import copy
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from kleinanzeigen_bot import delete_flow
from kleinanzeigen_bot.app import KleinanzeigenBot
from kleinanzeigen_bot.delete_flow import DeleteResult
from kleinanzeigen_bot.model.ad_model import Ad


def remove_fields(config:dict[str, Any], *fields:str) -> dict[str, Any]:
    """Create a new ad configuration with specified fields removed."""
    result = copy.deepcopy(config)
    for field in fields:
        if "." in field:
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


class TestKleinanzeigenBotAdDeletion:
    """Tests for ad deletion functionality."""

    @pytest.mark.asyncio
    async def test_delete_ad_by_title_match_succeeds(
        self,
        test_bot:KleinanzeigenBot,
        minimal_ad_config:dict[str, Any],
    ) -> None:
        """When title matches a published ad and server returns 200, should return True and clear id."""
        ad_cfg = Ad.model_validate(minimal_ad_config | {"title": "Test Title", "id": None})
        published_ads = [{"title": "Test Title", "id": 67890}, {"title": "Other Title", "id": 11111}]

        with (
            patch.object(test_bot, "web_open", new_callable = AsyncMock),
            patch.object(test_bot, "web_find", new_callable = AsyncMock) as mock_find,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch.object(test_bot, "web_request", new_callable = AsyncMock,
                         return_value = {"statusCode": 200, "statusMessage": "OK", "content": "{}"}),
        ):
            mock_find.return_value.attrs = {"content": "some-token"}
            result = await delete_flow.delete_ad(
                web = test_bot, root_url = test_bot.root_url,
                ad_cfg = ad_cfg,
                published_ads_list = published_ads,
                delete_old_ads_by_title = True,
            )

        assert isinstance(result, DeleteResult)
        assert result == DeleteResult(deleted = True, attempted = True)
        assert ad_cfg.id is None

    @pytest.mark.asyncio
    async def test_delete_ad_by_id_succeeds(self, test_bot:KleinanzeigenBot, minimal_ad_config:dict[str, Any]) -> None:
        """When ad has an ID and server returns 200, should return True and clear id."""
        ad_cfg = Ad.model_validate(minimal_ad_config | {"id": 12345})
        published_ads = [{"title": "Different Title", "id": 12345}, {"title": "Other Title", "id": 11111}]

        with (
            patch.object(test_bot, "web_open", new_callable = AsyncMock),
            patch.object(test_bot, "web_find", new_callable = AsyncMock) as mock_find,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch.object(test_bot, "web_request", new_callable = AsyncMock,
                         return_value = {"statusCode": 200, "statusMessage": "OK", "content": "{}"}),
        ):
            mock_find.return_value.attrs = {"content": "some-token"}
            result = await delete_flow.delete_ad(
                web = test_bot, root_url = test_bot.root_url,
                ad_cfg = ad_cfg,
                published_ads_list = published_ads,
                delete_old_ads_by_title = False,
            )

        assert isinstance(result, DeleteResult)
        assert result == DeleteResult(deleted = True, attempted = True)
        assert ad_cfg.id is None

    @pytest.mark.asyncio
    async def test_delete_ad_returns_false_when_no_match(
        self,
        test_bot:KleinanzeigenBot,
        minimal_ad_config:dict[str, Any],
    ) -> None:
        """When no published ads match, should return False without opening any pages."""
        ad_cfg = Ad.model_validate(minimal_ad_config | {"title": "No Match Title", "id": None})
        published_ads = [{"title": "Different Title", "id": 12345}]

        with (
            patch.object(test_bot, "web_open", new_callable = AsyncMock) as mock_web_open,
            patch.object(test_bot, "web_find", new_callable = AsyncMock) as mock_web_find,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as mock_web_sleep,
            patch.object(test_bot, "web_request", new_callable = AsyncMock) as mock_request,
        ):
            result = await delete_flow.delete_ad(
                web = test_bot, root_url = test_bot.root_url,
                ad_cfg = ad_cfg,
                published_ads_list = published_ads,
                delete_old_ads_by_title = True,
            )

        assert isinstance(result, DeleteResult)
        assert result == DeleteResult(deleted = False, attempted = False)
        assert ad_cfg.id is None  # Preserved — no deletion attempted
        mock_web_open.assert_not_called()
        mock_web_find.assert_not_called()
        mock_web_sleep.assert_not_called()
        mock_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_ad_returns_false_on_404_clears_id(
        self,
        test_bot:KleinanzeigenBot,
        minimal_ad_config:dict[str, Any],
    ) -> None:
        """When delete is attempted but server returns 404, should return False but still clear the id."""
        ad_cfg = Ad.model_validate(minimal_ad_config | {"id": 12345})
        published_ads:list[dict[str, Any]] = []

        with (
            patch.object(test_bot, "web_open", new_callable = AsyncMock),
            patch.object(test_bot, "web_find", new_callable = AsyncMock) as mock_find,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch.object(test_bot, "web_request", new_callable = AsyncMock, return_value = {"statusCode": 404, "statusMessage": "Not Found", "content": "{}"}),
        ):
            mock_find.return_value.attrs = {"content": "some-token"}
            result = await delete_flow.delete_ad(
                web = test_bot, root_url = test_bot.root_url,
                ad_cfg = ad_cfg,
                published_ads_list = published_ads,
                delete_old_ads_by_title = False,
            )

        assert isinstance(result, DeleteResult)
        assert result == DeleteResult(deleted = False, attempted = True)
        assert ad_cfg.id is None  # Cleared because delete was attempted

    @pytest.mark.asyncio
    async def test_delete_ad_skips_invalid_published_ad_id(self, test_bot:KleinanzeigenBot, minimal_ad_config:dict[str, Any]) -> None:
        """When a title-matched published ad has an invalid id (None), it should be skipped."""
        ad_cfg = Ad.model_validate(minimal_ad_config | {"title": "Test Title", "id": None})
        published_ads:list[dict[str, Any]] = [
            {"title": "Test Title", "id": None},  # Invalid — should be skipped
            {"title": "Test Title", "id": "not-a-number"},  # Invalid — should be skipped
        ]

        with (
            patch.object(test_bot, "web_open", new_callable = AsyncMock) as mock_web_open,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch.object(test_bot, "web_request", new_callable = AsyncMock),
        ):
            result = await delete_flow.delete_ad(
                web = test_bot, root_url = test_bot.root_url,
                ad_cfg = ad_cfg,
                published_ads_list = published_ads,
                delete_old_ads_by_title = True,
            )

        assert isinstance(result, DeleteResult)
        assert result == DeleteResult(deleted = False, attempted = False)
        mock_web_open.assert_not_called()  # No valid IDs → no page open

    @pytest.mark.asyncio
    async def test_delete_ad_with_zero_id(self, test_bot:KleinanzeigenBot, minimal_ad_config:dict[str, Any]) -> None:
        """When ad_cfg.id is 0 (falsy but valid), should still enter the ID-based deletion path."""
        ad_cfg = Ad.model_validate(minimal_ad_config | {"id": 0})
        published_ads:list[dict[str, Any]] = []

        with (
            patch.object(test_bot, "web_open", new_callable = AsyncMock),
            patch.object(test_bot, "web_find", new_callable = AsyncMock) as mock_find,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch.object(test_bot, "web_request", new_callable = AsyncMock,
                         return_value = {"statusCode": 200, "statusMessage": "OK", "content": "{}"}) as mock_request,
        ):
            mock_find.return_value.attrs = {"content": "some-token"}
            result = await delete_flow.delete_ad(
                web = test_bot, root_url = test_bot.root_url,
                ad_cfg = ad_cfg,
                published_ads_list = published_ads,
                delete_old_ads_by_title = False,
            )

        assert isinstance(result, DeleteResult)
        assert result == DeleteResult(deleted = True, attempted = True)
        assert ad_cfg.id is None
        mock_request.assert_called_once()
        assert "ids=0" in mock_request.call_args[1]["url"]

    @pytest.mark.asyncio
    async def test_delete_ad_multiple_title_matches_fails_closed(
        self, test_bot:KleinanzeigenBot, minimal_ad_config:dict[str, Any],
    ) -> None:
        """When multiple published ads match by title, should skip deletion as ambiguous."""
        ad_cfg = Ad.model_validate(minimal_ad_config | {"title": "Test Title", "id": None})
        published_ads = [
            {"title": "Test Title", "id": 100},
            {"title": "Test Title", "id": 200},
            {"title": "Test Title", "id": 300},
        ]

        with (
            patch.object(test_bot, "web_open", new_callable = AsyncMock) as mock_web_open,
            patch.object(test_bot, "web_find", new_callable = AsyncMock) as mock_web_find,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as mock_web_sleep,
            patch.object(test_bot, "web_request", new_callable = AsyncMock) as mock_request,
        ):
            result = await delete_flow.delete_ad(
                web = test_bot, root_url = test_bot.root_url,
                ad_cfg = ad_cfg,
                published_ads_list = published_ads,
                delete_old_ads_by_title = True,
            )

        assert isinstance(result, DeleteResult)
        assert result == DeleteResult(deleted = False, attempted = False)
        assert ad_cfg.id is None
        mock_web_open.assert_not_called()
        mock_web_find.assert_not_called()
        mock_web_sleep.assert_not_called()
        mock_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_ad_with_id_does_not_expand_to_same_title_matches(
        self, test_bot:KleinanzeigenBot, minimal_ad_config:dict[str, Any],
    ) -> None:
        """When an ID is present, title matching must not expand deletion to other ads."""
        ad_cfg = Ad.model_validate(minimal_ad_config | {"title": "Test Title", "id": 100})
        published_ads = [
            {"title": "Test Title", "id": 100},
            {"title": "Test Title", "id": 200},
            {"title": "Test Title", "id": 300},
        ]
        ok_response = {"statusCode": 200, "statusMessage": "OK", "content": "{}"}

        with (
            patch.object(test_bot, "web_open", new_callable = AsyncMock),
            patch.object(test_bot, "web_find", new_callable = AsyncMock) as mock_find,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch.object(test_bot, "web_request", new_callable = AsyncMock, return_value = ok_response) as mock_request,
        ):
            mock_find.return_value.attrs = {"content": "some-token"}
            result = await delete_flow.delete_ad(
                web = test_bot, root_url = test_bot.root_url,
                ad_cfg = ad_cfg,
                published_ads_list = published_ads,
                delete_old_ads_by_title = True,
            )

        assert isinstance(result, DeleteResult)
        assert result == DeleteResult(deleted = True, attempted = True)
        assert ad_cfg.id is None
        mock_request.assert_called_once()
        assert mock_request.call_args.kwargs["url"] == f"{test_bot.root_url}/m-anzeigen-loeschen.json?ids=100"

    @pytest.mark.asyncio
    async def test_delete_ad_with_id_ignores_published_ads_list_for_exact_id_delete(
        self, test_bot:KleinanzeigenBot, minimal_ad_config:dict[str, Any],
    ) -> None:
        """When an ID is present, deletion should target that exact ID even without a published-list match."""
        ad_cfg = Ad.model_validate(minimal_ad_config | {"title": "Test Title", "id": 100})
        published_ads = [{"title": "Other Title", "id": 200}]
        ok_response = {"statusCode": 200, "statusMessage": "OK", "content": "{}"}

        with (
            patch.object(test_bot, "web_open", new_callable = AsyncMock),
            patch.object(test_bot, "web_find", new_callable = AsyncMock) as mock_find,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch.object(test_bot, "web_request", new_callable = AsyncMock, return_value = ok_response) as mock_request,
        ):
            mock_find.return_value.attrs = {"content": "some-token"}
            result = await delete_flow.delete_ad(
                web = test_bot, root_url = test_bot.root_url,
                ad_cfg = ad_cfg,
                published_ads_list = published_ads,
                delete_old_ads_by_title = True,
            )

        assert isinstance(result, DeleteResult)
        assert result == DeleteResult(deleted = True, attempted = True)
        assert ad_cfg.id is None
        mock_request.assert_called_once()
        assert mock_request.call_args.kwargs["url"] == f"{test_bot.root_url}/m-anzeigen-loeschen.json?ids=100"

    @pytest.mark.asyncio
    async def test_delete_ad_exception_preserves_id(self, test_bot:KleinanzeigenBot, minimal_ad_config:dict[str, Any]) -> None:
        """When web_request raises an exception mid-loop, ad_cfg.id should be preserved and web_sleep not called."""
        ad_cfg = Ad.model_validate(minimal_ad_config | {"id": 12345})
        published_ads:list[dict[str, Any]] = []

        with (
            patch.object(test_bot, "web_open", new_callable = AsyncMock),
            patch.object(test_bot, "web_find", new_callable = AsyncMock) as mock_find,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as mock_web_sleep,
            patch.object(test_bot, "web_request", new_callable = AsyncMock, side_effect = TimeoutError("request timed out")),
        ):
            mock_find.return_value.attrs = {"content": "some-token"}
            with pytest.raises(TimeoutError, match = "request timed out"):
                await delete_flow.delete_ad(
                    web = test_bot, root_url = test_bot.root_url,
                    ad_cfg = ad_cfg,
                    published_ads_list = published_ads,
                    delete_old_ads_by_title = False,
                )

        assert ad_cfg.id == 12345  # Preserved — exception prevented clearing
        mock_web_sleep.assert_not_called()


class TestDeleteAdsAfterDeletePolicy:
    """Tests for delete_ads orchestration with after_delete policy integration."""

    @staticmethod
    def _make_ad(minimal_ad_config:dict[str, Any], tmp_path:Path) -> tuple[str, Ad, dict[str, Any]]:
        ad_cfg = Ad.model_validate(minimal_ad_config | {
            "id": 12345, "active": True,
            "created_on": "2024-06-01T12:00:00", "updated_on": "2024-06-10T08:30:00",
            "content_hash": "abc123", "repost_count": 3, "price_reduction_count": 1,
        })
        return str(tmp_path / "ad.yaml"), ad_cfg, ad_cfg.model_dump()

    @pytest.mark.asyncio
    async def test_cleanup_on_404_detection(
        self, test_bot:KleinanzeigenBot, minimal_ad_config:dict[str, Any], tmp_path:Path,
    ) -> None:
        """Cleanup runs when delete_ad returns False but cleared the id (404 path)."""
        test_bot.config.deleting.after_delete = "RESET"
        ad_file, ad_cfg, ad_cfg_orig = self._make_ad(minimal_ad_config, tmp_path)

        async def fake_delete(
            _web:Any, _root_url:str, ad:Ad, _published:list[dict[str, Any]], **__:Any
        ) -> DeleteResult:
            ad.id = None  # Phase B ran and cleared the id
            return DeleteResult(deleted = False, attempted = True)  # all responses were 404 but deletion was attempted

        with (
            patch("kleinanzeigen_bot.published_ads.fetch_published_ads", new_callable = AsyncMock, return_value = []),
            patch("kleinanzeigen_bot.delete_flow.delete_ad", new_callable = AsyncMock, side_effect = fake_delete),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.utils.dicts.save_dict") as mock_save,
        ):
            await delete_flow.delete_ads(
                web = test_bot, root_url = test_bot.root_url,
                after_delete = test_bot.config.deleting.after_delete,
                delete_old_ads_by_title = test_bot.config.publishing.delete_old_ads_by_title,
                ad_cfgs = [(ad_file, ad_cfg, ad_cfg_orig)],
            )

        assert ad_cfg.repost_count == 0
        assert "id" not in ad_cfg_orig
        mock_save.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_cleanup_when_delete_not_attempted(
        self, test_bot:KleinanzeigenBot, minimal_ad_config:dict[str, Any], tmp_path:Path,
    ) -> None:
        """No cleanup when delete_ad returns False with id preserved (no match)."""
        test_bot.config.deleting.after_delete = "RESET"
        ad_file, ad_cfg, ad_cfg_orig = self._make_ad(minimal_ad_config, tmp_path)

        with (
            patch("kleinanzeigen_bot.published_ads.fetch_published_ads", new_callable = AsyncMock, return_value = []),
            patch("kleinanzeigen_bot.delete_flow.delete_ad", new_callable = AsyncMock, return_value = DeleteResult(deleted = False, attempted = False)),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.utils.dicts.save_dict") as mock_save,
        ):
            await delete_flow.delete_ads(
                web = test_bot, root_url = test_bot.root_url,
                after_delete = test_bot.config.deleting.after_delete,
                delete_old_ads_by_title = test_bot.config.publishing.delete_old_ads_by_title,
                ad_cfgs = [(ad_file, ad_cfg, ad_cfg_orig)],
            )

        mock_save.assert_not_called()
        assert ad_cfg.id == 12345

    @pytest.mark.asyncio
    async def test_delete_ads_counts_deletions(
        self, test_bot:KleinanzeigenBot, minimal_ad_config:dict[str, Any], tmp_path:Path,
    ) -> None:
        """Orchestrator increments deleted_count when delete_ad returns True."""
        test_bot.config.deleting.after_delete = "NONE"
        ad1 = self._make_ad(minimal_ad_config, tmp_path)
        # Create second ad with different title/id
        ad_cfg2 = Ad.model_validate(minimal_ad_config | {
            "id": 67890, "title": "Second Ad Here", "active": True,
            "created_on": "2024-06-01T12:00:00", "updated_on": "2024-06-10T08:30:00",
            "content_hash": "def456",
        })
        ad2 = (str(tmp_path / "ad2.yaml"), ad_cfg2, ad_cfg2.model_dump())

        with (
            patch("kleinanzeigen_bot.published_ads.fetch_published_ads", new_callable = AsyncMock, return_value = []),
            patch("kleinanzeigen_bot.delete_flow.delete_ad",
                  new_callable = AsyncMock,
                  return_value = DeleteResult(deleted = True, attempted = True)) as mock_delete_ad,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.utils.dicts.save_dict") as mock_save,
        ):
            await delete_flow.delete_ads(
                web = test_bot, root_url = test_bot.root_url,
                after_delete = test_bot.config.deleting.after_delete,
                delete_old_ads_by_title = test_bot.config.publishing.delete_old_ads_by_title,
                ad_cfgs = [ad1, ad2],
            )

        # save_dict not called because after_delete is NONE
        mock_save.assert_not_called()
        # delete_ad called twice (once per ad)
        assert mock_delete_ad.call_count == 2

    @pytest.mark.asyncio
    async def test_delete_ads_fetches_published_ads_strictly_for_id_less_title_matching(
        self, test_bot:KleinanzeigenBot, minimal_ad_config:dict[str, Any], tmp_path:Path,
    ) -> None:
        """Title matching needs a complete published-ad list to detect ambiguous matches safely."""
        test_bot.config.deleting.after_delete = "NONE"
        ad_file, ad_cfg, ad_cfg_orig = self._make_ad(minimal_ad_config, tmp_path)
        ad_cfg.id = None
        ad_cfg_orig.pop("id", None)

        with (
            patch("kleinanzeigen_bot.published_ads.fetch_published_ads", new_callable = AsyncMock, return_value = []) as mock_fetch,
            patch("kleinanzeigen_bot.delete_flow.delete_ad", new_callable = AsyncMock, return_value = DeleteResult(deleted = False, attempted = False)),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
        ):
            await delete_flow.delete_ads(
                web = test_bot, root_url = test_bot.root_url,
                after_delete = test_bot.config.deleting.after_delete,
                delete_old_ads_by_title = True,
                ad_cfgs = [(ad_file, ad_cfg, ad_cfg_orig)],
            )

        mock_fetch.assert_awaited_once_with(test_bot, test_bot.root_url, strict = True)

    @pytest.mark.asyncio
    async def test_delete_ads_keeps_published_ads_fetch_non_strict_for_id_only_deletes(
        self, test_bot:KleinanzeigenBot, minimal_ad_config:dict[str, Any], tmp_path:Path,
    ) -> None:
        """ID-only deletes should not fail because unrelated published-ad pagination is incomplete."""
        test_bot.config.deleting.after_delete = "NONE"
        ad_file, ad_cfg, ad_cfg_orig = self._make_ad(minimal_ad_config, tmp_path)

        with (
            patch("kleinanzeigen_bot.published_ads.fetch_published_ads", new_callable = AsyncMock, return_value = []) as mock_fetch,
            patch("kleinanzeigen_bot.delete_flow.delete_ad", new_callable = AsyncMock, return_value = DeleteResult(deleted = False, attempted = False)),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
        ):
            await delete_flow.delete_ads(
                web = test_bot, root_url = test_bot.root_url,
                after_delete = test_bot.config.deleting.after_delete,
                delete_old_ads_by_title = True,
                ad_cfgs = [(ad_file, ad_cfg, ad_cfg_orig)],
            )

        mock_fetch.assert_awaited_once_with(test_bot, test_bot.root_url, strict = False)

    @pytest.mark.asyncio
    async def test_cleanup_on_title_match_all_404_with_id_none(
        self, test_bot:KleinanzeigenBot, minimal_ad_config:dict[str, Any], tmp_path:Path,
    ) -> None:
        """Regression test for #1103: after_delete policy applied when title-mode delete
        returns all-404 and ad_cfg.id was already None."""
        test_bot.config.deleting.after_delete = "RESET"
        minimal_ad_config["id"] = None
        ad_file, ad_cfg, ad_cfg_orig = self._make_ad(minimal_ad_config, tmp_path)
        ad_cfg.id = None  # Simulate id was never assigned

        mock_delete = AsyncMock(return_value = DeleteResult(deleted = False, attempted = True))

        with (
            patch("kleinanzeigen_bot.published_ads.fetch_published_ads", new_callable = AsyncMock, return_value = []),
            patch("kleinanzeigen_bot.delete_flow.delete_ad", new = mock_delete),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.utils.dicts.save_dict") as mock_save,
        ):
            await delete_flow.delete_ads(
                web = test_bot, root_url = test_bot.root_url,
                after_delete = test_bot.config.deleting.after_delete,
                delete_old_ads_by_title = True,
                ad_cfgs = [(ad_file, ad_cfg, ad_cfg_orig)],
            )

        # Policy must be applied: deletion was attempted
        assert ad_cfg.repost_count == 0
        assert "id" not in ad_cfg_orig
        mock_save.assert_called_once()
