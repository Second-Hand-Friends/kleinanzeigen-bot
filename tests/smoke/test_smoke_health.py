# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""
Minimal smoke tests: post-deployment health checks for kleinanzeigen-bot.
These tests verify that the most essential components are operational.
"""

import logging
import subprocess  # noqa: S404
import sys
from pathlib import Path

import pytest

from kleinanzeigen_bot.model.config_model import Config
from kleinanzeigen_bot.utils import i18n
from tests.conftest import DummyBrowser, DummyPage, SmokeKleinanzeigenBot


def run_cli_subcommand(args:list[str], cwd:str | None = None) -> subprocess.CompletedProcess[str]:
    """
    Run the kleinanzeigen-bot CLI as a subprocess with the given arguments.
    Returns the CompletedProcess object.
    """
    cli_module = "kleinanzeigen_bot.__main__"
    cmd = [sys.executable, "-m", cli_module] + args
    return subprocess.run(cmd, check = False, capture_output = True, text = True, cwd = cwd)  # noqa: S603


@pytest.mark.smoke
def test_app_starts(smoke_bot:SmokeKleinanzeigenBot) -> None:
    """Smoke: Bot can be instantiated and started without error."""
    assert smoke_bot is not None
    # Optionally call a minimal method if available
    assert hasattr(smoke_bot, "run") or hasattr(smoke_bot, "login")


@pytest.mark.smoke
def test_config_loads() -> None:
    """Smoke: Minimal config loads successfully."""
    minimal_cfg = {
        "ad_defaults": {"contact": {"name": "dummy", "zipcode": "12345"}},
        "login": {"username": "dummy", "password": "dummy"},
        "publishing": {"delete_old_ads": "BEFORE_PUBLISH", "delete_old_ads_by_title": False},
    }
    config = Config.model_validate(minimal_cfg)
    assert config.login.username == "dummy"
    assert config.login.password == "dummy"  # noqa: S105


@pytest.mark.smoke
def test_logger_initializes(tmp_path:Path, caplog:pytest.LogCaptureFixture) -> None:
    """Smoke: Logger can be initialized and used, robust to pytest log capture."""
    log_path = tmp_path / "smoke_test.log"
    logger_name = "smoke_test_logger_unique"
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    # Remove all handlers to start clean
    for h in list(logger.handlers):
        logger.removeHandler(h)
    # Create and attach a file handler
    handle = logging.FileHandler(str(log_path), encoding = "utf-8")
    handle.setLevel(logging.DEBUG)
    formatter = logging.Formatter("%(levelname)s:%(name)s:%(message)s")
    handle.setFormatter(formatter)
    logger.addHandler(handle)
    # Log a message
    logger.info("Smoke test log message")
    # Flush and close the handler
    handle.flush()
    handle.close()
    # Remove the handler from the logger
    logger.removeHandler(handle)
    assert log_path.exists()
    with open(log_path, "r", encoding = "utf-8") as f:
        contents = f.read()
    assert "Smoke test log message" in contents


@pytest.mark.smoke
def test_translation_system_healthy() -> None:
    """Smoke: Translation system loads and retrieves a known key."""
    # Use a known string that should exist in translations (fallback to identity)
    en = i18n.translate("Login", None)
    assert isinstance(en, str)
    assert len(en) > 0
    # Switch to German and test
    i18n.set_current_locale(i18n.Locale("de"))
    de = i18n.translate("Login", None)
    assert isinstance(de, str)
    assert len(de) > 0
    # Reset locale
    i18n.set_current_locale(i18n.Locale("en"))


@pytest.mark.smoke
def test_dummy_browser_session() -> None:
    """Smoke: Dummy browser session can be created and closed."""
    browser = DummyBrowser()
    page = browser.page
    assert isinstance(page, DummyPage)
    browser.stop()  # Should not raise


@pytest.mark.smoke
def test_cli_entrypoint_help_runs() -> None:
    """Smoke: CLI entry point runs with --help and exits cleanly (subprocess)."""
    result = run_cli_subcommand(["--help"])
    assert result.returncode in {0, 1}, f"CLI exited with unexpected code: {result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}"
    assert "Usage" in result.stdout or "usage" in result.stdout or "help" in result.stdout.lower(), f"No help text in CLI output: {result.stdout}"


@pytest.mark.smoke
def test_cli_create_config_creates_file(tmp_path:Path) -> None:
    """Smoke: CLI 'create-config' creates a config.yaml file in the current directory."""
    result = run_cli_subcommand(["create-config"], cwd = str(tmp_path))
    config_path = tmp_path / "config.yaml"
    assert result.returncode == 0, f"CLI exited with code {result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}"
    assert config_path.exists(), "config.yaml was not created by create-config command"
