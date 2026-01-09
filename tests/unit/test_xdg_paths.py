# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

"""Unit tests for XDG paths module."""

import io
from pathlib import Path

import pytest

from kleinanzeigen_bot.utils import xdg_paths

pytestmark = pytest.mark.unit


class TestGetXdgBaseDir:
    """Tests for get_xdg_base_dir function."""

    def test_returns_state_dir(self, tmp_path:Path, monkeypatch:pytest.MonkeyPatch) -> None:
        """Test resolving XDG state directory."""
        state_dir = tmp_path / "state"
        monkeypatch.setattr("platformdirs.user_state_dir", lambda app_name, *args, **kwargs: str(state_dir / app_name))

        resolved = xdg_paths.get_xdg_base_dir("state")

        assert resolved == state_dir / "kleinanzeigen-bot"

    def test_raises_for_unknown_category(self) -> None:
        """Test invalid category handling."""
        with pytest.raises(ValueError, match = "Unsupported XDG category"):
            xdg_paths.get_xdg_base_dir("invalid")  # type: ignore[arg-type]

    def test_raises_when_base_dir_is_none(self, monkeypatch:pytest.MonkeyPatch) -> None:
        """Test runtime error when platformdirs returns None."""
        monkeypatch.setattr("platformdirs.user_state_dir", lambda _app_name, *args, **kwargs: None)

        with pytest.raises(RuntimeError, match = "Failed to resolve XDG base directory"):
            xdg_paths.get_xdg_base_dir("state")


class TestDetectInstallationMode:
    """Tests for detect_installation_mode function."""

    def test_detects_portable_mode_when_config_exists_in_cwd(
        self,
        tmp_path:Path,
        monkeypatch:pytest.MonkeyPatch
    ) -> None:
        """Test that portable mode is detected when config.yaml exists in CWD."""
        # Setup: Create config.yaml in CWD
        monkeypatch.chdir(tmp_path)
        (tmp_path / "config.yaml").touch()

        # Execute
        mode = xdg_paths.detect_installation_mode()

        # Verify
        assert mode == "portable"

    def test_detects_xdg_mode_when_config_exists_in_xdg_location(
        self,
        tmp_path:Path,
        monkeypatch:pytest.MonkeyPatch
    ) -> None:
        """Test that XDG mode is detected when config exists in XDG location."""
        # Setup: Create config in mock XDG directory
        xdg_config = tmp_path / "config" / "kleinanzeigen-bot"
        xdg_config.mkdir(parents = True)
        (xdg_config / "config.yaml").touch()

        # Mock platformdirs to return our test directory
        monkeypatch.setattr("platformdirs.user_config_dir", lambda app_name, *args, **kwargs: str(tmp_path / "config" / app_name))

        # Change to a different directory (no local config)
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        monkeypatch.chdir(cwd)

        # Execute
        mode = xdg_paths.detect_installation_mode()

        # Verify
        assert mode == "xdg"

    def test_returns_none_when_no_config_found(
        self,
        tmp_path:Path,
        monkeypatch:pytest.MonkeyPatch
    ) -> None:
        """Test that None is returned when no config exists anywhere."""
        # Setup: Empty directories
        monkeypatch.chdir(tmp_path)

        # Execute
        mode = xdg_paths.detect_installation_mode()

        # Verify
        assert mode is None


class TestGetConfigFilePath:
    """Tests for get_config_file_path function."""

    def test_returns_cwd_path_in_portable_mode(
        self,
        tmp_path:Path,
        monkeypatch:pytest.MonkeyPatch
    ) -> None:
        """Test that portable mode returns ./config.yaml."""
        monkeypatch.chdir(tmp_path)

        path = xdg_paths.get_config_file_path("portable")

        assert path == tmp_path / "config.yaml"

    def test_returns_xdg_path_in_xdg_mode(
        self,
        tmp_path:Path,
        monkeypatch:pytest.MonkeyPatch
    ) -> None:
        """Test that XDG mode returns XDG config path."""
        xdg_config = tmp_path / "config"
        monkeypatch.setattr(
            "platformdirs.user_config_dir",
            lambda app_name, *args, **kwargs: str(xdg_config / app_name),
        )

        path = xdg_paths.get_config_file_path("xdg")

        assert "kleinanzeigen-bot" in str(path)
        assert path.name == "config.yaml"


