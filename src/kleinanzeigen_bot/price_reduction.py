# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

"""Auto price reduction logic for Kleinanzeigen ads."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from kleinanzeigen_bot.model.ad_model import (
    Ad,
    AdUpdateStrategy,
    calculate_auto_price,
    calculate_auto_price_with_trace,
)

# Import 'misc' as a module (not functions directly) to preserve monkeypatch
# compatibility in tests — patches to 'kleinanzeigen_bot.misc.now' must affect
# the module object that callers reference.
from kleinanzeigen_bot.utils import loggers, misc
from kleinanzeigen_bot.utils.loggers import get_logger

LOG = get_logger(__name__)


def _repost_delay_state(ad_cfg:Ad) -> tuple[int, int, int, int]:
    """Return repost-delay state tuple.

    Returns:
        tuple[int, int, int, int]:
            (total_reposts, delay_reposts, applied_cycles, eligible_cycles)
    """
    total_reposts = ad_cfg.repost_count or 0
    delay_reposts = ad_cfg.auto_price_reduction.delay_reposts
    applied_cycles = ad_cfg.price_reduction_count or 0
    eligible_cycles = max(total_reposts - delay_reposts, 0)
    return total_reposts, delay_reposts, applied_cycles, eligible_cycles


def _day_delay_state(ad_cfg:Ad) -> tuple[bool, int | None, datetime | None]:
    """Return day-delay state tuple.

    Returns:
        tuple[bool, int | None, datetime | None]:
            (ready_flag, elapsed_days_or_none, reference_timestamp_or_none)
    """
    delay_days = ad_cfg.auto_price_reduction.delay_days
    # Use getattr to support lightweight test doubles without these attributes.
    reference = getattr(ad_cfg, "updated_on", None) or getattr(ad_cfg, "created_on", None)
    if delay_days == 0:
        return True, 0, reference

    if not reference:
        return False, None, None

    # Note: .days truncates to whole days (e.g., 1.9 days -> 1 day)
    # This is intentional: delays count complete 24-hour periods since publish
    # Both misc.now() and stored timestamps use UTC (via misc.now()), ensuring consistent calculations
    elapsed_days = (misc.now() - reference).days
    return elapsed_days >= delay_days, elapsed_days, reference


@dataclass(frozen = True)
class PriceReductionDecision:
    mode:AdUpdateStrategy
    enabled:bool
    on_update:bool
    base_price:int | None
    restored_price:int | None
    result_price:int | None
    applied_cycles:int
    next_cycle:int | None
    cycle_advanced:bool
    reason:str
    total_reposts:int
    delay_reposts:int
    eligible_cycles:int
    delay_days:int
    elapsed_days:int | None
    reference:datetime | None
    delay_reposts_ignored:bool


def evaluate_auto_price_reduction(ad_cfg:Ad, _ad_file_relative:str, *, mode:AdUpdateStrategy = AdUpdateStrategy.REPLACE) -> PriceReductionDecision:
    """Evaluate automatic price reduction without mutating ``ad_cfg``.

    Note:
        ``_ad_file_relative`` is intentionally unused and kept for API parity
        with :func:`apply_auto_price_reduction`.
    """
    cfg = ad_cfg.auto_price_reduction
    if cfg is None:
        return PriceReductionDecision(
            mode = mode,
            enabled = False,
            on_update = False,
            base_price = ad_cfg.price,
            restored_price = None,
            result_price = None,
            applied_cycles = 0,
            next_cycle = None,
            cycle_advanced = False,
            reason = "not_configured",
            total_reposts = ad_cfg.repost_count or 0,
            delay_reposts = 0,
            eligible_cycles = 0,
            delay_days = 0,
            elapsed_days = None,
            reference = None,
            delay_reposts_ignored = False,
        )
    on_update = bool(getattr(cfg, "on_update", False))
    base_price = ad_cfg.price
    total_reposts, delay_reposts, applied_cycles, eligible_cycles = _repost_delay_state(ad_cfg)
    day_ready, elapsed_days, reference = _day_delay_state(ad_cfg)
    delay_days = cfg.delay_days

    restored_price = base_price

    if not cfg.enabled:
        return PriceReductionDecision(
            mode = mode,
            enabled = False,
            on_update = on_update,
            base_price = base_price,
            restored_price = restored_price,
            result_price = restored_price,
            applied_cycles = applied_cycles,
            next_cycle = None,
            cycle_advanced = False,
            reason = "not_configured",
            total_reposts = total_reposts,
            delay_reposts = delay_reposts,
            eligible_cycles = eligible_cycles,
            delay_days = delay_days,
            elapsed_days = elapsed_days,
            reference = reference,
            delay_reposts_ignored = False,
        )

    if base_price is None:
        return PriceReductionDecision(
            mode = mode,
            enabled = True,
            on_update = on_update,
            base_price = None,
            restored_price = None,
            result_price = None,
            applied_cycles = applied_cycles,
            next_cycle = None,
            cycle_advanced = False,
            reason = "missing_price",
            total_reposts = total_reposts,
            delay_reposts = delay_reposts,
            eligible_cycles = eligible_cycles,
            delay_days = delay_days,
            elapsed_days = elapsed_days,
            reference = reference,
            delay_reposts_ignored = False,
        )

    if applied_cycles > 0:
        restored_price = calculate_auto_price(base_price = base_price, auto_price_reduction = cfg, target_reduction_cycle = applied_cycles)

    if cfg.min_price is not None and cfg.min_price == base_price and applied_cycles == 0:
        return PriceReductionDecision(
            mode = mode,
            enabled = True,
            on_update = on_update,
            base_price = base_price,
            restored_price = restored_price,
            result_price = restored_price,
            applied_cycles = applied_cycles,
            next_cycle = None,
            cycle_advanced = False,
            reason = "min_price_equals_price",
            total_reposts = total_reposts,
            delay_reposts = delay_reposts,
            eligible_cycles = eligible_cycles,
            delay_days = delay_days,
            elapsed_days = elapsed_days,
            reference = reference,
            delay_reposts_ignored = False,
        )

    if mode == AdUpdateStrategy.MODIFY and not on_update:
        return PriceReductionDecision(
            mode = mode,
            enabled = True,
            on_update = False,
            base_price = base_price,
            restored_price = restored_price,
            result_price = restored_price,
            applied_cycles = applied_cycles,
            next_cycle = None,
            cycle_advanced = False,
            reason = "update_disabled",
            total_reposts = total_reposts,
            delay_reposts = delay_reposts,
            eligible_cycles = eligible_cycles,
            delay_days = delay_days,
            elapsed_days = elapsed_days,
            reference = reference,
            delay_reposts_ignored = False,
        )

    delay_reposts_ignored = False
    if mode == AdUpdateStrategy.REPLACE:
        if total_reposts <= delay_reposts:
            reason = "repost_delay_waiting"
        elif eligible_cycles <= applied_cycles:
            reason = "repost_delay_applied"
        elif not day_ready:
            reason = "day_delay_missing_timestamp" if reference is None else "day_delay_waiting"
        else:
            reason = "eligible"
    else:
        delay_reposts_ignored = delay_reposts > 0
        reason = ("day_delay_missing_timestamp" if reference is None else "day_delay_waiting") if not day_ready else "eligible"

    if reason != "eligible":
        return PriceReductionDecision(
            mode = mode,
            enabled = True,
            on_update = on_update,
            base_price = base_price,
            restored_price = restored_price,
            result_price = restored_price,
            applied_cycles = applied_cycles,
            next_cycle = None,
            cycle_advanced = False,
            reason = reason,
            total_reposts = total_reposts,
            delay_reposts = delay_reposts,
            eligible_cycles = eligible_cycles,
            delay_days = delay_days,
            elapsed_days = elapsed_days,
            reference = reference,
            delay_reposts_ignored = delay_reposts_ignored,
        )

    next_cycle = applied_cycles + 1
    result_price = calculate_auto_price(base_price = base_price, auto_price_reduction = cfg, target_reduction_cycle = next_cycle)

    if result_price is None:
        return PriceReductionDecision(
            mode = mode,
            enabled = True,
            on_update = on_update,
            base_price = base_price,
            restored_price = restored_price,
            result_price = None,
            applied_cycles = applied_cycles,
            next_cycle = None,
            cycle_advanced = False,
            reason = "calculation_failed",
            total_reposts = total_reposts,
            delay_reposts = delay_reposts,
            eligible_cycles = eligible_cycles,
            delay_days = delay_days,
            elapsed_days = elapsed_days,
            reference = reference,
            delay_reposts_ignored = delay_reposts_ignored,
        )

    cycle_advanced = result_price != restored_price

    return PriceReductionDecision(
        mode = mode,
        enabled = True,
        on_update = on_update,
        base_price = base_price,
        restored_price = restored_price,
        result_price = result_price,
        applied_cycles = applied_cycles,
        next_cycle = next_cycle,
        cycle_advanced = cycle_advanced,
        reason = "eligible" if result_price != restored_price else "no_visible_change",
        total_reposts = total_reposts,
        delay_reposts = delay_reposts,
        eligible_cycles = eligible_cycles,
        delay_days = delay_days,
        elapsed_days = elapsed_days,
        reference = reference,
        delay_reposts_ignored = delay_reposts_ignored,
    )


def _log_auto_price_reduction_preview(ad_file_relative:str, decision:PriceReductionDecision) -> None:
    mode_label = "publish" if decision.mode == AdUpdateStrategy.REPLACE else "update"
    if not decision.enabled:
        LOG.info("Auto price reduction preview for [%s] (%s): disabled", ad_file_relative, mode_label)
        return

    if decision.base_price is None:
        LOG.info("Auto price reduction preview for [%s] (%s): missing price", ad_file_relative, mode_label)
        return

    if decision.mode == AdUpdateStrategy.MODIFY and not decision.on_update:
        LOG.info(
            "Auto price reduction preview for [%s] (%s): disabled (on_update=false, effective_price=%s)",
            ad_file_relative,
            mode_label,
            decision.result_price,
        )
        return

    if decision.cycle_advanced:
        LOG.info(
            "Auto price reduction preview for [%s] (%s): %s -> %s (cycle %s)",
            ad_file_relative,
            mode_label,
            decision.restored_price,
            decision.result_price,
            decision.next_cycle,
        )
        return

    LOG.info(
        "Auto price reduction preview for [%s] (%s): no new reduction (effective_price=%s, reason=%s)",
        ad_file_relative,
        mode_label,
        decision.result_price,
        decision.reason,
    )
    if decision.delay_reposts_ignored:
        LOG.debug(
            "Auto price reduction preview for [%s] (%s): delay_reposts=%s ignored in MODIFY mode",
            ad_file_relative,
            mode_label,
            decision.delay_reposts,
        )


def apply_auto_price_reduction(
    ad_cfg:Ad,
    _ad_cfg_orig:dict[str, Any],
    ad_file_relative:str,
    *,
    mode:AdUpdateStrategy = AdUpdateStrategy.REPLACE,
) -> None:
    """
    Apply automatic price reduction to an ad based on repost count and configuration.

    This function modifies ad_cfg in-place, updating the price and price_reduction_count
    fields when a reduction is applicable.

    :param ad_cfg: The ad configuration to potentially modify
    :param _ad_cfg_orig: The original ad configuration (unused, kept for compatibility)
    :param ad_file_relative: Relative path to the ad file for logging
    :param mode: Price-reduction evaluation mode. REPLACE uses repost+day delays,
        MODIFY uses day delay only and requires ``on_update``.
    """
    decision = evaluate_auto_price_reduction(ad_cfg, ad_file_relative, mode = mode)

    if not decision.enabled:
        LOG.debug("Auto price reduction: not configured for [%s]", ad_file_relative)
        return

    base_price = decision.base_price
    if base_price is None:
        LOG.warning("Auto price reduction is enabled for [%s] but no price is configured.", ad_file_relative)
        return

    if decision.restored_price is not None:
        ad_cfg.price = decision.restored_price

    if decision.reason == "min_price_equals_price":
        LOG.warning("Auto price reduction is enabled for [%s] but min_price equals price (%s) - no reductions will occur.", ad_file_relative, base_price)
        return

    total_reposts = decision.total_reposts
    delay_reposts = decision.delay_reposts
    applied_cycles = decision.applied_cycles
    eligible_cycles = decision.eligible_cycles
    elapsed_days = decision.elapsed_days
    reference = decision.reference
    delay_days = decision.delay_days
    elapsed_display = "missing" if elapsed_days is None else str(elapsed_days)
    reference_display = "missing" if reference is None else reference.isoformat(timespec = "seconds")

    if decision.reason == "update_disabled":
        LOG.debug("Auto price reduction skipped for [%s] in update mode because on_update is false", ad_file_relative)
        return

    if decision.reason == "calculation_failed":
        return

    if decision.reason in {"repost_delay_waiting", "repost_delay_applied"}:
        if decision.reason == "repost_delay_waiting":
            remaining = (delay_reposts + 1) - total_reposts
            LOG.info(
                "Auto price reduction delayed for [%s]: waiting %s more reposts (completed %s, applied %s reductions)",
                ad_file_relative,
                max(remaining, 1),
                total_reposts,
                applied_cycles,
            )
        else:
            current_price = decision.result_price if decision.result_price is not None else ad_cfg.price
            LOG.info(
                "Auto price reduction already applied for [%s]: price %s -> %s; %s reductions match %s eligible reposts",
                ad_file_relative,
                base_price,
                current_price,
                applied_cycles,
                eligible_cycles,
            )

        next_repost = delay_reposts + 1 if total_reposts <= delay_reposts else delay_reposts + applied_cycles + 1
        LOG.debug(
            "Auto price reduction decision for [%s]: skipped (repost delay). next reduction earliest at repost >= %s and day delay %s/%s days."
            " repost_count=%s eligible_cycles=%s applied_cycles=%s reference=%s",
            ad_file_relative,
            next_repost,
            elapsed_display,
            delay_days,
            total_reposts,
            eligible_cycles,
            applied_cycles,
            reference_display,
        )
        return

    if decision.reason in {"day_delay_waiting", "day_delay_missing_timestamp"}:
        if decision.reason == "day_delay_missing_timestamp":
            LOG.info("Auto price reduction delayed for [%s]: waiting %s days but publish timestamp missing", ad_file_relative, delay_days)
        else:
            LOG.info("Auto price reduction delayed for [%s]: waiting %s days (elapsed %s)", ad_file_relative, delay_days, elapsed_days)

        if decision.mode == AdUpdateStrategy.MODIFY and decision.delay_reposts_ignored:
            LOG.debug(
                "Auto price reduction for [%s]: delay_reposts=%s ignored in MODIFY mode (only delay_days applies)",
                ad_file_relative,
                delay_reposts,
            )
            LOG.debug(
                "Auto price reduction decision for [%s]: skipped (day delay, update mode). "
                "next reduction earliest when elapsed_days >= %s. elapsed_days=%s price_reduction_count=%s reference=%s",
                ad_file_relative,
                delay_days,
                elapsed_display,
                applied_cycles,
                reference_display,
            )
            return

        LOG.debug(
            "Auto price reduction decision for [%s]: skipped (day delay). next reduction earliest when elapsed_days >= %s."
            " elapsed_days=%s repost_count=%s eligible_cycles=%s applied_cycles=%s reference=%s",
            ad_file_relative,
            delay_days,
            elapsed_display,
            total_reposts,
            eligible_cycles,
            applied_cycles,
            reference_display,
        )
        return

    if decision.mode == AdUpdateStrategy.MODIFY and decision.delay_reposts_ignored:
        LOG.debug(
            "Auto price reduction for [%s]: delay_reposts=%s ignored in MODIFY mode (only delay_days applies)",
            ad_file_relative,
            delay_reposts,
        )

    if decision.reason == "no_visible_change":
        next_cycle = decision.next_cycle
        if next_cycle is None:
            LOG.debug("Auto price reduction skipped for [%s]: missing next_cycle for no_visible_change", ad_file_relative)
            return
        ad_cfg.price_reduction_count = next_cycle
        LOG.info("Auto price reduction kept price %s for [%s] after attempting %s reduction cycles", decision.restored_price, ad_file_relative, next_cycle)
        return

    LOG.debug(
        "Auto price reduction decision for [%s]: applying now (eligible_cycles=%s, applied_cycles=%s, elapsed_days=%s/%s).",
        ad_file_relative,
        eligible_cycles,
        applied_cycles,
        elapsed_display,
        delay_days,
    )

    next_cycle = decision.next_cycle
    if next_cycle is None:
        return

    if loggers.is_debug(LOG):
        effective_price, reduction_steps, price_floor = calculate_auto_price_with_trace(
            base_price = base_price,
            auto_price_reduction = ad_cfg.auto_price_reduction,
            target_reduction_cycle = next_cycle,
        )
        LOG.debug(
            "Auto price reduction trace for [%s]: strategy=%s amount=%s floor=%s target_cycle=%s base_price=%s",
            ad_file_relative,
            ad_cfg.auto_price_reduction.strategy,
            ad_cfg.auto_price_reduction.amount,
            price_floor,
            next_cycle,
            base_price,
        )
        for step in reduction_steps:
            LOG.debug(
                " -> cycle=%s before=%s reduction=%s after_rounding=%s floor_applied=%s",
                step.cycle,
                step.price_before,
                step.reduction_value,
                step.price_after_rounding,
                step.floor_applied,
            )
    else:
        effective_price = decision.result_price

    if effective_price is None:
        return

    ad_cfg.price = effective_price

    LOG.info("Auto price reduction applied for [%s]: %s -> %s after %s reduction cycles",
             ad_file_relative, decision.restored_price, effective_price, next_cycle)
    ad_cfg.price_reduction_count = next_cycle
    # Note: price_reduction_count is persisted to ad_cfg_orig only after successful publish
