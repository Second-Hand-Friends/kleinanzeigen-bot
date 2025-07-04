# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import sys, time  # isort: skip
from gettext import gettext as _

import kleinanzeigen_bot
from kleinanzeigen_bot.utils.exceptions import CaptchaEncountered
from kleinanzeigen_bot.utils.launch_mode_guard import ensure_not_launched_from_windows_explorer
from kleinanzeigen_bot.utils.misc import format_timedelta

# --------------------------------------------------------------------------- #
# Refuse GUI/double-click launch on Windows
# --------------------------------------------------------------------------- #
ensure_not_launched_from_windows_explorer()

# --------------------------------------------------------------------------- #
# Main loop: run bot → if captcha → sleep → restart
# --------------------------------------------------------------------------- #
while True:
    try:
        kleinanzeigen_bot.main(sys.argv)  # runs & returns when finished
        sys.exit(0)  # not using `break` to prevent process closing issues
    except CaptchaEncountered as ex:
        delay = ex.restart_delay
        print(_("[INFO] Captcha detected. Sleeping %s before restart...") % format_timedelta(delay))
        time.sleep(delay.total_seconds())
        # loop continues and starts a fresh run
