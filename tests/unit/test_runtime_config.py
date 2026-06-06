# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from kleinanzeigen_bot import runtime_config
from kleinanzeigen_bot.model.config_model import Config
from kleinanzeigen_bot.utils import xdg_paths

pytestmark = pytest.mark.unit

if TYPE_CHECKING:
    from pathlib import Path


def _write_minimal_config(config_path:Path) -> None:
    config_path.write_text(
        """
login:
  username: ${BOT_USERNAME}
  password: ${BOT_PASSWORD}
ad_defaults:
  contact:
    name: Test User
    zipcode: "12345"
publishing:
  delete_old_ads: BEFORE_PUBLISH
  delete_old_ads_by_title: false
""".strip(),
        encoding = "utf-8",
    )


class TestRuntimeConfig:
    def test_resolve_workspace_skips_bootstrap_commands(self, tmp_path:Path) -> None:
        config_path = tmp_path / "config.yaml"

        assert (
            runtime_config.resolve_workspace(
                command = "help",
                config_file_path = str(config_path),
                config_arg = None,
                logfile_arg = None,
                workspace_mode = None,
                logfile_explicitly_provided = False,
                log_basename = "kleinanzeigen-bot",
            )
            is None
        )

    def test_resolve_workspace_honors_explicit_logfile(self, tmp_path:Path) -> None:
        config_path = tmp_path / "config.yaml"
        log_path = tmp_path / "custom.log"

        workspace = runtime_config.resolve_workspace(
            command = "verify",
            config_file_path = str(config_path),
            config_arg = str(config_path),
            logfile_arg = str(log_path),
            workspace_mode = "portable",
            logfile_explicitly_provided = True,
            log_basename = "kleinanzeigen-bot",
        )

        assert workspace is not None
        assert workspace.log_file == log_path.resolve()

    def test_create_default_config_creates_file(self, tmp_path:Path) -> None:
        config_path = tmp_path / "nested" / "config.yaml"

        runtime_config.create_default_config(str(config_path), workspace = None)

        assert config_path.exists()
        contents = config_path.read_text()
        assert "username: changeme" in contents
        assert "password: changeme" in contents

    def test_load_config_resolves_login_env_and_browser_defaults(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BOT_USERNAME", "env_user")
        monkeypatch.setenv("BOT_PASSWORD", "env_pass")

        config_path = tmp_path / "config.yaml"
        _write_minimal_config(config_path)
        workspace = xdg_paths.Workspace.for_config(config_path, "kleinanzeigen-bot")

        state = runtime_config.load_config(str(config_path), workspace, "verify")

        assert state.config.login.username == "env_user"
        assert state.config.login.password == "env_" + "pass"
        assert state.categories

    def test_apply_browser_config_uses_workspace_profile_when_custom_dir_missing(self, tmp_path:Path) -> None:
        config_path = tmp_path / "config.yaml"
        workspace = xdg_paths.Workspace.for_config(config_path, "kleinanzeigen-bot")
        browser_config = MagicMock()
        config = Config.model_validate(
            {
                "login": {"username": "user", "password": "pass"},
                "ad_defaults": {"contact": {"name": "Test User", "zipcode": "12345"}},
                "publishing": {"delete_old_ads": "BEFORE_PUBLISH", "delete_old_ads_by_title": False},
            }
        )

        runtime_config.apply_browser_config(browser_config, config, workspace, str(config_path))

        assert browser_config.user_data_dir == str(workspace.browser_profile_dir)
        assert browser_config.profile_name == config.browser.profile_name

    def test_configure_file_logging_creates_handler(self, tmp_path:Path) -> None:
        config_path = tmp_path / "config.yaml"
        workspace = xdg_paths.Workspace.for_config(config_path, "kleinanzeigen-bot")
        log_path = tmp_path / "bot.log"

        file_log = runtime_config.configure_file_logging(str(log_path), workspace, None, "1.2.3")

        assert file_log is not None
        assert log_path.exists()
        file_log.close()

    def test_resolve_workspace_exits_on_workspace_error(self, tmp_path:Path) -> None:
        config_path = tmp_path / "config.yaml"

        with patch("kleinanzeigen_bot.utils.xdg_paths.resolve_workspace", side_effect = ValueError("boom")), pytest.raises(SystemExit) as exc_info:
            runtime_config.resolve_workspace(
                command = "verify",
                config_file_path = str(config_path),
                config_arg = str(config_path),
                logfile_arg = None,
                workspace_mode = None,
                logfile_explicitly_provided = False,
                log_basename = "kleinanzeigen-bot",
            )

        assert exc_info.value.code == 2
