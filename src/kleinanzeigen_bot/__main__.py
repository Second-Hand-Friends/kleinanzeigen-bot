"""
SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""
import sys, time
from pathlib import Path
from gettext import gettext as _

import ruamel.yaml as _yaml

import kleinanzeigen_bot
from kleinanzeigen_bot.utils.exceptions import CaptchaEncountered

DEFAULT_DELAY_H = 6


def _get_cfg_path(argv: list[str]) -> Path:
    """Return --config=<path> if present, else ./config.yaml."""
    for arg in argv:
        if arg.startswith("--config="):
            return Path(arg.split("=", 1)[1])
    return Path("config.yaml")


def _read_restart_delay(fallback: int = DEFAULT_DELAY_H) -> int:
    """Read captcha.restart_delay_h from YAML config or return fallback."""
    cfg_file = _get_cfg_path(sys.argv)
    try:
        with cfg_file.open(encoding = "utf-8") as fh:
            data = _yaml.YAML(typ = "safe").load(fh) or {}
        return int(data.get("captcha", {}).get("restart_delay_h", fallback))
    except Exception as ex:  # noqa: BLE001
        print(f"[WARN] Config read error ({ex}) – falling back to {fallback} h")
        return fallback


# --------------------------------------------------------------------------- #
# Main loop: run bot → if captcha → sleep → restart
# --------------------------------------------------------------------------- #
while True:
    try:
        kleinanzeigen_bot.main(sys.argv)  # runs & returns when finished
        sys.exit(0)  # not using `break` to prevent process closing issues

    except CaptchaEncountered:
        delay_h = _read_restart_delay()
        print(_("[INFO] Captcha detected. Sleeping %(h)s h before restart...") % {"h": delay_h})
        time.sleep(delay_h * 3600)
        # loop continues and starts a fresh run
