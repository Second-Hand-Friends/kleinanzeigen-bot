"""
SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""
import copy, logging, re, sys
from gettext import gettext as _
from logging import Logger, DEBUG, INFO, WARNING, ERROR, CRITICAL
from logging.handlers import RotatingFileHandler
from typing import Any, Final

import colorama
from . import i18n, reflect

__all__ = [
    "Logger",
    "LogFileHandle",
    "DEBUG",
    "INFO",
    "configure_console_logging",
    "configure_file_logging",
    "flush_all_handlers",
    "get_logger",
    "is_debug"
]

LOG_ROOT:Final[logging.Logger] = logging.getLogger()


def configure_console_logging() -> None:

    class CustomFormatter(logging.Formatter):
        LEVEL_COLORS = {
            DEBUG: colorama.Fore.BLACK + colorama.Style.BRIGHT,
            INFO: colorama.Fore.BLACK + colorama.Style.BRIGHT,
            WARNING: colorama.Fore.YELLOW,
            ERROR: colorama.Fore.RED,
            CRITICAL: colorama.Fore.RED,
        }
        MESSAGE_COLORS = {
            DEBUG: colorama.Fore.BLACK + colorama.Style.BRIGHT,
            INFO: colorama.Fore.RESET,
            WARNING: colorama.Fore.YELLOW,
            ERROR: colorama.Fore.RED,
            CRITICAL: colorama.Fore.RED + colorama.Style.BRIGHT,
        }
        VALUE_COLORS = {
            DEBUG: colorama.Fore.BLACK + colorama.Style.BRIGHT,
            INFO: colorama.Fore.MAGENTA,
            WARNING: colorama.Fore.MAGENTA,
            ERROR: colorama.Fore.MAGENTA,
            CRITICAL: colorama.Fore.MAGENTA,
        }

        def format(self, record:logging.LogRecord) -> str:
            record = copy.deepcopy(record)

            level_color = self.LEVEL_COLORS.get(record.levelno, "")
            msg_color = self.MESSAGE_COLORS.get(record.levelno, "")
            value_color = self.VALUE_COLORS.get(record.levelno, "")

            # translate and colorize log level name
            levelname = _(record.levelname) if record.levelno > DEBUG else record.levelname
            record.levelname = f"{level_color}[{levelname}]{colorama.Style.RESET_ALL}"

            # highlight message values enclosed by [...], "...", and '...'
            record.msg = re.sub(
                r"\[([^\]]+)\]|\"([^\"]+)\"|\'([^\']+)\'",
                lambda match: f"[{value_color}{match.group(1) or match.group(2) or match.group(3)}{colorama.Fore.RESET}{msg_color}]",
                str(record.msg),
            )

            # colorize message
            record.msg = f"{msg_color}{record.msg}{colorama.Style.RESET_ALL}"

            return super().format(record)

    formatter = CustomFormatter("%(levelname)s %(message)s")

    stdout_log = logging.StreamHandler(sys.stderr)
    stdout_log.setLevel(DEBUG)
    stdout_log.addFilter(type("", (logging.Filter,), {
        "filter": lambda rec: rec.levelno <= INFO
    }))
    stdout_log.setFormatter(formatter)
    LOG_ROOT.addHandler(stdout_log)

    stderr_log = logging.StreamHandler(sys.stderr)
    stderr_log.setLevel(WARNING)
    stderr_log.setFormatter(formatter)
    LOG_ROOT.addHandler(stderr_log)


class LogFileHandle:
    """Handle for a log file handler."""

    def __init__(
        self,
        handler: logging.FileHandler | RotatingFileHandler,
        log_file_path: str,
    ):
        """Initialize the log file handle.

        Args:
            handler: The log file handler.
            log_file_path: The path to the log file.
        """
        self.handler: logging.FileHandler | RotatingFileHandler | None = handler
        self.log_file_path = log_file_path

    def close(self) -> None:
        """Flushes, removes, and closes the log handler."""
        if self.handler:
            self.handler.flush()
            LOG_ROOT.removeHandler(self.handler)
            self.handler.close()
            self.handler = None

    def is_closed(self) -> bool:
        """Returns whether the log handler has been closed."""
        return not self.handler


def configure_file_logging(log_file_path:str) -> LogFileHandle:
    """
    Sets up a file logger and returns a callable to flush, remove, and close it.

    @param log_file_path: Path to the log file.
    @return: LogFileHandle: An object that can be used to clean up the log handler.
    """
    # Use standard FileHandler instead of RotatingFileHandler to avoid compatibility issues with Python 3.13
    fh = logging.FileHandler(
        filename=log_file_path,
        encoding="utf-8",
        mode="a"
    )
    fh.setLevel(DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    LOG_ROOT.addHandler(fh)
    return LogFileHandle(fh, log_file_path)


def flush_all_handlers() -> None:
    for handler in LOG_ROOT.handlers:
        handler.flush()


def get_logger(name: str | None = None) -> logging.Logger:
    """
    Returns a localized logger
    """

    class TranslatingLogger(logging.Logger):

        def _log(self, level: int, msg: object, *args: Any, **kwargs: Any) -> None:
            if level != DEBUG:  # debug messages should not be translated
                msg = i18n.translate(msg, reflect.get_caller(2))
            super()._log(level, msg, *args, **kwargs)

    logging.setLoggerClass(TranslatingLogger)
    return logging.getLogger(name)


def is_debug(logger:Logger) -> bool:
    return logger.isEnabledFor(DEBUG)