class TestGetAdFilesSearchDir:
    """Tests for get_ad_files_search_dir function."""

    def test_returns_cwd_in_portable_mode(
        self,
        tmp_path:Path,
        monkeypatch:pytest.MonkeyPatch
    ) -> None:
        """Test that portable mode searches in CWD."""
        monkeypatch.chdir(tmp_path)

        search_dir = xdg_paths.get_ad_files_search_dir("portable")

        assert search_dir == tmp_path

    def test_returns_xdg_config_dir_in_xdg_mode(
        self,
        tmp_path:Path,
        monkeypatch:pytest.MonkeyPatch
    ) -> None:
        """Test that XDG mode searches in XDG config directory (same as config file)."""
        xdg_config = tmp_path / "config"
        monkeypatch.setattr(
            "platformdirs.user_config_dir",
            lambda app_name, *args, **kwargs: str(xdg_config / app_name),
        )

        search_dir = xdg_paths.get_ad_files_search_dir("xdg")

        assert "kleinanzeigen-bot" in str(search_dir)
        # Ad files searched in same directory as config file, not separate ads/ subdirectory
        assert search_dir.name == "kleinanzeigen-bot"


class TestGetDownloadedAdsPath:
    """Tests for get_downloaded_ads_path function."""

    def test_returns_cwd_downloaded_ads_in_portable_mode(
        self,
        tmp_path:Path,
        monkeypatch:pytest.MonkeyPatch
    ) -> None:
        """Test that portable mode uses ./downloaded-ads/."""
        monkeypatch.chdir(tmp_path)

        ads_path = xdg_paths.get_downloaded_ads_path("portable")

        assert ads_path == tmp_path / "downloaded-ads"

    def test_creates_directory_if_not_exists(
        self,
        tmp_path:Path,
        monkeypatch:pytest.MonkeyPatch
    ) -> None:
        """Test that directory is created if it doesn't exist."""
        monkeypatch.chdir(tmp_path)

        ads_path = xdg_paths.get_downloaded_ads_path("portable")

        assert ads_path.exists()
        assert ads_path.is_dir()

    def test_returns_xdg_downloaded_ads_in_xdg_mode(
        self,
        tmp_path:Path,
        monkeypatch:pytest.MonkeyPatch
    ) -> None:
        """Test that XDG mode uses XDG config/downloaded-ads/."""
        xdg_config = tmp_path / "config"
        monkeypatch.setattr(
            "platformdirs.user_config_dir",
            lambda app_name, *args, **kwargs: str(xdg_config / app_name),
        )

        ads_path = xdg_paths.get_downloaded_ads_path("xdg")

        assert "kleinanzeigen-bot" in str(ads_path)
        assert ads_path.name == "downloaded-ads"


class TestGetBrowserProfilePath:
    """Tests for get_browser_profile_path function."""

    def test_returns_cwd_temp_in_portable_mode(
        self,
        tmp_path:Path,
        monkeypatch:pytest.MonkeyPatch
    ) -> None:
        """Test that portable mode uses ./.temp/browser-profile."""
        monkeypatch.chdir(tmp_path)

        profile_path = xdg_paths.get_browser_profile_path("portable")

        assert profile_path == tmp_path / ".temp" / "browser-profile"

    def test_returns_xdg_cache_in_xdg_mode(
        self,
        tmp_path:Path,
        monkeypatch:pytest.MonkeyPatch
    ) -> None:
        """Test that XDG mode uses XDG cache directory."""
        xdg_cache = tmp_path / "cache"
        monkeypatch.setenv("XDG_CACHE_HOME", str(xdg_cache))

        profile_path = xdg_paths.get_browser_profile_path("xdg")

        assert "kleinanzeigen-bot" in str(profile_path)
        assert profile_path.name == "browser-profile"

    def test_creates_directory_if_not_exists(
        self,
        tmp_path:Path,
        monkeypatch:pytest.MonkeyPatch
    ) -> None:
        """Test that browser profile directory is created."""
        monkeypatch.chdir(tmp_path)

        profile_path = xdg_paths.get_browser_profile_path("portable")

        assert profile_path.exists()
        assert profile_path.is_dir()


