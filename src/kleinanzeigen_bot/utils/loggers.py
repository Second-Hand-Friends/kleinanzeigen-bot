# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import copy, logging, os, re, sys  # isort: skip
from gettext import gettext as _
from logging import CRITICAL, DEBUG, ERROR, INFO, WARNING, Logger
from logging.handlers import RotatingFileHandler
from typing import Any, Final  # @UnusedImport

import colorama

from . import i18n, reflect

__all__ = [
    "Logger",
    "LogFileHandle",
    "DEBUG",
    "INFO",
    "WARNING",
    "ERROR",
    "CRITICAL",
    "configure_console_logging",
    "configure_file_logging",
    "flush_all_handlers",
    "get_logger",
    "is_debug"
]

LOG_ROOT:Final[logging.Logger] = logging.getLogger()


class _MaxLevelFilter(logging.Filter):

    def __init__(self, level:int) -> None:
        super().__init__()
        self.level = level

    def filter(self, record:logging.LogRecord) -> bool:
        return record.levelno <= self.level


def configure_console_logging() -> None:
    # if a StreamHandler already exists, do not append it again
    if any(isinstance(h, logging.StreamHandler) for h in LOG_ROOT.handlers):
        return

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

        def _relativize_paths_under_cwd(self, record:logging.LogRecord) -> None:
            """
            Mutate record.args in-place, converting any absolute-path strings
            under the current working directory into relative paths.
            """

            if not record.args:
                return

            cwd = os.getcwd()

            def _rel_if_subpath(val:Any) -> Any:
                if isinstance(val, str) and os.path.isabs(val):
                    # don't relativize log-file paths
                    if val.endswith(".log"):
                        return val

                    try:
                        if os.path.commonpath([cwd, val]) == cwd:
                            return os.path.relpath(val, cwd)
                    except ValueError:
                        return val
                return val

            if isinstance(record.args, tuple):
                record.args = tuple(_rel_if_subpath(a) for a in record.args)
            elif isinstance(record.args, dict):
                record.args = {k: _rel_if_subpath(v) for k, v in record.args.items()}

        def format(self, record:logging.LogRecord) -> str:
            # Deep copy fails if record.args contains objects with
            # __init__(...) parameters (e.g., CaptchaEncountered).
            # A shallow copy is sufficient to preserve the original.
            record = copy.copy(record)

            self._relativize_paths_under_cwd(record)

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
    stdout_log.addFilter(_MaxLevelFilter(INFO))
    stdout_log.setFormatter(formatter)
    LOG_ROOT.addHandler(stdout_log)

    stderr_log = logging.StreamHandler(sys.stderr)
    stderr_log.setLevel(WARNING)
    stderr_log.setFormatter(formatter)
    LOG_ROOT.addHandler(stderr_log)


class LogFileHandle:
    """Encapsulates a log file handler with close and status methods."""

    def __init__(self, file_path:str, handler:RotatingFileHandler, logger:logging.Logger) -> None:
        self.file_path = file_path
        self._handler:RotatingFileHandler | None = handler
        self._logger = logger

    def close(self) -> None:
        """Flushes, removes, and closes the log handler."""
        if self._handler:
            self._handler.flush()
            self._logger.removeHandler(self._handler)
            self._handler.close()
            self._handler = None

    def is_closed(self) -> bool:
        """Returns whether the log handler has been closed."""
        return not self._handler


def configure_file_logging(log_file_path:str) -> LogFileHandle:
    """
    Sets up a file logger and returns a callable to flush, remove, and close it.

    @param log_file_path: Path to the log file.
    @return: Callable[[], None]: A function that cleans up the log handler.
    """
    fh = RotatingFileHandler(
        filename = log_file_path,
        maxBytes = 10 * 1024 * 1024,  # 10 MB
        backupCount = 10,
        encoding = "utf-8"
    )
    fh.setLevel(DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    LOG_ROOT.addHandler(fh)
    return LogFileHandle(log_file_path, fh, LOG_ROOT)


def flush_all_handlers() -> None:
    for handler in LOG_ROOT.handlers:
        handler.flush()


def get_logger(name:str | None = None) -> logging.Logger:
    """
    Returns a localized logger
    """

    class TranslatingLogger(logging.Logger):

        def _log(self, level:int, msg:object, *args:Any, **kwargs:Any) -> None:
            if level != DEBUG:  # debug messages should not be translated
                msg = i18n.translate(msg, reflect.get_caller(2))
            super()._log(level, msg, *args, **kwargs)

    logging.setLoggerClass(TranslatingLogger)
    return logging.getLogger(name)


def is_debug(logger:Logger) -> bool:
    return logger.isEnabledFor(DEBUG)
