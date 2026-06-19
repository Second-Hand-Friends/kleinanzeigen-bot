# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Runtime configuration and bootstrap helpers.

Provides config loading, default generation, workspace resolution,
browser config application, and file-logging setup. Central types:
:class:`RuntimeState` and :func:`load_config`.
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from gettext import gettext as _
from pathlib import Path
from typing import Any, Final

from kleinanzeigen_bot import resources as _resources
from kleinanzeigen_bot.model.config_model import Config
from kleinanzeigen_bot.utils import dicts as _dicts
from kleinanzeigen_bot.utils import loggers as _loggers
from kleinanzeigen_bot.utils import xdg_paths as _xdg_paths
from kleinanzeigen_bot.utils.files import abspath
from kleinanzeigen_bot.utils.timing_collector import TimingCollector

LOG:Final[_loggers.Logger] = _loggers.get_logger(__name__)
LOG.setLevel(_loggers.INFO)

_LOGIN_ENV_PATTERN:Final[re.Pattern[str]] = re.compile(r"^\$\{(?P<var>\w+)(?::-(?P<default>.*))?\}$")

# Commands that do not need workspace / filesystem state.
WORKSPACE_FREE_COMMANDS:Final[frozenset[str]] = frozenset({"help", "version", "create-config"})
# All valid CLI commands.  Keep in sync with the dispatch in app.py/run().
VALID_COMMANDS:Final[frozenset[str]] = frozenset({
    "help", "version", "create-config", "diagnose", "verify",
    "update-check", "update-content-hash",
    "publish", "update", "delete", "extend", "download",
})


@dataclass(slots = True)
class RuntimeState:
    config:Config
    categories:dict[str, str]
    timing_collector:TimingCollector | None


def create_default_config(config_file_path:str, workspace:_xdg_paths.Workspace | None) -> None:
    if os.path.exists(config_file_path):
        LOG.error("Config file %s already exists. Aborting creation.", config_file_path)
        return

    config_parent = workspace.config_file.parent if workspace else Path(config_file_path).parent
    _xdg_paths.ensure_directory(config_parent, "config directory")
    default_config = Config.model_construct()
    default_config.login.username = "changeme"  # noqa: S105 placeholder for default config, not a real username
    default_config.login.password = "changeme"  # noqa: S105 placeholder for default config, not a real password
    _dicts.save_commented_model(
        config_file_path,
        default_config,
        header = "# yaml-language-server: $schema=https://raw.githubusercontent.com/Second-Hand-Friends/kleinanzeigen-bot/main/schemas/config.schema.json",
        exclude = {
            "ad_defaults": {"description"},
        },
    )


def _resolve_login_credentials(config_yaml:dict[str, Any]) -> None:
    if not isinstance(config_yaml, dict):
        return

    login = config_yaml.get("login")
    if not isinstance(login, dict):
        return

    for field in ("username", "password"):
        value = login.get(field)
        if not isinstance(value, str):
            continue

        match = _LOGIN_ENV_PATTERN.match(value)
        if not match:
            continue

        var_name = match.group("var")
        resolved = os.environ.get(var_name)
        if resolved is not None:
            login[field] = resolved
        elif match.group("default") is not None:
            login[field] = match.group("default")
        else:
            raise ValueError(_("Environment variable %s is required for login.%s but is not set") % (var_name, field))


def load_config(config_file_path:str, workspace:_xdg_paths.Workspace | None, command:str) -> RuntimeState:
    """Load the runtime config and derived lookup tables.

    Args:
        config_file_path: Path to the active config file.
        workspace: Resolved workspace, if one exists for this command.
        command: Active CLI command, used for timing collection labels.

    Returns:
        RuntimeState: Parsed config, merged categories, and optional timing collector.

    Example:
        `load_config("config.yaml", workspace, "verify")` returns a RuntimeState whose
        `config`, `categories`, and `timing_collector` fields are ready for the command run.
    """
    if not os.path.exists(config_file_path):
        # Keep bootstrapping self-contained: first run must create a usable config file.
        create_default_config(config_file_path, workspace)

    config_yaml = _dicts.load_dict_if_exists(config_file_path, _("config"))
    if isinstance(config_yaml, dict):
        # Resolve ${ENV} placeholders before schema validation so the model sees final values.
        _resolve_login_credentials(config_yaml)
    # Validate strictly and keep the file path in context so model errors point at the source file.
    config = Config.model_validate(config_yaml, strict = True, context = config_file_path)

    timing_enabled = config.diagnostics.timing_collection
    if timing_enabled and workspace:
        # Diagnostics live under the workspace diagnostics tree; timing data sits next to it.
        timing_dir = workspace.diagnostics_dir.parent / "timing"
        timing_collector:TimingCollector | None = TimingCollector(timing_dir, command)
    else:
        # No workspace or disabled timing collection means we skip the collector entirely.
        timing_collector = None

    # Merge order matters: bundled defaults first, deprecated aliases second, user overrides last.
    categories:dict[str, str] = _dicts.load_dict_from_module(_resources, "categories.yaml", "")
    LOG.debug("Loaded %s categories from categories.yaml", len(categories))
    deprecated_categories = _dicts.load_dict_from_module(_resources, "categories_old.yaml", "")
    LOG.debug("Loaded %s categories from categories_old.yaml", len(deprecated_categories))
    categories.update(deprecated_categories)
    if config.categories:
        categories.update(config.categories)
        LOG.debug("Loaded %s categories from config.yaml (custom)", len(config.categories))
    if not categories:
        # This should only happen if resources are broken or empty, so surface it loudly.
        LOG.warning("No categories loaded - category files may be missing or empty")
    LOG.debug("Loaded %s categories in total", len(categories))

    return RuntimeState(config = config, categories = categories, timing_collector = timing_collector)


