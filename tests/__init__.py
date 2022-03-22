"""
Copyright (C) 2022 Sebastian Thomschke and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
"""
import logging
from typing import Final

from kleinanzeigen_bot import utils

utils.configure_console_logging()

LOG:Final[logging.Logger] = logging.getLogger("kleinanzeigen_bot")
LOG.setLevel(logging.DEBUG)