class TestGetLogFilePath:
    """Tests for get_log_file_path function."""

    def test_returns_cwd_log_in_portable_mode(
        self,
        tmp_path:Path,
        monkeypatch:pytest.MonkeyPatch
    ) -> None:
        """Test that portable mode uses ./{basename}.log."""
        monkeypatch.chdir(tmp_path)

        log_path = xdg_paths.get_log_file_path("test", "portable")

        assert log_path == tmp_path / "test.log"

    def test_returns_xdg_state_log_in_xdg_mode(
        self,
        tmp_path:Path,
        monkeypatch:pytest.MonkeyPatch
    ) -> None:
        """Test that XDG mode uses XDG state directory."""
        xdg_state = tmp_path / "state"
        monkeypatch.setenv("XDG_STATE_HOME", str(xdg_state))

        log_path = xdg_paths.get_log_file_path("test", "xdg")

        assert "kleinanzeigen-bot" in str(log_path)
        assert log_path.name == "test.log"


class TestGetUpdateCheckStatePath:
    """Tests for get_update_check_state_path function."""

    def test_returns_cwd_temp_in_portable_mode(
        self,
        tmp_path:Path,
        monkeypatch:pytest.MonkeyPatch
    ) -> None:
        """Test that portable mode uses ./.temp/update_check_state.json."""
        monkeypatch.chdir(tmp_path)

        state_path = xdg_paths.get_update_check_state_path("portable")

        assert state_path == tmp_path / ".temp" / "update_check_state.json"

    def test_returns_xdg_state_in_xdg_mode(
        self,
        tmp_path:Path,
        monkeypatch:pytest.MonkeyPatch
    ) -> None:
        """Test that XDG mode uses XDG state directory."""
        xdg_state = tmp_path / "state"
        monkeypatch.setenv("XDG_STATE_HOME", str(xdg_state))

        state_path = xdg_paths.get_update_check_state_path("xdg")

        assert "kleinanzeigen-bot" in str(state_path)
        assert state_path.name == "update_check_state.json"


