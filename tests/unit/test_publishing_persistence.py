# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Tests for publishing persistence functionality."""

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kleinanzeigen_bot import (
    local_path_renaming as _local_path_renaming,
)
from kleinanzeigen_bot import publishing_persistence
from kleinanzeigen_bot.app import KleinanzeigenBot
from kleinanzeigen_bot.local_path_renaming import ImageRenameResult, LocalPathRenameResult, RenameStatus
from kleinanzeigen_bot.model.ad_model import Ad, AdUpdateStrategy
from kleinanzeigen_bot.model.config_model import Config
from kleinanzeigen_bot.publishing_workflow import PostPublishPersistenceError


def _make_rename_result(*, renamed:bool = False, blocked:bool = False, id_mismatch:bool = True) -> LocalPathRenameResult:
    """Build a LocalPathRenameResult for _log_local_path_rename_result tests."""
    return LocalPathRenameResult(
        ad_file = Path("test_123.yaml"),
        file_status = (
            RenameStatus.TARGET_EXISTS if blocked else
            RenameStatus.RENAMED if renamed else
            RenameStatus.SAME
        ),
        folder_status = (
            RenameStatus.TARGET_EXISTS if blocked else
            RenameStatus.RENAMED if renamed else
            RenameStatus.SAME
        ),
        renamed_image_count = 0,
        blocked_image_count = 0,
        path_old_id = 1,
        yaml_old_id = 3 if id_mismatch else 1,
    )


def _make_image_rename_result() -> ImageRenameResult:
    """Build an ImageRenameResult with no renamed paths."""
    return ImageRenameResult(
        renamed_count = 0,
        blocked_count = 0,
        updated_images = None,
        renamed_paths = [],
    )


def _make_image_rename_result_with_updates() -> ImageRenameResult:
    """Build an ImageRenameResult with updated_images set."""
    return ImageRenameResult(
        renamed_count = 0,
        blocked_count = 0,
        updated_images = ["new_image.jpg"],
        renamed_paths = [],
    )


def _make_image_rename_result_with_rollback() -> ImageRenameResult:
    """Build an ImageRenameResult with renamed_paths for rollback testing."""
    return ImageRenameResult(
        renamed_count = 1,
        blocked_count = 0,
        updated_images = None,
        renamed_paths = [(Path("old.jpg"), Path("new.jpg"))],
    )


def _make_config() -> Config:
    """Minimal config with TEMPLATE_MATCH enabled for local path renaming."""
    return Config.model_validate({
        "active": True,
        "download": {
            "ad_file_name_template": "{id}_{title}",
            "folder_name_template": "{id}",
        },
        "publishing": {
            "local_path_renaming": {
                "mode": "TEMPLATE_MATCH",
            },
        },
    })


def _make_min_ad() -> Ad:
    return Ad.model_validate({
        "title": "Test Ad Title",
        "description": "Test description for the ad listing.",
        "type": "OFFER",
        "price_type": "FIXED",
        "price": 10,
        "sell_directly": False,
        "shipping_type": "NOT_APPLICABLE",
        "category": "160",
        "special_attributes": {},
        "images": [],
        "active": True,
        "republication_interval": 7,
        "contact": {
            "name": "Test User",
            "zipcode": "12345",
            "location": "Test City",
        },
    })


class TestLogLocalPathRenameResult:
    """Tests for the _log_local_path_rename_result helper."""

    @pytest.mark.parametrize(("renamed", "blocked", "id_mismatch", "mode", "expect_info", "expect_warning"), [
        pytest.param(  # noqa: ERA001
            True, False, True, "TEMPLATE_MATCH", True, False,
            id = "renamed-logs-info",
        ),
        pytest.param(  # noqa: ERA001
            False, True, True, "TEMPLATE_MATCH", False, True,
            id = "blocked-logs-warning",
        ),
        pytest.param(  # noqa: ERA001
            False, False, True, "OFF", False, False,
            id = "neither-off-mode-skips",
        ),
        pytest.param(  # noqa: ERA001
            False, False, True, "TEMPLATE_MATCH", True, False,
            id = "neither-template-match-logs-info",
        ),
    ])
    def test_covers_all_branches(  # noqa: PLR0913
        self, renamed:bool, blocked:bool, id_mismatch:bool, mode:str, expect_info:bool, expect_warning:bool,
    ) -> None:
        result = _make_rename_result(renamed = renamed, blocked = blocked, id_mismatch = id_mismatch)
        with (
            patch.object(publishing_persistence.LOG, "info") as mock_info,
            patch.object(publishing_persistence.LOG, "warning") as mock_warning,
        ):
            publishing_persistence._log_local_path_rename_result(result, ad_id = 2, local_path_renaming_mode = mode)

        assert mock_info.called == expect_info
        assert mock_warning.called == expect_warning


