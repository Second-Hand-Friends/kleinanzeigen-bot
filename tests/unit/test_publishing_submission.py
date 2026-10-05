# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Tests for publishing submission functionality."""

import json
import sys
from collections.abc import Awaitable, Callable
from typing import Any, Literal, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nodejs_wheel import node
from nodriver.core.connection import ProtocolException

from kleinanzeigen_bot import publishing_submission
from kleinanzeigen_bot.app import KleinanzeigenBot
from kleinanzeigen_bot.model.ad_model import Ad, AdUpdateStrategy
from kleinanzeigen_bot.published_ads import PublishedAdsFetchIncompleteError
from kleinanzeigen_bot.utils.exceptions import AdFormValidationError, ManualPackageSelectionRequiredError, PublishSubmissionUncertainError
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


def _confirmation_execute(url:str) -> Callable[[str], Awaitable[Any]]:
    """Return a successful final click and the requested confirmation URL."""
    async def execute(script:str) -> Any:
        """Simulate submit discovery, the final click, and publication URL reads."""
        if "const action =" in script and "const labels =" in script:
            return "submit" if 'const action = "inspect"' in script else True
        if "window.location.href" in script:
            return url
        if "document.referrer" in script:
            return ""
        return None
    return execute


def _idless_success_execute(root_url:str) -> Callable[[str], Awaitable[Any]]:
    """Return browser-script behavior for the redesigned ID-less success page."""

    async def execute(script:str) -> Any:
        """Async side effect for mocking web_execute calls."""
        if "document.referrer" in script:
            return ""
        # _click_submit_button injects JS that locates a <button> by label
        # text and clicks it, returning True/False. The helper must report a
        # successful click so the submit step advances.
        if "querySelectorAll('button')" in script and ".textContent.trim().includes" in script:
            return True
        if "Geschafft!" in script:
            return True
        if "window.location.href" in script:
            return f"{root_url}/done"
        return None

    return execute


