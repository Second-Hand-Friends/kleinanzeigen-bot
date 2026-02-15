# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

from __future__ import annotations

import difflib
import json
import subprocess  # noqa: S404
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from pydantic import BaseModel

from kleinanzeigen_bot.model.ad_model import AdPartial
from kleinanzeigen_bot.model.config_model import Config

SCHEMA_DEFINITIONS:Final[tuple[tuple[str, type[BaseModel], str], ...]] = (
    ("schemas/config.schema.json", Config, "Config"),
    ("schemas/ad.schema.json", AdPartial, "Ad"),
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


def generate_schema_content(model:type[BaseModel], name:str) -> str:
    schema = model.model_json_schema(mode = "validation")
    schema.setdefault("title", f"{name} Schema")
    schema.setdefault("description", f"Auto-generated JSON Schema for {name}")
    return json.dumps(schema, indent = 2) + "\n"


def get_schema_diffs(repo_root:Path) -> dict[str, str]:
    diffs:dict[str, str] = {}
    for schema_path, model, schema_name in SCHEMA_DEFINITIONS:
        expected_schema_path = repo_root / schema_path
        expected = expected_schema_path.read_text(encoding = "utf-8") if expected_schema_path.is_file() else ""

        generated = generate_schema_content(model, schema_name)
        if expected == generated:
            continue

        diffs[schema_path] = "".join(
            difflib.unified_diff(
                expected.splitlines(keepends = True),
                generated.splitlines(keepends = True),
                fromfile = schema_path,
                tofile = f"<generated via: {model.__name__}.model_json_schema>",
            )
        )

    return diffs


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

    schema_diffs = get_schema_diffs(repo_root)
    default_config_diff = get_default_config_diff(repo_root)

    if schema_diffs or default_config_diff:
        messages:list[str] = ["Generated artifacts are not up-to-date."]

        if schema_diffs:
            messages.append("Outdated schema files detected:")
            for path, schema_diff in schema_diffs.items():
                messages.append(f"- {path}")
                messages.append(schema_diff)

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
