# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
from __future__ import annotations

import difflib
import shutil
import subprocess  # noqa: S404
import sys
import tempfile
from pathlib import Path
from typing import Final

SCHEMA_FILES:Final[tuple[str, ...]] = (
    "schemas/config.schema.json",
    "schemas/ad.schema.json",
)
DEFAULT_CONFIG_PATH:Final[Path] = Path("docs/config.default.yaml")


def generate_default_config_via_cli(path:Path, repo_root:Path) -> None:
    subprocess.run(  # noqa: S603 trusted, static command arguments
        [
            sys.executable,
            "-m",
            "kleinanzeigen_bot",
            "--config",
            str(path),
            "create-config",
        ],
        cwd = repo_root,
        check = True,
    )


def get_changed_schema_files(repo_root:Path, git_executable:str) -> list[str]:
    diff_result = subprocess.run(  # noqa: S603 trusted, static command arguments
        [git_executable, "diff", "--name-only", "--", *SCHEMA_FILES],
        cwd = repo_root,
        check = True,
        capture_output = True,
        text = True,
    )
    return [line.strip() for line in diff_result.stdout.splitlines() if line.strip()]


def get_default_config_diff(repo_root:Path) -> str:
    expected_config_path = repo_root / DEFAULT_CONFIG_PATH
    if not expected_config_path.is_file():
        raise FileNotFoundError(f"Missing required default config file: {DEFAULT_CONFIG_PATH}")

    with tempfile.TemporaryDirectory() as tmpdir:
        generated_config_path = Path(tmpdir) / "config.default.yaml"
        generate_default_config_via_cli(generated_config_path, repo_root)

        expected = expected_config_path.read_text(encoding = "utf-8")
        generated = generated_config_path.read_text(encoding = "utf-8")

    if expected == generated:
        return ""

    return "".join(
        difflib.unified_diff(
            expected.splitlines(keepends = True),
            generated.splitlines(keepends = True),
            fromfile = str(DEFAULT_CONFIG_PATH),
            tofile = "<generated via: python -m kleinanzeigen_bot --config /tmp/config.default.yaml create-config>",
        )
    )


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent

    try:
        subprocess.run(  # noqa: S603 trusted, static command arguments
            [sys.executable, "scripts/generate_schemas.py"],
            cwd = repo_root,
            check = True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Failed to generate schemas (exit code {exc.returncode})") from exc

    git_executable = shutil.which("git")
    if git_executable is None:
        raise RuntimeError("git executable not found")

    changed_schema_files = get_changed_schema_files(repo_root, git_executable)
    default_config_diff = get_default_config_diff(repo_root)

    if changed_schema_files or default_config_diff:
        messages:list[str] = ["Generated artifacts are not up-to-date."]

        if changed_schema_files:
            messages.append("Outdated schema files detected:")
            messages.extend(f"- {path}" for path in changed_schema_files)

        if default_config_diff:
            messages.append("Outdated docs/config.default.yaml detected.")
            messages.append(default_config_diff)

        messages.append("Regenerate with one of the following:")
        messages.append("- Schema files: pdm run generate-schemas")
        messages.append("- Default config snapshot: pdm run generate-config")
        messages.append("- Both: pdm run generate-artifacts")
        raise SystemExit("\n".join(messages))

    print("Generated schemas and docs/config.default.yaml are up-to-date.")


if __name__ == "__main__":
    main()
