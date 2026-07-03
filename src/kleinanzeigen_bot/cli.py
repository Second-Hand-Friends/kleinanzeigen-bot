# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""CLI bootstrap and argument parsing for kleinanzeigen-bot.

Handles argument parsing via :func:`parse_args`, signal handling,
daemonizing or foreground execution, log and locale setup, and
dispatches to :class:`KleinanzeigenBot <kleinanzeigen_bot.app.KleinanzeigenBot>`.

Primary entry point: :func:`main`.
"""
from __future__ import annotations

import atexit
import getopt
import importlib.metadata
import os
import signal
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Sequence

import colorama
import nodriver

from kleinanzeigen_bot._version import __version__
from kleinanzeigen_bot.app import KleinanzeigenBot
from kleinanzeigen_bot.runtime_config import VALID_COMMANDS
from kleinanzeigen_bot.utils import error_handlers as _error_handlers
from kleinanzeigen_bot.utils import loggers as _loggers
from kleinanzeigen_bot.utils.exceptions import CaptchaEncountered
from kleinanzeigen_bot.utils.files import abspath
from kleinanzeigen_bot.utils.i18n import Locale, get_current_locale, set_current_locale
from kleinanzeigen_bot.utils.misc import is_frozen

LOG:Final[_loggers.Logger] = _loggers.get_logger(__name__)
LOG.setLevel(_loggers.INFO)

# Keep this marker and warning guidance in sync with scripts/fix_nodriver.py
# and the README source-install caveat.
_NODRIVER_PATCH_MARKER:Final[str] = "KLEINANZEIGEN_BOT_NODEDRIVER_CDP_REATTACH_PATCH_V1"
_warned_nodriver_patch:list[bool] = [False]


@dataclass(slots = True)
class ParsedArgs:
    command:str = "help"
    ads_selector:str = "due"
    ads_selector_explicit:bool = False
    keep_old_ads:bool = False
    preserve_local_settings:bool = False
    config_arg:str | None = None
    config_file_path:str | None = None
    logfile_arg:str | None = None
    log_file_path:str | None = None
    logfile_explicitly_provided:bool = False
    workspace_mode:str | None = None


def _warn_unpatched_nodriver() -> None:
    """Check installed nodriver for the CDP re-attach patch marker and warn if missing.

    Keeps a module-level flag so repeated ``main()`` calls only warn once.
    Silent on frozen builds, uninstalled nodriver, missing/unreadable files,
    or any metadata weirdness.
    """
    if _warned_nodriver_patch[0] or is_frozen():
        return

    try:
        dist = importlib.metadata.distribution("nodriver")
    except importlib.metadata.PackageNotFoundError:
        return
    except Exception:
        if _loggers.is_debug(LOG):
            LOG.debug("nodriver patch check: metadata lookup failed", exc_info = True)
        return

    try:
        try:
            connection_path = Path(dist.locate_file("nodriver/core/connection.py"))  # type: ignore[arg-type]
        except AttributeError:
            # Fallback for older Python/importlib_metadata.
            site_packages = Path(dist._path).parent  # type: ignore[attr-defined]  # noqa: SLF001
            connection_path = site_packages / "nodriver/core/connection.py"
    except Exception:
        if _loggers.is_debug(LOG):
            LOG.debug("nodriver patch check: path resolution failed", exc_info = True)
        return

    if not connection_path.is_file():
        return
    try:
        text = connection_path.read_text("utf-8")
    except OSError:
        return

    if _NODRIVER_PATCH_MARKER in text:
        return

    _warned_nodriver_patch[0] = True
    LOG.warning(
        "nodriver CDP re-attach patch not found: installed nodriver may miss the flat-mode fix. "
        "Plain pip installs skip the PDM post_install hook; run `pdm install` from a source "
        "checkout or `python scripts/fix_nodriver.py` from the repository. "
        "Symptom: repeated `Re-attaching CDP session after -32601`."
    )


def _help_executable() -> str:
    if is_frozen():
        return sys.argv[0]
    if os.getenv("PDM_PROJECT_ROOT", ""):
        return "pdm run app"
    return "python -m kleinanzeigen_bot"


def help_text(*, executable:str | None = None, language:str | None = None) -> str:
    exe = executable if executable is not None else _help_executable()
    lang = language if language is not None else get_current_locale().language
    if lang == "de":
        return textwrap.dedent(
            f"""\
            Verwendung: {colorama.Fore.LIGHTMAGENTA_EX}{exe} BEFEHL [OPTIONEN]{colorama.Style.RESET_ALL}

            Befehle:
              publish  - (Wieder-)Veröffentlicht Anzeigen
              verify   - Überprüft die Konfigurationsdateien und Ad-Integrität
              delete   - Löscht Anzeigen
              update   - Aktualisiert bestehende Anzeigen
              extend   - Verlängert Anzeigen im 8-Tage-Zeitfenster (behält Beobachter/Interessenten bei und zählt nicht zum monatlichen Anzeigenkontingent)
              download - Lädt eine oder mehrere Anzeigen herunter
              update-check - Prüft auf verfügbare Updates
              update-content-hash - Berechnet den content_hash aller Anzeigen anhand der aktuellen ad_defaults neu;
                                    nach Änderungen an den config.yaml/ad_defaults verhindert es, dass alle Anzeigen als
                                    "geändert" gelten und neu veröffentlicht werden.
              create-config - Erstellt eine neue Standard-Konfigurationsdatei, falls noch nicht vorhanden
              diagnose - Diagnostiziert Browser-Verbindungsprobleme und zeigt Troubleshooting-Informationen
              status   - Zeigt Anzeigenstatus und APR-Vorschau an
              --
              help     - Zeigt diese Hilfe an (Standardbefehl)
              version  - Zeigt die Version der Anwendung an

            Optionen:
              --ads=all|due|new|changed|<id(s)> (publish) - Gibt an, welche Anzeigen (erneut) veröffentlicht werden sollen (STANDARD: due)
                    Mögliche Werte:
                    * all: Veröffentlicht alle Anzeigen erneut, ignoriert republication_interval
                    * due: Veröffentlicht alle neuen Anzeigen und erneut entsprechend dem republication_interval
                    * new: Veröffentlicht nur neue Anzeigen (d.h. Anzeigen ohne ID in der Konfigurationsdatei)
                    * changed: Veröffentlicht nur Anzeigen, die seit der letzten Veröffentlichung geändert wurden
                    * <id(s)>: Gibt eine oder mehrere Anzeigen-IDs an, die veröffentlicht werden sollen, z. B. "--ads=1,2,3", ignoriert republication_interval
                    * Kombinationen: Sie können mehrere Selektoren mit Kommas kombinieren, z. B. "--ads=changed,due" um sowohl geänderte als auch
                      fällige Anzeigen zu veröffentlichen
              --ads=all|new|<id(s)> (download) - Gibt an, welche Anzeigen heruntergeladen werden sollen (STANDARD: new)
                    Mögliche Werte:
                    * all: Lädt alle Anzeigen aus Ihrem Profil herunter
                    * new: Lädt Anzeigen aus Ihrem Profil herunter, die lokal noch nicht gespeichert sind
                    * <id(s)>: Gibt eine oder mehrere Anzeigen-IDs zum Herunterladen an, z. B. "--ads=1,2,3"
              --ads=all|changed|<id(s)> (update) - Gibt an, welche Anzeigen aktualisiert werden sollen (STANDARD: changed)
                    Mögliche Werte:
                    * all: Aktualisiert alle Anzeigen
                    * changed: Aktualisiert nur Anzeigen, die seit der letzten Veröffentlichung geändert wurden
                    * <id(s)>: Gibt eine oder mehrere Anzeigen-IDs zum Aktualisieren an, z. B. "--ads=1,2,3"
              --ads=all|<id(s)> (extend) - Gibt an, welche Anzeigen verlängert werden sollen (STANDARD: all)
                    Mögliche Werte:
                    * all: Verlängert alle Anzeigen, die innerhalb von 8 Tagen ablaufen
                    * <id(s)>: Gibt bestimmte Anzeigen-IDs an, z. B. "--ads=1,2,3"
                    * Hinweis: Anzeigen außerhalb des 8-Tage-Fensters werden übersprungen.
              --force           - Alias für '--ads=all'
              --keep-old        - Verhindert das Löschen alter Anzeigen bei erneuter Veröffentlichung
              --preserve-local-settings - Erzwingt das Beibehalten lokaler Einstellungen bei erneutem Download (überschreibt config-Wert false)
              --config=<PATH>   - Pfad zur YAML- oder JSON-Konfigurationsdatei (ändert den Workspace-Modus nicht implizit)
              --workspace-mode=portable|xdg - Überschreibt den Workspace-Modus für diesen Lauf
              --logfile=<PATH>  - Pfad zur Protokolldatei (STANDARD: vom aktiven Workspace-Modus abhängig)
              --lang=en|de      - Anzeigesprache (STANDARD: Systemsprache, wenn unterstützt, sonst Englisch)
              -v, --verbose     - Aktiviert detaillierte Ausgabe – nur nützlich zur Fehlerbehebung
            """.rstrip()
        )

    return textwrap.dedent(
        f"""\
        Usage: {colorama.Fore.LIGHTMAGENTA_EX}{exe} COMMAND [OPTIONS]{colorama.Style.RESET_ALL}

        Commands:
          publish  - (re-)publishes ads
          verify   - verifies the configuration files and ad integrity
          delete   - deletes ads
          update   - updates published ads
          extend   - extends ads within the 8-day window before expiry (keeps watchers/savers and does not count towards the monthly ad quota)
          download - downloads one or multiple ads
          update-check - checks for available updates
          update-content-hash – recalculates each ad's content_hash based on the current ad_defaults;
                                use this after changing config.yaml/ad_defaults to avoid every ad being marked "changed" and republished
          create-config - creates a new default configuration file if one does not exist
          diagnose - diagnoses browser connection issues and shows troubleshooting information
          status   - shows ad status and APR preview details
          --
          help     - displays this help (default command)
          version  - displays the application version

        Options:
          --ads=all|due|new|changed|<id(s)> (publish) - specifies which ads to (re-)publish (DEFAULT: due)
                Possible values:
                * all: (re-)publish all ads ignoring republication_interval
                * due: publish all new ads and republish ads according the republication_interval
                * new: only publish new ads (i.e. ads that have no id in the config file)
                * changed: only publish ads that have been modified since last publication
                * <id(s)>: provide one or several ads by ID to (re-)publish, like e.g. "--ads=1,2,3" ignoring republication_interval
                * Combinations: You can combine multiple selectors with commas, e.g. "--ads=changed,due" to publish both changed and due ads
          --ads=all|new|<id(s)> (download) - specifies which ads to download (DEFAULT: new)
                Possible values:
                * all: downloads all ads from your profile
                * new: downloads ads from your profile that are not locally saved yet
                * <id(s)>: provide one or several ads by ID to download, like e.g. "--ads=1,2,3"
          --ads=all|changed|<id(s)> (update) - specifies which ads to update (DEFAULT: changed)
                Possible values:
                * all: update all ads
                * changed: only update ads that have been modified since last publication
                * <id(s)>: provide one or several ads by ID to update, like e.g. "--ads=1,2,3"
          --ads=all|<id(s)> (extend) - specifies which ads to extend (DEFAULT: all)
                Possible values:
                * all: extend all ads expiring within 8 days
                * <id(s)>: specify ad IDs to extend, e.g. "--ads=1,2,3"
                * Note: ads outside the 8-day window are skipped.
          --force           - alias for '--ads=all'
          --keep-old        - don't delete old ads on republication
          --preserve-local-settings - force-enable preservation of local-only settings on re-download (overrides config value of false)
          --config=<PATH>   - path to the config YAML or JSON file (does not implicitly change workspace mode)
          --workspace-mode=portable|xdg - overrides workspace mode for this run
          --logfile=<PATH>  - path to the logfile (DEFAULT: depends on active workspace mode)
          --lang=en|de      - display language (STANDARD: system language if supported, otherwise English)
          -v, --verbose     - enables verbose output - only useful when troubleshooting issues
        """.rstrip()
    )


def show_help() -> None:
    print(help_text())


def parse_args(args:Sequence[str]) -> ParsedArgs:
    parsed = ParsedArgs()
    help_requested = False
    try:
        options, arguments = getopt.gnu_getopt(
            list(args)[1:],
            "hv",
            ["ads=", "config=", "force", "help", "keep-old", "logfile=", "lang=", "preserve-local-settings", "verbose", "workspace-mode="],
        )
    except getopt.error as ex:
        LOG.error(ex.msg)
        LOG.error("Use --help to display available options.")
        sys.exit(2)

    for option, value in options:
        match option:
            case "-h" | "--help":
                help_requested = True
            case "--config":
                parsed.config_file_path = abspath(value)
                parsed.config_arg = value
            case "--logfile":
                parsed.logfile_arg = value
                parsed.logfile_explicitly_provided = True
                parsed.log_file_path = abspath(value) if value else None
            case "--workspace-mode":
                mode = value.strip().lower()
                if mode not in {"portable", "xdg"}:
                    LOG.error("Invalid --workspace-mode '%s'. Use 'portable' or 'xdg'.", value)
                    sys.exit(2)
                parsed.workspace_mode = mode
            case "--ads":
                parsed.ads_selector = value.strip().lower()
                parsed.ads_selector_explicit = True
            case "--force":
                parsed.ads_selector = "all"
                parsed.ads_selector_explicit = True
            case "--keep-old":
                parsed.keep_old_ads = True
            case "--preserve-local-settings":
                parsed.preserve_local_settings = True
            case "--lang":
                set_current_locale(Locale.of(value))
            case "-v" | "--verbose":
                LOG.setLevel(_loggers.DEBUG)
                _loggers.get_logger("kleinanzeigen_bot").setLevel(_loggers.DEBUG)
                _loggers.get_logger("kleinanzeigen_bot.runtime_config").setLevel(_loggers.DEBUG)
                _loggers.get_logger("nodriver").setLevel(_loggers.INFO)

    if help_requested:
        show_help()
        sys.exit(0)

    match len(arguments):
        case 0:
            parsed.command = "help"
        case 1:
            parsed.command = arguments[0]
            if parsed.command not in VALID_COMMANDS:
                LOG.error("Unknown command: %s", parsed.command)
                sys.exit(2)
        case _:
            LOG.error("More than one command given: %s", arguments)
            sys.exit(2)

    return parsed


def main(args:Sequence[str]) -> None:
    if "version" not in args:
        print(
            textwrap.dedent(rf"""
         _    _      _                           _                       _           _
        | | _| | ___(_)_ __   __ _ _ __  _______(_) __ _  ___ _ __      | |__   ___ | |_
        | |/ / |/ _ \ | '_ \ / _` | '_ \|_  / _ \ |/ _` |/ _ \ '_ \ ____| '_ \ / _ \| __|
        |   <| |  __/ | | | | (_| | | | |/ /  __/ | (_| |  __/ | | |____| |_) | (_) | |_
        |_|\_\_|\___|_|_| |_|\__,_|_| |_/___\___|_|\__, |\___|_| |_|    |_.__/ \___/ \__|
                                                   |___/
                                 https://github.com/Second-Hand-Friends/kleinanzeigen-bot
                                 Version: {__version__}
        """)[1:],
            flush = True,
        )  # [1:] removes the first empty blank line

    _loggers.configure_console_logging()
    _warn_unpatched_nodriver()
    signal.signal(signal.SIGINT, _error_handlers.on_sigint)
    atexit.register(_loggers.flush_all_handlers)

    try:
        bot = KleinanzeigenBot()
        nodriver.loop().run_until_complete(bot.run(list(args)))  # type: ignore[attr-defined]
    except CaptchaEncountered:
        raise
    except Exception:
        _error_handlers.on_exception(*sys.exc_info())
        sys.exit(1)
