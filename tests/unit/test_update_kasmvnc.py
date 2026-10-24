# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Tests for validated, repeatable KasmVNC Docker pin updates."""

import json
import subprocess  # noqa: S404
import sys
from pathlib import Path
from typing import Any

import pytest

_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from update_kasmvnc import update_dockerfile  # noqa: E402  # pyright: ignore[reportMissingImports]

pytestmark = pytest.mark.unit


def release(version:str, prefix:str = "kasmvncserver_trixie") -> dict[str, Any]:
    return {
        "tag_name": f"v{version}",
        "draft": False,
        "prerelease": False,
        "assets": [
            {
                "name": f"{prefix}_{version}_{arch}.deb",
                "digest": f"sha256:{digest * 64}",
                "browser_download_url": f"https://github.com/kasmtech/KasmVNC/releases/download/v{version}/{prefix}_{version}_{arch}.deb",
                "state": "uploaded",
            }
            for arch, digest in (("amd64", "a"), ("arm64", "b"))
        ],
    }


def dockerfile(metadata:dict[str, Any]) -> str:
    version = metadata["tag_name"][1:]
    lines = ["# Keep this comment", "FROM debian:trixie", f"ARG KASMVNC_VERSION={version}"]
    for arch, asset in zip(("AMD64", "ARM64"), metadata["assets"], strict = True):
        lines.extend((f"ARG KASMVNC_SHA256_{arch}={asset['digest'][7:]}", f"ARG KASMVNC_URL_{arch}={asset['browser_download_url']}"))
    return "\n".join([*lines, "RUN echo unchanged", ""])


def test_successive_releases_and_idempotence_preserve_other_content() -> None:
    original = dockerfile(release("1.5.0"))
    first_release = release("1.5.2", "kasmvncserver_debian_trixie")
    first = update_dockerfile(original, first_release)
    assert first == dockerfile(first_release)
    assert update_dockerfile(first, first_release) == first
    next_release = release("1.10.0")
    latest = update_dockerfile(first, next_release)
    assert latest == dockerfile(next_release)
    assert update_dockerfile(latest, next_release) == latest


@pytest.mark.parametrize(("field", "value", "message"), [
    ("tag_name", "v1.6.0-rc1", "Invalid stable"),
    ("tag_name", "1.6.0", "Release tag"),
    ("tag_name", "v1.6.0\nRUN evil", "Invalid stable"),
    ("draft", True, "Only published"),
    ("prerelease", True, "Only published"),
    ("assets", None, "Release assets"),
    ("assets", [None], "Release assets"),
])
def test_rejects_invalid_release(field:str, value:Any, message:str) -> None:
    metadata = release("1.6.0")
    metadata[field] = value
    with pytest.raises(ValueError, match = message):
        update_dockerfile(dockerfile(release("1.5.0")), metadata)


@pytest.mark.parametrize(("field", "value"), [
    ("digest", None),
    ("digest", "sha256:abc"),
    ("digest", "sha512:" + "a" * 64),
    ("digest", "sha256:" + "g" * 64),
    ("browser_download_url", "https://example.org/package.deb"),
    ("browser_download_url", "https://github.com/kasmtech/KasmVNC/releases/download/v1.5.0/kasmvncserver_trixie_1.6.0_amd64.deb"),
    ("browser_download_url", "https://github.com/kasmtech/KasmVNC/releases/download/v1.6.0/kasmvncserver_trixie_1.6.0_amd64.deb\nRUN evil"),
    ("state", "new"),
    ("name", "kasmvncserver_bookworm_1.6.0_amd64.deb"),
    ("name", "other_trixie_1.6.0_amd64.deb"),
    ("name", "kasmvncserver_trixie_1.5.0_amd64.deb"),
])
def test_rejects_invalid_assets(field:str, value:Any) -> None:
    metadata = release("1.6.0")
    metadata["assets"][0][field] = value
    with pytest.raises(ValueError, match = "Invalid SHA-256|Invalid download URL|Expected exactly one"):
        update_dockerfile(dockerfile(release("1.5.0")), metadata)


@pytest.mark.parametrize("change", ["missing", "duplicate", "both-prefixes"])
def test_requires_exactly_one_asset_per_architecture(change:str) -> None:
    metadata = release("1.6.0")
    if change == "missing":
        metadata["assets"].pop()
    elif change == "duplicate":
        metadata["assets"].append(metadata["assets"][0])
    else:
        metadata["assets"].append(release("1.6.0", "kasmvncserver_debian_trixie")["assets"][0])
    with pytest.raises(ValueError, match = "exactly one"):
        update_dockerfile(dockerfile(release("1.5.0")), metadata)


def test_rejects_downgrade() -> None:
    with pytest.raises(ValueError, match = "downgrade"):
        update_dockerfile(dockerfile(release("1.10.0")), release("1.9.0"))


@pytest.mark.parametrize("changed_field", ["digest", "name"])
def test_rejects_changed_pins_at_same_version(changed_field:str) -> None:
    original = dockerfile(release("1.5.0"))
    metadata = release("1.5.0")
    if changed_field == "digest":
        metadata["assets"][0]["digest"] = "sha256:" + "c" * 64
    else:
        metadata = release("1.5.0", "kasmvncserver_debian_trixie")
    with pytest.raises(ValueError, match = "Same-version"):
        update_dockerfile(original, metadata)


@pytest.mark.parametrize("name", ["KASMVNC_VERSION", "KASMVNC_SHA256_AMD64", "KASMVNC_SHA256_ARM64", "KASMVNC_URL_AMD64", "KASMVNC_URL_ARM64"])
@pytest.mark.parametrize("duplicate", [False, True])
def test_requires_exactly_one_of_every_arg(name:str, duplicate:bool) -> None:
    original = dockerfile(release("1.5.0"))
    line = next(line for line in original.splitlines(keepends = True) if line.startswith(f"ARG {name}="))
    invalid = original + line if duplicate else original.replace(line, "")
    with pytest.raises(ValueError, match = "exactly one ARG"):
        update_dockerfile(invalid, release("1.6.0"))


@pytest.mark.parametrize("valid", [False, True])
def test_cli_validates_before_writing_and_prints_only_version(tmp_path:Path, valid:bool) -> None:
    path = tmp_path / "Dockerfile"
    original = dockerfile(release("1.5.0")).replace("\n", "\r\n").encode()
    path.write_bytes(original)
    metadata = release("1.6.0")
    if not valid:
        metadata["assets"][1]["digest"] = None
    metadata_path = tmp_path / "release.json"
    metadata_path.write_text(json.dumps(metadata), encoding = "utf-8")
    result = subprocess.run(  # noqa: S603 trusted script and temporary file paths
        [sys.executable, str(Path(_SCRIPTS_DIR) / "update_kasmvnc.py"), str(metadata_path), str(path)],
        capture_output = True, text = True, check = False, timeout = 10,
    )
    if valid:
        assert result.returncode == 0
        assert result.stdout == "1.6.0\n"
        assert not result.stderr
        assert path.read_bytes() == dockerfile(metadata).replace("\n", "\r\n").encode()
    else:
        assert result.returncode != 0
        assert not result.stdout
        assert path.read_bytes() == original
