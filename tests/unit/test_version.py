# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import importlib
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

import version


class TestVersion:
    def test_get_version_prefers_git_commit_hash_env_var(self, monkeypatch:pytest.MonkeyPatch) -> None:
        """Use explicit build metadata when present."""
        monkeypatch.setenv("GIT_COMMIT_HASH", "abc1234")

        with patch("version.shutil.which") as which_mock, patch("version.subprocess.run") as run_mock:
            assert version.get_version() == f"{datetime.now(timezone.utc).year}+abc1234"

        which_mock.assert_not_called()
        run_mock.assert_not_called()

    def test_get_version_falls_back_to_git(self, monkeypatch:pytest.MonkeyPatch) -> None:
        """Resolve the version from git metadata when no override is provided."""
        monkeypatch.delenv("GIT_COMMIT_HASH", raising = False)
        result = MagicMock(stdout = "deadbee\n")

        with patch("version.shutil.which", return_value = "/usr/bin/git") as which_mock, patch("version.subprocess.run", return_value = result) as run_mock:
            assert version.get_version() == f"{datetime.now(timezone.utc).year}+deadbee"

        which_mock.assert_called_once_with("git")
        run_mock.assert_called_once_with(
            ["/usr/bin/git", "rev-parse", "--short", "HEAD"],
            check = True,
            capture_output = True,
            text = True,
        )

    def test_get_version_raises_when_git_is_missing(self, monkeypatch:pytest.MonkeyPatch) -> None:
        """Fail clearly when no explicit hash and no git executable are available."""
        monkeypatch.delenv("GIT_COMMIT_HASH", raising = False)

        with patch("version.shutil.which", return_value = None), pytest.raises(RuntimeError, match = "set GIT_COMMIT_HASH or build from a valid git checkout"):
            version.get_version()

    def test_get_version_raises_when_git_head_is_unavailable(self, monkeypatch:pytest.MonkeyPatch) -> None:
        """Fail clearly when git exists but repository metadata is unavailable."""
        monkeypatch.delenv("GIT_COMMIT_HASH", raising = False)
        called_process_error = getattr(importlib.import_module("subprocess"), "CalledProcessError")

        with patch("version.shutil.which", return_value = "/usr/bin/git"), patch(
            "version.subprocess.run",
            side_effect = called_process_error(128, ["/usr/bin/git", "rev-parse", "--short", "HEAD"]),
        ), pytest.raises(RuntimeError, match = "set GIT_COMMIT_HASH or build from a valid git checkout"):
            version.get_version()
