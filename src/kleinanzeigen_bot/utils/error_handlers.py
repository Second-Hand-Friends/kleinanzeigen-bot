# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import sys, traceback  # isort: skip
from types import FrameType, TracebackType
from typing import Any, Final

from . import loggers

LOG:Final[loggers.Logger] = loggers.get_logger(__name__)


def on_exception(ex_type:type[BaseException], ex_value:Any, ex_traceback:TracebackType | None) -> None:
    if issubclass(ex_type, KeyboardInterrupt):
        sys.__excepthook__(ex_type, ex_value, ex_traceback)
    elif loggers.is_debug(LOG) or isinstance(ex_value, (AttributeError, ImportError, NameError, TypeError)):
        LOG.error("".join(traceback.format_exception(ex_type, ex_value, ex_traceback)))
    elif isinstance(ex_value, AssertionError):
        LOG.error(ex_value)
    else:
        LOG.error("%s: %s", ex_type.__name__, ex_value)


def on_sigint(_sig:int, _frame:FrameType | None) -> None:
    LOG.warning("Aborted on user request.")
    sys.exit(0)
