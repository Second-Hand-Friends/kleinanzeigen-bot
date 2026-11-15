# SPDX-FileCopyrightText: © albert0815 and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Read the category-specific fields of the ad form without submitting anything.

``special_attributes`` depend on the category, and so do the fields the form requires.
This opens the ad form, selects the category with the same dialog code used for publishing
and reports every ``attributeMap[...]`` field: key, ``special_attributes`` key, label,
required flag, preselected value and the selectable options.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Final

from . import publishing_form
from .utils import page_scripts

if TYPE_CHECKING:
    from .utils.web_scraping_mixin import WebScrapingMixin

# Category-dependent fields render after the category is set; comboboxes need a moment to list their options.
_OPTIONS_DELAY_MS:Final[int] = 800


async def read_category_fields(web:WebScrapingMixin, root_url:str, category:str, categories:dict[str, str]) -> list[dict[str, Any]]:
    """Return the attribute fields of ``category`` (a name from categories.yaml or a numeric path).

    Each entry has ``key`` (e.g. ``fahrraeder.type``), ``attribute`` (the ``special_attributes`` key,
    e.g. ``type_s``), ``label``, ``required``, ``preset`` and ``options`` (``value`` and ``label``).
    """
    category_path = categories.get(category, category)
    await web.web_open(f"{root_url}/p-anzeige-aufgeben-schritt2.html", reload_if_already_open = True)
    await web.dismiss_consent_banner()
    await publishing_form.set_category(web, category = category_path, ad_file = "(fields)")
    await web.web_sleep()
    fields = await web.web_execute(page_scripts.call("category_fields", _OPTIONS_DELAY_MS))
    return fields if isinstance(fields, list) else []


def render_fields(fields:list[dict[str, Any]]) -> str:
    """Format the fields as JSON, one object per field."""
    return json.dumps(fields, ensure_ascii = False, indent = 2)
