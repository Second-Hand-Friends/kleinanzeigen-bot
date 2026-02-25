# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import pytest

from kleinanzeigen_bot.model.dom_rules_model import DomRulesConfig


def test_dom_rules_model_accepts_valid_payload() -> None:
    cfg = DomRulesConfig.model_validate(
        {
            "schema_version": 1,
            "ruleset_version": "1.0.0",
            "selectors": {
                "auth.login.email": [{"by": "ID", "value": "login-email"}],
            },
        }
    )

    assert cfg.schema_version == 1
    assert cfg.ruleset_version == "1.0.0"
    assert cfg.selectors["auth.login.email"][0].by == "ID"


def test_dom_rules_model_rejects_blank_rule_keys() -> None:
    with pytest.raises(ValueError, match = "selector rule keys must not be blank"):
        DomRulesConfig.model_validate(
            {
                "schema_version": 1,
                "ruleset_version": "1.0.0",
                "selectors": {
                    "   ": [{"by": "ID", "value": "login-email"}],
                },
            }
        )


def test_dom_rules_model_rejects_empty_alternative_list() -> None:
    with pytest.raises(ValueError, match = "must define at least one alternative"):
        DomRulesConfig.model_validate(
            {
                "schema_version": 1,
                "ruleset_version": "1.0.0",
                "selectors": {
                    "auth.login.email": [],
                },
            }
        )