class TestFormValidation:
    """Explicit rejection must terminate confirmation without ID recovery."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("empty_checks", [0, 1, 2], ids = ["immediate", "first-poll", "later-poll"])
    async def test_reports_rejection_without_uncertain_submission_recovery(
        self, test_bot:KleinanzeigenBot, empty_checks:int,
    ) -> None:
        # Use the real polling loop: it retries predicate exceptions, so rejection
        # must be carried out of the predicate instead of raised inside it.
        test_bot.browser = MagicMock(update_targets = AsyncMock())
        errors = ["Art: Bitte gib einen Wert ein.", "Marke: Bitte gib einen Wert ein."]
        with (
            patch("kleinanzeigen_bot.captcha_flow.check_and_wait_for_captcha", new_callable = AsyncMock),
            patch.object(test_bot, "web_set_input_value", new_callable = AsyncMock),
            patch.object(test_bot, "timeout", return_value = 3.0),
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, return_value = "https://example.invalid/edit"),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, return_value = None),
            patch("kleinanzeigen_bot.publishing_submission._click_submit_button", new_callable = AsyncMock) as submit,
            patch("kleinanzeigen_bot.publishing_submission._is_idless_publish_success_page", new_callable = AsyncMock, return_value = False),
            patch(
                "kleinanzeigen_bot.publishing_submission._get_form_validation_errors",
                new_callable = AsyncMock, side_effect = [[] for _ in range(empty_checks)] + [errors],
            ) as validation,
            patch("kleinanzeigen_bot.publishing_submission._try_recover_ad_id_from_redirect", new_callable = AsyncMock) as tracking,
            patch("kleinanzeigen_bot.publishing_submission._try_recover_ad_id_from_published_ads", new_callable = AsyncMock) as published,
            pytest.raises(AdFormValidationError) as exc_info,
        ):
            await publishing_submission.submit_and_confirm_ad(
                test_bot, "test.yaml", _make_min_ad(), AdUpdateStrategy.REPLACE,
                captcha_config = test_bot.config.captcha, root_url = test_bot.root_url,
            )

        assert str(exc_info.value) == "Ad form validation failed: " + "; ".join(errors)
        assert validation.await_count == empty_checks + 1
        submit.assert_awaited_once()
        tracking.assert_not_awaited()
        published.assert_not_awaited()


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


class TestClickSubmitButton:
    """Tests for the _click_submit_button helper."""

    @pytest.mark.asyncio
    async def test_raises_timeout_when_no_submit_button_found(self, test_bot:KleinanzeigenBot) -> None:
        """When web_execute returns False for all labels, TimeoutError is raised."""

        async def await_side_effect(condition:Any, **__:Any) -> Any:
            """Simulate web_await: call condition, raise TimeoutError if falsy."""
            result = await condition()
            if not result:
                raise TimeoutError("Could not find submit button")

        with (
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, return_value = False),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = await_side_effect),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            pytest.raises(TimeoutError, match = "Could not find submit button"),
        ):
            await publishing_submission._click_submit_button(test_bot)


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
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, side_effect = _confirmation_execute(confirmation_url)),
            patch.object(test_bot, "web_scroll_page_down", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.publishing_submission.ainput", new_callable = AsyncMock),
        ):
            result = await publishing_submission.submit_and_confirm_ad(
                test_bot, "test.yaml", ad, AdUpdateStrategy.REPLACE,
                captcha_config = captcha_config,
                root_url = test_bot.root_url,
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
        dismiss_btn = AsyncMock()
        confirmation_url = "https://www.kleinanzeigen.de/p-anzeige-aufgeben-bestaetigung.html?adId=12345"

        with (
            patch("kleinanzeigen_bot.captcha_flow.check_and_wait_for_captcha", new_callable = AsyncMock),
            patch.object(test_bot, "web_set_input_value", new_callable = AsyncMock),
            patch.object(test_bot, "web_click", new_callable = AsyncMock) as mock_click,
            # First probe detects the upsell dialog (By.TEXT); remaining probes
            # (imprint, no-image hint, payment form) return None.
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = [upsell_element, None, None, None]),
            patch.object(test_bot, "web_find", new_callable = AsyncMock, return_value = dismiss_btn) as mock_find,
            patch.object(test_bot, "web_await", new_callable = AsyncMock),
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, side_effect = _confirmation_execute(confirmation_url)),
            patch.object(test_bot, "web_scroll_page_down", new_callable = AsyncMock),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.publishing_submission.ainput", new_callable = AsyncMock),
        ):
            result = await publishing_submission.submit_and_confirm_ad(
                test_bot, "test.yaml", ad, AdUpdateStrategy.REPLACE,
                captcha_config = captcha_config,
                root_url = test_bot.root_url,
            )

        assert result == 12345
        # Submit is now performed via web_execute (JS), not web_click.
        mock_click.assert_not_awaited()
        # The dismiss button is located by text and clicked directly.
        dismiss_btn.click.assert_awaited_once()
        assert any(
            call_args.args[:2] == (By.TEXT, "Ohne Hochschieben weiter")
            for call_args in mock_find.await_args_list
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
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, side_effect = _confirmation_execute(confirmation_url)),
            patch.object(test_bot, "web_scroll_page_down", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.publishing_submission.ainput", new_callable = AsyncMock),
        ):
            result = await publishing_submission.submit_and_confirm_ad(
                test_bot, "test.yaml", ad, AdUpdateStrategy.REPLACE,
                captcha_config = captcha_config,
                root_url = test_bot.root_url,
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
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, side_effect = _confirmation_execute(confirmation_url)),
            patch.object(test_bot, "web_scroll_page_down", new_callable = AsyncMock) as mock_scroll,
            patch("kleinanzeigen_bot.publishing_submission.ainput", new_callable = AsyncMock) as mock_ainput,
        ):
            result = await publishing_submission.submit_and_confirm_ad(
                test_bot, "test.yaml", ad, AdUpdateStrategy.REPLACE,
                captcha_config = captcha_config,
                root_url = test_bot.root_url,
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
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = [True, TimeoutError("timed out")]),
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, side_effect = _confirmation_execute("https://example.invalid/edit")),
            patch.object(test_bot, "web_scroll_page_down", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.publishing_submission._try_recover_ad_id_from_redirect", new_callable = AsyncMock, return_value = 99999),
            patch("kleinanzeigen_bot.publishing_submission.ainput", new_callable = AsyncMock),
        ):
            result = await publishing_submission.submit_and_confirm_ad(
                test_bot, "test.yaml", ad, AdUpdateStrategy.REPLACE,
                captcha_config = captcha_config,
                root_url = test_bot.root_url,
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
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = [True, TimeoutError("timed out")]),
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, side_effect = _confirmation_execute("https://example.invalid/edit")),
            patch.object(test_bot, "web_scroll_page_down", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.publishing_submission._try_recover_ad_id_from_redirect", new_callable = AsyncMock, return_value = None),
            patch("kleinanzeigen_bot.publishing_submission.ainput", new_callable = AsyncMock),
            pytest.raises(PublishSubmissionUncertainError),
        ):
            await publishing_submission.submit_and_confirm_ad(
                test_bot, "test.yaml", ad, AdUpdateStrategy.REPLACE,
                captcha_config = captcha_config,
                root_url = test_bot.root_url,
            )


class TestPublishedAdsRecovery:
    """Tests for fail-closed recovery from a complete published-ad snapshot."""

    @pytest.mark.asyncio
    async def test_requires_complete_pre_submit_baseline(self, test_bot:KleinanzeigenBot) -> None:
        """Recovery requires a complete pre-submit baseline before attempting reconciliation."""
        with patch(
            "kleinanzeigen_bot.publishing_submission.published_ads.fetch_published_ads",
            new_callable = AsyncMock,
        ) as fetch_mock:
            result = await publishing_submission._try_recover_ad_id_from_published_ads(
                test_bot,
                root_url = test_bot.root_url,
                title = "Test Ad Title",
                known_published_ad_ids = None,
            )

        assert result is None
        fetch_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_retries_until_one_new_exact_title_id_appears(self, test_bot:KleinanzeigenBot) -> None:
        """Recovery retries until exactly one new ad with the expected title ID appears."""
        with (
            patch(
                "kleinanzeigen_bot.publishing_submission.published_ads.fetch_published_ads",
                new_callable = AsyncMock,
                side_effect = [
                    [{"id": 10, "state": "active", "title": "Test Ad Title"}],
                    [
                        {"id": 10, "state": "active", "title": "Test Ad Title"},
                        {"id": "11", "state": "active", "title": "Test Ad Title"},
                        {"id": 12, "state": "active", "title": "Test Ad Title extra"},
                    ],
                ],
            ) as fetch_mock,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as sleep_mock,
        ):
            result = await publishing_submission._try_recover_ad_id_from_published_ads(
                test_bot,
                root_url = test_bot.root_url,
                title = "Test Ad Title",
                known_published_ad_ids = frozenset({10}),
            )

        assert result == 11
        assert fetch_mock.await_count == 2
        sleep_mock.assert_awaited_once_with(1_000)

    @pytest.mark.asyncio
    async def test_rejects_ambiguous_new_exact_title_ids(self, test_bot:KleinanzeigenBot) -> None:
        """Recovery rejects ambiguous results when multiple new exact-title IDs are found."""
        with (
            patch(
                "kleinanzeigen_bot.publishing_submission.published_ads.fetch_published_ads",
                new_callable = AsyncMock,
                return_value = [
                    {"id": 11, "state": "active", "title": "Test Ad Title"},
                    {"id": 12, "state": "active", "title": "Test Ad Title"},
                ],
            ),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as sleep_mock,
        ):
            result = await publishing_submission._try_recover_ad_id_from_published_ads(
                test_bot,
                root_url = test_bot.root_url,
                title = "Test Ad Title",
                known_published_ad_ids = frozenset({10}),
            )

        assert result is None
        sleep_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_recovery_continues_after_incomplete_fetch_and_ignores_invalid_ids(
        self,
        test_bot:KleinanzeigenBot,
    ) -> None:
        """Recovery continues after an incomplete fetch and ignores invalid ad IDs."""
        recovered_ads = [
            {"title": "Test Ad Title"},
            {"id": "not-an-id", "title": "Test Ad Title"},
            {"id": 10, "title": "Test Ad Title"},
            {"id": 11, "title": "Different title"},
            {"id": 12, "title": "Test Ad Title"},
        ]
        with (
            patch(
                "kleinanzeigen_bot.publishing_submission.published_ads.fetch_published_ads",
                new_callable = AsyncMock,
                side_effect = [
                    PublishedAdsFetchIncompleteError("incomplete"),
                    recovered_ads,
                ],
            ) as fetch_mock,
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock) as sleep_mock,
        ):
            result = await publishing_submission._try_recover_ad_id_from_published_ads(
                test_bot,
                root_url = test_bot.root_url,
                title = "Test Ad Title",
                known_published_ad_ids = frozenset({10}),
            )

        assert result == 12
        assert fetch_mock.await_count == 2
        sleep_mock.assert_awaited_once_with(1_000)

    @pytest.mark.asyncio
    async def test_submit_recovers_id_after_explicit_idless_success(self, test_bot:KleinanzeigenBot) -> None:
        """Submit recovers the ad ID after an explicit id-less success response."""
        ad = _make_min_ad()

        async def await_condition(condition:Any, **_:object) -> bool:
            return bool(await condition())

        with (
            patch("kleinanzeigen_bot.captcha_flow.check_and_wait_for_captcha", new_callable = AsyncMock),
            patch.object(test_bot, "web_set_input_value", new_callable = AsyncMock),
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = [None] * 4),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = await_condition),
            patch.object(
                test_bot,
                "web_execute",
                new_callable = AsyncMock,
                side_effect = _idless_success_execute(test_bot.root_url),
            ),
            patch(
                "kleinanzeigen_bot.publishing_submission._try_recover_ad_id_from_redirect",
                new_callable = AsyncMock,
                return_value = None,
            ) as redirect_recover_mock,
            patch(
                "kleinanzeigen_bot.publishing_submission._try_recover_ad_id_from_published_ads",
                new_callable = AsyncMock,
                return_value = 777,
            ) as recover_mock,
        ):
            result = await publishing_submission.submit_and_confirm_ad(
                test_bot,
                "test.yaml",
                ad,
                AdUpdateStrategy.REPLACE,
                captcha_config = test_bot.config.captcha,
                root_url = test_bot.root_url,
                known_published_ad_ids = frozenset({10}),
            )

        assert result == 777
        redirect_recover_mock.assert_not_awaited()
        recover_mock.assert_awaited_once_with(
            test_bot,
            root_url = test_bot.root_url,
            title = ad.title,
            known_published_ad_ids = frozenset({10}),
        )

    @pytest.mark.asyncio
    async def test_submit_accepts_idless_success_after_update(self, test_bot:KleinanzeigenBot) -> None:
        """An ID-less update confirmation reuses the configured ad ID."""
        ad = _make_min_ad()
        ad.id = 777

        async def await_condition(condition:Any, **_:object) -> bool:
            return bool(await condition())

        with (
            patch("kleinanzeigen_bot.publishing_submission._get_form_validation_errors", new_callable = AsyncMock, return_value = []),
            patch("kleinanzeigen_bot.captcha_flow.check_and_wait_for_captcha", new_callable = AsyncMock),
            patch.object(test_bot, "web_set_input_value", new_callable = AsyncMock),
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = [None] * 4),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = await_condition),
            patch.object(
                test_bot,
                "web_execute",
                new_callable = AsyncMock,
                side_effect = ["", "submit", True, f"{test_bot.root_url}/done"],
            ),
            patch(
                "kleinanzeigen_bot.publishing_submission._is_idless_publish_success_page",
                new_callable = AsyncMock,
                return_value = True,
            ),
            patch(
                "kleinanzeigen_bot.publishing_submission._try_recover_ad_id_from_published_ads",
                new_callable = AsyncMock,
            ) as recover_mock,
        ):
            result = await publishing_submission.submit_and_confirm_ad(
                test_bot,
                "test.yaml",
                ad,
                AdUpdateStrategy.MODIFY,
                captcha_config = test_bot.config.captcha,
                root_url = test_bot.root_url,
            )

        assert result == 777
        recover_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_submit_rejects_idless_update_success_without_configured_id(self, test_bot:KleinanzeigenBot) -> None:
        """An update cannot recover safely if its configured ID is unexpectedly absent."""
        ad = _make_min_ad()

        async def await_condition(condition:Any, **_:object) -> bool:
            return bool(await condition())

        with (
            patch("kleinanzeigen_bot.publishing_submission._get_form_validation_errors", new_callable = AsyncMock, return_value = []),
            patch("kleinanzeigen_bot.captcha_flow.check_and_wait_for_captcha", new_callable = AsyncMock),
            patch.object(test_bot, "web_set_input_value", new_callable = AsyncMock),
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = [None] * 4),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = await_condition),
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, side_effect = ["", "submit", True, f"{test_bot.root_url}/done"]),
            patch(
                "kleinanzeigen_bot.publishing_submission._is_idless_publish_success_page",
                new_callable = AsyncMock,
                return_value = True,
            ),
            pytest.raises(PublishSubmissionUncertainError, match = "configured ad ID is missing"),
        ):
            await publishing_submission.submit_and_confirm_ad(
                test_bot,
                "test.yaml",
                ad,
                AdUpdateStrategy.MODIFY,
                captcha_config = test_bot.config.captcha,
                root_url = test_bot.root_url,
            )

    @pytest.mark.asyncio
    async def test_submit_falls_back_to_tracking_when_idless_marker_is_absent(self, test_bot:KleinanzeigenBot) -> None:
        """An unrecognized confirmation page retains the existing tracking fallback."""
        ad = _make_min_ad()

        async def await_condition(condition:Any, **_:object) -> bool:
            return bool(await condition())

        with (
            patch("kleinanzeigen_bot.publishing_submission._get_form_validation_errors", new_callable = AsyncMock, return_value = []),
            patch("kleinanzeigen_bot.captcha_flow.check_and_wait_for_captcha", new_callable = AsyncMock),
            patch.object(test_bot, "web_set_input_value", new_callable = AsyncMock),
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = [None] * 4),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = await_condition),
            patch.object(
                test_bot,
                "web_execute",
                new_callable = AsyncMock,
                side_effect = ["", "submit", True, f"{test_bot.root_url}/done", f"{test_bot.root_url}/done"],
            ),
            patch(
                "kleinanzeigen_bot.publishing_submission._is_idless_publish_success_page",
                new_callable = AsyncMock,
                return_value = False,
            ),
            patch(
                "kleinanzeigen_bot.publishing_submission._try_recover_ad_id_from_redirect",
                new_callable = AsyncMock,
                return_value = 777,
            ) as tracking_recover_mock,
        ):
            result = await publishing_submission.submit_and_confirm_ad(
                test_bot,
                "test.yaml",
                ad,
                AdUpdateStrategy.MODIFY,
                captcha_config = test_bot.config.captcha,
                root_url = test_bot.root_url,
            )

        assert result == 777
        tracking_recover_mock.assert_awaited_once_with(test_bot, pre_submit_referrer = "")

    @pytest.mark.asyncio
    async def test_submit_fails_closed_when_idless_publish_recovery_finds_no_ad(self, test_bot:KleinanzeigenBot) -> None:
        """An ID-less publish confirmation remains uncertain without one new exact-title ad."""
        ad = _make_min_ad()

        async def await_condition(condition:Any, **_:object) -> bool:
            return bool(await condition())

        with (
            patch("kleinanzeigen_bot.publishing_submission._get_form_validation_errors", new_callable = AsyncMock, return_value = []),
            patch("kleinanzeigen_bot.captcha_flow.check_and_wait_for_captcha", new_callable = AsyncMock),
            patch.object(test_bot, "web_set_input_value", new_callable = AsyncMock),
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = [None] * 4),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = await_condition),
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, side_effect = ["", "submit", True, f"{test_bot.root_url}/done"]),
            patch(
                "kleinanzeigen_bot.publishing_submission._is_idless_publish_success_page",
                new_callable = AsyncMock,
                return_value = True,
            ),
            patch(
                "kleinanzeigen_bot.publishing_submission._try_recover_ad_id_from_published_ads",
                new_callable = AsyncMock,
                return_value = None,
            ),
            pytest.raises(PublishSubmissionUncertainError, match = "no ad ID could be recovered"),
        ):
            await publishing_submission.submit_and_confirm_ad(
                test_bot,
                "test.yaml",
                ad,
                AdUpdateStrategy.REPLACE,
                captcha_config = test_bot.config.captcha,
                root_url = test_bot.root_url,
                known_published_ad_ids = frozenset({10}),
            )

    @pytest.mark.asyncio
    async def test_submit_fails_closed_when_published_ads_recovery_raises(
        self,
        test_bot:KleinanzeigenBot,
    ) -> None:
        """Submit fails closed when published-ads recovery itself raises an exception."""
        ad = _make_min_ad()

        async def await_condition(condition:Any, **_:object) -> bool:
            return bool(await condition())

        with (
            patch("kleinanzeigen_bot.captcha_flow.check_and_wait_for_captcha", new_callable = AsyncMock),
            patch.object(test_bot, "web_set_input_value", new_callable = AsyncMock),
            patch.object(test_bot, "web_click", new_callable = AsyncMock),
            patch.object(test_bot, "web_probe", new_callable = AsyncMock, side_effect = [None] * 4),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = await_condition),
            patch.object(
                test_bot,
                "web_execute",
                new_callable = AsyncMock,
                side_effect = _idless_success_execute(test_bot.root_url),
            ),
            patch(
                "kleinanzeigen_bot.publishing_submission._try_recover_ad_id_from_redirect",
                new_callable = AsyncMock,
                return_value = None,
            ) as redirect_recover_mock,
            patch(
                "kleinanzeigen_bot.publishing_submission._try_recover_ad_id_from_published_ads",
                new_callable = AsyncMock,
                side_effect = RuntimeError("API unavailable"),
            ),
            pytest.raises(PublishSubmissionUncertainError),
        ):
            await publishing_submission.submit_and_confirm_ad(
                test_bot,
                "test.yaml",
                ad,
                AdUpdateStrategy.REPLACE,
                captcha_config = test_bot.config.captcha,
                root_url = test_bot.root_url,
                known_published_ad_ids = frozenset({10}),
            )

        redirect_recover_mock.assert_not_awaited()


# This small DOM fixture executes the production browser scripts in the Node
# runtime already provided by the development toolchain. It has no networking.
_DOM_FIXTURE = r"""
const payload = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const clicks = [];
class Element {
    constructor(spec, parent = null) {
        this.tagName = (spec.tag || 'div').toUpperCase();
        this.attrs = spec.attrs || {};
        this.id = this.attrs.id || '';
        this.htmlFor = this.attrs.for || '';
        this.ownText = spec.text || '';
        this.hidden = Boolean(spec.hidden);
        this.disabled = Boolean(spec.disabled);
        this.validationMessage = spec.validationMessage || '';
        this.invalid = Boolean(spec.invalid);
        this.parentElement = parent;
        this.children = (spec.children || []).map(child => new Element(child, this));
    }
    get innerText() { return [this.ownText, ...this.children.map(child => child.innerText)].filter(Boolean).join(' '); }
    get textContent() { return this.innerText; }
    getAttribute(name) { return this.attrs[name] ?? null; }
    getClientRects() {
        for (let element = this; element; element = element.parentElement) if (element.hidden) return [];
        return [{}];
    }
    contains(element) {
        for (let candidate = element; candidate; candidate = candidate.parentElement) if (candidate === this) return true;
        return false;
    }
    matches(selector) {
        return selector.split(',').some(raw => {
            let part = raw.trim();
            if (part === '*') return true;
            if (part.startsWith('.')) return (this.attrs.class || '').split(' ').includes(part.slice(1));
            if (part.includes(':invalid')) {
                if (!this.invalid) return false;
                part = part.replace(':invalid', '');
            }
            const tag = part.match(/^[a-z]+/i)?.[0];
            if (tag && this.tagName !== tag.toUpperCase()) return false;
            return [...part.matchAll(/\[([\w-]+)(?:(\$?=)"([^"]*)")?\]/g)].every(([, name, op, value]) => {
                const actual = this.getAttribute(name);
                if (actual === null) return false;
                return !op || (op === '$=' ? actual.endsWith(value) : actual === value);
            });
        });
    }
    querySelectorAll(selector) {
        return this.children.flatMap(child => [child, ...child.querySelectorAll('*')]).filter(child => child.matches(selector));
    }
    closest(selector) {
        for (let element = this; element; element = element.parentElement) if (element.matches(selector)) return element;
        return null;
    }
    click() {
        clicks.push(this.innerText);
        if (this.getAttribute('role') === 'radio') {
            for (const radio of document.querySelectorAll('button[role="radio"]')) radio.attrs['aria-checked'] = 'false';
            this.attrs['aria-checked'] = 'true';
        }
    }
}
const body = new Element(payload.dom);
const document = {
    body,
    querySelectorAll: selector => body.querySelectorAll(selector),
    getElementById: id => [body, ...body.querySelectorAll('*')].find(element => element.id === id) || null,
};
const window = {location: {pathname: payload.pathname}};
const getComputedStyle = element => ({visibility: element.getClientRects().length ? 'visible' : 'hidden'});
const result = eval(payload.script);
process.stdout.write(JSON.stringify({result, clicks}));
"""


def _run_dom_script(script:str, dom:dict[str, Any], *, pathname:str = "/p-anzeigentypauswahl/216/123/draft") -> dict[str, Any]:
    """Execute a production script against a synthetic DOM without a browser."""
    completed = node(
        ["-e", _DOM_FIXTURE], return_completed_process = True,
        input = json.dumps({"script": script, "dom": dom, "pathname": pathname}),
        capture_output = True, text = True, encoding = "utf-8", check = True, timeout = 10,
    )
    return cast(dict[str, Any], json.loads(completed.stdout))


def _basis_dom(*, selected:bool = True) -> dict[str, Any]:
    """Build two independent package cards like the observed listing screen."""
    return {"tag": "body", "children": [{"tag": "main", "children": [
        {"children": [
            {"tag": "button", "text": "Basis Paket", "attrs": {"role": "radio", "aria-checked": str(selected).lower()}},
            {"tag": "p", "text": "Kostenlos inserieren – ohne zusätzliche Vorteile."},
            {"tag": "h3", "text": "Kostenlos"},
            {"tag": "p", "text": "60 Tage Anzeigendauer."},
        ]},
        {"children": [
            {"tag": "button", "text": "Plus Paket", "attrs": {"role": "radio", "aria-checked": str(not selected).lower()}},
            {"tag": "h3", "text": "11,99 €"},
        ]},
        {"tag": "button", "text": "Anzeige aufgeben"},
    ]}]}


def _editor_dom(*, container:str = "main", errors:bool = False) -> dict[str, Any]:
    """Build an editor with React aria-invalid status errors and no native error."""
    return {"tag": "body", "children": [
        {"tag": container, "children": [
            {"tag": "input", "attrs": {"id": "ad-title"}},
            {"children": [
                {"tag": "label", "text": "Kilometerstand", "attrs": {"for": "autos.km"}},
                {"tag": "input", "attrs": {"id": "autos.km", "name": "attributeMap[autos.km]",
                                          "aria-invalid": str(errors).lower(), "aria-describedby": "km-message"}},
                {"tag": "p", "text": "Bitte gib einen Wert ein." if errors else "Trage den Kilometerstand ein.",
                 "attrs": {"id": "km-message", "role": "status"}},
            ]},
            {"tag": "button", "text": "Nächster Schritt"},
        ]},
        {"tag": "aside", "children": [{"tag": "p", "text": "Unrelated page error", "attrs": {"id": "other-error"}}]},
    ]}


class TestFreeBasisPackage:
    """The real package script must never choose a paid or unverified option."""

    @pytest.mark.parametrize(("action", "selected", "expected_clicks", "expected_state"), [
        ("inspect", True, [], "ready"),
        ("select", True, [], "ready"),
        ("select", False, ["Basis Paket"], "waiting"),
        ("submit", True, ["Anzeige aufgeben"], "submitted"),
        ("submit", False, [], "unsafe"),
    ])
    def test_only_chosen_free_basis_can_be_submitted(
        self, action:str, selected:bool, expected_clicks:list[str], expected_state:str,
    ) -> None:
        """Select or submit only the independently verified free Basis package."""
        script = publishing_submission._FREE_BASIS_PACKAGE_SCRIPT.replace("ACTION", json.dumps(action))
        result = _run_dom_script(script, _basis_dom(selected = selected))
        assert result["result"]["state"] == expected_state
        assert result["clicks"] == expected_clicks

    @pytest.mark.parametrize("case", ["paid", "extra-fee", "hidden-free-price", "duplicate-basis",
                             "shared-free-text", "disabled", "double-selected", "missing-submit"])
    def test_rejects_paid_ambiguous_and_unavailable_packages(self, case:str) -> None:
        """Refuse package clicks when pricing, selection, or availability is unsafe."""
        dom = _basis_dom()
        cards = dom["children"][0]["children"]
        basis = cards[0]
        if case == "paid":
            basis["children"][2]["text"] = "9,99 €"
        elif case == "extra-fee":
            basis["children"].append({"tag": "p", "text": "Gebühr 5,00 EUR"})
        elif case == "hidden-free-price":
            basis["children"][2]["hidden"] = True
        elif case == "duplicate-basis":
            cards.append({"tag": "button", "text": "Basis Paket", "attrs": {"role": "radio"}})
        elif case == "shared-free-text":
            cards.extend(basis["children"][1:3])
            basis["children"] = basis["children"][:1]
        elif case == "disabled":
            basis["children"][0]["disabled"] = True
        elif case == "double-selected":
            cards[1]["children"][0]["attrs"]["aria-checked"] = "true"
        else:
            cards.pop(2)
        script = publishing_submission._FREE_BASIS_PACKAGE_SCRIPT.replace("ACTION", json.dumps("submit"))
        result = _run_dom_script(script, dom)
        assert result["result"]["state"] == "unsafe"
        assert result["clicks"] == []

    def test_draft_route_is_not_publication_confirmation(self) -> None:
        """Leave editor drafts untouched when the package-selection page has not loaded."""
        result = _run_dom_script(
            publishing_submission._FREE_BASIS_PACKAGE_SCRIPT.replace("ACTION", json.dumps("submit")),
            _basis_dom(), pathname = "/p-anzeige-aufgeben-schritt2.html",
        )
        assert result["result"]["state"] == "waiting"
        assert result["clicks"] == []


class TestReactFormValidation:
    """Read referenced status errors with and without a native form container."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("container", ["form", "main", "div"])
    async def test_reports_referenced_react_errors_without_native_invalid_state(self, test_bot:KleinanzeigenBot, container:str) -> None:
        """Report field-associated React errors even when native validity passes."""
        with patch.object(
            test_bot, "web_execute", new_callable = AsyncMock,
            side_effect = lambda script: _run_dom_script(script, _editor_dom(container = container, errors = True))["result"],
        ):
            result = await publishing_submission._get_form_validation_errors(test_bot)
        assert result == ["Kilometerstand: Bitte gib einen Wert ein."]

    @pytest.mark.asyncio
    async def test_ignores_status_hints_and_unrelated_page_errors(self, test_bot:KleinanzeigenBot) -> None:
        """Ignore informative status text and errors outside the ad editor."""
        with patch.object(
            test_bot, "web_execute", new_callable = AsyncMock,
            side_effect = lambda script: _run_dom_script(script, _editor_dom())["result"],
        ):
            result = await publishing_submission._get_form_validation_errors(test_bot)
        assert result == []

    @pytest.mark.asyncio
    async def test_native_form_retains_its_explicit_summary_error(self, test_bot:KleinanzeigenBot) -> None:
        """Native form ownership includes a summary, but excludes sibling errors."""
        dom = {"tag": "body", "children": [
            {"tag": "form", "children": [
                {"tag": "input", "attrs": {"id": "ad-title"}},
                {"tag": "div", "text": "Bitte korrigiere die Anzeige.", "attrs": {"id": "ad-form-error"}},
            ]},
            {"tag": "div", "text": "Unrelated page notification.", "attrs": {"id": "notification-error"}},
        ]}
        with patch.object(
            test_bot, "web_execute", new_callable = AsyncMock,
            side_effect = lambda script: _run_dom_script(script, dom)["result"],
        ):
            assert await publishing_submission._get_form_validation_errors(test_bot) == ["Bitte korrigiere die Anzeige."]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("backing_key", "button_id"), [
        ("autos.marke_s+autos.model_s", "autos.marke_s"),
        ("autos.marke_s+autos.model_s", "vehicle-brand-control"),
        ("autos.marke_s", "vehicle-brand-control"),
    ])
    async def test_attribute_backing_associates_its_local_combobox_error(
        self, test_bot:KleinanzeigenBot, backing_key:str, button_id:str,
    ) -> None:
        """Compound names and arbitrary button IDs still identify one ad field."""
        dom = {"tag": "body", "children": [{"children": [
            {"tag": "input", "attrs": {"id": "ad-title"}},
            {"children": [
                {"tag": "input", "hidden": True, "attrs": {"type": "hidden", "name": f"attributeMap[{backing_key}]"}},
                {"tag": "label", "text": "Marke", "attrs": {"for": button_id}},
                {"tag": "button", "attrs": {"id": button_id, "role": "combobox", "aria-invalid": "true", "aria-describedby": "marke-message"}},
                {"tag": "p", "text": "Bitte waehle eine Marke.", "attrs": {"id": "marke-message", "role": "status"}},
            ]},
            {"children": [
                {"tag": "button", "attrs": {"id": "foreign-control", "role": "combobox", "aria-invalid": "true", "aria-describedby": "foreign-error"}},
                {"tag": "p", "text": "Unrelated field error.", "attrs": {"id": "foreign-error", "role": "status"}},
            ]},
        ]}]}
        with patch.object(
            test_bot, "web_execute", new_callable = AsyncMock,
            side_effect = lambda script: _run_dom_script(script, dom)["result"],
        ):
            assert await publishing_submission._get_form_validation_errors(test_bot) == ["Marke: Bitte waehle eine Marke."]


