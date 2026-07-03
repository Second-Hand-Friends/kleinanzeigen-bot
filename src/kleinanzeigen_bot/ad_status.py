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
from . import price_reduction as _price_reduction
from .model.ad_model import AdUpdateStrategy

if TYPE_CHECKING:
    from .model.ad_model import Ad


@dataclass(frozen = True, slots = True)
class StatusRow:
    """One row in the status table. Rendered by :func:`render_status_rows`."""

    title:str  # ad.title
    ad_id:str  # "-" if None, else str(ad.id)
    filename:str  # Relative ad-file path (e.g. "ads/sofa.yaml")
    status:str  # One of: "disabled", "draft", "changed", "due", "published-local"
    apr_repost:str | None = None  # APR repost cell; ``None`` → rendered as "off"
    apr_update:str | None = None  # APR update cell; ``None`` → rendered as "off"


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


def _format_apr_status(decision:_price_reduction.PriceReductionDecision) -> str | None:
    """Format a price-reduction decision into a compact APR cell string.

    Returns ``None`` when the decision is not effective (rendered as ``off``).
    Otherwise one of: ``due: <price>``, ``not due``, ``error``.
    """
    if not decision.enabled:
        return None
    if decision.mode == AdUpdateStrategy.MODIFY and not decision.on_update:
        return None
    if decision.reason in {"missing_price", "calculation_failed"}:
        return _("error")
    if decision.cycle_advanced and decision.result_price is not None:
        return _("due: %s") % decision.result_price
    return _("not due")


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
    """Build status rows from ad-file / Ad / raw-dict triples.

    The first element of each triple is the **relative** ad file path,
    used for APR evaluation (``evaluate_auto_price_reduction``).
    """
    rows:list[StatusRow] = []
    for ad_file_rel, ad_cfg, ad_cfg_orig in ads:
        status = compute_ad_status(ad_cfg, ad_cfg_orig, now = now)

        apr_repost:str | None = None
        apr_update:str | None = None
        if ad_cfg.active:
            replace_dec = _price_reduction.evaluate_auto_price_reduction(
                ad_cfg, ad_file_rel, mode = AdUpdateStrategy.REPLACE,
            )
            apr_repost = _format_apr_status(replace_dec)
            if ad_cfg.id is not None:
                modify_dec = _price_reduction.evaluate_auto_price_reduction(
                    ad_cfg, ad_file_rel, mode = AdUpdateStrategy.MODIFY,
                )
                apr_update = _format_apr_status(modify_dec)

        rows.append(
            StatusRow(
                title = ad_cfg.title,
                ad_id = "-" if ad_cfg.id is None else str(ad_cfg.id),
                filename = ad_file_rel,
                status = status,
                apr_repost = apr_repost,
                apr_update = apr_update,
            )
        )
    return rows


def _apr_cell(value:str | None) -> str:
    """Return the display text for an APR cell — ``off`` when ``None``."""
    return _("off") if value is None else value


def _has_apr(rows:list[StatusRow]) -> bool:
    """Return ``True`` if any row has non-``None`` APR data (show APR columns)."""
    return any(r.apr_repost is not None or r.apr_update is not None for r in rows)


def _apr_layout(rows:list[StatusRow]) -> tuple[bool, str, str, int, int]:
    """Compute APR column layout: (show, h_repost, h_update, w_repost, w_update).

    Returns ``show=False`` with empty strings and zero widths when no
    effective APR data exists across *rows*.
    """
    if not _has_apr(rows):
        return False, "", "", 0, 0
    h_repost = _("APR repost")
    h_update = _("APR update")
    off = _("off")
    cells_repost = [off if r.apr_repost is None else r.apr_repost for r in rows]
    cells_update = [off if r.apr_update is None else r.apr_update for r in rows]
    w_repost = max(len(h_repost), *[len(c) for c in cells_repost], 0)
    w_update = max(len(h_update), *[len(c) for c in cells_update], 0)
    return True, h_repost, h_update, w_repost, w_update


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

    # Column header labels and data-driven widths.
    H = {
        "id": _("Ad ID"),
        "fn": _("Filename"),
        "title": _("Title"),
        "status": _("Status"),
    }
    W = {
        "id": max(len(H["id"]), max((len(r.ad_id) for r in rows), default = 0)),
        "fn": max(len(H["fn"]), max((len(r.filename) for r in rows), default = 0)),
        "title": max(len(H["title"]), max((len(r.title) for r in rows), default = 0)),
        "status": max(len(H["status"]), *[len(_translate_status(s)) for s in _STATUS_ORDER], 0),
    }

    # APR columns — only if any row has non-None APR data
    apr_show, h_apr_r, h_apr_u, w_apr_r, w_apr_u = _apr_layout(rows)

    # Build separator and header row
    widths = [W["id"], W["fn"], W["title"], W["status"]]
    if apr_show:
        widths += [w_apr_r, w_apr_u]
    sep = "".join("+" + "-" * (w + 2) for w in widths) + "+"

    hdr_parts = [
        "| ", H["id"].ljust(W["id"]), " | ", H["fn"].ljust(W["fn"]),
        " | ", H["title"].ljust(W["title"]), " | ", H["status"].ljust(W["status"]),
    ]
    if apr_show:
        hdr_parts += [" | ", h_apr_r.ljust(w_apr_r), " | ", h_apr_u.ljust(w_apr_u)]
    hdr_parts.append(" |")
    hdr = "".join(hdr_parts)

    lines:list[str] = [sep, hdr, sep]

    for r in rows:
        label = _translate_status(r.status).ljust(W["status"])
        cell = _colorize_status(r.status, label) if color else label

        parts = [
            "| ", r.ad_id.ljust(W["id"]), " | ", r.filename.ljust(W["fn"]),
            " | ", r.title.ljust(W["title"]), " | ", cell,
        ]
        if apr_show:
            parts += [
                " | ", _apr_cell(r.apr_repost).ljust(w_apr_r),
                " | ", _apr_cell(r.apr_update).ljust(w_apr_u),
            ]
        parts.append(" |")
        lines.append("".join(parts))

    lines.append(sep)

    # Summary line — always plain
    counts:dict[str, int] = {}
    for r in rows:
        counts[r.status] = counts.get(r.status, 0) + 1

    lines.append(
        _("Summary: %s (%d total)")
        % (", ".join(
            f"{_translate_status(s)}: {counts[s]}"
            for s in _STATUS_ORDER if s in counts
        ), len(rows))
    )

    return "\n".join(lines) + "\n"
