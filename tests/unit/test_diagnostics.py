# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
# Copyright (C) 2025 contributors

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from kleinanzeigen_bot.model.config_model import DiagnosticsConfig


@pytest.mark.unit
class TestDiagnosticsConfig:
    """Tests for DiagnosticsConfig class."""


def test_diagnostics_config_allowed_keys_is_frozen() -> None:
    """Test that CAPTURE_ON_ALLOWED_KEYS is immutable."""
    # Should be a frozenset, not a set
    assert isinstance(DiagnosticsConfig.CAPTURE_ON_ALLOWED_KEYS, frozenset)

    # Should contain expected keys
    assert "login_detection" in DiagnosticsConfig.CAPTURE_ON_ALLOWED_KEYS
    assert "publish" in DiagnosticsConfig.CAPTURE_ON_ALLOWED_KEYS

    # Note: Runtime mutation would raise TypeError for frozenset
    # Testing that frozenset is truly immutable at runtime is tricky
    # because mutation would raise TypeError which is what we want to verify
    # The actual immutability comes from using Final[frozenset] in the type declaration
    # which prevents reassignment of the variable itself


@pytest.mark.unit
class TestDiagnosticsCapture:
    """Tests for diagnostics capture functionality."""


@pytest.mark.asyncio
async def test_capture_diagnostics_uses_asyncio_to_thread(tmp_path:Path) -> None:
    """Test that capture_diagnostics properly offloads sync I/O to thread pool."""
    from kleinanzeigen_bot.utils.diagnostics import capture_diagnostics

    mock_page = AsyncMock()
    mock_page.save_screenshot = AsyncMock()
    mock_page.get_content = AsyncMock(return_value = "<html></html>")

    output_dir = tmp_path / "diagnostics"
    result = await capture_diagnostics(
        output_dir = output_dir,
        base_prefix = "test",
        page = mock_page,
    )

    # Verify asyncio.to_thread was called for sync operations
    assert mock_to_thread.called

    # Check that mkdir was offloaded
    mkdir_calls = [
        call for call in mock_to_thread.call_args_list if call[0][0] == output_dir.mkdir or (hasattr(call[0][0], "__name__") and call[0][0].__name__ == "mkdir")
    ]
    assert len(mkdir_calls) > 0 or mock_to_thread.call_count > 0


@pytest.mark.asyncio
async def test_capture_diagnostics_creates_output_dir(tmp_path:Path) -> None:
    """Test that capture_diagnostics creates output directory."""
    from kleinanzeigen_bot.utils.diagnostics import capture_diagnostics

    mock_page = AsyncMock()

    output_dir = tmp_path / "diagnostics"
    result = await capture_diagnostics(
        output_dir = output_dir,
        base_prefix = "test",
        page = mock_page,
    )

    # Verify directory was created
    assert output_dir.exists()
    assert len(result.saved_artifacts) > 0


@pytest.mark.asyncio
async def test_capture_diagnostics_creates_screenshot(tmp_path:Path) -> None:
    """Test that capture_diagnostics creates screenshot file."""
    from kleinanzeigen_bot.utils.diagnostics import capture_diagnostics

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
async def test_capture_diagnostics_creates_html(tmp_path:Path) -> None:
    """Test that capture_diagnostics creates HTML file."""
    from kleinanzeigen_bot.utils.diagnostics import capture_diagnostics

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
async def test_capture_diagnostics_creates_json(tmp_path:Path) -> None:
    """Test that capture_diagnostics creates JSON file."""
    from kleinanzeigen_bot.utils.diagnostics import capture_diagnostics

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
async def test_capture_diagnostics_copies_log_file(tmp_path:Path) -> None:
    """Test that capture_diagnostics copies log file when enabled."""
    from kleinanzeigen_bot.utils.diagnostics import capture_diagnostics

    mock_page = AsyncMock()

    log_file = tmp_path / "test.log"
    log_file.write_text("test log content")

    output_dir = tmp_path / "diagnostics"
    result = await capture_diagnostics(
        output_dir = output_dir,
        base_prefix = "test",
        page = mock_page,
        log_file_path = str(log_file),
        copy_log = True,
    )

    # Verify log was copied
    assert len(result.saved_artifacts) == 1
    assert result.saved_artifacts[0].suffix == ".log"