class TestPromptInstallationMode:
    """Tests for prompt_installation_mode function."""

    @pytest.fixture(autouse = True)
    def _force_identity_translation(self, monkeypatch:pytest.MonkeyPatch) -> None:
        """Ensure prompt strings are stable regardless of locale."""
        monkeypatch.setattr(xdg_paths, "_", lambda message: message)

    def test_returns_portable_for_non_interactive_mode_no_stdin(
        self,
        monkeypatch:pytest.MonkeyPatch
    ) -> None:
        """Test that non-interactive mode (no stdin) defaults to portable."""
        # Mock sys.stdin to be None (simulates non-interactive environment)
        monkeypatch.setattr("sys.stdin", None)

        mode = xdg_paths.prompt_installation_mode()

        assert mode == "portable"

    def test_returns_portable_for_non_interactive_mode_not_tty(
        self,
        monkeypatch:pytest.MonkeyPatch
    ) -> None:
        """Test that non-interactive mode (not a TTY) defaults to portable."""
        # Mock sys.stdin.isatty() to return False (simulates piped input or file redirect)
        mock_stdin = io.StringIO()
        mock_stdin.isatty = lambda: False  # type: ignore[method-assign]
        monkeypatch.setattr("sys.stdin", mock_stdin)

        mode = xdg_paths.prompt_installation_mode()

        assert mode == "portable"

    def test_returns_portable_when_user_enters_1(
        self,
        monkeypatch:pytest.MonkeyPatch,
        capsys:pytest.CaptureFixture[str]
    ) -> None:
        """Test that user entering '1' selects portable mode."""
        # Mock sys.stdin to simulate interactive terminal
        mock_stdin = io.StringIO()
        mock_stdin.isatty = lambda: True  # type: ignore[method-assign]
        monkeypatch.setattr("sys.stdin", mock_stdin)

        # Mock interactive input
        monkeypatch.setattr("builtins.input", lambda _: "1")

        mode = xdg_paths.prompt_installation_mode()

        assert mode == "portable"
        # Verify prompt was shown
        captured = capsys.readouterr()
        assert "Choose installation type:" in captured.out
        assert "[1] Portable" in captured.out

    def test_returns_xdg_when_user_enters_2(
        self,
        monkeypatch:pytest.MonkeyPatch,
        capsys:pytest.CaptureFixture[str]
    ) -> None:
        """Test that user entering '2' selects XDG mode."""
        # Mock sys.stdin to simulate interactive terminal
        mock_stdin = io.StringIO()
        mock_stdin.isatty = lambda: True  # type: ignore[method-assign]
        monkeypatch.setattr("sys.stdin", mock_stdin)

        # Mock interactive input
        monkeypatch.setattr("builtins.input", lambda _: "2")

        mode = xdg_paths.prompt_installation_mode()

        assert mode == "xdg"
        # Verify prompt was shown
        captured = capsys.readouterr()
        assert "Choose installation type:" in captured.out
        assert "[2] System-wide" in captured.out

    def test_reprompts_on_invalid_input_then_accepts_valid(
        self,
        monkeypatch:pytest.MonkeyPatch,
        capsys:pytest.CaptureFixture[str]
    ) -> None:
        """Test that invalid input causes re-prompt, then valid input is accepted."""
        # Mock sys.stdin to simulate interactive terminal
        mock_stdin = io.StringIO()
        mock_stdin.isatty = lambda: True  # type: ignore[method-assign]
        monkeypatch.setattr("sys.stdin", mock_stdin)

        # Mock sequence of inputs: invalid, then valid
        inputs = iter(["3", "invalid", "1"])
        monkeypatch.setattr("builtins.input", lambda _: next(inputs))

        mode = xdg_paths.prompt_installation_mode()

        assert mode == "portable"
        # Verify error message was shown
        captured = capsys.readouterr()
        assert "Invalid choice" in captured.out

    def test_returns_portable_on_eof_error(
        self,
        monkeypatch:pytest.MonkeyPatch,
        capsys:pytest.CaptureFixture[str]
    ) -> None:
        """Test that EOFError (Ctrl+D) defaults to portable mode."""
        # Mock sys.stdin to simulate interactive terminal
        mock_stdin = io.StringIO()
        mock_stdin.isatty = lambda: True  # type: ignore[method-assign]
        monkeypatch.setattr("sys.stdin", mock_stdin)

        # Mock input raising EOFError
        def mock_input(_:str) -> str:
            raise EOFError

        monkeypatch.setattr("builtins.input", mock_input)

        mode = xdg_paths.prompt_installation_mode()

        assert mode == "portable"
        # Verify newline was printed after EOF
        captured = capsys.readouterr()
        assert captured.out.endswith("\n")

    def test_returns_portable_on_keyboard_interrupt(
        self,
        monkeypatch:pytest.MonkeyPatch,
        capsys:pytest.CaptureFixture[str]
    ) -> None:
        """Test that KeyboardInterrupt (Ctrl+C) defaults to portable mode."""
        # Mock sys.stdin to simulate interactive terminal
        mock_stdin = io.StringIO()
        mock_stdin.isatty = lambda: True  # type: ignore[method-assign]
        monkeypatch.setattr("sys.stdin", mock_stdin)

        # Mock input raising KeyboardInterrupt
        def mock_input(_:str) -> str:
            raise KeyboardInterrupt

        monkeypatch.setattr("builtins.input", mock_input)

        mode = xdg_paths.prompt_installation_mode()

        assert mode == "portable"
        # Verify newline was printed after interrupt
        captured = capsys.readouterr()
        assert captured.out.endswith("\n")


