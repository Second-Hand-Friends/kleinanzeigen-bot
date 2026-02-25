# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
from __future__ import annotations

from functools import lru_cache
from string import Formatter
from typing import TYPE_CHECKING, Final

from kleinanzeigen_bot import resources
from kleinanzeigen_bot.model.dom_rules_model import DomRulesConfig, SelectorAlternative
from kleinanzeigen_bot.utils import dicts

if TYPE_CHECKING:
    from collections.abc import Mapping


class SelectorRuleError(ValueError):
    """Base class for selector rule resolution errors."""


class SelectorNotFoundError(SelectorRuleError):
    """Raised when a selector rule key is missing from the ruleset."""


class SelectorPlaceholderError(SelectorRuleError):
    """Raised when selector placeholders are missing in provided context."""

    def __init__(self, rule_key:str, missing_keys:set[str]) -> None:
        self.rule_key = rule_key
        self.missing_keys = missing_keys
        super().__init__(f"Missing placeholders {sorted(missing_keys)} for rule '{rule_key}'")


_FORMATTER:Final[Formatter] = Formatter()


def _get_placeholders(selector_value:str) -> set[str]:
    placeholders:set[str] = set()
    for _, field_name, _, _ in _FORMATTER.parse(selector_value):
        if not field_name:
            continue
        placeholders.add(field_name)
    return placeholders


@lru_cache(maxsize = 1)
def load_bundled_dom_rules() -> DomRulesConfig:
    payload = dicts.load_dict_from_module(resources, "dom_rules.v1.json", "DOM rules")
    return DomRulesConfig.model_validate(payload, context = "dom_rules.v1.json")


def resolve_selector_alternatives(rule_key:str, *, placeholders:Mapping[str, str] | None = None) -> list[SelectorAlternative]:
    rules = load_bundled_dom_rules()
    alternatives = rules.selectors.get(rule_key)
    if alternatives is None:
        raise SelectorNotFoundError(f"DOM selector rule '{rule_key}' does not exist")

    values = placeholders or {}
    resolved:list[SelectorAlternative] = []

    for alternative in alternatives:
        required = _get_placeholders(alternative.value)
        missing = required.difference(values)
        if missing:
            raise SelectorPlaceholderError(rule_key, missing)

        resolved_value = alternative.value.format_map(values) if required else alternative.value
        resolved.append(alternative.model_copy(update = {"value": resolved_value}))

    return resolved
