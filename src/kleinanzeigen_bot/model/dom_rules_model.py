# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from kleinanzeigen_bot.utils.pydantics import ContextualModel


class SelectorAlternative(ContextualModel):
    by:Literal["ID", "CLASS_NAME", "CSS_SELECTOR", "TAG_NAME", "TEXT", "XPATH"] = Field(description = "Selector strategy.")
    value:str = Field(min_length = 1, description = "Selector expression.")


class DomRulesConfig(ContextualModel):
    schema_version:Literal[1] = Field(description = "DOM rules schema version.")
    ruleset_version:str = Field(min_length = 1, description = "Version of the bundled ruleset.")
    selectors:dict[str, list[SelectorAlternative]] = Field(default_factory = dict, description = "Rule key to selector alternatives mapping.")

    @model_validator(mode = "after")
    def _validate_selectors(self) -> "DomRulesConfig":
        for key, alternatives in self.selectors.items():
            if not key.strip():
                raise ValueError("selector rule keys must not be blank")
            if not alternatives:
                raise ValueError(f"selector rule '{key}' must define at least one alternative")
        return self
