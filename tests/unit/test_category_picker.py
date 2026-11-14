# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Tests for the category picker data read from Kleinanzeigen's category tree actions."""

import asyncio
import logging
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from kleinanzeigen_bot.app import KleinanzeigenBot
from kleinanzeigen_bot.category_picker import category_picker_labels, dependent_picker_options


def _picker_response(attribute_id:str, options:dict[str, str], *, required:bool = True) -> dict[str, Any]:
    return {
        "statusCode": 200,
        "attributes": [{
            "attributeId": attribute_id,
            "required": required,
            "options": [{"value": value, "label": label} for value, label in options.items()],
        }],
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_category_picker_labels_use_the_sites_current_labels(test_bot:KleinanzeigenBot) -> None:
    """#1388: attribute levels are renamed by the site; their labels come from the page's picker data."""
    responses = [
        _picker_response("pc_videospiele.art", {"xbox": "Xbox", "playstation": "PlayStation"}),
        _picker_response("pc_videospiele.platform", {"playstation_5": "PlayStation 5", "playstation_4": "PlayStation 4"}, required = False),
    ]
    with patch.object(test_bot, "web_execute", new_callable = AsyncMock, side_effect = responses) as execute:
        labels = await category_picker_labels(test_bot, "161/227/playstation/playstation_4")

    assert labels == {2: "PlayStation", 3: "PlayStation 4"}
    assert "getDependentPickerAttributes" in execute.await_args_list[1].args[0]
    assert '"attributeId": "pc_videospiele.art"' in execute.await_args_list[1].args[0]


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("category", ["161/227", "17/22/123", "161/abc/playstation"])
async def test_category_picker_labels_skip_paths_without_attribute_levels(test_bot:KleinanzeigenBot, category:str) -> None:
    with patch.object(test_bot, "web_execute", new_callable = AsyncMock) as execute:
        assert await category_picker_labels(test_bot, category) == {}
    execute.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_category_picker_labels_warn_when_value_is_not_offered(
    test_bot:KleinanzeigenBot, caplog:pytest.LogCaptureFixture,
) -> None:
    response = _picker_response("pc_videospiele.art", {"xbox": "Xbox", "playstation": "PlayStation"})
    with (
        patch.object(test_bot, "web_execute", new_callable = AsyncMock, return_value = response),
        caplog.at_level(logging.WARNING),
    ):
        assert await category_picker_labels(test_bot, "161/227/dreamcast") == {}
    assert "'dreamcast' is not offered under category '227'" in caplog.text
    assert "xbox, playstation" in caplog.text


@pytest.mark.unit
@pytest.mark.asyncio
async def test_category_picker_labels_warn_when_dependent_value_is_not_offered(
    test_bot:KleinanzeigenBot, caplog:pytest.LogCaptureFixture,
) -> None:
    responses = [
        _picker_response("pc_videospiele.art", {"playstation": "PlayStation"}),
        _picker_response("pc_videospiele.platform", {"playstation_5": "PlayStation 5", "playstation_4": "PlayStation 4"}, required = False),
    ]
    with (
        patch.object(test_bot, "web_execute", new_callable = AsyncMock, side_effect = responses),
        caplog.at_level(logging.WARNING),
    ):
        assert await category_picker_labels(test_bot, "161/227/playstation/playstation_9") == {2: "PlayStation"}
    assert "'playstation_9' is not offered under 'playstation'" in caplog.text
    assert "playstation_5, playstation_4" in caplog.text


@pytest.mark.unit
@pytest.mark.asyncio
async def test_category_picker_request_is_bounded_by_a_timeout(test_bot:KleinanzeigenBot) -> None:
    async def stall(_code:str) -> None:
        await asyncio.sleep(10)

    with (
        patch.object(test_bot, "timeout", return_value = 0.01),
        patch.object(test_bot, "web_execute", side_effect = stall),
    ):
        assert await category_picker_labels(test_bot, "161/227/playstation") == {}


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("response", [None, {"statusCode": 500, "attributes": []}, {"statusCode": 200, "attributes": []}, TimeoutError("slow")])
async def test_category_picker_labels_fall_back_silently_without_site_data(
    test_bot:KleinanzeigenBot, response:Any, caplog:pytest.LogCaptureFixture,
) -> None:
    mock = AsyncMock(side_effect = response) if isinstance(response, Exception) else AsyncMock(return_value = response)
    with patch.object(test_bot, "web_execute", mock), caplog.at_level(logging.WARNING):
        assert await category_picker_labels(test_bot, "161/227/playstation") == {}
    assert "not offered" not in caplog.text


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dependent_picker_options_list_the_values_below_an_attribute(test_bot:KleinanzeigenBot) -> None:
    responses = [
        _picker_response("pc_videospiele.art", {"playstation": "PlayStation"}),
        _picker_response("pc_videospiele.platform", {"playstation_5": "PlayStation 5", "playstation_4": "PlayStation 4"}, required = False),
    ]
    with patch.object(test_bot, "web_execute", new_callable = AsyncMock, side_effect = responses):
        assert await dependent_picker_options(test_bot, "161/227/playstation") == [
            ("playstation_5", "PlayStation 5"), ("playstation_4", "PlayStation 4"),
        ]
