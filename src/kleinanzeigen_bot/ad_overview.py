# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Query parameters of the ad overview page (``m-meine-anzeigen.html``)."""

from typing import Final
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

__all__ = [
    "UNFILTERED_FIRST_PAGE_PARAMS",
    "first_page_url",
]

# The overview restores these UI-stored query parameters from ``sessionStorage`` (``queryParams``)
# unless its URL sets them, so a plain URL can open on a later page or with a stale search
# (issue #1302). ``sort`` needs no pinning: the page adds a default ``sort`` to every URL lacking one.
UNFILTERED_FIRST_PAGE_PARAMS:Final[dict[str, str]] = {"pageNumber": "1", "keyword": ""}


def first_page_url(page_url:str) -> str:
    """Return ``page_url`` pinned to the first, unfiltered overview page; other parameters are kept."""
    parts = urlsplit(page_url)
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values = True) if key not in UNFILTERED_FIRST_PAGE_PARAMS]
    query.extend(UNFILTERED_FIRST_PAGE_PARAMS.items())
    return urlunsplit(parts._replace(query = urlencode(query)))
