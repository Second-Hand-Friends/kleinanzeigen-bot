# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Ad status computation and display for the ``status`` CLI command.
This module owns status label mapping, row building, and ASCII table rendering.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime  # noqa: TC003 — used in runtime type annotations
from gettext import gettext as _
from typing import TYPE_CHECKING, Any

import colorama

from . import ad_loading

if TYPE_CHECKING:
    from .model.ad_model import Ad


@dataclass(frozen = True, slots = True)
class StatusRow:
    """One row in the status table. Rendered by :func:`render_status_rows`."""

    title:str  # ad.title
    ad_id:str  # "-" if None, else str(ad.id)
    status:str  # One of: "disabled", "draft", "changed", "due", "published-local"


def _translate_status(status:str) -> str:
    """Return the translated display label for a status string."""
    if status == "disabled":
        return _("disabled")
    if status == "draft":
        return _("draft")
    if status == "changed":
        return _("changed")
    if status == "due":
        return _("due")
    if status == "published-local":
        return _("published-local")
    return status


# Canonical status ordering (matches precedence in :func:`compute_ad_status`).
_STATUS_ORDER:tuple[str, ...] = ("disabled", "draft", "changed", "due", "published-local")

# Status → ANSI colour prefix mapping.
# Applied only when *color* is enabled in :func:`render_status_rows`.
_STATUS_COLORS:dict[str, str] = {
    "published-local": colorama.Fore.GREEN,
    "changed": colorama.Fore.YELLOW,
    "due": colorama.Fore.RED,
    "draft": colorama.Fore.BLUE,
    "disabled": colorama.Style.DIM,
}


def _colorize_status(status:str, text:str) -> str:
    """Wrap *text* in ANSI colour codes for the given *status*, if a colour is mapped."""
    prefix = _STATUS_COLORS.get(status)
    if prefix is None:
        return text
    return f"{prefix}{text}{colorama.Style.RESET_ALL}"


def compute_ad_status(
    ad:Ad,
    ad_cfg_orig:dict[str, Any],
    *,
    now:datetime | None = None,
) -> str:
    """Map a single :class:`Ad` to a status string.

    Precedence (first match wins):
        1. ``disabled`` — *ad.active* is ``False``
        2. ``draft`` — *ad.id* is ``None``
        3. ``changed`` — stored *content_hash* exists, non-empty, and differs
           from recomputed hash
        4. ``due`` — both timestamp fields are ``None`` or
           *republication_interval* has elapsed
        5. ``published-local`` — fallthrough (has id, not disabled/draft/changed/due)
    """
    if not ad.active:
        return "disabled"
    if ad.id is None:
        return "draft"
    if ad_loading.has_ad_content_changed(ad, ad_cfg_orig):
        return "changed"
    if ad_loading.is_ad_due_for_republication(ad, now = now):
        return "due"
    return "published-local"


def build_status_rows(
    ads:list[tuple[str, Ad, dict[str, Any]]],
    *,
    now:datetime | None = None,
) -> list[StatusRow]:
    """Build status rows from ad-file / Ad / raw-dict triples."""
    rows:list[StatusRow] = []
    for _ad_file, ad_cfg, ad_cfg_orig in ads:
        status = compute_ad_status(ad_cfg, ad_cfg_orig, now = now)
        rows.append(
            StatusRow(
                title = ad_cfg.title,
                ad_id = "-" if ad_cfg.id is None else str(ad_cfg.id),
                status = status,
            )
        )
    return rows


def render_status_rows(rows:list[StatusRow], *, color:bool = False) -> str:
    """Format status rows into an ASCII table string.

    Args:
        rows:  Rows to render.
        color: If ``True``, apply ANSI colour codes to the status column.
               Column widths are always computed from plain (uncoloured)
               labels so that coloured and uncoloured output align identically.
    """
    if not rows:
        return ""

    h_id = _("Ad ID")
    h_title = _("Title")
    h_status = _("Status")

    col_id = max(len(h_id), max((len(r.ad_id) for r in rows), default = 0))

    # Translated labels for column width calculation.
    # Uses ``_translate_status()`` which contains explicit ``_("...")`` calls
    # that the translation-coverage scanner can find.
    translated_statuses = [_translate_status(s) for s in _STATUS_ORDER]
    col_status = max(len(h_status), *[len(s) for s in translated_statuses], 0)

    # Title width is data-driven, but clamp to at least header width
    col_title = max(len(h_title), max((len(r.title) for r in rows), default = 0))

    sep = (
        "+"
        + "-" * (col_id + 2)
        + "+"
        + "-" * (col_title + 2)
        + "+"
        + "-" * (col_status + 2)
        + "+"
    )
    header = (
        "| "
        + h_id.ljust(col_id)
        + " | "
        + h_title.ljust(col_title)
        + " | "
        + h_status.ljust(col_status)
        + " |"
    )

    lines:list[str] = [sep, header, sep]

    for r in rows:
        label = _translate_status(r.status)
        # Pad with plain label first, then wrap in colour if enabled.
        # This keeps stripped (ANSI-free) column widths identical.
        padded = label.ljust(col_status)
        display = _colorize_status(r.status, padded) if color else padded
        lines.append(
            "| "
            + r.ad_id.ljust(col_id)
            + " | "
            + r.title.ljust(col_title)
            + " | "
            + display
            + " |"
        )

    lines.append(sep)

    # Summary line — always plain
    counts:dict[str, int] = {}
    for r in rows:
        counts[r.status] = counts.get(r.status, 0) + 1

    summary_parts:list[str] = [
        f"{_translate_status(label)}: {counts[label]}"
        for label in _STATUS_ORDER
        if label in counts
    ]

    summary = _("Summary: %s (%d total)") % (", ".join(summary_parts), len(rows))
    lines.append(summary)

    return "\n".join(lines) + "\n"
