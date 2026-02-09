# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

"""XDG Base Directory path resolution with workspace abstraction."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from gettext import gettext as _
from pathlib import Path
from typing import Final, Literal

import platformdirs

from kleinanzeigen_bot.utils import loggers
from kleinanzeigen_bot.utils.files import abspath

LOG:Final[loggers.Logger] = loggers.get_logger(__name__)

APP_NAME:Final[str] = "kleinanzeigen-bot"
PathCategory = Literal["config", "cache", "state"]


@dataclass(frozen = True)
class Workspace:
    """Resolved workspace paths for all bot side effects."""

    config_file:Path
    config_dir:Path
    log_file:Path | None
    state_dir:Path
    download_dir:Path
    browser_profile_dir:Path
    diagnostics_dir:Path

    @classmethod
    def for_config(cls, config_file:Path, log_basename:str) -> Workspace:
        """Build a portable-style workspace rooted at the config parent directory."""
        config_file = config_file.resolve()
        config_dir = config_file.parent
        state_dir = config_dir / ".temp"
        return cls(
            config_file = config_file,
            config_dir = config_dir,
            log_file = config_dir / f"{log_basename}.log",
            state_dir = state_dir,
            download_dir = config_dir / "downloaded-ads",
            browser_profile_dir = state_dir / "browser-profile",
            diagnostics_dir = state_dir / "diagnostics",
        )


def ensure_directory(path:Path, description:str) -> None:
    """Create directory and verify it exists."""
    LOG.debug("Creating directory: %s", path)
    try:
        path.mkdir(parents = True, exist_ok = True)
    except OSError as exc:
        LOG.error("Failed to create %s %s: %s", description, path, exc)
        raise
    if not path.is_dir():
        raise NotADirectoryError(str(path))


def _ensure_directory(path:Path, description:str) -> None:
    """Backward-compatible alias for ensure_directory."""
    ensure_directory(path, description)


def _build_xdg_workspace(log_basename:str) -> Workspace:
    """Build an XDG-style workspace using standard user directories."""
    config_file = (get_xdg_base_dir("config") / "config.yaml").resolve()
    config_dir = config_file.parent
    return Workspace(
        config_file = config_file,
        config_dir = config_dir,
        log_file = config_dir / f"{log_basename}.log",
        state_dir = get_xdg_base_dir("state").resolve(),
        download_dir = config_dir / "downloaded-ads",
        browser_profile_dir = (get_xdg_base_dir("cache") / "browser-profile").resolve(),
        diagnostics_dir = (get_xdg_base_dir("cache") / "diagnostics").resolve(),
    )


def get_xdg_base_dir(category:PathCategory) -> Path:
    """Get XDG base directory for the given category."""
    resolved:str | None = None
    match category:
        case "config":
            resolved = platformdirs.user_config_dir(APP_NAME)
        case "cache":
            resolved = platformdirs.user_cache_dir(APP_NAME)
        case "state":
            resolved = platformdirs.user_state_dir(APP_NAME)
        case _:
            raise ValueError(f"Unsupported XDG category: {category}")

    if resolved is None:
        raise RuntimeError(f"Failed to resolve XDG base directory for category: {category}")

    base_dir = Path(resolved)
    LOG.debug("XDG %s directory: %s", category, base_dir)
    return base_dir


def detect_installation_mode() -> Literal["portable", "xdg"] | None:
    """Detect installation mode based on config file location."""
    portable_config = Path.cwd() / "config.yaml"
    LOG.debug("Checking for portable config at: %s", portable_config)

    if portable_config.exists():
        LOG.debug("Detected installation mode: %s", "portable")
        return "portable"

    xdg_config = get_xdg_base_dir("config") / "config.yaml"
    LOG.debug("Checking for XDG config at: %s", xdg_config)

    if xdg_config.exists():
        LOG.debug("Detected installation mode: %s", "xdg")
        return "xdg"

    LOG.info("No existing configuration (portable or system-wide) found")
    return None


def prompt_installation_mode() -> Literal["portable", "xdg"]:
    """Prompt user to choose installation mode on first run."""
    if not sys.stdin or not sys.stdin.isatty():
        LOG.info("Non-interactive mode detected, defaulting to portable installation")
        return "portable"

    portable_ws = Workspace.for_config((Path.cwd() / "config.yaml").resolve(), APP_NAME)
    xdg_workspace = _build_xdg_workspace(APP_NAME)

    print(_("Choose installation type:"))
    print(_("[1] Portable (current directory)"))
    print(f"    config: {portable_ws.config_file}")
    print(f"    log:    {portable_ws.log_file}")
    print(_("[2] User directories (per-user standard locations)"))
    print(f"    config: {xdg_workspace.config_file}")
    print(f"    log:    {xdg_workspace.log_file}")

    while True:
        try:
            choice = input(_("Enter 1 or 2: ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            LOG.info("Defaulting to portable installation mode")
            return "portable"

        if choice == "1":
            mode:Literal["portable", "xdg"] = "portable"
            LOG.info("User selected installation mode: %s", mode)
            return mode
        if choice == "2":
            mode = "xdg"
            LOG.info("User selected installation mode: %s", mode)
            return mode
        print(_("Invalid choice. Please enter 1 or 2."))


def resolve_workspace(
    config_arg:str | None,
    logfile_arg:str | None,
    *,
    logfile_explicitly_provided:bool,
    log_basename:str,
) -> Workspace:
    """Resolve workspace paths from CLI flags and auto-detected installation mode."""
    if config_arg:
        workspace = Workspace.for_config(Path(abspath(config_arg)), log_basename)
    else:
        mode = detect_installation_mode()
        if mode is None:
            mode = prompt_installation_mode()

        workspace = Workspace.for_config((Path.cwd() / "config.yaml").resolve(), log_basename) if mode == "portable" else _build_xdg_workspace(log_basename)

    if logfile_explicitly_provided:
        workspace = Workspace(
            config_file = workspace.config_file,
            config_dir = workspace.config_dir,
            log_file = Path(abspath(logfile_arg)).resolve() if logfile_arg else None,
            state_dir = workspace.state_dir,
            download_dir = workspace.download_dir,
            browser_profile_dir = workspace.browser_profile_dir,
            diagnostics_dir = workspace.diagnostics_dir,
        )

    return workspace
