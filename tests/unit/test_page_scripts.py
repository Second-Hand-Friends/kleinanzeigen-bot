# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Guard the conventions of the in-page scripts in ``resources/js``."""

import re
from importlib import resources

import pytest

_SCRIPTS = sorted(
    (entry.name.removesuffix(".js"), entry.read_text(encoding = "utf-8"))
    for entry in resources.files("kleinanzeigen_bot.resources").joinpath("js").iterdir()
    if entry.name.endswith(".js")
)
_SUPPRESSION = re.compile(r"@ts-expect-error|@ts-ignore|@ts-nocheck|deno-lint-ignore")


@pytest.mark.parametrize(("name", "source"), _SCRIPTS, ids = [name for name, _ in _SCRIPTS])
def test_script_has_header_and_type_checking(name:str, source:str) -> None:
    """Every script carries the license header and opts into type checking."""
    assert source.startswith("// SPDX-FileCopyrightText: © Jens Bergmann and contributors\n")
    assert "// @ts-check\n" in source


@pytest.mark.parametrize(("name", "source"), _SCRIPTS, ids = [name for name, _ in _SCRIPTS])
def test_no_suppressions(name:str, source:str) -> None:
    """Scripts pass deno lint and deno check without any suppression."""
    suppressions = [line.strip() for line in source.splitlines() if _SUPPRESSION.search(line)]
    assert not suppressions, suppressions
