# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

"""XDG Base Directory path resolution with backward compatibility.

Supports two installation modes:
- Portable: All files in current working directory (for existing installations)
- System-wide: Files organized in XDG directories (for new installations or package managers)
"""

from __future__ import annotations

import logging
import sys
from gettext import gettext as _
from pathlib import Path
from typing import Final, Literal

import platformdirs

LOG = logging.getLogger(__name__)

APP_NAME:Final[str] = "kleinanzeigen-bot"

InstallationMode = Literal["portable", "xdg"]
PathCategory = Literal["config", "cache", "state"]


def get_xdg_base_dir(category:PathCategory) -> Path:
    """Get XDG base directory for the given category.

    Args:
        category: The XDG category (config, cache, or state)

    Returns:
        Path to the XDG base directory for this app
    """
    match category:
        case "config":
            base_dir = Path(platformdirs.user_config_dir(APP_NAME))
        case "cache":
            base_dir = Path(platformdirs.user_cache_dir(APP_NAME))
        case "state":
            base_dir = Path(platformdirs.user_state_dir(APP_NAME))
        case _:
            raise ValueError(f"Unsupported XDG category: {category}")

    LOG.debug("XDG %s directory: %s", category, base_dir)
    return base_dir


def detect_installation_mode() -> InstallationMode | None:
    """Detect installation mode based on config file location.

    Returns:
        "portable" if ./config.yaml exists in CWD
        "xdg" if config exists in XDG location
        None if neither exists (first run)
    """
    # Check for portable installation (./config.yaml in CWD)
    portable_config = Path.cwd() / "config.yaml"
    LOG.debug(_("Checking for portable config at: %s"), portable_config)

    if portable_config.exists():
        LOG.info(_("Detected installation mode: %s"), "portable")
        return "portable"

    # Check for XDG installation
    xdg_config = get_xdg_base_dir("config") / "config.yaml"
    LOG.debug(_("Checking for XDG config at: %s"), xdg_config)

    if xdg_config.exists():
        LOG.info(_("Detected installation mode: %s"), "xdg")
        return "xdg"

    # Neither exists - first run
    LOG.info(_("No existing installation found"))
    return None


def prompt_installation_mode() -> InstallationMode:
    """Prompt user to choose installation mode on first run.

    Returns:
        "portable" or "xdg" based on user choice, or "portable" as default for non-interactive mode
    """
    # Check if running in non-interactive mode (no stdin or not a TTY)
    if not sys.stdin or not sys.stdin.isatty():
        LOG.info("Non-interactive mode detected, defaulting to portable installation")
        return "portable"

    print(_("Choose installation type:"))
    print(_("[1] Portable (current directory)"))
    print(_("[2] System-wide (XDG directories)"))

    while True:
        try:
            choice = input("Enter 1 or 2: ").strip()
        except (EOFError, KeyboardInterrupt):
            # Non-interactive or interrupted - default to portable
            print()  # newline after ^C or EOF
            LOG.info("Defaulting to portable installation mode")
            return "portable"

        if choice == "1":
            mode:InstallationMode = "portable"
            LOG.info(_("User selected installation mode: %s"), mode)
            return mode
        if choice == "2":
            mode = "xdg"
            LOG.info(_("User selected installation mode: %s"), mode)
            return mode
        print(_("Invalid choice. Please enter 1 or 2."))


def get_config_file_path(mode:str | InstallationMode) -> Path:
    """Get config.yaml file path for the given mode.

    Args:
        mode: Installation mode (portable or xdg)

    Returns:
        Path to config.yaml
    """
    config_path = Path.cwd() / "config.yaml" if mode == "portable" else get_xdg_base_dir("config") / "config.yaml"

    LOG.debug("Resolving config file path for mode '%s': %s", mode, config_path)
    return config_path


def get_ad_files_search_dir(mode:str | InstallationMode) -> Path:
    """Get directory to search for ad files.

    Ad files are searched relative to the config file directory,
    matching the documented behavior that glob patterns are relative to config.yaml.

    Args:
        mode: Installation mode (portable or xdg)

    Returns:
        Path to ad files search directory (same as config file directory)
    """
    search_dir = Path.cwd() if mode == "portable" else get_xdg_base_dir("config")

    LOG.debug("Resolving ad files search directory for mode '%s': %s", mode, search_dir)
    return search_dir


def get_downloaded_ads_path(mode:str | InstallationMode) -> Path:
    """Get downloaded ads directory path.

    Args:
        mode: Installation mode (portable or xdg)

    Returns:
        Path to downloaded ads directory
    """
    ads_path = Path.cwd() / "downloaded-ads" if mode == "portable" else get_xdg_base_dir("config") / "downloaded-ads"

    LOG.debug("Resolving downloaded ads path for mode '%s': %s", mode, ads_path)

    # Create directory if it doesn't exist
    if not ads_path.exists():
        LOG.debug("Creating directory: %s", ads_path)
        ads_path.mkdir(parents = True, exist_ok = True)

    return ads_path


def get_browser_profile_path(mode:str | InstallationMode, config_override:str | None = None) -> Path:
    """Get browser profile directory path.

    Args:
        mode: Installation mode (portable or xdg)
        config_override: Optional config override path (takes precedence)

    Returns:
        Path to browser profile directory
    """
    if config_override:
        profile_path = Path(config_override)
        LOG.debug("Resolving browser profile path for mode '%s' (config override): %s", mode, profile_path)
    elif mode == "portable":
        profile_path = Path.cwd() / ".temp" / "browser-profile"
        LOG.debug("Resolving browser profile path for mode '%s': %s", mode, profile_path)
    else:  # xdg
        profile_path = get_xdg_base_dir("cache") / "browser-profile"
        LOG.debug("Resolving browser profile path for mode '%s': %s", mode, profile_path)

    # Create directory if it doesn't exist
    if not profile_path.exists():
        LOG.debug("Creating directory: %s", profile_path)
        profile_path.mkdir(parents = True, exist_ok = True)

    return profile_path


def get_log_file_path(basename:str, mode:str | InstallationMode) -> Path:
    """Get log file path.

    Args:
        basename: Log file basename (without .log extension)
        mode: Installation mode (portable or xdg)

    Returns:
        Path to log file
    """
    log_path = Path.cwd() / f"{basename}.log" if mode == "portable" else get_xdg_base_dir("state") / f"{basename}.log"

    LOG.debug("Resolving log file path for mode '%s': %s", mode, log_path)

    # Create parent directory if it doesn't exist
    if not log_path.parent.exists():
        LOG.debug("Creating directory: %s", log_path.parent)
        log_path.parent.mkdir(parents = True, exist_ok = True)

    return log_path


def get_update_check_state_path(mode:str | InstallationMode) -> Path:
    """Get update check state file path.

    Args:
        mode: Installation mode (portable or xdg)

    Returns:
        Path to update check state file
    """
    state_path = Path.cwd() / ".temp" / "update_check_state.json" if mode == "portable" else get_xdg_base_dir("state") / "update_check_state.json"

    LOG.debug("Resolving update check state path for mode '%s': %s", mode, state_path)

    # Create parent directory if it doesn't exist
    if not state_path.parent.exists():
        LOG.debug("Creating directory: %s", state_path.parent)
        state_path.parent.mkdir(parents = True, exist_ok = True)

    return state_path