def apply_browser_config(browser_config:Any, config:Config, workspace:_xdg_paths.Workspace | None, config_file_path:str) -> None:
    browser_config.arguments = config.browser.arguments
    browser_config.binary_location = config.browser.binary_location
    browser_config.extensions = [abspath(item, relative_to = config_file_path) for item in config.browser.extensions]
    browser_config.use_private_window = config.browser.use_private_window
    if config.browser.user_data_dir:
        browser_config.user_data_dir = abspath(config.browser.user_data_dir, relative_to = config_file_path)
    elif workspace:
        browser_config.user_data_dir = str(workspace.browser_profile_dir)
    browser_config.profile_name = config.browser.profile_name


def configure_file_logging(
    log_file_path:str | None,
    workspace:_xdg_paths.Workspace | None,
    file_log:_loggers.LogFileHandle | None,
    version:str,
) -> _loggers.LogFileHandle | None:
    if not log_file_path or file_log:
        return file_log

    if workspace and workspace.log_file:
        _xdg_paths.ensure_directory(workspace.log_file.parent, "log directory")

    LOG.info("Logging to [%s]...", log_file_path)
    file_log = _loggers.configure_file_logging(log_file_path)
    LOG.info("App version: %s", version)
    LOG.info("Python version: %s", sys.version)
    return file_log


def resolve_workspace(
    *,
    command:str,
    config_file_path:str,
    config_arg:str | None,
    logfile_arg:str | None,
    workspace_mode:_xdg_paths.InstallationMode | None,
    logfile_explicitly_provided:bool,
    log_basename:str,
) -> _xdg_paths.Workspace | None:
    """Resolve the workspace for a command that needs filesystem state.

    Typical input: `command="verify"`, `config_file_path="config.yaml"`.
    Typical output: a workspace rooted under the configured mode, or `None` for help/version.
    """
    if command in WORKSPACE_FREE_COMMANDS:
        return None

    effective_config_arg = config_arg
    effective_workspace_mode = workspace_mode
    if not effective_config_arg:
        # Programmatic callers sometimes set config_file_path directly, so infer the config arg from it.
        default_config = (Path.cwd() / "config.yaml").resolve()
        if Path(config_file_path).resolve() != default_config:
            effective_config_arg = config_file_path
            if effective_workspace_mode is None:
                # Preserve the old default: infer portable vs xdg from the config location.
                config_path = Path(config_file_path).resolve()
                xdg_config_dir = _xdg_paths.get_xdg_base_dir("config").resolve()
                effective_workspace_mode = "xdg" if config_path.is_relative_to(xdg_config_dir) else "portable"

    try:
        workspace = _xdg_paths.resolve_workspace(
            config_arg = effective_config_arg,
            logfile_arg = logfile_arg,
            workspace_mode = effective_workspace_mode,
            logfile_explicitly_provided = logfile_explicitly_provided,
            log_basename = log_basename,
        )
    except ValueError as exc:
        LOG.error(str(exc))
        sys.exit(2)

    _xdg_paths.ensure_directory(workspace.config_file.parent, "config directory")

    LOG.info("Config:    %s", workspace.config_file)
    LOG.info("Workspace mode: %s", workspace.mode)
    LOG.info("Workspace: %s", workspace.config_dir)
    if _loggers.is_debug(LOG):
        LOG.debug("Log file:        %s", workspace.log_file)
        LOG.debug("State dir:       %s", workspace.state_dir)
        LOG.debug("Download dir:    %s", workspace.download_dir)
        LOG.debug("Browser profile: %s", workspace.browser_profile_dir)
        LOG.debug("Diagnostics dir: %s", workspace.diagnostics_dir)

    return workspace