class TestStagedPackageSubmission:
    """Exercise the complete staged boundary with production scripts and fake DOMs."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("scenario", ["normal", "already-package", "invalid", "paid", "submit-timeout", "submit-response-lost"])
    async def test_next_step_and_free_basis_are_handled_without_duplicate_submits(
        self, test_bot:KleinanzeigenBot, scenario:str,
    ) -> None:
        """Publish free Basis once after staging, or stop before unsafe package submission."""
        stage = "package" if scenario == "already-package" else "editor"
        selected = False
        clicked:list[str] = []

        async def execute(script:str) -> Any:
            """Advance the local editor and package states while recording clicks and injected failures."""
            nonlocal stage, selected
            dom = _basis_dom(selected = selected) if stage == "package" else _editor_dom(errors = scenario == "invalid")
            pathname = "/p-anzeigentypauswahl/216/123/draft" if stage == "package" else "/p-anzeige-aufgeben-schritt2.html"
            if scenario == "paid" and stage == "package":
                dom["children"][0]["children"][0]["children"][2]["text"] = "9,99 €"
            result = _run_dom_script(script, dom, pathname = pathname)
            clicked.extend(result["clicks"])
            if "Nächster Schritt" in result["clicks"] and scenario != "invalid":
                stage = "package"
            if "Basis Paket" in result["clicks"]:
                selected = True
            if "Anzeige aufgeben" in result["clicks"]:
                if scenario == "submit-timeout":
                    raise TimeoutError("response lost after final click")
                if scenario == "submit-response-lost":
                    return None
            return result["result"]

        async def await_condition(condition:Any, **_:Any) -> Any:
            """Poll the simulated browser condition with a bounded number of attempts."""
            for _attempt in range(3):
                result = await condition()
                if result:
                    return result
            raise TimeoutError("fixture condition was not met")

        with (
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, side_effect = execute),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = await_condition),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
        ):
            if scenario in {"invalid", "paid"}:
                with pytest.raises(AdFormValidationError):
                    await publishing_submission._click_submit_button(test_bot, package_selection = "BASIS")
                assert "Anzeige aufgeben" not in clicked
                assert "Basis Paket" not in clicked
            elif scenario in {"submit-timeout", "submit-response-lost"}:
                with pytest.raises(PublishSubmissionUncertainError):
                    await publishing_submission._click_submit_button(test_bot, package_selection = "BASIS")
                assert clicked.count("Anzeige aufgeben") == 1
            else:
                await publishing_submission._click_submit_button(test_bot, package_selection = "BASIS")
                assert clicked.count("Basis Paket") == 1
                assert clicked.count("Anzeige aufgeben") == 1
        assert "Plus Paket" not in clicked
        assert clicked.count("Nächster Schritt") <= 1


class TestPreSubmitTransportFailures:
    """Transport loss before publication is retryable, not form rejection."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("boundary", ["discovery", "next", "package-inspect", "package-select", "selected-state"])
    @pytest.mark.parametrize("error_type", ["timeout", "protocol"])
    async def test_original_transport_failure_is_preserved_before_final_click(
        self, test_bot:KleinanzeigenBot, boundary:str, error_type:str,
    ) -> None:
        """Preserve retryable transport errors until the final publication click."""
        failure = TimeoutError("transport lost before publication") if error_type == "timeout" else ProtocolException(MagicMock(), "lost", 0)
        stage = "editor" if boundary in {"discovery", "next"} else "package"
        selected = False
        clicked:list[str] = []

        async def execute(script:str) -> Any:
            """Inject transport failures at the selected pre-publication boundary."""
            nonlocal stage, selected
            failing = (
                (boundary == "discovery" and 'const action = "inspect"' in script and "const labels =" in script)
                or (boundary == "next" and 'const action = "next"' in script)
                or (boundary == "package-inspect" and 'const action = "inspect"' in script and "const labels =" not in script)
                or (boundary == "package-select" and 'const action = "select"' in script)
                or (boundary == "selected-state" and selected and 'const action = "inspect"' in script)
            )
            if failing:
                raise failure
            dom = _editor_dom() if stage == "editor" else _basis_dom(selected = selected)
            pathname = "/p-anzeige-aufgeben-schritt2.html" if stage == "editor" else "/p-anzeigentypauswahl/local/draft"
            result = _run_dom_script(script, dom, pathname = pathname)
            clicked.extend(result["clicks"])
            if "Nächster Schritt" in result["clicks"]:
                stage = "package"
            if "Basis Paket" in result["clicks"]:
                selected = True
            return result["result"]

        async def await_condition(condition:Any, **_:Any) -> Any:
            """Evaluate the simulated browser condition once without masking its transport error."""
            return await condition()

        with (
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, side_effect = execute),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = await_condition),
            pytest.raises(type(failure)) as exc_info,
        ):
            await publishing_submission._click_submit_button(test_bot, package_selection = "BASIS")
        assert exc_info.value is failure
        assert "Anzeige aufgeben" not in clicked


