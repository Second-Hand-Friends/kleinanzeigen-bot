# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Tests for publishing flow functionality."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kleinanzeigen_bot import (
    LOG,
    KleinanzeigenBot,
    publishing_flow,
)
from kleinanzeigen_bot import (
    local_path_renaming as _local_path_renaming,
)
from kleinanzeigen_bot import (
    publishing_flow as _publishing_flow,
)
from kleinanzeigen_bot.local_path_renaming import ImageRenameResult, LocalPathRenameResult, RenameStatus
from kleinanzeigen_bot.model.ad_model import Ad, AdUpdateStrategy
from kleinanzeigen_bot.model.config_model import AdDefaults, Config
from kleinanzeigen_bot.utils.exceptions import CategoryResolutionError, PublishSubmissionUncertainError
from kleinanzeigen_bot.utils.web_scraping_mixin import By, Element


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


def _make_flow(test_bot:KleinanzeigenBot) -> _publishing_flow.PublishingFormFlow:
    """Helper to create a PublishingFormFlow wrapping test_bot for tests."""
    return _publishing_flow.PublishingFormFlow(
        web = test_bot,
        root_url = test_bot.root_url,
        ad_defaults = getattr(test_bot.config, "ad_defaults", AdDefaults()),
    )


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
        patch.object(publishing_flow.PublishingFormFlow, "fill_ad_form", new_callable = AsyncMock),
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


class TestKleinanzeigenBotContactLocationHardening:
    @pytest.mark.parametrize(
        ("target", "candidate", "expected"),
        [
            ("10115 - Metroville", "10115 - Metroville", True),
            ("10115 - Metroville", "12623 - Metroville", False),
            ("Metroville", "12623 - Metroville", True),
            ("Berlin", "Berlin - Mitte", True),
            ("Metroville", None, False),
            ("Berlin", "Hamburg", False),
            ("Berlin", "berlin", True),
            ("Berlin", "  Berlin  ", True),
        ],
    )
    def test_location_matches_target(self, test_bot:KleinanzeigenBot, target:str, candidate:str | None, expected:bool) -> None:
        matcher = getattr(_publishing_flow.PublishingFormFlow, "_location_matches_target")
        assert matcher(target, candidate) is expected

    @pytest.mark.asyncio
    async def test_read_city_selection_text_prefers_live_input_value(self, test_bot:KleinanzeigenBot) -> None:
        city_input = MagicMock(spec = Element)
        city_input.local_name = "input"
        city_input.apply = AsyncMock(return_value = "Live City")

        with (
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = city_input),
            patch.object(test_bot, "web_text", new_callable = AsyncMock) as web_text_mock,
        ):
            selected = await getattr(_make_flow(test_bot), "_read_city_selection_text")()

        assert selected == "Live City"
        web_text_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_read_city_selection_text_non_input_fallback_uses_text_content(
        self,
        test_bot:KleinanzeigenBot,
    ) -> None:
        """When city element is not an input and web_text times out, fallback to textContent."""
        city_element = MagicMock(spec = Element)
        city_element.local_name = "button"
        city_element.apply = AsyncMock(return_value = "Berlin - Mitte")

        with (
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = city_element),
            patch.object(test_bot, "web_text", new_callable = AsyncMock, side_effect = TimeoutError("timeout")),
        ):
            selected = await getattr(_make_flow(test_bot), "_read_city_selection_text")()

        assert selected == "Berlin - Mitte"
        city_element.apply.assert_called_once_with("(elem) => (elem.textContent || '').trim()")

    @pytest.mark.asyncio
    async def test_set_contact_fields_fails_closed_when_zipcode_cannot_be_set(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        ad_cfg = Ad.model_validate(base_ad_config)

        with (
            patch.object(test_bot, "web_input", new_callable = AsyncMock, side_effect = TimeoutError("zip timeout")),
            patch.object(_publishing_flow.PublishingFormFlow, "_set_contact_location", new_callable = AsyncMock) as set_location_mock,
            pytest.raises(TimeoutError, match = "Failed to set contact zipcode"),
        ):
            await getattr(_make_flow(test_bot), "_set_contact_fields")(ad_cfg.contact)

        set_location_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_set_contact_fields_skips_zipcode_and_location_when_empty(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """When no zipcode is configured, both ZIP entry and location setting are skipped without error."""
        config = base_ad_config | {"contact": base_ad_config["contact"] | {"zipcode": ""}}
        ad_cfg = Ad.model_validate(config)

        with (
            patch.object(test_bot, "web_input", new_callable = AsyncMock) as web_input_mock,
            patch.object(_publishing_flow.PublishingFormFlow, "_set_contact_location", new_callable = AsyncMock) as set_location_mock,
            patch.object(test_bot, "web_check", new_callable = AsyncMock, return_value = True),
        ):
            await getattr(_make_flow(test_bot), "_set_contact_fields")(ad_cfg.contact)

        web_input_mock.assert_not_awaited()
        set_location_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_set_contact_location_fails_when_city_suffix_matches_multiple_zip_codes(self, test_bot:KleinanzeigenBot) -> None:
        """When multiple ZIP codes share the same city name and no exact match, selection must fail closed."""
        city_button = MagicMock(spec = Element)
        city_button.local_name = "button"
        city_button.attrs = {"role": "combobox", "aria-controls": "ad-city-menu"}

        option_a = MagicMock(spec = Element)
        option_a.text = "10115 - Metroville"
        option_b = MagicMock(spec = Element)
        option_b.text = "12623 - Metroville"

        def _mock_city_option_text(elem:Element) -> str:
            return str(getattr(elem, "text", "") or "")

        async def _web_await_side_effect(condition:Callable[..., Awaitable[bool] | bool], **_:Any) -> Any:
            result = condition()
            return await result if asyncio.iscoroutine(result) else result

        with (
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = city_button),
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, return_value = [option_a, option_b]),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = _web_await_side_effect),
            patch.object(_publishing_flow.PublishingFormFlow, "_read_city_selection_text", new_callable = AsyncMock, return_value = None),
            patch.object(_publishing_flow.PublishingFormFlow, "_city_option_text", new_callable = AsyncMock, side_effect = _mock_city_option_text),
            pytest.raises(TimeoutError, match = "City combobox options are ambiguous for location: Metroville"),
        ):
            await getattr(_make_flow(test_bot), "_set_contact_location")("Metroville")

    @pytest.mark.asyncio
    async def test_set_contact_location_raises_when_selection_does_not_converge(self, test_bot:KleinanzeigenBot) -> None:
        city_button = MagicMock(spec = Element)
        city_button.local_name = "button"
        city_button.attrs = {"role": "combobox", "aria-controls": "ad-city-menu"}

        target_option = MagicMock(spec = Element)
        target_option.text = "10115 - Metroville"
        target_option.click = AsyncMock()

        wait_calls = 0

        async def web_await_side_effect(condition:Callable[..., Awaitable[bool] | bool], **_:Any) -> Any:
            nonlocal wait_calls
            wait_calls += 1

            result = condition()
            condition_value = await result if asyncio.iscoroutine(result) else result
            if wait_calls == 1:
                return condition_value
            raise TimeoutError("Condition not met")

        with (
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = city_button),
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, return_value = [target_option]),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = web_await_side_effect),
            patch.object(_publishing_flow.PublishingFormFlow, "_read_city_selection_text", new_callable = AsyncMock, return_value = "20095 - Rivertown"),
            pytest.raises(TimeoutError, match = "City selection did not converge"),
        ):
            await getattr(_make_flow(test_bot), "_set_contact_location")("10115 - Metroville")

    @pytest.mark.asyncio
    async def test_set_contact_location_accepts_readonly_input_with_zip_derived_value(self, test_bot:KleinanzeigenBot) -> None:
        """When ad-city is a readonly <input> with a non-empty prefilled value (zip-derived), accept it."""
        city_input = MagicMock(spec = Element)
        city_input.local_name = "input"
        city_input.attrs = {"readonly": "", "value": "Metroville - Riverside"}

        with (
            patch.object(_publishing_flow.PublishingFormFlow, "_read_city_selection_text", new_callable = AsyncMock, return_value = "Metroville - Riverside"),
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = city_input),
            patch.object(_publishing_flow.PublishingFormFlow, "_select_city_combobox_option", new_callable = AsyncMock) as combobox_mock,
        ):
            await getattr(_make_flow(test_bot), "_set_contact_location")("Metroville")
            combobox_mock.assert_not_called()


