# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import importlib
import sys
from typing import Final

import colorama

from .app import KleinanzeigenBot as KleinanzeigenBot
from .utils import loggers as _loggers

LOG:Final[_loggers.Logger] = _loggers.get_logger(__name__)
LOG.setLevel(_loggers.INFO)

colorama.just_fix_windows_console()


def main(args:list[str]) -> None:
    _cli = importlib.import_module(".cli", __name__)
    _cli.main(args)


if __name__ == "__main__":
    _loggers.configure_console_logging()
    LOG.error("Direct execution not supported. Use 'pdm run app'")
    sys.exit(1)
