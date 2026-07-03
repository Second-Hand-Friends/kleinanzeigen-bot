# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Ad status computation and display for the ``status`` CLI command.
This module owns status label mapping, row building, and terminal rendering.
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
class AprDetail:
    """Structured APR preview detail for the status output."""

    result_key:str
    result:str
    effective_price:int | None
    reason_key:str
    reason:str
    price_before:int | None = None
    price_after:int | None = None
    cycle:int | None = None


@dataclass(frozen = True, slots = True)
class StatusRow:
    """One row in status output rendered by :func:`render_status_rows`."""

    title:str  # ad.title
    ad_id:str  # "-" if None, else str(ad.id)
    filename:str  # Relative ad-file path (e.g. "ads/sofa.yaml")
    status:str  # One of: "disabled", "draft", "changed", "due", "published-local"
    apr_repost_detail:AprDetail | None = None
    apr_update_detail:AprDetail | None = None


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

_STATUS_COLORS:dict[str, str] = {
    "published-local": colorama.Fore.GREEN,
    "changed": colorama.Fore.YELLOW,
    "due": colorama.Fore.RED,
    "draft": colorama.Fore.BLUE,
    "disabled": colorama.Style.DIM,
}

_MESSAGE_TEMPLATES:dict[str, str] = {
    "missing_price": "missing price",
    "min_price_equals_price": "minimum price equals current price",
    "update_disabled": "update reductions disabled",
    "repost_delay_waiting": "waiting for repost delay",
    "repost_delay_applied": "repost delay already applied",
    "day_delay_missing_timestamp": "waiting for publish timestamp",
    "day_delay_waiting": "waiting for day delay",
    "eligible": "eligible",
    "calculation_failed": "calculation failed",
    "no_visible_change": "no visible price change",
}


def _format_status(value:str, *, color:bool) -> str:
    """Return the translated status label, optionally colorized."""
    label = _translate_status(value)
    prefix = _STATUS_COLORS.get(value)
    if not color or prefix is None:
        return label
    return f"{prefix}{label}{colorama.Style.RESET_ALL}"


def _colorize(text:str, prefix:str, *, color:bool) -> str:
    if not color:
        return text
    return f"{prefix}{text}{colorama.Style.RESET_ALL}"


def _format_apr_reason(decision:_price_reduction.PriceReductionDecision) -> str:
    template = __get_message_template(decision.reason)
    return template if template is not None else decision.reason.replace("_", " ")


def __get_message_template(reason:str) -> str | None:
    template = _MESSAGE_TEMPLATES.get(reason)
    return _(template) if template is not None else None


def _format_apr_price(value:int | None) -> str:
    if value is None:
        return _("no effective price")
    return str(value)


def _format_apr_detail(decision:_price_reduction.PriceReductionDecision) -> AprDetail | None:
    """Return the structured APR preview for the status detail table."""
    if not decision.enabled:
        return None

    if decision.base_price is None:
        return AprDetail(
            result_key = "missing_price",
            result = _("missing price"),
            effective_price = None,
            reason_key = decision.reason,
            reason = _format_apr_reason(decision),
        )

    if decision.mode == AdUpdateStrategy.MODIFY and not decision.on_update:
        return AprDetail(
            result_key = "disabled",
            result = _("disabled"),
            effective_price = decision.result_price,
            reason_key = decision.reason,
            reason = _format_apr_reason(decision),
        )

    if decision.cycle_advanced:
        return AprDetail(
            result_key = "price_reduction",
            result = _("price reduction"),
            effective_price = decision.result_price,
            reason_key = decision.reason,
            reason = _format_apr_reason(decision),
            price_before = decision.restored_price,
            price_after = decision.result_price,
            cycle = decision.next_cycle,
        )

    return AprDetail(
        result_key = "no_new_reduction",
        result = _("no new reduction"),
        effective_price = decision.result_price,
        reason_key = decision.reason,
        reason = _format_apr_reason(decision),
    )


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

        if ad_cfg.active:
            replace_dec = _price_reduction.evaluate_auto_price_reduction(
                ad_cfg, ad_file_rel, mode = AdUpdateStrategy.REPLACE,
            )
            apr_repost_detail = _format_apr_detail(replace_dec)
            if ad_cfg.id is not None:
                modify_dec = _price_reduction.evaluate_auto_price_reduction(
                    ad_cfg, ad_file_rel, mode = AdUpdateStrategy.MODIFY,
                )
                apr_update_detail = _format_apr_detail(modify_dec)
            else:
                apr_update_detail = None
        else:
            apr_repost_detail = None
            apr_update_detail = None

        rows.append(
            StatusRow(
                title = ad_cfg.title,
                ad_id = "-" if ad_cfg.id is None else str(ad_cfg.id),
                filename = ad_file_rel,
                status = status,
                apr_repost_detail = apr_repost_detail,
                apr_update_detail = apr_update_detail,
            )
        )
    return rows


