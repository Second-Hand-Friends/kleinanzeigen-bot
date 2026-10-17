# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Unit tests for saving new ads as drafts (``draft`` command)."""

from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kleinanzeigen_bot import publishing_form, publishing_persistence, publishing_submission, publishing_workflow
from kleinanzeigen_bot.model.ad_model import Ad
from kleinanzeigen_bot.model.config_model import Config
from kleinanzeigen_bot.utils.exceptions import AdBatchError, PublishSubmissionUncertainError

_AD:dict[str, Any] = {
    "active": True,
    "type": "OFFER",
    "sell_directly": False,
    "republication_interval": 7,
    "title": "Test Mini-PC with a long enough title",
    "description": "Test description",
    "category": "161/228",
    "price": 100,
    "price_type": "NEGOTIABLE",
    "shipping_type": "PICKUP",
    "contact": {"name": "Test", "zipcode": "12345"},
}


async def _await_once(condition:Callable[[], Awaitable[Any]], **_kwargs:Any) -> Any:
    """web_await replacement: evaluate once, time out if the condition is not met."""
    result = await condition()
    if not result:
        raise TimeoutError("condition not met")
    return result


class FakeEditor:
    """Answers the draft button script like the ad editor would."""

    def __init__(self, *, inspect:str = "draft", click:str = "clicked", confirmed:bool = True) -> None:
        self.inspect = inspect
        self.click = click
        self.confirmed = confirmed
        self.clicks = 0

    async def execute(self, script:str) -> Any:
        if script == publishing_submission._draft_button_script("inspect"):
            return self.inspect
        if script == publishing_submission._draft_button_script("click"):
            self.clicks += 1
            return self.click
        if script == publishing_submission._draft_button_script("confirmed"):
            return self.confirmed
        raise AssertionError(f"unexpected script: {script[:80]}")


def _web(editor:FakeEditor) -> MagicMock:
    web = MagicMock()
    web.web_execute = AsyncMock(side_effect = editor.execute)
    web.web_await = AsyncMock(side_effect = _await_once)
    web.web_set_input_value = AsyncMock()
    web.timeout = MagicMock(return_value = 1.0)
    return web


class TestSubmitDraft:

    @pytest.fixture(autouse = True)
    def no_captcha(self) -> Any:
        with patch("kleinanzeigen_bot.publishing_submission.captcha_flow.check_and_wait_for_captcha", new_callable = AsyncMock):
            yield

    @pytest.mark.asyncio
    async def test_clicks_draft_button_once(self) -> None:
        editor = FakeEditor()
        web = _web(editor)
        await publishing_submission.submit_draft(web, Ad.model_validate(_AD), captcha_config = Config().captcha)
        assert editor.clicks == 1
        web.web_set_input_value.assert_awaited_once_with("ad-title", _AD["title"])

    @pytest.mark.asyncio
    @pytest.mark.parametrize("inspect", ["matches:0", "matches:2", "no-form"])
    async def test_ambiguous_button_clicks_nothing(self, inspect:str) -> None:
        editor = FakeEditor(inspect = inspect)
        with pytest.raises(TimeoutError):
            await publishing_submission.submit_draft(_web(editor), Ad.model_validate(_AD), captcha_config = Config().captcha)
        assert editor.clicks == 0

    @pytest.mark.asyncio
    async def test_missing_confirmation_is_uncertain_and_not_retried(self) -> None:
        editor = FakeEditor(confirmed = False)
        with pytest.raises(PublishSubmissionUncertainError):
            await publishing_submission.submit_draft(_web(editor), Ad.model_validate(_AD), captcha_config = Config().captcha)
        assert editor.clicks == 1


class TestSaveAdDrafts:

    @pytest.mark.asyncio
    async def test_skips_ads_with_id_and_never_publishes_or_persists(self) -> None:
        new_ad = Ad.model_validate(_AD)
        old_ad = Ad.model_validate({**_AD, "id": 123})
        with (
            patch.object(publishing_workflow, "save_ad_draft", new_callable = AsyncMock) as save,
            patch.object(publishing_workflow, "delete_old_ad_if_needed", new_callable = AsyncMock) as delete,
            patch.object(publishing_persistence, "persist_published_ad") as persist,
        ):
            await publishing_workflow.save_ad_drafts(
                MagicMock(), [("new.yaml", new_ad, dict(_AD)), ("old.yaml", old_ad, {**_AD, "id": 123})],
                root_url = "https://example.invalid", config = Config(),
            )
        save.assert_awaited_once()
        assert save.await_args is not None
        assert save.await_args.args[2] is new_ad
        delete.assert_not_called()
        persist.assert_not_called()

    @pytest.mark.asyncio
    async def test_uncertain_draft_is_not_retried_and_fails_the_batch(self) -> None:
        ad = Ad.model_validate(_AD)
        with (
            patch.object(publishing_workflow, "save_ad_draft", new_callable = AsyncMock,
                         side_effect = PublishSubmissionUncertainError("lost")) as save,
            pytest.raises(AdBatchError),
        ):
            await publishing_workflow.save_ad_drafts(MagicMock(), [("a.yaml", ad, dict(_AD))], root_url = "https://example.invalid", config = Config())
        save.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_save_ad_draft_opens_create_form_and_submits_draft(self) -> None:
        web = MagicMock()
        web.web_open = AsyncMock()
        web.dismiss_consent_banner = AsyncMock()
        ad = Ad.model_validate(_AD)
        with (
            patch.object(publishing_form, "fill_ad_form", new_callable = AsyncMock) as fill,
            patch.object(publishing_submission, "submit_draft", new_callable = AsyncMock) as submit,
            patch.object(publishing_submission, "submit_and_confirm_ad", new_callable = AsyncMock) as publish,
        ):
            await publishing_workflow.save_ad_draft(web, "a.yaml", ad, root_url = "https://example.invalid", config = Config())
        web.web_open.assert_awaited_once_with("https://example.invalid/p-anzeige-aufgeben-schritt2.html", reload_if_already_open = True)
        fill.assert_awaited_once()
        submit.assert_awaited_once()
        publish.assert_not_called()
