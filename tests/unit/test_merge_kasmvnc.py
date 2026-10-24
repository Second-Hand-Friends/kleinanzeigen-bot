# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Tests for the trusted, current-head KasmVNC merge gate."""

import json
import subprocess  # noqa: S404
import sys
from pathlib import Path
from typing import Any

import pytest

_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import merge_kasmvnc  # noqa: E402  # pyright: ignore[reportMissingImports]

pytestmark = pytest.mark.unit


@pytest.fixture
def eligible() -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    pr = {
        "number": 42,
        "author": {"is_bot": True, "login": "app/kleinanzeigen-bot-tu"},
        "headRefName": merge_kasmvnc.BRANCH,
        "headRefOid": "a" * 40,
        "baseRefName": "main",
        "isCrossRepository": False,
        "isDraft": False,
        "reviewDecision": "APPROVED",
        "mergeStateStatus": "CLEAN",
        "statusCheckRollup": [
            {"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"},
            {"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SKIPPED"},
            {"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "NEUTRAL"},
            {"__typename": "StatusContext", "state": "SUCCESS"},
        ],
    }
    runs = [{
        "event": "pull_request",
        "head_sha": pr["headRefOid"],
        "head_branch": merge_kasmvnc.BRANCH,
        "head_repository": {"full_name": merge_kasmvnc.REPOSITORY},
        "status": "completed",
        "conclusion": "success",
    }]
    lines = ["@@ -28,5 +28,5 @@"]
    for sign, version, digest in (("-", "1.5.0", "a"), ("+", "1.6.0", "b")):
        lines.append(f"{sign}ARG KASMVNC_VERSION={version}")
        for arch in ("amd64", "arm64"):
            lines.append(f"{sign}ARG KASMVNC_SHA256_{arch.upper()}={digest * 64}")
            url = f"https://github.com/kasmtech/KasmVNC/releases/download/v{version}/kasmvncserver_trixie_{version}_{arch}.deb"
            lines.append(f"{sign}ARG KASMVNC_URL_{arch.upper()}={url}")
    files = [{"filename": merge_kasmvnc.DOCKERFILE, "status": "modified", "patch": "\n".join(lines), "additions": 5, "deletions": 5}]
    return pr, runs, files


@pytest.mark.parametrize("login", ["app/kleinanzeigen-bot-tu", "kleinanzeigen-bot-tu[bot]"])
@pytest.mark.parametrize("line_ending", ["\n", "\r\n"])
def test_accepts_reviewed_pin_update_with_successful_current_head_build(
    eligible:tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]], login:str, line_ending:str,
) -> None:
    eligible[0]["author"]["login"] = login
    eligible[2][0]["patch"] = eligible[2][0]["patch"].replace("\n", line_ending)
    assert merge_kasmvnc.merge_block_reason(*eligible) is None


@pytest.mark.parametrize("separator", ["\r", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"])
def test_rejects_control_characters_hiding_extra_pin_content(
    eligible:tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]], separator:str,
) -> None:
    changed = eligible[2][0]
    changed["patch"] = changed["patch"].replace("+ARG KASMVNC_VERSION=1.6.0", f"+ARG KASMVNC_VERSION=1.6.0{separator}extra")
    assert merge_kasmvnc.merge_block_reason(*eligible) is not None


@pytest.mark.parametrize(("field", "value"), [
    ("head_sha", "b" * 40),
    ("event", "push"),
    ("head_branch", "other"),
    ("head_repository", {"full_name": "someone/fork"}),
    ("status", "in_progress"),
    ("conclusion", "failure"),
    ("conclusion", "skipped"),
    ("conclusion", "cancelled"),
])
def test_rejects_untrusted_or_unsuccessful_build(
    eligible:tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]], field:str, value:Any,
) -> None:
    eligible[1][0][field] = value
    assert merge_kasmvnc.merge_block_reason(*eligible) is not None


def test_uses_latest_build_even_if_previous_build_succeeded(eligible:tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]) -> None:
    eligible[1].insert(0, {**eligible[1][0], "status": "queued", "conclusion": None})
    assert merge_kasmvnc.merge_block_reason(*eligible) is not None
    eligible[1].clear()
    assert merge_kasmvnc.merge_block_reason(*eligible) is not None


