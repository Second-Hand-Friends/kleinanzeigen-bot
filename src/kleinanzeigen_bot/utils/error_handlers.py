# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import sys, traceback  # isort: skip
from types import FrameType, TracebackType
from typing import Final

from pydantic import ValidationError

from . import loggers
from .pydantics import format_validation_error

LOG:Final[loggers.Logger] = loggers.get_logger(__name__)


def on_exception(ex_type:type[BaseException] | None, ex:BaseException | None, ex_traceback:TracebackType | None) -> None:
    if ex_type is None or ex is None:
        LOG.error("Unknown exception occurred (missing exception info): ex_type=%s, ex=%s", ex_type, ex)
        return

    if issubclass(ex_type, KeyboardInterrupt):
        sys.__excepthook__(ex_type, ex, ex_traceback)
    elif loggers.is_debug(LOG) or isinstance(ex, (AttributeError, ImportError, NameError, TypeError)):
        LOG.error("".join(traceback.format_exception(ex_type, ex, ex_traceback)))
    elif isinstance(ex, ValidationError):
        LOG.error(format_validation_error(ex))
    elif isinstance(ex, AssertionError):
        LOG.error(ex)
    else:
        LOG.error("%s: %s", ex_type.__name__, ex)
    sys.exit(1)


def on_sigint(_sig:int, _frame:FrameType | None) -> None:
    LOG.warning("Aborted on user request.")
    sys.exit(0)