class TestPersistPublishedAdPriceReductionCount:
    """Tests for price_reduction_count persistence in persist_published_ad."""

    @staticmethod
    def _make_ad_cfg_orig() -> dict[str, Any]:
        """Minimal ad_cfg_orig with required fields for AdPartial validation."""
        return {
            "title": "Test Ad Title",
            "description": "Test description for the ad listing.",
            "type": "OFFER",
            "category": "160",
        }

    def test_persists_when_positive(self) -> None:
        """When price_reduction_count > 0, it is written to ad_cfg_orig."""
        ad = _make_min_ad()
        ad.price_reduction_count = 3
        ad_cfg_orig = self._make_ad_cfg_orig()
        cfg = _make_config()

        with (
            patch("kleinanzeigen_bot.local_path_renaming.rename_referenced_local_image_files_after_id_change",
                  return_value = _make_image_rename_result()),
            patch("kleinanzeigen_bot.local_path_renaming.rename_local_ad_file_and_folder_after_id_change",
                  return_value = _local_path_renaming.LocalPathRenameResult(
                      ad_file = Path("test.yaml"),
                      file_status = RenameStatus.SAME,
                      folder_status = RenameStatus.SAME,
                  )),
            patch("kleinanzeigen_bot.utils.dicts.save_dict"),
            patch("kleinanzeigen_bot.utils.misc.now"),
        ):
            publishing_persistence.persist_published_ad(
                ad_file = "test.yaml",
                ad_cfg = ad,
                ad_cfg_orig = ad_cfg_orig,
                old_ad_id = None,
                ad_id = 12345,
                mode = AdUpdateStrategy.REPLACE,
                config = cfg,
            )

        assert ad_cfg_orig["price_reduction_count"] == 3

    def test_skips_when_zero(self) -> None:
        """When price_reduction_count is 0 (default), it is NOT written."""
        ad = _make_min_ad()
        ad.price_reduction_count = 0
        ad_cfg_orig = self._make_ad_cfg_orig()
        cfg = _make_config()

        with (
            patch("kleinanzeigen_bot.local_path_renaming.rename_referenced_local_image_files_after_id_change",
                  return_value = _make_image_rename_result()),
            patch("kleinanzeigen_bot.local_path_renaming.rename_local_ad_file_and_folder_after_id_change",
                  return_value = _local_path_renaming.LocalPathRenameResult(
                      ad_file = Path("test.yaml"),
                      file_status = RenameStatus.SAME,
                      folder_status = RenameStatus.SAME,
                  )),
            patch("kleinanzeigen_bot.utils.dicts.save_dict"),
            patch("kleinanzeigen_bot.utils.misc.now"),
        ):
            publishing_persistence.persist_published_ad(
                ad_file = "test.yaml",
                ad_cfg = ad,
                ad_cfg_orig = ad_cfg_orig,
                old_ad_id = None,
                ad_id = 12345,
                mode = AdUpdateStrategy.REPLACE,
                config = cfg,
            )

        assert "price_reduction_count" not in ad_cfg_orig


