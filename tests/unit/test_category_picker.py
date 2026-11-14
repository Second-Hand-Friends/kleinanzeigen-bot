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
from kleinanzeigen_bot.category_picker import category_label_match_score, category_picker_labels, dependent_picker_options, resolve_category_text
from kleinanzeigen_bot.utils.exceptions import CategoryResolutionError

_TREE = [
    {"id": 161, "name": "Elektronik", "children": [{"id": 173, "name": "Smartphones", "children": []}, {"id": 227, "name": "Videospiele", "children": []}]},
    {"id": 17, "name": "Familie, Kind & Baby", "children": [{"id": 23, "name": "Spielzeug", "children": []}]},
]


def _attributes(attribute_id:str, options:dict[str, str], *, required:bool = True) -> list[dict[str, Any]]:
    return [{
        "attributeId": attribute_id,
        "required": required,
        "localizedOptions": [{"value": value, "localizedValue": label} for value, label in options.items()],
    }]


_ART = _attributes("pc_videospiele.art", {"xbox": "Xbox", "playstation": "PlayStation"})
_PLATFORM = _attributes("pc_videospiele.platform", {"playstation_5": "PlayStation 5", "playstation_4": "PlayStation 4"}, required = False)


def _site(*, tree:Any = _TREE, picker:Any = _ART, dependent:Any = _PLATFORM) -> AsyncMock:
    """Fake ``web_execute`` answering like the page's category tree actions."""
    data = {"getTree": tree, "getPickerAttributes": picker, "getDependentPickerAttributes": dependent}

    async def execute(script:str) -> Any:
        action = max(data, key = lambda name: script.rfind(f'"{name}"'))  # the call arguments come last, after the script source
        return {"statusCode": 200, "data": data[action]}

    return AsyncMock(side_effect = execute)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_category_picker_labels_use_the_sites_current_labels(test_bot:KleinanzeigenBot) -> None:
    """#1388: every level is labelled by the site: categories from the tree, attribute levels from the picker."""
    execute = _site()
    with patch.object(test_bot, "web_execute", execute):
        labels = await category_picker_labels(test_bot, "161/227/playstation/playstation_4")

    assert labels == {0: "Elektronik", 1: "Videospiele", 2: "PlayStation", 3: "PlayStation 4"}
    assert '"attributeId": "pc_videospiele.art"' in execute.await_args_list[-1].args[0]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_category_picker_labels_name_renamed_categories_by_id(test_bot:KleinanzeigenBot) -> None:
    with patch.object(test_bot, "web_execute", _site()):
        assert await category_picker_labels(test_bot, "161/173") == {0: "Elektronik", 1: "Smartphones"}


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("category", ["17/22/123", "161/abc/playstation"])
async def test_category_picker_labels_skip_attribute_requests_for_paths_without_attribute_levels(
    test_bot:KleinanzeigenBot, category:str,
) -> None:
    execute = _site()
    with patch.object(test_bot, "web_execute", execute):
        await category_picker_labels(test_bot, category)
    assert len(execute.await_args_list) == 1
    assert execute.await_args_list[0].args[0].rfind('"getTree"') > execute.await_args_list[0].args[0].rfind('"getPickerAttributes"')


@pytest.mark.unit
@pytest.mark.asyncio
async def test_category_picker_labels_warn_when_a_category_is_not_offered(
    test_bot:KleinanzeigenBot, caplog:pytest.LogCaptureFixture,
) -> None:
    with patch.object(test_bot, "web_execute", _site()), caplog.at_level(logging.WARNING):
        assert await category_picker_labels(test_bot, "161/999") == {0: "Elektronik"}
    assert "Category '999' is not offered by Kleinanzeigen any more" in caplog.text
    assert "173 (Smartphones)" in caplog.text


@pytest.mark.unit
@pytest.mark.asyncio
async def test_category_picker_labels_warn_when_value_is_not_offered(
    test_bot:KleinanzeigenBot, caplog:pytest.LogCaptureFixture,
) -> None:
    with patch.object(test_bot, "web_execute", _site()), caplog.at_level(logging.WARNING):
        assert await category_picker_labels(test_bot, "161/227/dreamcast") == {0: "Elektronik", 1: "Videospiele"}
    assert "'dreamcast' is not offered under category '227'" in caplog.text
    assert "xbox, playstation" in caplog.text


