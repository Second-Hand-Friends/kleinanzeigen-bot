# SPDX-FileCopyrightText: © 2025 Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from kleinanzeigen_bot.utils import diagnostics as diagnostics_module
from kleinanzeigen_bot.utils.diagnostics import capture_diagnostics


@pytest.mark.unit
class TestDiagnosticsCapture:
    """Tests for diagnostics capture functionality."""

    @pytest.mark.asyncio
    async def test_capture_diagnostics_creates_output_dir(self, tmp_path:Path) -> None:
        """Test that capture_diagnostics creates output directory."""
        mock_page = AsyncMock()

        output_dir = tmp_path / "diagnostics"
        _ = await capture_diagnostics(
            output_dir = output_dir,
            base_prefix = "test",
            page = mock_page,
        )

        # Verify directory was created
        assert output_dir.exists()

    @pytest.mark.asyncio
    async def test_capture_diagnostics_creates_screenshot(self, tmp_path:Path) -> None:
        """Test that capture_diagnostics creates screenshot file."""
        mock_page = AsyncMock()
        mock_page.save_screenshot = AsyncMock()

        output_dir = tmp_path / "diagnostics"
        result = await capture_diagnostics(
            output_dir = output_dir,
            base_prefix = "test",
            page = mock_page,
        )

        # Verify screenshot file was created and page method was called
        assert len(result.saved_artifacts) == 1
        assert result.saved_artifacts[0].suffix == ".png"
        mock_page.save_screenshot.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_capture_diagnostics_creates_html(self, tmp_path:Path) -> None:
        """Test that capture_diagnostics creates HTML file."""
        mock_page = AsyncMock()
        mock_page.get_content = AsyncMock(return_value = "<html></html>")

        output_dir = tmp_path / "diagnostics"
        result = await capture_diagnostics(
            output_dir = output_dir,
            base_prefix = "test",
            page = mock_page,
        )

        # Verify HTML file was created along with screenshot
        assert len(result.saved_artifacts) == 2
        assert any(a.suffix == ".html" for a in result.saved_artifacts)

    @pytest.mark.asyncio
    async def test_capture_diagnostics_creates_json(self, tmp_path:Path) -> None:
        """Test that capture_diagnostics creates JSON file."""
        mock_page = AsyncMock()
        mock_page.get_content = AsyncMock(return_value = "<html></html>")

        output_dir = tmp_path / "diagnostics"
        result = await capture_diagnostics(
            output_dir = output_dir,
            base_prefix = "test",
            page = mock_page,
            json_payload = {"test": "data"},
        )

        # Verify JSON file was created along with HTML and screenshot
        assert len(result.saved_artifacts) == 3
        assert any(a.suffix == ".json" for a in result.saved_artifacts)

    @pytest.mark.asyncio
    async def test_capture_diagnostics_copies_log_file(self, tmp_path:Path) -> None:
        """Test that capture_diagnostics copies log file when enabled."""
        log_file = tmp_path / "test.log"
        log_file.write_text("test log content")

        output_dir = tmp_path / "diagnostics"
        result = await capture_diagnostics(
            output_dir = output_dir,
            base_prefix = "test",
            page = None,  # No page to avoid screenshot
            log_file_path = str(log_file),
            copy_log = True,
        )

        # Verify log was copied
        assert len(result.saved_artifacts) == 1
        assert result.saved_artifacts[0].suffix == ".log"

    def test_copy_log_sync_returns_false_when_file_not_found(self, tmp_path:Path) -> None:
        """Test _copy_log_sync returns False when log file does not exist."""
        non_existent_log = tmp_path / "non_existent.log"
        log_path = tmp_path / "output.log"

        result = diagnostics_module._copy_log_sync(str(non_existent_log), log_path)

        assert result is False
        assert not log_path.exists()

    @pytest.mark.asyncio
    async def test_capture_diagnostics_handles_screenshot_exception(self, tmp_path:Path, caplog:pytest.LogCaptureFixture) -> None:
        """Test that capture_diagnostics handles screenshot capture exceptions gracefully."""
        mock_page = AsyncMock()
        mock_page.save_screenshot = AsyncMock(side_effect = Exception("Screenshot failed"))

        output_dir = tmp_path / "diagnostics"
        result = await capture_diagnostics(
            output_dir = output_dir,
            base_prefix = "test",
            page = mock_page,
        )

        # Verify no artifacts were saved due to exception
        assert len(result.saved_artifacts) == 0
        assert "Diagnostics screenshot capture failed" in caplog.text

    @pytest.mark.asyncio
    async def test_capture_diagnostics_handles_json_exception(self, tmp_path:Path, caplog:pytest.LogCaptureFixture, monkeypatch:pytest.MonkeyPatch) -> None:
        """Test that capture_diagnostics handles JSON write exceptions gracefully."""
        mock_page = AsyncMock()
        mock_page.get_content = AsyncMock(return_value = "<html></html>")

        output_dir = tmp_path / "diagnostics"

        # Mock _write_json_sync to raise an exception
        monkeypatch.setattr(diagnostics_module, "_write_json_sync", MagicMock(side_effect = Exception("JSON write failed")))

        result = await capture_diagnostics(
            output_dir = output_dir,
            base_prefix = "test",
            page = mock_page,
            json_payload = {"test": "data"},
        )

        # Verify screenshot and HTML were saved, but JSON failed
        assert len(result.saved_artifacts) == 2
        assert any(a.suffix == ".png" for a in result.saved_artifacts)
        assert any(a.suffix == ".html" for a in result.saved_artifacts)
        assert not any(a.suffix == ".json" for a in result.saved_artifacts)
        assert "Diagnostics JSON capture failed" in caplog.text

    @pytest.mark.asyncio
    async def test_capture_diagnostics_handles_log_copy_exception(
        self, tmp_path:Path, caplog:pytest.LogCaptureFixture, monkeypatch:pytest.MonkeyPatch
    ) -> None:
        """Test that capture_diagnostics handles log copy exceptions gracefully."""
        # Create a log file
        log_file = tmp_path / "test.log"
        log_file.write_text("test log content")

        output_dir = tmp_path / "diagnostics"

        # Mock _copy_log_sync to raise an exception
        original_copy_log = diagnostics_module._copy_log_sync
        monkeypatch.setattr(diagnostics_module, "_copy_log_sync", MagicMock(side_effect = Exception("Copy failed")))

        try:
            result = await capture_diagnostics(
                output_dir = output_dir,
                base_prefix = "test",
                page = None,
                log_file_path = str(log_file),
                copy_log = True,
            )

            # Verify no artifacts were saved due to exception
            assert len(result.saved_artifacts) == 0
            assert "Diagnostics log copy failed" in caplog.text
        finally:
            monkeypatch.setattr(diagnostics_module, "_copy_log_sync", original_copy_log)

    @pytest.mark.asyncio
    async def test_capture_diagnostics_logs_warning_when_all_captures_fail(
        self, tmp_path:Path, caplog:pytest.LogCaptureFixture, monkeypatch:pytest.MonkeyPatch
    ) -> None:
        """Test warning is logged when capture is requested but all fail."""
        mock_page = AsyncMock()
        mock_page.save_screenshot = AsyncMock(side_effect = Exception("Screenshot failed"))
        mock_page.get_content = AsyncMock(side_effect = Exception("HTML failed"))

        # Mock JSON write to also fail
        monkeypatch.setattr(diagnostics_module, "_write_json_sync", MagicMock(side_effect = Exception("JSON write failed")))

        output_dir = tmp_path / "diagnostics"
        result = await capture_diagnostics(
            output_dir = output_dir,
            base_prefix = "test",
            page = mock_page,
            json_payload = {"test": "data"},
        )

        # Verify no artifacts were saved
        assert len(result.saved_artifacts) == 0
        assert "Diagnostics capture attempted but no artifacts were saved" in caplog.text

    @pytest.mark.asyncio
    async def test_capture_diagnostics_logs_debug_when_no_capture_requested(self, tmp_path:Path, caplog:pytest.LogCaptureFixture) -> None:
        """Test debug is logged when no diagnostics capture is requested."""
        output_dir = tmp_path / "diagnostics"

        with caplog.at_level("DEBUG"):
            _ = await capture_diagnostics(
                output_dir = output_dir,
                base_prefix = "test",
                page = None,
                json_payload = None,
                copy_log = False,
            )

        assert "No diagnostics capture requested" in caplog.text
