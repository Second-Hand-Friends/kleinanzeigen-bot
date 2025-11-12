# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""
Minimal smoke tests: post-deployment health checks for kleinanzeigen-bot.
These tests verify that the most essential components are operational.
"""

import contextlib
import io
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from unittest.mock import patch

import pytest
from ruyaml import YAML

import kleinanzeigen_bot
from kleinanzeigen_bot.model.config_model import Config
from kleinanzeigen_bot.utils.i18n import get_current_locale, set_current_locale
from tests.conftest import SmokeKleinanzeigenBot

pytestmark = pytest.mark.slow


@dataclass(slots = True)
class CLIResult:
    returncode:int
    stdout:str
    stderr:str


def invoke_cli(args:list[str], cwd:Path | None = None) -> CLIResult:
    """
    Run the kleinanzeigen-bot CLI in-process and capture stdout/stderr.
    """
    stdout = io.StringIO()
    stderr = io.StringIO()
    previous_cwd:Path | None = None
    previous_locale = get_current_locale()

    def capture_register(func:Callable[..., object], *_cb_args:Any, **_cb_kwargs:Any) -> Callable[..., object]:
        return func

    log_capture = io.StringIO()
    log_handler = logging.StreamHandler(log_capture)
    log_handler.setLevel(logging.DEBUG)

    def build_result(exit_code:object) -> CLIResult:
        if exit_code is None:
            normalized = 0
        elif isinstance(exit_code, int):
            normalized = exit_code
        else:
            normalized = 1
        combined_stderr = stderr.getvalue() + log_capture.getvalue()
        return CLIResult(normalized, stdout.getvalue(), combined_stderr)

    try:
        if cwd is not None:
            previous_cwd = Path.cwd()
            os.chdir(os.fspath(cwd))
        logging.getLogger().addHandler(log_handler)
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch("kleinanzeigen_bot.atexit.register", capture_register))
            stack.enter_context(contextlib.redirect_stdout(stdout))
            stack.enter_context(contextlib.redirect_stderr(stderr))
            try:
                kleinanzeigen_bot.main(["kleinanzeigen-bot", *args])
            except SystemExit as exc:
                return build_result(exc.code)
            return build_result(0)
    finally:
        logging.getLogger().removeHandler(log_handler)
        log_handler.close()
        if previous_cwd is not None:
            os.chdir(previous_cwd)
        set_current_locale(previous_locale)


@pytest.fixture(autouse = True)
def disable_update_checker(monkeypatch:pytest.MonkeyPatch) -> None:
    """Prevent smoke tests from hitting GitHub for update checks."""

    def _no_update(*_args:object, **_kwargs:object) -> None:
        return None

    monkeypatch.setattr("kleinanzeigen_bot.update_checker.UpdateChecker.check_for_updates", _no_update)


@pytest.mark.smoke
def test_app_starts(smoke_bot:SmokeKleinanzeigenBot) -> None:
    """Smoke: Bot can be instantiated and started without error."""
    assert smoke_bot is not None
    # Optionally call a minimal method if available
    assert hasattr(smoke_bot, "run") or hasattr(smoke_bot, "login")


@pytest.mark.smoke
@pytest.mark.parametrize("subcommand", [
    "--help",
    "help",
    "version",
    "diagnose",
])
def test_cli_subcommands_no_config(subcommand:str, tmp_path:Path) -> None:
    """
    Smoke: CLI subcommands that do not require a config file (--help, help, version, diagnose).
    """
    args = [subcommand]
    result = invoke_cli(args, cwd = tmp_path)
    assert result.returncode == 0
    out = (result.stdout + "\n" + result.stderr).lower()
    if subcommand in {"--help", "help"}:
        assert "usage" in out or "help" in out, f"Expected help text in CLI output.\n{out}"
    elif subcommand == "version":
        assert re.match(r"^\s*\d{4}\+\w+", result.stdout.strip()), f"Output does not look like a version string: {result.stdout}"
    elif subcommand == "diagnose":
        assert "browser connection diagnostics" in out or "browser-verbindungsdiagnose" in out, f"Expected diagnostic output.\n{out}"


@pytest.mark.smoke
def test_cli_subcommands_create_config_creates_file(tmp_path:Path) -> None:
    """
    Smoke: CLI 'create-config' creates a config.yaml file in the current directory.
    """
    result = invoke_cli(["create-config"], cwd = tmp_path)
    config_file = tmp_path / "config.yaml"
    assert result.returncode == 0
    assert config_file.exists(), "config.yaml was not created by create-config command"
    out = (result.stdout + "\n" + result.stderr).lower()
    assert "saving" in out, f"Expected saving message in CLI output.\n{out}"
    assert "config.yaml" in out, f"Expected config.yaml in CLI output.\n{out}"


@pytest.mark.smoke
def test_cli_subcommands_create_config_fails_if_exists(tmp_path:Path) -> None:
    """
    Smoke: CLI 'create-config' does not overwrite config.yaml if it already exists.
    """
    config_file = tmp_path / "config.yaml"
    config_file.write_text("# dummy config\n", encoding = "utf-8")
    result = invoke_cli(["create-config"], cwd = tmp_path)
    assert result.returncode == 0
    assert config_file.exists(), "config.yaml was deleted or not present after second create-config run"
    out = (result.stdout + "\n" + result.stderr).lower()
    assert (
        "already exists" in out or "not overwritten" in out or "saving" in out
    ), f"Expected message about existing config in CLI output.\n{out}"


@pytest.mark.smoke
@pytest.mark.parametrize(("subcommand", "output_check"), [
    ("verify", "verify"),
    ("update-check", "update"),
    ("update-content-hash", "update-content-hash"),
    ("diagnose", "diagnose"),
])
@pytest.mark.parametrize(("config_ext", "serializer"), [
    ("yaml", None),
    ("yml", None),
    ("json", json.dumps),
])
def test_cli_subcommands_with_config_formats(
    subcommand:str,
    output_check:str,
    config_ext:str,
    serializer:Callable[[dict[str, object]], str] | None,
    tmp_path:Path,
    test_bot_config:Config,
) -> None:
    """
    Smoke: CLI subcommands that require a config file, tested with all supported formats.
    """
    config_path = tmp_path / f"config.{config_ext}"
    try:
        config_dict = test_bot_config.model_dump()
    except AttributeError:
        config_dict = test_bot_config.dict()
    if config_ext in {"yaml", "yml"}:
        yaml = YAML(typ = "unsafe", pure = True)
        with open(config_path, "w", encoding = "utf-8") as f:
            yaml.dump(config_dict, f)
    elif serializer is not None:
        config_path.write_text(serializer(config_dict), encoding = "utf-8")
    args = [subcommand, "--config", str(config_path)]
    result = invoke_cli(args, cwd = tmp_path)
    assert result.returncode == 0
    out = (result.stdout + "\n" + result.stderr).lower()
    if subcommand == "verify":
        assert "no configuration errors found" in out, f"Expected 'no configuration errors found' in output for 'verify'.\n{out}"
    elif subcommand == "update-content-hash":
        assert "no active ads found" in out, f"Expected 'no active ads found' in output for 'update-content-hash'.\n{out}"
    elif subcommand == "update-check":
        assert result.returncode == 0
    elif subcommand == "diagnose":
        assert "browser connection diagnostics" in out or "browser-verbindungsdiagnose" in out, f"Expected diagnostic output for 'diagnose'.\n{out}"
