# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Tests for publishing flow functionality."""

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kleinanzeigen_bot import (
    KleinanzeigenBot,
    publishing_flow,
)
from kleinanzeigen_bot import (
    local_path_renaming as _local_path_renaming,
)
from kleinanzeigen_bot.local_path_renaming import ImageRenameResult, LocalPathRenameResult, RenameStatus
from kleinanzeigen_bot.model.ad_model import Ad, AdUpdateStrategy
from kleinanzeigen_bot.model.config_model import Config
from kleinanzeigen_bot.utils.exceptions import PublishSubmissionUncertainError
from kleinanzeigen_bot.utils.web_scraping_mixin import By


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
        path_old_id = 1 if id_mismatch else 2,
        yaml_old_id = 1 if id_mismatch else 2,
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
            patch.object(publishing_flow.LOG, "info") as mock_info,
            patch.object(publishing_flow.LOG, "warning") as mock_warning,
        ):
            publishing_flow._log_local_path_rename_result(result, ad_id = 2, local_path_renaming_mode = mode)

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
            publishing_flow.persist_published_ad(
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
            publishing_flow.persist_published_ad(
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
            publishing_flow.persist_published_ad(
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
            publishing_flow.persist_published_ad(
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
async def test_publish_ad_survives_persistence_failure() -> None:
    """When persist_published_ad raises, publish_ad logs and does not propagate."""
    ad = _make_min_ad()
    ad_cfg_orig:dict[str, Any] = {"title": "Test Ad Title", "description": "Test description for the ad listing.", "type": "OFFER", "category": "160"}
    cfg = _make_config()
    bot = KleinanzeigenBot()
    bot.browser = MagicMock()
    bot.browser.get = AsyncMock()
    bot.config = cfg

    with (
        patch.object(bot, "_delete_old_ad_if_needed", new_callable = AsyncMock),
        patch.object(bot, "web_open", new_callable = AsyncMock),
        patch.object(bot, "_dismiss_consent_banner", new_callable = AsyncMock),
        patch.object(bot, "_fill_ad_form", new_callable = AsyncMock),
        patch("kleinanzeigen_bot.publishing_flow.submit_and_confirm_ad", new_callable = AsyncMock, return_value = 12345),
        patch("kleinanzeigen_bot.publishing_flow.persist_published_ad",
              side_effect = RuntimeError("disk full")),
    ):
        # Must not raise — the try/except in publish_ad swallows the error
        await bot.publish_ad("test.yaml", ad, ad_cfg_orig, [], AdUpdateStrategy.REPLACE)


class TestTrackingFallback:
    """Tests for _try_recover_ad_id_from_redirect helper method."""

    @pytest.mark.asyncio
    async def test_extract_ad_id_from_referrer(self, test_bot:KleinanzeigenBot) -> None:
        """Ad ID should be extracted from document.referrer containing the confirmation URL."""
        referrer_url = "https://www.kleinanzeigen.de/p-anzeige-aufgeben-bestaetigung.html?adId=3382410263"
        with patch.object(test_bot, "web_execute", new_callable = AsyncMock, return_value = referrer_url):
            result = await publishing_flow._try_recover_ad_id_from_redirect(test_bot)

        assert result == 3382410263

    @pytest.mark.asyncio
    async def test_extract_ad_id_from_script_content(self, test_bot:KleinanzeigenBot) -> None:
        """When referrer has no confirmation URL, ad ID should be extracted from inline script content."""
        referrer = "https://www.kleinanzeigen.de/m-meine-anzeigen.html"
        script_content = (
            'Belen.Tracking.initTrackingData({"page":"p-anzeige-aufgeben-bestaetigung.html?adId=44556677"});'
        )
        execute_returns = [referrer, script_content]

        with patch.object(test_bot, "web_execute", new_callable = AsyncMock, side_effect = execute_returns):
            result = await publishing_flow._try_recover_ad_id_from_redirect(test_bot)

        assert result == 44556677

    @pytest.mark.asyncio
    async def test_extract_ad_id_returns_none_when_not_found(self, test_bot:KleinanzeigenBot) -> None:
        """When neither referrer nor scripts contain a confirmation URL, None should be returned."""
        execute_returns = [
            "https://www.kleinanzeigen.de/m-meine-anzeigen.html",  # referrer
            "var x = 42;",  # script content — no confirmation URL
        ]

        with patch.object(test_bot, "web_execute", new_callable = AsyncMock, side_effect = execute_returns):
            result = await publishing_flow._try_recover_ad_id_from_redirect(test_bot)

        assert result is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("referrer_value", ["", None], ids = ["empty-referrer", "none-referrer"])
    async def test_extract_ad_id_falls_back_to_script_when_referrer_lacks_confirmation_url(
        self, test_bot:KleinanzeigenBot, referrer_value:str | None,
    ) -> None:
        """When document.referrer is empty or None, the script scan fallback should extract the ad ID."""
        script_content = 'initTrackingData("p-anzeige-aufgeben-bestaetigung.html?adId=11223344")'
        execute_returns = [referrer_value, script_content]

        with patch.object(test_bot, "web_execute", new_callable = AsyncMock, side_effect = execute_returns):
            result = await publishing_flow._try_recover_ad_id_from_redirect(test_bot)

        assert result == 11223344

    @pytest.mark.asyncio
    async def test_referrer_lookup_fails_gracefully_with_timeout(self, test_bot:KleinanzeigenBot) -> None:
        """When document.referrer lookup raises TimeoutError, script scan is tried as fallback."""
        script_content = 'initTrackingData("p-anzeige-aufgeben-bestaetigung.html?adId=55556666")'
        execute_returns:list[object] = [TimeoutError("timed out"), script_content]

        with patch.object(test_bot, "web_execute", new_callable = AsyncMock, side_effect = execute_returns):
            result = await publishing_flow._try_recover_ad_id_from_redirect(test_bot)

        assert result == 55556666

    @pytest.mark.asyncio
    async def test_script_scan_fails_gracefully(self, test_bot:KleinanzeigenBot) -> None:
        """When script content scan raises TimeoutError, None is returned."""
        referrer = "https://www.kleinanzeigen.de/m-meine-anzeigen.html"
        execute_returns:list[object] = [referrer, TimeoutError("timed out")]

        with patch.object(test_bot, "web_execute", new_callable = AsyncMock, side_effect = execute_returns):
            result = await publishing_flow._try_recover_ad_id_from_redirect(test_bot)

        assert result is None


class TestSubmitAndConfirmAd:
    """Tests for the submit_and_confirm_ad helper."""

    @pytest.mark.asyncio
    async def test_returns_ad_id_on_success(self, test_bot:KleinanzeigenBot) -> None:
        """Happy path: ad ID is extracted from confirmation URL."""
        ad = _make_min_ad()
        captcha_config = test_bot.config.captcha
        confirmation_url = "https://www.kleinanzeigen.de/p-anzeige-aufgeben-bestaetigung.html?adId=12345"

        with (
            patch("kleinanzeigen_bot.captcha_flow.check_and_wait_for_captcha", new_callable = AsyncMock) as mock_captcha,
            patch.object(test_bot, "web_set_input_value", new_callable = AsyncMock) as mock_set_title,
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = [None] * 4),
            patch.object(test_bot, "web_await", new_callable = AsyncMock),
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, return_value = confirmation_url),
            patch.object(test_bot, "web_scroll_page_down", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.publishing_flow.ainput", new_callable = AsyncMock),
        ):
            result = await publishing_flow.submit_and_confirm_ad(
                test_bot, "test.yaml", ad, AdUpdateStrategy.REPLACE,
                captcha_config = captcha_config,
            )

        assert result == 12345
        mock_captcha.assert_awaited_once()
        mock_set_title.assert_awaited_once_with("ad-title", "Test Ad Title")

    @pytest.mark.asyncio
    async def test_dismisses_upsell_dialog(self, test_bot:KleinanzeigenBot) -> None:
        """Upsell dialog is dismissed when present."""
        ad = _make_min_ad()
        captcha_config = test_bot.config.captcha
        upsell_element = AsyncMock()
        confirmation_url = "https://www.kleinanzeigen.de/p-anzeige-aufgeben-bestaetigung.html?adId=12345"

        with (
            patch("kleinanzeigen_bot.captcha_flow.check_and_wait_for_captcha", new_callable = AsyncMock),
            patch.object(test_bot, "web_set_input_value", new_callable = AsyncMock),
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = [upsell_element, None, None, None]),
            patch.object(test_bot, "web_await", new_callable = AsyncMock),
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, return_value = confirmation_url),
            patch.object(test_bot, "web_scroll_page_down", new_callable = AsyncMock),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.publishing_flow.ainput", new_callable = AsyncMock),
        ):
            result = await publishing_flow.submit_and_confirm_ad(
                test_bot, "test.yaml", ad, AdUpdateStrategy.REPLACE,
                captcha_config = captcha_config,
            )

        assert result == 12345
        assert mock_click.await_count == 2
        # Verify the dismiss button XPath was used (without coupling to internal timeout constant)
        dismiss_xpath = "//dialog[@open]//button[contains(., 'Ohne Hochschieben weiter')]"
        assert any(
            call_args.args[0] == By.XPATH and call_args.args[1] == dismiss_xpath
            for call_args in mock_click.await_args_list
        )

    @pytest.mark.asyncio
    async def test_confirms_no_image_warning(self, test_bot:KleinanzeigenBot) -> None:
        """No-image warning is confirmed when ad has no images."""
        ad = _make_min_ad()
        captcha_config = test_bot.config.captcha
        no_image_element = AsyncMock()
        confirmation_url = "https://www.kleinanzeigen.de/p-anzeige-aufgeben-bestaetigung.html?adId=12345"

        with (
            patch("kleinanzeigen_bot.captcha_flow.check_and_wait_for_captcha", new_callable = AsyncMock),
            patch.object(test_bot, "web_set_input_value", new_callable = AsyncMock),
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = [None, None, no_image_element, None]),
            patch.object(test_bot, "web_await", new_callable = AsyncMock),
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, return_value = confirmation_url),
            patch.object(test_bot, "web_scroll_page_down", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.publishing_flow.ainput", new_callable = AsyncMock),
        ):
            result = await publishing_flow.submit_and_confirm_ad(
                test_bot, "test.yaml", ad, AdUpdateStrategy.REPLACE,
                captcha_config = captcha_config,
            )

        assert result == 12345
        no_image_element.click.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_detects_payment_form(self, test_bot:KleinanzeigenBot) -> None:
        """Payment form detection triggers scroll and ainput."""
        ad = _make_min_ad()
        captcha_config = test_bot.config.captcha
        payment_element = AsyncMock()
        confirmation_url = "https://www.kleinanzeigen.de/p-anzeige-aufgeben-bestaetigung.html?adId=12345"

        with (
            patch("kleinanzeigen_bot.captcha_flow.check_and_wait_for_captcha", new_callable = AsyncMock),
            patch.object(test_bot, "web_set_input_value", new_callable = AsyncMock),
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = [None, None, None, payment_element]),
            patch.object(test_bot, "web_await", new_callable = AsyncMock),
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, return_value = confirmation_url),
            patch.object(test_bot, "web_scroll_page_down", new_callable = AsyncMock) as mock_scroll,
            patch("kleinanzeigen_bot.publishing_flow.ainput", new_callable = AsyncMock) as mock_ainput,
        ):
            result = await publishing_flow.submit_and_confirm_ad(
                test_bot, "test.yaml", ad, AdUpdateStrategy.REPLACE,
                captcha_config = captcha_config,
            )

        assert result == 12345
        mock_scroll.assert_awaited_once()
        mock_ainput.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_falls_back_to_tracking_when_confirmation_fails(self, test_bot:KleinanzeigenBot) -> None:
        """When confirmation URL polling fails, ad ID is recovered from tracking data."""
        ad = _make_min_ad()
        captcha_config = test_bot.config.captcha

        with (
            patch("kleinanzeigen_bot.captcha_flow.check_and_wait_for_captcha", new_callable = AsyncMock),
            patch.object(test_bot, "web_set_input_value", new_callable = AsyncMock),
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = [None] * 4),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = TimeoutError("timed out")),
            patch.object(test_bot, "web_execute", new_callable = AsyncMock),
            patch.object(test_bot, "web_scroll_page_down", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.publishing_flow._try_recover_ad_id_from_redirect", new_callable = AsyncMock, return_value = 99999),
            patch("kleinanzeigen_bot.publishing_flow.ainput", new_callable = AsyncMock),
        ):
            result = await publishing_flow.submit_and_confirm_ad(
                test_bot, "test.yaml", ad, AdUpdateStrategy.REPLACE,
                captcha_config = captcha_config,
            )

        assert result == 99999

    @pytest.mark.asyncio
    async def test_raises_uncertainty_error_when_recovery_fails(self, test_bot:KleinanzeigenBot) -> None:
        """When confirmation and tracking recovery fail, PublishSubmissionUncertainError is raised."""
        ad = _make_min_ad()
        captcha_config = test_bot.config.captcha

        with (
            patch("kleinanzeigen_bot.captcha_flow.check_and_wait_for_captcha", new_callable = AsyncMock),
            patch.object(test_bot, "web_set_input_value", new_callable = AsyncMock),
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = [None] * 4),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = TimeoutError("timed out")),
            patch.object(test_bot, "web_execute", new_callable = AsyncMock),
            patch.object(test_bot, "web_scroll_page_down", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.publishing_flow._try_recover_ad_id_from_redirect", new_callable = AsyncMock, return_value = None),
            patch("kleinanzeigen_bot.publishing_flow.ainput", new_callable = AsyncMock),
            pytest.raises(PublishSubmissionUncertainError),
        ):
            await publishing_flow.submit_and_confirm_ad(
                test_bot, "test.yaml", ad, AdUpdateStrategy.REPLACE,
                captcha_config = captcha_config,
            )
