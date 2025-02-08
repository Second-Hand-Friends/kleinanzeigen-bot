"""
SPDX-FileCopyrightText: © Jens Bergmann and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""
import logging

__all__ = [
    "LOG_ROOT",
    "Logger",
    "get_logger",
    "configure_basic_logging",
    # Re-export logging constants
    "DEBUG",
    "INFO",
    "WARNING",
    "ERROR",
    "CRITICAL",
    "FileHandler",
    "Formatter",
]

# Re-export logging constants and classes
DEBUG = logging.DEBUG
INFO = logging.INFO
WARNING = logging.WARNING
ERROR = logging.ERROR
CRITICAL = logging.CRITICAL
FileHandler = logging.FileHandler
Formatter = logging.Formatter

# Re-export Logger class
Logger = logging.Logger
LOG_ROOT: logging.Logger = logging.getLogger()


def get_logger(name: str | None = None) -> logging.Logger:
    """
    Get or create a logger instance.
    Args:
        name: The name of the logger. If None, returns the root logger.
    Returns:
        A logger instance.
    """
    return logging.getLogger(name)


def configure_basic_logging(level: int = logging.INFO) -> None:
    """
    Configure basic logging with a console handler.
    Args:
        level: The logging level to use.
    """
    LOG_ROOT.setLevel(level)
