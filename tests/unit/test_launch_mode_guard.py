# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

import builtins, importlib, sys  # isort: skip
from typing import NoReturn
from unittest import mock

import pytest

# Test: On Windows, the guard should trigger and exit on double-click


@pytest.mark.skipif(sys.platform != "win32", reason = "Windows-specific test")
def test_guard_triggers_on_double_click(monkeypatch:pytest.MonkeyPatch) -> None:
    # Simulate Windows
    monkeypatch.setattr(sys, "platform", "win32")
    # Patch GetConsoleProcessList to return 2 (simulate double-click)
    mock_kernel32 = mock.Mock()
    mock_kernel32.GetConsoleProcessList.return_value = 2
    monkeypatch.setattr("ctypes.windll.kernel32", mock_kernel32)
    # Patch input to avoid blocking
    monkeypatch.setattr(builtins, "input", lambda: None)
    # Patch sys.exit to capture exit
    exit_called = {}

    def fake_exit(code:int) -> NoReturn:
        exit_called["code"] = code
        raise SystemExit

    monkeypatch.setattr(sys, "exit", fake_exit)
    # Patch i18n to always return English
    monkeypatch.setattr(
        "kleinanzeigen_bot.utils.launch_mode_guard.get_current_locale",
        lambda: mock.Mock(language = "en")
    )
    launch_mode_guard = importlib.import_module("kleinanzeigen_bot.utils.launch_mode_guard")
    with pytest.raises(SystemExit):
        launch_mode_guard.ensure_not_launched_from_windows_explorer()
    assert exit_called["code"] == 1

# Test: On non-Windows, the guard should do nothing and not error


@pytest.mark.skipif(sys.platform == "win32", reason = "Non-Windows-specific test")
def test_guard_noop_on_non_windows(monkeypatch:pytest.MonkeyPatch) -> None:
    # Simulate non-Windows platform
    monkeypatch.setattr(sys, "platform", "linux")
    # Patch input and sys.exit to ensure they are not called
    monkeypatch.setattr(builtins, "input", lambda: None)
    exit_mock = mock.Mock()
    monkeypatch.setattr(sys, "exit", exit_mock)
    launch_mode_guard = importlib.import_module("kleinanzeigen_bot.utils.launch_mode_guard")
    # Should not raise or exit
    launch_mode_guard.ensure_not_launched_from_windows_explorer()
    exit_mock.assert_not_called()


@pytest.mark.skipif(sys.platform != "win32", reason = "Windows-specific behavior")
def test_guard_noop_on_terminal(monkeypatch:pytest.MonkeyPatch) -> None:
    # Arrange: Patch sys.platform
    monkeypatch.setattr(sys, "platform", "win32")
    # Patch GetConsoleProcessList to return 3 (simulate terminal launch)
    mock_kernel32 = mock.Mock()
    mock_kernel32.GetConsoleProcessList.return_value = 3
    monkeypatch.setattr("ctypes.windll.kernel32", mock_kernel32)
    # Patch input and sys.exit to ensure they are not called
    monkeypatch.setattr(builtins, "input", lambda: None)
    exit_mock = mock.Mock()
    monkeypatch.setattr(sys, "exit", exit_mock)
    # Patch i18n to always return English
    monkeypatch.setattr(
        "kleinanzeigen_bot.utils.launch_mode_guard.get_current_locale",
        lambda: mock.Mock(language = "en")
    )
    # Reload module to pick up monkeypatches
    launch_mode_guard = importlib.import_module("kleinanzeigen_bot.utils.launch_mode_guard")
    # Act & Assert: Should not exit or call input
    launch_mode_guard.ensure_not_launched_from_windows_explorer()
    exit_mock.assert_not_called()
