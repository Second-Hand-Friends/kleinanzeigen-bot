# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Merge only reviewed KasmVNC pin updates with a successful current-head PR Build."""

import json
import os
import re
import subprocess  # noqa: S404
import sys
from typing import Any

REPOSITORY = "Second-Hand-Friends/kleinanzeigen-bot"
BRANCH = "chore/update-kasmvnc"
BASE = "main"
AUTHOR = "app/kleinanzeigen-bot-tu"
DOCKERFILE = "docker/image/Dockerfile"
_VERSION = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"


def _pins_only(files:list[dict[str, Any]]) -> bool:
    changed = files[0] if len(files) == 1 else {}
    if changed.get("filename") != DOCKERFILE or changed.get("status") != "modified":
        return False
    patch = changed.get("patch")
    if not isinstance(patch, str) or not patch:
        return False
    pins:dict[str, dict[str, str]] = {"+": {}, "-": {}}
    counts = {"+": 0, "-": 0}
    for line in patch.splitlines():
        if not line.startswith(("+", "-")):
            continue
        sign = line[0]
        match = re.fullmatch(r"[+-]ARG (KASMVNC_(?:VERSION|SHA256_(?:AMD64|ARM64)|URL_(?:AMD64|ARM64)))=(\S+)", line)
        if match is None or match[1] in pins[sign]:
            return False
        name, value = match.groups()
        pins[sign][name] = value
        counts[sign] += 1
    if counts["+"] != changed.get("additions") or counts["-"] != changed.get("deletions") or pins["+"].keys() != pins["-"].keys():
        return False
    for values in pins.values():
        version = values.get("KASMVNC_VERSION", "")
        if not re.fullmatch(_VERSION, version):
            return False
        for arch in ("AMD64", "ARM64"):
            digest = values.get(f"KASMVNC_SHA256_{arch}", "")
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                return False
            url = values.get(f"KASMVNC_URL_{arch}", "")
            expected = rf"https://github\.com/kasmtech/KasmVNC/releases/download/v{re.escape(version)}/"
            expected += rf"kasmvncserver_(?:debian_)?trixie_{re.escape(version)}_{arch.lower()}\.deb"
            if not re.fullmatch(expected, url):
                return False
    return tuple(map(int, pins["+"]["KASMVNC_VERSION"].split("."))) > tuple(map(int, pins["-"]["KASMVNC_VERSION"].split(".")))


def merge_block_reason(pr:dict[str, Any], runs:list[dict[str, Any]], files:list[dict[str, Any]]) -> str | None:
    """Fail closed on identity, scope, reviews, checks, and the latest PR Build."""
    if (pr.get("author", {}).get("login") != AUTHOR or pr.get("headRefName") != BRANCH
            or pr.get("baseRefName") != BASE or pr.get("isCrossRepository") is not False or pr.get("isDraft") is not False):
        return "PR identity or branch is not eligible"
    if not _pins_only(files):
        return "PR changes are not limited to valid KasmVNC pin updates"
    if pr.get("reviewDecision") != "APPROVED" or pr.get("mergeStateStatus") != "CLEAN":
        return "PR is awaiting approval or a clean merge state"
    checks = pr.get("statusCheckRollup")
    if not isinstance(checks, list) or not checks:
        return "PR checks are missing"
    for check in checks:
        if check.get("__typename") == "CheckRun":
            if check.get("status") != "COMPLETED" or check.get("conclusion") not in {"SUCCESS", "SKIPPED", "NEUTRAL"}:
                return "PR checks are pending or unsuccessful"
        elif check.get("__typename") != "StatusContext" or check.get("state") != "SUCCESS":
            return "PR checks are pending or unsuccessful"
    head = pr.get("headRefOid", "")
    run = runs[0] if runs else {}
    valid_head = isinstance(head, str) and bool(re.fullmatch(r"[0-9a-f]{40}", head))
    expected = {"event": "pull_request", "head_sha": head, "head_branch": BRANCH, "status": "completed", "conclusion": "success"}
    if (not valid_head or any(run.get(key) != value for key, value in expected.items())
            or run.get("head_repository", {}).get("full_name") != REPOSITORY):
        return "Current-head PR Build is pending or unsuccessful"
    return None


def _gh(*args:str, token:str | None = None) -> str:
    try:
        return subprocess.run(  # noqa: S603 trusted CLI and argument arrays; never execute PR content
            ["gh", *args], check = True, capture_output = True, text = True, timeout = 60,  # noqa: S607 trusted CI PATH
            env = None if token is None else {**os.environ, "GH_TOKEN": token},
        ).stdout
    except subprocess.CalledProcessError as error:
        print(error.stderr or str(error), file = sys.stderr)
        raise


def main() -> None:
    fields = "number,author,headRefOid,headRefName,baseRefName,isCrossRepository,isDraft,reviewDecision,mergeStateStatus,statusCheckRollup"
    prs = json.loads(_gh("pr", "list", "--repo", REPOSITORY, "--head", BRANCH, "--base", BASE, "--state", "open", "--json", fields))
    if len(prs) != 1:
        print("KasmVNC merge skipped: no unique open update PR")
        return
    pr = prs[0]
    number = str(pr["number"])
    head = pr["headRefOid"]
    files = json.loads(_gh("api", f"repos/{REPOSITORY}/pulls/{number}/files?per_page=100"))
    runs = json.loads(_gh("api", f"repos/{REPOSITORY}/actions/workflows/build.yml/runs?event=pull_request&head_sha={head}&per_page=1"))
    reason = merge_block_reason(pr, runs["workflow_runs"], files)
    if reason:
        print(f"KasmVNC merge skipped: {reason}")
        return
    # Native auto-merge can remain armed after the updater replaces the tested head.
    _gh("pr", "merge", number, "--repo", REPOSITORY, "--squash", "--match-head-commit", head, token = os.environ["GH_MERGE_TOKEN"])
    print(f"Merged KasmVNC update PR #{number} at {head}")


if __name__ == "__main__":
    main()
