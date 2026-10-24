# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Update Docker's KasmVNC pins from a GitHub release API response."""

import argparse
import json
import re
from pathlib import Path
from typing import Any


def _version(value:str) -> tuple[int, ...]:
    if not re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", value):
        raise ValueError(f"Invalid stable KasmVNC version: {value!r}")
    return tuple(int(part) for part in value.split("."))


def _release_pins(release:dict[str, Any]) -> dict[str, str]:
    tag = release.get("tag_name")
    if not isinstance(tag, str) or not tag.startswith("v"):
        raise ValueError("Release tag must be vX.Y.Z")
    version = tag[1:]
    _version(version)
    if release.get("draft") is not False or release.get("prerelease") is not False:
        raise ValueError("Only published stable releases are supported")
    assets = release.get("assets")
    if not isinstance(assets, list) or any(not isinstance(asset, dict) for asset in assets):
        raise ValueError("Release assets must be a list of objects")

    pins = {"KASMVNC_VERSION": version}
    for arch in ("amd64", "arm64"):
        pattern = rf"kasmvncserver_(?:debian_)?trixie_{re.escape(version)}_{arch}\.deb"
        matches = [asset for asset in assets if isinstance(asset.get("name"), str) and re.fullmatch(pattern, asset["name"])]
        if len(matches) != 1:
            raise ValueError(f"Expected exactly one trixie {arch} asset, found {len(matches)}")
        asset = matches[0]
        digest = asset.get("digest")
        if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
            raise ValueError(f"Invalid SHA-256 digest for {arch}")
        url = f"https://github.com/kasmtech/KasmVNC/releases/download/{tag}/{asset['name']}"
        if asset.get("browser_download_url") != url or asset.get("state") != "uploaded":
            raise ValueError(f"Invalid download URL or upload state for {arch}")
        pins[f"KASMVNC_SHA256_{arch.upper()}"] = digest.removeprefix("sha256:").lower()
        pins[f"KASMVNC_URL_{arch.upper()}"] = url
    return pins


def update_dockerfile(text:str, release:dict[str, Any]) -> str:
    """Validate all release pins before replacing exactly one of each Docker ARG."""
    pins = _release_pins(release)
    declarations:dict[str, re.Match[str]] = {}
    for name in pins:
        lines = list(re.finditer(rf"^[ \t]*ARG[ \t]+{name}\b[^\r\n]*", text, re.MULTILINE))
        if len(lines) != 1:
            raise ValueError(f"Expected exactly one ARG {name}, found {len(lines)}")
        match = re.fullmatch(rf"([ \t]*ARG[ \t]+{name}[ \t]*=[ \t]*)(\S+)([ \t]*)", lines[0][0])
        if match is None:
            raise ValueError(f"Invalid ARG {name}")
        declarations[name] = match

    current = declarations["KASMVNC_VERSION"][2]
    if _version(pins["KASMVNC_VERSION"]) < _version(current):
        raise ValueError(f"Refusing KasmVNC downgrade from {current} to {pins['KASMVNC_VERSION']}")
    if pins["KASMVNC_VERSION"] == current:
        if any(declarations[name][2] != value for name, value in pins.items()):
            raise ValueError("Same-version release assets changed; refusing to replace existing pins")
        return text

    for name, value in pins.items():
        match = declarations[name]
        text = re.sub(rf"^{re.escape(match[0])}(?=\r?$)", f"{match[1]}{value}{match[3]}", text, count = 1, flags = re.MULTILINE)
    return text


def main() -> None:
    parser = argparse.ArgumentParser(description = __doc__)
    parser.add_argument("release_json", type = Path)
    parser.add_argument("dockerfile", type = Path, nargs = "?", default = Path("docker/image/Dockerfile"))
    args = parser.parse_args()
    try:
        release = json.loads(args.release_json.read_text(encoding = "utf-8"))
        if not isinstance(release, dict):
            raise ValueError("Release JSON must be an object")
        original = args.dockerfile.read_bytes().decode("utf-8")
        updated = update_dockerfile(original, release)
        if updated != original:
            args.dockerfile.write_bytes(updated.encode("utf-8"))
    except (OSError, ValueError) as error:
        parser.exit(1, f"KasmVNC update failed: {error}\n")
    print(release["tag_name"][1:])


if __name__ == "__main__":
    main()
