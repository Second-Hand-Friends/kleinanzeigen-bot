# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Regenerate the default config using the interpreter selected by PDM."""

import subprocess  # noqa: S404
import sys
from pathlib import Path


def main() -> None:
    """Replace the default config snapshot through the CLI with the current interpreter."""
    repo_root = Path(__file__).resolve().parent.parent
    config_path = repo_root / "docs/config.default.yaml"
    config_path.unlink(missing_ok = True)
    subprocess.run(  # noqa: S603 trusted, static command arguments
        [sys.executable, "-m", "kleinanzeigen_bot", "--config", str(config_path), "create-config"],
        cwd = repo_root,
        check = True,
        timeout = 60,
    )


if __name__ == "__main__":
    main()
