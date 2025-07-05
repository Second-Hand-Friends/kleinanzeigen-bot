# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import ctypes, sys  # isort: skip

from kleinanzeigen_bot.utils.i18n import get_current_locale
from kleinanzeigen_bot.utils.misc import is_frozen


def _is_launched_from_windows_explorer() -> bool:
    """
    Returns True if this process is the *only* one attached to the console,
    i.e. the user started us by double-clicking in Windows Explorer.
    """
    if not is_frozen():
        return False  # Only relevant when compiled exe

    if sys.platform != "win32":
        return False  # Only relevant on Windows

    # Allocate small buffer for at most 3 PIDs
    DWORD = ctypes.c_uint
    pids = (DWORD * 3)()
    n = int(ctypes.windll.kernel32.GetConsoleProcessList(pids, 3))
    return n <= 2  # our PID (+ maybe conhost.exe) -> console dies with us  # noqa: PLR2004  # Magic value used in comparison


def ensure_not_launched_from_windows_explorer() -> None:
    """
    Terminates the application if the EXE was started by double-clicking in Windows Explorer
    instead of from a terminal (cmd.exe / PowerShell).
    """

    if not _is_launched_from_windows_explorer():
        return

    if get_current_locale().language == "de":
        banner = (
            "\n"
            "  ┌─────────────────────────────────────────────────────────────┐\n"
            "  │  Kleinanzeigen-Bot ist ein *Kommandozeilentool*.            │\n"
            "  │                                                             │\n"
            "  │  Du hast das Programm scheinbar per Doppelklick gestartet.  │\n"
            "  │                                                             │\n"
            "  │  ->  Bitte starte es stattdessen in einem Terminal:         │\n"
            "  │                                                             │\n"
            "  │      kleinanzeigen-bot.exe [OPTIONEN]                       │\n"
            "  │                                                             │\n"
            "  │  Schneller Weg, ein Terminal zu öffnen:                     │\n"
            "  │    1. Drücke Win + R, gib cmd ein und drücke Enter.         │\n"
            "  │    2. Wechsle per `cd` in das Verzeichnis mit dieser Datei. │\n"
            "  │    3. Gib den obigen Befehl ein und drücke Enter.           │\n"
            "  │                                                             │\n"
            "  │─────────────────────────────────────────────────────────────│\n"
            "  │  Drücke <Enter>, um dieses Fenster zu schließen.            │\n"
            "  └─────────────────────────────────────────────────────────────┘\n"
        )
    else:
        banner = (
            "\n"
            "  ┌─────────────────────────────────────────────────────────────┐\n"
            "  │  Kleinanzeigen-Bot is a *command-line* tool.                │\n"
            "  │                                                             │\n"
            "  │  It looks like you launched it by double-clicking the EXE.  │\n"
            "  │                                                             │\n"
            "  │  ->  Please run it from a terminal instead:                 │\n"
            "  │                                                             │\n"
            "  │      kleinanzeigen-bot.exe [OPTIONS]                        │\n"
            "  │                                                             │\n"
            "  │  Quick way to open a terminal:                              │\n"
            "  │    1. Press  Win + R , type  cmd  and press Enter.          │\n"
            "  │    2. cd to the folder that contains this file.             │\n"
            "  │    3. Type the command above and press Enter.               │\n"
            "  │                                                             │\n"
            "  │─────────────────────────────────────────────────────────────│\n"
            "  │  Press <Enter> to close this window.                        │\n"
            "  └─────────────────────────────────────────────────────────────┘\n"
        )

    print(banner, file = sys.stderr, flush = True)
    input()  # keep window open
    sys.exit(1)
