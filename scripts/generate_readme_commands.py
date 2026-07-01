# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Generate the CLI Usage console block in README.md from source of truth.

Replaces the content between ``<!-- readme-usage:generated:start -->`` and
``<!-- readme-usage:generated:end -->`` markers in README.md with the
normalized English help text from :func:`kleinanzeigen_bot.cli.help_text`.

Pure helpers (:func:`build_usage_block`, :func:`replace_marked_section`) are
importable for in-memory use by the artifact checker.
"""
from __future__ import annotations

import pathlib
import re
import sys

from kleinanzeigen_bot.cli import help_text

START_MARKER:str = "<!-- readme-usage:generated:start -->"
END_MARKER:str = "<!-- readme-usage:generated:end -->"
README_PATH:pathlib.Path = pathlib.Path(__file__).resolve().parent.parent / "README.md"
_ANSI_RE = re.compile(r"\x1B(?:\[[0-9;]*[A-Za-z]|\[)")


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def build_usage_block() -> str:
    """Build the normalized Usage console block for the README.

    Returns a markdown console-fenced block (including leading/trailing
    newlines) ready to be placed between the start/end markers.

    Forces:
    *  executable name → ``kleinanzeigen-bot``
    *  language       → English (``en``)
    *  no ANSI escape codes
    *  no subprocess  (imports ``help_text`` directly)
    """
    raw = help_text(executable = "kleinanzeigen-bot", language = "en")
    clean = _strip_ansi(raw)
    return f"\n```console\n{clean}\n```\n"


def replace_marked_section(text:str, new_block:str) -> str:
    """Replace content between README markers with *new_block*.

    Validates that both markers exist exactly once and are in the correct
    order.  Raises :class:`ValueError` with a clear message on failure.

    The markers themselves are preserved; only the content between them
    (including surrounding whitespace) is replaced.
    """
    _validate_markers(text)

    start_idx = text.find(START_MARKER) + len(START_MARKER)
    end_idx = text.find(END_MARKER)

    before = text[:start_idx]
    after = text[end_idx:]
    return before + new_block + after


def _validate_markers(text:str) -> None:
    """Check marker presence, uniqueness, and order."""
    start_count = text.count(START_MARKER)
    end_count = text.count(END_MARKER)

    if start_count == 0:
        raise ValueError(
            f"Missing start marker {START_MARKER!r} in README. "
            f"Add it before the generated Usage console block."
        )
    if end_count == 0:
        raise ValueError(
            f"Missing end marker {END_MARKER!r} in README. "
            f"Add it after the generated Usage console block."
        )
    if start_count > 1:
        raise ValueError(
            f"Duplicate start marker {START_MARKER!r}: found {start_count} occurrences. "
            f"Exactly one is required."
        )
    if end_count > 1:
        raise ValueError(
            f"Duplicate end marker {END_MARKER!r}: found {end_count} occurrences. "
            f"Exactly one is required."
        )

    start_pos = text.find(START_MARKER)
    end_pos = text.find(END_MARKER)
    if start_pos > end_pos:
        raise ValueError(
            f"Markers are reversed: start marker found at position {start_pos} "
            f"after end marker at position {end_pos}."
        )


def _strip_ansi(text:str) -> str:
    """Remove ANSI escape sequences from *text*."""
    return _ANSI_RE.sub("", text)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def update_readme() -> None:
    """Read README.md, replace the marked section, and write back.

    Exits with status 1 on validation errors.
    """
    original = README_PATH.read_text(encoding = "utf-8")
    new_block = build_usage_block()

    try:
        updated = replace_marked_section(original, new_block)
    except ValueError as exc:
        print(f"Error: {exc}", file = sys.stderr)
        sys.exit(1)

    README_PATH.write_text(updated, encoding = "utf-8")
    print(f"[OK] Updated {README_PATH}")


def main() -> None:
    update_readme()


if __name__ == "__main__":
    main()
