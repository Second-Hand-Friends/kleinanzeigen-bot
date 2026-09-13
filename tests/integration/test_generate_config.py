# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Regression coverage for config generation with a different Python on PATH."""

import os
import subprocess  # noqa: S404
import sys
from pathlib import Path

import pytest


@pytest.mark.itest
def test_generate_config_uses_current_interpreter(tmp_path:Path) -> None:
    repo_root = Path(__file__).resolve().parent.parent.parent
    script_path = tmp_path / "scripts/generate_config.py"
    script_path.parent.mkdir()
    script_path.write_bytes((repo_root / "scripts/generate_config.py").read_bytes())
    config_path = tmp_path / "docs/config.default.yaml"
    config_path.parent.mkdir()
    config_path.write_text("stale config", encoding = "utf-8")

    # A PATH-resolved Python cannot run; the selected interpreter must be used instead.
    bin_path = tmp_path / "bin"
    bin_path.mkdir()
    for name in ("python", "python.exe"):
        wrong_python = bin_path / name
        wrong_python.write_text("invalid executable", encoding = "utf-8")
        wrong_python.chmod(0o755)

    subprocess.run(  # noqa: S603 trusted test command
        [sys.executable, str(script_path)],
        env = {**os.environ, "PATH": str(bin_path)},
        cwd = tmp_path,
        check = True,
        capture_output = True,
        timeout = 60,
    )

    assert config_path.read_bytes() == (repo_root / "docs/config.default.yaml").read_bytes()
