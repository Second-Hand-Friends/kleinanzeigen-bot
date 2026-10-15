# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Load the in-page JavaScript functions from ``resources/js`` and build calls to them.

Each ``resources/js/<name>.js`` file holds exactly one parenthesized function expression.
Arguments are passed as JSON literals, never spliced into the script text.
"""

import functools
import json
from importlib import resources


@functools.cache
def _source(name:str) -> str:
    """Return the function expression of ``resources/js/<name>.js``."""
    text = resources.files("kleinanzeigen_bot.resources").joinpath(f"js/{name}.js").read_text(encoding = "utf-8")
    return text.strip().removesuffix(";")


def _arguments(args:tuple[object, ...]) -> str:
    return ", ".join(json.dumps(arg) for arg in args)


def call(name:str, *args:object) -> str:
    """Return an expression for ``web_execute`` that calls the function with ``args``."""
    return f"{_source(name)}({_arguments(args)})"


def function(name:str, *bound:object) -> str:
    """Return the function for ``Element.apply``; ``bound`` arguments follow the element."""
    if not bound:
        return _source(name)
    return f"(element) => {_source(name)}(element, {_arguments(bound)})"
