# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

"""Unit tests for workspace/path resolution."""

import io
from pathlib import Path
from unittest.mock import patch

import pytest

from kleinanzeigen_bot.utils import xdg_paths

pytestmark = pytest.mark.unit


class TestGetXdgBaseDir:
    def test_returns_state_dir(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        state_dir = tmp_path / "state"
        monkeypatch.setattr("platformdirs.user_state_dir", lambda app_name, *args, **kwargs: str(state_dir / app_name))

        resolved = xdg_paths.get_xdg_base_dir("state")

        assert resolved == state_dir / "kleinanzeigen-bot"

    def test_raises_for_unknown_category(self) -> None:
        with pytest.raises(ValueError, match = "Unsupported XDG category"):
            xdg_paths.get_xdg_base_dir("invalid")  # type: ignore[arg-type]

    def test_raises_when_base_dir_is_none(self, monkeypatch:pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("platformdirs.user_state_dir", lambda app_name, *args, **kwargs: None)

        with pytest.raises(RuntimeError, match = "Failed to resolve XDG base directory for category: state"):
            xdg_paths.get_xdg_base_dir("state")


class TestDetectInstallationMode:
    def test_detects_portable_mode_when_config_exists_in_cwd(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "config.yaml").touch()

        assert xdg_paths.detect_installation_mode() == "portable"

    def test_detects_xdg_mode_when_config_exists_in_xdg_location(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        xdg_config = tmp_path / "config" / "kleinanzeigen-bot"
        xdg_config.mkdir(parents = True)
        (xdg_config / "config.yaml").touch()
        monkeypatch.setattr("platformdirs.user_config_dir", lambda app_name, *args, **kwargs: str(tmp_path / "config" / app_name))

        cwd = tmp_path / "cwd"
        cwd.mkdir()
        monkeypatch.chdir(cwd)

        assert xdg_paths.detect_installation_mode() == "xdg"

    def test_returns_none_when_no_config_found(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("platformdirs.user_config_dir", lambda app_name, *args, **kwargs: str(tmp_path / "xdg" / app_name))

        assert xdg_paths.detect_installation_mode() is None


class TestPromptInstallationMode:
    @pytest.fixture(autouse = True)
    def _force_identity_translation(self, monkeypatch:pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(xdg_paths, "_", lambda message: message)

    def test_returns_portable_for_non_interactive_mode(self, monkeypatch:pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.stdin", None)
        assert xdg_paths.prompt_installation_mode() == "portable"

    def test_returns_portable_for_non_interactive_mode_not_tty(self, monkeypatch:pytest.MonkeyPatch) -> None:
        mock_stdin = io.StringIO()
        mock_stdin.isatty = lambda: False  # type: ignore[method-assign]
        monkeypatch.setattr("sys.stdin", mock_stdin)

        assert xdg_paths.prompt_installation_mode() == "portable"

    def test_returns_portable_when_user_enters_1(self, monkeypatch:pytest.MonkeyPatch) -> None:
        mock_stdin = io.StringIO()
        mock_stdin.isatty = lambda: True  # type: ignore[method-assign]
        monkeypatch.setattr("sys.stdin", mock_stdin)
        monkeypatch.setattr("builtins.input", lambda _: "1")

        assert xdg_paths.prompt_installation_mode() == "portable"

    def test_returns_xdg_when_user_enters_2(self, monkeypatch:pytest.MonkeyPatch, capsys:pytest.CaptureFixture[str]) -> None:
        mock_stdin = io.StringIO()
        mock_stdin.isatty = lambda: True  # type: ignore[method-assign]
        monkeypatch.setattr("sys.stdin", mock_stdin)
        monkeypatch.setattr("builtins.input", lambda _: "2")

        mode = xdg_paths.prompt_installation_mode()

        assert mode == "xdg"
        captured = capsys.readouterr()
        assert "Choose installation type:" in captured.out
        assert "[2] User directories" in captured.out

    def test_reprompts_on_invalid_input_then_accepts_valid(
        self,
        monkeypatch:pytest.MonkeyPatch,
        capsys:pytest.CaptureFixture[str],
    ) -> None:
        mock_stdin = io.StringIO()
        mock_stdin.isatty = lambda: True  # type: ignore[method-assign]
        monkeypatch.setattr("sys.stdin", mock_stdin)
        inputs = iter(["invalid", "2"])
        monkeypatch.setattr("builtins.input", lambda _: next(inputs))

        mode = xdg_paths.prompt_installation_mode()

        assert mode == "xdg"
        captured = capsys.readouterr()
        assert "Invalid choice. Please enter 1 or 2." in captured.out

    def test_returns_portable_on_eof_error(self, monkeypatch:pytest.MonkeyPatch) -> None:
        mock_stdin = io.StringIO()
        mock_stdin.isatty = lambda: True  # type: ignore[method-assign]
        monkeypatch.setattr("sys.stdin", mock_stdin)

        def raise_eof(_prompt:str) -> str:
            raise EOFError

        monkeypatch.setattr("builtins.input", raise_eof)

        assert xdg_paths.prompt_installation_mode() == "portable"

    def test_returns_portable_on_keyboard_interrupt(self, monkeypatch:pytest.MonkeyPatch) -> None:
        mock_stdin = io.StringIO()
        mock_stdin.isatty = lambda: True  # type: ignore[method-assign]
        monkeypatch.setattr("sys.stdin", mock_stdin)

        def raise_keyboard_interrupt(_prompt:str) -> str:
            raise KeyboardInterrupt

        monkeypatch.setattr("builtins.input", raise_keyboard_interrupt)

        assert xdg_paths.prompt_installation_mode() == "portable"


class TestWorkspace:
    def test_ensure_directory_raises_when_target_is_not_directory(self, tmp_path:Path) -> None:
        target = tmp_path / "created"

        with patch.object(Path, "is_dir", return_value = False), pytest.raises(NotADirectoryError, match = str(target)):
            xdg_paths.ensure_directory(target, "test directory")

    def test_for_config_derives_portable_layout(self, tmp_path:Path) -> None:
        config_file = tmp_path / "custom" / "config.yaml"
        ws = xdg_paths.Workspace.for_config(config_file, "mybot")

        assert ws.config_file == config_file.resolve()
        assert ws.config_dir == config_file.parent.resolve()
        assert ws.log_file == config_file.parent.resolve() / "mybot.log"
        assert ws.state_dir == config_file.parent.resolve() / ".temp"
        assert ws.download_dir == config_file.parent.resolve() / "downloaded-ads"
        assert ws.browser_profile_dir == config_file.parent.resolve() / ".temp" / "browser-profile"
        assert ws.diagnostics_dir == config_file.parent.resolve() / ".temp" / "diagnostics"

    def test_resolve_workspace_uses_config_arg(self, tmp_path:Path) -> None:
        config_path = tmp_path / "cfg" / "config.yaml"

        ws = xdg_paths.resolve_workspace(
            config_arg = str(config_path),
            logfile_arg = None,
            workspace_mode = "portable",
            logfile_explicitly_provided = False,
            log_basename = "kleinanzeigen-bot",
        )

        assert ws.config_file == config_path.resolve()
        assert ws.log_file == config_path.parent.resolve() / "kleinanzeigen-bot.log"

    def test_resolve_workspace_uses_detected_xdg_layout(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(xdg_paths, "detect_installation_mode", lambda: "xdg")
        monkeypatch.setattr(
            xdg_paths,
            "get_xdg_base_dir",
            lambda category: {
                "config": tmp_path / "xdg-config" / xdg_paths.APP_NAME,
                "state": tmp_path / "xdg-state" / xdg_paths.APP_NAME,
                "cache": tmp_path / "xdg-cache" / xdg_paths.APP_NAME,
            }[category],
        )

        ws = xdg_paths.resolve_workspace(None, None, workspace_mode = None, logfile_explicitly_provided = False, log_basename = "kleinanzeigen-bot")

        assert ws.config_file == (tmp_path / "xdg-config" / xdg_paths.APP_NAME / "config.yaml").resolve()
        assert ws.log_file == (tmp_path / "xdg-state" / xdg_paths.APP_NAME / "kleinanzeigen-bot.log").resolve()
        assert ws.state_dir == (tmp_path / "xdg-state" / xdg_paths.APP_NAME).resolve()
        assert ws.browser_profile_dir == (tmp_path / "xdg-cache" / xdg_paths.APP_NAME / "browser-profile").resolve()
        assert ws.diagnostics_dir == (tmp_path / "xdg-cache" / xdg_paths.APP_NAME / "diagnostics").resolve()

    def test_resolve_workspace_first_run_uses_prompt_choice(self, monkeypatch:pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(xdg_paths, "detect_installation_mode", lambda: None)
        monkeypatch.setattr(xdg_paths, "prompt_installation_mode", lambda: "portable")

        ws = xdg_paths.resolve_workspace(None, None, workspace_mode = None, logfile_explicitly_provided = False, log_basename = "kleinanzeigen-bot")

        assert ws.config_file == (Path.cwd() / "config.yaml").resolve()

    def test_resolve_workspace_honors_logfile_override(self, tmp_path:Path) -> None:
        config_path = tmp_path / "cfg" / "config.yaml"
        explicit_log = tmp_path / "logs" / "my.log"

        ws = xdg_paths.resolve_workspace(
            config_arg = str(config_path),
            logfile_arg = str(explicit_log),
            workspace_mode = "portable",
            logfile_explicitly_provided = True,
            log_basename = "kleinanzeigen-bot",
        )

        assert ws.log_file == explicit_log.resolve()

    def test_resolve_workspace_disables_logfile_when_empty_flag(self, tmp_path:Path) -> None:
        ws = xdg_paths.resolve_workspace(
            config_arg = str(tmp_path / "config.yaml"),
            logfile_arg = "",
            workspace_mode = "portable",
            logfile_explicitly_provided = True,
            log_basename = "kleinanzeigen-bot",
        )

        assert ws.log_file is None

    def test_resolve_workspace_fails_when_config_mode_is_ambiguous(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        config_path = tmp_path / "cfg" / "config.yaml"
        config_path.parent.mkdir(parents = True, exist_ok = True)
        config_path.touch()
        (config_path.parent / ".temp").mkdir(parents = True, exist_ok = True)

        cwd_config = tmp_path / "cwd" / "config.yaml"
        cwd_config.parent.mkdir(parents = True, exist_ok = True)
        cwd_config.touch()
        monkeypatch.chdir(cwd_config.parent)

        monkeypatch.setattr("platformdirs.user_config_dir", lambda app_name, *args, **kwargs: str(tmp_path / "xdg-config" / app_name))
        monkeypatch.setattr("platformdirs.user_state_dir", lambda app_name, *args, **kwargs: str(tmp_path / "xdg-state" / app_name))
        monkeypatch.setattr("platformdirs.user_cache_dir", lambda app_name, *args, **kwargs: str(tmp_path / "xdg-cache" / app_name))
        (tmp_path / "xdg-config" / xdg_paths.APP_NAME / "config.yaml").parent.mkdir(parents = True, exist_ok = True)
        (tmp_path / "xdg-config" / xdg_paths.APP_NAME / "config.yaml").touch()

        with pytest.raises(ValueError, match = "Detected both portable and XDG footprints") as exc_info:
            xdg_paths.resolve_workspace(
                config_arg = str(config_path),
                logfile_arg = None,
                workspace_mode = None,
                logfile_explicitly_provided = False,
                log_basename = "kleinanzeigen-bot",
            )
        assert str((config_path.parent / ".temp").resolve()) in str(exc_info.value)
        assert str((tmp_path / "xdg-config" / xdg_paths.APP_NAME / "config.yaml").resolve()) in str(exc_info.value)

    def test_resolve_workspace_detects_portable_mode_from_custom_config_footprint(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        config_path = tmp_path / "cfg" / "config.yaml"
        config_path.parent.mkdir(parents = True, exist_ok = True)
        config_path.touch()
        (config_path.parent / ".temp").mkdir(parents = True, exist_ok = True)

        monkeypatch.setattr("platformdirs.user_config_dir", lambda app_name, *args, **kwargs: str(tmp_path / "xdg-config" / app_name))
        monkeypatch.setattr("platformdirs.user_state_dir", lambda app_name, *args, **kwargs: str(tmp_path / "xdg-state" / app_name))
        monkeypatch.setattr("platformdirs.user_cache_dir", lambda app_name, *args, **kwargs: str(tmp_path / "xdg-cache" / app_name))

        ws = xdg_paths.resolve_workspace(
            config_arg = str(config_path),
            logfile_arg = None,
            workspace_mode = None,
            logfile_explicitly_provided = False,
            log_basename = "kleinanzeigen-bot",
        )

        assert ws.mode == "portable"
        assert ws.config_file == config_path.resolve()
        assert ws.state_dir == (config_path.parent / ".temp").resolve()

    def test_resolve_workspace_detects_xdg_mode_from_xdg_footprint(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        xdg_config_dir = tmp_path / "xdg-config" / xdg_paths.APP_NAME
        xdg_cache_dir = tmp_path / "xdg-cache" / xdg_paths.APP_NAME
        xdg_state_dir = tmp_path / "xdg-state" / xdg_paths.APP_NAME
        xdg_config_dir.mkdir(parents = True, exist_ok = True)
        xdg_cache_dir.mkdir(parents = True, exist_ok = True)
        xdg_state_dir.mkdir(parents = True, exist_ok = True)
        (xdg_cache_dir / "browser-profile").mkdir(parents = True, exist_ok = True)
        (xdg_config_dir / "downloaded-ads").mkdir(parents = True, exist_ok = True)

        monkeypatch.setattr("platformdirs.user_config_dir", lambda app_name, *args, **kwargs: str(tmp_path / "xdg-config" / app_name))
        monkeypatch.setattr("platformdirs.user_state_dir", lambda app_name, *args, **kwargs: str(tmp_path / "xdg-state" / app_name))
        monkeypatch.setattr("platformdirs.user_cache_dir", lambda app_name, *args, **kwargs: str(tmp_path / "xdg-cache" / app_name))

        config_path = xdg_config_dir / "config-alt.yaml"
        config_path.touch()

        ws = xdg_paths.resolve_workspace(
            config_arg = str(config_path),
            logfile_arg = None,
            workspace_mode = None,
            logfile_explicitly_provided = False,
            log_basename = "kleinanzeigen-bot",
        )

        assert ws.mode == "xdg"
        assert ws.config_file == config_path.resolve()
        assert ws.browser_profile_dir == (xdg_cache_dir / "browser-profile").resolve()

    def test_detect_mode_from_footprints_collects_portable_and_xdg_hit_paths(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        config_path = tmp_path / "config.yaml"
        config_path.touch()
        (tmp_path / "downloaded-ads").mkdir(parents = True, exist_ok = True)

        xdg_config_dir = tmp_path / "xdg-config" / xdg_paths.APP_NAME
        xdg_cache_dir = tmp_path / "xdg-cache" / xdg_paths.APP_NAME
        xdg_state_dir = tmp_path / "xdg-state" / xdg_paths.APP_NAME
        xdg_config_dir.mkdir(parents = True, exist_ok = True)
        xdg_cache_dir.mkdir(parents = True, exist_ok = True)
        xdg_state_dir.mkdir(parents = True, exist_ok = True)
        (xdg_cache_dir / "diagnostics").mkdir(parents = True, exist_ok = True)
        (xdg_state_dir / "update_check_state.json").touch()

        monkeypatch.setattr("platformdirs.user_config_dir", lambda app_name, *args, **kwargs: str(tmp_path / "xdg-config" / app_name))
        monkeypatch.setattr("platformdirs.user_state_dir", lambda app_name, *args, **kwargs: str(tmp_path / "xdg-state" / app_name))
        monkeypatch.setattr("platformdirs.user_cache_dir", lambda app_name, *args, **kwargs: str(tmp_path / "xdg-cache" / app_name))

        detected_mode, portable_hits, xdg_hits = xdg_paths._detect_mode_from_footprints_with_hits(config_path)  # noqa: SLF001

        assert detected_mode == "ambiguous"
        assert config_path.resolve() in portable_hits
        assert (tmp_path / "downloaded-ads").resolve() in portable_hits
        assert (xdg_cache_dir / "diagnostics").resolve() in xdg_hits
        assert (xdg_state_dir / "update_check_state.json").resolve() in xdg_hits

    def test_resolve_workspace_ignores_unrelated_cwd_config_when_config_is_elsewhere(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        cwd = tmp_path / "cwd"
        cwd.mkdir(parents = True, exist_ok = True)
        (cwd / "config.yaml").touch()
        monkeypatch.chdir(cwd)

        xdg_config_dir = tmp_path / "xdg-config" / xdg_paths.APP_NAME
        xdg_cache_dir = tmp_path / "xdg-cache" / xdg_paths.APP_NAME
        xdg_state_dir = tmp_path / "xdg-state" / xdg_paths.APP_NAME
        xdg_config_dir.mkdir(parents = True, exist_ok = True)
        xdg_cache_dir.mkdir(parents = True, exist_ok = True)
        xdg_state_dir.mkdir(parents = True, exist_ok = True)
        (xdg_config_dir / "config.yaml").touch()

        monkeypatch.setattr("platformdirs.user_config_dir", lambda app_name, *args, **kwargs: str(tmp_path / "xdg-config" / app_name))
        monkeypatch.setattr("platformdirs.user_state_dir", lambda app_name, *args, **kwargs: str(tmp_path / "xdg-state" / app_name))
        monkeypatch.setattr("platformdirs.user_cache_dir", lambda app_name, *args, **kwargs: str(tmp_path / "xdg-cache" / app_name))

        custom_config = tmp_path / "external" / "config.yaml"
        custom_config.parent.mkdir(parents = True, exist_ok = True)
        custom_config.touch()

        ws = xdg_paths.resolve_workspace(
            config_arg = str(custom_config),
            logfile_arg = None,
            workspace_mode = None,
            logfile_explicitly_provided = False,
            log_basename = "kleinanzeigen-bot",
        )

        assert ws.mode == "xdg"

    def test_resolve_workspace_fails_when_config_mode_is_unknown(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        config_path = tmp_path / "cfg" / "config.yaml"
        config_path.parent.mkdir(parents = True, exist_ok = True)
        config_path.touch()
        (tmp_path / "cwd").mkdir(parents = True, exist_ok = True)
        monkeypatch.chdir(tmp_path / "cwd")

        monkeypatch.setattr("platformdirs.user_config_dir", lambda app_name, *args, **kwargs: str(tmp_path / "xdg-config" / app_name))
        monkeypatch.setattr("platformdirs.user_state_dir", lambda app_name, *args, **kwargs: str(tmp_path / "xdg-state" / app_name))
        monkeypatch.setattr("platformdirs.user_cache_dir", lambda app_name, *args, **kwargs: str(tmp_path / "xdg-cache" / app_name))

        with pytest.raises(ValueError, match = "Detected neither portable nor XDG footprints") as exc_info:
            xdg_paths.resolve_workspace(
                config_arg = str(config_path),
                logfile_arg = None,
                workspace_mode = None,
                logfile_explicitly_provided = False,
                log_basename = "kleinanzeigen-bot",
            )
        assert "Portable footprint hits: none" in str(exc_info.value)
        assert "XDG footprint hits: none" in str(exc_info.value)

    def test_resolve_workspace_raises_when_config_path_is_unresolved(self, tmp_path:Path) -> None:
        config_path = (tmp_path / "config.yaml").resolve()
        original_resolve = Path.resolve

        def patched_resolve(self:Path, strict:bool = False) -> object:
            if self == config_path:
                return None
            return original_resolve(self, strict)

        with patch.object(Path, "resolve", patched_resolve), pytest.raises(
            ValueError, match = "Workspace mode and config path must be resolved"
        ):
            xdg_paths.resolve_workspace(
                config_arg = str(config_path),
                logfile_arg = None,
                workspace_mode = "portable",
                logfile_explicitly_provided = False,
                log_basename = "kleinanzeigen-bot",
            )
