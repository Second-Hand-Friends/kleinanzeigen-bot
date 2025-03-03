"""
SPDX-FileCopyrightText: © Jens Bergmann and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

Tests for KleinanzeigenBot localization and help text functionality.
"""
from typing import Protocol
from unittest.mock import patch

# Use Protocol for KleinanzeigenBot to provide better type hints


class KleinanzeigenBot(Protocol):
    """Protocol for KleinanzeigenBot class to provide type hints."""

    def show_help(self) -> None:
        """Show help text."""


def test_show_help_displays_german_text(test_bot: KleinanzeigenBot) -> None:
    """Verify that help text is displayed in German when language is German."""
    with patch('kleinanzeigen_bot.get_current_locale') as mock_locale, \
            patch('builtins.print') as mock_print:
        mock_locale.return_value.language = "de"
        test_bot.show_help()
        printed_text = ''.join(str(call.args[0]) for call in mock_print.call_args_list)
        assert "Verwendung:" in printed_text
        assert "Befehle:" in printed_text


def test_show_help_displays_english_text(test_bot: KleinanzeigenBot) -> None:
    """Verify that help text is displayed in English when language is English."""
    with patch('kleinanzeigen_bot.get_current_locale') as mock_locale, \
            patch('builtins.print') as mock_print:
        mock_locale.return_value.language = "en"
        test_bot.show_help()
        printed_text = ''.join(str(call.args[0]) for call in mock_print.call_args_list)
        assert "Usage:" in printed_text
        assert "Commands:" in printed_text
