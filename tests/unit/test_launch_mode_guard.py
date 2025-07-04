# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

import builtins, importlib, sys  # isort: skip
from unittest import mock

import pytest

from kleinanzeigen_bot.utils.i18n import Locale


# --- Platform-specific test for Windows double-click guard ---
@pytest.mark.parametrize(
    ("compiled_exe", "windows_double_click_launch", "expected_error_msg_lang"),
    [
        (True, True, "en"),  # Windows Explorer double-click - English locale
        (True, True, "de"),  # Windows Explorer double-click - German locale
        (True, False, None),  # Windows Terminal launch - compiled exe
        (False, False, None),  # Windows Terminal launch - from source code
    ],
)
@pytest.mark.skipif(sys.platform != "win32", reason = "ctypes.windll only exists on Windows")
def test_guard_triggers_on_double_click_windows(
    monkeypatch:pytest.MonkeyPatch,
    capsys:pytest.CaptureFixture[str],
    compiled_exe:bool,
    windows_double_click_launch:bool | None,
    expected_error_msg_lang:str | None
) -> None:
    # Prevent blocking in tests
    monkeypatch.setattr(builtins, "input", lambda: None)

    # Simulate target platform
    monkeypatch.setattr(sys, "platform", "win32")

    # Simulate compiled executable
    monkeypatch.setattr(
        "kleinanzeigen_bot.utils.misc.is_frozen",
        lambda: compiled_exe,
    )

    # Force specific locale
    if expected_error_msg_lang:
        monkeypatch.setattr(
            "kleinanzeigen_bot.utils.i18n.get_current_locale",
            lambda: Locale(expected_error_msg_lang),
        )

    # Spy on sys.exit
    exit_mock = mock.Mock(wraps = sys.exit)
    monkeypatch.setattr(sys, "exit", exit_mock)

    # Simulate double-click launch on Windows
    if windows_double_click_launch is not None:
        pid_count = 2 if windows_double_click_launch else 3  # 2 -> Explorer, 3 -> Terminal
        k32 = mock.Mock()
        k32.GetConsoleProcessList.return_value = pid_count
        monkeypatch.setattr("ctypes.windll.kernel32", k32)

    # Reload module to pick up system monkeypatches
    guard = importlib.reload(
        importlib.import_module("kleinanzeigen_bot.utils.launch_mode_guard")
    )

    if expected_error_msg_lang:
        with pytest.raises(SystemExit) as exc:
            guard.ensure_not_launched_from_windows_explorer()
        assert exc.value.code == 1
        exit_mock.assert_called_once_with(1)

        captured = capsys.readouterr()
        if expected_error_msg_lang == "de":
            assert "Du hast das Programm scheinbar per Doppelklick gestartet." in captured.err
        else:
            assert "It looks like you launched it by double-clicking the EXE." in captured.err
        assert not captured.out  # nothing to stdout
    else:
        guard.ensure_not_launched_from_windows_explorer()
        exit_mock.assert_not_called()
        captured = capsys.readouterr()
        assert not captured.err  # nothing to stderr


# --- Platform-agnostic tests for non-Windows and non-frozen code paths ---
@pytest.mark.parametrize(
    ("platform", "compiled_exe"),
    [
        ("linux", True),
        ("linux", False),
        ("darwin", True),
        ("darwin", False),
    ],
)
def test_guard_non_windows_and_non_frozen(
    monkeypatch:pytest.MonkeyPatch,
    platform:str,
    compiled_exe:bool
) -> None:
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr("kleinanzeigen_bot.utils.misc.is_frozen", lambda: compiled_exe)
    # Reload module to pick up system monkeypatches
    guard = importlib.reload(
        importlib.import_module("kleinanzeigen_bot.utils.launch_mode_guard")
    )
    # Should not raise or print anything
    guard.ensure_not_launched_from_windows_explorer()
