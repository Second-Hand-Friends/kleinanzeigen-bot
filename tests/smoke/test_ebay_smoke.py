# SPDX-FileCopyrightText: © Tomblarom and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Minimal eBay CLI health check without credentials, browsers or network."""
from __future__ import annotations

import os
import subprocess  # noqa: S404 - runs fixed local CLI entry point
import sys
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.smoke
def test_ebay_dry_run_starts_without_credentials(tmp_path:Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text('ad_files: ["ad_*.yaml"]\nebay: {}\n', encoding = "utf-8")
    (tmp_path / "ad_camera.yaml").write_text(
        'title: Camera with original box\ncategory: "161"\ndescription: "Good condition. Visit https://example.com"\nprice: 50\n',
        encoding = "utf-8",
    )
    environment = {**os.environ, "XDG_STATE_HOME": str(tmp_path / "state"), "XDG_CONFIG_HOME": str(tmp_path / "user-config")}
    result = subprocess.run(  # noqa: S603 - fixed CLI entry point with temporary local fixtures
        [sys.executable, "-m", "kleinanzeigen_bot", "ebay-publish", "--dry-run", "--lang=en", "--workspace-mode=portable",
         "--config", str(config), "--logfile", str(tmp_path / "ebay.log")],
        capture_output = True, text = True, env = environment, timeout = 30, check = False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "1 links removed" in result.stdout + result.stderr
    assert "Good condition. Visit" in result.stdout + result.stderr
    assert "1 links removed" in (tmp_path / "ebay.log").read_text(encoding = "utf-8")
    assert not (tmp_path / "user-config" / "kleinanzeigen-bot" / "ebay").exists()
