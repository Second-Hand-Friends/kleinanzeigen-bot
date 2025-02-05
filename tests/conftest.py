"""
SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""
import logging
from typing import Final

from kleinanzeigen_bot import utils
from kleinanzeigen_bot.i18n import get_translating_logger

utils.configure_console_logging()

LOG:Final[logging.Logger] = get_translating_logger("kleinanzeigen_bot")
LOG.setLevel(logging.DEBUG)
