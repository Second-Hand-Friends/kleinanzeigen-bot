# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import atexit, asyncio, enum, json, os, re, signal, sys, textwrap  # isort: skip
import getopt  # pylint: disable=deprecated-module
import urllib.parse as urllib_parse
from datetime import datetime
from gettext import gettext as _
from pathlib import Path
from typing import Any, Final

import certifi, colorama, nodriver  # isort: skip
from nodriver.core.connection import ProtocolException
from ruamel.yaml import YAML
from wcmatch import glob

from . import extract, resources
from ._version import __version__
from .model.ad_model import MAX_DESCRIPTION_LENGTH, Ad, AdPartial, Contact, calculate_auto_price
from .model.config_model import Config
from .update_checker import UpdateChecker
from .utils import diagnostics, dicts, error_handlers, loggers, misc, xdg_paths
from .utils.exceptions import CaptchaEncountered
from .utils.files import abspath
from .utils.i18n import Locale, get_current_locale, pluralize, set_current_locale
from .utils.misc import ainput, ensure, is_frozen
from .utils.web_scraping_mixin import By, Element, Is, WebScrapingMixin

# W0406: possibly a bug, see https://github.com/PyCQA/pylint/issues/3933

LOG:Final[loggers.Logger] = loggers.get_logger(__name__)
LOG.setLevel(loggers.INFO)

PUBLISH_MAX_RETRIES:Final[int] = 3

colorama.just_fix_windows_console()


class AdUpdateStrategy(enum.Enum):
    REPLACE = enum.auto()
    MODIFY = enum.auto()


class LoginState(enum.Enum):
    LOGGED_IN = enum.auto()
    LOGGED_OUT = enum.auto()
    UNKNOWN = enum.auto()


def _repost_cycle_ready(ad_cfg:Ad, ad_file_relative:str) -> bool:
    """
    Check if the repost cycle delay has been satisfied.

    :param ad_cfg: The ad configuration
    :param ad_file_relative: Relative path to the ad file for logging
    :return: True if ready to apply price reduction, False otherwise
    """
    total_reposts = ad_cfg.repost_count or 0
    delay_reposts = ad_cfg.auto_price_reduction.delay_reposts
    applied_cycles = ad_cfg.price_reduction_count or 0
    eligible_cycles = max(total_reposts - delay_reposts, 0)

    if total_reposts <= delay_reposts:
        remaining = (delay_reposts + 1) - total_reposts
        LOG.info(
            _("Auto price reduction delayed for [%s]: waiting %s more reposts (completed %s, applied %s reductions)"),
            ad_file_relative,
            max(remaining, 1),  # Clamp to 1 to avoid showing "0 more reposts" when at threshold
            total_reposts,
            applied_cycles,
        )
        return False

    if eligible_cycles <= applied_cycles:
        LOG.debug(
            _("Auto price reduction already applied for [%s]: %s reductions match %s eligible reposts"), ad_file_relative, applied_cycles, eligible_cycles
        )
        return False

    return True


def _day_delay_elapsed(ad_cfg:Ad, ad_file_relative:str) -> bool:
    """
    Check if the day delay has elapsed since the ad was last published.

    :param ad_cfg: The ad configuration
    :param ad_file_relative: Relative path to the ad file for logging
    :return: True if the delay has elapsed, False otherwise
    """
    delay_days = ad_cfg.auto_price_reduction.delay_days
    if delay_days == 0:
        return True

    reference = ad_cfg.updated_on or ad_cfg.created_on
    if not reference:
        LOG.info("Auto price reduction delayed for [%s]: waiting %s days but publish timestamp missing", ad_file_relative, delay_days)
        return False

    # Note: .days truncates to whole days (e.g., 1.9 days -> 1 day)
    # This is intentional: delays count complete 24-hour periods since publish
    # Both misc.now() and stored timestamps use UTC (via misc.now()), ensuring consistent calculations
    elapsed_days = (misc.now() - reference).days
    if elapsed_days < delay_days:
        LOG.info("Auto price reduction delayed for [%s]: waiting %s days (elapsed %s)", ad_file_relative, delay_days, elapsed_days)
        return False

    return True


def apply_auto_price_reduction(ad_cfg:Ad, _ad_cfg_orig:dict[str, Any], ad_file_relative:str) -> None:
    """
    Apply automatic price reduction to an ad based on repost count and configuration.

    This function modifies ad_cfg in-place, updating the price and price_reduction_count
    fields when a reduction is applicable.

    :param ad_cfg: The ad configuration to potentially modify
    :param _ad_cfg_orig: The original ad configuration (unused, kept for compatibility)
    :param ad_file_relative: Relative path to the ad file for logging
    """
    if not ad_cfg.auto_price_reduction.enabled:
        return

    base_price = ad_cfg.price
    if base_price is None:
        LOG.warning("Auto price reduction is enabled for [%s] but no price is configured.", ad_file_relative)
        return

    if ad_cfg.auto_price_reduction.min_price is not None and ad_cfg.auto_price_reduction.min_price == base_price:
        LOG.warning("Auto price reduction is enabled for [%s] but min_price equals price (%s) - no reductions will occur.", ad_file_relative, base_price)
        return

    if not _repost_cycle_ready(ad_cfg, ad_file_relative):
        return

    if not _day_delay_elapsed(ad_cfg, ad_file_relative):
        return

    applied_cycles = ad_cfg.price_reduction_count or 0
    next_cycle = applied_cycles + 1

    effective_price = calculate_auto_price(base_price = base_price, auto_price_reduction = ad_cfg.auto_price_reduction, target_reduction_cycle = next_cycle)

    if effective_price is None:
        return

    if effective_price == base_price:
        # Still increment counter so small fractional reductions can accumulate over multiple cycles
        ad_cfg.price_reduction_count = next_cycle
        LOG.info("Auto price reduction kept price %s after attempting %s reduction cycles", effective_price, next_cycle)
        return

    LOG.info("Auto price reduction applied: %s -> %s after %s reduction cycles", base_price, effective_price, next_cycle)
    ad_cfg.price = effective_price
    ad_cfg.price_reduction_count = next_cycle
    # Note: price_reduction_count is persisted to ad_cfg_orig only after successful publish