class TestConditionSelector:
    """Regression tests for condition dialog selection."""

    @pytest.mark.asyncio
    async def test_condition_selects_radio_by_value(self, test_bot:KleinanzeigenBot) -> None:
        """Condition selection should resolve radios by value in the new dialog."""
        dialog = MagicMock()
        trigger = MagicMock()
        trigger.attrs = {"id": "condition-trigger", "aria-controls": "condition-dialog"}
        trigger.click = AsyncMock()
        radio = MagicMock()
        radio_attrs = MagicMock()
        radio_attrs.id = "radio-condition-ok"
        radio_attrs.get.side_effect = lambda key, default = None: "radio-condition-ok" if key == "id" else default
        radio.attrs = radio_attrs
        radio.click = AsyncMock()

        async def probe_side_effect(selector_type:By, selector_value:str, **_:Any) -> Element | None:
            # trigger lookup returns the dialog trigger button
            if selector_type == By.XPATH and "contains(@for, '.condition')" in selector_value:
                return trigger
            # radio lookup returns the matching radio
            if selector_type == By.XPATH and "@type='radio'" in selector_value and "@value='ok'" in selector_value:
                return radio
            return None

        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = probe_side_effect),
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = dialog),
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
        ):
            handled = await getattr(_make_flow(test_bot), "_set_condition")("ok")

            assert handled is True

            clicked_xpath_selectors = [str(call.args[1]) for call in mock_click.await_args_list if len(call.args) > 1]
            trigger.click.assert_awaited_once()
            assert any("label[@for=" in selector and "radio-condition-ok" in selector for selector in clicked_xpath_selectors)
            assert any("Bestätigen" in selector for selector in clicked_xpath_selectors)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("configured", "expected_api_value", "expect_warning"),
        [
            ("wie_neu", "like_new", True),
            ("sehr_gut", "like_new", True),
            ("new", "new", False),
            ("like_new", "like_new", False),
            ("ok", "ok", False),
            ("alright", "alright", False),
            ("defect", "defect", False),
        ],
    )
    async def test_condition_tokens_warn_only_for_legacy_values(
        self,
        test_bot:KleinanzeigenBot,
        configured:str,
        expected_api_value:str,
        expect_warning:bool,
        caplog:pytest.LogCaptureFixture,
    ) -> None:
        """Condition tokens should resolve to the current API codes and warn only for legacy German values."""
        caplog.set_level(logging.WARNING, logger = LOG.name)
        dialog = MagicMock()
        trigger = MagicMock()
        trigger.attrs = {"id": "condition-trigger", "aria-controls": "condition-dialog"}
        trigger.click = AsyncMock()
        radio = MagicMock()
        radio_attrs = MagicMock()
        radio_attrs.get.side_effect = lambda key, default = None: f"radio-condition-{expected_api_value}" if key == "id" else default
        radio.attrs = radio_attrs
        radio.click = AsyncMock()

        probed_values:list[str] = []

        async def probe_side_effect(selector_type:By, selector_value:str, **_:Any) -> Element | None:
            if selector_type == By.XPATH and "contains(@for, '.condition')" in selector_value:
                return trigger
            if selector_type == By.XPATH and "@type='radio'" in selector_value:
                if f"@value='{expected_api_value}'" in selector_value:
                    probed_values.append(expected_api_value)
                    return radio
                if f"@value='{configured}'" in selector_value:
                    probed_values.append(configured)
                    return radio
            return None

        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = probe_side_effect),
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = dialog),
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
        ):
            handled = await getattr(_make_flow(test_bot), "_set_condition")(configured)

        assert handled is True
        warning_messages = [record.message for record in caplog.records if record.levelno == logging.WARNING]
        if expect_warning:
            assert len(warning_messages) == 1
            assert configured in warning_messages[0]
            assert expected_api_value in warning_messages[0]
            # Legacy German values should prefer the mapped API code and stop once it is found.
            assert probed_values == [expected_api_value]
        else:
            assert warning_messages == []
            assert probed_values == [configured]
        clicked_xpath_selectors = [str(call.args[1]) for call in mock_click.await_args_list if len(call.args) > 1]
        assert any(f"radio-condition-{expected_api_value}" in selector for selector in clicked_xpath_selectors)

    @pytest.mark.asyncio
    async def test_condition_legacy_value_falls_back_when_mapped_value_is_missing(
        self,
        test_bot:KleinanzeigenBot,
        caplog:pytest.LogCaptureFixture,
    ) -> None:
        """Legacy values should still work if the mapped API radio is unavailable."""
        caplog.set_level(logging.WARNING, logger = LOG.name)
        dialog = MagicMock()
        trigger = MagicMock()
        trigger.attrs = {"id": "condition-trigger", "aria-controls": "condition-dialog"}
        trigger.click = AsyncMock()
        radio = MagicMock()
        radio_attrs = MagicMock()
        radio_attrs.id = "radio-condition-wie_neu"
        radio_attrs.get.side_effect = lambda key, default = None: "radio-condition-wie_neu" if key == "id" else default
        radio.attrs = radio_attrs
        radio.click = AsyncMock()

        probed_values:list[str] = []

        async def probe_side_effect(selector_type:By, selector_value:str, **_:Any) -> Element | None:
            if selector_type == By.XPATH and "contains(@for, '.condition')" in selector_value:
                return trigger
            if selector_type == By.XPATH and "@type='radio'" in selector_value:
                if "@value='like_new'" in selector_value:
                    probed_values.append("like_new")
                    return None
                if "@value='wie_neu'" in selector_value:
                    probed_values.append("wie_neu")
                    return radio
            return None

        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = probe_side_effect),
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = dialog),
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
        ):
            handled = await getattr(_make_flow(test_bot), "_set_condition")("wie_neu")

        assert handled is True
        warning_messages = [record.message for record in caplog.records if record.levelno == logging.WARNING]
        assert len(warning_messages) == 1
        assert "wie_neu" in warning_messages[0]
        assert "like_new" in warning_messages[0]
        assert probed_values == ["like_new", "wie_neu"]
        clicked_xpath_selectors = [str(call.args[1]) for call in mock_click.await_args_list if len(call.args) > 1]
        assert any("radio-condition-wie_neu" in selector for selector in clicked_xpath_selectors)

    @pytest.mark.asyncio
    async def test_condition_legacy_value_warns_even_when_not_handled(
        self,
        test_bot:KleinanzeigenBot,
        caplog:pytest.LogCaptureFixture,
    ) -> None:
        """Legacy German values should warn even when the condition dialog path is unavailable."""
        caplog.set_level(logging.WARNING, logger = LOG.name)

        with patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = None):
            handled = await getattr(_make_flow(test_bot), "_set_condition")("wie_neu")

        assert handled is False
        warning_messages = [record.message for record in caplog.records if record.levelno == logging.WARNING]
        assert len(warning_messages) == 1
        assert "wie_neu" in warning_messages[0]
        assert "like_new" in warning_messages[0]

    @pytest.mark.asyncio
    async def test_condition_unknown_value_raises(self, test_bot:KleinanzeigenBot) -> None:
        """Unknown condition values should raise when no matching radio option is present."""
        dialog = MagicMock()
        trigger = MagicMock()
        trigger.attrs = {"id": "condition-trigger", "aria-controls": "condition-dialog"}
        trigger.click = AsyncMock()

        async def probe_side_effect(selector_type:By, selector_value:str, **_:Any) -> Element | None:
            if selector_type == By.XPATH and "contains(@for, '.condition')" in selector_value:
                return trigger
            # Radio lookups for unknown values return None (no matching radio in the dialog).
            return None

        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = probe_side_effect),
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = dialog),
            pytest.raises(TimeoutError, match = "Failed to set attribute 'condition_s'"),
        ):
            await getattr(_make_flow(test_bot), "_set_condition")("totally_unknown_value")

    @pytest.mark.asyncio
    async def test_condition_rejects_shipping_trigger(self, test_bot:KleinanzeigenBot) -> None:
        """Condition dialog path should not click shipping trigger controls."""
        trigger = MagicMock()
        trigger.attrs = {
            "id": "ad-shipping-options",
            "aria-controls": None,
            "aria-haspopup": "dialog",
        }
        trigger.click = AsyncMock()

        async def find_side_effect(selector_type:By, selector_value:str, **_:Any) -> Element:
            if selector_type == By.XPATH and "contains(@for, '.condition')" in selector_value:
                return trigger
            raise TimeoutError("unexpected selector")

        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = trigger),
            patch.object(test_bot, "web_find", new_callable = AsyncMock, side_effect = find_side_effect),
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
        ):
            handled = await getattr(_make_flow(test_bot), "_set_condition")("new")

        assert handled is False
        # Regression guard: wrong shipping trigger must never be clicked by condition handler
        trigger.click.assert_not_awaited()
        mock_click.assert_not_awaited()