@pytest.mark.parametrize(("field", "value"), [
    ("author", {"login": "someone"}),
    ("author", {"login": "kleinanzeigen-bot-tu"}),
    ("author", {"login": "app/someone"}),
    ("headRefName", "other"),
    ("headRefOid", "b" * 40),
    ("headRefOid", ""),
    ("baseRefName", "release"),
    ("isCrossRepository", True),
    ("isDraft", True),
    ("reviewDecision", "REVIEW_REQUIRED"),
    ("reviewDecision", "CHANGES_REQUESTED"),
    ("mergeStateStatus", "BLOCKED"),
    ("statusCheckRollup", []),
    ("statusCheckRollup", [{"__typename": "CheckRun", "status": "IN_PROGRESS", "conclusion": None}]),
    ("statusCheckRollup", [{"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "FAILURE"}]),
    ("statusCheckRollup", [{"__typename": "StatusContext", "state": "PENDING"}]),
    ("statusCheckRollup", [{"__typename": "StatusContext", "state": "FAILURE"}]),
])
def test_rejects_ineligible_pr(
    eligible:tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]], field:str, value:Any,
) -> None:
    eligible[0][field] = value
    assert merge_kasmvnc.merge_block_reason(*eligible) is not None


@pytest.mark.parametrize("change", ["extra-file", "wrong-file", "renamed", "missing-patch", "truncated",
                         "command", "duplicate", "url", "digest", "missing-digest", "downgrade"])
def test_rejects_changes_outside_validated_pins(
    eligible:tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]], change:str,
) -> None:
    files = eligible[2]
    changed = files[0]
    if change == "extra-file":
        files.append({"filename": "scripts/merge_kasmvnc.py"})
    elif change == "wrong-file":
        changed["filename"] = "Dockerfile"
    elif change == "renamed":
        changed["status"] = "renamed"
    elif change == "missing-patch":
        changed.pop("patch")
    elif change == "truncated":
        changed["additions"] += 1
    elif change == "command":
        changed["patch"] += "\n+RUN echo unsafe"
        changed["additions"] += 1
    elif change == "duplicate":
        changed["patch"] += "\n+ARG KASMVNC_VERSION=1.6.0"
        changed["additions"] += 1
    elif change == "url":
        changed["patch"] = changed["patch"].replace("https://github.com/", "https://example.org/")
    elif change == "digest":
        changed["patch"] = changed["patch"].replace("b" * 64, "z" * 64)
    elif change == "missing-digest":
        changed["patch"] = "\n".join(line for line in changed["patch"].splitlines() if "KASMVNC_SHA256_" not in line)
        changed["additions"] -= 2
        changed["deletions"] -= 2
    else:
        changed["patch"] = changed["patch"].replace("1.6.0", "1.4.0")
    assert merge_kasmvnc.merge_block_reason(*eligible) is not None


@pytest.mark.parametrize("ready", [False, True])
def test_cli_merges_only_ready_head_with_app_token(
    eligible:tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]], monkeypatch:pytest.MonkeyPatch, *, ready:bool,
) -> None:
    pr, runs, files = eligible
    if not ready:
        pr["reviewDecision"] = "REVIEW_REQUIRED"
    calls:list[list[str]] = []
    monkeypatch.setenv("GH_TOKEN", "read-token")
    monkeypatch.setenv("GH_MERGE_TOKEN", "merge-token")

    def run(args:list[str], **kwargs:Any) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[1:3] == ["pr", "merge"]:
            assert kwargs["env"]["GH_TOKEN"] == "merge-token"  # noqa: S105 test credential
            assert args == ["gh", "pr", "merge", "42", "--repo", merge_kasmvnc.REPOSITORY, "--squash", "--match-head-commit", pr["headRefOid"]]
            result:Any = None
        else:
            assert kwargs["env"] is None
            if args[1:3] == ["pr", "list"]:
                result = [pr]
            elif "/files?" in args[2]:
                result = files
            else:
                assert f"event=pull_request&head_sha={pr['headRefOid']}&per_page=1" in args[2]
                result = {"workflow_runs": runs}
        return subprocess.CompletedProcess(args, 0, json.dumps(result), "")

    monkeypatch.setattr(subprocess, "run", run)
    merge_kasmvnc.main()
    assert sum(args[1:3] == ["pr", "merge"] for args in calls) == int(ready)


def test_api_failure_is_reported_and_not_a_successful_skip(monkeypatch:pytest.MonkeyPatch, capsys:pytest.CaptureFixture[str]) -> None:
    def fail(*_args:Any, **_kwargs:Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(1, ["gh", "pr", "list"], stderr = "Resource not accessible by integration")

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        merge_kasmvnc.main()
    assert "Resource not accessible by integration" in capsys.readouterr().err