class TestSingleFinalSubmit:
    """Button discovery may poll, but a final click may never be repeated."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("label", ["Anzeige aufgeben", "Änderungen speichern", "Anzeige speichern"])
    @pytest.mark.parametrize("outcome", ["success", "timeout", "protocol", "response-lost", "sleep-timeout"])
    async def test_legacy_publish_and_update_click_once_outside_polling(
        self, test_bot:KleinanzeigenBot, label:str, outcome:str,
    ) -> None:
        """Keep the final publish or update click outside polling and avoid retries after uncertainty."""
        clicked:list[str] = []
        discovery_attempts = 0
        polling = False

        async def execute(script:str) -> Any:
            """Simulate delayed button discovery and failures after the recorded final click."""
            nonlocal discovery_attempts
            if 'const action = "inspect"' in script:
                discovery_attempts += 1
                dom = {"tag": "body", "children": [] if discovery_attempts == 1 else [{"tag": "button", "text": label}]}
            else:
                assert not polling, "The final click must not be inside retry polling"
                dom = {"tag": "body", "children": [{"tag": "button", "text": label}]}
            result = _run_dom_script(script, dom, pathname = "/p-anzeige-aufgeben-schritt2.html")
            clicked.extend(result["clicks"])
            if result["clicks"]:
                if outcome == "timeout":
                    raise TimeoutError("response lost after final click")
                if outcome == "protocol":
                    raise ProtocolException(MagicMock(), "response lost after final click", 0)
                if outcome == "response-lost":
                    return None
            return result["result"]

        async def await_condition(condition:Any, **_:Any) -> Any:
            """Poll only discovery callbacks while exposing whether a click occurs during polling."""
            nonlocal polling
            polling = True
            try:
                for _attempt in range(3):
                    result = await condition()
                    if result:
                        return result
            finally:
                polling = False
            raise TimeoutError("No submit button")

        with (
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, side_effect = execute),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = await_condition),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock,
                         side_effect = TimeoutError("navigation lost after click") if outcome == "sleep-timeout" else None),
        ):
            if outcome == "success":
                await publishing_submission._click_submit_button(test_bot)
            else:
                with pytest.raises(PublishSubmissionUncertainError):
                    await publishing_submission._click_submit_button(test_bot)
        assert discovery_attempts == 2
        assert clicked == [label]


@pytest.mark.asyncio
async def test_unreferenced_status_error_is_scoped_to_one_invalid_field(test_bot:KleinanzeigenBot) -> None:
    """An adjacent React error is read without collecting other page statuses."""
    dom = _editor_dom(errors = True)
    field_container = dom["children"][0]["children"][1]
    del field_container["children"][1]["attrs"]["aria-describedby"]
    field_container["children"][2]["attrs"].pop("id")
    dom["children"][0]["children"].append({"tag": "p", "text": "Unrelated main status", "attrs": {"role": "status"}})
    with patch.object(
        test_bot, "web_execute", new_callable = AsyncMock,
        side_effect = lambda script: _run_dom_script(script, dom)["result"],
    ):
        result = await publishing_submission._get_form_validation_errors(test_bot)
    assert result == ["Kilometerstand: Bitte gib einen Wert ein."]


class TestConfigurablePackageSelection:
    """Package choices propagate to the submit boundary without changing legacy forms."""

    @pytest.fixture(autouse = True)
    def interactive_terminal(self, monkeypatch:pytest.MonkeyPatch) -> None:
        """Manual-flow fixtures explicitly model an interactive terminal."""
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("package_selection", [None, "BASIS", "MANUAL"])
    async def test_submission_passes_the_configured_package_selection(
        self, test_bot:KleinanzeigenBot, package_selection:Literal["BASIS", "MANUAL"] | None,
    ) -> None:
        """Forward the configured package preference and use MANUAL when it is omitted."""
        selection_kwargs:dict[str, Any] = {} if package_selection is None else {"package_selection": package_selection}
        with (
            patch("kleinanzeigen_bot.captcha_flow.check_and_wait_for_captcha", new_callable = AsyncMock),
            patch.object(test_bot, "web_set_input_value", new_callable = AsyncMock),
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, return_value = ""),
            patch("kleinanzeigen_bot.publishing_submission._click_submit_button", new_callable = AsyncMock,
                  side_effect = AdFormValidationError("fixture stops before submission")) as submit,
            pytest.raises(AdFormValidationError),
        ):
            await publishing_submission.submit_and_confirm_ad(
                test_bot, "test.yaml", _make_min_ad(), AdUpdateStrategy.REPLACE,
                captcha_config = test_bot.config.captcha, root_url = test_bot.root_url,
                **selection_kwargs,
            )
        submit.assert_awaited_once_with(test_bot, package_selection = package_selection or "MANUAL")

    @pytest.mark.asyncio
    async def test_omitted_selection_never_chooses_or_publishes_a_package(self, test_bot:KleinanzeigenBot) -> None:
        """Require manual completion without choosing or submitting an unspecified package."""
        clicked:list[str] = []

        async def execute(script:str) -> Any:
            """Run the package fixture and record any attempted control clicks."""
            result = _run_dom_script(script, _basis_dom(selected = False), pathname = "/p-anzeigentypauswahl/216/123/draft")
            clicked.extend(result["clicks"])
            return result["result"]

        async def await_condition(condition:Any, **_:Any) -> Any:
            """Evaluate the package fixture condition without adding retry behavior."""
            return await condition()

        with (
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, side_effect = execute),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = await_condition),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.publishing_submission.ainput", new_callable = AsyncMock, return_value = "") as manual_input,
            pytest.raises(PublishSubmissionUncertainError),
        ):
            await publishing_submission._click_submit_button(test_bot)
        assert clicked == []
        manual_input.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_manual_selection_keeps_the_legacy_submit_flow(self, test_bot:KleinanzeigenBot) -> None:
        """Preserve single-step publishing when no package-selection page appears."""
        dom = {"tag": "body", "children": [{"tag": "button", "text": "Anzeige aufgeben"}]}
        clicked:list[str] = []

        async def execute(script:str) -> Any:
            """Run the legacy editor fixture and record the final submit click."""
            result = _run_dom_script(script, dom, pathname = "/p-anzeige-aufgeben-schritt2.html")
            clicked.extend(result["clicks"])
            return result["result"]

        async def await_condition(condition:Any, **_:Any) -> Any:
            """Evaluate the legacy submit-discovery condition once."""
            return await condition()

        with (
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, side_effect = execute),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = await_condition),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.publishing_submission.ainput", new_callable = AsyncMock) as manual_input,
        ):
            await publishing_submission._click_submit_button(test_bot, package_selection = "MANUAL")
        assert clicked == ["Anzeige aufgeben"]
        manual_input.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("initial_stage", ["editor", "package"])
    @pytest.mark.parametrize("outcome", ["completed", "still-package", "still-editor", "eof", "input-error", "page-timeout"])
    async def test_manual_package_selection_never_clicks_a_package_or_final_submit(
        self, test_bot:KleinanzeigenBot, initial_stage:str, outcome:str,
    ) -> None:
        """Leave package and final submit controls to the user throughout manual completion."""
        stage = initial_stage
        input_seen = False
        clicked:list[str] = []

        async def execute(script:str) -> Any:
            """Simulate editor, package, and confirmation states around manual completion."""
            nonlocal stage
            if input_seen and outcome == "page-timeout":
                raise TimeoutError("manual completion state unavailable")
            if stage == "package":
                dom = _basis_dom(selected = False)
                pathname = "/p-anzeigentypauswahl/216/123/draft"
            elif stage == "editor":
                dom = _editor_dom()
                pathname = "/p-anzeige-aufgeben-schritt2.html"
            else:
                dom = {"tag": "body", "children": []}
                pathname = "/p-anzeige-aufgeben-bestaetigung.html"
            result = _run_dom_script(script, dom, pathname = pathname)
            clicked.extend(result["clicks"])
            if "Nächster Schritt" in result["clicks"]:
                stage = "package"
            return result["result"]

        async def await_condition(condition:Any, **_:Any) -> Any:
            """Poll the simulated page state with a bounded number of attempts."""
            for _attempt in range(3):
                result = await condition()
                if result:
                    return result
            raise TimeoutError("fixture condition was not met")

        async def manual_completion(*_:object) -> str:
            """Simulate user completion, unfinished drafts, or interactive input failures."""
            nonlocal stage, input_seen
            assert stage == "package"
            assert "Basis Paket" not in clicked
            assert "Anzeige aufgeben" not in clicked
            input_seen = True
            if outcome == "eof":
                raise EOFError("interactive input unavailable")
            if outcome == "input-error":
                raise OSError("interactive input unavailable")
            stage = {"still-package": "package", "still-editor": "editor"}.get(outcome, "done")
            return ""

        with (
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, side_effect = execute),
            patch.object(test_bot, "web_await", new_callable = AsyncMock, side_effect = await_condition),
            patch.object(test_bot, "web_sleep", new_callable = AsyncMock),
            patch("kleinanzeigen_bot.publishing_submission.ainput", new_callable = AsyncMock,
                  side_effect = manual_completion) as manual_input,
        ):
            if outcome == "completed":
                await publishing_submission._click_submit_button(test_bot, package_selection = "MANUAL")
            else:
                with pytest.raises(PublishSubmissionUncertainError):
                    await publishing_submission._click_submit_button(test_bot, package_selection = "MANUAL")
        assert clicked == (["Nächster Schritt"] if initial_stage == "editor" else [])
        manual_input.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_incomplete_manual_submission_prevents_draft_id_recovery(self, test_bot:KleinanzeigenBot) -> None:
        """Prevent draft ID recovery when manual publication remains unconfirmed."""
        with (
            patch("kleinanzeigen_bot.captcha_flow.check_and_wait_for_captcha", new_callable = AsyncMock),
            patch.object(test_bot, "web_set_input_value", new_callable = AsyncMock),
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, return_value = ""),
            patch("kleinanzeigen_bot.publishing_submission._click_submit_button", new_callable = AsyncMock,
                  side_effect = PublishSubmissionUncertainError("manual submission was not confirmed")),
            patch("kleinanzeigen_bot.publishing_submission._try_recover_ad_id_from_redirect", new_callable = AsyncMock) as redirect,
            patch("kleinanzeigen_bot.publishing_submission._try_recover_ad_id_from_published_ads", new_callable = AsyncMock) as published,
            pytest.raises(PublishSubmissionUncertainError),
        ):
            await publishing_submission.submit_and_confirm_ad(
                test_bot, "test.yaml", _make_min_ad(), AdUpdateStrategy.REPLACE,
                captcha_config = test_bot.config.captcha, root_url = test_bot.root_url,
                package_selection = "MANUAL", known_published_ad_ids = frozenset({100}),
            )
        redirect.assert_not_awaited()
        published.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("result", [None, 0, "false"])
    async def test_manual_completion_requires_an_explicit_non_editor_state(
        self, test_bot:KleinanzeigenBot, result:object,
    ) -> None:
        """Reject ambiguous browser state after the user confirms manual completion."""
        with (
            patch("kleinanzeigen_bot.publishing_submission.ainput", new_callable = AsyncMock, return_value = ""),
            patch.object(test_bot, "web_execute", new_callable = AsyncMock, return_value = result),
            pytest.raises(PublishSubmissionUncertainError),
        ):
            await publishing_submission._wait_for_manual_package_submission(test_bot)

    @pytest.mark.asyncio
    async def test_manual_completion_browser_disconnect_is_uncertain(self, test_bot:KleinanzeigenBot) -> None:
        """Keep manual completion uncertain when the browser connection is lost."""
        with (
            patch("kleinanzeigen_bot.publishing_submission.ainput", new_callable = AsyncMock, return_value = ""),
            patch.object(test_bot, "web_execute", new_callable = AsyncMock,
                         side_effect = ProtocolException(MagicMock(), "connection lost", 0)),
            pytest.raises(PublishSubmissionUncertainError),
        ):
            await publishing_submission._wait_for_manual_package_submission(test_bot)


@pytest.mark.asyncio
@pytest.mark.parametrize("stdin_missing", [False, True])
async def test_manual_preflight_requires_interactive_input_before_prompt(
    test_bot:KleinanzeigenBot, monkeypatch:pytest.MonkeyPatch, stdin_missing:bool,
) -> None:
    """A known unattended run has not yet attempted publication or human input."""
    if stdin_missing:
        monkeypatch.setattr(sys, "stdin", None)
    else:
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with (
        patch("kleinanzeigen_bot.publishing_submission.ainput", new_callable = AsyncMock) as manual_input,
        patch.object(test_bot, "web_execute", new_callable = AsyncMock) as execute,
        pytest.raises(ManualPackageSelectionRequiredError, match = "publishing.package_selection: BASIS"),
    ):
        await publishing_submission._wait_for_manual_package_submission(test_bot)
    manual_input.assert_not_awaited()
    execute.assert_not_awaited()