class TestConditionFallbackToGenericHandler:
    """Regression tests for condition_s fallback behavior.

    When _set_condition reports "not handled" (e.g. category uses a button-combobox
    instead of a dialog), _set_special_attributes should fall through to the generic
    XPath-based handler.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("condition_s", "expected_generic_value"),
        [
            ("new", "new"),
            ("wie_neu", "like_new"),
        ],
    )
    async def test_condition_falls_back_to_generic_handler_with_canonical_value(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        condition_s:str,
        expected_generic_value:str,
    ) -> None:
        """Fallback should pass the canonical condition value to the generic combobox handler."""
        ad_cfg = Ad.model_validate(base_ad_config | {"category": "185/249", "special_attributes": {"condition_s": condition_s}, "shipping_type": "PICKUP"})

        button_elem = MagicMock()
        button_attrs = MagicMock()
        button_attrs.get.side_effect = lambda key, default = None: {
            "id": "modellbau.condition",
            "type": "button",
            "role": "combobox",
            "name": None,
        }.get(key, default)
        button_elem.attrs = button_attrs
        button_elem.local_name = "button"
        probe_elem = MagicMock()
        probe_elem.attrs = {"id": "modellbau.condition"}

        with (
            patch.object(
                _publishing_flow.PublishingFormFlow,
                "_set_condition",
                new_callable = AsyncMock,
                return_value = False,
            ) as mock_set_condition,
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = probe_elem),
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, return_value = [button_elem]),
            patch.object(
                _publishing_flow.PublishingFormFlow,
                "_select_button_combobox",
                new_callable = AsyncMock,
            ) as mock_select_combobox,
        ):
            await getattr(_make_flow(test_bot), "_set_special_attributes")(ad_cfg)

        mock_set_condition.assert_awaited_once_with(condition_s)
        mock_select_combobox.assert_awaited_once_with("modellbau.condition", expected_generic_value)

    @pytest.mark.asyncio
    async def test_condition_timeout_propagates_instead_of_falling_back(self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any]) -> None:
        """Real condition dialog failures should propagate and not silently use generic fallback."""
        ad_cfg = Ad.model_validate(base_ad_config | {"category": "161/176", "special_attributes": {"condition_s": "ok"}})

        with (
            patch.object(
                _publishing_flow.PublishingFormFlow,
                "_set_condition",
                new_callable = AsyncMock,
                side_effect = TimeoutError("dialog timeout"),
            ),
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock) as mock_find_all,
            pytest.raises(TimeoutError, match = "dialog timeout"),
        ):
            await getattr(_make_flow(test_bot), "_set_special_attributes")(ad_cfg)

        mock_find_all.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_condition_s_missing_control_logs_warning_and_continues(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        caplog:pytest.LogCaptureFixture,
    ) -> None:
        """Missing condition_s control should warn and allow later attributes to continue."""
        caplog.set_level(logging.WARNING, logger = LOG.name)
        ad_cfg = Ad.model_validate(
            base_ad_config
            | {
                "category": "185/249",
                "special_attributes": {"condition_s": "ok", "color_s": "beige"},
                "shipping_type": "PICKUP",
            }
        )

        color_elem = MagicMock()
        color_attrs = MagicMock()
        color_attrs.id = "kleidung_herren.color"
        color_attrs.get.side_effect = lambda key, default = None: {
            "id": "kleidung_herren.color",
            "type": "button",
            "role": "combobox",
            "name": None,
        }.get(key, default)
        color_elem.attrs = color_attrs
        color_elem.local_name = "button"

        condition_probe = MagicMock()
        condition_probe.attrs = {"id": "condition_s"}

        async def find_all_side_effect(selector_type:By, selector_value:str, **_:Any) -> list[Element]:
            if selector_type == By.XPATH and "color_s" in selector_value:
                return [color_elem]
            if selector_type == By.XPATH and "condition_s" in selector_value:
                raise AssertionError("condition_s lookup should be skipped when the probe returns None")
            return []

        async def probe_side_effect(selector_type:By, selector_value:str, **_:Any) -> Element | None:
            if selector_type == By.XPATH and "condition_s" in selector_value:
                return None
            if selector_type == By.XPATH and "color_s" in selector_value:
                return condition_probe
            return None

        with (
            patch.object(_publishing_flow.PublishingFormFlow, "_set_condition", new_callable = AsyncMock, return_value = False) as mock_set_condition,
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = probe_side_effect),
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, side_effect = find_all_side_effect),
            patch.object(_publishing_flow.PublishingFormFlow, "_select_button_combobox", new_callable = AsyncMock) as mock_select_combobox,
        ):
            await getattr(_make_flow(test_bot), "_set_special_attributes")(ad_cfg)

        mock_set_condition.assert_awaited_once_with("ok")
        mock_select_combobox.assert_awaited_once_with("kleidung_herren.color", "beige")
        warning_messages = [record.message for record in caplog.records if record.levelno == logging.WARNING]
        assert len([message for message in warning_messages if "Special attribute 'condition_s' is not available" in message]) == 1

    @pytest.mark.asyncio
    async def test_condition_s_lookup_timeout_propagates(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """Lookup timeouts for condition_s should still fail instead of being skipped."""
        ad_cfg = Ad.model_validate(base_ad_config | {"category": "185/249", "special_attributes": {"condition_s": "ok"}, "shipping_type": "PICKUP"})

        condition_probe = MagicMock()
        condition_probe.attrs = {"id": "condition_s"}

        with (
            patch.object(_publishing_flow.PublishingFormFlow, "_set_condition", new_callable = AsyncMock, return_value = False),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = condition_probe),
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, side_effect = TimeoutError("lookup timeout")),
            pytest.raises(TimeoutError, match = "lookup timeout"),
        ):
            await getattr(_make_flow(test_bot), "_set_special_attributes")(ad_cfg)


class TestCategoryProbeBehavior:
    """Tests for category marker probing without retry backoff."""

    @pytest.mark.asyncio
    async def test_set_category_uses_probe_for_auto_selected_marker(self, test_bot:KleinanzeigenBot) -> None:
        """In _set_category, category marker lookup should go through web_probe."""
        category_marker = MagicMock()
        category_marker.apply = AsyncMock(return_value = "Auto Category")

        async def probe(selector_type:Any, selector_value:str, **_kwargs:Any) -> Any:
            if selector_value == "ad-category-path":
                return category_marker
            return None  # no suggestion picker shown

        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = probe) as mock_probe,
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_find", new_callable = AsyncMock),
            patch.object(test_bot, "web_open", new_callable = AsyncMock),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
        ):
            await getattr(_make_flow(test_bot), "_set_category")("185/249", "data/my_ads/ad.yaml")

        mock_probe.assert_any_await(By.ID, "ad-category-path")

    @pytest.mark.asyncio
    async def test_set_category_without_explicit_category_requires_probe_match(self, test_bot:KleinanzeigenBot) -> None:
        """When no category is configured, missing marker should fail fast."""
        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = None),
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            pytest.raises(AssertionError, match = "No category specified"),
        ):
            await getattr(_make_flow(test_bot), "_set_category")(None, "data/my_ads/ad.yaml")


class TestCategorySuggestionPicker:
    """Regression tests for the post-redesign category-suggestion radio picker fallback."""

    @staticmethod
    def _picker_probe_factory(picker_present:bool) -> Callable[..., Any]:
        async def probe(selector_type:Any, selector_value:str, **_kwargs:Any) -> Any:
            if selector_value == "ad-category-path":
                marker = MagicMock()
                marker.apply = AsyncMock(return_value = "")
                return marker
            if selector_value == "ad-category-picker":
                return MagicMock() if picker_present else None
            return None

        return probe

    @staticmethod
    def _radio(value:str, radio_id:str | None = None) -> MagicMock:
        elem = MagicMock()
        elem.attrs = {"value": value}
        if radio_id is not None:
            elem.attrs["id"] = radio_id
        elem.click = AsyncMock()
        return elem

    @pytest.mark.asyncio
    async def test_picker_absent_leaves_flow_unchanged(self, test_bot:KleinanzeigenBot) -> None:
        """No picker -> no-op, no find_all / label click."""
        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = self._picker_probe_factory(picker_present = False)),
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock) as mock_find_all,
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
        ):
            await getattr(_make_flow(test_bot), "_resolve_category_suggestions")("73/76/sachbuecher")

        mock_find_all.assert_not_awaited()
        mock_click.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_picker_present_without_rendered_radios_retries_then_times_out(self, test_bot:KleinanzeigenBot) -> None:
        """Picker shell present but radios not rendered yet should fail closed after a bounded retry."""
        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = self._picker_probe_factory(picker_present = True)),
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, return_value = []) as mock_find_all,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as mock_sleep,
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            pytest.raises(TimeoutError, match = "Category suggestion picker element found but no radio suggestions rendered after waiting."),
        ):
            await getattr(_make_flow(test_bot), "_resolve_category_suggestions")("73/76/sachbuecher")

        assert mock_find_all.await_count == 2
        mock_sleep.assert_awaited_once()
        mock_click.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_picker_present_matches_leaf_segment_and_clicks_label(self, test_bot:KleinanzeigenBot) -> None:
        """Picker present with matching radio value -> label[for=ID] is clicked (value != id to catch regressions)."""
        radios = [
            self._radio("76", "category-suggestion-parent"),
            self._radio("77", "category-suggestion-leaf"),
            self._radio("240", "category-suggestion-other"),
        ]

        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = self._picker_probe_factory(picker_present = True)),
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, return_value = radios),
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
        ):
            await getattr(_make_flow(test_bot), "_resolve_category_suggestions")("73/77")

        mock_click.assert_awaited_once()
        selector_type, selector_value = mock_click.call_args.args[:2]
        assert selector_type == By.XPATH
        assert "label[@for='category-suggestion-leaf']" in selector_value
        assert "'ad-category-picker'" in selector_value

    @pytest.mark.asyncio
    async def test_picker_present_no_match_raises_with_offered_list(self, test_bot:KleinanzeigenBot) -> None:
        """Picker present but path has no matching segment -> CategoryResolutionError listing offered IDs."""
        radios = [
            self._radio("76", "category-suggestion-parent"),
            self._radio("77", "category-suggestion-leaf"),
            self._radio("240", "category-suggestion-other"),
        ]

        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = self._picker_probe_factory(picker_present = True)),
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, return_value = radios),
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            pytest.raises(CategoryResolutionError, match = r"Category suggestion picker shown.*offered") as exc_info,
        ):
            await getattr(_make_flow(test_bot), "_resolve_category_suggestions")("999/888")

        mock_click.assert_not_awaited()
        error_message = str(exc_info.value)
        # The error must name the configured (unmatched) path and every offered ID,
        # otherwise the user cannot know what to correct.
        assert "999/888" in error_message
        for offered_id in ("76", "77", "240"):
            assert offered_id in error_message

    @pytest.mark.asyncio
    async def test_picker_prefers_deepest_matching_segment(self, test_bot:KleinanzeigenBot) -> None:
        """When both parent and leaf segments match radios, the leaf (deepest) wins."""
        radios = [self._radio("76", "id-for-76"), self._radio("77", "id-for-77")]

        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = self._picker_probe_factory(picker_present = True)),
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, return_value = radios),
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
        ):
            await getattr(_make_flow(test_bot), "_resolve_category_suggestions")("76/77")

        mock_click.assert_awaited_once()
        assert "label[@for='id-for-77']" in mock_click.call_args.args[1]


class TestShippingDialogFlow:
    """Regression tests for shipping dialog flow using new radio selectors only."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("selected", [False, True])
    async def test_pickup_shipping_radio_selection(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        selected:bool,
    ) -> None:
        """PICKUP shipping should click the pickup radio only when it is not already selected."""
        ad_cfg = Ad.model_validate(base_ad_config | {"shipping_type": "PICKUP"})

        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = MagicMock()) as mock_probe,
            patch.object(test_bot, "web_check", new_callable = AsyncMock, return_value = selected),
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
        ):
            await getattr(_make_flow(test_bot), "_set_shipping")(ad_cfg)

        mock_probe.assert_awaited_once()
        assert mock_probe.call_args.args[:2] == (By.ID, "ad-shipping-enabled-no")
        if selected:
            mock_click.assert_not_awaited()
        else:
            mock_click.assert_awaited_once()
            assert mock_click.call_args.args[:2] == (By.ID, "ad-shipping-enabled-no")

    @pytest.mark.asyncio
    async def test_pickup_shipping_raises_when_radio_lookup_times_out(self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any]) -> None:
        """PICKUP shipping should fail fast when pickup radio selector is unavailable."""
        ad_cfg = Ad.model_validate(base_ad_config | {"shipping_type": "PICKUP"})

        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = MagicMock()),
            patch.object(test_bot, "web_check", new_callable = AsyncMock, side_effect = TimeoutError("pickup lookup timed out")),
            pytest.raises(TimeoutError, match = "Failed to set shipping attribute for type 'PICKUP'!"),
        ):
            await getattr(_make_flow(test_bot), "_set_shipping")(ad_cfg)

    @pytest.mark.asyncio
    async def test_pickup_shipping_skips_when_toggle_not_rendered(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """Categories without a shipping fieldset (e.g. books 76/77, comics 76/77/15156)
        are PICKUP-only by site convention — the absence of both shipping selectors should
        short-circuit without calling ``web_check``/``web_click``."""
        ad_cfg = Ad.model_validate(base_ad_config | {"shipping_type": "PICKUP"})

        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = [None, None]),
            patch.object(test_bot, "web_check", new_callable = AsyncMock) as mock_check,
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
        ):
            await getattr(_make_flow(test_bot), "_set_shipping")(ad_cfg)

        mock_check.assert_not_awaited()
        mock_click.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_pickup_shipping_raises_when_fieldset_rendered_but_pickup_radio_missing(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """A rendered shipping fieldset without the pickup radio should be treated as an error."""
        ad_cfg = Ad.model_validate(base_ad_config | {"shipping_type": "PICKUP"})

        with (
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = [None, MagicMock()]) as mock_probe,
            patch.object(test_bot, "web_check", new_callable = AsyncMock) as mock_check,
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            pytest.raises(
                TimeoutError,
                match = "Shipping fieldset is rendered, but the pickup radio is missing; page may not be fully loaded.",
            ),
        ):
            await getattr(_make_flow(test_bot), "_set_shipping")(ad_cfg)

        assert mock_probe.await_count == 2
        assert [call.args[:2] for call in mock_probe.await_args_list] == [
            (By.ID, "ad-shipping-enabled-no"),
            (By.ID, "ad-shipping-enabled"),
        ]
        mock_check.assert_not_awaited()
        mock_click.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_shipping_without_options_uses_radio_and_dialog(self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any]) -> None:
        """Shipping without package options should use radio + dialog flow."""
        ad_cfg = Ad.model_validate(base_ad_config | {"shipping_type": "SHIPPING", "shipping_options": [], "shipping_costs": 4.95})

        with (
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = None),
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = MagicMock()),
            patch.object(test_bot, "web_set_input_value", new_callable = AsyncMock) as mock_set_input,
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, return_value = "4,95"),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
        ):
            await getattr(_make_flow(test_bot), "_set_shipping")(ad_cfg)

            click_args = [c.args for c in mock_click.await_args_list]
            assert any(len(a) >= 2 and a[0] == By.ID and a[1] == "ad-individual-shipping-checkbox-control" for a in click_args)
            assert any("Fertig" in str(a[1]) for a in click_args if len(a) >= 2)
            mock_set_input.assert_awaited_once_with("ad-individual-shipping-price", "4,95")

    @pytest.mark.asyncio
    async def test_shipping_finish_timeout_raises(self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any]) -> None:
        """Timeout while confirming shipping dialog should raise a clear error."""
        ad_cfg = Ad.model_validate(base_ad_config | {"shipping_type": "SHIPPING", "shipping_options": []})

        async def click_side_effect(selector_type:By, selector_value:str, **_:Any) -> None:
            if selector_type == By.XPATH and "Fertig" in selector_value:
                raise TimeoutError("finish timeout")

        with (
            patch.object(test_bot, "web_click", new_callable = AsyncMock, side_effect = click_side_effect),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = None),
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = MagicMock()),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            pytest.raises(TimeoutError, match = "Unable to close shipping dialog!"),
        ):
            await getattr(_make_flow(test_bot), "_set_shipping")(ad_cfg)

    @pytest.mark.asyncio
    async def test_shipping_without_options_does_not_toggle_checkbox_when_price_input_visible(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """When price input is already visible, individual-shipping checkbox is not toggled."""
        ad_cfg = Ad.model_validate(base_ad_config | {"shipping_type": "SHIPPING", "shipping_options": [], "shipping_costs": 4.95})

        with (
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = MagicMock()),
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = MagicMock()),
            patch.object(test_bot, "web_set_input_value", new_callable = AsyncMock) as mock_set_input,
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, return_value = "4,95"),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
        ):
            await getattr(_make_flow(test_bot), "_set_shipping")(ad_cfg)

        click_args = [c.args for c in mock_click.await_args_list]
        assert not any(len(a) >= 2 and a[0] == By.ID and a[1] == "ad-individual-shipping-checkbox-control" for a in click_args)
        mock_set_input.assert_awaited_once_with("ad-individual-shipping-price", "4,95")

    @pytest.mark.asyncio
    async def test_shipping_price_lost_to_react_rerender_raises(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """When React re-render swallows the shipping price, the dialog must NOT be closed."""
        ad_cfg = Ad.model_validate(base_ad_config | {"shipping_type": "SHIPPING", "shipping_options": [], "shipping_costs": 4.95})

        async def set_input_side_effect(element_id:str, value:str) -> None:
            # Simulate web_set_input_value succeeding (no TimeoutError) but
            # the JS returning early because the element is null after re-render.
            # The real web_set_input_value does a web_find + web_execute whose JS
            # silently returns on `if(!el) return;`.  By mocking at this level we
            # model the observable effect: the call completes without error, but
            # the DOM value was never written.
            pass

        with (
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = None),
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = MagicMock()),
            patch.object(test_bot, "web_set_input_value", new_callable = AsyncMock, side_effect = set_input_side_effect),
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, return_value = None),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            pytest.raises(TimeoutError, match = "Unable to set shipping price!"),
        ):
            await getattr(_make_flow(test_bot), "_set_shipping")(ad_cfg)

        # Fertig must never be clicked when the price was not confirmed
        fertig_clicks = [c for c in mock_click.await_args_list if c.args and len(c.args) >= 2 and "Fertig" in str(c.args[1])]
        assert not fertig_clicks, "Fertig was clicked despite shipping price not being set"

    @pytest.mark.asyncio
    async def test_shipping_price_recovers_when_readback_matches_on_retry(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """First attempt mismatches (readback empty), second attempt succeeds — must close the dialog normally."""
        ad_cfg = Ad.model_validate(base_ad_config | {"shipping_type": "SHIPPING", "shipping_options": [], "shipping_costs": 4.95})

        with (
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = None),
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = MagicMock()),
            patch.object(test_bot, "web_set_input_value", new_callable = AsyncMock) as mock_set_input,
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, side_effect = [None, "4,95"]),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as mock_sleep,
        ):
            await getattr(_make_flow(test_bot), "_set_shipping")(ad_cfg)

        assert mock_set_input.await_count == 2, "expected exactly one retry after the first mismatch"
        inter_attempt_sleeps = [c for c in mock_sleep.await_args_list if c.args == (300, 500)]
        assert len(inter_attempt_sleeps) == 1, "expected one inter-attempt backoff sleep"
        fertig_clicks = [c for c in mock_click.await_args_list if c.args and len(c.args) >= 2 and "Fertig" in str(c.args[1])]
        assert fertig_clicks, "Fertig must be clicked once the readback confirms the price"

    @pytest.mark.asyncio
    async def test_shipping_price_retries_when_readback_raises_transiently(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """A TimeoutError from the readback web_execute must be retried, not propagated on the first occurrence."""
        ad_cfg = Ad.model_validate(base_ad_config | {"shipping_type": "SHIPPING", "shipping_options": [], "shipping_costs": 4.95})

        readback_results:list[str | Exception] = [TimeoutError("readback raced with re-render"), "4,95"]

        async def readback_side_effect(*_args:Any, **_kwargs:Any) -> str | None:
            result = readback_results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        with (
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = None),
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = MagicMock()),
            patch.object(test_bot, "web_set_input_value", new_callable = AsyncMock) as mock_set_input,
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, side_effect = readback_side_effect),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
        ):
            await getattr(_make_flow(test_bot), "_set_shipping")(ad_cfg)

        assert mock_set_input.await_count == 2, "expected one retry after the readback raised"
        fertig_clicks = [c for c in mock_click.await_args_list if c.args and len(c.args) >= 2 and "Fertig" in str(c.args[1])]
        assert fertig_clicks, "Fertig must be clicked once a later readback confirms the price"


