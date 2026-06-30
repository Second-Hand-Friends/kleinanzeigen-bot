# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Post-install fixes for installed nodriver.

1. **Encoding fix** — ``cdp/network.py`` contains a lone ISO-8859-1 ``±``
   byte (0xb1) without an encoding declaration. Python 3.12+ rejects this
   at import time. The fix adds a ``# -*- coding: utf-8 -*-`` header and
   replaces the byte with proper UTF-8.

2. **Flat-mode session retry** — nodriver 0.50.3+ uses flat-mode CDP
   (browser-level WebSocket + per-target ``sessionId``). Chromium 148+
   rejects ``DOM.enable`` when sent to the browser target without a
   ``sessionId``, and rejects stale-session commands with ``-32601``.
   Wraps ``Connection.send`` to re-attach and retry once on that code,
   switching ``_attach=True`` commands to include ``sessionId``.

Designed as a PDM ``post_install`` hook. All patches are idempotent.
"""

from __future__ import annotations

import importlib.metadata
import sys
import textwrap
from pathlib import Path


def _locate_file(relative:str) -> Path | None:
    """Locate an installed nodriver source file via importlib.metadata."""
    try:
        dist = importlib.metadata.distribution("nodriver")
    except importlib.metadata.PackageNotFoundError:
        return None
    try:
        return Path(dist.locate_file(relative))  # type: ignore[arg-type]
    except AttributeError:
        site_packages = Path(dist._path).parent  # type: ignore[attr-defined]  # noqa: SLF001
        return site_packages / relative


# ── network.py encoding fix ────────────────────────────────────────────────

def _fix_network_encoding(path:Path) -> str:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        print(f"fix_nodriver: cannot read {path}: {exc}", file = sys.stderr)
        sys.exit(1)

    bad = b"(\xb1Inf)"
    if bad not in raw:
        try:
            raw.decode("utf-8")
            return "already-ok"
        except UnicodeDecodeError as exc:
            print(
                f"fix_nodriver: {path}: unexpected content: {exc}",
                file = sys.stderr,
            )
            sys.exit(1)

    fixed = raw.replace(bad, b"(\xc2\xb1Inf)")
    if not fixed.startswith(b"# -*- coding: utf-8 -*-"):
        fixed = b"# -*- coding: utf-8 -*-\n" + fixed
    fixed = fixed.replace(b"\r\n", b"\n")
    try:
        fixed.decode("utf-8")
    except UnicodeDecodeError as exc:
        print(f"fix_nodriver: {path}: invalid UTF-8: {exc}", file = sys.stderr)
        sys.exit(1)
    path.write_bytes(fixed)
    return "fixed"


# ── connection.py flat-mode session retry ──────────────────────────────────

# Keep this marker and guidance in sync with the CLI runtime guard in
# src/kleinanzeigen_bot/cli.py and the README source-install caveat.
_CONNECTION_SEND_PATCH_MARKER = "KLEINANZEIGEN_BOT_NODEDRIVER_CDP_REATTACH_PATCH_V1"

_SEND_START = "        if not _attach:"
_SEND_ERR_END = "            raise exception"

_MARKER_COMMENT_LINE = f"        # {_CONNECTION_SEND_PATCH_MARKER}: re-attach/retry CDP -32601 for Chromium flat-mode sessions."

_NEW_SEND = f"""\
        method, *params = next(cdp_obj).values()
        if params:
            params = params.pop()

{_MARKER_COMMENT_LINE}
        for _retry in range(2):
            if not _attach:
                if not self.attached or not self.socket:
                    await self.attach()

            _id = next(self.__count__)
            message = {{"method": method, "params": params, "id": _id}}
            if not _attach:
                message["sessionId"] = self.session_id
            message.update(kwargs)

            tx = Transaction(message)
            self.transactions.append(tx)
            self._tx_by_id[_id] = tx

            future = asyncio.get_running_loop().create_future()
            self._mapper[_id] = future

            while len(self.transactions) > 25:
                removed_tx = self.transactions.pop(0)
                if removed_tx.id is not None:
                    self._tx_by_id.pop(removed_tx.id, None)

            ws = self.socket
            if ws is None:
                raise RuntimeError("WebSocket is not connected")

            async with self.lock:
                try:
                    await ws.send(json.dumps(message))
                except Exception:
                    self._mapper.pop(_id, None)
                    if not future.done():
                        future.cancel()
                    raise

            try:
                response_message = await future
            finally:
                self._mapper.pop(_id, None)

            if "error" in response_message:
                if _retry == 0 and response_message["error"].get("code") == -32601:
                    # Chromium 148+: switch from browser-level to page-target
                    # routing when the command is rejected without sessionId.
                    _attach = False
                    await self.attach()
                    continue
                exception = ProtocolException(response_message["error"])
                tx.result = exception
                raise exception
            break"""

# Derive the old markerless body from _NEW_SEND for legacy normalization.
_OLD_SEND = _NEW_SEND.replace(f"{_MARKER_COMMENT_LINE}\n", "")


def _fix_connection_send(path:Path) -> str:
    try:
        text = path.read_text("utf-8")
    except OSError as exc:
        print(f"fix_nodriver: cannot read {path}: {exc}", file = sys.stderr)
        sys.exit(1)

    # Marker-based idempotency check.
    if _CONNECTION_SEND_PATCH_MARKER in text:
        return "already-ok"

    # Check for old markerless patched body (marker absent, _SEND_START may be
    # gone because the original patch replaced it).  _OLD_SEND uses 8-space
    # template indent matching nodriver's coding style.
    if _OLD_SEND in text:
        # Exact old markerless body found — normalize by replacing with _NEW_SEND.
        text = text.replace(_OLD_SEND, _NEW_SEND)
        path.write_text(text, "utf-8")
        return "updated"

    # Fresh patch: find the original unpatched send-method body.
    start = text.find(_SEND_START)
    if start == -1:
        print("fix_nodriver: send start marker not found", file = sys.stderr)
        sys.exit(1)

    indent = text[text.rfind("\n", 0, start) + 1: start]
    err = text.find(_SEND_ERR_END, start)
    if err == -1:
        print("fix_nodriver: send error end not found", file = sys.stderr)
        sys.exit(1)

    err_end = text.index("\n", err)
    body = textwrap.indent(_NEW_SEND, indent).removeprefix("\n")
    result = text[:start] + body + text[err_end:]
    path.write_text(result, "utf-8")
    return "fixed"


# ── main ───────────────────────────────────────────────────────────────────

def main() -> int:
    for rel, fix in [
        ("nodriver/cdp/network.py", _fix_network_encoding),
        ("nodriver/core/connection.py", _fix_connection_send),
    ]:
        path = _locate_file(rel)
        if path is None:
            print(f"fix_nodriver: nodriver not installed, cannot patch {rel}", file = sys.stderr)
            return 1
        if not path.is_file():
            print(f"fix_nodriver: {path} not found, cannot patch", file = sys.stderr)
            return 1
        print(f"fix_nodriver: {path} -> {fix(path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
