# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Tests for the :mod:`kleinanzeigen_bot.utils.color` gating helper."""

from __future__ import annotations

import sys
from typing import Any

import pytest

from kleinanzeigen_bot.utils.color import should_use_color

pytestmark = pytest.mark.unit


class TestShouldUseColor:
    """``should_use_color()`` — pure env/TTY gating policy."""

    # ------------------------------------------------------------------ #
    # TTY detection
    # ------------------------------------------------------------------ #

    def test_tty_true_enables_color(self) -> None:
        """TTY stream with no env overrides → colour enabled."""
        assert should_use_color(stream = _tty(True))

    def test_tty_false_disables_color(self) -> None:
        """Non-TTY stream with no env overrides → colour disabled."""
        assert not should_use_color(stream = _tty(False))

    def test_tty_oserror_disables_color(self) -> None:
        """Stream.isatty() raising OSError → colour disabled."""

        class _Boom:
            def isatty(self) -> bool:  # noqa: PLR6301
                msg = "boom"
                raise OSError(msg)

        assert not should_use_color(stream = _Boom())  # type: ignore[arg-type]

    def test_tty_attributeerror_disables_color(self) -> None:
        """Stream without isatty() → colour disabled."""
        assert not should_use_color(stream = _NoIsATTY())  # type: ignore[arg-type]

    # ------------------------------------------------------------------ #
    # NO_COLOR
    # ------------------------------------------------------------------ #

    def test_no_color_disables_on_tty(self) -> None:
        """NO_COLOR present and non-empty disables colour even on TTY."""
        assert not should_use_color(stream = _tty(True), env = {"NO_COLOR": "1"})

    def test_no_color_empty_ignored(self) -> None:
        """Empty-string NO_COLOR is treated as unset — TTY still enables."""
        assert should_use_color(stream = _tty(True), env = {"NO_COLOR": ""})

    # ------------------------------------------------------------------ #
    # FORCE_COLOR
    # ------------------------------------------------------------------ #

    def test_force_color_enables_on_non_tty(self) -> None:
        """FORCE_COLOR present and non-empty enables colour even on non-TTY."""
        assert should_use_color(stream = _tty(False), env = {"FORCE_COLOR": "1"})

    def test_force_color_empty_ignored(self) -> None:
        """Empty-string FORCE_COLOR is treated as unset — non-TTY stays disabled."""
        assert not should_use_color(stream = _tty(False), env = {"FORCE_COLOR": ""})

    # ------------------------------------------------------------------ #
    # Both env vars
    # ------------------------------------------------------------------ #

    def test_both_no_color_wins(self) -> None:
        """If both NO_COLOR and FORCE_COLOR are non-empty, NO_COLOR wins."""
        assert not should_use_color(
            stream = _tty(False),
            env = {"NO_COLOR": "1", "FORCE_COLOR": "1"},
        )

    def test_both_no_color_wins_even_on_tty(self) -> None:
        """NO_COLOR beats FORCE_COLOR even on TTY."""
        assert not should_use_color(
            stream = _tty(True),
            env = {"NO_COLOR": "1", "FORCE_COLOR": "1"},
        )

    # ------------------------------------------------------------------ #
    # Defaults (no env)
    # ------------------------------------------------------------------ #

    def test_default_env_equals_no_env(self, monkeypatch:pytest.MonkeyPatch) -> None:
        """Calling with env=None behaves the same as empty env dict.

        Clears ambient NO_COLOR/FORCE_COLOR so the os.environ path
        produces the same result as an explicit empty dict.
        """
        monkeypatch.delenv("NO_COLOR", raising = False)
        monkeypatch.delenv("FORCE_COLOR", raising = False)
        assert not should_use_color(stream = _tty(False), env = {})
        assert not should_use_color(stream = _tty(False))

    def test_lazy_default_uses_current_stdout(self, monkeypatch:pytest.MonkeyPatch) -> None:
        """``stream=None`` reads the current ``sys.stdout`` for TTY detection."""
        monkeypatch.delenv("NO_COLOR", raising = False)
        monkeypatch.delenv("FORCE_COLOR", raising = False)

        marker:list[bool] = []

        class _FakeStdout:
            def isatty(self) -> bool:
                marker.append(True)
                return False

        monkeypatch.setattr(sys, "stdout", _FakeStdout())
        assert not should_use_color(env = {})
        assert marker, "sys.stdout.isatty() should be consulted when stream=None"


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #


class _NoIsATTY:
    """Fake stream without an ``isatty`` method."""


def _tty(isatty_val:bool) -> Any:
    """Build a fake stream whose ``isatty()`` returns *isatty_val*."""

    class _FakeTTY:
        def isatty(self) -> bool:
            return isatty_val

    return _FakeTTY()
