# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Tests for publishing submission functionality."""

from unittest.mock import AsyncMock, patch

import pytest

from kleinanzeigen_bot import KleinanzeigenBot, publishing_submission
from kleinanzeigen_bot.model.ad_model import Ad, AdUpdateStrategy
from kleinanzeigen_bot.utils.exceptions import PublishSubmissionUncertainError
from kleinanzeigen_bot.utils.web_scraping_mixin import By


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


class TestTrackingFallback:
    """Tests for _try_recover_ad_id_from_redirect helper method."""

    @pytest.mark.asyncio
    async def test_extract_ad_id_from_referrer(self, test_bot:KleinanzeigenBot) -> None:
        """Ad ID should be extracted from document.referrer containing the confirmation URL."""
        referrer_url = "https://www.kleinanzeigen.de/p-anzeige-aufgeben-bestaetigung.html?adId=3382410263"
        with patch.object(test_bot, "web_execute", new_callable = AsyncMock, return_value = referrer_url):
            result = await publishing_submission._try_recover_ad_id_from_redirect(test_bot)

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
            result = await publishing_submission._try_recover_ad_id_from_redirect(test_bot)

        assert result == 44556677

    @pytest.mark.asyncio
    async def test_extract_ad_id_returns_none_when_not_found(self, test_bot:KleinanzeigenBot) -> None:
        """When neither referrer nor scripts contain a confirmation URL, None should be returned."""
        execute_returns = [
            "https://www.kleinanzeigen.de/m-meine-anzeigen.html",  # referrer
            "var x = 42;",  # script content — no confirmation URL
        ]

        with patch.object(test_bot, "web_execute", new_callable = AsyncMock, side_effect = execute_returns):
            result = await publishing_submission._try_recover_ad_id_from_redirect(test_bot)

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
            result = await publishing_submission._try_recover_ad_id_from_redirect(test_bot)

        assert result == 11223344

    @pytest.mark.asyncio
    async def test_referrer_lookup_fails_gracefully_with_timeout(self, test_bot:KleinanzeigenBot) -> None:
        """When document.referrer lookup raises TimeoutError, script scan is tried as fallback."""
        script_content = 'initTrackingData("p-anzeige-aufgeben-bestaetigung.html?adId=55556666")'
        execute_returns:list[object] = [TimeoutError("timed out"), script_content]

        with patch.object(test_bot, "web_execute", new_callable = AsyncMock, side_effect = execute_returns):
            result = await publishing_submission._try_recover_ad_id_from_redirect(test_bot)

        assert result == 55556666

    @pytest.mark.asyncio
    async def test_script_scan_fails_gracefully(self, test_bot:KleinanzeigenBot) -> None:
        """When script content scan raises TimeoutError, None is returned."""
        referrer = "https://www.kleinanzeigen.de/m-meine-anzeigen.html"
        execute_returns:list[object] = [referrer, TimeoutError("timed out")]

        with patch.object(test_bot, "web_execute", new_callable = AsyncMock, side_effect = execute_returns):
            result = await publishing_submission._try_recover_ad_id_from_redirect(test_bot)

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
            patch("kleinanzeigen_bot.publishing_submission.ainput", new_callable = AsyncMock),
        ):
            result = await publishing_submission.submit_and_confirm_ad(
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
            patch("kleinanzeigen_bot.publishing_submission.ainput", new_callable = AsyncMock),
        ):
            result = await publishing_submission.submit_and_confirm_ad(
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
            patch("kleinanzeigen_bot.publishing_submission.ainput", new_callable = AsyncMock),
        ):
            result = await publishing_submission.submit_and_confirm_ad(
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
            patch("kleinanzeigen_bot.publishing_submission.ainput", new_callable = AsyncMock) as mock_ainput,
        ):
            result = await publishing_submission.submit_and_confirm_ad(
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
            patch("kleinanzeigen_bot.publishing_submission._try_recover_ad_id_from_redirect", new_callable = AsyncMock, return_value = 99999),
            patch("kleinanzeigen_bot.publishing_submission.ainput", new_callable = AsyncMock),
        ):
            result = await publishing_submission.submit_and_confirm_ad(
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
            patch("kleinanzeigen_bot.publishing_submission._try_recover_ad_id_from_redirect", new_callable = AsyncMock, return_value = None),
            patch("kleinanzeigen_bot.publishing_submission.ainput", new_callable = AsyncMock),
            pytest.raises(PublishSubmissionUncertainError),
        ):
            await publishing_submission.submit_and_confirm_ad(
                test_bot, "test.yaml", ad, AdUpdateStrategy.REPLACE,
                captcha_config = captcha_config,
            )
