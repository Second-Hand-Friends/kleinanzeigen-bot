# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import json
from pathlib import Path
from typing import Type

from pydantic import BaseModel

from kleinanzeigen_bot.model.ad_model import AdPartial
from kleinanzeigen_bot.model.config_model import Config


def generate_schema(model:Type[BaseModel], name:str, out_dir:Path) -> None:
    """
    Generate and write JSON schema for the given model.
    """
    print(f"[+] Generating schema for model [{name}]...")

    # Create JSON Schema dict
    schema = model.model_json_schema(mode = "validation")
    schema.setdefault("title", f"{name} Schema")
    schema.setdefault("description", f"Auto-generated JSON Schema for {name}")

    # Write JSON
    json_path = out_dir / f"{name.lower()}.schema.json"
    with json_path.open("w", encoding = "utf-8") as f_json:
        json.dump(schema, f_json, indent = 2)
        f_json.write("\n")
        print(f"[✓] {json_path}")


project_root = Path(__file__).parent.parent
out_dir = project_root / "schemas"
out_dir.mkdir(parents = True, exist_ok = True)

print(f"Generating schemas in: {out_dir.resolve()}")
generate_schema(Config, "Config", out_dir)
generate_schema(AdPartial, "Ad", out_dir)
print("All schemas generated successfully.")
