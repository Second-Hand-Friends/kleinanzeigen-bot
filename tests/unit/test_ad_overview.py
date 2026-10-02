# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Tests for the ad overview query parameters."""

import pytest

from kleinanzeigen_bot import ad_overview


@pytest.mark.parametrize(
    ("page_url", "expected"),
    [
        ("https://example.invalid/m-meine-anzeigen.html", "https://example.invalid/m-meine-anzeigen.html?pageNumber=1&keyword="),
        ("https://example.invalid/m-meine-anzeigen.html?tab=ADS", "https://example.invalid/m-meine-anzeigen.html?tab=ADS&pageNumber=1&keyword="),
        (
            "https://example.invalid/m-meine-anzeigen.html?pageNumber=3&keyword=lamp&tab=ADS",
            "https://example.invalid/m-meine-anzeigen.html?tab=ADS&pageNumber=1&keyword=",
        ),
    ],
    ids = ["plain", "keeps-other-params", "replaces-page-and-keyword"],
)
def test_first_page_url_pins_first_unfiltered_page(page_url:str, expected:str) -> None:
    """The overview restores the last page and search unless the URL sets them (#1302)."""
    assert ad_overview.first_page_url(page_url) == expected
