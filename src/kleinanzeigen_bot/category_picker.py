# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Current category picker values and labels, read from Kleinanzeigen's own category tree actions.

``161/227/playstation/playstation_4`` has the category ``227`` at depth 1; ``playstation`` (depth 2)
and ``playstation_4`` (depth 3) are attribute values whose labels Kleinanzeigen renames over time.
The site's own category tree (depth 0-1) and picker data (depth 2-3) are the reliable source for the labels.
"""

import asyncio
from collections.abc import Mapping
from typing import Any, Final

from .utils import loggers as _loggers
from .utils import page_scripts
from .utils.web_scraping_mixin import WebScrapingMixin

LOG:Final[_loggers.Logger] = _loggers.get_logger(__name__)

_HTTP_OK:Final[int] = 200
_ATTRIBUTE_DEPTH:Final[int] = 2  # index of the first attribute segment in a category path


async def fetch_category_action(web:WebScrapingMixin, action:str, body:Mapping[str, str | int]) -> Any:
    """Call one of the page's category tree actions and return its decoded answer; ``None`` if unavailable."""
    try:
        async with asyncio.timeout(web.timeout("page_load")):
            response = await web.web_execute(page_scripts.call("category_tree_action", action, dict(body)))
    except TimeoutError:
        return None
    if not isinstance(response, dict) or response.get("statusCode") != _HTTP_OK:
        LOG.debug("Category tree action %s returned no usable data: %r", action, response)
        return None
    return response.get("data")


async def fetch_picker_attributes(web:WebScrapingMixin, action:str, body:Mapping[str, str | int]) -> list[dict[str, Any]]:
    """Read picker attributes (``getPickerAttributes`` / ``getDependentPickerAttributes``); empty if unavailable."""
    data = await fetch_category_action(web, action, body)
    if not isinstance(data, list):
        return []
    return [
        {
            "attributeId": attribute.get("attributeId"),
            "required": attribute.get("required") is True,
            "options": [
                {"value": option.get("value"), "label": option.get("localizedValue")}
                for option in attribute.get("localizedOptions") or []
                if isinstance(option, dict)
            ],
        }
        for attribute in data
        if isinstance(attribute, dict)
    ]


async def _tree_labels(web:WebScrapingMixin, path:list[str]) -> dict[int, str]:
    """Return the labels of the category levels 0 and 1 from the page's category tree."""
    tree = await fetch_category_action(web, "getTree", {})
    if not isinstance(tree, list):
        return {}
    labels:dict[int, str] = {}
    nodes = [node for node in tree if isinstance(node, dict)]
    for depth in range(min(len(path), _ATTRIBUTE_DEPTH)):
        node = next((node for node in nodes if str(node.get("id")) == path[depth] and node.get("name")), None)
        if node is None:
            LOG.warning(
                "Category '%s' is not offered by Kleinanzeigen any more. Offered categories: %s",
                path[depth],
                ", ".join(f"{node.get('id')} ({node.get('name')})" for node in nodes),
            )
            break
        labels[depth] = str(node["name"])
        nodes = [child for child in node.get("children") or [] if isinstance(child, dict)]
    return labels


def _options(attribute:Mapping[str, Any]) -> list[tuple[str, str]]:
    return [
        (str(option.get("value", "")), str(option.get("label") or ""))
        for option in attribute.get("options") or []
        if isinstance(option, dict)
    ]


def _label_of(attribute:Mapping[str, Any], value:str) -> str | None:
    for option_value, label in _options(attribute):
        if option_value.casefold() == value.casefold():
            return label or None
    return None


def _path(category:str) -> list[str]:
    return [segment for segment in category.split("/") if segment]


async def _find_attribute(web:WebScrapingMixin, path:list[str]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Return the attribute of category ``path[1]`` that offers ``path[2]``, and all attributes of that category."""
    if len(path) <= _ATTRIBUTE_DEPTH or not path[1].isdigit() or path[2].isdigit():
        return None, []
    attributes = await fetch_picker_attributes(web, "getPickerAttributes", {"categoryId": int(path[1])})
    return next((attribute for attribute in attributes if _label_of(attribute, path[2]) is not None), None), attributes


async def _dependent_attributes(web:WebScrapingMixin, path:list[str], attribute:Mapping[str, Any]) -> list[dict[str, Any]]:
    return await fetch_picker_attributes(
        web,
        "getDependentPickerAttributes",
        {"categoryId": path[1], "attributeId": str(attribute["attributeId"]), "optionValue": path[2]},
    )


async def category_picker_labels(web:WebScrapingMixin, category:str) -> dict[int, str]:
    """Return the labels the site currently shows for each level of a category path (depth 0-3), as far as it offers them."""
    path = _path(category)
    labels = await _tree_labels(web, path)
    attribute, attributes = await _find_attribute(web, path)
    if attribute is None:
        if attributes:
            LOG.warning(
                "Category value '%s' is not offered under category '%s' any more. Offered values: %s",
                path[2],
                path[1],
                ", ".join(value for candidate in attributes for value, _label in _options(candidate)),
            )
        return labels

    labels[_ATTRIBUTE_DEPTH] = _label_of(attribute, path[2]) or path[2]
    if len(path) > _ATTRIBUTE_DEPTH + 1:
        dependents = await _dependent_attributes(web, path, attribute)
        for dependent in dependents:
            if (label := _label_of(dependent, path[3])) is not None:
                labels[_ATTRIBUTE_DEPTH + 1] = label
                break
        else:
            if dependents:
                LOG.warning(
                    "Category value '%s' is not offered under '%s' any more. Offered values: %s",
                    path[3],
                    path[2],
                    ", ".join(value for dependent in dependents for value, _label in _options(dependent)),
                )
    return labels


async def dependent_picker_options(web:WebScrapingMixin, category:str) -> list[tuple[str, str]]:
    """Return the ``(value, label)`` pairs offered one level below ``category`` (its attribute values or their dependents)."""
    path = _path(category)
    if len(path) == _ATTRIBUTE_DEPTH and path[1].isdigit():  # the category itself asks for an attribute value
        attributes = await fetch_picker_attributes(web, "getPickerAttributes", {"categoryId": int(path[1])})
        return [option for attribute in attributes for option in _options(attribute)]
    if len(path) != _ATTRIBUTE_DEPTH + 1:  # only a path that ends at the attribute value can take one more segment
        return []
    attribute, _attributes = await _find_attribute(web, path)
    if attribute is None:
        return []
    return [option for dependent in await _dependent_attributes(web, path, attribute) for option in _options(dependent)]
