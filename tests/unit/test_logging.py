"""
SPDX-FileCopyrightText: © Jens Bergmann and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""
import logging
from collections.abc import Generator

import pytest

from kleinanzeigen_bot.logging import LOG_ROOT, configure_basic_logging, get_logger


@pytest.fixture(autouse=True)
def clean_logging() -> Generator[None]:
    """Reset logging configuration before and after each test."""
    LOG_ROOT.handlers = []
    logging.Logger.manager.loggerDict.clear()
    yield
    LOG_ROOT.handlers = []
    logging.Logger.manager.loggerDict.clear()


def test_basic_logging(caplog: pytest.LogCaptureFixture) -> None:
    """Test basic logging functionality."""
    caplog.set_level(logging.DEBUG)
    configure_basic_logging(level=logging.DEBUG)
    logger = get_logger("test_logger")
    logger.setLevel(logging.DEBUG)

    test_messages = {
        "debug": "debug message",
        "info": "info message",
        "warning": "warning message",
        "error": "error message"
    }

    logger.debug(test_messages["debug"])
    logger.info(test_messages["info"])
    logger.warning(test_messages["warning"])
    logger.error(test_messages["error"])

    for record in caplog.records:
        if record.name == "test_logger":
            assert record.message == test_messages[record.levelname.lower()]
