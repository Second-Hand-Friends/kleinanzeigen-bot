# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
from __future__ import annotations

import runpy
import sys
from datetime import timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from kleinanzeigen_bot import cli, runtime_config
from kleinanzeigen_bot.utils import i18n, loggers
from kleinanzeigen_bot.utils.exceptions import CaptchaEncountered

pytestmark = pytest.mark.unit

if TYPE_CHECKING:
    from pathlib import Path


class TestCliParseArgs:
    @pytest.mark.parametrize(
        ("args", "expected_command", "expected_selector", "expected_keep_old"),
        [
            (["script.py", "publish", "--ads=all"], "publish", "all", False),
            (["script.py", "--force", "publish"], "publish", "all", False),
            (["script.py", "--keep-old", "publish"], "publish", "due", True),
            (["script.py", "download", "--ads=123,456"], "download", "123,456", False),
        ],
    )
    def test_parses_command_and_selector(self, args:list[str], expected_command:str, expected_selector:str, expected_keep_old:bool) -> None:
        parsed = cli.parse_args(args)

        assert parsed.command == expected_command
        assert parsed.ads_selector == expected_selector
        assert parsed.keep_old_ads is expected_keep_old

    def test_parses_config_and_logfile_paths(self, tmp_path:Path) -> None:
        config_path = tmp_path / "config.yaml"
        log_path = tmp_path / "bot.log"

        parsed = cli.parse_args(["script.py", "--config", str(config_path), "--logfile", str(log_path), "help"])

        assert parsed.config_arg == str(config_path)
        assert parsed.config_file_path == str(config_path.resolve())
        assert parsed.logfile_explicitly_provided is True
        assert parsed.logfile_arg == str(log_path)
        assert parsed.log_file_path == str(log_path.resolve())
        assert parsed.command == "help"

    def test_verbose_flag_enables_debug_logging(self, monkeypatch:pytest.MonkeyPatch) -> None:
        package_logger = loggers.get_logger("kleinanzeigen_bot")
        runtime_logger = loggers.get_logger(runtime_config.__name__)
        monkeypatch.setattr(package_logger, "level", loggers.INFO)
        monkeypatch.setattr(runtime_logger, "level", loggers.INFO)

        cli.parse_args(["script.py", "-v", "help"])

        assert loggers.is_debug(package_logger)
        assert loggers.is_debug(runtime_logger)

    def test_help_prints_and_exits(self, capsys:pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as exc_info:
            cli.parse_args(["script.py", "--help"])

        assert exc_info.value.code == 0
        stdout = capsys.readouterr().out
        assert "Usage:" in stdout
        assert "Commands:" in stdout

    def test_help_respects_language_flag_after_help(self, capsys:pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as exc_info:
            cli.parse_args(["script.py", "--help", "--lang=de"])

        assert exc_info.value.code == 0
        stdout = capsys.readouterr().out
        assert "Verwendung:" in stdout

    def test_invalid_workspace_mode_exits(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            cli.parse_args(["script.py", "--workspace-mode=invalid", "help"])

        assert exc_info.value.code == 2

    def test_lang_option_updates_locale(self) -> None:
        cli.parse_args(["script.py", "--lang=en", "help"])

        assert i18n.get_current_locale().language == "en"

    def test_defaults_to_help_without_command(self) -> None:
        parsed = cli.parse_args(["script.py"])

        assert parsed.command == "help"

    def test_invalid_option_exits(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            cli.parse_args(["script.py", "--bogus"])

        assert exc_info.value.code == 2

    def test_more_than_one_command_exits(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            cli.parse_args(["script.py", "verify", "publish"])

        assert exc_info.value.code == 2


class TestCliHelpText:
    def test_show_help_uses_german_text(self, capsys:pytest.CaptureFixture[str], monkeypatch:pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cli, "get_current_locale", lambda: SimpleNamespace(language = "de"))

        cli.show_help()

        stdout = capsys.readouterr().out
        assert "Verwendung:" in stdout
        assert "Befehle:" in stdout

    def test_help_executable_uses_frozen_executable(self, monkeypatch:pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cli, "is_frozen", lambda: True)
        monkeypatch.setattr(sys, "argv", ["/usr/local/bin/kleinanzeigen-bot"])

        assert cli._help_executable() == "/usr/local/bin/kleinanzeigen-bot"  # noqa: SLF001

    def test_help_executable_uses_pdm_when_project_root_is_set(self, monkeypatch:pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cli, "is_frozen", lambda: False)
        monkeypatch.setenv("PDM_PROJECT_ROOT", "/project")

        assert cli._help_executable() == "pdm run app"  # noqa: SLF001

    def test_help_executable_uses_module_invocation_by_default(self, monkeypatch:pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cli, "is_frozen", lambda: False)
        monkeypatch.delenv("PDM_PROJECT_ROOT", raising = False)

        assert cli._help_executable() == "python -m kleinanzeigen_bot"  # noqa: SLF001


class TestCliMain:
    @staticmethod
    def _fake_bot() -> SimpleNamespace:
        return SimpleNamespace(close_browser_session = lambda: None, run = lambda args: object())

    def test_main_forwards_unhandled_exceptions_to_error_handler(self, monkeypatch:pytest.MonkeyPatch) -> None:
        handled:dict[str, object] = {}

        class FailingLoop:
            def run_until_complete(self, _coro:object) -> None:
                raise RuntimeError("boom")

        def handle_exception(exc_type:object, exc:object, _traceback:object) -> None:
            handled.update(exc_type = exc_type, exc = exc)

        monkeypatch.setattr("kleinanzeigen_bot.cli.atexit.register", lambda *args, **kwargs: None)
        monkeypatch.setattr("kleinanzeigen_bot.cli.nodriver.loop", FailingLoop)
        monkeypatch.setattr(cli, "KleinanzeigenBot", self._fake_bot)
        monkeypatch.setattr("kleinanzeigen_bot.cli._error_handlers.on_exception", handle_exception)

        with pytest.raises(SystemExit) as exc_info:
            cli.main(["script.py", "version"])

        assert handled["exc_type"] is RuntimeError
        assert str(handled["exc"]) == "boom"
        assert exc_info.value.code == 1

    def test_main_reraises_captcha(self, monkeypatch:pytest.MonkeyPatch) -> None:
        captcha = CaptchaEncountered(timedelta(seconds = 1))

        class CaptchaLoop:
            def run_until_complete(self, _coro:object) -> None:
                raise captcha

        monkeypatch.setattr("kleinanzeigen_bot.cli.atexit.register", lambda *args, **kwargs: None)
        monkeypatch.setattr("kleinanzeigen_bot.cli.nodriver.loop", CaptchaLoop)
        monkeypatch.setattr(cli, "KleinanzeigenBot", self._fake_bot)

        with pytest.raises(CaptchaEncountered) as exc_info:
            cli.main(["script.py", "version"])

        assert exc_info.value is captcha

    def test_module_entrypoint_calls_cli_main_and_exits(self, monkeypatch:pytest.MonkeyPatch) -> None:
        calls:list[list[str]] = []
        monkeypatch.setattr(sys, "argv", ["kleinanzeigen-bot", "version"])
        monkeypatch.setattr(cli, "main", lambda args: calls.append(list(args)))

        with pytest.raises(SystemExit) as exc_info:
            runpy.run_module("kleinanzeigen_bot.__main__", run_name = "__main__")

        assert exc_info.value.code == 0
        assert calls == [["kleinanzeigen-bot", "version"]]
