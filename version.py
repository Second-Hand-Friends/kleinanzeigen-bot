"""
SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""

import shutil
import subprocess
from datetime import datetime, timezone


# used in pyproject.toml [tool.pdm.version]
def get_version() -> str:
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("git executable not found, unable to compute version")
    result = subprocess.run(  # noqa: S603 running git is safe here
        [git, "rev-parse", "--short", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    commit_hash = result.stdout.strip()
    return f"{datetime.now(timezone.utc).year}+{commit_hash}"