class TestShippingOptionsDialog:
    """Tests for _set_shipping_options using carrier-code-based selectors."""

    @staticmethod
    def _make_ad_with_options(base_ad_config:dict[str, Any], options:list[str]) -> Ad:
        return Ad.model_validate(
            base_ad_config
            | {
                "shipping_type": "SHIPPING",
                "shipping_options": options,
            }
        )

    @staticmethod
    def _mock_checkbox(checked:bool = False) -> MagicMock:
        """Create a mock checkbox element with optional checked attribute."""
        el = MagicMock()
        if checked:
            el.attrs = {"checked": ""}
        else:
            el.attrs = {}
        return el

    @pytest.mark.parametrize(
        "case",
        [
            # SMALL pre-checked, only unwanted carriers are toggled
            {
                "options": ["Hermes_Päckchen"],
                "radio_checked": True,
                "expected_radio_click": False,
                "expected_clicked_carriers": ["HERMES_002", "DHL_001"],
                "expected_not_clicked_carriers": ["HERMES_001"],
            },
            # LARGE not checked, radio click needed and only unwanted carriers are toggled
            {
                "options": ["DHL_10"],
                "radio_checked": False,
                "expected_radio_click": True,
                "expected_clicked_carriers": ["HERMES_004", "DHL_004", "DHL_005"],
                "expected_not_clicked_carriers": ["DHL_003"],
            },
        ],
    )
    @pytest.mark.asyncio
    async def test_replace_mode_handles_radio_state(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        case:dict[str, Any],
    ) -> None:
        """REPLACE mode: handles both pre-checked and unchecked radio states."""
        ad_cfg = self._make_ad_with_options(base_ad_config, case["options"])

        radio_mock = self._mock_checkbox(checked = case["radio_checked"])

        async def find_side_effect(selector_type:By, selector_value:str, **_:Any) -> MagicMock:
            if "radio" in selector_value:
                return radio_mock
            return self._mock_checkbox(checked = True)  # all checkboxes pre-checked

        with (
            patch.object(test_bot, "web_find", new_callable = AsyncMock, side_effect = find_side_effect),
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
        ):
            await getattr(_make_flow(test_bot), "_set_shipping_options")(ad_cfg, mode = AdUpdateStrategy.REPLACE)

        click_args = [(c.args[0], c.args[1]) for c in mock_click.await_args_list if len(c.args) >= 2]

        # Radio click behavior matches expectation
        radio_clicked = any("radio" in str(a[1]) for a in click_args)
        assert radio_clicked == case["expected_radio_click"]

        # Should click Weiter and Fertig
        assert any("Weiter" in str(a[1]) for a in click_args)
        assert any("Fertig" in str(a[1]) for a in click_args)

        # Should toggle exactly the expected carriers for this scenario
        for carrier_code in case["expected_clicked_carriers"]:
            assert any(carrier_code in str(a[1]) for a in click_args)

        for carrier_code in case["expected_not_clicked_carriers"]:
            assert not any(carrier_code in str(a[1]) for a in click_args)

    @pytest.mark.asyncio
    async def test_replace_mode_dom_verified_unchecked_defaults_select_wanted_carrier(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """REPLACE mode must select wanted carriers when defaults are unchecked (DOM-verified for MEDIUM/LARGE)."""
        ad_cfg = self._make_ad_with_options(base_ad_config, ["DHL_5"])

        radio_mock = self._mock_checkbox(checked = False)  # MEDIUM radio not selected yet

        async def find_side_effect(selector_type:By, selector_value:str, **_:Any) -> MagicMock:
            if "radio" in selector_value and "MEDIUM" in selector_value:
                return radio_mock
            # DOM probe confirms MEDIUM defaults can be unchecked after "Weiter"
            if "HERMES_003" in selector_value:
                return self._mock_checkbox(checked = False)
            if "DHL_002" in selector_value:
                return self._mock_checkbox(checked = False)
            return self._mock_checkbox(checked = False)

        with (
            patch.object(test_bot, "web_find", new_callable = AsyncMock, side_effect = find_side_effect),
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
        ):
            await getattr(_make_flow(test_bot), "_set_shipping_options")(ad_cfg, mode = AdUpdateStrategy.REPLACE)

        click_args = [(c.args[0], c.args[1]) for c in mock_click.await_args_list if len(c.args) >= 2]

        # Regression guard for issue #956: wanted DHL_002 must be selected
        assert any("DHL_002" in str(a[1]) for a in click_args)
        # Unwanted Hermes checkbox must remain untouched when already unchecked
        assert not any("HERMES_003" in str(a[1]) for a in click_args)

    @pytest.mark.asyncio
    async def test_modify_mode_toggles_carriers(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """MODIFY mode: explicitly (de-)selects each carrier based on wanted set."""
        ad_cfg = self._make_ad_with_options(base_ad_config, ["Hermes_Päckchen", "DHL_2"])

        radio_mock = self._mock_checkbox(checked = True)  # SMALL already selected

        async def find_side_effect(selector_type:By, selector_value:str, **_:Any) -> MagicMock:
            if "radio" in selector_value and "SMALL" in selector_value:
                return radio_mock
            # HERMES_001 checked, HERMES_002 checked, DHL_001 unchecked
            if "HERMES_001" in selector_value:
                return self._mock_checkbox(checked = True)
            if "HERMES_002" in selector_value:
                return self._mock_checkbox(checked = True)
            if "DHL_001" in selector_value:
                return self._mock_checkbox(checked = False)
            return self._mock_checkbox(checked = False)

        with (
            patch.object(test_bot, "web_find", new_callable = AsyncMock, side_effect = find_side_effect),
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
        ):
            await getattr(_make_flow(test_bot), "_set_shipping_options")(ad_cfg, mode = AdUpdateStrategy.MODIFY)

        click_args = [(c.args[0], c.args[1]) for c in mock_click.await_args_list if len(c.args) >= 2]
        # HERMES_002 should be deselected (was checked, not wanted)
        assert any("HERMES_002" in str(a[1]) for a in click_args)
        # DHL_001 should be selected (was unchecked, wanted via DHL_2 → DHL_001)
        assert any("DHL_001" in str(a[1]) for a in click_args)
        # HERMES_001 should NOT be clicked (was checked, wanted)
        assert not any("HERMES_001" in str(a[1]) for a in click_args)

    @pytest.mark.asyncio
    async def test_unknown_option_raises_key_error(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """Unknown shipping option name raises KeyError with helpful message."""
        ad_cfg = self._make_ad_with_options(base_ad_config, ["NonExistent_Option"])

        with (
            patch.object(test_bot, "web_find", new_callable = AsyncMock) as mock_find,
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as mock_sleep,
            pytest.raises(KeyError, match = "Unknown shipping option"),
        ):
            await getattr(_make_flow(test_bot), "_set_shipping_options")(ad_cfg)

        # Validation errors must occur before any DOM interaction
        mock_find.assert_not_awaited()
        mock_click.assert_not_awaited()
        mock_sleep.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mixed_size_options_raises_value_error(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """Options from different size groups raise ValueError."""
        ad_cfg = self._make_ad_with_options(base_ad_config, ["Hermes_Päckchen", "DHL_5"])

        with (
            patch.object(test_bot, "web_find", new_callable = AsyncMock) as mock_find,
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as mock_sleep,
            pytest.raises(ValueError, match = "one package size"),
        ):
            await getattr(_make_flow(test_bot), "_set_shipping_options")(ad_cfg)

        # Validation errors must occur before any DOM interaction
        mock_find.assert_not_awaited()
        mock_click.assert_not_awaited()
        mock_sleep.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_timeout_in_dialog_raises(self, test_bot:KleinanzeigenBot, base_ad_config:dict[str, Any]) -> None:
        """TimeoutError during dialog interaction is re-raised with descriptive message."""
        ad_cfg = self._make_ad_with_options(base_ad_config, ["Hermes_Päckchen"])

        with (
            patch.object(test_bot, "web_find", new_callable = AsyncMock, side_effect = TimeoutError("radio not found")),
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            pytest.raises(TimeoutError, match = "Failed to configure shipping options in dialog!"),
        ):
            await getattr(_make_flow(test_bot), "_set_shipping_options")(ad_cfg)


class TestImageUploadProcessedMarkerFallback:
    """Regression tests for image upload completion detection via hidden marker inputs."""

    @staticmethod
    def _build_two_image_ad(base_ad_config:dict[str, Any], tmp_path:Path) -> tuple[Ad, str, str]:
        image_a = tmp_path / "img_a.jpg"
        image_b = tmp_path / "img_b.jpg"
        image_a.write_bytes(b"")
        image_b.write_bytes(b"")
        ad_cfg = Ad.model_validate(base_ad_config | {"images": [str(image_a), str(image_b)]})
        return ad_cfg, str(image_a), str(image_b)

    @staticmethod
    def _build_marker(url:str) -> MagicMock:
        marker = MagicMock()
        marker.attrs.value = url
        return marker

    @staticmethod
    @contextmanager
    def _mock_upload_dependencies(
        test_bot:KleinanzeigenBot,
        file_input:MagicMock,
        find_all_side_effect:Callable[..., Awaitable[list[MagicMock]]],
        await_side_effect:Callable[..., Awaitable[Any]],
    ) -> Iterator[None]:
        async def find_all_once_side_effect(selector_type:By, selector_value:str, *_:Any, **__:Any) -> list[MagicMock]:
            return await find_all_side_effect(selector_type, selector_value, **__)

        with (
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = file_input),
            patch.object(test_bot, "web_find_all_once", new_callable = AsyncMock, side_effect = find_all_once_side_effect),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = await_side_effect),
        ):
            yield

    @pytest.mark.asyncio
    async def test_upload_images_succeeds_with_hidden_markers_when_thumbnails_absent(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        tmp_path:Path,
    ) -> None:
        """Hidden adImages markers should satisfy completion when thumbnail list is missing."""
        ad_cfg, image_a, image_b = self._build_two_image_ad(base_ad_config, tmp_path)

        file_input = MagicMock()
        file_input.send_file = AsyncMock()

        marker_a = self._build_marker("https://img.example/a.jpg")
        marker_b = self._build_marker("https://img.example/b.jpg")
        marker_query_count = 0

        async def find_all_side_effect(selector_type:By, selector_value:str, *_:Any, **__:Any) -> list[MagicMock]:
            nonlocal marker_query_count
            if selector_type == By.CSS_SELECTOR and selector_value == "input[name^='adImages'][name$='.url']":
                marker_query_count += 1
                if marker_query_count == 1:
                    return []  # baseline before upload
                return [marker_a, marker_b]
            return []

        async def await_side_effect(condition:Callable[[], Awaitable[bool]], **_:Any) -> bool:
            if await condition():
                return True
            raise TimeoutError("condition did not pass")

        with self._mock_upload_dependencies(test_bot, file_input, find_all_side_effect, await_side_effect):
            await getattr(_make_flow(test_bot), "_upload_images")(ad_cfg)

        file_input.send_file.assert_any_await(image_a)
        file_input.send_file.assert_any_await(image_b)

    @pytest.mark.asyncio
    async def test_upload_images_refetches_file_input_per_image_to_avoid_stale_element(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        tmp_path:Path,
    ) -> None:
        """Each image upload should re-fetch the file input because the DOM replaces it after selection."""
        ad_cfg, image_a, image_b = self._build_two_image_ad(base_ad_config, tmp_path)

        first_file_input = MagicMock()
        first_file_input.send_file = AsyncMock()
        second_file_input = MagicMock()
        second_file_input.send_file = AsyncMock()

        marker_a = self._build_marker("https://img.example/a.jpg")
        marker_b = self._build_marker("https://img.example/b.jpg")
        marker_query_count = 0

        async def find_side_effect(selector_type:By, selector_value:str, **_:Any) -> MagicMock:
            assert selector_type == By.CSS_SELECTOR
            assert selector_value == "input[type=file]"
            if first_file_input.send_file.await_count == 0:
                return first_file_input
            return second_file_input

        async def find_all_side_effect(selector_type:By, selector_value:str, *_:Any, **__:Any) -> list[MagicMock]:
            nonlocal marker_query_count
            if selector_type == By.CSS_SELECTOR and selector_value == "input[name^='adImages'][name$='.url']":
                marker_query_count += 1
                if marker_query_count == 1:
                    return []  # baseline before upload
                return [marker_a, marker_b]
            return []

        async def await_side_effect(condition:Callable[[], Awaitable[bool]], **_:Any) -> bool:
            if await condition():
                return True
            raise TimeoutError("condition did not pass")

        with (
            patch.object(test_bot, "web_find", new_callable = AsyncMock, side_effect = find_side_effect) as mock_find,
            patch.object(test_bot, "web_find_all_once", new_callable = AsyncMock, side_effect = find_all_side_effect),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = await_side_effect),
        ):
            await getattr(_make_flow(test_bot), "_upload_images")(ad_cfg)

        first_file_input.send_file.assert_awaited_once_with(image_a)
        second_file_input.send_file.assert_awaited_once_with(image_b)
        assert mock_find.await_count >= 2

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("baseline_count", "post_count", "expected_found"),
        [
            pytest.param(2, 2, 0, id = "stale-only-markers"),
            pytest.param(0, 1, 1, id = "one-new-marker"),
        ],
    )
    async def test_upload_images_timeout_reports_processed_count(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        tmp_path:Path,
        baseline_count:int,
        post_count:int,
        expected_found:int,
    ) -> None:
        """Upload timeout should report the correct processed-marker count based on baseline vs post-upload markers."""
        ad_cfg, image_a, image_b = self._build_two_image_ad(base_ad_config, tmp_path)

        file_input = MagicMock()
        file_input.send_file = AsyncMock()

        marker_query_count = 0

        async def find_all_side_effect(selector_type:By, selector_value:str, **_:Any) -> list[MagicMock]:
            nonlocal marker_query_count
            if selector_type == By.CSS_SELECTOR and selector_value == "input[name^='adImages'][name$='.url']":
                marker_query_count += 1
                if marker_query_count == 1:
                    return [self._build_marker(f"https://img.example/baseline-{i}.jpg") for i in range(baseline_count)]
                return [self._build_marker(f"https://img.example/post-{i}.jpg") for i in range(post_count)]
            return []

        async def await_timeout(*_:Any, **__:Any) -> None:
            raise TimeoutError("Image upload timeout exceeded")

        with (
            pytest.raises(TimeoutError, match = rf"Expected 2, found {expected_found} processed"),
            self._mock_upload_dependencies(test_bot, file_input, find_all_side_effect, await_timeout),
        ):
            await getattr(_make_flow(test_bot), "_upload_images")(ad_cfg)

        file_input.send_file.assert_any_await(image_a)
        file_input.send_file.assert_any_await(image_b)

    @pytest.mark.asyncio
    async def test_upload_images_succeeds_when_new_markers_exceed_baseline(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        tmp_path:Path,
    ) -> None:
        """Only marker delta beyond baseline should satisfy completion when thumbnails are absent."""
        ad_cfg, image_a, image_b = self._build_two_image_ad(base_ad_config, tmp_path)

        file_input = MagicMock()
        file_input.send_file = AsyncMock()

        stale_marker = self._build_marker("https://img.example/stale.jpg")
        marker_a = self._build_marker("https://img.example/a.jpg")
        marker_b = self._build_marker("https://img.example/b.jpg")
        marker_query_count = 0

        async def find_all_side_effect(selector_type:By, selector_value:str, **_:Any) -> list[MagicMock]:
            nonlocal marker_query_count
            if selector_type == By.CSS_SELECTOR and selector_value == "input[name^='adImages'][name$='.url']":
                marker_query_count += 1
                if marker_query_count == 1:
                    return [stale_marker]  # baseline before upload
                return [stale_marker, marker_a, marker_b]  # 2 new markers beyond baseline
            return []

        async def await_side_effect(condition:Callable[[], Awaitable[bool]], **_:Any) -> bool:
            if await condition():
                return True
            raise TimeoutError("condition did not pass")

        with self._mock_upload_dependencies(test_bot, file_input, find_all_side_effect, await_side_effect):
            await getattr(_make_flow(test_bot), "_upload_images")(ad_cfg)

        file_input.send_file.assert_any_await(image_a)
        file_input.send_file.assert_any_await(image_b)

    @pytest.mark.asyncio
    async def test_upload_images_baseline_capture_timeout_defaults_to_zero(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        tmp_path:Path,
    ) -> None:
        """If baseline marker lookup times out, marker fallback should still work with baseline=0."""
        ad_cfg, image_a, image_b = self._build_two_image_ad(base_ad_config, tmp_path)

        file_input = MagicMock()
        file_input.send_file = AsyncMock()

        marker_a = self._build_marker("https://img.example/a.jpg")
        marker_b = self._build_marker("https://img.example/b.jpg")
        marker_query_count = 0

        async def find_all_side_effect(selector_type:By, selector_value:str, **_:Any) -> list[MagicMock]:
            nonlocal marker_query_count
            if selector_type == By.CSS_SELECTOR and selector_value == "input[name^='adImages'][name$='.url']":
                marker_query_count += 1
                if marker_query_count == 1:
                    raise TimeoutError("baseline markers unavailable")
                return [marker_a, marker_b]
            return []

        async def await_side_effect(condition:Callable[[], Awaitable[bool]], **_:Any) -> bool:
            if await condition():
                return True
            raise TimeoutError("condition did not pass")

        with self._mock_upload_dependencies(test_bot, file_input, find_all_side_effect, await_side_effect):
            await getattr(_make_flow(test_bot), "_upload_images")(ad_cfg)

        file_input.send_file.assert_any_await(image_a)
        file_input.send_file.assert_any_await(image_b)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("baseline_count", "post_count"),
        [
            pytest.param(0, 2, id = "no_baseline"),
            pytest.param(1, 3, id = "one_stale_plus_two_new"),
            pytest.param(2, 4, id = "two_stale_plus_two_new"),
        ],
    )
    async def test_upload_images_marker_delta_determines_completion(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        tmp_path:Path,
        baseline_count:int,
        post_count:int,
    ) -> None:
        """Completion should succeed when marker delta reaches expected count."""
        ad_cfg, image_a, image_b = self._build_two_image_ad(base_ad_config, tmp_path)

        file_input = MagicMock()
        file_input.send_file = AsyncMock()

        marker_query_count = 0

        async def find_all_side_effect(selector_type:By, selector_value:str, **_:Any) -> list[MagicMock]:
            nonlocal marker_query_count
            if selector_type == By.CSS_SELECTOR and selector_value == "input[name^='adImages'][name$='.url']":
                marker_query_count += 1
                if marker_query_count == 1:
                    return [self._build_marker(f"https://img.example/stale-{i}.jpg") for i in range(baseline_count)]
                return [self._build_marker(f"https://img.example/post-{i}.jpg") for i in range(post_count)]
            return []

        async def await_side_effect(condition:Callable[[], Awaitable[bool]], **_:Any) -> bool:
            if await condition():
                return True
            raise TimeoutError("condition did not pass")

        with self._mock_upload_dependencies(test_bot, file_input, find_all_side_effect, await_side_effect):
            await getattr(_make_flow(test_bot), "_upload_images")(ad_cfg)

        file_input.send_file.assert_any_await(image_a)
        file_input.send_file.assert_any_await(image_b)