class TestGetBrowserProfilePathWithOverride:
    """Tests for get_browser_profile_path config_override parameter."""

    def test_respects_config_override_in_portable_mode(
        self,
        tmp_path:Path,
        monkeypatch:pytest.MonkeyPatch
    ) -> None:
        """Test that config_override takes precedence in portable mode."""
        monkeypatch.chdir(tmp_path)

        custom_path = str(tmp_path / "custom" / "browser")
        profile_path = xdg_paths.get_browser_profile_path("portable", config_override = custom_path)

        assert profile_path == Path(custom_path)
        assert profile_path.exists()  # Verify directory was created
        assert profile_path.is_dir()

    def test_respects_config_override_in_xdg_mode(
        self,
        tmp_path:Path,
        monkeypatch:pytest.MonkeyPatch
    ) -> None:
        """Test that config_override takes precedence in XDG mode."""
        xdg_cache = tmp_path / "cache"
        monkeypatch.setenv("XDG_CACHE_HOME", str(xdg_cache))

        custom_path = str(tmp_path / "custom" / "browser")
        profile_path = xdg_paths.get_browser_profile_path("xdg", config_override = custom_path)

        assert profile_path == Path(custom_path)
        # Verify it didn't use XDG cache directory
        assert str(profile_path) != str(xdg_cache / "kleinanzeigen-bot" / "browser-profile")
        assert profile_path.exists()
        assert profile_path.is_dir()


class TestUnicodeHandling:
    """Tests for Unicode path handling (NFD vs NFC normalization)."""

    def test_portable_mode_handles_unicode_in_cwd(
        self,
        tmp_path:Path,
        monkeypatch:pytest.MonkeyPatch
    ) -> None:
        """Test that portable mode works with Unicode characters in CWD path.

        This tests the edge case where the current directory contains Unicode
        characters (e.g., user names with umlauts), which may be stored in
        different normalization forms (NFD on macOS, NFC on Linux/Windows).
        """
        # Create directory with German umlaut in composed (NFC) form
        # ä = U+00E4 (NFC) vs a + ̈ = U+0061 + U+0308 (NFD)
        unicode_dir = tmp_path / "Müller_config"
        unicode_dir.mkdir()
        monkeypatch.chdir(unicode_dir)

        # Get paths - should work regardless of normalization
        config_path = xdg_paths.get_config_file_path("portable")
        log_path = xdg_paths.get_log_file_path("test", "portable")

        # Verify paths are within the Unicode directory
        assert config_path.parent == unicode_dir
        assert log_path.parent == unicode_dir
        assert config_path.name == "config.yaml"
        assert log_path.name == "test.log"

    def test_xdg_mode_handles_unicode_in_paths(
        self,
        tmp_path:Path,
        monkeypatch:pytest.MonkeyPatch
    ) -> None:
        """Test that XDG mode handles Unicode in XDG directory paths.

        This tests the edge case where XDG directories contain Unicode
        characters (e.g., /Users/Müller/.config/), which may be in NFD
        form on macOS filesystems.
        """
        # Create XDG directory with umlaut
        xdg_base = tmp_path / "Users" / "Müller" / ".config"
        xdg_base.mkdir(parents = True)

        monkeypatch.setattr(
            "platformdirs.user_config_dir",
            lambda app_name, *args, **kwargs: str(xdg_base / app_name),
        )

        # Get config path
        config_path = xdg_paths.get_config_file_path("xdg")

        # Verify path contains the Unicode directory
        assert "Müller" in str(config_path) or "Mu\u0308ller" in str(config_path)
        assert config_path.name == "config.yaml"

    def test_downloaded_ads_path_handles_unicode(
        self,
        tmp_path:Path,
        monkeypatch:pytest.MonkeyPatch
    ) -> None:
        """Test that downloaded ads directory creation works with Unicode paths."""
        # Create XDG config directory with umlaut
        xdg_config = tmp_path / "config" / "Müller"
        xdg_config.mkdir(parents = True)

        monkeypatch.setattr(
            "platformdirs.user_config_dir",
            lambda app_name, *args, **kwargs: str(xdg_config / app_name),
        )

        # Get downloaded ads path - this will create the directory
        ads_path = xdg_paths.get_downloaded_ads_path("xdg")

        # Verify directory was created successfully
        assert ads_path.exists()
        assert ads_path.is_dir()
        assert ads_path.name == "downloaded-ads"
