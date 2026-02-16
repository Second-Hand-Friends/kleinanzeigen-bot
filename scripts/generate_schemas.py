# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
from pathlib import Path

from pydantic import BaseModel
from schema_utils import generate_schema_content

from kleinanzeigen_bot.model.ad_model import AdPartial
from kleinanzeigen_bot.model.config_model import Config


def generate_schema(model:type[BaseModel], name:str, out_dir:Path) -> None:
    """
    Generate and write JSON schema for the given model.
    """
    print(f"[+] Generating schema for model [{name}]...")

    schema_content = generate_schema_content(model, name)

    # Write JSON
    json_path = out_dir / f"{name.lower()}.schema.json"
    with json_path.open("w", encoding = "utf-8") as json_file:
        json_file.write(schema_content)
        print(f"[OK] {json_path}")


project_root = Path(__file__).parent.parent
out_dir = project_root / "schemas"
out_dir.mkdir(parents = True, exist_ok = True)

print(f"Generating schemas in: {out_dir.resolve()}")
generate_schema(Config, "Config", out_dir)
generate_schema(AdPartial, "Ad", out_dir)
print("All schemas generated successfully.")