def _render_detail_line(label:str, value:str) -> str:
    return f"  {label}: {value}"


def _build_apr_line_parts(detail:AprDetail) -> str:
    parts:list[str] = [detail.result]

    parts.append(f"{_('effective price')}: {_format_apr_price(detail.effective_price)}")

    if detail.price_before is not None and detail.price_after is not None:
        before_formatted = _format_apr_price(detail.price_before)
        after_formatted = _format_apr_price(detail.price_after)
        if before_formatted != after_formatted:
            parts.append(f"{_('price change')}: {before_formatted} -> {after_formatted}")

    if detail.cycle is not None:
        parts.append(f"{_('cycle')}: {detail.cycle}")

    if detail.reason:
        parts.append(f"{_('reason')}: {detail.reason}")

    return "; ".join(parts)


def _render_apr_detail(detail:AprDetail, *, color:bool) -> str:
    text = _build_apr_line_parts(detail)
    prefix = _apr_detail_color(detail)
    if prefix is None:
        return text
    return _colorize(text, prefix, color = color)


def _apr_detail_color(detail:AprDetail) -> str | None:
    if detail.price_before is not None and detail.price_after is not None and detail.price_before != detail.price_after:
        return str(colorama.Fore.YELLOW)
    if detail.effective_price is None:
        return str(colorama.Fore.RED)
    if detail.result_key in {"disabled", "no_new_reduction"}:
        return str(colorama.Style.DIM)
    return None


def _render_status_block(row:StatusRow, *, color:bool) -> list[str]:
    lines:list[str] = [
        _colorize(row.filename, colorama.Style.BRIGHT, color = color),
        _render_detail_line(_("title"), row.title),
        _render_detail_line(_("id"), row.ad_id),
        _render_detail_line(_("status"), _format_status(row.status, color = color)),
    ]

    if row.apr_update_detail is not None:
        lines.append(
            _render_detail_line(
                _("APR update"),
                _render_apr_detail(row.apr_update_detail, color = color),
            )
        )
    if row.apr_repost_detail is not None:
        lines.append(
            _render_detail_line(
                _("APR publish"),
                _render_apr_detail(row.apr_repost_detail, color = color),
            )
        )
    return lines


def render_status_rows(
    rows:list[StatusRow],
    *,
    color:bool = False,
) -> str:
    """Format status rows into terminal output.

    Args:
        rows: Rows to render.
        color: If ``True``, emit terminal colours for the status value.
    """
    if not rows:
        return ""

    lines:list[str] = []

    for index, row in enumerate(rows):
        lines.extend(_render_status_block(row, color = color))
        if index != len(rows) - 1:
            lines.append("")

    # Summary line — always plain
    counts:dict[str, int] = {}
    for r in rows:
        counts[r.status] = counts.get(r.status, 0) + 1

    lines.append("")
    lines.append(
        _("Summary: %s (%d total)")
        % (", ".join(
            f"{_translate_status(s)}: {counts[s]}"
            for s in _STATUS_ORDER if s in counts
        ), len(rows))
    )

    return "\n".join(lines) + "\n"
