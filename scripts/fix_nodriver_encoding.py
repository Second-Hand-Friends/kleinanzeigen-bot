# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Post-install fixes for installed nodriver that upstream does not accept.

1. **Encoding fix** — ``cdp/network.py`` ISO-8859-1 ``±`` byte without
   encoding declaration → add UTF-8 header + fix the byte.

2. **Send retry** — nodriver 0.50.3+ flat-mode ``sessionId`` can go stale
   after complex navigations (category flow). Edge 148 rejects stale-session
   commands with ``-32601``. Wraps ``send`` to re-attach and retry once.

3. **DOM.enable routing** — ``tab`` methods call ``send(cdp.dom.enable(),
   _attach=True)``, which sends the command at browser level without
   ``sessionId``. Edge 148 does not accept ``DOM.enable`` on the browser
   target. Removes the ``_attach`` flag so the command reaches the page
   target (with ``sessionId``).

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
        print(f"fix_nodriver_encoding: cannot read {path}: {exc}", file = sys.stderr)
        sys.exit(1)
    bad = b"(\xb1Inf)"
    if bad not in raw:
        try:
            raw.decode("utf-8")
            return "already-ok"
        except UnicodeDecodeError:
            print(f"fix_nodriver_encoding: {path}: unexpected content", file = sys.stderr)
            sys.exit(1)
    fixed = raw.replace(bad, b"(\xc2\xb1Inf)")
    if not fixed.startswith(b"# -*- coding: utf-8 -*-"):
        fixed = b"# -*- coding: utf-8 -*-\n" + fixed
    fixed = fixed.replace(b"\r\n", b"\n")
    try:
        fixed.decode("utf-8")
    except UnicodeDecodeError as exc:
        print(f"fix_nodriver_encoding: {path}: invalid UTF-8 after patch: {exc}", file = sys.stderr)
        sys.exit(1)
    path.write_bytes(fixed)
    return "fixed"


# ── connection.py send retry on stale session ──────────────────────────────

_SEND_START = "        if not _attach:"
_SEND_ERR_END = "            raise exception"

_NEW_SEND = """\
        method, *params = next(cdp_obj).values()
        if params:
            params = params.pop()

        for _retry in range(2):
            if not _attach:
                if not self.attached or not self.socket:
                    await self.attach()

            _id = next(self.__count__)
            message = {"method": method, "params": params, "id": _id}
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
                    # If the command was sent without sessionId (_attach=True),
                    # re-attach and retry WITH sessionId so it reaches the page.
                    _attach = False
                    await self.attach()
                    continue
                exception = ProtocolException(response_message["error"])
                tx.result = exception
                raise exception
            break"""


def _fix_connection_send(path:Path) -> str:
    try:
        text = path.read_text("utf-8")
    except OSError as exc:
        print(f"fix_nodriver_encoding: cannot read {path}: {exc}", file = sys.stderr)
        sys.exit(1)
    if "for _retry in range(2):" in text:
        return "already-ok"
    start = text.find(_SEND_START)
    if start == -1:
        print("fix_nodriver_encoding: send start marker not found", file = sys.stderr)
        sys.exit(1)
    err = text.find(_SEND_ERR_END, start)
    if err == -1:
        print("fix_nodriver_encoding: send error end marker not found", file = sys.stderr)
        sys.exit(1)
    err_end = text.index("\n", err)
    indent = text[text.rfind("\n", 0, start) + 1: start]
    new_body = textwrap.indent(_NEW_SEND, indent).removeprefix("\n")
    result = text[:start] + new_body + text[err_end:]
    path.write_text(result, "utf-8")
    return "fixed"


# ── tab.py: remove _attach flag from DOM.enable/disable ────────────────────

_TAB_PATCHES = [
    ("self.send(cdp.dom.enable(), True)", "self.send(cdp.dom.enable())"),
    ("self.send(cdp.dom.disable(), True)", "self.send(cdp.dom.disable())"),
]


def _fix_tab_dom_calls(path:Path) -> str:
    try:
        text = path.read_text("utf-8")
    except OSError as exc:
        print(f"fix_nodriver_encoding: cannot read {path}: {exc}", file = sys.stderr)
        sys.exit(1)
    changed = False
    for old, new in _TAB_PATCHES:
        # Only patch the first occurrence (find_all/find methods); the others
        # (in query_selector etc.) don't have the _attach flag.
        if old in text:
            text = text.replace(old, new, 1)
            changed = True
    return "fixed" if changed else "already-ok"


# ── main ───────────────────────────────────────────────────────────────────

def main() -> int:
    patches = [
        ("nodriver/cdp/network.py", _fix_network_encoding),
        ("nodriver/core/connection.py", _fix_connection_send),
        ("nodriver/core/tab.py", _fix_tab_dom_calls),
    ]
    for rel, fix in patches:
        p = _locate_file(rel)
        if p and p.is_file():
            print(f"fix_nodriver_encoding: {p} -> {fix(p)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
