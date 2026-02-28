# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
from collections.abc import Generator

import pytest

from kleinanzeigen_bot.utils.dom_rules import (
    SelectorNotFoundError,
    SelectorPlaceholderError,
    load_bundled_dom_rules,
    resolve_selector_alternatives,
)


@pytest.fixture(autouse = True)
def _clear_dom_rules_cache() -> Generator[None, None, None]:
    load_bundled_dom_rules.cache_clear()
    yield
    load_bundled_dom_rules.cache_clear()


def test_load_bundled_dom_rules_contains_expected_keys() -> None:
    rules = load_bundled_dom_rules()

    assert rules.schema_version == 1
    assert "auth.login_detection.user_info" in rules.selectors
    assert "pagination.container" in rules.selectors


def test_resolve_selector_alternatives_resolves_template_placeholders() -> None:
    alternatives = resolve_selector_alternatives("ad_management.extend_button", placeholders = {"ad_id": "123"})

    assert alternatives
    assert any("123" in alternative.value for alternative in alternatives)


def test_resolve_selector_alternatives_raises_on_missing_rule() -> None:
    with pytest.raises(SelectorNotFoundError, match = "does not exist"):
        _ = resolve_selector_alternatives("missing.key")


def test_resolve_selector_alternatives_raises_on_missing_placeholder() -> None:
    with pytest.raises(SelectorPlaceholderError, match = "Missing placeholders"):
        _ = resolve_selector_alternatives("ad_management.extend_button")


def test_resolve_selector_alternatives_returns_independent_copies() -> None:
    first = resolve_selector_alternatives("pagination.container")
    first[0].value = ".Changed"

    second = resolve_selector_alternatives("pagination.container")
    assert second[0].value == ".Pagination"