class TestPersistPublishedAdSaveRollback:
    """Test that a save failure propagates the exception after rollback."""

    def test_propagates_save_error(self) -> None:
        """When save_dict raises, the exception is re-raised after rollback."""
        ad = _make_min_ad()
        ad_cfg_orig:dict[str, Any] = {"title": "Test Ad Title", "description": "Test description for the ad listing.", "type": "OFFER", "category": "160"}
        cfg = _make_config()

        with (
            patch("kleinanzeigen_bot.utils.dicts.save_dict", side_effect = RuntimeError("disk full")),
            patch("kleinanzeigen_bot.local_path_renaming.rename_referenced_local_image_files_after_id_change",
                  return_value = _make_image_rename_result_with_rollback()),
            patch("kleinanzeigen_bot.local_path_renaming.rename_local_ad_file_and_folder_after_id_change",
                  return_value = _local_path_renaming.LocalPathRenameResult(
                      ad_file = Path("test.yaml"),
                      file_status = RenameStatus.SAME,
                      folder_status = RenameStatus.SAME,
                  )),
            patch("kleinanzeigen_bot.utils.misc.now"),
            pytest.raises(RuntimeError, match = "disk full"),
        ):
            publishing_persistence.persist_published_ad(
                ad_file = "test.yaml",
                ad_cfg = ad,
                ad_cfg_orig = ad_cfg_orig,
                old_ad_id = None,
                ad_id = 12345,
                mode = AdUpdateStrategy.REPLACE,
                config = cfg,
            )

    def test_updates_images_from_rename_result(self) -> None:
        """When image_result.updated_images is not None, persist_published_ad writes it to ad_cfg_orig."""
        ad = _make_min_ad()
        ad_cfg_orig:dict[str, Any] = {"title": "Test Ad Title", "description": "Test description for the ad listing.", "type": "OFFER", "category": "160"}
        cfg = _make_config()

        with (
            patch("kleinanzeigen_bot.utils.dicts.save_dict"),
            patch("kleinanzeigen_bot.local_path_renaming.rename_referenced_local_image_files_after_id_change",
                  return_value = _make_image_rename_result_with_updates()),
            patch("kleinanzeigen_bot.local_path_renaming.rename_local_ad_file_and_folder_after_id_change",
                  return_value = _local_path_renaming.LocalPathRenameResult(
                      ad_file = Path("test.yaml"),
                      file_status = RenameStatus.SAME,
                      folder_status = RenameStatus.SAME,
                  )),
            patch("kleinanzeigen_bot.utils.misc.now"),
        ):
            publishing_persistence.persist_published_ad(
                ad_file = "test.yaml",
                ad_cfg = ad,
                ad_cfg_orig = ad_cfg_orig,
                old_ad_id = None,
                ad_id = 12345,
                mode = AdUpdateStrategy.REPLACE,
                config = cfg,
            )

        assert ad_cfg_orig["images"] == ["new_image.jpg"]


@pytest.mark.asyncio
async def test_publish_ad_raises_post_publish_persistence_error(caplog:pytest.LogCaptureFixture) -> None:
    """When persist_published_ad fails, publish_ad raises a post-submit persistence error."""
    ad = _make_min_ad()
    ad_cfg_orig:dict[str, Any] = {
        "title": "Test Ad Title",
        "description": "Test description for the ad listing.",
        "type": "OFFER",
        "category": "160",
    }
    cfg = _make_config()
    bot = KleinanzeigenBot()
    bot.browser = MagicMock()
    bot.browser.get = AsyncMock()
    bot.config = cfg

    with (
        patch("kleinanzeigen_bot.publishing_workflow.delete_old_ad_if_needed", new_callable = AsyncMock),
        patch.object(bot, "web_open", new_callable = AsyncMock),
        patch.object(bot, "dismiss_consent_banner", new_callable = AsyncMock),
        patch("kleinanzeigen_bot.publishing_form.fill_ad_form", new_callable = AsyncMock),
        patch("kleinanzeigen_bot.publishing_submission.submit_and_confirm_ad", new_callable = AsyncMock, return_value = 12345),
        patch("kleinanzeigen_bot.publishing_persistence.persist_published_ad",
              side_effect = RuntimeError("disk full")),
        patch("kleinanzeigen_bot.utils.misc.now"),
        caplog.at_level("ERROR"),
        pytest.raises(
            PostPublishPersistenceError,
            match = r"Post-publish persistence failed for 'Test Ad Title' \(ad ID 12345\)",
        ) as exc_info,
    ):
        await bot.publish_ad("test.yaml", ad, ad_cfg_orig, [], AdUpdateStrategy.REPLACE)

    assert exc_info.value.ad_id == 12345
    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert str(exc_info.value.__cause__) == "disk full"

    diagnostics = [
        record for record in caplog.records
        if "Post-publish persistence failed for 'Test Ad Title'" in record.getMessage()
    ]
    assert diagnostics
