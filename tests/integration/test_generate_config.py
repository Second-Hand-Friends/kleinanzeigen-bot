# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Regression coverage for config generation with a different Python on PATH."""

import json
import os
import shutil
import subprocess  # noqa: S404
import sys
from pathlib import Path

import pytest


@pytest.mark.itest
def test_generate_config_uses_current_interpreter(tmp_path:Path) -> None:
    repo_root = Path(__file__).resolve().parent.parent.parent
    pdm_path = shutil.which("pdm")
    assert pdm_path is not None, "PDM is required to test the generate-config task"
    # Expose test dependencies only to the task; PDM itself may use a different Python.
    python_path = os.pathsep.join(str(Path(path).resolve()) for path in sys.path if path)
    (tmp_path / "pyproject.toml").write_text(
        (repo_root / "pyproject.toml").read_text(encoding = "utf-8")
        + f"\n[tool.pdm.scripts._]\nenv = {{ PYTHONPATH = {json.dumps(python_path)} }}\n",
        encoding = "utf-8",
    )
    (tmp_path / ".pdm-python").write_text(sys.executable, encoding = "utf-8")
    (tmp_path / "pdm.toml").write_text("[python]\nuse_venv = false\n", encoding = "utf-8")
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

    result = subprocess.run(  # noqa: S603 trusted test command
        [pdm_path, "run", "generate-config"],
        env = {
            **{key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
            "PATH": str(bin_path),
            "PDM_CHECK_UPDATE": "false",
        },
        cwd = tmp_path,
        check = False,
        capture_output = True,
        text = True,
        timeout = 60,
    )

    assert result.returncode == 0, result.stderr
    assert config_path.read_bytes() == (repo_root / "docs/config.default.yaml").read_bytes()
