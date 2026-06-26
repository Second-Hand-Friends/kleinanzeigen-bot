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


def render_status_rows(rows:list[StatusRow]) -> str:
    """Format status rows into an ASCII table string."""
    if not rows:
        return ""

    h_id = _("Ad ID")
    h_title = _("Title")
    h_status = _("Status")

    col_id = max(len(h_id), max((len(r.ad_id) for r in rows), default = 0))

    # Translated labels for column width calculation.
    # Uses ``_translate_status()`` which contains explicit ``_("...")`` calls
    # that the translation-coverage scanner can find.
    translated_statuses = [
        _translate_status("disabled"),
        _translate_status("draft"),
        _translate_status("changed"),
        _translate_status("due"),
        _translate_status("published-local"),
    ]
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
        lines.append(
            "| "
            + r.ad_id.ljust(col_id)
            + " | "
            + r.title.ljust(col_title)
            + " | "
            + label.ljust(col_status)
            + " |"
        )

    lines.append(sep)

    # Summary line
    counts:dict[str, int] = {}
    for r in rows:
        counts[r.status] = counts.get(r.status, 0) + 1

    summary_parts:list[str] = [
        f"{_translate_status(label)}: {counts[label]}"
        for label in ("disabled", "draft", "changed", "due", "published-local")
        if label in counts
    ]

    summary = _("Summary: %s (%d total)") % (", ".join(summary_parts), len(rows))
    lines.append(summary)

    return "\n".join(lines) + "\n"
