"""
SPDX-FileCopyrightText: © Jens Bergmann and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

This module contains tests for the __main__.py module.
"""
import sys
from unittest.mock import patch


def test_main_module_execution() -> None:
    """Test that the main module correctly calls kleinanzeigen_bot.main with sys.argv."""
    test_args: list[str] = ['program', '--help']

    with patch('sys.argv', test_args):
        with patch('kleinanzeigen_bot.main') as mock_main:
            # We need to reload the module to trigger the code in __main__.py
            with patch.dict('sys.modules'):
                if 'kleinanzeigen_bot.__main__' in sys.modules:
                    del sys.modules['kleinanzeigen_bot.__main__']
                # This will execute the module
                import kleinanzeigen_bot.__main__  # pylint: disable=import-outside-toplevel,unused-import

                # Verify that kleinanzeigen_bot.main was called with sys.argv
                mock_main.assert_called_once_with(test_args)
