"""
SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""
import sys, traceback
from types import FrameType, TracebackType
from typing import Any, Final
import os

from . import loggers

LOG:Final[loggers.Logger] = loggers.get_logger(__name__)


def on_exception(ex_type:type[BaseException], ex_value:Any, ex_traceback:TracebackType | None = None) -> None:
    """Handle exceptions by logging appropriate messages based on exception type.

    Args:
        ex_type: The exception class
        ex_value: The exception instance
        ex_traceback: The traceback object

    This function is designed to be used as a sys.excepthook handler.
    """
    try:
        if issubclass(ex_type, KeyboardInterrupt):
            # Let the default handler deal with KeyboardInterrupt
            sys.__excepthook__(ex_type, ex_value, ex_traceback)
        elif loggers.is_debug(LOG) or isinstance(ex_value, (AttributeError, ImportError, NameError, TypeError)):
            # In debug mode or for common errors, log the full traceback
            LOG.error("".join(traceback.format_exception(ex_type, ex_value, ex_traceback)))
        elif isinstance(ex_value, AssertionError):
            # For assertion errors, just log the message
            LOG.error(ex_value)
        else:
            # For all other errors, log the exception type and message
            LOG.error("%s: %s", ex_type.__name__, ex_value)
    except Exception as e:
        # Ensure we don't crash while handling exceptions
        try:
            LOG.error("Error while handling exception: %s", e)
        except Exception:
            # Last resort if even logging fails - write directly to stderr
            sys.stderr.write(f"CRITICAL: Error in exception handler: {ex_type.__name__}: {ex_value}\n")


def on_sigint(_sig:int, _frame:FrameType | None = None) -> None:
    """Handle SIGINT (Ctrl+C) signals gracefully.

    Args:
        _sig: The signal number
        _frame: The current stack frame

    This function is designed to be used as a signal handler.
    """
    try:
        LOG.warning("Aborted on user request.")
        sys.exit(0)
    except Exception as e:
        # Ensure we don't crash while handling the signal
        try:
            LOG.error("Error while handling SIGINT: %s", e)
            sys.exit(1)  # Exit with error code
        except Exception:
            # Force exit if all else fails - write error to stderr before exiting
            sys.stderr.write("CRITICAL: Error while handling SIGINT. Forcing exit.\n")
            os._exit(1)  # pylint: disable=protected-access
