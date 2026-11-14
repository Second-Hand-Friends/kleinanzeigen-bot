# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Current category picker values and labels, read from Kleinanzeigen's own category tree actions.

``161/227/playstation/playstation_4`` has the category ``227`` at depth 1; ``playstation`` (depth 2)
and ``playstation_4`` (depth 3) are attribute values whose labels Kleinanzeigen renames over time.
The site's picker data is the only reliable source for them.
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


async def fetch_picker_attributes(web:WebScrapingMixin, action:str, body:Mapping[str, str | int]) -> list[dict[str, Any]]:
    """Read picker attributes from the page's category tree actions; empty if unavailable."""
    try:
        async with asyncio.timeout(web.timeout("page_load")):
            response = await web.web_execute(page_scripts.call("category_picker_attributes", action, dict(body)))
    except TimeoutError:
        return []
    if not isinstance(response, dict) or response.get("statusCode") != _HTTP_OK or not isinstance(response.get("attributes"), list):
        LOG.debug("Category picker action %s returned no usable data: %r", action, response)
        return []
    return [attribute for attribute in response["attributes"] if isinstance(attribute, dict)]


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
    """Return the labels the site currently shows for the attribute levels (depth 2 and 3) of a category path."""
    path = _path(category)
    attribute, attributes = await _find_attribute(web, path)
    if attribute is None:
        if attributes:
            LOG.warning(
                "Category value '%s' is not offered under category '%s' any more. Offered values: %s",
                path[2],
                path[1],
                ", ".join(value for candidate in attributes for value, _label in _options(candidate)),
            )
        return {}

    labels = {_ATTRIBUTE_DEPTH: _label_of(attribute, path[2]) or path[2]}
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
    """Return the ``(value, label)`` pairs offered one level below the attribute value of ``category``."""
    path = _path(category)
    attribute, _attributes = await _find_attribute(web, path)
    if attribute is None:
        return []
    return [option for dependent in await _dependent_attributes(web, path, attribute) for option in _options(dependent)]
