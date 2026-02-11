# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

"""XDG Base Directory path resolution with workspace abstraction."""

from __future__ import annotations

import sys
from dataclasses import dataclass, replace
from gettext import gettext as _
from pathlib import Path
from typing import Final, Literal

import platformdirs

from kleinanzeigen_bot.utils import loggers
from kleinanzeigen_bot.utils.files import abspath

LOG:Final[loggers.Logger] = loggers.get_logger(__name__)

APP_NAME:Final[str] = "kleinanzeigen-bot"
InstallationMode = Literal["portable", "xdg"]
PathCategory = Literal["config", "cache", "state"]


@dataclass(frozen = True)
class Workspace:
    """Resolved workspace paths for all bot side effects."""

    mode:InstallationMode
    config_file:Path
    config_dir:Path  # root directory for mode-dependent artifacts
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
            mode = "portable",
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


def _build_xdg_workspace(log_basename:str, config_file_override:Path | None = None) -> Workspace:
    """Build an XDG-style workspace using standard user directories."""
    config_dir = get_xdg_base_dir("config").resolve()
    state_dir = get_xdg_base_dir("state").resolve()
    config_file = config_file_override.resolve() if config_file_override is not None else config_dir / "config.yaml"
    return Workspace(
        mode = "xdg",
        config_file = config_file,
        config_dir = config_dir,
        log_file = state_dir / f"{log_basename}.log",
        state_dir = state_dir,
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


def _detect_mode_from_footprints_with_hits(
    config_file:Path,
) -> tuple[Literal["portable", "xdg", "ambiguous", "unknown"], list[Path], list[Path]]:
    """
    Detect workspace mode and return concrete footprint hits for diagnostics.
    """
    config_file = config_file.resolve()
    cwd_config = (Path.cwd() / "config.yaml").resolve()
    xdg_config_dir = get_xdg_base_dir("config").resolve()
    xdg_cache_dir = get_xdg_base_dir("cache").resolve()
    xdg_state_dir = get_xdg_base_dir("state").resolve()
    config_in_xdg_tree = config_file.is_relative_to(xdg_config_dir)

    portable_hits:list[Path] = []
    xdg_hits:list[Path] = []

    if config_file == cwd_config:
        portable_hits.append(cwd_config)
    if not config_in_xdg_tree:
        if (config_file.parent / ".temp").exists():
            portable_hits.append((config_file.parent / ".temp").resolve())
        if (config_file.parent / "downloaded-ads").exists():
            portable_hits.append((config_file.parent / "downloaded-ads").resolve())

    if config_in_xdg_tree:
        xdg_hits.append(config_file)
    if not config_in_xdg_tree and (xdg_config_dir / "config.yaml").exists():
        xdg_hits.append((xdg_config_dir / "config.yaml").resolve())
    if (xdg_config_dir / "downloaded-ads").exists():
        xdg_hits.append((xdg_config_dir / "downloaded-ads").resolve())
    if (xdg_cache_dir / "browser-profile").exists():
        xdg_hits.append((xdg_cache_dir / "browser-profile").resolve())
    if (xdg_cache_dir / "diagnostics").exists():
        xdg_hits.append((xdg_cache_dir / "diagnostics").resolve())
    if (xdg_state_dir / "update_check_state.json").exists():
        xdg_hits.append((xdg_state_dir / "update_check_state.json").resolve())

    portable_detected = len(portable_hits) > 0
    xdg_detected = len(xdg_hits) > 0

    if portable_detected and xdg_detected:
        return "ambiguous", portable_hits, xdg_hits
    if portable_detected:
        return "portable", portable_hits, xdg_hits
    if xdg_detected:
        return "xdg", portable_hits, xdg_hits
    return "unknown", portable_hits, xdg_hits


def _workspace_mode_resolution_error(
    config_file:Path,
    detected_mode:Literal["ambiguous", "unknown"],
    portable_hits:list[Path],
    xdg_hits:list[Path],
) -> ValueError:
    def _format_hits(label:str, hits:list[Path]) -> str:
        if not hits:
            return f"{label}: {_('none')}"
        deduped = list(dict.fromkeys(hits))
        return f"{label}:\n- " + "\n- ".join(str(hit) for hit in deduped)

    guidance = _(
        "Cannot determine workspace mode for --config=%(config_file)s. "
        "Use --workspace-mode=portable or --workspace-mode=xdg.\n"
        "For cleanup guidance, see: %(url)s"
    ) % {
        "config_file": config_file,
        "url": "https://github.com/Second-Hand-Friends/kleinanzeigen-bot/blob/main/docs/CONFIGURATION.md#installation-modes",
    }
    details = f"{_format_hits(_('Portable footprint hits'), portable_hits)}\n{_format_hits(_('XDG footprint hits'), xdg_hits)}"
    if detected_mode == "ambiguous":
        return ValueError(f"{guidance}\n{_('Detected both portable and XDG footprints.')}\n{details}")
    return ValueError(f"{guidance}\n{_('Detected neither portable nor XDG footprints.')}\n{details}")


def resolve_workspace(
    config_arg:str | None,
    logfile_arg:str | None,
    *,
    workspace_mode:InstallationMode | None,
    logfile_explicitly_provided:bool,
    log_basename:str,
) -> Workspace:
    """Resolve workspace paths from CLI flags and auto-detected installation mode."""
    config_path = Path(abspath(config_arg)).resolve() if config_arg else None
    mode = workspace_mode

    if config_path and mode is None:
        detected_mode, portable_hits, xdg_hits = _detect_mode_from_footprints_with_hits(config_path)
        if detected_mode == "portable":
            mode = "portable"
        elif detected_mode == "xdg":
            mode = "xdg"
        else:
            raise _workspace_mode_resolution_error(
                config_path,
                detected_mode,
                portable_hits,
                xdg_hits,
            )

    if config_arg:
        if config_path is None or mode is None:
            raise RuntimeError("Workspace mode and config path must be resolved when --config is supplied")
        if mode == "portable":
            workspace = Workspace.for_config(config_path, log_basename)
        else:
            workspace = _build_xdg_workspace(log_basename, config_file_override = config_path)
    else:
        mode = mode or detect_installation_mode()
        if mode is None:
            mode = prompt_installation_mode()

        workspace = Workspace.for_config((Path.cwd() / "config.yaml").resolve(), log_basename) if mode == "portable" else _build_xdg_workspace(log_basename)

    if logfile_explicitly_provided:
        workspace = replace(workspace, log_file = Path(abspath(logfile_arg)).resolve() if logfile_arg else None)

    return workspace
