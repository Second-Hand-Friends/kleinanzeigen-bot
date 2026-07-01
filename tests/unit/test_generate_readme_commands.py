# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Unit tests for scripts/generate_readme_commands.py helpers."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from kleinanzeigen_bot import runtime_config
from kleinanzeigen_bot.utils.i18n import Locale, get_current_locale, set_current_locale

# Ensure scripts/ is importable as a top-level module, matching the import style
# used by scripts/check_generated_artifacts.py and avoiding the mypy
# double-module mapping ("generate_readme_commands" vs "scripts.generate_readme_commands").
_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from generate_readme_commands import (  # noqa: E402  # pyright: ignore[reportMissingImports]
    END_MARKER,
    START_MARKER,
    build_usage_block,
    replace_marked_section,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Marker replacement — success cases
# ---------------------------------------------------------------------------


class TestReplaceMarkers:
    def test_replaces_content_between_markers(self) -> None:
        text = f"before\n{START_MARKER}\nold\n{END_MARKER}\nafter"
        result = replace_marked_section(text, "\nNEW\n")
        assert result == f"before\n{START_MARKER}\nNEW\n{END_MARKER}\nafter"

    def test_preserves_surrounding_context(self) -> None:
        text = f"HEADER\n{START_MARKER}\nSTALE\n{END_MARKER}\nFOOTER"
        result = replace_marked_section(text, "\nFRESH\n")
        assert "HEADER" in result
        assert "FOOTER" in result
        assert "STALE" not in result

    def test_noop_when_content_identical(self) -> None:
        block = "\n```console\nUsage: kleinanzeigen-bot\n```\n"
        text = f"preamble\n{START_MARKER}{block}{END_MARKER}\npostamble"
        result = replace_marked_section(text, block)
        assert result == text


# ---------------------------------------------------------------------------
# Marker validation — failure cases
# ---------------------------------------------------------------------------


class TestReplaceMarkersErrors:
    @pytest.mark.parametrize(
        ("text", "expected_substring"),
        [
            pytest.param(
                "no markers here",
                "Missing start marker",
                id = "missing-start",
            ),
            pytest.param(
                f"only {END_MARKER}",
                "Missing start marker",
                id = "missing-start-but-has-end",
            ),
            pytest.param(
                f"{START_MARKER}\nno end",
                "Missing end marker",
                id = "missing-end",
            ),
            pytest.param(
                f"{END_MARKER}\nno start",
                "Missing start marker",
                id = "missing-both-start-actually-end-first",
            ),
            pytest.param(
                f"{START_MARKER}\n{START_MARKER}\n{END_MARKER}",
                "Duplicate start marker",
                id = "duplicate-start",
            ),
            pytest.param(
                f"{START_MARKER}\n{END_MARKER}\n{END_MARKER}",
                "Duplicate end marker",
                id = "duplicate-end",
            ),
            pytest.param(
                f"{END_MARKER}\n{START_MARKER}",
                "reversed",
                id = "reversed-markers",
            ),
        ],
    )
    def test_raises_value_error(self, text:str, expected_substring:str) -> None:
        with pytest.raises(ValueError, match = expected_substring):
            replace_marked_section(text, "\nBLOCK\n")


# ---------------------------------------------------------------------------
# Generated help block — normalization
# ---------------------------------------------------------------------------


class TestBuildUsageBlock:
    def test_contains_expected_executable_name(self) -> None:
        block = build_usage_block()
        assert "Usage: kleinanzeigen-bot COMMAND [OPTIONS]" in block

    def test_contains_no_ansi_escape_codes(self) -> None:
        block = build_usage_block()
        assert "\x1b[" not in block

    def test_wrapped_in_console_fence(self) -> None:
        block = build_usage_block()
        assert block.startswith("\n```console\n")
        assert block.endswith("\n```\n")

    def test_is_english_regardless_of_current_locale(self) -> None:
        """Force German locale, then verify generated help is still English."""
        saved = get_current_locale()
        try:
            set_current_locale(Locale("de"))
            block = build_usage_block()
            assert "Commands:" in block
            assert "Befehle:" not in block
        finally:
            set_current_locale(saved)


# ---------------------------------------------------------------------------
# Generated help — command coverage
# ---------------------------------------------------------------------------


class TestBuildUsageBlockCommands:
    """Verify that generated help covers the known command set."""

    def test_every_valid_command_appears_in_generated_help(self) -> None:
        block = build_usage_block()
        for cmd in runtime_config.VALID_COMMANDS:
            assert cmd in block, (
                f"Command {cmd!r} is in VALID_COMMANDS but missing from generated help"
            )