class KleinanzeigenBot(WebScrapingMixin):  # noqa: PLR0904
    def __init__(self) -> None:
        # workaround for https://github.com/Second-Hand-Friends/kleinanzeigen-bot/issues/295
        # see https://github.com/pyinstaller/pyinstaller/issues/7229#issuecomment-1309383026
        os.environ["SSL_CERT_FILE"] = certifi.where()

        super().__init__()

        self.root_url = "https://www.kleinanzeigen.de"

        self.config:Config
        self.config_file_path = abspath("config.yaml")
        self.config_explicitly_provided = False

        self.installation_mode:xdg_paths.InstallationMode | None = None

        self.categories:dict[str, str] = {}

        self.file_log:loggers.LogFileHandle | None = None
        log_file_basename = is_frozen() and os.path.splitext(os.path.basename(sys.executable))[0] or self.__module__
        self.log_file_path:str | None = abspath(f"{log_file_basename}.log")
        self.log_file_basename = log_file_basename
        self.log_file_explicitly_provided = False

        self.command = "help"
        self.ads_selector = "due"
        self.keep_old_ads = False

        self._login_detection_diagnostics_captured:bool = False

    def __del__(self) -> None:
        if self.file_log:
            self.file_log.close()
            self.file_log = None
        self.close_browser_session()

    @property
    def installation_mode_or_portable(self) -> xdg_paths.InstallationMode:
        return self.installation_mode or "portable"

    def get_version(self) -> str:
        return __version__

    def finalize_installation_mode(self) -> None:
        """
        Finalize installation mode detection after CLI args are parsed.
        Must be called after parse_args() to respect --config overrides.
        """
        if self.command in {"help", "version"}:
            return
        # Check if config_file_path was already customized (by --config or tests)
        default_portable_config = xdg_paths.get_config_file_path("portable").resolve()
        config_path = Path(self.config_file_path).resolve() if self.config_file_path else None
        config_was_customized = self.config_explicitly_provided or (config_path is not None and config_path != default_portable_config)

        if config_was_customized and self.config_file_path:
            # Config path was explicitly set - detect mode based on it
            LOG.debug("Detecting installation mode from explicit config path: %s", self.config_file_path)

            if config_path is not None and config_path == (Path.cwd() / "config.yaml").resolve():
                # Explicit path points to CWD config
                self.installation_mode = "portable"
                LOG.debug("Explicit config is in CWD, using portable mode")
            elif config_path is not None and config_path.is_relative_to(xdg_paths.get_xdg_base_dir("config").resolve()):
                # Explicit path is within XDG config directory
                self.installation_mode = "xdg"
                LOG.debug("Explicit config is in XDG directory, using xdg mode")
            else:
                # Custom location - default to portable mode (all paths relative to config)
                self.installation_mode = "portable"
                LOG.debug("Explicit config is in custom location, defaulting to portable mode")
        else:
            # No explicit config - use auto-detection
            LOG.debug("Detecting installation mode...")
            self.installation_mode = xdg_paths.detect_installation_mode()

            if self.installation_mode is None:
                # First run - prompt user
                LOG.info("First run detected, prompting user for installation mode")
                self.installation_mode = xdg_paths.prompt_installation_mode()

            # Set config path based on detected mode
            self.config_file_path = str(xdg_paths.get_config_file_path(self.installation_mode))

        # Set log file path based on mode (unless explicitly overridden via --logfile)
        using_default_portable_log = (
            self.log_file_path is not None and Path(self.log_file_path).resolve() == xdg_paths.get_log_file_path(self.log_file_basename, "portable").resolve()
        )
        if not self.log_file_explicitly_provided and using_default_portable_log:
            # Still using default portable path - update to match detected mode
            self.log_file_path = str(xdg_paths.get_log_file_path(self.log_file_basename, self.installation_mode))
            LOG.debug("Log file path: %s", self.log_file_path)

        # Log installation mode and config location (INFO level for user visibility)
        mode_display = "portable (current directory)" if self.installation_mode == "portable" else "system-wide (XDG directories)"
        LOG.info("Installation mode: %s", mode_display)
        LOG.info("Config file: %s", self.config_file_path)

    async def run(self, args:list[str]) -> None:
        self.parse_args(args)
        self.finalize_installation_mode()
        try:
            match self.command:
                case "help":
                    self.show_help()
                    return
                case "version":
                    print(self.get_version())
                case "create-config":
                    self.create_default_config()
                    return
                case "diagnose":
                    self.configure_file_logging()
                    self.load_config()
                    self.diagnose_browser_issues()
                    return
                case "verify":
                    self.configure_file_logging()
                    self.load_config()
                    # Check for updates on startup
                    checker = UpdateChecker(self.config, self.installation_mode_or_portable)
                    checker.check_for_updates()
                    self.load_ads()
                    LOG.info("############################################")
                    LOG.info("DONE: No configuration errors found.")
                    LOG.info("############################################")
                case "update-check":
                    self.configure_file_logging()
                    self.load_config()
                    checker = UpdateChecker(self.config, self.installation_mode_or_portable)
                    checker.check_for_updates(skip_interval_check = True)
                case "update-content-hash":
                    self.configure_file_logging()
                    self.load_config()
                    # Check for updates on startup
                    checker = UpdateChecker(self.config, self.installation_mode_or_portable)
                    checker.check_for_updates()
                    self.ads_selector = "all"
                    if ads := self.load_ads(exclude_ads_with_id = False):
                        self.update_content_hashes(ads)
                    else:
                        LOG.info("############################################")
                        LOG.info("DONE: No active ads found.")
                        LOG.info("############################################")
                case "publish":
                    self.configure_file_logging()
                    self.load_config()
                    # Check for updates on startup
                    checker = UpdateChecker(self.config, self.installation_mode_or_portable)
                    checker.check_for_updates()

                    if not (
                        self.ads_selector in {"all", "new", "due", "changed"}
                        or any(selector in self.ads_selector.split(",") for selector in ("all", "new", "due", "changed"))
                        or re.compile(r"\d+[,\d+]*").search(self.ads_selector)
                    ):
                        LOG.warning('You provided no ads selector. Defaulting to "due".')
                        self.ads_selector = "due"

                    if ads := self.load_ads():
                        await self.create_browser_session()
                        await self.login()
                        await self.publish_ads(ads)
                    else:
                        LOG.info("############################################")
                        LOG.info("DONE: No new/outdated ads found.")
                        LOG.info("############################################")
                case "update":
                    self.configure_file_logging()
                    self.load_config()

                    if not (
                        self.ads_selector in {"all", "changed"}
                        or any(selector in self.ads_selector.split(",") for selector in ("all", "changed"))
                        or re.compile(r"\d+[,\d+]*").search(self.ads_selector)
                    ):
                        LOG.warning('You provided no ads selector. Defaulting to "changed".')
                        self.ads_selector = "changed"

                    if ads := self.load_ads():
                        await self.create_browser_session()
                        await self.login()
                        await self.update_ads(ads)
                    else:
                        LOG.info("############################################")
                        LOG.info("DONE: No changed ads found.")
                        LOG.info("############################################")
                case "delete":
                    self.configure_file_logging()
                    self.load_config()
                    # Check for updates on startup
                    checker = UpdateChecker(self.config, self.installation_mode_or_portable)
                    checker.check_for_updates()
                    if ads := self.load_ads():
                        await self.create_browser_session()
                        await self.login()
                        await self.delete_ads(ads)
                    else:
                        LOG.info("############################################")
                        LOG.info("DONE: No ads to delete found.")
                        LOG.info("############################################")
                case "extend":
                    self.configure_file_logging()
                    self.load_config()
                    # Check for updates on startup
                    checker = UpdateChecker(self.config, self.installation_mode_or_portable)
                    checker.check_for_updates()

                    # Default to all ads if no selector provided
                    if not re.compile(r"\d+[,\d+]*").search(self.ads_selector):
                        LOG.info("Extending all ads within 8-day window...")
                        self.ads_selector = "all"

                    if ads := self.load_ads():
                        await self.create_browser_session()
                        await self.login()
                        await self.extend_ads(ads)
                    else:
                        LOG.info("############################################")
                        LOG.info("DONE: No ads found to extend.")
                        LOG.info("############################################")
                case "download":
                    self.configure_file_logging()
                    # ad IDs depends on selector
                    if not (self.ads_selector in {"all", "new"} or re.compile(r"\d+[,\d+]*").search(self.ads_selector)):
                        LOG.warning('You provided no ads selector. Defaulting to "new".')
                        self.ads_selector = "new"
                    self.load_config()
                    # Check for updates on startup
                    checker = UpdateChecker(self.config, self.installation_mode_or_portable)
                    checker.check_for_updates()
                    await self.create_browser_session()
                    await self.login()
                    await self.download_ads()

                case _:
                    LOG.error("Unknown command: %s", self.command)
                    sys.exit(2)
        finally:
            self.close_browser_session()

    def show_help(self) -> None:
        if is_frozen():
            exe = sys.argv[0]
        elif os.getenv("PDM_PROJECT_ROOT", ""):
            exe = "pdm run app"
        else:
            exe = "python -m kleinanzeigen_bot"

        if get_current_locale().language == "de":
            print(
                textwrap.dedent(
                    f"""\
            Verwendung: {colorama.Fore.LIGHTMAGENTA_EX}{exe} BEFEHL [OPTIONEN]{colorama.Style.RESET_ALL}

            Befehle:
              publish  - (Wieder-)Veröffentlicht Anzeigen
              verify   - Überprüft die Konfigurationsdateien
              delete   - Löscht Anzeigen
              update   - Aktualisiert bestehende Anzeigen
              extend   - Verlängert Anzeigen innerhalb des 8-Tage-Zeitfensters
              download - Lädt eine oder mehrere Anzeigen herunter
              update-check - Prüft auf verfügbare Updates
              update-content-hash - Berechnet den content_hash aller Anzeigen anhand der aktuellen ad_defaults neu;
                                    nach Änderungen an den config.yaml/ad_defaults verhindert es, dass alle Anzeigen als
                                    "geändert" gelten und neu veröffentlicht werden.
              create-config - Erstellt eine neue Standard-Konfigurationsdatei, falls noch nicht vorhanden
              diagnose - Diagnostiziert Browser-Verbindungsprobleme und zeigt Troubleshooting-Informationen
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
              --ads=changed|<id(s)> (update) - Gibt an, welche Anzeigen aktualisiert werden sollen (STANDARD: changed)
                    Mögliche Werte:
                    * changed: Aktualisiert nur Anzeigen, die seit der letzten Veröffentlichung geändert wurden
                    * <id(s)>: Gibt eine oder mehrere Anzeigen-IDs zum Aktualisieren an, z. B. "--ads=1,2,3"
              --ads=<id(s)> (extend) - Gibt an, welche Anzeigen verlängert werden sollen
                    Standardmäßig werden alle Anzeigen verlängert, die innerhalb von 8 Tagen ablaufen.
                    Mit dieser Option können Sie bestimmte Anzeigen-IDs angeben, z. B. "--ads=1,2,3"
              --force           - Alias für '--ads=all'
              --keep-old        - Verhindert das Löschen alter Anzeigen bei erneuter Veröffentlichung
              --config=<PATH>   - Pfad zur YAML- oder JSON-Konfigurationsdatei (STANDARD: ./config.yaml)
              --logfile=<PATH>  - Pfad zur Protokolldatei (STANDARD: ./kleinanzeigen-bot.log)
              --lang=en|de      - Anzeigesprache (STANDARD: Systemsprache, wenn unterstützt, sonst Englisch)
              -v, --verbose     - Aktiviert detaillierte Ausgabe – nur nützlich zur Fehlerbehebung
            """.rstrip()
                )
            )
        else:
            print(
                textwrap.dedent(
                    f"""\
            Usage: {colorama.Fore.LIGHTMAGENTA_EX}{exe} COMMAND [OPTIONS]{colorama.Style.RESET_ALL}

            Commands:
              publish  - (re-)publishes ads
              verify   - verifies the configuration files
              delete   - deletes ads
              update   - updates published ads
              extend   - extends ads within the 8-day window before expiry
              download - downloads one or multiple ads
              update-check - checks for available updates
              update-content-hash – recalculates each ad's content_hash based on the current ad_defaults;
                                    use this after changing config.yaml/ad_defaults to avoid every ad being marked "changed" and republished
              create-config - creates a new default configuration file if one does not exist
              diagnose - diagnoses browser connection issues and shows troubleshooting information
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
              --ads=changed|<id(s)> (update) - specifies which ads to update (DEFAULT: changed)
                    Possible values:
                    * changed: only update ads that have been modified since last publication
                    * <id(s)>: provide one or several ads by ID to update, like e.g. "--ads=1,2,3"
              --ads=<id(s)> (extend) - specifies which ads to extend
                    By default, extends all ads expiring within 8 days.
                    Use this option to specify ad IDs, e.g. "--ads=1,2,3"
              --force           - alias for '--ads=all'
              --keep-old        - don't delete old ads on republication
              --config=<PATH>   - path to the config YAML or JSON file (DEFAULT: ./config.yaml)
              --logfile=<PATH>  - path to the logfile (DEFAULT: ./kleinanzeigen-bot.log)
              --lang=en|de      - display language (STANDARD: system language if supported, otherwise English)
              -v, --verbose     - enables verbose output - only useful when troubleshooting issues
            """.rstrip()
                )
            )

    def parse_args(self, args:list[str]) -> None:
        try:
            options, arguments = getopt.gnu_getopt(args[1:], "hv", ["ads=", "config=", "force", "help", "keep-old", "logfile=", "lang=", "verbose"])
        except getopt.error as ex:
            LOG.error(ex.msg)
            LOG.error("Use --help to display available options.")
            sys.exit(2)

        for option, value in options:
            match option:
                case "-h" | "--help":
                    self.show_help()
                    sys.exit(0)
                case "--config":
                    self.config_file_path = abspath(value)
                    self.config_explicitly_provided = True
                case "--logfile":
                    if value:
                        self.log_file_path = abspath(value)
                    else:
                        self.log_file_path = None
                    self.log_file_explicitly_provided = True
                case "--ads":
                    self.ads_selector = value.strip().lower()
                case "--force":
                    self.ads_selector = "all"
                case "--keep-old":
                    self.keep_old_ads = True
                case "--lang":
                    set_current_locale(Locale.of(value))
                case "-v" | "--verbose":
                    LOG.setLevel(loggers.DEBUG)
                    loggers.get_logger("nodriver").setLevel(loggers.INFO)

        match len(arguments):
            case 0:
                self.command = "help"
            case 1:
                self.command = arguments[0]
            case _:
                LOG.error("More than one command given: %s", arguments)
                sys.exit(2)

    def configure_file_logging(self) -> None:
        if not self.log_file_path:
            return
        if self.file_log:
            return

        LOG.info("Logging to [%s]...", self.log_file_path)
        self.file_log = loggers.configure_file_logging(self.log_file_path)

        LOG.info("App version: %s", self.get_version())
        LOG.info("Python version: %s", sys.version)

    def create_default_config(self) -> None:
        """
        Create a default config.yaml in the project root if it does not exist.
        If it exists, log an error and inform the user.
        """
        if os.path.exists(self.config_file_path):
            LOG.error("Config file %s already exists. Aborting creation.", self.config_file_path)
            return
        default_config = Config.model_construct()
        default_config.login.username = "changeme"  # noqa: S105 placeholder for default config, not a real username
        default_config.login.password = "changeme"  # noqa: S105 placeholder for default config, not a real password
        dicts.save_dict(
            self.config_file_path,
            default_config.model_dump(exclude_none = True, exclude = {"ad_defaults": {"description"}}),
            header=(
                "# yaml-language-server: $schema="
                "https://raw.githubusercontent.com/Second-Hand-Friends/kleinanzeigen-bot"
                "/refs/heads/main/schemas/config.schema.json"
            ),
        )

    def load_config(self) -> None:
        # write default config.yaml if config file does not exist
        if not os.path.exists(self.config_file_path):
            self.create_default_config()

        config_yaml = dicts.load_dict_if_exists(self.config_file_path, _("config"))
        self.config = Config.model_validate(config_yaml, strict = True, context = self.config_file_path)

        # load built-in category mappings
        self.categories = dicts.load_dict_from_module(resources, "categories.yaml", "categories")
        deprecated_categories = dicts.load_dict_from_module(resources, "categories_old.yaml", "categories")
        self.categories.update(deprecated_categories)
        if self.config.categories:
            self.categories.update(self.config.categories)
        LOG.info(" -> found %s", pluralize("category", self.categories))

        # populate browser_config object used by WebScrapingMixin
        self.browser_config.arguments = self.config.browser.arguments
        self.browser_config.binary_location = self.config.browser.binary_location
        self.browser_config.extensions = [abspath(item, relative_to = self.config_file_path) for item in self.config.browser.extensions]
        self.browser_config.use_private_window = self.config.browser.use_private_window
        if self.config.browser.user_data_dir:
            self.browser_config.user_data_dir = abspath(self.config.browser.user_data_dir, relative_to = self.config_file_path)
        self.browser_config.profile_name = self.config.browser.profile_name

    def __check_ad_republication(self, ad_cfg:Ad, ad_file_relative:str) -> bool:
        """
        Check if an ad needs to be republished based on republication interval.
        Note:  This method does not check for content changes. Use __check_ad_changed for that.

        Returns:
            True if the ad should be republished based on the interval.
        """
        if ad_cfg.updated_on:
            last_updated_on = ad_cfg.updated_on
        elif ad_cfg.created_on:
            last_updated_on = ad_cfg.created_on
        else:
            return True

        if not last_updated_on:
            return True

        # Check republication interval
        ad_age = misc.now() - last_updated_on
        if ad_age.days <= ad_cfg.republication_interval:
            LOG.info(
                " -> SKIPPED: ad [%s] was last published %d days ago. republication is only required every %s days",
                ad_file_relative,
                ad_age.days,
                ad_cfg.republication_interval,
            )
            return False

        return True

    def __check_ad_changed(self, ad_cfg:Ad, ad_cfg_orig:dict[str, Any], ad_file_relative:str) -> bool:
        """
        Check if an ad has been changed since last publication.

        Returns:
            True if the ad has been changed.
        """
        if not ad_cfg.id:
            # New ads are not considered "changed"
            return False

        # Calculate hash on original config to match what was stored
        current_hash = AdPartial.model_validate(ad_cfg_orig).update_content_hash().content_hash
        stored_hash = ad_cfg_orig.get("content_hash")

        LOG.debug("Hash comparison for [%s]:", ad_file_relative)
        LOG.debug("    Stored hash: %s", stored_hash)
        LOG.debug("    Current hash: %s", current_hash)

        if stored_hash and current_hash != stored_hash:
            LOG.info("Changes detected in ad [%s], will republish", ad_file_relative)
            # Update hash in original configuration
            ad_cfg_orig["content_hash"] = current_hash
            return True

        return False

    def load_ads(self, *, ignore_inactive:bool = True, exclude_ads_with_id:bool = True) -> list[tuple[str, Ad, dict[str, Any]]]:
        """
        Load and validate all ad config files, optionally filtering out inactive or already-published ads.

        Args:
            ignore_inactive (bool):
                Skip ads with `active=False`.
            exclude_ads_with_id (bool):
                Skip ads whose raw data already contains an `id`, i.e. was published before.

        Returns:
            list[tuple[str, Ad, dict[str, Any]]]:
            Tuples of (file_path, validated Ad model, original raw data).
        """
        LOG.info("Searching for ad config files...")

        ad_files:dict[str, str] = {}
        data_root_dir = os.path.dirname(self.config_file_path)
        for file_pattern in self.config.ad_files:
            for ad_file in glob.glob(file_pattern, root_dir = data_root_dir, flags = glob.GLOBSTAR | glob.BRACE | glob.EXTGLOB):
                if not str(ad_file).endswith("ad_fields.yaml"):
                    ad_files[abspath(ad_file, relative_to = data_root_dir)] = ad_file
        LOG.info(" -> found %s", pluralize("ad config file", ad_files))
        if not ad_files:
            return []

        ids = []
        use_specific_ads = False
        selectors = self.ads_selector.split(",")

        if re.compile(r"\d+[,\d+]*").search(self.ads_selector):
            ids = [int(n) for n in self.ads_selector.split(",")]
            use_specific_ads = True
            LOG.info("Start fetch task for the ad(s) with id(s):")
            LOG.info(" | ".join([str(id_) for id_ in ids]))

        ads = []
        for ad_file, ad_file_relative in sorted(ad_files.items()):
            ad_cfg_orig:dict[str, Any] = dicts.load_dict(ad_file, "ad")
            ad_cfg:Ad = self.load_ad(ad_cfg_orig)

            if ignore_inactive and not ad_cfg.active:
                LOG.info(" -> SKIPPED: inactive ad [%s]", ad_file_relative)
                continue

            if use_specific_ads:
                if ad_cfg.id not in ids:
                    LOG.info(" -> SKIPPED: ad [%s] is not in list of given ids.", ad_file_relative)
                    continue
            else:
                # Check if ad should be included based on selectors
                should_include = False

                # Check for 'changed' selector
                if "changed" in selectors and self.__check_ad_changed(ad_cfg, ad_cfg_orig, ad_file_relative):
                    should_include = True

                # Check for 'new' selector
                if "new" in selectors and (not ad_cfg.id or not exclude_ads_with_id):
                    should_include = True
                elif "new" in selectors and ad_cfg.id and exclude_ads_with_id:
                    LOG.info(" -> SKIPPED: ad [%s] is not new. already has an id assigned.", ad_file_relative)

                # Check for 'due' selector
                if "due" in selectors:
                    # For 'due' selector, check if the ad is due for republication based on interval
                    if self.__check_ad_republication(ad_cfg, ad_file_relative):
                        should_include = True

                # Check for 'all' selector (always include)
                if "all" in selectors:
                    should_include = True

                if not should_include:
                    continue

            ensure(self.__get_description(ad_cfg, with_affixes = False), f"-> property [description] not specified @ [{ad_file}]")
            self.__get_description(ad_cfg, with_affixes = True)  # validates complete description

            if ad_cfg.category:
                resolved_category_id = self.categories.get(ad_cfg.category)
                if not resolved_category_id and ">" in ad_cfg.category:
                    # this maps actually to the sonstiges/weiteres sub-category
                    parent_category = ad_cfg.category.rpartition(">")[0].strip()
                    resolved_category_id = self.categories.get(parent_category)
                    if resolved_category_id:
                        LOG.warning("Category [%s] unknown. Using category [%s] with ID [%s] instead.", ad_cfg.category, parent_category, resolved_category_id)

                if resolved_category_id:
                    ad_cfg.category = resolved_category_id

            if ad_cfg.images:
                images = []
                ad_dir = os.path.dirname(ad_file)
                for image_pattern in ad_cfg.images:
                    pattern_images = set()
                    for image_file in glob.glob(image_pattern, root_dir = ad_dir, flags = glob.GLOBSTAR | glob.BRACE | glob.EXTGLOB):
                        _, image_file_ext = os.path.splitext(image_file)
                        ensure(image_file_ext.lower() in {".gif", ".jpg", ".jpeg", ".png"}, f"Unsupported image file type [{image_file}]")
                        if os.path.isabs(image_file):
                            pattern_images.add(image_file)
                        else:
                            pattern_images.add(abspath(image_file, relative_to = ad_file))
                    images.extend(sorted(pattern_images))
                ensure(images or not ad_cfg.images, f"No images found for given file patterns {ad_cfg.images} at {ad_dir}")
                ad_cfg.images = list(dict.fromkeys(images))

            ads.append((ad_file, ad_cfg, ad_cfg_orig))

        LOG.info("Loaded %s", pluralize("ad", ads))
        return ads

    def load_ad(self, ad_cfg_orig:dict[str, Any]) -> Ad:
        return AdPartial.model_validate(ad_cfg_orig).to_ad(self.config.ad_defaults)

    async def check_and_wait_for_captcha(self, *, is_login_page:bool = True) -> None:
        try:
            captcha_timeout = self._timeout("captcha_detection")
            await self.web_find(By.CSS_SELECTOR, "iframe[name^='a-'][src^='https://www.google.com/recaptcha/api2/anchor?']", timeout = captcha_timeout)

            if not is_login_page and self.config.captcha.auto_restart:
                LOG.warning("Captcha recognized - auto-restart enabled, abort run...")
                raise CaptchaEncountered(misc.parse_duration(self.config.captcha.restart_delay))

            LOG.warning("############################################")
            LOG.warning("# Captcha present! Please solve the captcha.")
            LOG.warning("############################################")

            if not is_login_page:
                await self.web_scroll_page_down()

            await ainput(_("Press a key to continue..."))
        except TimeoutError:
            # No captcha detected within timeout.
            pass

    async def login(self) -> None:
        LOG.info("Checking if already logged in...")
        await self.web_open(f"{self.root_url}")
        if getattr(self, "page", None) is not None:
            LOG.debug("Current page URL after opening homepage: %s", self.page.url)

        state = await self.get_login_state()
        if state == LoginState.LOGGED_IN:
            LOG.info("Already logged in as [%s]. Skipping login.", self.config.login.username)
            return

        if state == LoginState.UNKNOWN:
            LOG.warning("Login state is UNKNOWN - cannot determine if already logged in. Skipping login attempt.")
            return

        LOG.info("Opening login page...")
        await self.web_open(f"{self.root_url}/m-einloggen.html?targetUrl=/")

        await self.fill_login_data_and_send()
        await self.handle_after_login_logic()

        # Sometimes a second login is required
        state = await self.get_login_state()
        if state == LoginState.UNKNOWN:
            LOG.warning("Login state is UNKNOWN after first login attempt - cannot determine login status. Aborting login process.")
            return

        if state == LoginState.LOGGED_OUT:
            LOG.debug("First login attempt did not succeed, trying second login attempt")
            await self.fill_login_data_and_send()
            await self.handle_after_login_logic()

            state = await self.get_login_state()
            if state == LoginState.LOGGED_IN:
                LOG.debug("Second login attempt succeeded")
            else:
                LOG.warning("Second login attempt also failed - login may not have succeeded")

    async def fill_login_data_and_send(self) -> None:
        LOG.info("Logging in as [%s]...", self.config.login.username)
        await self.web_input(By.ID, "login-email", self.config.login.username)

        # clearing password input in case browser has stored login data set
        await self.web_input(By.ID, "login-password", "")
        await self.web_input(By.ID, "login-password", self.config.login.password)

        await self.check_and_wait_for_captcha(is_login_page = True)

        await self.web_click(By.CSS_SELECTOR, "form#login-form button[type='submit']")

    async def handle_after_login_logic(self) -> None:
        try:
            sms_timeout = self._timeout("sms_verification")
            await self.web_find(By.TEXT, "Wir haben dir gerade einen 6-stelligen Code für die Telefonnummer", timeout = sms_timeout)
            LOG.warning("############################################")
            LOG.warning("# Device verification message detected. Please follow the instruction displayed in the Browser.")
            LOG.warning("############################################")
            await ainput(_("Press ENTER when done..."))
        except TimeoutError:
            # No SMS verification prompt detected.
            pass

        try:
            email_timeout = self._timeout("email_verification")
            await self.web_find(By.TEXT, "Um dein Konto zu schützen haben wir dir eine E-Mail geschickt", timeout = email_timeout)
            LOG.warning("############################################")
            LOG.warning("# Device verification message detected. Please follow the instruction displayed in the Browser.")
            LOG.warning("############################################")
            await ainput(_("Press ENTER when done..."))
        except TimeoutError:
            # No email verification prompt detected.
            pass

        try:
            LOG.info("Handling GDPR disclaimer...")
            gdpr_timeout = self._timeout("gdpr_prompt")
            await self.web_find(By.ID, "gdpr-banner-accept", timeout = gdpr_timeout)
            await self.web_click(By.ID, "gdpr-banner-cmp-button")
            await self.web_click(
                By.XPATH, "//div[@id='ConsentManagementPage']//*//button//*[contains(., 'Alle ablehnen und fortfahren')]", timeout = gdpr_timeout
            )
        except TimeoutError:
            # GDPR banner not shown within timeout.
            pass

    async def _auth_probe_login_state(self) -> LoginState:
        """Probe an auth-required endpoint to classify login state.

        The probe is non-mutating (GET request). It is used as a fallback method by
        get_login_state() when DOM-based checks are inconclusive.
        """

        url = f"{self.root_url}/m-meine-anzeigen-verwalten.json?sort=DEFAULT"
        try:
            response = await self.web_request(url, valid_response_codes = [200, 401, 403])
        except (TimeoutError, AssertionError):
            # AssertionError can occur when web_request() fails to parse the response (e.g., unexpected content type)
            # Treat both timeout and assertion failures as UNKNOWN to avoid false assumptions about login state
            return LoginState.UNKNOWN

        status_code = response.get("statusCode")
        if status_code in {401, 403}:
            return LoginState.LOGGED_OUT

        content = response.get("content", "")
        if not isinstance(content, str):
            return LoginState.UNKNOWN

        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            lowered = content.lower()
            if "m-einloggen" in lowered or "login-email" in lowered or "login-password" in lowered or "login-form" in lowered:
                return LoginState.LOGGED_OUT
            return LoginState.UNKNOWN

        if isinstance(payload, dict) and "ads" in payload:
            return LoginState.LOGGED_IN

        return LoginState.UNKNOWN

    async def get_login_state(self) -> LoginState:
        """Determine current login state using layered detection.

        Order:
        1) DOM-based check via `is_logged_in(include_probe=False)` (preferred - stealthy)
        2) Server-side auth probe via `_auth_probe_login_state` (fallback - more reliable)
        3) If still inconclusive, capture diagnostics via
           `_capture_login_detection_diagnostics_if_enabled` and return `UNKNOWN`
        """
        # Prefer DOM-based checks first to minimize bot-like behavior.
        # The auth probe makes a JSON API request that normal users wouldn't trigger.
        if await self.is_logged_in(include_probe = False):
            return LoginState.LOGGED_IN

        # Fall back to the more reliable server-side auth probe.
        # SPA/hydration delays can cause DOM-based checks to temporarily miss login indicators.
        state = await self._auth_probe_login_state()
        if state != LoginState.UNKNOWN:
            return state

        await self._capture_login_detection_diagnostics_if_enabled()
        return LoginState.UNKNOWN

    def _diagnostics_output_dir(self) -> Path:
        diagnostics = getattr(self.config, "diagnostics", None)
        if diagnostics is not None and diagnostics.output_dir and diagnostics.output_dir.strip():
            return Path(abspath(diagnostics.output_dir, relative_to = self.config_file_path)).resolve()

        if self.installation_mode_or_portable == "xdg":
            return xdg_paths.get_xdg_base_dir("cache") / "diagnostics"

        return (Path.cwd() / ".temp" / "diagnostics").resolve()

    async def _capture_login_detection_diagnostics_if_enabled(self) -> None:
        cfg = getattr(self.config, "diagnostics", None)
        if cfg is None or not cfg.capture_on.login_detection:
            return

        if self._login_detection_diagnostics_captured:
            return

        page = getattr(self, "page", None)
        if page is None:
            return

        self._login_detection_diagnostics_captured = True

        try:
            await diagnostics.capture_diagnostics(
                output_dir = self._diagnostics_output_dir(),
                base_prefix = "login_detection_unknown",
                page = page,
            )
        except Exception as exc:  # noqa: BLE001
            LOG.debug(
                "Login diagnostics capture failed (output_dir=%s, base_prefix=%s): %s",
                self._diagnostics_output_dir(),
                "login_detection_unknown",
                exc,
            )

        if cfg.pause_on_login_detection_failure and getattr(sys.stdin, "isatty", lambda: False)():
            LOG.warning("############################################")
            LOG.warning("# Login detection returned UNKNOWN. Browser is paused for manual inspection.")
            LOG.warning("############################################")
            await ainput(_("Press a key to continue..."))

    async def _capture_publish_error_diagnostics_if_enabled(
        self,
        ad_cfg:Ad,
        ad_cfg_orig:dict[str, Any],
        ad_file:str,
        attempt:int,
        exc:Exception,
    ) -> None:
        """Capture publish failure diagnostics when enabled and a page is available.

        Runs only if cfg.capture_on.publish is enabled and self.page is set.
        Uses the ad configuration and publish attempt details to write screenshot, HTML,
        JSON payload, and optional log copy for debugging.
        """
        cfg = getattr(self.config, "diagnostics", None)
        if cfg is None or not cfg.capture_on.publish:
            return

        page = getattr(self, "page", None)
        if page is None:
            return

        ad_basename = Path(ad_file).name
        match = re.search(r"(ad_\d+)", ad_basename)
        ad_token = match.group(1) if match else "ad_unknown"

        json_payload = {
            "timestamp": misc.now().isoformat(timespec = "seconds"),
            "attempt": attempt,
            "page_url": getattr(page, "url", None),
            "exception": {
                "type": exc.__class__.__name__,
                "message": str(exc),
                "repr": repr(exc),
            },
            "ad_token": ad_token,
            "ad_config_effective": ad_cfg.model_dump(mode = "json"),
            "ad_config_original": ad_cfg_orig,
        }

        try:
            await diagnostics.capture_diagnostics(
                output_dir = self._diagnostics_output_dir(),
                base_prefix = "publish_error",
                attempt = attempt,
                subject = ad_token,
                page = page,
                json_payload = json_payload,
                log_file_path = self.log_file_path,
                copy_log = cfg.capture_log_copy,
            )
        except Exception as error:  # noqa: BLE001
            LOG.warning("Diagnostics capture failed during publish error handling: %s", error)

    async def is_logged_in(self, *, include_probe:bool = True) -> bool:
        # Use login_detection timeout (10s default) instead of default (5s)
        # to allow sufficient time for client-side JavaScript rendering after page load.
        # This is especially important for older sessions (20+ days) that require
        # additional server-side validation time.
        login_check_timeout = self._timeout("login_detection")
        effective_timeout = self._effective_timeout("login_detection")
        username = self.config.login.username.lower()
        LOG.debug(
            "Starting login detection (timeout: %.1fs base, %.1fs effective with multiplier/backoff)",
            login_check_timeout,
            effective_timeout,
        )

        # Try to find the standard element first
        try:
            user_info = await self.web_text(By.CLASS_NAME, "mr-medium", timeout = login_check_timeout)
            if username in user_info.lower():
                LOG.debug("Login detected via .mr-medium element")
                return True
        except TimeoutError:
            LOG.debug("Timeout waiting for .mr-medium element after %.1fs", effective_timeout)

        # If standard element not found or didn't contain username, try the alternative
        try:
            user_info = await self.web_text(By.ID, "user-email", timeout = login_check_timeout)
            if username in user_info.lower():
                LOG.debug("Login detected via #user-email element")
                return True
        except TimeoutError:
            LOG.debug("Timeout waiting for #user-email element after %.1fs", effective_timeout)

        if not include_probe:
            LOG.debug("No login detected - neither .mr-medium nor #user-email found with username")
            return False

        state = await self._auth_probe_login_state()
        if state == LoginState.LOGGED_IN:
            return True

        LOG.debug("No login detected - DOM elements not found and server probe returned %s", state.name)
        return False

    async def delete_ads(self, ad_cfgs:list[tuple[str, Ad, dict[str, Any]]]) -> None:
        count = 0

        published_ads = json.loads((await self.web_request(f"{self.root_url}/m-meine-anzeigen-verwalten.json?sort=DEFAULT"))["content"])["ads"]

        for ad_file, ad_cfg, _ad_cfg_orig in ad_cfgs:
            count += 1
            LOG.info("Processing %s/%s: '%s' from [%s]...", count, len(ad_cfgs), ad_cfg.title, ad_file)
            await self.delete_ad(ad_cfg, published_ads, delete_old_ads_by_title = self.config.publishing.delete_old_ads_by_title)
            await self.web_sleep()

        LOG.info("############################################")
        LOG.info("DONE: Deleted %s", pluralize("ad", count))
        LOG.info("############################################")

    async def delete_ad(self, ad_cfg:Ad, published_ads:list[dict[str, Any]], *, delete_old_ads_by_title:bool) -> bool:
        LOG.info("Deleting ad '%s' if already present...", ad_cfg.title)

        await self.web_open(f"{self.root_url}/m-meine-anzeigen.html")
        csrf_token_elem = await self.web_find(By.CSS_SELECTOR, "meta[name=_csrf]")
        csrf_token = csrf_token_elem.attrs["content"]
        ensure(csrf_token is not None, "Expected CSRF Token not found in HTML content!")

        if delete_old_ads_by_title:
            for published_ad in published_ads:
                published_ad_id = int(published_ad.get("id", -1))
                published_ad_title = published_ad.get("title", "")
                if ad_cfg.id == published_ad_id or ad_cfg.title == published_ad_title:
                    LOG.info(" -> deleting %s '%s'...", published_ad_id, published_ad_title)
                    await self.web_request(
                        url = f"{self.root_url}/m-anzeigen-loeschen.json?ids={published_ad_id}", method = "POST", headers = {"x-csrf-token": str(csrf_token)}
                    )
        elif ad_cfg.id:
            await self.web_request(
                url = f"{self.root_url}/m-anzeigen-loeschen.json?ids={ad_cfg.id}",
                method = "POST",
                headers = {"x-csrf-token": str(csrf_token)},
                valid_response_codes = [200, 404],
            )

        await self.web_sleep()
        ad_cfg.id = None
        return True

    async def extend_ads(self, ad_cfgs:list[tuple[str, Ad, dict[str, Any]]]) -> None:
        """Extends ads that are close to expiry."""
        # Fetch currently published ads from API
        published_ads = json.loads((await self.web_request(f"{self.root_url}/m-meine-anzeigen-verwalten.json?sort=DEFAULT"))["content"])["ads"]

        # Filter ads that need extension
        ads_to_extend = []
        for ad_file, ad_cfg, ad_cfg_orig in ad_cfgs:
            # Skip unpublished ads (no ID)
            if not ad_cfg.id:
                LOG.info(" -> SKIPPED: ad '%s' is not published yet", ad_cfg.title)
                continue

            # Find ad in published list
            published_ad = next((ad for ad in published_ads if ad["id"] == ad_cfg.id), None)
            if not published_ad:
                LOG.warning(" -> SKIPPED: ad '%s' (ID: %s) not found in published ads", ad_cfg.title, ad_cfg.id)
                continue

            # Skip non-active ads
            if published_ad.get("state") != "active":
                LOG.info(" -> SKIPPED: ad '%s' is not active (state: %s)", ad_cfg.title, published_ad.get("state"))
                continue

            # Check if ad is within 8-day extension window using API's endDate
            end_date_str = published_ad.get("endDate")
            if not end_date_str:
                LOG.warning(" -> SKIPPED: ad '%s' has no endDate in API response", ad_cfg.title)
                continue

            # Intentionally parsing naive datetime from kleinanzeigen API's German date format, timezone not relevant for date-only comparison
            end_date = datetime.strptime(end_date_str, "%d.%m.%Y")  # noqa: DTZ007
            days_until_expiry = (end_date.date() - misc.now().date()).days

            # Magic value 8 is kleinanzeigen.de's platform policy: extensions only possible within 8 days of expiry
            if days_until_expiry <= 8:  # noqa: PLR2004
                LOG.info(" -> ad '%s' expires in %d days, will extend", ad_cfg.title, days_until_expiry)
                ads_to_extend.append((ad_file, ad_cfg, ad_cfg_orig, published_ad))
            else:
                LOG.info(" -> SKIPPED: ad '%s' expires in %d days (can only extend within 8 days)", ad_cfg.title, days_until_expiry)

        if not ads_to_extend:
            LOG.info("No ads need extension at this time.")
            LOG.info("############################################")
            LOG.info("DONE: No ads extended.")
            LOG.info("############################################")
            return

        # Process extensions
        success_count = 0
        for idx, (ad_file, ad_cfg, ad_cfg_orig, _published_ad) in enumerate(ads_to_extend, start = 1):
            LOG.info("Processing %s/%s: '%s' from [%s]...", idx, len(ads_to_extend), ad_cfg.title, ad_file)
            if await self.extend_ad(ad_file, ad_cfg, ad_cfg_orig):
                success_count += 1
            await self.web_sleep()

        LOG.info("############################################")
        LOG.info("DONE: Extended %s", pluralize("ad", success_count))
        LOG.info("############################################")

    async def extend_ad(self, ad_file:str, ad_cfg:Ad, ad_cfg_orig:dict[str, Any]) -> bool:
        """Extends a single ad listing."""
        LOG.info("Extending ad '%s' (ID: %s)...", ad_cfg.title, ad_cfg.id)

        try:
            # Navigate to ad management page and find extend button across all pages
            extend_button_xpath = f'//li[@data-adid="{ad_cfg.id}"]//button[contains(., "Verlängern")]'

            async def find_and_click_extend_button(page_num:int) -> bool:
                """Try to find and click extend button on current page."""
                try:
                    extend_button = await self.web_find(By.XPATH, extend_button_xpath, timeout = self._timeout("quick_dom"))
                    LOG.info("Found extend button on page %s", page_num)
                    await extend_button.click()
                    return True  # Success - stop pagination
                except TimeoutError:
                    LOG.debug("Extend button not found on page %s", page_num)
                    return False  # Continue to next page

            success = await self._navigate_paginated_ad_overview(find_and_click_extend_button, page_url = f"{self.root_url}/m-meine-anzeigen.html")

            if not success:
                LOG.error(" -> FAILED: Could not find extend button for ad ID %s", ad_cfg.id)
                return False

            # Handle confirmation dialog
            # After clicking "Verlängern", a dialog appears with:
            # - Title: "Vielen Dank!"
            # - Message: "Deine Anzeige ... wurde erfolgreich verlängert."
            # - Paid bump-up option (skipped by closing dialog)
            # Simply close the dialog with the X button (aria-label="Schließen")
            try:
                dialog_close_timeout = self._timeout("quick_dom")
                await self.web_click(By.CSS_SELECTOR, 'button[aria-label="Schließen"]', timeout = dialog_close_timeout)
                LOG.debug(" -> Closed confirmation dialog")
            except TimeoutError:
                LOG.warning(" -> No confirmation dialog found, extension may have completed directly")

            # Update metadata in YAML file
            # Update updated_on to track when ad was extended
            ad_cfg_orig["updated_on"] = misc.now().isoformat(timespec = "seconds")
            dicts.save_dict(ad_file, ad_cfg_orig)

            LOG.info(" -> SUCCESS: ad extended with ID %s", ad_cfg.id)
            return True

        except TimeoutError as ex:
            LOG.error(" -> FAILED: Timeout while extending ad '%s': %s", ad_cfg.title, ex)
            return False
        except OSError as ex:
            LOG.error(" -> FAILED: Could not persist extension for ad '%s': %s", ad_cfg.title, ex)
            return False

    async def __check_publishing_result(self) -> bool:
        # Check for success messages
        return await self.web_check(By.ID, "checking-done", Is.DISPLAYED) or await self.web_check(By.ID, "not-completed", Is.DISPLAYED)

    async def publish_ads(self, ad_cfgs:list[tuple[str, Ad, dict[str, Any]]]) -> None:
        count = 0
        failed_count = 0
        max_retries = PUBLISH_MAX_RETRIES

        published_ads = json.loads((await self.web_request(f"{self.root_url}/m-meine-anzeigen-verwalten.json?sort=DEFAULT"))["content"])["ads"]

        for ad_file, ad_cfg, ad_cfg_orig in ad_cfgs:
            LOG.info("Processing %s/%s: '%s' from [%s]...", count + 1, len(ad_cfgs), ad_cfg.title, ad_file)

            if [x for x in published_ads if x["id"] == ad_cfg.id and x["state"] == "paused"]:
                LOG.info("Skipping because ad is reserved")
                continue

            count += 1
            success = False

            # Retry loop only for publish_ad (before submission completes)
            for attempt in range(1, max_retries + 1):
                try:
                    await self.publish_ad(ad_file, ad_cfg, ad_cfg_orig, published_ads, AdUpdateStrategy.REPLACE)
                    success = True
                    break  # Publish succeeded, exit retry loop
                except asyncio.CancelledError:
                    raise  # Respect task cancellation
                except (TimeoutError, ProtocolException) as ex:
                    await self._capture_publish_error_diagnostics_if_enabled(ad_cfg, ad_cfg_orig, ad_file, attempt, ex)
                    if attempt < max_retries:
                        LOG.warning("Attempt %s/%s failed for '%s': %s. Retrying...", attempt, max_retries, ad_cfg.title, ex)
                        await self.web_sleep(2)  # Wait before retry
                    else:
                        LOG.error("All %s attempts failed for '%s': %s. Skipping ad.", max_retries, ad_cfg.title, ex)
                        failed_count += 1

            # Check publishing result separately (no retry - ad is already submitted)
            if success:
                try:
                    publish_timeout = self._timeout("publishing_result")
                    await self.web_await(self.__check_publishing_result, timeout = publish_timeout)
                except TimeoutError:
                    LOG.warning(" -> Could not confirm publishing for '%s', but ad may be online", ad_cfg.title)

            if success and self.config.publishing.delete_old_ads == "AFTER_PUBLISH" and not self.keep_old_ads:
                await self.delete_ad(ad_cfg, published_ads, delete_old_ads_by_title = False)

        LOG.info("############################################")
        if failed_count > 0:
            LOG.info("DONE: (Re-)published %s (%s failed after retries)", pluralize("ad", count - failed_count), failed_count)
        else:
            LOG.info("DONE: (Re-)published %s", pluralize("ad", count))
        LOG.info("############################################")

    async def publish_ad(
        self, ad_file:str, ad_cfg:Ad, ad_cfg_orig:dict[str, Any], published_ads:list[dict[str, Any]], mode:AdUpdateStrategy = AdUpdateStrategy.REPLACE
    ) -> None:
        """
        @param ad_cfg: the effective ad config (i.e. with default values applied etc.)
        @param ad_cfg_orig: the ad config as present in the YAML file
        @param published_ads: json list of published ads
        @param mode: the mode of ad editing, either publishing a new or updating an existing ad
        """

        if mode == AdUpdateStrategy.REPLACE:
            if self.config.publishing.delete_old_ads == "BEFORE_PUBLISH" and not self.keep_old_ads:
                await self.delete_ad(ad_cfg, published_ads, delete_old_ads_by_title = self.config.publishing.delete_old_ads_by_title)

            # Apply auto price reduction only for REPLACE operations (actual reposts)
            # This ensures price reductions only happen on republish, not on UPDATE
            try:
                ad_file_relative = str(Path(ad_file).relative_to(Path(self.config_file_path).parent))
            except ValueError:
                # On Windows, relative_to fails when paths are on different drives
                ad_file_relative = ad_file
            apply_auto_price_reduction(ad_cfg, ad_cfg_orig, ad_file_relative)

            LOG.info("Publishing ad '%s'...", ad_cfg.title)
            await self.web_open(f"{self.root_url}/p-anzeige-aufgeben-schritt2.html")
        else:
            LOG.info("Updating ad '%s'...", ad_cfg.title)
            await self.web_open(f"{self.root_url}/p-anzeige-bearbeiten.html?adId={ad_cfg.id}")

        if loggers.is_debug(LOG):
            LOG.debug(" -> effective ad meta:")
            YAML().dump(ad_cfg.model_dump(), sys.stdout)

        if ad_cfg.type == "WANTED":
            await self.web_click(By.ID, "adType2")

        #############################
        # set category (before title to avoid form reset clearing title)
        #############################
        await self.__set_category(ad_cfg.category, ad_file)

        #############################
        # set title
        #############################
        await self.web_input(By.ID, "postad-title", ad_cfg.title)

        #############################
        # set special attributes
        #############################
        await self.__set_special_attributes(ad_cfg)

        #############################
        # set shipping type/options/costs
        #############################
        shipping_type = ad_cfg.shipping_type
        if shipping_type != "NOT_APPLICABLE":
            if ad_cfg.type == "WANTED":
                # special handling for ads of type WANTED since shipping is a special attribute for these
                if shipping_type in {"PICKUP", "SHIPPING"}:
                    shipping_value = "ja" if shipping_type == "SHIPPING" else "nein"
                    try:
                        await self.web_select(By.XPATH, "//select[contains(@id, '.versand_s')]", shipping_value)
                    except TimeoutError:
                        LOG.warning("Failed to set shipping attribute for type '%s'!", shipping_type)
            else:
                await self.__set_shipping(ad_cfg, mode)
        else:
            LOG.debug("Shipping step skipped - reason: NOT_APPLICABLE")

        #############################
        # set price
        #############################
        price_type = ad_cfg.price_type
        if price_type != "NOT_APPLICABLE":
            try:
                await self.web_select(By.CSS_SELECTOR, "select#price-type-react, select#micro-frontend-price-type, select#priceType", price_type)
            except TimeoutError:
                # Price type selector not present on this page variant.
                pass
            if ad_cfg.price:
                if mode == AdUpdateStrategy.MODIFY:
                    # Clear the price field first to prevent concatenation of old and new values
                    # This is needed because some input fields don't clear properly with just clear_input()
                    price_field = await self.web_find(By.CSS_SELECTOR, "input#post-ad-frontend-price, input#micro-frontend-price, input#pstad-price")
                    await price_field.clear_input()
                    await price_field.send_keys("")  # Ensure field is completely empty
                    await self.web_sleep(500)  # Brief pause to ensure clearing is complete
                await self.web_input(By.CSS_SELECTOR, "input#post-ad-frontend-price, input#micro-frontend-price, input#pstad-price", str(ad_cfg.price))

        #############################
        # set sell_directly
        #############################
        sell_directly = ad_cfg.sell_directly
        try:
            if ad_cfg.shipping_type == "SHIPPING":
                if sell_directly and ad_cfg.shipping_options and price_type in {"FIXED", "NEGOTIABLE"}:
                    if not await self.web_check(By.ID, "radio-buy-now-yes", Is.SELECTED):
                        await self.web_click(By.ID, "radio-buy-now-yes")
                elif not await self.web_check(By.ID, "radio-buy-now-no", Is.SELECTED):
                    await self.web_click(By.ID, "radio-buy-now-no")
        except TimeoutError as ex:
            LOG.debug(ex, exc_info = True)

        #############################
        # set description
        #############################
        description = self.__get_description(ad_cfg, with_affixes = True)
        await self.web_execute("document.querySelector('#pstad-descrptn').value = `" + description.replace("`", "'") + "`")

        await self.__set_contact_fields(ad_cfg.contact)

        if mode == AdUpdateStrategy.MODIFY:
            #############################
            # delete previous images because we don't know which have changed
            #############################
            img_items = await self.web_find_all(By.CSS_SELECTOR, "ul#j-pictureupload-thumbnails > li:not(.is-placeholder)")
            for element in img_items:
                btn = await self.web_find(By.CSS_SELECTOR, "button.pictureupload-thumbnails-remove", parent = element)
                await btn.click()

        #############################
        # upload images
        #############################
        await self.__upload_images(ad_cfg)

        #############################
        # wait for captcha
        #############################
        await self.check_and_wait_for_captcha(is_login_page = False)

        #############################
        # submit
        #############################
        try:
            await self.web_click(By.ID, "pstad-submit")
        except TimeoutError:
            # https://github.com/Second-Hand-Friends/kleinanzeigen-bot/issues/40
            await self.web_click(By.XPATH, "//fieldset[@id='postad-publish']//*[contains(., 'Anzeige aufgeben')]")
            await self.web_click(By.ID, "imprint-guidance-submit")

        # check for no image question
        try:
            image_hint_xpath = '//button[contains(., "Ohne Bild veröffentlichen")]'
            if not ad_cfg.images and await self.web_check(By.XPATH, image_hint_xpath, Is.DISPLAYED):
                await self.web_click(By.XPATH, image_hint_xpath)
        except TimeoutError:
            # Image hint not shown; continue publish flow.
            pass  # nosec

        #############################
        # wait for payment form if commercial account is used
        #############################
        try:
            short_timeout = self._timeout("quick_dom")
            await self.web_find(By.ID, "myftr-shppngcrt-frm", timeout = short_timeout)

            LOG.warning("############################################")
            LOG.warning("# Payment form detected! Please proceed with payment.")
            LOG.warning("############################################")
            await self.web_scroll_page_down()
            await ainput(_("Press a key to continue..."))
        except TimeoutError:
            # Payment form not present.
            pass

        confirmation_timeout = self._timeout("publishing_confirmation")
        await self.web_await(lambda: "p-anzeige-aufgeben-bestaetigung.html?adId=" in self.page.url, timeout = confirmation_timeout)

        # extract the ad id from the URL's query parameter
        current_url_query_params = urllib_parse.parse_qs(urllib_parse.urlparse(self.page.url).query)
        ad_id = int(current_url_query_params.get("adId", [])[0])
        ad_cfg_orig["id"] = ad_id

        # Update content hash after successful publication
        # Calculate hash on original config to ensure consistent comparison on restart
        ad_cfg_orig["content_hash"] = AdPartial.model_validate(ad_cfg_orig).update_content_hash().content_hash
        ad_cfg_orig["updated_on"] = misc.now().isoformat(timespec = "seconds")
        if not ad_cfg.created_on and not ad_cfg.id:
            ad_cfg_orig["created_on"] = ad_cfg_orig["updated_on"]

        # Increment repost_count and persist price_reduction_count only for REPLACE operations (actual reposts)
        # This ensures counters only advance on republish, not on UPDATE
        if mode == AdUpdateStrategy.REPLACE:
            # Increment repost_count after successful publish
            # Note: This happens AFTER publish, so price reduction logic (which runs before publish)
            # sees the count from the PREVIOUS run. This is intentional: the first publish uses
            # repost_count=0 (no reduction), the second publish uses repost_count=1 (first reduction), etc.
            current_reposts = int(ad_cfg_orig.get("repost_count", ad_cfg.repost_count or 0))
            ad_cfg_orig["repost_count"] = current_reposts + 1
            ad_cfg.repost_count = ad_cfg_orig["repost_count"]

            # Persist price_reduction_count after successful publish
            # This ensures failed publishes don't incorrectly increment the reduction counter
            if ad_cfg.price_reduction_count is not None and ad_cfg.price_reduction_count > 0:
                ad_cfg_orig["price_reduction_count"] = ad_cfg.price_reduction_count

        if mode == AdUpdateStrategy.REPLACE:
            LOG.info(" -> SUCCESS: ad published with ID %s", ad_id)
        else:
            LOG.info(" -> SUCCESS: ad updated with ID %s", ad_id)

        dicts.save_dict(ad_file, ad_cfg_orig)

    async def __set_contact_fields(self, contact:Contact) -> None:
        #############################
        # set contact zipcode
        #############################
        if contact.zipcode:
            zipcode_set = True
            try:
                zip_field = await self.web_find(By.ID, "pstad-zip")
                if zip_field is None:
                    raise TimeoutError("ZIP input not found")
                await zip_field.clear_input()
            except TimeoutError:
                # fall back to standard input below
                pass
            try:
                await self.web_input(By.ID, "pstad-zip", contact.zipcode)
            except TimeoutError:
                LOG.warning("Could not set contact zipcode: %s", contact.zipcode)
                zipcode_set = False
            # Set city if location is specified
            if contact.location and zipcode_set:
                try:
                    options = await self.web_find_all(By.CSS_SELECTOR, "#pstad-citychsr option")

                    found = False
                    for option in options:
                        opt_text = option.text.strip()
                        target = contact.location.strip()
                        if opt_text == target:
                            await self.web_select(By.ID, "pstad-citychsr", option.attrs.value)
                            found = True
                            break
                        if " - " in opt_text and opt_text.split(" - ", 1)[1] == target:
                            await self.web_select(By.ID, "pstad-citychsr", option.attrs.value)
                            found = True
                            break
                    if not found:
                        LOG.warning("No city dropdown option matched location: %s", contact.location)
                except TimeoutError:
                    LOG.warning("Could not set contact location: %s", contact.location)

        #############################
        # set contact street
        #############################
        if contact.street:
            try:
                if await self.web_check(By.ID, "pstad-street", Is.DISABLED):
                    await self.web_click(By.ID, "addressVisibility")
                    await self.web_sleep()
                await self.web_input(By.ID, "pstad-street", contact.street)
            except TimeoutError:
                LOG.warning("Could not set contact street.")

        #############################
        # set contact name
        #############################
        if contact.name:
            try:
                if not await self.web_check(By.ID, "postad-contactname", Is.READONLY):
                    await self.web_input(By.ID, "postad-contactname", contact.name)
            except TimeoutError:
                LOG.warning("Could not set contact name.")

        #############################
        # set contact phone
        #############################
        if contact.phone:
            try:
                if await self.web_check(By.ID, "postad-phonenumber", Is.DISPLAYED):
                    try:
                        if await self.web_check(By.ID, "postad-phonenumber", Is.DISABLED):
                            await self.web_click(By.ID, "phoneNumberVisibility")
                            await self.web_sleep()
                    except TimeoutError:
                        # ignore
                        pass
                    await self.web_input(By.ID, "postad-phonenumber", contact.phone)
            except TimeoutError:
                LOG.warning(
                    _(
                        "Phone number field not present on page. This is expected for many private accounts; "
                        "commercial accounts may still support phone numbers."
                    )
                )

    async def update_ads(self, ad_cfgs:list[tuple[str, Ad, dict[str, Any]]]) -> None:
        """
        Updates a list of ads.
        The list gets filtered, so that only already published ads will be updated.
        Calls publish_ad in MODIFY mode.

        Args:
            ad_cfgs: List of ad configurations

        Returns:
            None
        """
        count = 0

        published_ads = json.loads((await self.web_request(f"{self.root_url}/m-meine-anzeigen-verwalten.json?sort=DEFAULT"))["content"])["ads"]

        for ad_file, ad_cfg, ad_cfg_orig in ad_cfgs:
            ad = next((ad for ad in published_ads if ad["id"] == ad_cfg.id), None)

            if not ad:
                continue

            LOG.info("Processing %s/%s: '%s' from [%s]...", count + 1, len(ad_cfgs), ad_cfg.title, ad_file)
            if ad["state"] == "paused":
                LOG.info("Skipping because ad is reserved")
                continue

            count += 1

            await self.publish_ad(ad_file, ad_cfg, ad_cfg_orig, published_ads, AdUpdateStrategy.MODIFY)
            publish_timeout = self._timeout("publishing_result")
            await self.web_await(self.__check_publishing_result, timeout = publish_timeout)

        LOG.info("############################################")
        LOG.info("DONE: updated %s", pluralize("ad", count))
        LOG.info("############################################")

    async def __set_condition(self, condition_value:str) -> None:
        try:
            # Open condition dialog
            await self.web_click(By.XPATH, '//*[@id="j-post-listing-frontend-conditions"]//button[@aria-haspopup="true"]')
        except TimeoutError:
            LOG.debug("Unable to open condition dialog and select condition [%s]", condition_value, exc_info = True)
            return

        try:
            # Click radio button
            await self.web_click(By.ID, f"radio-button-{condition_value}")
        except TimeoutError:
            LOG.debug("Unable to select condition [%s]", condition_value, exc_info = True)

        try:
            # Click accept button
            await self.web_click(By.XPATH, '//dialog//button[.//span[text()="Bestätigen"]]')
        except TimeoutError as ex:
            raise TimeoutError(_("Unable to close condition dialog!")) from ex

    async def __set_category(self, category:str | None, ad_file:str) -> None:
        # click on something to trigger automatic category detection
        await self.web_click(By.ID, "pstad-descrptn")

        is_category_auto_selected = False
        try:
            if await self.web_text(By.ID, "postad-category-path"):
                is_category_auto_selected = True
        except TimeoutError:
            # Category auto-selection indicator not available within timeout.
            pass

        if category:
            await self.web_sleep()  # workaround for https://github.com/Second-Hand-Friends/kleinanzeigen-bot/issues/39
            await self.web_click(By.ID, "pstad-lnk-chngeCtgry")
            await self.web_find(By.ID, "postad-step1-sbmt")

            category_url = f"{self.root_url}/p-kategorie-aendern.html#?path={category}"
            await self.web_open(category_url)
            await self.web_click(By.XPATH, "//*[@id='postad-step1-sbmt']/button")
        else:
            ensure(is_category_auto_selected, f"No category specified in [{ad_file}] and automatic category detection failed")

    async def __set_special_attributes(self, ad_cfg:Ad) -> None:
        if not ad_cfg.special_attributes:
            return

        LOG.debug("Found %i special attributes", len(ad_cfg.special_attributes))
        for special_attribute_key, special_attribute_value in ad_cfg.special_attributes.items():
            # Ensure special_attribute_value is treated as a string
            special_attribute_value_str = str(special_attribute_value)

            if special_attribute_key == "condition_s":
                await self.__set_condition(special_attribute_value_str)
                continue

            LOG.debug("Setting special attribute [%s] to [%s]...", special_attribute_key, special_attribute_value_str)
            try:
                # if the <select> element exists but is inside an invisible container, make the container visible
                select_container_xpath = f"//div[@class='l-row' and descendant::select[@id='{special_attribute_key}']]"
                if not await self.web_check(By.XPATH, select_container_xpath, Is.DISPLAYED):
                    await (await self.web_find(By.XPATH, select_container_xpath)).apply("elem => elem.singleNodeValue.style.display = 'block'")
            except TimeoutError:
                # Skip visibility adjustment when container cannot be located in time.
                pass  # nosec

            try:
                # finding element by name cause id are composed sometimes eg. autos.marke_s+autos.model_s for Modell by cars
                special_attr_elem = await self.web_find(By.XPATH, f"//*[contains(@name, '{special_attribute_key}')]")
            except TimeoutError:
                # Trying to find element by ID instead cause sometimes there is NO name attribute...
                try:
                    special_attr_elem = await self.web_find(By.ID, special_attribute_key)
                except TimeoutError as ex:
                    LOG.debug("Attribute field '%s' could not be found.", special_attribute_key)
                    raise TimeoutError(_("Failed to set attribute '%s'") % special_attribute_key) from ex

            try:
                elem_id:str = str(special_attr_elem.attrs.id)
                if special_attr_elem.local_name == "select":
                    LOG.debug("Attribute field '%s' seems to be a select...", special_attribute_key)
                    await self.web_select(By.ID, elem_id, special_attribute_value_str)
                elif special_attr_elem.attrs.type == "checkbox":
                    LOG.debug("Attribute field '%s' seems to be a checkbox...", special_attribute_key)
                    await self.web_click(By.ID, elem_id)
                elif special_attr_elem.attrs.type == "text" and special_attr_elem.attrs.get("role") == "combobox":
                    LOG.debug("Attribute field '%s' seems to be a Combobox (i.e. text input with filtering dropdown)...", special_attribute_key)
                    await self.web_select_combobox(By.ID, elem_id, special_attribute_value_str)
                else:
                    LOG.debug("Attribute field '%s' seems to be a text input...", special_attribute_key)
                    await self.web_input(By.ID, elem_id, special_attribute_value_str)
            except TimeoutError as ex:
                LOG.debug("Failed to set attribute field '%s' via known input types.", special_attribute_key)
                raise TimeoutError(_("Failed to set attribute '%s'") % special_attribute_key) from ex
            LOG.debug("Successfully set attribute field [%s] to [%s]...", special_attribute_key, special_attribute_value_str)

    async def __set_shipping(self, ad_cfg:Ad, mode:AdUpdateStrategy = AdUpdateStrategy.REPLACE) -> None:
        short_timeout = self._timeout("quick_dom")
        if ad_cfg.shipping_type == "PICKUP":
            try:
                await self.web_click(By.ID, "radio-pickup")
            except TimeoutError as ex:
                LOG.debug(ex, exc_info = True)
        elif ad_cfg.shipping_options:
            await self.web_click(By.XPATH, '//button//span[contains(., "Versandmethoden auswählen")]')

            if mode == AdUpdateStrategy.MODIFY:
                try:
                    # when "Andere Versandmethoden" is not available, go back and start over new
                    await self.web_find(By.XPATH, '//dialog//button[contains(., "Andere Versandmethoden")]', timeout = short_timeout)
                except TimeoutError:
                    await self.web_click(By.XPATH, '//dialog//button[contains(., "Zurück")]')

                    # in some categories we need to go another dialog back
                    try:
                        await self.web_find(By.XPATH, '//dialog//button[contains(., "Andere Versandmethoden")]', timeout = short_timeout)
                    except TimeoutError:
                        await self.web_click(By.XPATH, '//dialog//button[contains(., "Zurück")]')

            await self.web_click(By.XPATH, '//dialog//button[contains(., "Andere Versandmethoden")]')
            await self.__set_shipping_options(ad_cfg, mode)
        else:
            special_shipping_selector = '//select[contains(@id, ".versand_s")]'
            if await self.web_check(By.XPATH, special_shipping_selector, Is.DISPLAYED):
                # try to set special attribute selector (then we have a commercial account)
                shipping_value = "ja" if ad_cfg.shipping_type == "SHIPPING" else "nein"
                await self.web_select(By.XPATH, special_shipping_selector, shipping_value)
            else:
                try:
                    # no options. only costs. Set custom shipping cost
                    await self.web_click(By.XPATH, '//button//span[contains(., "Versandmethoden auswählen")]')
                    try:
                        # when "Andere Versandmethoden" is not available, then we are already on the individual page
                        await self.web_click(By.XPATH, '//dialog//button[contains(., "Andere Versandmethoden")]')
                    except TimeoutError:
                        # Dialog option not present; already on the individual shipping page.
                        pass

                    try:
                        # only click on "Individueller Versand" when "IndividualShippingInput" is not available, otherwise its already checked
                        # (important for mode = UPDATE)
                        await self.web_find(By.XPATH, '//input[contains(@placeholder, "Versandkosten (optional)")]', timeout = short_timeout)
                    except TimeoutError:
                        # Input not visible yet; click the individual shipping option.
                        await self.web_click(By.XPATH, '//*[contains(@id, "INDIVIDUAL") and contains(@data-testid, "Individueller Versand")]')

                    if ad_cfg.shipping_costs is not None:
                        await self.web_input(
                            By.XPATH, '//input[contains(@placeholder, "Versandkosten (optional)")]', str.replace(str(ad_cfg.shipping_costs), ".", ",")
                        )
                    await self.web_click(By.XPATH, '//dialog//button[contains(., "Fertig")]')
                except TimeoutError as ex:
                    LOG.debug(ex, exc_info = True)
                    raise TimeoutError(_("Unable to close shipping dialog!")) from ex

    async def __set_shipping_options(self, ad_cfg:Ad, mode:AdUpdateStrategy = AdUpdateStrategy.REPLACE) -> None:
        if not ad_cfg.shipping_options:
            return

        shipping_options_mapping = {
            "DHL_2": ("Klein", "SMALL", "Paket 2 kg"),
            "Hermes_Päckchen": ("Klein", "SMALL", "Päckchen"),
            "Hermes_S": ("Klein", "SMALL", "S-Paket"),
            "DHL_5": ("Mittel", "MEDIUM", "Paket 5 kg"),
            "Hermes_M": ("Mittel", "MEDIUM", "M-Paket"),
            "DHL_10": ("Groß", "LARGE", "Paket 10 kg"),
            "DHL_20": ("Groß", "LARGE", "Paket 20 kg"),
            "DHL_31,5": ("Groß", "LARGE", "Paket 31,5 kg"),
            "Hermes_L": ("Groß", "LARGE", "L-Paket"),
        }
        try:
            mapped_shipping_options = [shipping_options_mapping[option] for option in set(ad_cfg.shipping_options)]
        except KeyError as ex:
            raise KeyError(f"Unknown shipping option(s), please refer to the documentation/README: {ad_cfg.shipping_options}") from ex

        shipping_sizes, shipping_selector, shipping_packages = zip(*mapped_shipping_options, strict = False)

        try:
            (shipping_size,) = set(shipping_sizes)
        except ValueError as ex:
            raise ValueError("You can only specify shipping options for one package size!") from ex

        try:
            shipping_radio_selector = shipping_selector[0]
            shipping_size_radio = await self.web_find(By.ID, f"radio-button-{shipping_radio_selector}")
            shipping_size_radio_is_checked = hasattr(shipping_size_radio.attrs, "checked")

            if shipping_size_radio_is_checked:
                # in the same size category all options are preselected, so deselect the unwanted ones
                unwanted_shipping_packages = [
                    package for size, selector, package in shipping_options_mapping.values() if size == shipping_size and package not in shipping_packages
                ]
                to_be_clicked_shipping_packages = unwanted_shipping_packages
            else:
                # in a different size category nothing is preselected, so select all we want
                await self.web_click(By.ID, f"radio-button-{shipping_radio_selector}")
                to_be_clicked_shipping_packages = list(shipping_packages)

            await self.web_click(By.XPATH, '//dialog//button[contains(., "Weiter")]')

            if mode == AdUpdateStrategy.MODIFY:
                # in update mode we cannot rely on any information and have to (de-)select every package
                LOG.debug("Using MODIFY mode logic for shipping options")

                # get only correct size
                selected_size_shipping_packages = [package for size, selector, package in shipping_options_mapping.values() if size == shipping_size]
                LOG.debug("Processing %d packages for size '%s'", len(selected_size_shipping_packages), shipping_size)

                for shipping_package in selected_size_shipping_packages:
                    shipping_package_xpath = f'//dialog//input[contains(@data-testid, "{shipping_package}")]'
                    shipping_package_checkbox = await self.web_find(By.XPATH, shipping_package_xpath)
                    shipping_package_checkbox_is_checked = hasattr(shipping_package_checkbox.attrs, "checked")

                    LOG.debug(
                        "Package '%s': checked=%s, wanted=%s", shipping_package, shipping_package_checkbox_is_checked, shipping_package in shipping_packages
                    )

                    # select wanted packages if not checked already
                    if shipping_package in shipping_packages:
                        if not shipping_package_checkbox_is_checked:
                            # select
                            LOG.debug("Selecting package '%s'", shipping_package)
                            await self.web_click(By.XPATH, shipping_package_xpath)
                    # deselect unwanted if selected
                    elif shipping_package_checkbox_is_checked:
                        LOG.debug("Deselecting package '%s'", shipping_package)
                        await self.web_click(By.XPATH, shipping_package_xpath)
            else:
                for shipping_package in to_be_clicked_shipping_packages:
                    await self.web_click(By.XPATH, f'//dialog//input[contains(@data-testid, "{shipping_package}")]')
        except TimeoutError as ex:
            LOG.debug(ex, exc_info = True)
        try:
            # Click apply button
            await self.web_click(By.XPATH, '//dialog//button[contains(., "Fertig")]')
        except TimeoutError as ex:
            raise TimeoutError(_("Unable to close shipping dialog!")) from ex

    async def __upload_images(self, ad_cfg:Ad) -> None:
        if not ad_cfg.images:
            return

        LOG.info(" -> found %s", pluralize("image", ad_cfg.images))
        image_upload:Element = await self.web_find(By.CSS_SELECTOR, "input[type=file]")

        for image in ad_cfg.images:
            LOG.info(" -> uploading image [%s]", image)
            await image_upload.send_file(image)
            await self.web_sleep()

        # Wait for all images to be processed and thumbnails to appear
        expected_count = len(ad_cfg.images)
        LOG.info(" -> waiting for %s to be processed...", pluralize("image", ad_cfg.images))

        async def check_thumbnails_uploaded() -> bool:
            try:
                thumbnails = await self.web_find_all(
                    By.CSS_SELECTOR,
                    "ul#j-pictureupload-thumbnails > li:not(.is-placeholder)",
                    timeout = self._timeout("quick_dom"),  # Fast timeout for polling
                )
                current_count = len(thumbnails)
                if current_count < expected_count:
                    LOG.debug(" -> %d of %d images processed", current_count, expected_count)
                return current_count == expected_count
            except TimeoutError:
                # No thumbnails found yet, continue polling
                return False

        try:
            await self.web_await(check_thumbnails_uploaded, timeout = self._timeout("image_upload"), timeout_error_message = _("Image upload timeout exceeded"))
        except TimeoutError as ex:
            # Get current count for better error message
            try:
                thumbnails = await self.web_find_all(
                    By.CSS_SELECTOR, "ul#j-pictureupload-thumbnails > li:not(.is-placeholder)", timeout = self._timeout("quick_dom")
                )
                current_count = len(thumbnails)
            except TimeoutError:
                # Still no thumbnails after full timeout
                current_count = 0
            raise TimeoutError(
                _("Not all images were uploaded within timeout. Expected %(expected)d, found %(found)d thumbnails.")
                % {"expected": expected_count, "found": current_count}
            ) from ex

        LOG.info(" -> all images uploaded successfully")

    async def download_ads(self) -> None:
        """
        Determines which download mode was chosen with the arguments, and calls the specified download routine.
        This downloads either all, only unsaved (new), or specific ads given by ID.
        """

        ad_extractor = extract.AdExtractor(self.browser, self.config, self.installation_mode_or_portable)

        # use relevant download routine
        if self.ads_selector in {"all", "new"}:  # explore ads overview for these two modes
            LOG.info("Scanning your ad overview...")
            own_ad_urls = await ad_extractor.extract_own_ads_urls()
            LOG.info("%s found.", pluralize("ad", len(own_ad_urls)))

            if self.ads_selector == "all":  # download all of your adds
                LOG.info("Starting download of all ads...")

                success_count = 0
                # call download function for each ad page
                for add_url in own_ad_urls:
                    ad_id = ad_extractor.extract_ad_id_from_ad_url(add_url)
                    if await ad_extractor.navigate_to_ad_page(add_url):
                        await ad_extractor.download_ad(ad_id)
                        success_count += 1
                LOG.info("%d of %d ads were downloaded from your profile.", success_count, len(own_ad_urls))

            elif self.ads_selector == "new":  # download only unsaved ads
                # check which ads already saved
                saved_ad_ids = []
                ads = self.load_ads(ignore_inactive = False, exclude_ads_with_id = False)  # do not skip because of existing IDs
                for ad in ads:
                    saved_ad_id = ad[1].id
                    if saved_ad_id is None:
                        LOG.debug("Skipping saved ad without id (likely unpublished or manually created): %s", ad[0])
                        continue
                    saved_ad_ids.append(int(saved_ad_id))

                # determine ad IDs from links
                ad_id_by_url = {url: ad_extractor.extract_ad_id_from_ad_url(url) for url in own_ad_urls}

                LOG.info("Starting download of not yet downloaded ads...")
                new_count = 0
                for ad_url, ad_id in ad_id_by_url.items():
                    # check if ad with ID already saved
                    if ad_id in saved_ad_ids:
                        LOG.info("The ad with id %d has already been saved.", ad_id)
                        continue

                    if await ad_extractor.navigate_to_ad_page(ad_url):
                        await ad_extractor.download_ad(ad_id)
                        new_count += 1
                LOG.info("%s were downloaded from your profile.", pluralize("new ad", new_count))

        elif re.compile(r"\d+[,\d+]*").search(self.ads_selector):  # download ad(s) with specific id(s)
            ids = [int(n) for n in self.ads_selector.split(",")]
            LOG.info("Starting download of ad(s) with the id(s):")
            LOG.info(" | ".join([str(ad_id) for ad_id in ids]))

            for ad_id in ids:  # call download routine for every id
                exists = await ad_extractor.navigate_to_ad_page(ad_id)
                if exists:
                    await ad_extractor.download_ad(ad_id)
                    LOG.info("Downloaded ad with id %d", ad_id)
                else:
                    LOG.error("The page with the id %d does not exist!", ad_id)

    def __get_description(self, ad_cfg:Ad, *, with_affixes:bool) -> str:
        """Get the ad description optionally with prefix and suffix applied.

        Precedence (highest to lowest):
        1. Direct ad-level affixes (description_prefix/suffix)
        2. Global flattened affixes (ad_defaults.description_prefix/suffix)
        3. Legacy global nested affixes (ad_defaults.description.prefix/suffix)

        Args:
            ad_cfg: The ad configuration dictionary

        Returns:
            The raw or complete description with prefix and suffix applied
        """
        # Get the main description text
        description_text = ""
        if ad_cfg.description:
            description_text = ad_cfg.description

        if with_affixes:
            # Get prefix with precedence
            prefix = (
                # 1. Direct ad-level prefix
                ad_cfg.description_prefix
                if ad_cfg.description_prefix is not None
                # 2. Global prefix from config
                else self.config.ad_defaults.description_prefix or ""  # Default to empty string if all sources are None
            )

            # Get suffix with precedence
            suffix = (
                # 1. Direct ad-level suffix
                ad_cfg.description_suffix
                if ad_cfg.description_suffix is not None
                # 2. Global suffix from config
                else self.config.ad_defaults.description_suffix or ""  # Default to empty string if all sources are None
            )

            # Combine the parts and replace @ with (at)
            final_description = str(prefix) + str(description_text) + str(suffix)
            final_description = final_description.replace("@", "(at)")
        else:
            final_description = description_text

        # Validate length
        ensure(
            len(final_description) <= MAX_DESCRIPTION_LENGTH,
            f"Length of ad description including prefix and suffix exceeds {MAX_DESCRIPTION_LENGTH} chars. Description length: {len(final_description)} chars.",
        )

        return final_description

    def update_content_hashes(self, ads:list[tuple[str, Ad, dict[str, Any]]]) -> None:
        count = 0

        for ad_file, ad_cfg, ad_cfg_orig in ads:
            LOG.info("Processing %s/%s: '%s' from [%s]...", count + 1, len(ads), ad_cfg.title, ad_file)
            ad_cfg.update_content_hash()
            if ad_cfg.content_hash != ad_cfg_orig["content_hash"]:
                count += 1
                ad_cfg_orig["content_hash"] = ad_cfg.content_hash
                dicts.save_dict(ad_file, ad_cfg_orig)

        LOG.info("############################################")
        LOG.info("DONE: Updated [content_hash] in %s", pluralize("ad", count))
        LOG.info("############################################")


#############################
# main entry point
#############################


def main(args:list[str]) -> None:
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

    loggers.configure_console_logging()

    signal.signal(signal.SIGINT, error_handlers.on_sigint)  # capture CTRL+C

    # sys.excepthook = error_handlers.on_exception
    # -> commented out because it causes PyInstaller to log "[PYI-28040:ERROR] Failed to execute script '__main__' due to unhandled exception!",
    #    despite the exceptions being properly processed by our custom error_handlers.on_exception callback.
    #    We now handle exceptions explicitly using a top-level try/except block.

    atexit.register(loggers.flush_all_handlers)

    try:
        bot = KleinanzeigenBot()
        atexit.register(bot.close_browser_session)
        nodriver.loop().run_until_complete(bot.run(args))  # type: ignore[attr-defined]
    except CaptchaEncountered as ex:
        raise ex
    except Exception:
        error_handlers.on_exception(*sys.exc_info())


if __name__ == "__main__":
    loggers.configure_console_logging()
    LOG.error("Direct execution not supported. Use 'pdm run app'")
    sys.exit(1)
