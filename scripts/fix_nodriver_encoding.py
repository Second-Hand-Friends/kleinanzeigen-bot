# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Fix UTF-8 encoding issues in installed nodriver cdp files.

nodriver 0.50.3 ships ``cdp/network.py`` with an ISO-8859-1 ``±`` character
(byte ``0xb1``) in the comment ``# JSON (±Inf)`` on line 1345, and no encoding
declaration. Python 3.12+ rejects this at import time.

This script patches the installed file to use proper UTF-8 and adds the
required ``# -*- coding: utf-8 -*-`` declaration. The patch is idempotent.

Designed as a PDM ``post_install`` hook. Exits non-zero if the file is found
but does not contain the expected bad pattern.
"""

from __future__ import annotations

import importlib.metadata
import sys
from pathlib import Path


def _locate_target() -> Path | None:
    """Locate the installed ``nodriver/cdp/network.py`` via importlib.metadata."""
    try:
        dist = importlib.metadata.distribution("nodriver")
    except importlib.metadata.PackageNotFoundError:
        return None

    try:
        # Preferred public API
        return Path(dist.locate_file("nodriver/cdp/network.py"))  # type: ignore[arg-type]
    except AttributeError:
        # Python 3.10-3.11 fallback: dist._path → .dist-info dir → site-packages
        site_packages = Path(dist._path).parent  # type: ignore[attr-defined]  # noqa: SLF001
        return site_packages / "nodriver" / "cdp" / "network.py"


def _fix_file(path:Path) -> str:
    """Fix encoding in *path*. Returns ``"fixed"``, ``"already-ok"``, or exits.

    Exits with code 1 if the file is found but does not contain the expected
    bad pattern and is not already valid UTF-8 — this indicates an unexpected
    upstream change that needs human review.
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        print(
            f"fix_nodriver_encoding: cannot read {path}: {exc}",
            file = sys.stderr,
        )
        sys.exit(1)

    # Known bad pattern in nodriver 0.50.3:
    #   ``b"    #: JSON (\xb1Inf).\r\n"`` (line 1345)
    # We match the specific context ``(\xb1Inf)`` to avoid false positives.
    bad_pattern = b"(\xb1Inf)"

    if bad_pattern not in raw:
        # Pattern not found — verify the file is already valid UTF-8
        try:
            raw.decode("utf-8")
            return "already-ok"
        except UnicodeDecodeError as exc:
            print(
                f"fix_nodriver_encoding: {path}: file does not contain the "
                f"expected bad pattern and is not valid UTF-8 ({exc}). "
                f"Upstream may have changed — review needed.",
                file = sys.stderr,
            )
            sys.exit(1)

    # Patch: replace the specific bad byte sequence with proper UTF-8
    fixed = raw.replace(bad_pattern, b"(\xc2\xb1Inf)")

    # Add encoding declaration if not present
    if not fixed.startswith(b"# -*- coding: utf-8 -*-"):
        fixed = b"# -*- coding: utf-8 -*-\n" + fixed

    # Normalize line endings (upstream uses CRLF in this file)
    fixed = fixed.replace(b"\r\n", b"\n")

    # Validate the result is valid UTF-8
    try:
        fixed.decode("utf-8")
    except UnicodeDecodeError as exc:
        print(
            f"fix_nodriver_encoding: {path}: patched file is not valid UTF-8: {exc}",
            file = sys.stderr,
        )
        sys.exit(1)

    path.write_bytes(fixed)
    return "fixed"


def main() -> int:
    target = _locate_target()
    if target is None:
        print("fix_nodriver_encoding: nodriver not installed, skipping", file = sys.stderr)
        return 0

    if not target.is_file():
        print(f"fix_nodriver_encoding: {target} not found, skipping", file = sys.stderr)
        return 0

    result = _fix_file(target)
    print(f"fix_nodriver_encoding: {target} -> {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