class TestSpecialAttributesHandler:
    """Tests for _set_special_attributes dispatch logic in PublishingFormFlow."""

    @pytest.mark.asyncio
    async def test_special_attributes_compound_name_lookup(self, test_bot:"KleinanzeigenBot", base_ad_config:dict[str, Any]) -> None:
        """Compound special-attribute names should be matched via original key in @name."""
        ad_cfg = Ad.model_validate(
            base_ad_config
            | {
                "special_attributes": {"autos.model_s": "a3"},
                "updated_on": "2024-01-01T00:00:00",
                "created_on": "2024-01-01T00:00:00",
            }
        )

        model_elem = MagicMock()
        model_attrs = MagicMock()
        model_attrs.id = None
        model_attrs.name = "attributeMap[autos.marke_s+autos.model_s]"
        model_attrs.get.side_effect = lambda key, default = None: {
            "id": None,
            "name": "attributeMap[autos.marke_s+autos.model_s]",
            "type": None,
            "role": None,
        }.get(key, default)
        model_elem.attrs = model_attrs
        model_elem.local_name = "select"

        with (
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock) as mock_find_all,
            patch.object(test_bot, "web_select", new_callable = AsyncMock) as mock_select,
        ):

            async def find_all_side_effect(selector_type:By, selector_value:str, **_:Any) -> list[Element]:
                if selector_type == By.XPATH and "autos.model_s" in selector_value:
                    return [model_elem]
                return []

            mock_find_all.side_effect = find_all_side_effect

            await getattr(_make_flow(test_bot), "_set_special_attributes")(ad_cfg)

            assert mock_select.await_count == 1
            assert mock_select.await_args is not None
            assert mock_select.await_args.args[0] == By.XPATH
            assert "contains(@name, 'autos.model_s')" in str(mock_select.await_args.args[1])
            assert mock_select.await_args.args[2] == "a3"

    @pytest.mark.asyncio
    async def test_special_attributes_prefers_button_combobox_over_hidden_input(
        self,
        test_bot:"KleinanzeigenBot",
        base_ad_config:dict[str, Any],
    ) -> None:
        """Hidden backing inputs must not win over visible button combobox controls."""
        ad_cfg = Ad.model_validate(
            base_ad_config
            | {
                "special_attributes": {"color_s": "beige"},
                "updated_on": "2024-01-01T00:00:00",
                "created_on": "2024-01-01T00:00:00",
            }
        )

        hidden_elem = MagicMock()
        hidden_attrs = MagicMock()
        hidden_attrs.id = None
        hidden_attrs.type = "hidden"
        hidden_attrs.get.side_effect = lambda key, default = None: {
            "id": None,
            "name": "attributeMap[kleidung_herren.color]",
            "type": "hidden",
            "role": None,
        }.get(key, default)
        hidden_elem.attrs = hidden_attrs
        hidden_elem.local_name = "input"

        button_elem = MagicMock()
        button_attrs = MagicMock()
        button_attrs.id = "kleidung_herren.color"
        button_attrs.type = "button"
        button_attrs.get.side_effect = lambda key, default = None: {
            "id": "kleidung_herren.color",
            "name": None,
            "type": "button",
            "role": "combobox",
        }.get(key, default)
        button_elem.attrs = button_attrs
        button_elem.local_name = "button"

        with (
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, return_value = [hidden_elem, button_elem]),
            patch.object(_publishing_flow.PublishingFormFlow, "_select_button_combobox", new_callable = AsyncMock) as mock_button_combobox,
            patch.object(test_bot, "web_input", new_callable = AsyncMock) as mock_input,
        ):
            await getattr(_make_flow(test_bot), "_set_special_attributes")(ad_cfg)

        mock_button_combobox.assert_awaited_once_with("kleidung_herren.color", "beige")
        mock_input.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("combobox_type", ["text", None], ids = ["type-text", "type-absent"])
    async def test_special_attributes_combobox_routed_over_hidden_input(
        self,
        test_bot:"KleinanzeigenBot",
        base_ad_config:dict[str, Any],
        combobox_type:str | None,
    ) -> None:
        """Combobox <input> must be routed to web_select_combobox regardless of type attribute presence."""
        ad_cfg = Ad.model_validate(
            base_ad_config
            | {
                "special_attributes": {"brand_s": "armani"},
                "updated_on": "2024-01-01T00:00:00",
                "created_on": "2024-01-01T00:00:00",
            }
        )

        hidden_elem = MagicMock()
        hidden_attrs = MagicMock()
        hidden_attrs.id = None
        hidden_attrs.type = "hidden"
        hidden_attrs.get.side_effect = lambda key, default = None: {
            "id": None,
            "name": "attributeMap[kleidung_herren.brand]",
            "type": "hidden",
            "role": None,
        }.get(key, default)
        hidden_elem.attrs = hidden_attrs
        hidden_elem.local_name = "input"

        combobox_elem = MagicMock()
        combobox_attrs = MagicMock()
        combobox_attrs.id = "kleidung_herren.brand"
        combobox_attrs.type = combobox_type
        combobox_attrs.get.side_effect = lambda key, default = None: {
            "id": "kleidung_herren.brand",
            "name": None,
            "type": combobox_type,
            "role": "combobox",
        }.get(key, default)
        combobox_elem.attrs = combobox_attrs
        combobox_elem.local_name = "input"

        with (
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, return_value = [hidden_elem, combobox_elem]),
            patch.object(test_bot, "web_select_combobox", new_callable = AsyncMock) as mock_select_combobox,
            patch.object(test_bot, "web_input", new_callable = AsyncMock) as mock_input,
        ):
            await getattr(_make_flow(test_bot), "_set_special_attributes")(ad_cfg)

        mock_select_combobox.assert_awaited_once_with(By.ID, "kleidung_herren.brand", "armani")
        mock_input.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("checked_attr", "attribute_value", "expect_click"),
        [(None, "true", True), ("checked", "true", False), ("checked", "false", True)],
    )
    async def test_special_attributes_checkbox_clicks_only_on_state_change(
        self,
        test_bot:"KleinanzeigenBot",
        base_ad_config:dict[str, Any],
        checked_attr:str | None,
        attribute_value:str,
        expect_click:bool,
    ) -> None:
        """Checkbox attributes should only click when current and desired states differ."""
        ad_cfg = Ad.model_validate(
            base_ad_config
            | {
                "special_attributes": {"feature_b": attribute_value},
                "updated_on": "2024-01-01T00:00:00",
                "created_on": "2024-01-01T00:00:00",
            }
        )

        checkbox_elem = MagicMock()
        checkbox_attrs = MagicMock()
        checkbox_attrs.id = "feature"
        checkbox_attrs.type = "checkbox"
        checkbox_attrs.get.side_effect = lambda key, default = None: {
            "id": "feature",
            "name": "attributeMap[feature]",
            "type": "checkbox",
            "role": None,
            "checked": checked_attr,
        }.get(key, default)
        checkbox_elem.attrs = checkbox_attrs
        checkbox_elem.local_name = "input"

        with (
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, return_value = [checkbox_elem]),
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
        ):
            await getattr(_make_flow(test_bot), "_set_special_attributes")(ad_cfg)

        if expect_click:
            mock_click.assert_awaited_once_with(By.ID, "feature")
        else:
            mock_click.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_hidden_input_fallback_finds_associated_button_combobox(
        self,
        test_bot:"KleinanzeigenBot",
        base_ad_config:dict[str, Any],
    ) -> None:
        """When XPath matches only a hidden backing input (dynamic React IDs),
        the fallback should locate the associated <button role="combobox"> and use
        _select_button_combobox.  Regression test for issue #1096."""
        ad_cfg = Ad.model_validate(
            base_ad_config
            | {
                "special_attributes": {"groesse_s": "68"},
                "updated_on": "2024-01-01T00:00:00",
                "created_on": "2024-01-01T00:00:00",
            }
        )

        hidden_elem = MagicMock()
        hidden_attrs = MagicMock()
        hidden_attrs.id = None
        hidden_attrs.type = "hidden"
        hidden_attrs.get.side_effect = lambda key, default = None: {
            "id": None,
            "name": "attributeMap[groesse]",
            "type": "hidden",
            "role": None,
        }.get(key, default)
        hidden_elem.attrs = hidden_attrs
        hidden_elem.local_name = "input"

        with (
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, return_value = [hidden_elem]),
            patch.object(
                _publishing_flow.PublishingFormFlow,
                "_find_associated_button_combobox",
                new_callable = AsyncMock,
                return_value = ":r8r7:",
            ) as mock_find_button,
            patch.object(
                _publishing_flow.PublishingFormFlow,
                "_select_button_combobox",
                new_callable = AsyncMock,
            ) as mock_select_combobox,
            patch.object(test_bot, "web_input", new_callable = AsyncMock) as mock_input,
        ):
            await getattr(_make_flow(test_bot), "_set_special_attributes")(ad_cfg)

        mock_find_button.assert_awaited_once_with(hidden_input_name = "attributeMap[groesse]")
        mock_select_combobox.assert_awaited_once_with(":r8r7:", "68")
        mock_input.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_hidden_input_fallback_no_associated_button_raises(
        self,
        test_bot:"KleinanzeigenBot",
        base_ad_config:dict[str, Any],
    ) -> None:
        """When the fallback cannot find an associated button combobox, a
        TimeoutError is raised instead of falling through to the text-input handler."""
        ad_cfg = Ad.model_validate(
            base_ad_config
            | {
                "special_attributes": {"color_s": "beige"},
                "updated_on": "2024-01-01T00:00:00",
                "created_on": "2024-01-01T00:00:00",
            }
        )

        hidden_elem = MagicMock()
        hidden_attrs = MagicMock()
        hidden_attrs.id = None
        hidden_attrs.type = "hidden"
        hidden_attrs.get.side_effect = lambda key, default = None: {
            "id": None,
            "name": "attributeMap[color]",
            "type": "hidden",
            "role": None,
        }.get(key, default)
        hidden_elem.attrs = hidden_attrs
        hidden_elem.local_name = "input"

        with (
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, return_value = [hidden_elem]),
            patch.object(
                _publishing_flow.PublishingFormFlow,
                "_find_associated_button_combobox",
                new_callable = AsyncMock,
                return_value = None,
            ) as mock_find_button,
            pytest.raises(TimeoutError),
        ):
            await getattr(_make_flow(test_bot), "_set_special_attributes")(ad_cfg)

        mock_find_button.assert_awaited_once_with(hidden_input_name = "attributeMap[color]")

    @pytest.mark.asyncio
    async def test_fallback_not_triggered_when_button_combobox_directly_matched(
        self,
        test_bot:"KleinanzeigenBot",
        base_ad_config:dict[str, Any],
    ) -> None:
        """When the XPath directly matches a <button role="combobox"> (not just
        a hidden input), the fallback search should NOT be triggered — the normal
        dispatch path handles it.  This guards against unnecessary JS execution."""
        ad_cfg = Ad.model_validate(
            base_ad_config
            | {
                "special_attributes": {"type_s": "accessoires"},
                "updated_on": "2024-01-01T00:00:00",
                "created_on": "2024-01-01T00:00:00",
            }
        )

        button_elem = MagicMock()
        button_attrs = MagicMock()
        button_attrs.id = "kleidung_herren.type"
        button_attrs.type = "button"
        button_attrs.get.side_effect = lambda key, default = None: {
            "id": "kleidung_herren.type",
            "name": None,
            "type": "button",
            "role": "combobox",
        }.get(key, default)
        button_elem.attrs = button_attrs
        button_elem.local_name = "button"

        with (
            patch.object(test_bot, "web_find_all", new_callable = AsyncMock, return_value = [button_elem]),
            patch.object(
                _publishing_flow.PublishingFormFlow,
                "_find_associated_button_combobox",
                new_callable = AsyncMock,
            ) as mock_find_button,
            patch.object(
                _publishing_flow.PublishingFormFlow,
                "_select_button_combobox",
                new_callable = AsyncMock,
            ) as mock_select_combobox,
        ):
            await getattr(_make_flow(test_bot), "_set_special_attributes")(ad_cfg)

        mock_find_button.assert_not_awaited()
        mock_select_combobox.assert_awaited_once_with("kleidung_herren.type", "accessoires")


