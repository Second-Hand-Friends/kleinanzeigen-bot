"""
SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""
import os
import shutil
import subprocess
from datetime import datetime, timezone


# used in pyproject.toml [tool.pdm.version]
def get_version() -> str:
    commit_hash = os.environ.get("GIT_COMMIT_HASH", "").strip()
    if commit_hash:
        return f"{datetime.now(timezone.utc).year}+{commit_hash}"

    git = shutil.which("git")
    if git is None:
        raise RuntimeError("unable to compute version: set GIT_COMMIT_HASH or build from a valid git checkout")
    try:
        result = subprocess.run(  # noqa: S603 running git is safe here
            [git, "rev-parse", "--short", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as ex:
        raise RuntimeError("unable to compute version: set GIT_COMMIT_HASH or build from a valid git checkout") from ex
    commit_hash = result.stdout.strip()
    return f"{datetime.now(timezone.utc).year}+{commit_hash}"
