# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic import BaseModel


def generate_schema_content(model:type[BaseModel], name:str) -> str:
    """
    Build normalized JSON schema output for project models.
    """
    schema = model.model_json_schema(mode = "validation")
    schema.setdefault("title", f"{name} Schema")
    schema.setdefault("description", f"Auto-generated JSON Schema for {name}")
    return json.dumps(schema, indent = 2) + "\n"