class TestFillAdFormSellDirectly:
    """Tests for the sell_directly section of PublishingFormFlow.fill_ad_form."""

    @pytest.mark.asyncio
    async def test_sell_directly_shipping_absent_buy_now_true_logs_warning(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
        caplog:pytest.LogCaptureFixture,
    ) -> None:
        """When sell_directly is True with SHIPPING and ad-buy-now-true is absent: warn and skip."""
        ad_cfg = Ad.model_validate(base_ad_config | {"sell_directly": True, "shipping_type": "SHIPPING"})
        caplog.set_level(logging.WARNING)
        flow = _make_flow(test_bot)

        with (
            patch.object(flow, "_set_category", new_callable = AsyncMock),
            patch.object(flow, "_set_special_attributes", new_callable = AsyncMock),
            patch.object(flow, "_set_shipping", new_callable = AsyncMock),
            patch.object(flow, "_set_contact_fields", new_callable = AsyncMock),
            patch.object(flow, "_upload_images", new_callable = AsyncMock),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            patch.object(test_bot, "web_set_input_value", new_callable = AsyncMock),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = None) as mock_probe,
            patch.object(test_bot, "web_find_all_once", new_callable = AsyncMock, return_value = []),
        ):
            await flow.fill_ad_form("test.yaml", ad_cfg, AdUpdateStrategy.REPLACE)

        assert mock_probe.await_count == 1
        assert mock_probe.await_args is not None
        assert mock_probe.await_args.args[:2] == (By.ID, "ad-buy-now-true")
        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("Direct-buy (sell_directly) is not available" in msg for msg in warning_messages)
        assert not any(
            len(c.args) >= 2 and c.args[1] == "ad-buy-now-true"
            for c in mock_click.await_args_list
        )

    @pytest.mark.asyncio
    async def test_sell_directly_pickup_opts_out_via_buy_now_false(
        self,
        test_bot:KleinanzeigenBot,
        base_ad_config:dict[str, Any],
    ) -> None:
        """With PICKUP shipping, opt out via ad-buy-now-false when present and not selected."""
        ad_cfg = Ad.model_validate(base_ad_config | {"sell_directly": True, "shipping_type": "PICKUP"})
        flow = _make_flow(test_bot)

        with (
            patch.object(flow, "_set_category", new_callable = AsyncMock),
            patch.object(flow, "_set_special_attributes", new_callable = AsyncMock),
            patch.object(flow, "_set_shipping", new_callable = AsyncMock),
            patch.object(flow, "_set_contact_fields", new_callable = AsyncMock),
            patch.object(flow, "_upload_images", new_callable = AsyncMock),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            patch.object(test_bot, "web_set_input_value", new_callable = AsyncMock),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = MagicMock()) as mock_probe,
            patch.object(test_bot, "web_check", new_callable = AsyncMock, return_value = False) as mock_check,
            patch.object(test_bot, "web_find_all_once", new_callable = AsyncMock, return_value = []),
        ):
            await flow.fill_ad_form("test.yaml", ad_cfg, AdUpdateStrategy.REPLACE)

        assert mock_probe.await_count == 1
        assert mock_probe.await_args is not None
        assert mock_probe.await_args.args[:2] == (By.ID, "ad-buy-now-false")
        assert mock_check.await_count == 1
        assert mock_check.await_args is not None
        assert mock_check.await_args.args[:2] == (By.ID, "ad-buy-now-false")
        assert any(
            len(c.args) >= 2 and c.args[1] == "ad-buy-now-false"
            for c in mock_click.await_args_list
        )
