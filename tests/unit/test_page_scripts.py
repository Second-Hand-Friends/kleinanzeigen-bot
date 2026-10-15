# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Guard the conventions of the in-page scripts in ``resources/js``."""

import re
from importlib import resources

import pytest

from kleinanzeigen_bot.utils import page_scripts

_SCRIPTS = sorted(
    (entry.name.removesuffix(".js"), entry.read_text(encoding = "utf-8"))
    for entry in resources.files("kleinanzeigen_bot.resources").joinpath("js").iterdir()
    if entry.name.endswith(".js")
)
_SUPPRESSION = re.compile(r"@ts-expect-error|@ts-ignore|@ts-nocheck|deno-lint-ignore")

# One-off backlog from moving the existing scripts out of Python strings (#1342): script name -> number
# of js-debt markers. Only ever lower these numbers; new or changed scripts must pass without suppressions.
_MIGRATION_DEBT:dict[str, int] = {
    "select_button_combobox": 3,
    "select_listbox_option": 1,
}


@pytest.mark.parametrize(("name", "source"), _SCRIPTS, ids = [name for name, _ in _SCRIPTS])
def test_script_has_header_and_type_checking(name:str, source:str) -> None:
    """Every script carries the license header and opts into type checking."""
    assert source.startswith("// SPDX-FileCopyrightText: © Jens Bergmann and contributors\n")
    assert "// @ts-check\n" in source


@pytest.mark.parametrize(("name", "source"), _SCRIPTS, ids = [name for name, _ in _SCRIPTS])
def test_no_new_suppressions(name:str, source:str) -> None:
    """Suppressions are allowed only as recorded js-debt from the migration, never as new debt."""
    suppressions = [line.strip() for line in source.splitlines() if _SUPPRESSION.search(line)]
    assert all("js-debt:" in line for line in suppressions), suppressions
    assert len(suppressions) <= _MIGRATION_DEBT.get(name, 0), suppressions


def test_migration_debt_lists_only_existing_scripts() -> None:
    """A removed or renamed script must not keep an allowance that a new script could reuse."""
    assert set(_MIGRATION_DEBT) <= {name for name, _ in _SCRIPTS}


def test_call_passes_arguments_as_json() -> None:
    """Arguments become JSON literals, so quotes in values cannot break out of the script."""
    script = page_scripts.call("submit_button", "inspect", ['Say "hi"', "it's"])
    assert script.endswith(""")("inspect", ["Say \\"hi\\"", "it's"])""")


def test_function_binds_arguments_after_the_element() -> None:
    """Bound arguments follow the element that ``Element.apply`` passes first."""
    assert page_scripts.function("submit_button").startswith("// SPDX")
    assert page_scripts.function("submit_button", 1).startswith("(element) => // SPDX")
    assert page_scripts.function("submit_button", 1).endswith("(element, 1)")