@pytest.mark.unit
@pytest.mark.asyncio
async def test_category_picker_labels_warn_when_dependent_value_is_not_offered(
    test_bot:KleinanzeigenBot, caplog:pytest.LogCaptureFixture,
) -> None:
    with patch.object(test_bot, "web_execute", _site()), caplog.at_level(logging.WARNING):
        labels = await category_picker_labels(test_bot, "161/227/playstation/playstation_9")
    assert labels == {0: "Elektronik", 1: "Videospiele", 2: "PlayStation"}
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
@pytest.mark.parametrize("response", [None, {"statusCode": 500, "data": None}, {"statusCode": 200, "data": None}, TimeoutError("slow")])
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
    with patch.object(test_bot, "web_execute", _site(picker = _attributes("pc_videospiele.art", {"playstation": "PlayStation"}))):
        assert await dependent_picker_options(test_bot, "161/227/playstation") == [
            ("playstation_5", "PlayStation 5"), ("playstation_4", "PlayStation 4"),
        ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dependent_picker_options_are_empty_for_a_complete_path(test_bot:KleinanzeigenBot) -> None:
    with patch.object(test_bot, "web_execute", new_callable = AsyncMock) as execute:
        assert await dependent_picker_options(test_bot, "161/227/playstation/playstation_4") == []
    execute.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dependent_picker_options_list_the_attribute_values_of_a_category(test_bot:KleinanzeigenBot) -> None:
    """A category such as Wearables asks for an attribute value right below it."""
    with patch.object(test_bot, "web_execute", _site()):
        assert await dependent_picker_options(test_bot, "161/227") == [("xbox", "Xbox"), ("playstation", "PlayStation")]


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(("text", "expected"), [
    pytest.param("Elektronik > Smartphones", "161/173", id = "category"),
    pytest.param("elektronik>videospiele > PlayStation", "161/227/playstation", id = "attribute-value"),
    pytest.param("Elektronik > Videospiele > PlayStation > PlayStation 4", "161/227/playstation/playstation_4", id = "dependent-value"),
])
async def test_resolve_category_text_returns_the_id_path(test_bot:KleinanzeigenBot, text:str, expected:str) -> None:
    with patch.object(test_bot, "web_execute", _site()):
        assert await resolve_category_text(test_bot, text) == expected


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(("text", "offered"), [
    pytest.param("Elektronik > Handy & Telefon", "Smartphones, Videospiele", id = "renamed-category"),
    pytest.param("Elektronik > Videospiele > Dreamcast", "Xbox, PlayStation", id = "unknown-attribute-value"),
    pytest.param("Elektronik > Videospiele > PlayStation > PlayStation 9", "PlayStation 5, PlayStation 4", id = "unknown-dependent-value"),
])
async def test_resolve_category_text_names_the_offered_values(test_bot:KleinanzeigenBot, text:str, offered:str) -> None:
    with patch.object(test_bot, "web_execute", _site()), pytest.raises(CategoryResolutionError) as raised:
        await resolve_category_text(test_bot, text)
    assert offered in str(raised.value)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_category_text_rejects_too_many_levels(test_bot:KleinanzeigenBot) -> None:
    with patch.object(test_bot, "web_execute", _site()), pytest.raises(CategoryResolutionError, match = "more levels"):
        await resolve_category_text(test_bot, "a > b > c > d > e")


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["", " > "])
async def test_resolve_category_text_ignores_empty_text(test_bot:KleinanzeigenBot, text:str) -> None:
    with patch.object(test_bot, "web_execute", new_callable = AsyncMock) as execute:
        assert await resolve_category_text(test_bot, text) is None
    execute.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_category_text_without_site_data_returns_none(test_bot:KleinanzeigenBot) -> None:
    with patch.object(test_bot, "web_execute", new_callable = AsyncMock, return_value = None):
        assert await resolve_category_text(test_bot, "Elektronik > Smartphones") is None


def test_category_label_match_score_ranks_exact_before_contained() -> None:
    assert category_label_match_score("Pullover", "Pullover & Strickjacken") == 0
    assert category_label_match_score("Hosen, Jeans & Shorts", "Hosen & Jeans") == 2
    assert category_label_match_score("Haus & Garten", "Haus und Garten") == 3
