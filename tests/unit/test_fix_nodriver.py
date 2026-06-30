# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Unit tests for scripts/fix_nodriver.py _fix_connection_send.

Exercised on temporary files to avoid touching the installed nodriver.

Note: ``fix_nodriver`` is loaded by file path via ``importlib.util``
to avoid a mypy double-module mapping (*fix_nodriver* vs *scripts.fix_nodriver*)
that occurs when importing via ``from scripts import fix_nodriver``.
"""

from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parent.parent.parent / "scripts" / "fix_nodriver.py"
_spec = importlib.util.spec_from_file_location("fix_nodriver", _SCRIPT_PATH)
assert _spec is not None, f"Cannot load spec from {_SCRIPT_PATH}"
assert _spec.loader is not None, f"No loader for spec from {_SCRIPT_PATH}"
fix_nodriver = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fix_nodriver)

pytestmark = pytest.mark.unit


# ── helpers ──────────────────────────────────────────────────────────────────

_SEND_START_LINE = "        if not _attach:"

_ORIG_BODY_AFTER_START = """\
            if not self.attached or not self.socket:
                await self.attach()

        _id = next(self.__count__)
        message = {"method": method, "params": params, "id": _id}
        if not _attach:
            message["sessionId"] = self.session_id

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
            exception = ProtocolException(response_message["error"])
            tx.result = exception
            raise exception"""

_ORIG_SEND_METHOD = (
    "    async def send(self, cdp_obj, **kwargs):\n"
    f"{_SEND_START_LINE}\n"
    f"{_ORIG_BODY_AFTER_START}\n"
    "        return response_message"
)

_UNRELATED_RETRY_BLOCK = (
    "# Some other function that happens to have for _retry in range(2):\n"
    "for _retry in range(2):\n"
    "    pass\n"
)


def _make_connection_py(method_body:str, extra:str = "") -> str:
    """Build a fake connection.py with a given method body."""
    lines = [
        "import json",
        "import asyncio",
        "",
        "class Connection:",
        method_body,
        "",
        "class SomethingElse:",
        "    pass",
    ]
    if extra:
        lines.extend(["", extra])
    return "\n".join(lines) + "\n"


def _apply_fix(method_body:str, extra:str = "") -> tuple[str, str]:
    """Write content to a temp file, run _fix_connection_send, return (text, status)."""
    content = _make_connection_py(method_body, extra)
    with tempfile.NamedTemporaryFile(mode = "w", suffix = ".py", delete = False, encoding = "utf-8") as f:
        f.write(content)
        tmpname = f.name
    try:
        status = fix_nodriver._fix_connection_send(Path(tmpname))  # noqa: SLF001
        return Path(tmpname).read_text("utf-8"), status
    finally:
        Path(tmpname).unlink(missing_ok = True)


def _markerless_send_method() -> str:
    """Build a send method body that uses the old markerless patch."""
    return (
        "    async def send(self, cdp_obj, **kwargs):\n"
        f"{fix_nodriver._OLD_SEND.rstrip(chr(10))}\n"  # noqa: SLF001
        "        return response_message"
    )


def _patched_send_method() -> str:
    """Build a send method body that uses the new patch (with marker)."""
    return (
        "    async def send(self, cdp_obj, **kwargs):\n"
        f"{fix_nodriver._NEW_SEND.rstrip(chr(10))}\n"  # noqa: SLF001
        "        return response_message"
    )


# ── tests ────────────────────────────────────────────────────────────────────


class TestFixConnectionSend:
    """Tests for _fix_connection_send on temporary files."""

    def test_fresh_patch_includes_marker(self) -> None:
        """Fresh patch inserts the marker comment."""
        text, status = _apply_fix(_ORIG_SEND_METHOD)
        assert status == "fixed"
        assert fix_nodriver._CONNECTION_SEND_PATCH_MARKER in text  # noqa: SLF001

    def test_marker_present_is_idempotent(self) -> None:
        """Marker present → already-ok, no changes."""
        text, status = _apply_fix(_patched_send_method())
        assert status == "already-ok"
        # Marker still present.
        assert fix_nodriver._CONNECTION_SEND_PATCH_MARKER in text  # noqa: SLF001

    def test_legacy_markerless_body_normalized(self) -> None:
        """Exact old markerless body gets normalized to _NEW_SEND."""
        old_markerless = fix_nodriver._OLD_SEND  # noqa: SLF001
        # Sanity: old body does NOT contain the marker.
        assert fix_nodriver._CONNECTION_SEND_PATCH_MARKER not in old_markerless  # noqa: SLF001
        text, status = _apply_fix(_markerless_send_method())
        assert status == "updated", f"Expected 'updated', got '{status}'"
        # Now contains marker.
        assert fix_nodriver._CONNECTION_SEND_PATCH_MARKER in text  # noqa: SLF001

    def test_unrelated_retry_not_normalized(self) -> None:
        """Arbitrary 'for _retry in range(2):' outside the patch body is NOT normalized."""
        text, status = _apply_fix(_ORIG_SEND_METHOD, _UNRELATED_RETRY_BLOCK)
        assert status == "fixed"
        assert fix_nodriver._CONNECTION_SEND_PATCH_MARKER in text  # noqa: SLF001
        # The unrelated retry block is still present unchanged AND lacks the marker.
        marker_prefix = f"# {fix_nodriver._CONNECTION_SEND_PATCH_MARKER}"  # noqa: SLF001
        # Find all occurrences of "for _retry in range(2):" and verify the
        # one from the unrelated block is NOT preceded by the marker comment.
        occ_count = text.count("for _retry in range(2):")
        assert occ_count >= 2, (
            f"Expected at least 2 occurrences (patch + unrelated), got {occ_count}"
        )
        # The unrelated block comes after the patched send method,
        # so its "for _retry in range(2):" is NOT preceded by the marker.
        # Verify by checking that the LAST occurrence lacks the marker prefix.
        last_retry_pos = text.rfind("for _retry in range(2):")
        preceding = text[max(0, last_retry_pos - 80):last_retry_pos]
        assert marker_prefix not in preceding, (
            "Unrelated retry loop should NOT have the marker comment before it"
        )
