# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import atexit, asyncio, enum, json, os, re, signal, sys, textwrap  # isort: skip
import getopt  # pylint: disable=deprecated-module
import urllib.parse as urllib_parse
from dataclasses import dataclass
from datetime import datetime
from gettext import gettext as _
from pathlib import Path
from typing import Any, Final, NamedTuple, Sequence, cast

import certifi, colorama, nodriver  # isort: skip
from nodriver.core.connection import ProtocolException
from ruamel.yaml import YAML
from wcmatch import glob

from . import extract, resources
from ._version import __version__
from .model.ad_model import (
    CARRIER_CODE_BY_OPTION,
    CARRIER_CODES_BY_SIZE,
    MAX_DESCRIPTION_LENGTH,
    SIZE_INFO_BY_CARRIER_CODE,
    Ad,
    AdPartial,
    Contact,
    calculate_auto_price,
    calculate_auto_price_with_trace,
)
from .model.config_model import DEFAULT_DOWNLOAD_DIR, Config
from .update_checker import UpdateChecker
from .utils import diagnostics, dicts, error_handlers, loggers, misc, xdg_paths
from .utils.exceptions import CaptchaEncountered, PublishedAdsFetchIncompleteError, PublishSubmissionUncertainError
from .utils.files import abspath
from .utils.i18n import Locale, get_current_locale, pluralize, set_current_locale
from .utils.misc import ainput, ensure, is_frozen
from .utils.timing_collector import TimingCollector
from .utils.web_scraping_mixin import By, Element, Is, WebScrapingMixin

# W0406: possibly a bug, see https://github.com/PyCQA/pylint/issues/3933

LOG:Final[loggers.Logger] = loggers.get_logger(__name__)
LOG.setLevel(loggers.INFO)

SUBMISSION_MAX_RETRIES:Final[int] = 3
_NUMERIC_IDS_RE:Final[re.Pattern[str]] = re.compile(r"^\d+(,\d+)*$")
_SPECIAL_ATTRIBUTE_TOKEN_RE:Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_]+$")
# See issue #930 for migrating __select_button_combobox to web_select_button_combobox
_WANTED_SHIPPING_LABELS:Final[dict[str, str]] = {
    "SHIPPING": "Versand möglich",
    "PICKUP": "Nur Abholung",
}
_LOGIN_DETECTION_SELECTORS:Final[list[tuple["By", str]]] = [
    (By.CLASS_NAME, "mr-medium"),
    (By.ID, "user-email"),
]
_LOGGED_OUT_CTA_SELECTORS:Final[list[tuple["By", str]]] = [
    (By.CSS_SELECTOR, 'a[href*="einloggen"]'),
    (By.CSS_SELECTOR, 'a[href*="/m-einloggen"]'),
]

colorama.just_fix_windows_console()


def _format_login_detection_selectors(selectors:Sequence[tuple["By", str]]) -> str:
    return ", ".join(f"{selector_type.name}={selector_value}" for selector_type, selector_value in selectors)


def _xpath_literal(value:str) -> str:
    """Return an XPath-safe string literal for *value*.

    Strategy:
    - no single quotes -> wrap in single quotes
    - no double quotes -> wrap in double quotes
    - contains both -> use concat('part1', "'", 'part2', ...)

    Example:
    - value = Bob's "Bike" -> concat('Bob', "'", 's "Bike"')

    This avoids quote-escaping issues in dynamic XPath expressions.
    """
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    return "concat(" + ', "\'", '.join(f"'{part}'" for part in value.split("'")) + ")"


class AdUpdateStrategy(enum.Enum):
    REPLACE = enum.auto()
    MODIFY = enum.auto()


class LoginDetectionReason(enum.Enum):
    USER_INFO_MATCH = enum.auto()
    CTA_MATCH = enum.auto()
    SELECTOR_TIMEOUT = enum.auto()


@dataclass(frozen = True)
class LoginDetectionResult:
    """Login detection result.

    Invariants:
    - is_logged_in=True only with USER_INFO_MATCH
    - is_logged_in=False with CTA_MATCH or SELECTOR_TIMEOUT
    """

    is_logged_in:bool
    reason:LoginDetectionReason

    def __post_init__(self) -> None:
        if not isinstance(self.is_logged_in, bool):
            raise TypeError("is_logged_in must be a bool")
        if not isinstance(self.reason, LoginDetectionReason):
            raise TypeError("reason must be a LoginDetectionReason")
        if self.is_logged_in and self.reason != LoginDetectionReason.USER_INFO_MATCH:
            raise ValueError("is_logged_in=True requires reason=USER_INFO_MATCH")
        if not self.is_logged_in and self.reason == LoginDetectionReason.USER_INFO_MATCH:
            raise ValueError("is_logged_in=False requires reason=CTA_MATCH or SELECTOR_TIMEOUT")


class ResolvedAdState(NamedTuple):
    """Resolution result for ad download state.

    Used by _resolve_download_ad_activity to return both the activity state
    and ownership status of an ad being downloaded.

    Attributes:
        active: Whether the ad should be saved as active (True) or inactive (False).
            Only ads with state="active" in the published profile are marked as active.
        owned: Whether the ad belongs to the current user (True) or is foreign (False).
            For "all"/"new" selectors this is typically True; for numeric IDs it may be False.
    """

    active:bool
    owned:bool


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


def _relative_ad_path(ad_file:str, config_file_path:str) -> str:
    """Compute an ad file path relative to the config directory, falling back to the absolute path."""
    try:
        return str(Path(ad_file).relative_to(Path(config_file_path).parent))
    except ValueError:
        return ad_file


def _get_marker_value(marker:Element) -> str:
    """Extract and normalize a hidden image marker value from a DOM element."""
    attrs = marker.attrs
    raw_value = attrs.get("value", "") if isinstance(attrs, dict) else getattr(attrs, "value", "")
    return str(raw_value or "").strip()


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
            LOG.info(
                "Auto price reduction already applied for [%s]: %s reductions match %s eligible reposts",
                ad_file_relative,
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
        LOG.info("Auto price reduction kept price %s after attempting %s reduction cycles", decision.restored_price, next_cycle)
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

    LOG.info("Auto price reduction applied: %s -> %s after %s reduction cycles", decision.restored_price, effective_price, next_cycle)
    ad_cfg.price_reduction_count = next_cycle
    # Note: price_reduction_count is persisted to ad_cfg_orig only after successful publish


class KleinanzeigenBot(WebScrapingMixin):  # noqa: PLR0904
    def __init__(self) -> None:
        # workaround for https://github.com/Second-Hand-Friends/kleinanzeigen-bot/issues/295
        # see https://github.com/pyinstaller/pyinstaller/issues/7229#issuecomment-1309383026
        os.environ["SSL_CERT_FILE"] = certifi.where()

        super().__init__()

        self.root_url = "https://www.kleinanzeigen.de"

        self.config:Config
        self.config_file_path = abspath("config.yaml")
        self.workspace:xdg_paths.Workspace | None = None
        self._config_arg:str | None = None
        self._workspace_mode_arg:xdg_paths.InstallationMode | None = None

        self.categories:dict[str, str] = {}

        self.file_log:loggers.LogFileHandle | None = None
        self._log_basename = os.path.splitext(os.path.basename(sys.executable))[0] if is_frozen() else self.__module__
        self.log_file_path:str | None = abspath(f"{self._log_basename}.log")
        self._logfile_arg:str | None = None
        self._logfile_explicitly_provided:bool = False

        self.command = "help"
        self.ads_selector = "due"
        self._ads_selector_explicit:bool = False
        self.keep_old_ads = False

        self._login_detection_diagnostics_captured:bool = False
        self._timing_collector:TimingCollector | None = None

    def __del__(self) -> None:
        if self.file_log:
            self.file_log.close()
            self.file_log = None
        self.close_browser_session()

    def get_version(self) -> str:
        return __version__

    def _workspace_or_raise(self) -> xdg_paths.Workspace:
        if self.workspace is None:
            raise AssertionError(_("Workspace must be resolved before command execution"))
        return self.workspace

    @property
    def _update_check_state_path(self) -> Path:
        return self._workspace_or_raise().state_dir / "update_check_state.json"

    def _resolve_download_dir(self) -> Path:
        workspace = self._workspace_or_raise()
        trimmed_dir = self.config.download.dir.strip()
        if trimmed_dir == DEFAULT_DOWNLOAD_DIR:
            return workspace.download_dir
        return Path(abspath(trimmed_dir, relative_to = str(Path(self.config_file_path).parent))).resolve()

    def _resolve_download_ad_activity(self, ad_id:int, published_ads_by_id:dict[int, dict[str, Any]]) -> ResolvedAdState:
        """Resolve downloaded ad activity and ownership for download selectors.

        Looks up the ad in the published profile and determines its activity state
        and ownership status. Used by "all", "new", and numeric ID selectors.

        Args:
            ad_id: The ad ID to look up in the published profile.
            published_ads_by_id: Dict mapping ad IDs to published ad data from the
                Kleinanzeigen API. Contains only the current user's own ads.

        Returns:
            ResolvedAdState with:
            - active=True if ad exists and state=="active", otherwise False
            - owned=True if ad exists in published_ads_by_id, otherwise False
        """
        published_ad = published_ads_by_id.get(ad_id)
        if published_ad is None:
            return ResolvedAdState(active = False, owned = False)

        return ResolvedAdState(active = published_ad.get("state") == "active", owned = True)

    async def _download_ad_with_resolved_state(self, ad_extractor:extract.AdExtractor, ad_id:int, published_ads_by_id:dict[int, dict[str, Any]]) -> None:
        """Download an ad with proper active state resolution and logging.

        Resolves the ad's activity state from the published profile, logs appropriately
        based on the resolution result, and initiates the download with the resolved state.

        This method centralizes the resolution + logging + download logic used by
        the "all" and "new" selectors.

        Args:
            ad_extractor: The AdExtractor instance to use for downloading.
            ad_id: The ad ID to download.
            published_ads_by_id: Dict mapping ad IDs to published ad data from API.

        Note:
            The numeric selector does NOT use this helper because it has different
            warning message semantics (foreign ads are expected, not anomalies).
        """
        resolved = self._resolve_download_ad_activity(ad_id, published_ads_by_id)

        if not resolved.owned:
            # Ad not in user's published profile - unexpected for "all"/"new" selectors
            # since these only list the user's own ads from the overview page
            LOG.warning("Ad %d found in overview but not in published profile. Saving as inactive.", ad_id)
        elif not resolved.active:
            # Ad is in published profile but not in active state (paused, inactive, etc.)
            published_ad = published_ads_by_id.get(ad_id, {})
            LOG.debug("Ad %d has state '%s'. Saving as inactive.", ad_id, published_ad.get("state", "unknown"))

        await ad_extractor.download_ad(ad_id, active = resolved.active)

    def _resolve_workspace(self) -> None:
        """
        Resolve workspace paths after CLI args are parsed.
        """
        if self.command in {"help", "version", "create-config"}:
            return
        effective_config_arg = self._config_arg
        effective_workspace_mode = self._workspace_mode_arg
        if not effective_config_arg:
            default_config = (Path.cwd() / "config.yaml").resolve()
            if self.config_file_path and Path(self.config_file_path).resolve() != default_config:
                effective_config_arg = self.config_file_path
                if effective_workspace_mode is None:
                    # Backward compatibility for tests/programmatic assignment of config_file_path:
                    # infer a stable default from the configured path location.
                    config_path = Path(self.config_file_path).resolve()
                    xdg_config_dir = xdg_paths.get_xdg_base_dir("config").resolve()
                    effective_workspace_mode = "xdg" if config_path.is_relative_to(xdg_config_dir) else "portable"

        try:
            self.workspace = xdg_paths.resolve_workspace(
                config_arg = effective_config_arg,
                logfile_arg = self._logfile_arg,
                workspace_mode = effective_workspace_mode,
                logfile_explicitly_provided = self._logfile_explicitly_provided,
                log_basename = self._log_basename,
            )
        except ValueError as exc:
            LOG.error(str(exc))
            sys.exit(2)

        xdg_paths.ensure_directory(self.workspace.config_file.parent, "config directory")

        self.config_file_path = str(self.workspace.config_file)
        self.log_file_path = str(self.workspace.log_file) if self.workspace.log_file else None

        LOG.info("Config:    %s", self.workspace.config_file)
        LOG.info("Workspace mode: %s", self.workspace.mode)
        LOG.info("Workspace: %s", self.workspace.config_dir)
        if loggers.is_debug(LOG):
            LOG.debug("Log file:        %s", self.workspace.log_file)
            LOG.debug("State dir:       %s", self.workspace.state_dir)
            LOG.debug("Download dir:    %s", self.workspace.download_dir)
            LOG.debug("Browser profile: %s", self.workspace.browser_profile_dir)
            LOG.debug("Diagnostics dir: %s", self.workspace.diagnostics_dir)

    async def run(self, args:list[str]) -> None:
        self.parse_args(args)
        self._resolve_workspace()
        try:
            match self.command:
                case "help":
                    self.show_help()
                    return
                case "version":
                    print(self.get_version())
                case "create-config":
                    self.create_default_config()
                    return
                case "diagnose":
                    self.configure_file_logging()
                    self.load_config()
                    self.diagnose_browser_issues()
                    return
                case "verify":
                    self.configure_file_logging()
                    self.load_config()
                    # Check for updates on startup
                    checker = UpdateChecker(self.config, self._update_check_state_path)
                    checker.check_for_updates()
                    self.ads_selector = "all"
                    if ads := self.load_ads(exclude_ads_with_id = False):
                        for ad_file, ad_cfg, _ad_cfg_orig in ads:
                            ad_file_relative = _relative_ad_path(ad_file, self.config_file_path)
                            publish_decision = evaluate_auto_price_reduction(ad_cfg, ad_file_relative, mode = AdUpdateStrategy.REPLACE)
                            _log_auto_price_reduction_preview(ad_file_relative, publish_decision)

                            update_decision = evaluate_auto_price_reduction(ad_cfg, ad_file_relative, mode = AdUpdateStrategy.MODIFY)
                            _log_auto_price_reduction_preview(ad_file_relative, update_decision)
                    LOG.info("############################################")
                    LOG.info("DONE: No configuration errors found.")
                    LOG.info("############################################")
                case "update-check":
                    self.configure_file_logging()
                    self.load_config()
                    checker = UpdateChecker(self.config, self._update_check_state_path)
                    checker.check_for_updates(skip_interval_check = True)
                case "update-content-hash":
                    self.configure_file_logging()
                    self.load_config()
                    # Check for updates on startup
                    checker = UpdateChecker(self.config, self._update_check_state_path)
                    checker.check_for_updates()
                    self.ads_selector = "all"
                    if ads := self.load_ads(exclude_ads_with_id = False):
                        self.update_content_hashes(ads)
                    else:
                        LOG.info("############################################")
                        LOG.info("DONE: No active ads found.")
                        LOG.info("############################################")
                case "publish":
                    self.configure_file_logging()
                    self.load_config()
                    # Check for updates on startup
                    checker = UpdateChecker(self.config, self._update_check_state_path)
                    checker.check_for_updates()

                    if not self._is_valid_ads_selector({"all", "new", "due", "changed"}):
                        if self._ads_selector_explicit:
                            LOG.error(
                                'Invalid --ads selector: "%s". Valid values: comma-separated keywords (all, new, due, changed) or numeric IDs.',
                                self.ads_selector,
                            )
                            sys.exit(2)
                        self.ads_selector = "due"

                    if ads := self.load_ads():
                        await self.create_browser_session()
                        await self.login()
                        await self.publish_ads(ads)
                    else:
                        LOG.info("############################################")
                        LOG.info("DONE: No new/outdated ads found.")
                        LOG.info("############################################")
                case "update":
                    self.configure_file_logging()
                    self.load_config()

                    if not self._is_valid_ads_selector({"all", "changed"}):
                        if self._ads_selector_explicit:
                            LOG.error('Invalid --ads selector: "%s". Valid values: comma-separated keywords (all, changed) or numeric IDs.', self.ads_selector)
                            sys.exit(2)
                        self.ads_selector = "changed"

                    if ads := self.load_ads():
                        await self.create_browser_session()
                        await self.login()
                        await self.update_ads(ads)
                    else:
                        LOG.info("############################################")
                        LOG.info("DONE: No changed ads found.")
                        LOG.info("############################################")
                case "delete":
                    self.configure_file_logging()
                    self.load_config()
                    # Check for updates on startup
                    checker = UpdateChecker(self.config, self._update_check_state_path)
                    checker.check_for_updates()
                    if ads := self.load_ads():
                        await self.create_browser_session()
                        await self.login()
                        await self.delete_ads(ads)
                    else:
                        LOG.info("############################################")
                        LOG.info("DONE: No ads to delete found.")
                        LOG.info("############################################")
                case "extend":
                    self.configure_file_logging()
                    self.load_config()
                    # Check for updates on startup
                    checker = UpdateChecker(self.config, self._update_check_state_path)
                    checker.check_for_updates()

                    # Default to all ads if no selector provided, but reject invalid values
                    if not self._is_valid_ads_selector({"all"}):
                        if self._ads_selector_explicit:
                            LOG.error('Invalid --ads selector: "%s". Valid values: all or comma-separated numeric IDs.', self.ads_selector)
                            sys.exit(2)
                        LOG.info("Extending all ads within 8-day window...")
                        self.ads_selector = "all"

                    if ads := self.load_ads():
                        await self.create_browser_session()
                        await self.login()
                        await self.extend_ads(ads)
                    else:
                        LOG.info("############################################")
                        LOG.info("DONE: No ads found to extend.")
                        LOG.info("############################################")
                case "download":
                    self.configure_file_logging()
                    # ad IDs depends on selector
                    if not self._is_valid_ads_selector({"all", "new"}):
                        if self._ads_selector_explicit:
                            LOG.error('Invalid --ads selector: "%s". Valid values: comma-separated keywords (all, new) or numeric IDs.', self.ads_selector)
                            sys.exit(2)
                        self.ads_selector = "new"
                    self.load_config()
                    # Check for updates on startup
                    checker = UpdateChecker(self.config, self._update_check_state_path)
                    checker.check_for_updates()
                    await self.create_browser_session()
                    await self.login()
                    await self.download_ads()

                case _:
                    LOG.error("Unknown command: %s", self.command)
                    sys.exit(2)
        finally:
            self.close_browser_session()
            if self._timing_collector is not None:
                try:
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(None, self._timing_collector.flush)
                except Exception as exc:  # noqa: BLE001
                    LOG.warning("Timing collector flush failed: %s", exc)

    def show_help(self) -> None:
        if is_frozen():
            exe = sys.argv[0]
        elif os.getenv("PDM_PROJECT_ROOT", ""):
            exe = "pdm run app"
        else:
            exe = "python -m kleinanzeigen_bot"

        if get_current_locale().language == "de":
            print(
                textwrap.dedent(
                    f"""\
            Verwendung: {colorama.Fore.LIGHTMAGENTA_EX}{exe} BEFEHL [OPTIONEN]{colorama.Style.RESET_ALL}

            Befehle:
              publish  - (Wieder-)Veröffentlicht Anzeigen
              verify   - Überprüft die Konfigurationsdateien
              delete   - Löscht Anzeigen
              update   - Aktualisiert bestehende Anzeigen
              extend   - Verlängert Anzeigen innerhalb des 8-Tage-Zeitfensters
              download - Lädt eine oder mehrere Anzeigen herunter
              update-check - Prüft auf verfügbare Updates
              update-content-hash - Berechnet den content_hash aller Anzeigen anhand der aktuellen ad_defaults neu;
                                    nach Änderungen an den config.yaml/ad_defaults verhindert es, dass alle Anzeigen als
                                    "geändert" gelten und neu veröffentlicht werden.
              create-config - Erstellt eine neue Standard-Konfigurationsdatei, falls noch nicht vorhanden
              diagnose - Diagnostiziert Browser-Verbindungsprobleme und zeigt Troubleshooting-Informationen
              --
              help     - Zeigt diese Hilfe an (Standardbefehl)
              version  - Zeigt die Version der Anwendung an

            Optionen:
              --ads=all|due|new|changed|<id(s)> (publish) - Gibt an, welche Anzeigen (erneut) veröffentlicht werden sollen (STANDARD: due)
                    Mögliche Werte:
                    * all: Veröffentlicht alle Anzeigen erneut, ignoriert republication_interval
                    * due: Veröffentlicht alle neuen Anzeigen und erneut entsprechend dem republication_interval
                    * new: Veröffentlicht nur neue Anzeigen (d.h. Anzeigen ohne ID in der Konfigurationsdatei)
                    * changed: Veröffentlicht nur Anzeigen, die seit der letzten Veröffentlichung geändert wurden
                    * <id(s)>: Gibt eine oder mehrere Anzeigen-IDs an, die veröffentlicht werden sollen, z. B. "--ads=1,2,3", ignoriert republication_interval
                    * Kombinationen: Sie können mehrere Selektoren mit Kommas kombinieren, z. B. "--ads=changed,due" um sowohl geänderte als auch
                      fällige Anzeigen zu veröffentlichen
              --ads=all|new|<id(s)> (download) - Gibt an, welche Anzeigen heruntergeladen werden sollen (STANDARD: new)
                    Mögliche Werte:
                    * all: Lädt alle Anzeigen aus Ihrem Profil herunter
                    * new: Lädt Anzeigen aus Ihrem Profil herunter, die lokal noch nicht gespeichert sind
                    * <id(s)>: Gibt eine oder mehrere Anzeigen-IDs zum Herunterladen an, z. B. "--ads=1,2,3"
              --ads=all|changed|<id(s)> (update) - Gibt an, welche Anzeigen aktualisiert werden sollen (STANDARD: changed)
                    Mögliche Werte:
                    * all: Aktualisiert alle Anzeigen
                    * changed: Aktualisiert nur Anzeigen, die seit der letzten Veröffentlichung geändert wurden
                    * <id(s)>: Gibt eine oder mehrere Anzeigen-IDs zum Aktualisieren an, z. B. "--ads=1,2,3"
              --ads=all|<id(s)> (extend) - Gibt an, welche Anzeigen verlängert werden sollen
                    Mögliche Werte:
                    * all: Verlängert alle Anzeigen, die innerhalb von 8 Tagen ablaufen
                    * <id(s)>: Gibt bestimmte Anzeigen-IDs an, z. B. "--ads=1,2,3"
              --force           - Alias für '--ads=all'
              --keep-old        - Verhindert das Löschen alter Anzeigen bei erneuter Veröffentlichung
              --config=<PATH>   - Pfad zur YAML- oder JSON-Konfigurationsdatei (ändert den Workspace-Modus nicht implizit)
              --workspace-mode=portable|xdg - Überschreibt den Workspace-Modus für diesen Lauf
              --logfile=<PATH>  - Pfad zur Protokolldatei (STANDARD: vom aktiven Workspace-Modus abhängig)
              --lang=en|de      - Anzeigesprache (STANDARD: Systemsprache, wenn unterstützt, sonst Englisch)
              -v, --verbose     - Aktiviert detaillierte Ausgabe – nur nützlich zur Fehlerbehebung
            """.rstrip()
                )
            )
        else:
            print(
                textwrap.dedent(
                    f"""\
            Usage: {colorama.Fore.LIGHTMAGENTA_EX}{exe} COMMAND [OPTIONS]{colorama.Style.RESET_ALL}

            Commands:
              publish  - (re-)publishes ads
              verify   - verifies the configuration files
              delete   - deletes ads
              update   - updates published ads
              extend   - extends ads within the 8-day window before expiry
              download - downloads one or multiple ads
              update-check - checks for available updates
              update-content-hash – recalculates each ad's content_hash based on the current ad_defaults;
                                    use this after changing config.yaml/ad_defaults to avoid every ad being marked "changed" and republished
              create-config - creates a new default configuration file if one does not exist
              diagnose - diagnoses browser connection issues and shows troubleshooting information
              --
              help     - displays this help (default command)
              version  - displays the application version

            Options:
              --ads=all|due|new|changed|<id(s)> (publish) - specifies which ads to (re-)publish (DEFAULT: due)
                    Possible values:
                    * all: (re-)publish all ads ignoring republication_interval
                    * due: publish all new ads and republish ads according the republication_interval
                    * new: only publish new ads (i.e. ads that have no id in the config file)
                    * changed: only publish ads that have been modified since last publication
                    * <id(s)>: provide one or several ads by ID to (re-)publish, like e.g. "--ads=1,2,3" ignoring republication_interval
                    * Combinations: You can combine multiple selectors with commas, e.g. "--ads=changed,due" to publish both changed and due ads
              --ads=all|new|<id(s)> (download) - specifies which ads to download (DEFAULT: new)
                    Possible values:
                    * all: downloads all ads from your profile
                    * new: downloads ads from your profile that are not locally saved yet
                    * <id(s)>: provide one or several ads by ID to download, like e.g. "--ads=1,2,3"
              --ads=all|changed|<id(s)> (update) - specifies which ads to update (DEFAULT: changed)
                    Possible values:
                    * all: update all ads
                    * changed: only update ads that have been modified since last publication
                    * <id(s)>: provide one or several ads by ID to update, like e.g. "--ads=1,2,3"
              --ads=all|<id(s)> (extend) - specifies which ads to extend
                    Possible values:
                    * all: extend all ads expiring within 8 days
                    * <id(s)>: specify ad IDs to extend, e.g. "--ads=1,2,3"
              --force           - alias for '--ads=all'
              --keep-old        - don't delete old ads on republication
              --config=<PATH>   - path to the config YAML or JSON file (does not implicitly change workspace mode)
              --workspace-mode=portable|xdg - overrides workspace mode for this run
              --logfile=<PATH>  - path to the logfile (DEFAULT: depends on active workspace mode)
              --lang=en|de      - display language (STANDARD: system language if supported, otherwise English)
              -v, --verbose     - enables verbose output - only useful when troubleshooting issues
            """.rstrip()
                )
            )

    def _is_valid_ads_selector(self, valid_keywords:set[str]) -> bool:
        """Check if the current ads_selector is valid for the given set of keyword selectors.

        Accepts a single keyword, a comma-separated list of keywords, or a comma-separated
        list of numeric IDs. Mixed keyword+numeric selectors are not supported.
        """
        return (
            self.ads_selector in valid_keywords
            or all(s.strip() in valid_keywords for s in self.ads_selector.split(","))
            or bool(_NUMERIC_IDS_RE.match(self.ads_selector))
        )

    def parse_args(self, args:list[str]) -> None:
        try:
            options, arguments = getopt.gnu_getopt(
                args[1:],
                "hv",
                ["ads=", "config=", "force", "help", "keep-old", "logfile=", "lang=", "verbose", "workspace-mode="],
            )
        except getopt.error as ex:
            LOG.error(ex.msg)
            LOG.error("Use --help to display available options.")
            sys.exit(2)

        for option, value in options:
            match option:
                case "-h" | "--help":
                    self.show_help()
                    sys.exit(0)
                case "--config":
                    self.config_file_path = abspath(value)
                    self._config_arg = value
                case "--logfile":
                    if value:
                        self.log_file_path = abspath(value)
                    else:
                        self.log_file_path = None
                    self._logfile_arg = value
                    self._logfile_explicitly_provided = True
                case "--workspace-mode":
                    mode = value.strip().lower()
                    if mode not in {"portable", "xdg"}:
                        LOG.error("Invalid --workspace-mode '%s'. Use 'portable' or 'xdg'.", value)
                        sys.exit(2)
                    self._workspace_mode_arg = cast(xdg_paths.InstallationMode, mode)
                case "--ads":
                    self.ads_selector = value.strip().lower()
                    self._ads_selector_explicit = True
                case "--force":
                    self.ads_selector = "all"
                    self._ads_selector_explicit = True
                case "--keep-old":
                    self.keep_old_ads = True
                case "--lang":
                    set_current_locale(Locale.of(value))
                case "-v" | "--verbose":
                    LOG.setLevel(loggers.DEBUG)
                    loggers.get_logger("nodriver").setLevel(loggers.INFO)

        match len(arguments):
            case 0:
                self.command = "help"
            case 1:
                self.command = arguments[0]
            case _:
                LOG.error("More than one command given: %s", arguments)
                sys.exit(2)

    def configure_file_logging(self) -> None:
        if not self.log_file_path:
            return
        if self.file_log:
            return

        if self.workspace and self.workspace.log_file:
            xdg_paths.ensure_directory(self.workspace.log_file.parent, "log directory")

        LOG.info("Logging to [%s]...", self.log_file_path)
        self.file_log = loggers.configure_file_logging(self.log_file_path)

        LOG.info("App version: %s", self.get_version())
        LOG.info("Python version: %s", sys.version)

    def create_default_config(self) -> None:
        """
        Create a default config.yaml in the project root if it does not exist.
        If it exists, log an error and inform the user.
        """
        if os.path.exists(self.config_file_path):
            LOG.error("Config file %s already exists. Aborting creation.", self.config_file_path)
            return
        config_parent = self.workspace.config_file.parent if self.workspace else Path(self.config_file_path).parent
        xdg_paths.ensure_directory(config_parent, "config directory")
        default_config = Config.model_construct()
        default_config.login.username = "changeme"  # noqa: S105 placeholder for default config, not a real username
        default_config.login.password = "changeme"  # noqa: S105 placeholder for default config, not a real password
        dicts.save_commented_model(
            self.config_file_path,
            default_config,
            header = "# yaml-language-server: $schema=https://raw.githubusercontent.com/Second-Hand-Friends/kleinanzeigen-bot/main/schemas/config.schema.json",
            exclude = {
                "ad_defaults": {"description"},
            },
        )

    def load_config(self) -> None:
        # write default config.yaml if config file does not exist
        if not os.path.exists(self.config_file_path):
            self.create_default_config()

        config_yaml = dicts.load_dict_if_exists(self.config_file_path, _("config"))
        self.config = Config.model_validate(config_yaml, strict = True, context = self.config_file_path)

        timing_enabled = self.config.diagnostics.timing_collection
        if timing_enabled and self.workspace:
            timing_dir = self.workspace.diagnostics_dir.parent / "timing"
            self._timing_collector = TimingCollector(timing_dir, self.command)
        else:
            self._timing_collector = None

        # load built-in category mappings
        self.categories = dicts.load_dict_from_module(resources, "categories.yaml", "")
        LOG.debug("Loaded %s categories from categories.yaml", len(self.categories))
        deprecated_categories = dicts.load_dict_from_module(resources, "categories_old.yaml", "")
        LOG.debug("Loaded %s categories from categories_old.yaml", len(deprecated_categories))
        self.categories.update(deprecated_categories)
        custom_count = 0
        if self.config.categories:
            custom_count = len(self.config.categories)
            self.categories.update(self.config.categories)
            LOG.debug("Loaded %s categories from config.yaml (custom)", custom_count)
        total_count = len(self.categories)
        if total_count == 0:
            LOG.warning("No categories loaded - category files may be missing or empty")
        LOG.debug("Loaded %s categories in total", total_count)

        # populate browser_config object used by WebScrapingMixin
        self.browser_config.arguments = self.config.browser.arguments
        self.browser_config.binary_location = self.config.browser.binary_location
        self.browser_config.extensions = [abspath(item, relative_to = self.config_file_path) for item in self.config.browser.extensions]
        self.browser_config.use_private_window = self.config.browser.use_private_window
        if self.config.browser.user_data_dir:
            self.browser_config.user_data_dir = abspath(self.config.browser.user_data_dir, relative_to = self.config_file_path)
        elif self.workspace:
            self.browser_config.user_data_dir = str(self.workspace.browser_profile_dir)
        self.browser_config.profile_name = self.config.browser.profile_name

    def __check_ad_republication(self, ad_cfg:Ad, ad_file_relative:str) -> bool:
        """
        Check if an ad needs to be republished based on republication interval.
        Note:  This method does not check for content changes. Use __check_ad_changed for that.

        Returns:
            True if the ad should be republished based on the interval.
        """
        if ad_cfg.updated_on:
            last_updated_on = ad_cfg.updated_on
        elif ad_cfg.created_on:
            last_updated_on = ad_cfg.created_on
        else:
            return True

        if not last_updated_on:
            return True

        # Check republication interval
        ad_age = misc.now() - last_updated_on
        if ad_age.days <= ad_cfg.republication_interval:
            LOG.info(
                " -> SKIPPED: ad [%s] was last published %d days ago. republication is only required every %s days",
                ad_file_relative,
                ad_age.days,
                ad_cfg.republication_interval,
            )
            return False

        return True

    def __check_ad_changed(self, ad_cfg:Ad, ad_cfg_orig:dict[str, Any], ad_file_relative:str) -> bool:
        """
        Check if an ad has been changed since last publication.

        Returns:
            True if the ad has been changed.
        """
        if not ad_cfg.id:
            # New ads are not considered "changed"
            return False

        # Calculate hash on original config to match what was stored
        current_hash = AdPartial.model_validate(ad_cfg_orig).update_content_hash().content_hash
        stored_hash = ad_cfg_orig.get("content_hash")

        LOG.debug("Hash comparison for [%s]:", ad_file_relative)
        LOG.debug("    Stored hash: %s", stored_hash)
        LOG.debug("    Current hash: %s", current_hash)

        if stored_hash and current_hash != stored_hash:
            LOG.info("Changes detected in ad [%s], will republish", ad_file_relative)
            # Update hash in original configuration
            ad_cfg_orig["content_hash"] = current_hash
            return True

        return False

    def load_ads(self, *, ignore_inactive:bool = True, exclude_ads_with_id:bool = True) -> list[tuple[str, Ad, dict[str, Any]]]:
        """
        Load and validate all ad config files, optionally filtering out inactive or already-published ads.

        Args:
            ignore_inactive (bool):
                Skip ads with `active=False`.
            exclude_ads_with_id (bool):
                Skip ads whose raw data already contains an `id`, i.e. was published before.

        Returns:
            list[tuple[str, Ad, dict[str, Any]]]:
            Tuples of (file_path, validated Ad model, original raw data).
        """
        LOG.info("Searching for ad config files...")

        ad_files:dict[str, str] = {}
        data_root_dir = os.path.dirname(self.config_file_path)
        for file_pattern in self.config.ad_files:
            for ad_file in glob.glob(file_pattern, root_dir = data_root_dir, flags = glob.GLOBSTAR | glob.BRACE | glob.EXTGLOB):
                if not str(ad_file).endswith("ad_fields.yaml"):
                    ad_files[abspath(ad_file, relative_to = data_root_dir)] = ad_file
        LOG.info(" -> found %s", pluralize("ad config file", ad_files))
        if not ad_files:
            return []

        ids = []
        use_specific_ads = False
        selectors = self.ads_selector.split(",")

        if _NUMERIC_IDS_RE.match(self.ads_selector):
            ids = [int(n) for n in self.ads_selector.split(",")]
            use_specific_ads = True
            LOG.info("Start fetch task for the ad(s) with id(s):")
            LOG.info(" | ".join([str(id_) for id_ in ids]))

        ads = []
        for ad_file, ad_file_relative in sorted(ad_files.items()):
            ad_cfg_orig:dict[str, Any] = dicts.load_dict(ad_file, "ad")
            ad_cfg:Ad = self.load_ad(ad_cfg_orig)

            if ignore_inactive and not ad_cfg.active:
                LOG.info(" -> SKIPPED: inactive ad [%s]", ad_file_relative)
                continue

            if use_specific_ads:
                if ad_cfg.id not in ids:
                    LOG.info(" -> SKIPPED: ad [%s] is not in list of given ids.", ad_file_relative)
                    continue
            else:
                # Check if ad should be included based on selectors
                should_include = False

                # Check for 'changed' selector
                if "changed" in selectors and self.__check_ad_changed(ad_cfg, ad_cfg_orig, ad_file_relative):
                    should_include = True

                # Check for 'new' selector
                if "new" in selectors and (not ad_cfg.id or not exclude_ads_with_id):
                    should_include = True
                elif "new" in selectors and ad_cfg.id and exclude_ads_with_id:
                    LOG.info(" -> SKIPPED: ad [%s] is not new. already has an id assigned.", ad_file_relative)

                # Check for 'due' selector
                if "due" in selectors:
                    # For 'due' selector, check if the ad is due for republication based on interval
                    if self.__check_ad_republication(ad_cfg, ad_file_relative):
                        should_include = True

                # Check for 'all' selector (always include)
                if "all" in selectors:
                    should_include = True

                if not should_include:
                    continue

            ensure(self.__get_description(ad_cfg, with_affixes = False), f"-> property [description] not specified @ [{ad_file}]")
            self.__get_description(ad_cfg, with_affixes = True)  # validates complete description

            if ad_cfg.category:
                resolved_category_id = self.categories.get(ad_cfg.category)
                if not resolved_category_id and ">" in ad_cfg.category:
                    # this maps actually to the sonstiges/weiteres sub-category
                    parent_category = ad_cfg.category.rpartition(">")[0].strip()
                    resolved_category_id = self.categories.get(parent_category)
                    if resolved_category_id:
                        LOG.warning("Category [%s] unknown. Using category [%s] with ID [%s] instead.", ad_cfg.category, parent_category, resolved_category_id)

                if resolved_category_id:
                    ad_cfg.category = resolved_category_id

            if ad_cfg.images:
                images = []
                ad_dir = os.path.dirname(ad_file)
                for image_pattern in ad_cfg.images:
                    pattern_images = set()
                    for image_file in glob.glob(image_pattern, root_dir = ad_dir, flags = glob.GLOBSTAR | glob.BRACE | glob.EXTGLOB):
                        _, image_file_ext = os.path.splitext(image_file)
                        ensure(image_file_ext.lower() in {".gif", ".jpg", ".jpeg", ".png"}, f"Unsupported image file type [{image_file}]")
                        if os.path.isabs(image_file):
                            pattern_images.add(image_file)
                        else:
                            pattern_images.add(abspath(image_file, relative_to = ad_file))
                    images.extend(sorted(pattern_images))
                ensure(images or not ad_cfg.images, f"No images found for given file patterns {ad_cfg.images} at {ad_dir}")
                ad_cfg.images = list(dict.fromkeys(images))

            LOG.info(" -> LOADED: ad [%s]", ad_file_relative)
            ads.append((ad_file, ad_cfg, ad_cfg_orig))

        LOG.info("Loaded %s", pluralize("ad", ads))
        return ads

    def load_ad(self, ad_cfg_orig:dict[str, Any]) -> Ad:
        return AdPartial.model_validate(ad_cfg_orig).to_ad(self.config.ad_defaults)

    async def check_and_wait_for_captcha(self, *, is_login_page:bool = True, page_context:str | None = None) -> None:
        captcha_elem = await self.web_probe(
            By.CSS_SELECTOR,
            "iframe[name^='a-'][src^='https://www.google.com/recaptcha/api2/anchor?']",
            timeout = self._timeout("captcha_detection"),
        )

        context_label = page_context or ("login page" if is_login_page else "publish operation")
        if captcha_elem is None:
            LOG.debug("No captcha detected within timeout (page_context=%s)", context_label)
            return

        if not is_login_page and self.config.captcha.auto_restart:
            LOG.warning("Captcha recognized - auto-restart enabled, abort run...")
            raise CaptchaEncountered(misc.parse_duration(self.config.captcha.restart_delay))

        LOG.warning("############################################")
        LOG.warning("# Captcha present! Please solve the captcha.")
        LOG.warning("############################################")

        if not is_login_page:
            await self.web_scroll_page_down()

        await ainput(_("Press a key to continue..."))

    async def login(self) -> None:
        self._login_detection_diagnostics_captured = False
        sso_navigation_timeout = self._timeout("page_load")
        pre_login_gdpr_timeout = self._timeout("quick_dom")

        LOG.info("Checking if already logged in...")
        await self.web_open(f"{self.root_url}")
        await self._click_gdpr_banner(timeout = pre_login_gdpr_timeout)

        detection_result = await self.get_login_state(capture_diagnostics = False)
        if detection_result.is_logged_in:
            LOG.info("Already logged in. Skipping login.")
            return

        LOG.debug("Navigating to SSO login page (Auth0)...")
        # m-einloggen-sso.html triggers immediate server-side redirect to Auth0
        # This avoids waiting for JS on m-einloggen.html which may not execute in headless mode
        try:
            await self.web_open(f"{self.root_url}/m-einloggen-sso.html", timeout = sso_navigation_timeout)
        except TimeoutError:
            LOG.warning("Timeout navigating to SSO login page after %.1fs", sso_navigation_timeout)
            await self._capture_login_detection_diagnostics_if_enabled(
                base_prefix = "login_detection_sso_navigation_timeout",
                pause_banner_message = "# SSO navigation timed out. Browser is paused for manual inspection.",
            )
            raise

        try:
            await self.fill_login_data_and_send()
            await self.handle_after_login_logic()
        except (AssertionError, TimeoutError):
            # AssertionError is intentionally part of auth-boundary control flow so
            # diagnostics are captured before the original error is re-raised.
            await self._capture_login_detection_diagnostics_if_enabled(
                base_prefix = "login_detection_auth0_flow_failure",
                pause_banner_message = "# Auth0 login flow failed. Browser is paused for manual inspection.",
            )
            raise

        await self._dismiss_consent_banner()

        detection_result = await self.get_login_state(capture_diagnostics = False)
        if detection_result.is_logged_in:
            LOG.info("Login confirmed.")
            return

        current_url = self._current_page_url()
        LOG.debug("Login detection reason after attempt is %s", detection_result.reason.name)
        LOG.warning("Login could not be confirmed after Auth0 flow (url=%s)", current_url)
        await self._capture_login_detection_diagnostics_if_enabled(
            base_prefix = f"login_detection_{detection_result.reason.name.lower()}",
            pause_banner_message = "# Login confirmation failed after Auth0 flow. Browser is paused for manual inspection.",
        )
        raise AssertionError(_("Login could not be confirmed after Auth0 flow (reason=%s, url=%s)") % (detection_result.reason.name, current_url))

    def _current_page_url(self) -> str:
        page = getattr(self, "page", None)
        if page is None:
            return "unknown"
        url = getattr(page, "url", None)
        if not isinstance(url, str) or not url:
            return "unknown"

        parsed = urllib_parse.urlparse(url)
        host = parsed.hostname or parsed.netloc.split("@")[-1]
        netloc = f"{host}:{parsed.port}" if parsed.port is not None and host else host
        sanitized = urllib_parse.urlunparse((parsed.scheme, netloc, parsed.path, "", "", ""))
        return sanitized or "unknown"

    async def _wait_for_auth0_login_context(self) -> None:
        redirect_timeout = self._timeout("login_detection")
        try:
            await self.web_await(
                lambda: "login.kleinanzeigen.de" in self._current_page_url() or "/u/login" in self._current_page_url(),
                timeout = redirect_timeout,
                timeout_error_message = f"Auth0 redirect did not start within {redirect_timeout} seconds",
                apply_multiplier = False,
            )
        except TimeoutError as ex:
            current_url = self._current_page_url()
            raise AssertionError(_("Auth0 redirect not detected (url=%s)") % current_url) from ex

    async def _wait_for_auth0_password_step(self) -> None:
        password_step_timeout = self._timeout("login_detection")
        try:
            await self.web_await(
                lambda: "/u/login/password" in self._current_page_url(),
                timeout = password_step_timeout,
                timeout_error_message = f"Auth0 password page not reached within {password_step_timeout} seconds",
                apply_multiplier = False,
            )
        except TimeoutError as ex:
            current_url = self._current_page_url()
            raise AssertionError(_("Auth0 password step not reached (url=%s)") % current_url) from ex

    async def _wait_for_post_auth0_submit_transition(self) -> None:
        post_submit_timeout = self._timeout("login_detection")
        quick_dom_timeout = self._timeout("quick_dom")
        fallback_max_ms = max(700, int(quick_dom_timeout * 1_000))
        fallback_min_ms = max(300, fallback_max_ms // 2)

        try:
            await self.web_await(
                lambda: self._is_valid_post_auth0_destination(self._current_page_url()),
                timeout = post_submit_timeout,
                timeout_error_message = f"Auth0 post-submit transition did not complete within {post_submit_timeout} seconds",
                apply_multiplier = False,
            )
            return
        except TimeoutError:
            LOG.debug("Post-submit transition not detected via URL, checking logged-in selectors")

        login_confirmed = False
        try:
            login_confirmed = await asyncio.wait_for(self.is_logged_in(), timeout = post_submit_timeout)
        except (TimeoutError, asyncio.TimeoutError):
            LOG.debug("Post-submit login verification did not complete within %.1fs", post_submit_timeout)

        if login_confirmed:
            return

        LOG.debug("Auth0 post-submit verification remained inconclusive; applying bounded fallback pause")
        await self.web_sleep(min_ms = fallback_min_ms, max_ms = fallback_max_ms)

        try:
            if await asyncio.wait_for(self.is_logged_in(), timeout = quick_dom_timeout):
                return
        except (TimeoutError, asyncio.TimeoutError):
            LOG.debug("Final post-submit login confirmation did not complete within %.1fs", quick_dom_timeout)

        current_url = self._current_page_url()
        raise TimeoutError(_("Auth0 post-submit verification remained inconclusive (url=%s)") % current_url)

    def _is_valid_post_auth0_destination(self, url:str) -> bool:
        if not url or url in {"unknown", "about:blank"}:
            return False

        parsed = urllib_parse.urlparse(url)
        host = (parsed.hostname or "").lower()
        path = parsed.path.lower()

        if host != "kleinanzeigen.de" and not host.endswith(".kleinanzeigen.de"):
            return False
        if host == "login.kleinanzeigen.de":
            return False
        if path.startswith("/u/login"):
            return False

        return "error" not in path

    async def fill_login_data_and_send(self) -> None:
        """Auth0 2-step login via m-einloggen-sso.html (server-side redirect, no JS needed).

        Step 1: /u/login/identifier - email
        Step 2: /u/login/password   - password
        """
        LOG.info("Logging in...")

        await self._wait_for_auth0_login_context()

        # Step 1: email identifier
        LOG.debug("Auth0 Step 1: entering email...")
        await self.web_input(By.ID, "username", self.config.login.username)
        await self.web_click(By.CSS_SELECTOR, "button[type='submit']")

        # Step 2: wait for password page then enter password
        LOG.debug("Waiting for Auth0 password page...")
        await self._wait_for_auth0_password_step()

        LOG.debug("Auth0 Step 2: entering password...")
        await self.web_input(By.CSS_SELECTOR, "input[type='password']", self.config.login.password)
        await self.check_and_wait_for_captcha(is_login_page = True)
        await self.web_click(By.CSS_SELECTOR, "button[type='submit']")
        await self._wait_for_post_auth0_submit_transition()
        LOG.debug("Auth0 login submitted.")

    async def handle_after_login_logic(self) -> None:
        await self._check_sms_verification()
        await self._check_email_verification()
        LOG.debug("Handling GDPR disclaimer...")
        await self._click_gdpr_banner()

    async def _check_sms_verification(self) -> None:
        sms_timeout = self._timeout("sms_verification")
        element = await self.web_probe(By.TEXT, "Wir haben dir gerade einen 6-stelligen Code für die Telefonnummer", timeout = sms_timeout)
        if element is None:
            LOG.debug("No SMS verification prompt detected after login")
            return
        LOG.warning("############################################")
        LOG.warning("# Device verification message detected. Please follow the instruction displayed in the Browser.")
        LOG.warning("############################################")
        await ainput(_("Press ENTER when done..."))

    async def _dismiss_consent_banner(self) -> None:
        """Dismiss the GDPR/TCF consent banner if it is present.

        This banner can appear on any page navigation (not just after login) and blocks
        all form interaction until dismissed. Uses a short timeout to avoid slowing down
        the flow when the banner is already gone.
        """
        banner_timeout = self._timeout("quick_dom")
        element = await self.web_probe(By.ID, "gdpr-banner-accept", timeout = banner_timeout)
        if element is not None:
            LOG.debug("Consent banner detected, clicking 'Alle akzeptieren'...")
            await element.click()
            await self.web_sleep()
        else:
            LOG.debug("Consent banner not present; continuing without dismissal")

    async def _check_email_verification(self) -> None:
        email_timeout = self._timeout("email_verification")
        element = await self.web_probe(By.TEXT, "Um dein Konto zu schützen haben wir dir eine E-Mail geschickt", timeout = email_timeout)
        if element is None:
            LOG.debug("No email verification prompt detected after login")
            return
        LOG.warning("############################################")
        LOG.warning("# Device verification message detected. Please follow the instruction displayed in the Browser.")
        LOG.warning("############################################")
        await ainput(_("Press ENTER when done..."))

    async def _click_gdpr_banner(self, *, timeout:float | None = None) -> None:
        gdpr_timeout = self._timeout("quick_dom") if timeout is None else timeout
        element = await self.web_probe(By.ID, "gdpr-banner-accept", timeout = gdpr_timeout)
        if element is not None:
            await element.click()
            await self.web_sleep()
        else:
            LOG.debug("GDPR banner not present; continuing without click")

    async def get_login_state(self, *, capture_diagnostics:bool = True) -> LoginDetectionResult:
        """Determine login status using DOM-first detection and return result with reason.

        Order:
        1) DOM-based logged-in marker check
        2) Logged-out CTA check
        3) If inconclusive, optionally capture diagnostics and return a timeout reason
        """
        # Prefer DOM-based checks first to minimize bot-like behavior and avoid
        # fragile API probing side effects. Server-side auth probing was removed.
        if await self._has_logged_in_marker():
            return LoginDetectionResult(is_logged_in = True, reason = LoginDetectionReason.USER_INFO_MATCH)

        if await self._has_logged_out_cta(log_timeout = False):
            return LoginDetectionResult(is_logged_in = False, reason = LoginDetectionReason.CTA_MATCH)

        if capture_diagnostics:
            await self._capture_login_detection_diagnostics_if_enabled(
                base_prefix = "login_detection_selector_timeout",
                pause_banner_message = "# Login detection remained inconclusive. Browser is paused for manual inspection.",
            )
        return LoginDetectionResult(is_logged_in = False, reason = LoginDetectionReason.SELECTOR_TIMEOUT)

    def _diagnostics_output_dir(self) -> Path:
        diagnostics = getattr(self.config, "diagnostics", None)
        if diagnostics is not None and diagnostics.output_dir and diagnostics.output_dir.strip():
            return Path(abspath(diagnostics.output_dir, relative_to = self.config_file_path)).resolve()

        workspace = self._workspace_or_raise()
        xdg_paths.ensure_directory(workspace.diagnostics_dir, "diagnostics directory")
        return workspace.diagnostics_dir

    async def _capture_login_detection_diagnostics_if_enabled(
        self,
        *,
        base_prefix:str = "login_detection_inconclusive",
        pause_banner_message:str = "# Login detection remained inconclusive. Browser is paused for manual inspection.",
    ) -> None:
        cfg = getattr(self.config, "diagnostics", None)
        if cfg is None or not cfg.capture_on.login_detection:
            return

        if self._login_detection_diagnostics_captured:
            return

        page = getattr(self, "page", None)

        try:
            output_dir = self._diagnostics_output_dir()
        except Exception as exc:  # noqa: BLE001
            LOG.debug("Login diagnostics capture skipped (base_prefix=%s): %s", base_prefix, exc)
            return

        try:
            await diagnostics.capture_diagnostics(
                output_dir = output_dir,
                base_prefix = base_prefix,
                page = page,
                log_file_path = self.log_file_path,
                copy_log = cfg.capture_log_copy,
            )
        except Exception as exc:  # noqa: BLE001
            LOG.debug(
                "Login diagnostics capture failed (output_dir=%s, base_prefix=%s): %s",
                output_dir,
                base_prefix,
                exc,
            )
            return

        self._login_detection_diagnostics_captured = True

        if cfg.pause_on_login_detection_failure and getattr(sys.stdin, "isatty", lambda: False)():
            LOG.warning("############################################")
            LOG.warning(pause_banner_message)
            LOG.warning("############################################")
            await ainput(_("Press a key to continue..."))

    async def _capture_publish_error_diagnostics_if_enabled(
        self,
        ad_cfg:Ad,
        ad_cfg_orig:dict[str, Any],
        ad_file:str,
        attempt:int,
        exc:Exception,
    ) -> None:
        """Capture publish failure diagnostics when enabled and a page is available.

        Runs only if cfg.capture_on.publish is enabled and self.page is set.
        Uses the ad configuration and publish attempt details to write screenshot, HTML,
        JSON payload, and optional log copy for debugging.
        """
        cfg = getattr(self.config, "diagnostics", None)
        if cfg is None or not cfg.capture_on.publish:
            return

        page = getattr(self, "page", None)
        if page is None:
            return

        # Use the ad filename (without extension) as identifier
        ad_file_stem = Path(ad_file).stem

        json_payload = {
            "timestamp": misc.now().isoformat(timespec = "seconds"),
            "attempt": attempt,
            "page_url": getattr(page, "url", None),
            "exception": {
                "type": exc.__class__.__name__,
                "message": str(exc),
                "repr": repr(exc),
            },
            "ad_file": ad_file,
            "ad_title": ad_cfg.title,
            "ad_config_effective": ad_cfg.model_dump(mode = "json"),
            "ad_config_original": ad_cfg_orig,
        }

        try:
            await diagnostics.capture_diagnostics(
                output_dir = self._diagnostics_output_dir(),
                base_prefix = "publish_error",
                attempt = attempt,
                subject = ad_file_stem,
                page = page,
                json_payload = json_payload,
                log_file_path = self.log_file_path,
                copy_log = cfg.capture_log_copy,
            )
        except Exception as error:  # noqa: BLE001
            LOG.warning("Diagnostics capture failed during publish error handling: %s", error)

    async def _has_logged_in_marker(self) -> bool:
        # Use login_detection timeout (10s default) instead of default (5s)
        # to allow sufficient time for client-side JavaScript rendering after page load.
        # This is especially important for older sessions (20+ days) that require
        # additional server-side validation time.
        login_check_timeout = self._timeout("login_detection")
        effective_timeout = self._effective_timeout("login_detection")
        username = self.config.login.username.lower()
        LOG.debug(
            "Starting login detection (timeout: %.1fs base, %.1fs effective with multiplier/backoff)",
            login_check_timeout,
            effective_timeout,
        )
        quick_dom_timeout = self._timeout("quick_dom")
        tried_login_selectors = _format_login_detection_selectors(_LOGIN_DETECTION_SELECTORS)

        try:
            user_info, matched_selector = await self.web_text_first_available(
                _LOGIN_DETECTION_SELECTORS,
                timeout = quick_dom_timeout,
                key = "quick_dom",
                description = "login_detection(quick_logged_in)",
            )
            if username in user_info.lower():
                matched_selector_display = (
                    f"{_LOGIN_DETECTION_SELECTORS[matched_selector][0].name}={_LOGIN_DETECTION_SELECTORS[matched_selector][1]}"
                    if 0 <= matched_selector < len(_LOGIN_DETECTION_SELECTORS)
                    else f"selector_index_{matched_selector}"
                )
                LOG.debug("Login detected via login detection selector '%s'", matched_selector_display)
                return True
        except TimeoutError:
            LOG.debug("No login detected via configured login detection selectors (%s)", tried_login_selectors)

        try:
            user_info, matched_selector = await self.web_text_first_available(
                _LOGIN_DETECTION_SELECTORS,
                timeout = login_check_timeout,
                key = "login_detection",
                description = "login_detection(selector_group)",
            )
            if username in user_info.lower():
                matched_selector_display = (
                    f"{_LOGIN_DETECTION_SELECTORS[matched_selector][0].name}={_LOGIN_DETECTION_SELECTORS[matched_selector][1]}"
                    if 0 <= matched_selector < len(_LOGIN_DETECTION_SELECTORS)
                    else f"selector_index_{matched_selector}"
                )
                LOG.debug("Login detected via login detection selector '%s'", matched_selector_display)
                return True
        except TimeoutError:
            LOG.debug("Timeout waiting for login detection selector group after %.1fs", effective_timeout)

        return False

    async def is_logged_in(self) -> bool:
        if await self._has_logged_in_marker():
            return True

        tried_login_selectors = _format_login_detection_selectors(_LOGIN_DETECTION_SELECTORS)

        LOG.debug("No login detected via configured login detection selectors (%s)", tried_login_selectors)
        return False

    # NOTE: Treats any matched CTA selector with non-empty text as logged-out evidence.
    # Does NOT verify visibility (hidden/footer/off-canvas links could theoretically match).
    # PR #870 verified these selectors work correctly in practice.
    # If false positives occur, harden by adding web_check(Is.DISPLAYED) on cta_element.
    # See issue #876.
    async def _has_logged_out_cta(self, *, log_timeout:bool = True) -> bool:
        quick_dom_timeout = self._timeout("quick_dom")
        tried_logged_out_selectors = _format_login_detection_selectors(_LOGGED_OUT_CTA_SELECTORS)

        try:
            cta_element, cta_index = await self.web_find_first_available(
                _LOGGED_OUT_CTA_SELECTORS,
                timeout = quick_dom_timeout,
                key = "quick_dom",
                description = "login_detection(logged_out_cta)",
            )
            cta_text = await self._extract_visible_text(cta_element)
            if cta_text.strip():
                matched_selector_display = (
                    f"{_LOGGED_OUT_CTA_SELECTORS[cta_index][0].name}={_LOGGED_OUT_CTA_SELECTORS[cta_index][1]}"
                    if 0 <= cta_index < len(_LOGGED_OUT_CTA_SELECTORS)
                    else f"selector_index_{cta_index}"
                )
                if 0 <= cta_index < len(_LOGGED_OUT_CTA_SELECTORS):
                    LOG.debug("Fast logged-out pre-check matched selector '%s'", matched_selector_display)
                    return True
                LOG.debug("Fast logged-out pre-check got unexpected selector index '%s'; failing closed", cta_index)
                return False
        except TimeoutError:
            if log_timeout:
                LOG.debug(
                    "Fast logged-out pre-check found no login CTA (%s) within %.1fs",
                    tried_logged_out_selectors,
                    quick_dom_timeout,
                )

        return False

    async def _fetch_published_ads(self, *, strict:bool = False) -> list[dict[str, Any]]:
        """Fetch all published ads, handling API pagination.

        Args:
            strict: If True, raise PublishedAdsFetchIncompleteError when pagination data is incomplete.

        Returns:
            List of all published ads across all pages.
        """
        ads:list[dict[str, Any]] = []
        page = 1
        MAX_PAGE_LIMIT:Final[int] = 100
        SNIPPET_LIMIT:Final[int] = 500

        def _handle_incomplete_fetch(template:str, *args:Any, cause:Exception | None = None) -> None:
            if strict:
                raise PublishedAdsFetchIncompleteError(_(template) % args) from cause

        while True:
            # Safety check: don't paginate beyond reasonable limit
            if page > MAX_PAGE_LIMIT:
                LOG.warning("Stopping pagination after %s pages to avoid infinite loop", MAX_PAGE_LIMIT)
                _handle_incomplete_fetch("Stopping pagination after %s pages to avoid infinite loop", MAX_PAGE_LIMIT)
                break

            try:
                response = await self.web_request(f"{self.root_url}/m-meine-anzeigen-verwalten.json?sort=DEFAULT&pageNum={page}")
            except TimeoutError as ex:
                LOG.warning("Pagination request failed on page %s: %s", page, ex)
                _handle_incomplete_fetch("Pagination request failed on page %s: %s", page, ex, cause = ex)
                break

            if not isinstance(response, dict):
                LOG.warning("Unexpected pagination response type on page %s: %s", page, type(response).__name__)
                _handle_incomplete_fetch("Unexpected pagination response type on page %s: %s", page, type(response).__name__)
                break

            content = response.get("content", "")
            if isinstance(content, bytearray):
                content = bytes(content)
            if isinstance(content, bytes):
                content = content.decode("utf-8", errors = "replace")
            if not isinstance(content, str):
                LOG.warning("Unexpected response content type on page %s: %s", page, type(content).__name__)
                _handle_incomplete_fetch("Unexpected response content type on page %s: %s", page, type(content).__name__)
                break

            try:
                json_data = json.loads(content)
            except (json.JSONDecodeError, TypeError) as ex:
                if not content:
                    LOG.warning("Empty JSON response content on page %s", page)
                    _handle_incomplete_fetch("Empty JSON response content on page %s", page, cause = ex)
                    break
                snippet = content[:SNIPPET_LIMIT] + ("..." if len(content) > SNIPPET_LIMIT else "")
                LOG.warning("Failed to parse JSON response on page %s: %s (content: %s)", page, ex, snippet)
                _handle_incomplete_fetch("Failed to parse JSON response on page %s: %s (content: %s)", page, ex, snippet, cause = ex)
                break

            if not isinstance(json_data, dict):
                snippet = content[:SNIPPET_LIMIT] + ("..." if len(content) > SNIPPET_LIMIT else "")
                LOG.warning("Unexpected JSON payload on page %s (content: %s)", page, snippet)
                _handle_incomplete_fetch("Unexpected JSON payload on page %s (content: %s)", page, snippet)
                break

            page_ads = json_data.get("ads", [])
            if not isinstance(page_ads, list):
                preview = str(page_ads)
                if len(preview) > SNIPPET_LIMIT:
                    preview = preview[:SNIPPET_LIMIT] + "..."
                LOG.warning("Unexpected 'ads' type on page %s: %s value: %s", page, type(page_ads).__name__, preview)
                _handle_incomplete_fetch("Unexpected 'ads' type on page %s: %s value: %s", page, type(page_ads).__name__, preview)
                break

            filtered_page_ads:list[dict[str, Any]] = []
            rejected_count = 0
            rejected_preview:str | None = None
            for entry in page_ads:
                if isinstance(entry, dict) and "id" in entry and "state" in entry:
                    filtered_page_ads.append(entry)
                    continue
                rejected_count += 1
                if rejected_preview is None:
                    rejected_preview = repr(entry)

            if rejected_count > 0:
                preview = rejected_preview or "<none>"
                if len(preview) > SNIPPET_LIMIT:
                    preview = preview[:SNIPPET_LIMIT] + "..."
                LOG.warning("Filtered %s malformed ad entries on page %s (sample: %s)", rejected_count, page, preview)
                _handle_incomplete_fetch("Filtered %s malformed ad entries on page %s (sample: %s)", rejected_count, page, preview)

            ads.extend(filtered_page_ads)

            paging = json_data.get("paging")
            if not isinstance(paging, dict):
                LOG.debug("No paging dict found on page %s, assuming single page", page)
                break

            # Use only real API fields (confirmed from production data)
            current_page_num = misc.coerce_page_number(paging.get("pageNum"))
            total_pages = misc.coerce_page_number(paging.get("last"))

            if current_page_num is None:
                LOG.warning("Invalid 'pageNum' in paging info: %s, stopping pagination", paging.get("pageNum"))
                _handle_incomplete_fetch("Invalid 'pageNum' in paging info: %s, stopping pagination", paging.get("pageNum"))
                break

            # Stop if reached last page (only when API provides 'last')
            if total_pages is not None and current_page_num >= total_pages:
                LOG.info("Reached last page %s of %s, stopping pagination", current_page_num, total_pages)
                break

            # Safety: stop if no ads returned
            if len(page_ads) == 0:
                LOG.info("No ads found on page %s, stopping pagination", page)
                break

            LOG.debug("Page %s: fetched %s ads (numFound=%s)", page, len(page_ads), paging.get("numFound"))

            # Use API's next field for navigation (more robust than our counter)
            next_page = misc.coerce_page_number(paging.get("next"))
            if next_page is None:
                if total_pages is not None:
                    LOG.warning("Invalid 'next' page value in paging info: %s, stopping pagination", paging.get("next"))
                    _handle_incomplete_fetch("Invalid 'next' page value in paging info: %s, stopping pagination", paging.get("next"))
                else:
                    LOG.debug("No 'next' in paging on page %s, assuming last page", page)
                    _handle_incomplete_fetch("No 'next' in paging on page %s, assuming last page", page)
                break
            page = next_page

        return ads

    async def delete_ads(self, ad_cfgs:list[tuple[str, Ad, dict[str, Any]]]) -> None:
        count = 0

        published_ads = await self._fetch_published_ads()

        for ad_file, ad_cfg, _ad_cfg_orig in ad_cfgs:
            count += 1
            LOG.info("Processing %s/%s: '%s' from [%s]...", count, len(ad_cfgs), ad_cfg.title, ad_file)
            await self.delete_ad(ad_cfg, published_ads, delete_old_ads_by_title = self.config.publishing.delete_old_ads_by_title)
            await self.web_sleep()

        LOG.info("############################################")
        LOG.info("DONE: Deleted %s", pluralize("ad", count))
        LOG.info("############################################")

    async def delete_ad(self, ad_cfg:Ad, published_ads:list[dict[str, Any]], *, delete_old_ads_by_title:bool) -> bool:
        LOG.info("Deleting ad '%s' if already present...", ad_cfg.title)

        await self.web_open(f"{self.root_url}/m-meine-anzeigen.html")
        csrf_token_elem = await self.web_find(By.CSS_SELECTOR, "meta[name=_csrf]")
        csrf_token = csrf_token_elem.attrs["content"]
        ensure(csrf_token is not None, "Expected CSRF Token not found in HTML content!")

        if delete_old_ads_by_title:
            for published_ad in published_ads:
                published_ad_id = int(published_ad.get("id", -1))
                published_ad_title = published_ad.get("title", "")
                if ad_cfg.id == published_ad_id or ad_cfg.title == published_ad_title:
                    LOG.info(" -> deleting %s '%s'...", published_ad_id, published_ad_title)
                    await self.web_request(
                        url = f"{self.root_url}/m-anzeigen-loeschen.json?ids={published_ad_id}", method = "POST", headers = {"x-csrf-token": str(csrf_token)}
                    )
        elif ad_cfg.id:
            await self.web_request(
                url = f"{self.root_url}/m-anzeigen-loeschen.json?ids={ad_cfg.id}",
                method = "POST",
                headers = {"x-csrf-token": str(csrf_token)},
                valid_response_codes = [200, 404],
            )

        await self.web_sleep()
        ad_cfg.id = None
        return True

    async def extend_ads(self, ad_cfgs:list[tuple[str, Ad, dict[str, Any]]]) -> None:
        """Extends ads that are close to expiry."""
        # Fetch currently published ads from API
        published_ads = await self._fetch_published_ads()

        # Filter ads that need extension
        ads_to_extend = []
        for ad_file, ad_cfg, ad_cfg_orig in ad_cfgs:
            # Skip unpublished ads (no ID)
            if not ad_cfg.id:
                LOG.info(" -> SKIPPED: ad '%s' is not published yet", ad_cfg.title)
                continue

            # Find ad in published list
            published_ad = next((ad for ad in published_ads if ad["id"] == ad_cfg.id), None)
            if not published_ad:
                LOG.warning(" -> SKIPPED: ad '%s' (ID: %s) not found in published ads", ad_cfg.title, ad_cfg.id)
                continue

            # Skip non-active ads
            if published_ad.get("state") != "active":
                LOG.info(" -> SKIPPED: ad '%s' is not active (state: %s)", ad_cfg.title, published_ad.get("state"))
                continue

            # Check if ad is within 8-day extension window using API's endDate
            end_date_str = published_ad.get("endDate")
            if not end_date_str:
                LOG.warning(" -> SKIPPED: ad '%s' has no endDate in API response", ad_cfg.title)
                continue

            # Intentionally parsing naive datetime from kleinanzeigen API's German date format, timezone not relevant for date-only comparison
            end_date = datetime.strptime(end_date_str, "%d.%m.%Y")  # noqa: DTZ007
            days_until_expiry = (end_date.date() - misc.now().date()).days

            # Magic value 8 is kleinanzeigen.de's platform policy: extensions only possible within 8 days of expiry
            if days_until_expiry <= 8:  # noqa: PLR2004
                LOG.info(" -> ad '%s' expires in %d days, will extend", ad_cfg.title, days_until_expiry)
                ads_to_extend.append((ad_file, ad_cfg, ad_cfg_orig, published_ad))
            else:
                LOG.info(" -> SKIPPED: ad '%s' expires in %d days (can only extend within 8 days)", ad_cfg.title, days_until_expiry)

        if not ads_to_extend:
            LOG.info("No ads need extension at this time.")
            LOG.info("############################################")
            LOG.info("DONE: No ads extended.")
            LOG.info("############################################")
            return

        # Process extensions
        success_count = 0
        for idx, (ad_file, ad_cfg, ad_cfg_orig, _published_ad) in enumerate(ads_to_extend, start = 1):
            LOG.info("Processing %s/%s: '%s' from [%s]...", idx, len(ads_to_extend), ad_cfg.title, ad_file)
            if await self.extend_ad(ad_file, ad_cfg, ad_cfg_orig):
                success_count += 1
            await self.web_sleep()

        LOG.info("############################################")
        LOG.info("DONE: Extended %s", pluralize("ad", success_count))
        LOG.info("############################################")

    async def extend_ad(self, ad_file:str, ad_cfg:Ad, ad_cfg_orig:dict[str, Any]) -> bool:
        """Extends a single ad listing."""
        LOG.info("Extending ad '%s' (ID: %s)...", ad_cfg.title, ad_cfg.id)

        try:
            # Navigate to ad management page and find extend button across all pages
            extend_button_xpath = f'//li[@data-adid="{ad_cfg.id}"]//button[contains(., "Verlängern")]'

            async def find_and_click_extend_button(page_num:int) -> bool:
                """Try to find and click extend button on current page."""
                try:
                    extend_button = await self.web_find(By.XPATH, extend_button_xpath, timeout = self._timeout("quick_dom"))
                    LOG.info("Found extend button on page %s", page_num)
                    await extend_button.click()
                    return True  # Success - stop pagination
                except TimeoutError:
                    LOG.debug("Extend button not found on page %s", page_num)
                    return False  # Continue to next page

            success = await self._navigate_paginated_ad_overview(find_and_click_extend_button, page_url = f"{self.root_url}/m-meine-anzeigen.html")

            if not success:
                LOG.error(" -> FAILED: Could not find extend button for ad ID %s", ad_cfg.id)
                return False

            # Handle confirmation dialog
            # After clicking "Verlängern", a dialog appears with:
            # - Title: "Vielen Dank!"
            # - Message: "Deine Anzeige ... wurde erfolgreich verlängert."
            # - Paid bump-up option (skipped by closing dialog)
            # Simply close the dialog with the X button (aria-label="Schließen")
            try:
                dialog_close_timeout = self._timeout("quick_dom")
                await self.web_click(By.CSS_SELECTOR, 'button[aria-label="Schließen"]', timeout = dialog_close_timeout)
                LOG.debug(" -> Closed confirmation dialog")
            except TimeoutError:
                LOG.warning(" -> No confirmation dialog found, extension may have completed directly")

            # Update metadata in YAML file
            # Update updated_on to track when ad was extended
            ad_cfg_orig["updated_on"] = misc.now().isoformat(timespec = "seconds")
            dicts.save_dict(ad_file, ad_cfg_orig)

            LOG.info(" -> SUCCESS: ad extended with ID %s", ad_cfg.id)
            return True

        except TimeoutError as ex:
            LOG.error(" -> FAILED: Timeout while extending ad '%s': %s", ad_cfg.title, ex)
            return False
        except OSError as ex:
            LOG.error(" -> FAILED: Could not persist extension for ad '%s': %s", ad_cfg.title, ex)
            return False

    async def __check_publishing_result(self) -> bool:
        # Check for success messages
        return await self.web_check(By.ID, "checking-done", Is.DISPLAYED) or await self.web_check(By.ID, "not-completed", Is.DISPLAYED)

    async def publish_ads(self, ad_cfgs:list[tuple[str, Ad, dict[str, Any]]]) -> None:
        count = 0
        failed_count = 0
        max_retries = SUBMISSION_MAX_RETRIES

        published_ads = await self._fetch_published_ads()

        for idx, (ad_file, ad_cfg, ad_cfg_orig) in enumerate(ad_cfgs, start = 1):
            LOG.info("Processing %s/%s: '%s' from [%s]...", idx, len(ad_cfgs), ad_cfg.title, ad_file)

            if [x for x in published_ads if x["id"] == ad_cfg.id and x["state"] == "paused"]:
                LOG.info("Skipping because ad is reserved")
                continue

            count += 1
            success = False
            baseline_price = ad_cfg.price
            baseline_price_reduction_count = ad_cfg.price_reduction_count

            for attempt in range(1, max_retries + 1):
                try:
                    # publish_ad mutates pricing fields before submit; reset them so retries
                    # remain idempotent for a single eligible reduction cycle.
                    ad_cfg.price = baseline_price
                    ad_cfg.price_reduction_count = baseline_price_reduction_count
                    await self.publish_ad(ad_file, ad_cfg, ad_cfg_orig, published_ads, AdUpdateStrategy.REPLACE)
                    success = True
                    break  # Publish succeeded, exit retry loop
                except asyncio.CancelledError:
                    raise  # Respect task cancellation
                except PublishSubmissionUncertainError as ex:
                    await self._capture_publish_error_diagnostics_if_enabled(ad_cfg, ad_cfg_orig, ad_file, attempt, ex)
                    LOG.warning(
                        "Attempt %s/%s for '%s' reached submit boundary but failed: %s. Not retrying to prevent duplicate listings.",
                        attempt,
                        max_retries,
                        ad_cfg.title,
                        ex,
                    )
                    LOG.warning("Manual recovery required for '%s'. Check 'Meine Anzeigen' to confirm whether the ad was posted.", ad_cfg.title)
                    LOG.warning(
                        "If posted, sync local state with 'kleinanzeigen-bot download --ads=new' or 'kleinanzeigen-bot download --ads=<id>'; "
                        "otherwise rerun publish for this ad."
                    )
                    failed_count += 1
                    break
                except (TimeoutError, ProtocolException) as ex:
                    await self._capture_publish_error_diagnostics_if_enabled(ad_cfg, ad_cfg_orig, ad_file, attempt, ex)
                    if attempt >= max_retries:
                        LOG.error("All %s attempts failed for '%s': %s. Skipping ad.", max_retries, ad_cfg.title, ex)
                        failed_count += 1
                        break

                    LOG.warning("Attempt %s/%s failed for '%s': %s. Retrying...", attempt, max_retries, ad_cfg.title, ex)
                    await self.web_sleep(2_000)  # Wait before retry

            # Check publishing result separately (no retry - ad is already submitted)
            if success:
                try:
                    publish_timeout = self._timeout("publishing_result")
                    await self.web_await(self.__check_publishing_result, timeout = publish_timeout)
                except TimeoutError:
                    LOG.warning(" -> Could not confirm publishing for '%s', but ad may be online", ad_cfg.title)

            if success and self.config.publishing.delete_old_ads == "AFTER_PUBLISH" and not self.keep_old_ads:
                await self.delete_ad(ad_cfg, published_ads, delete_old_ads_by_title = False)

        LOG.info("############################################")
        if failed_count > 0:
            LOG.info("DONE: (Re-)published %s (%s failed after retries)", pluralize("ad", count - failed_count), failed_count)
        else:
            LOG.info("DONE: (Re-)published %s", pluralize("ad", count))
        LOG.info("############################################")

    async def publish_ad(
        self, ad_file:str, ad_cfg:Ad, ad_cfg_orig:dict[str, Any], published_ads:list[dict[str, Any]], mode:AdUpdateStrategy = AdUpdateStrategy.REPLACE
    ) -> None:
        """Publish or update an ad on Kleinanzeigen.

        Args:
            ad_file: Path to the ad configuration YAML file.
            ad_cfg: The effective ad configuration with default values applied.
            ad_cfg_orig: The original ad configuration as present in the YAML file.
            published_ads: List of published ads from the API, used for deduplication
                and old ad deletion.
            mode: The ad editing strategy. REPLACE creates a new ad (full republish),
                MODIFY updates an existing ad in-place.

        Returns:
            None
        """

        if mode == AdUpdateStrategy.REPLACE:
            if self.config.publishing.delete_old_ads == "BEFORE_PUBLISH" and not self.keep_old_ads:
                await self.delete_ad(ad_cfg, published_ads, delete_old_ads_by_title = self.config.publishing.delete_old_ads_by_title)

            # Apply auto price reduction in REPLACE mode (republish flow)
            apply_auto_price_reduction(ad_cfg, ad_cfg_orig, _relative_ad_path(ad_file, self.config_file_path), mode = AdUpdateStrategy.REPLACE)

            LOG.info("Publishing ad '%s'...", ad_cfg.title)
            await self.web_open(f"{self.root_url}/p-anzeige-aufgeben-schritt2.html", reload_if_already_open = True)
        else:
            # Always run restore-first when enabled so previously applied reductions
            # are restored even when on_update is false.  The evaluator handles
            # the on_update guard internally (returns early without advancing).
            if ad_cfg.auto_price_reduction.enabled:
                apply_auto_price_reduction(ad_cfg, ad_cfg_orig, _relative_ad_path(ad_file, self.config_file_path), mode = AdUpdateStrategy.MODIFY)

            LOG.info("Updating ad '%s'...", ad_cfg.title)
            await self.web_open(f"{self.root_url}/p-anzeige-bearbeiten.html?adId={ad_cfg.id}", reload_if_already_open = True)

        await self._dismiss_consent_banner()

        if loggers.is_debug(LOG):
            LOG.debug(" -> effective ad meta:")
            YAML().dump(ad_cfg.model_dump(), sys.stdout)

        if ad_cfg.type == "WANTED":
            await self.web_click(By.ID, "ad-type-WANTED")

        #############################
        # set category (before title to avoid form reset clearing title)
        #############################
        await self.__set_category(ad_cfg.category, ad_file)
        await self.web_sleep()  # wait for category-dependent fields to render before setting attributes

        #############################
        # set special attributes
        #############################
        await self.__set_special_attributes(ad_cfg)

        #############################
        # set shipping type/options/costs
        #############################
        shipping_type = ad_cfg.shipping_type
        if shipping_type != "NOT_APPLICABLE":
            if ad_cfg.type == "WANTED":
                # WANTED ads render shipping as a special-attribute combobox dropdown,
                # not as radio buttons.  Select by display text using the standard
                # DOM-based web_select_button_combobox (no React fiber internals).
                # See issue #930 for broader React fiber migration.
                display_text = _WANTED_SHIPPING_LABELS.get(shipping_type)
                if display_text:
                    try:
                        shipping_btn = await self.web_find(
                            By.CSS_SELECTOR,
                            '[role="combobox"][id$=".versand"]',
                            timeout = self._timeout("quick_dom"),
                        )
                        btn_id = cast(str, shipping_btn.attrs.get("id"))
                        if not btn_id:
                            raise TimeoutError(_("Shipping combobox button has no id attribute"))
                        await self.web_select_button_combobox(btn_id, display_text)
                    except TimeoutError as ex:
                        LOG.warning("Failed to set shipping attribute for type '%s'!", shipping_type)
                        raise TimeoutError(_("Failed to set shipping attribute for type '%s'!") % shipping_type) from ex
            else:
                await self.__set_shipping(ad_cfg, mode)
        else:
            LOG.debug("Shipping step skipped - reason: NOT_APPLICABLE")

        #############################
        # set price
        #############################
        price_type = ad_cfg.price_type
        if price_type != "NOT_APPLICABLE":
            price_type_options = {"FIXED": 0, "NEGOTIABLE": 1, "GIVE_AWAY": 2}
            option_idx = price_type_options.get(price_type)
            if option_idx is not None:
                try:
                    await self.web_click(By.ID, "ad-price-type")
                    await self.web_click(By.ID, f"ad-price-type-menu-option-{option_idx}")
                except TimeoutError as ex:
                    raise TimeoutError(_("Failed to set price type '%s'") % price_type) from ex
            if ad_cfg.price is not None:
                await self.__set_input_value("ad-price-amount", str(ad_cfg.price))

        #############################
        # set sell_directly
        #############################
        if ad_cfg.type != "WANTED":
            sell_directly = ad_cfg.sell_directly
            quick_dom = self._timeout("quick_dom")
            if ad_cfg.shipping_type == "SHIPPING":
                if sell_directly and price_type in {"FIXED", "NEGOTIABLE"}:
                    buy_now_true = await self.web_probe(By.ID, "ad-buy-now-true", timeout = quick_dom)
                    if buy_now_true is None:
                        raise TimeoutError(_("Failed to enable direct-buy option: required control is not available."))
                    if not await self.web_check(By.ID, "ad-buy-now-true", Is.SELECTED, timeout = quick_dom):
                        await self.web_click(By.ID, "ad-buy-now-true", timeout = quick_dom)
                else:
                    buy_now_false = await self.web_probe(By.ID, "ad-buy-now-false", timeout = quick_dom)
                    if buy_now_false and not await self.web_check(By.ID, "ad-buy-now-false", Is.SELECTED, timeout = quick_dom):
                        await self.web_click(By.ID, "ad-buy-now-false", timeout = quick_dom)
            else:
                # For PICKUP/other types: always opt out of buy-now if the radio exists
                buy_now_false = await self.web_probe(By.ID, "ad-buy-now-false", timeout = quick_dom)
                if buy_now_false and not await self.web_check(By.ID, "ad-buy-now-false", Is.SELECTED, timeout = quick_dom):
                    await self.web_click(By.ID, "ad-buy-now-false", timeout = quick_dom)

        #############################
        # set description
        #############################
        description = self.__get_description(ad_cfg, with_affixes = True)
        await self.__set_input_value("ad-description", description)

        await self.__set_contact_fields(ad_cfg.contact)

        #############################
        # delete previous images to ensure a clean slate
        # (needed for MODIFY because we don't know which changed,
        #  and as defensive cleanup when the form is pre-populated with thumbnails)
        #############################
        remove_button_selector = "button[aria-label='Bild entfernen']"
        hidden_marker_selector = "input[name^='adImages'][name$='.url']"
        quick_dom = self._timeout("quick_dom")
        removed_count = 0

        try:
            existing_markers = await self._web_find_all_once(By.CSS_SELECTOR, hidden_marker_selector, quick_dom)
            existing_image_count = sum(1 for marker in existing_markers if _get_marker_value(marker))
        except TimeoutError:
            existing_image_count = 0

        if existing_image_count:
            for idx in range(existing_image_count):
                remove_btn = await self.web_probe(By.CSS_SELECTOR, remove_button_selector, timeout = quick_dom)
                if remove_btn is None:
                    raise TimeoutError(
                        _("Image cleanup failed before upload. Removed %(removed)d of %(total)d existing images.")
                        % {"removed": idx, "total": existing_image_count}
                    )
                await remove_btn.click()
                removed_count += 1
                await self.web_sleep(300, 500)

        if removed_count > 0:
            LOG.info(" -> removed %d existing image(s) before upload", removed_count)
            # Let async DOM updates settle before capturing hidden-marker baseline
            await self.web_sleep(200, 350)

        #############################
        # upload images
        #############################
        await self.__upload_images(ad_cfg)

        #############################
        # wait for captcha
        #############################
        operation_label = {
            AdUpdateStrategy.REPLACE: "publish",
            AdUpdateStrategy.MODIFY: "update",
        }.get(mode, mode.name.lower())
        await self.check_and_wait_for_captcha(is_login_page = False, page_context = f"{operation_label} operation")

        #############################
        # set title (right before submit to prevent React re-render clearing it)
        #############################
        LOG.debug("Setting title '%s' (deferred to prevent React re-render clearing it)", ad_cfg.title)
        await self.__set_input_value("ad-title", ad_cfg.title)

        #############################
        # submit
        #############################
        # Click is retryable — no submission can have occurred before this point.
        # Edit page uses 'Änderungen speichern' or 'Anzeige speichern'; publish page uses 'Anzeige aufgeben'
        await self.web_click(By.XPATH, "//button[contains(., 'Anzeige aufgeben') or contains(., 'Änderungen speichern') or contains(., 'Anzeige speichern')]")

        # Everything after the first click is uncertain: the ad may already have been submitted.
        ad_id:int | None = None
        try:
            quick_dom = self._timeout("quick_dom")

            imprint_btn = await self.web_probe(By.ID, "imprint-guidance-submit", timeout = quick_dom)
            if imprint_btn is not None:
                await imprint_btn.click()

            # check for no image question
            if not ad_cfg.images:
                image_hint_xpath = '//button[contains(., "Ohne Bild veröffentlichen")]'
                image_hint_button = await self.web_probe(By.XPATH, image_hint_xpath, timeout = quick_dom)
                if image_hint_button is not None:
                    await image_hint_button.click()

            #############################
            # wait for payment form if commercial account is used
            #############################
            payment_form = await self.web_probe(By.ID, "myftr-shppngcrt-frm", timeout = quick_dom)
            if payment_form is not None:
                LOG.warning("############################################")
                LOG.warning("# Payment form detected! Please proceed with payment.")
                LOG.warning("############################################")
                await self.web_scroll_page_down()
                await ainput(_("Press a key to continue..."))

            confirmation_timeout = self._timeout("publishing_confirmation")

            async def _check_confirmation_url() -> bool:
                url = str(await self.web_execute("window.location.href"))
                return "p-anzeige-aufgeben-bestaetigung.html?adId=" in url

            await self.web_await(_check_confirmation_url, timeout = confirmation_timeout)

            # extract the ad id from the URL's query parameter (use JS for fresh URL, not stale self.page.url)
            current_url = str(await self.web_execute("window.location.href"))
            current_url_query_params = urllib_parse.parse_qs(urllib_parse.urlparse(current_url).query)
            ad_id = int(current_url_query_params.get("adId", [])[0])

        except (TimeoutError, ProtocolException, IndexError, ValueError, TypeError) as ex:
            # The confirmation page may have auto-redirected before we could poll it,
            # or the URL was redirected between polling and extraction (race condition).
            # Try to recover the ad ID from tracking data on the current page.
            LOG.debug("Confirmation URL polling or extraction failed (%s), attempting tracking data fallback...", type(ex).__name__)
            try:
                ad_id = await self._try_recover_ad_id_from_redirect()
            except Exception as fallback_ex:  # noqa: BLE001
                LOG.debug("Tracking data fallback failed: %s", fallback_ex)

            if ad_id is None:
                raise PublishSubmissionUncertainError("submission may have succeeded before failure") from ex

            LOG.warning(
                "Confirmation page redirected too fast; extracted ad ID %s from page tracking data",
                ad_id,
            )

        # Defensive guard: ad_id must be set by now — either from the confirmation URL
        # (try block) or the tracking fallback (except block). The except block always
        # either sets ad_id or raises PublishSubmissionUncertainError, making this
        # unreachable in the current code. Guards against future regressions.
        if ad_id is None:
            msg = _("ad_id is unexpectedly None after confirmation flow for %s") % ad_file
            raise RuntimeError(msg)

        ad_cfg_orig["id"] = ad_id

        # Update content hash after successful publication
        # Calculate hash on original config to ensure consistent comparison on restart
        ad_cfg_orig["content_hash"] = AdPartial.model_validate(ad_cfg_orig).update_content_hash().content_hash
        ad_cfg_orig["updated_on"] = misc.now().isoformat(timespec = "seconds")
        if not ad_cfg.created_on and not ad_cfg.id:
            ad_cfg_orig["created_on"] = ad_cfg_orig["updated_on"]

        # Increment repost_count only for REPLACE operations (actual reposts)
        if mode == AdUpdateStrategy.REPLACE:
            # Increment repost_count after successful publish
            # Note: This happens AFTER publish, so price reduction logic (which runs before publish)
            # sees the count from the PREVIOUS run. This is intentional: the first publish uses
            # repost_count=0 (no reduction), the second publish uses repost_count=1 (first reduction), etc.
            current_reposts = int(ad_cfg_orig.get("repost_count", ad_cfg.repost_count or 0))
            ad_cfg_orig["repost_count"] = current_reposts + 1
            ad_cfg.repost_count = ad_cfg_orig["repost_count"]

        # Persist price_reduction_count after successful publish/update.
        # This ensures failed submissions don't incorrectly increment the reduction counter.
        if ad_cfg.price_reduction_count is not None and ad_cfg.price_reduction_count > 0:
            ad_cfg_orig["price_reduction_count"] = ad_cfg.price_reduction_count

        if mode == AdUpdateStrategy.REPLACE:
            LOG.info(" -> SUCCESS: ad published with ID %s", ad_id)
        else:
            LOG.info(" -> SUCCESS: ad updated with ID %s", ad_id)

        dicts.save_dict(ad_file, ad_cfg_orig)

    async def _try_recover_ad_id_from_redirect(self) -> int | None:
        """Try to extract the published ad ID from page tracking data.

        Used as a fallback when the confirmation page auto-redirects before
        the URL can be polled. Checks document.referrer first, then scans
        inline script content for the confirmation URL containing adId.

        Returns:
            The extracted ad ID, or None if no ad ID could be found.
        """
        # Layer 1: check document.referrer for the confirmation URL.
        # Note: referrer reflects the most recent navigation, so a stale ID from a
        # previous publish is not a concern — the publish flow navigates to the edit
        # page first, resetting the referrer before the confirmation redirect occurs.
        try:
            referrer = str(await self.web_execute("document.referrer") or "")
        except (TimeoutError, ProtocolException) as ex:
            LOG.debug("document.referrer lookup failed (%s), skipping to script scan", type(ex).__name__)
            referrer = ""

        if "p-anzeige-aufgeben-bestaetigung.html?adId=" in referrer:
            try:
                query = urllib_parse.parse_qs(urllib_parse.urlparse(referrer).query)
                ad_id_str = query.get("adId", [])[0]
                ad_id = int(ad_id_str)
                LOG.debug("Extracted ad ID %s from document.referrer fallback", ad_id)
                return ad_id
            except (IndexError, ValueError, TypeError):
                LOG.debug("Failed to parse ad ID from document.referrer: %s", referrer)

        # Layer 2: scan inline <script> tags for confirmation URL with adId
        try:
            script_content = str(await self.web_execute(
                "[...document.querySelectorAll('script')].map(s => s.textContent).join('\\n')"
            ) or "")
            match = re.search(r"p-anzeige-aufgeben-bestaetigung\.html\?adId=(\d+)", script_content)
            if match:
                ad_id = int(match.group(1))
                LOG.debug("Extracted ad ID %s from inline script fallback", ad_id)
                return ad_id
        except (TimeoutError, ProtocolException, ValueError, TypeError) as ex:
            LOG.debug("Script content scan failed (%s): %s", type(ex).__name__, ex)

        return None

    async def __set_input_value(self, element_id:str, value:str) -> None:
        """Sets a framework-controlled input value using the native DOM setter to trigger onChange."""
        await self.web_find(By.ID, element_id)  # raises TimeoutError if element is absent
        js_element_id = json.dumps(element_id)
        js_value = json.dumps(value)
        await self.web_execute(
            f"(function(id,v){{"
            "var el=document.getElementById(id);"
            "if(!el)return;"
            "var tag=el.tagName.toLowerCase();"
            "var proto=tag==='textarea'?window.HTMLTextAreaElement:window.HTMLInputElement;"
            "var setter=Object.getOwnPropertyDescriptor(proto.prototype,'value').set;"
            "setter.call(el,v);"
            "el.dispatchEvent(new Event('input',{bubbles:true}));"
            "el.dispatchEvent(new Event('change',{bubbles:true}));"
            f"}})({js_element_id},{js_value})"
        )

    @staticmethod
    def __location_matches_target(target:str, candidate:str | None) -> bool:
        if not candidate:
            return False

        normalized_target = " ".join(target.split()).casefold()
        normalized_candidate = " ".join(candidate.split()).casefold()
        if not normalized_target or not normalized_candidate:
            return False

        if normalized_target == normalized_candidate:
            return True

        if " - " in normalized_target:
            return False

        if normalized_candidate.startswith(f"{normalized_target} - "):
            return True

        candidate_city = normalized_candidate.rsplit(" - ", maxsplit = 1)[-1]
        return normalized_target == candidate_city

    async def __city_option_text(self, option:Element) -> str:
        text = str(getattr(option, "text", "") or "").strip()
        if text:
            return text
        try:
            return (await self._extract_visible_text(option)).strip()
        except TimeoutError:
            return ""

    async def __read_city_selection_text(self) -> str | None:
        city_timeout = self._timeout("default")
        quick_dom_timeout = self._timeout("quick_dom")
        try:
            city_element = await self.web_find(By.ID, "ad-city", timeout = city_timeout)
        except TimeoutError:
            return None
        if city_element is None:
            return None

        if city_element.local_name == "input":
            live_value = await city_element.apply("(elem) => (elem.value || '').trim()")
            if isinstance(live_value, str) and live_value.strip():
                return live_value

        try:
            selected_text = await self.web_text(By.ID, "ad-city-selected-option", timeout = quick_dom_timeout)
            if selected_text:
                return selected_text
        except TimeoutError:
            # #ad-city-selected-option may not exist in all DOM states; fall through to textContent
            pass

        live_text = await city_element.apply("(elem) => (elem.textContent || '').trim()")
        if isinstance(live_text, str) and live_text.strip():
            return live_text

        try:
            selected_text = await self.web_text(By.ID, "ad-city", timeout = quick_dom_timeout)
            if selected_text:
                return selected_text
        except TimeoutError:
            return None
        return None

    async def __select_city_combobox_option(self, target:str) -> None:
        quick_dom_timeout = self._timeout("quick_dom")
        city_flow_timeout = self._timeout("default")

        await self.web_click(By.ID, "ad-city", timeout = quick_dom_timeout)
        city_element = await self.web_find(By.ID, "ad-city", timeout = quick_dom_timeout)
        city_attrs = getattr(city_element, "attrs", None)
        listbox_id_raw = None
        if city_attrs is not None:
            listbox_id_raw = city_attrs.get("aria-controls") if hasattr(city_attrs, "get") else getattr(city_attrs, "aria-controls", None)
        listbox_id = next((candidate for candidate in str(listbox_id_raw or "").split() if candidate.strip()), "")
        if not listbox_id:
            listbox_id = "ad-city-menu"

        listbox_id_css = listbox_id.replace("\\", "\\\\").replace('"', '\\"')
        listbox_scope = f'[id="{listbox_id_css}"]'
        option_selector = (
            f"{listbox_scope} [role='option'], "
            f"{listbox_scope} li[aria-selected='true'], {listbox_scope} li[aria-selected='false'], "
            f"{listbox_scope} button[aria-selected='true'], {listbox_scope} button[aria-selected='false']"
        )

        candidates:list[Element] = []

        async def _options_available() -> bool:
            nonlocal candidates
            try:
                candidates = await self.web_find_all(By.CSS_SELECTOR, option_selector, timeout = quick_dom_timeout)
            except TimeoutError:
                candidates = []
            return bool(candidates)

        try:
            await self.web_await(_options_available, timeout = city_flow_timeout)
        except TimeoutError as ex:
            raise TimeoutError(_("City combobox options did not load for location: %s") % target) from ex

        def normalize(value:str) -> str:
            return " ".join(value.split()).casefold()

        target_norm = normalize(target)
        option_entries = [(candidate, normalize(await self.__city_option_text(candidate))) for candidate in candidates]

        exact_match = next((entry[0] for entry in option_entries if entry[1] == target_norm), None)
        city_matches:list[Element] = []
        prefix_matches:list[Element] = []
        if " - " not in target_norm:
            city_matches = [entry[0] for entry in option_entries if entry[1] and entry[1].rsplit(" - ", maxsplit = 1)[-1] == target_norm]
            prefix_matches = [entry[0] for entry in option_entries if entry[1].startswith(f"{target_norm} - ")]

        if exact_match is None and len(city_matches) > 1:
            raise TimeoutError(_("City combobox options are ambiguous for location: %s") % target)

        if exact_match is None and not city_matches and len(prefix_matches) > 1:
            raise TimeoutError(_("City combobox options are ambiguous for location: %s") % target)

        selected_option = exact_match or (city_matches[0] if city_matches else None) or (prefix_matches[0] if len(prefix_matches) == 1 else None)
        if selected_option is None:
            raise TimeoutError(_("No city combobox option matched location: %s") % target)

        await selected_option.click()

        async def _selection_converged() -> bool:
            selected_city = await self.__read_city_selection_text()
            return self.__location_matches_target(target, selected_city)

        try:
            await self.web_await(_selection_converged, timeout = city_flow_timeout)
        except TimeoutError as ex:
            raise TimeoutError(_("City selection did not converge for location: %s") % target) from ex

    async def __set_contact_location(self, location:str) -> None:
        target = location.strip()
        if not target:
            return

        selected_city = await self.__read_city_selection_text()
        if self.__location_matches_target(target, selected_city):
            return

        city_timeout = self._timeout("default")
        city_element = await self.web_find(By.ID, "ad-city", timeout = city_timeout)
        if city_element is None:
            raise TimeoutError(_("Unsupported city element type while setting contact location: <%s>") % "missing")
        city_tag = city_element.local_name
        city_role = str(city_element.attrs.get("role") or "").casefold()
        if city_tag != "button" or city_role != "combobox":
            raise TimeoutError(_("Unsupported city element type while setting contact location: <%s>") % city_tag)

        await self.__select_city_combobox_option(target)

    async def __set_contact_fields(self, contact:Contact) -> None:
        #############################
        # set contact zipcode + location
        #############################
        if contact.zipcode:
            try:
                await self.web_input(By.ID, "ad-zip-code", str(contact.zipcode))
            except TimeoutError as ex:
                LOG.warning("Could not set contact zipcode: %s (%s)", contact.zipcode, ex)
                raise TimeoutError(_("Failed to set contact zipcode: %s") % contact.zipcode) from ex

            if contact.location:
                await self.__set_contact_location(contact.location)

        #############################
        # set contact street
        #############################
        if contact.street:
            try:
                if await self.web_check(By.ID, "ad-street", Is.DISABLED):
                    await self.web_click(By.ID, "ad-address-visibility")
                    await self.web_sleep()
                await self.__set_input_value("ad-street", contact.street)
            except TimeoutError:
                LOG.warning("Could not set contact street.")

        #############################
        # set contact name
        #############################
        if contact.name:
            try:
                if not await self.web_check(By.ID, "ad-name", Is.READONLY):
                    await self.__set_input_value("ad-name", contact.name)
            except TimeoutError:
                LOG.warning("Could not set contact name.")

        #############################
        # set contact phone
        #############################
        if contact.phone:
            phone_elem = await self.web_probe(By.ID, "ad-phone", timeout = self._timeout("quick_dom"))
            if phone_elem is None:
                LOG.info(
                    "Phone number field not present on page. This is expected for many private accounts; commercial accounts may still support phone numbers."
                )
            else:
                try:
                    if await self.web_check(By.ID, "ad-phone", Is.DISABLED, timeout = self._timeout("quick_dom")):
                        await self.web_click(By.ID, "ad-phone-visibility", timeout = self._timeout("quick_dom"))
                        await self.web_sleep()
                    await self.__set_input_value("ad-phone", contact.phone)
                except TimeoutError as ex:
                    LOG.warning("Could not set contact phone despite visible phone field: %s", ex)

    async def update_ads(self, ad_cfgs:list[tuple[str, Ad, dict[str, Any]]]) -> None:
        """
        Updates a list of ads.
        The list gets filtered, so that only already published ads will be updated.
        Calls publish_ad in MODIFY mode.

        Args:
            ad_cfgs: List of ad configurations

        Returns:
            None
        """
        count = 0
        failed_count = 0
        max_retries = SUBMISSION_MAX_RETRIES

        published_ads = await self._fetch_published_ads()

        for idx, (ad_file, ad_cfg, ad_cfg_orig) in enumerate(ad_cfgs, start = 1):
            LOG.info("Processing %s/%s: '%s' from [%s]...", idx, len(ad_cfgs), ad_cfg.title, ad_file)

            ad = next((ad for ad in published_ads if ad["id"] == ad_cfg.id), None)

            if not ad:
                LOG.warning(" -> SKIPPED: ad '%s' (ID: %s) not found in published ads", ad_cfg.title, ad_cfg.id)
                continue

            if ad["state"] == "paused":
                LOG.info("Skipping because ad is reserved")
                continue

            count += 1
            success = False
            baseline_price = ad_cfg.price
            baseline_price_reduction_count = ad_cfg.price_reduction_count

            for attempt in range(1, max_retries + 1):
                try:
                    ad_cfg.price = baseline_price
                    ad_cfg.price_reduction_count = baseline_price_reduction_count
                    await self.publish_ad(ad_file, ad_cfg, ad_cfg_orig, published_ads, AdUpdateStrategy.MODIFY)
                    success = True
                    break
                except asyncio.CancelledError:
                    raise
                except PublishSubmissionUncertainError as ex:
                    await self._capture_publish_error_diagnostics_if_enabled(ad_cfg, ad_cfg_orig, ad_file, attempt, ex)
                    LOG.warning(
                        "Attempt %s/%s for '%s' reached submit boundary but failed: %s. Not retrying to prevent duplicate modifications.",
                        attempt,
                        max_retries,
                        ad_cfg.title,
                        ex,
                    )
                    LOG.warning("Manual recovery required for '%s'. Check 'Meine Anzeigen' to confirm whether the update was applied.", ad_cfg.title)
                    failed_count += 1
                    break
                except (TimeoutError, ProtocolException) as ex:
                    await self._capture_publish_error_diagnostics_if_enabled(ad_cfg, ad_cfg_orig, ad_file, attempt, ex)
                    if attempt >= max_retries:
                        LOG.error("All %s attempts failed for '%s': %s. Skipping ad.", max_retries, ad_cfg.title, ex)
                        failed_count += 1
                        break

                    LOG.warning("Attempt %s/%s failed for '%s': %s. Retrying...", attempt, max_retries, ad_cfg.title, ex)
                    await self.web_sleep(2_000)

            if success:
                try:
                    publish_timeout = self._timeout("publishing_result")
                    await self.web_await(self.__check_publishing_result, timeout = publish_timeout)
                except TimeoutError:
                    LOG.warning(" -> Could not confirm update for '%s', but changes may be online", ad_cfg.title)

        LOG.info("############################################")
        if failed_count > 0:
            LOG.info("DONE: updated %s (%s failed after retries)", pluralize("ad", count - failed_count), failed_count)
        else:
            LOG.info("DONE: updated %s", pluralize("ad", count))
        LOG.info("############################################")

    async def __set_condition(self, condition_value:str) -> bool:
        """Try to set condition via dialog path.

        Returns True when dialog handling succeeded, otherwise False to indicate
        that caller should use generic special-attribute handling.
        """
        short_timeout = self._timeout("quick_dom")
        condition_trigger_xpath = "//label[contains(@for, '.condition')]/following::button[@aria-haspopup='dialog' or @aria-haspopup='true'][1]"

        condition_trigger = await self.web_probe(By.XPATH, condition_trigger_xpath, timeout = short_timeout)
        if condition_trigger is None:
            LOG.debug("Condition dialog trigger not available for [%s]; falling back to generic handler.", condition_value)
            return False

        trigger_id = str(condition_trigger.attrs.get("id") or "")
        trigger_controls = str(condition_trigger.attrs.get("aria-controls") or "")
        LOG.debug("Condition dialog trigger resolved: id='%s', aria-controls='%s'", trigger_id, trigger_controls)

        # Some categories render condition as a combobox and the broad dialog-trigger XPath
        # may accidentally resolve to shipping controls (for example: id='ad-shipping-options').
        # In that case we deliberately skip the dialog path and fall back to generic handling.
        if "shipping" in trigger_id.lower() or "shipping" in trigger_controls.lower():
            LOG.debug(
                "Condition dialog trigger appears to be shipping-related (id='%s', aria-controls='%s'); skipping dialog path for condition_s.",
                trigger_id,
                trigger_controls,
            )
            return False

        try:
            await condition_trigger.click()
            await self.web_find(By.XPATH, '//*[self::dialog or @role="dialog"]', timeout = short_timeout)
            condition_radio = await self.web_find(
                By.XPATH,
                f"//*[self::dialog or @role='dialog']//input[@type='radio' and @value={_xpath_literal(condition_value)}]",
                timeout = short_timeout,
            )
            condition_radio_id = str(condition_radio.attrs.get("id") or "")
            if condition_radio_id:
                try:
                    await self.web_click(By.XPATH, f"//*[self::dialog or @role='dialog']//label[@for={_xpath_literal(condition_radio_id)}]")
                except TimeoutError:
                    await condition_radio.click()
            else:
                await condition_radio.click()
        except TimeoutError as ex:
            LOG.debug("Unable to select condition [%s]", condition_value, exc_info = True)
            raise TimeoutError(_("Failed to set attribute '%s'") % "condition_s") from ex

        try:
            # Click accept button
            await self.web_click(By.XPATH, '//*[self::dialog or @role="dialog"]//button[.//span[text()="Bestätigen"]]')
        except TimeoutError as ex:
            raise TimeoutError(_("Unable to close condition dialog!")) from ex

        return True

    async def __set_category(self, category:str | None, ad_file:str) -> None:
        # click on something to trigger automatic category detection
        await self.web_click(By.ID, "ad-description")

        is_category_auto_selected = False
        category_path_elem = await self.web_probe(By.ID, "ad-category-path")
        if category_path_elem and await self._extract_visible_text(category_path_elem):
            is_category_auto_selected = True

        if category:
            await self.web_sleep()  # workaround for https://github.com/Second-Hand-Friends/kleinanzeigen-bot/issues/39
            await self.web_click(By.XPATH, "//a[contains(., 'Kategorie')] | //button[contains(., 'Kategorie')]")
            await self.web_find(By.XPATH, "//button[contains(., 'Weiter')]")

            category_url = f"{self.root_url}/p-kategorie-aendern.html#?path={category}"
            await self.web_open(category_url)
            await self.web_click(By.XPATH, "//button[contains(., 'Weiter')]")
        else:
            ensure(is_category_auto_selected, f"No category specified in [{ad_file}] and automatic category detection failed")

    @staticmethod
    def __special_attribute_candidate_priority(elem:Element) -> tuple[int, int]:
        local_name = elem.local_name
        elem_type = str(cast(Any, elem.attrs.get("type")) or "").lower()
        role = str(cast(Any, elem.attrs.get("role")) or "").lower()

        if local_name == "button" and role == "combobox":
            return (0, 0)
        if local_name == "input" and elem_type in {"text", ""} and role == "combobox":
            return (1, 0)
        if local_name == "select":
            return (2, 0)
        if elem_type == "checkbox":
            return (3, 0)
        if local_name in {"input", "textarea"} and elem_type != "hidden":
            return (4, 0)
        if elem_type == "hidden":
            return (9, 1)
        return (8, 0)

    @staticmethod
    def __describe_special_attribute_candidate(elem:Element) -> str:
        elem_id = cast(str | None, elem.attrs.get("id"))
        elem_name = cast(str | None, elem.attrs.get("name"))
        elem_type = cast(str | None, elem.attrs.get("type"))
        elem_role = cast(str | None, elem.attrs.get("role"))
        return f"{elem.local_name}#'{elem_id}' name='{elem_name}' type='{elem_type}' role='{elem_role}'"

    def __pick_special_attribute_candidate(self, candidates:Sequence[Element], special_attribute_key:str) -> Element:
        ensure(candidates, f"No candidates found for special attribute [{special_attribute_key}]")
        ranked_candidates = sorted(
            enumerate(candidates),
            key = lambda entry: (self.__special_attribute_candidate_priority(entry[1]), entry[0]),
        )
        selected_idx, selected = ranked_candidates[0]

        if len(candidates) > 1:
            debug_candidates = ", ".join(f"#{idx}:{self.__describe_special_attribute_candidate(candidate)}" for idx, candidate in enumerate(candidates))
            LOG.debug(
                "Attribute field '%s' matched %s elements. Selected #%s: %s. Candidates: %s",
                special_attribute_key,
                len(candidates),
                selected_idx,
                self.__describe_special_attribute_candidate(selected),
                debug_candidates,
            )

        return selected

    async def __set_special_attributes(self, ad_cfg:Ad) -> None:
        if not ad_cfg.special_attributes:
            return

        LOG.debug("Found %i special attributes", len(ad_cfg.special_attributes))
        for special_attribute_key, special_attribute_value in ad_cfg.special_attributes.items():
            # Ensure special_attribute_value is treated as a string
            special_attribute_value_str = str(special_attribute_value)
            normalized_special_attribute_key = re.sub(r"_[a-z]+$", "", special_attribute_key).rsplit(".", maxsplit = 1)[-1]
            if not _SPECIAL_ATTRIBUTE_TOKEN_RE.fullmatch(normalized_special_attribute_key):
                LOG.debug(
                    "Attribute field '%s' has unsupported normalized key '%s'.",
                    special_attribute_key,
                    normalized_special_attribute_key,
                )
                raise TimeoutError(_("Failed to set attribute '%s'") % special_attribute_key)

            if normalized_special_attribute_key == "condition":
                LOG.debug("Special attribute [%s]: trying dedicated condition dialog path", special_attribute_key)
                if await self.__set_condition(special_attribute_value_str):
                    LOG.debug("Special attribute [%s]: condition dialog path succeeded", special_attribute_key)
                    continue

                LOG.info("Condition dialog not available, falling back to generic attribute handler for [%s]...", special_attribute_key)

            LOG.debug("Setting special attribute [%s] to [%s]...", special_attribute_key, special_attribute_value_str)
            id_suffix_literal = _xpath_literal(f".{normalized_special_attribute_key}")
            name_suffix_literal = _xpath_literal(f".{normalized_special_attribute_key}]")
            name_plus_literal = _xpath_literal(f".{normalized_special_attribute_key}+")
            bare_id_literal = _xpath_literal(normalized_special_attribute_key)
            bare_name_literal = _xpath_literal(f"attributeMap[{normalized_special_attribute_key}]")
            original_key_literal = _xpath_literal(special_attribute_key)
            # Match attribute fields by five patterns:
            # 1) exact id                 -> @id={bare_id_literal}
            # 2) dotted id suffix         -> ... = {id_suffix_literal}
            # 3) exact attributeMap name  -> @name={bare_name_literal}
            # 4) dotted name suffix       -> ... = {name_suffix_literal}
            # 5) compound key marker      -> contains(@name, {name_plus_literal})
            # Literals are derived via _xpath_literal from normalized_special_attribute_key.
            # 6) original config key      -> contains(@name, {original_key_literal}) for compound keys
            special_attr_xpath = (
                "//*["
                f"@id={bare_id_literal}"
                f" or (contains(@id, '.') and substring(@id, string-length(@id) - string-length({id_suffix_literal}) + 1) = {id_suffix_literal})"
                f" or @name={bare_name_literal}"
                f" or (contains(@name, '.') and substring(@name, string-length(@name) - string-length({name_suffix_literal}) + 1) = {name_suffix_literal})"
                f" or contains(@name, {name_plus_literal})"
                f" or contains(@name, {original_key_literal})"
                "]"
            )
            try:
                special_attr_candidates = await self.web_find_all(
                    By.XPATH,
                    special_attr_xpath,
                )
                special_attr_elem = self.__pick_special_attribute_candidate(special_attr_candidates, special_attribute_key)
            except TimeoutError as ex:
                LOG.debug(
                    "Attribute field '%s' (normalized: '%s') could not be found.",
                    special_attribute_key,
                    normalized_special_attribute_key,
                )
                if special_attribute_key.endswith("_s"):
                    LOG.debug(
                        "Legacy special-attribute id-only selectors (for example '%s') are intentionally not targeted directly. "
                        "If this category still renders legacy id-only controls, a dedicated rebuild is required.",
                        special_attribute_key,
                    )
                raise TimeoutError(_("Failed to set attribute '%s'") % special_attribute_key) from ex

            try:
                elem_id = cast(str | None, special_attr_elem.attrs.get("id"))
                elem_type = str(cast(Any, special_attr_elem.attrs.get("type")) or "").lower()
                elem_role = str(cast(Any, special_attr_elem.attrs.get("role")) or "").lower()
                elem_selector_type = By.ID if elem_id else By.XPATH
                elem_selector_value = elem_id or special_attr_xpath

                if special_attr_elem.local_name == "select":
                    LOG.debug("Attribute field '%s' seems to be a select...", special_attribute_key)
                    await self.web_select(elem_selector_type, elem_selector_value, special_attribute_value_str)
                elif elem_type == "checkbox":
                    LOG.debug("Attribute field '%s' seems to be a checkbox...", special_attribute_key)
                    truthy_values = {"1", "true", "yes", "on", "ja", "checked"}
                    falsy_values = {"", "0", "false", "no", "off", "nein", "unchecked", "none"}
                    normalized_checkbox_value = special_attribute_value_str.strip().lower()
                    if normalized_checkbox_value in truthy_values:
                        desired_checked = True
                    elif normalized_checkbox_value in falsy_values:
                        desired_checked = False
                    else:
                        LOG.debug(
                            "Attribute field '%s' has unsupported checkbox value '%s'.",
                            special_attribute_key,
                            special_attribute_value_str,
                        )
                        raise TimeoutError(_("Failed to set attribute '%s'") % special_attribute_key)

                    current_checked_attr = special_attr_elem.attrs.get("checked")
                    if isinstance(current_checked_attr, bool):
                        current_checked = current_checked_attr
                    else:
                        normalized_current_checked = str(current_checked_attr).strip().lower() if current_checked_attr is not None else ""
                        current_checked = normalized_current_checked not in falsy_values

                    if desired_checked != current_checked:
                        await self.web_click(elem_selector_type, elem_selector_value)
                elif special_attr_elem.local_name == "button" and elem_role == "combobox":
                    LOG.debug("Attribute field '%s' seems to be a button combobox (click-to-open dropdown)...", special_attribute_key)
                    ensure(elem_id, f"No id available for button combobox special attribute [{special_attribute_key}]")
                    await self.__select_button_combobox(cast(str, elem_id), special_attribute_value_str)
                elif elem_role == "combobox" and elem_type in {"text", ""} and special_attr_elem.local_name == "input":
                    LOG.debug("Attribute field '%s' seems to be a Combobox (i.e. text input with filtering dropdown)...", special_attribute_key)
                    await self.web_select_combobox(elem_selector_type, elem_selector_value, special_attribute_value_str)
                else:
                    LOG.debug("Attribute field '%s' seems to be a text input...", special_attribute_key)
                    await self.web_input(elem_selector_type, elem_selector_value, special_attribute_value_str)
            except TimeoutError as ex:
                LOG.debug("Failed to set attribute field '%s' via known input types.", special_attribute_key)
                raise TimeoutError(_("Failed to set attribute '%s'") % special_attribute_key) from ex
            LOG.debug("Successfully set attribute field [%s] to [%s]...", special_attribute_key, special_attribute_value_str)

    # TODO: Issue #930 — migrate to web_select_button_combobox (display-text-based, no React fiber)
    async def __select_button_combobox(self, elem_id:str, value:str) -> None:
        """Select an option from a <button role="combobox"> dropdown by its API value.

        Clicks the button to open the listbox, reads the options data from the React fiber
        (which maps API values to display labels), and clicks the matching option.
        """
        await self.web_click(By.ID, elem_id)
        listbox_id = f"{elem_id}-menu"
        await self.web_find(By.ID, listbox_id)
        js_btn_id = json.dumps(elem_id)
        js_listbox_id = json.dumps(listbox_id)
        js_value = json.dumps(value)
        ok = await self.web_execute(f"""(function() {{
            const listbox = document.getElementById({js_listbox_id});
            if (!listbox) return false;
            const liOptions = Array.from(listbox.querySelectorAll('[role="option"]'));
            const btnEl = document.getElementById({js_btn_id});
            if (!btnEl) return false;
            const fiberKey = Object.keys(btnEl).find(k => k.startsWith('__reactFiber'));
            let fiber = fiberKey ? btnEl[fiberKey] : null;
            for (let i = 0; i < 20 && fiber; i++, fiber = fiber.return) {{
                if (fiber.memoizedProps && fiber.memoizedProps.options) {{
                    const optionsData = fiber.memoizedProps.options;
                    for (let j = 0; j < optionsData.length; j++) {{
                        if (optionsData[j].value === {js_value} && liOptions[j]) {{
                            liOptions[j].click();
                            return true;
                        }}
                    }}
                    return false;
                }}
            }}
            return false;
        }})()""")
        if not ok:
            raise TimeoutError(_("Option '%(value)s' not found in button combobox '%(id)s'") % {"value": value, "id": elem_id})

    async def __set_shipping(self, ad_cfg:Ad, mode:AdUpdateStrategy = AdUpdateStrategy.REPLACE) -> None:
        short_timeout = self._timeout("quick_dom")
        if ad_cfg.shipping_type == "PICKUP":
            try:
                if not await self.web_check(By.ID, "ad-shipping-enabled-no", Is.SELECTED, timeout = short_timeout):
                    await self.web_click(By.ID, "ad-shipping-enabled-no", timeout = short_timeout)
            except TimeoutError as ex:
                LOG.debug(ex, exc_info = True)
                raise TimeoutError(_("Failed to set shipping attribute for type '%s'!") % ad_cfg.shipping_type) from ex
        elif ad_cfg.shipping_options:
            # Ensure shipping is enabled before opening the dialog (may already be selected)
            try:
                await self.web_click(By.ID, "ad-shipping-enabled-yes", timeout = short_timeout)
                await self.web_sleep(500, 800)
            except TimeoutError as ex:
                LOG.debug("Shipping enabled toggle not found before options dialog: %s", ex)
            await self.web_click(By.ID, "ad-shipping-options")

            if mode == AdUpdateStrategy.MODIFY:
                try:
                    # when "Andere Versandmethoden" is not available, go back and start over new
                    await self.web_find(By.XPATH, '//button[contains(., "Andere Versandmethoden")]', timeout = short_timeout)
                except TimeoutError:
                    await self.web_click(By.XPATH, '//button[contains(., "Zurück")]')

                    # in some categories we need to go another dialog back
                    try:
                        await self.web_find(By.XPATH, '//button[contains(., "Andere Versandmethoden")]', timeout = short_timeout)
                    except TimeoutError:
                        await self.web_click(By.XPATH, '//button[contains(., "Zurück")]')

            await self.web_click(By.XPATH, '//button[contains(., "Andere Versandmethoden")]')
            await self.__set_shipping_options(ad_cfg, mode)
        else:
            # Ensure shipping is enabled before opening the dialog (may already be selected)
            try:
                await self.web_click(By.ID, "ad-shipping-enabled-yes", timeout = short_timeout)
                await self.web_sleep(500, 800)
            except TimeoutError as ex:
                LOG.debug("Shipping enabled toggle not found before options dialog: %s", ex)

            # no options. only costs. Set custom shipping cost
            try:
                await self.web_click(By.ID, "ad-shipping-options")
            except TimeoutError as ex:
                LOG.debug(ex, exc_info = True)
                LOG.warning("Shipping options dialog entry not found. Legacy '.versand_s' select UI is no longer supported and requires dedicated rebuild.")
                raise TimeoutError(_("Unable to open shipping options dialog!")) from ex

            try:
                # when "Andere Versandmethoden" is not available, then we are already on the individual page
                await self.web_click(By.XPATH, '//button[contains(., "Andere Versandmethoden")]')
            except TimeoutError:
                # Dialog option not present; already on the individual shipping page.
                pass

            # only click on "Individueller Versand" when the price input is not available, otherwise it's already checked
            # (important for mode = UPDATE)
            individual_price_elem = await self.web_probe(By.ID, "ad-individual-shipping-price", timeout = short_timeout)
            if individual_price_elem is None:
                # Input not visible yet; click the individual shipping option.
                try:
                    await self.web_click(By.ID, "ad-individual-shipping-checkbox-control")
                except TimeoutError as ex:
                    LOG.debug(ex, exc_info = True)
                    raise TimeoutError(_("Unable to select individual shipping option!")) from ex

            if ad_cfg.shipping_costs is not None:
                try:
                    await self.web_input(By.ID, "ad-individual-shipping-price", str(ad_cfg.shipping_costs).replace(".", ","))
                except TimeoutError as ex:
                    LOG.debug(ex, exc_info = True)
                    raise TimeoutError(_("Unable to set shipping price!")) from ex
            else:
                LOG.debug(
                    "Shipping option 'ad-individual-shipping-checkbox-control' selected but no shipping_costs provided; "
                    "leaving field 'ad-individual-shipping-price' unchanged."
                )

            try:
                await self.web_click(By.XPATH, '//button[contains(., "Fertig")]')
            except TimeoutError as ex:
                LOG.debug(ex, exc_info = True)
                raise TimeoutError(_("Unable to close shipping dialog!")) from ex

    async def __set_shipping_options(self, ad_cfg:Ad, mode:AdUpdateStrategy = AdUpdateStrategy.REPLACE) -> None:
        if not ad_cfg.shipping_options:
            raise ValueError(_("shipping_options must be provided"))

        # Resolve user-facing config names to carrier codes
        try:
            wanted_carrier_codes = [CARRIER_CODE_BY_OPTION[opt] for opt in set(ad_cfg.shipping_options)]
        except KeyError as ex:
            raise KeyError(_("Unknown shipping option(s), please refer to the documentation/README: %s") % ad_cfg.shipping_options) from ex

        # Determine the size group — all options must belong to the same group
        size_info = {SIZE_INFO_BY_CARRIER_CODE[code] for code in wanted_carrier_codes}
        if len(size_info) != 1:
            raise ValueError(_("You can only specify shipping options for one package size!"))
        ((shipping_size, shipping_radio_value),) = size_info
        wanted_codes = set(wanted_carrier_codes)
        all_codes_for_size = CARRIER_CODES_BY_SIZE[shipping_size]

        short_timeout = self._timeout("quick_dom")
        dialog = '//*[self::dialog or @role="dialog"]'

        try:
            # Select the size group via radio button value (e.g. "SMALL", "MEDIUM", "LARGE")
            size_radio_xpath = f'{dialog}//input[@type="radio" and @value="{shipping_radio_value}"]'
            shipping_size_radio = await self.web_find(By.XPATH, size_radio_xpath, timeout = short_timeout)
            shipping_size_radio_is_checked = shipping_size_radio.attrs.get("checked") is not None

            if not shipping_size_radio_is_checked:
                LOG.debug("Selecting size '%s' (radio value=%s)", shipping_size, shipping_radio_value)
                await self.web_click(By.XPATH, size_radio_xpath, timeout = short_timeout)

            await self.web_sleep(300, 500)
            await self.web_click(By.XPATH, f'{dialog}//button[contains(., "Weiter")]', timeout = short_timeout)
            await self.web_sleep(500, 800)

            # Toggle package checkboxes by carrier code value attribute.
            # IMPORTANT: REPLACE intentionally uses the same state-based sync as MODIFY.
            # Live DOM defaults after "Weiter" are not stable across size/category (issue #956),
            # so we must read current checkbox state and reconcile with desired state.
            LOG.debug("Using state-based shipping option sync for mode '%s'", mode)
            LOG.debug("Processing %d packages for size '%s'", len(all_codes_for_size), shipping_size)

            for carrier_code in all_codes_for_size:
                checkbox_xpath = f'{dialog}//input[@type="checkbox" and @value="{carrier_code}"]'
                checkbox = await self.web_find(By.XPATH, checkbox_xpath, timeout = short_timeout)
                is_checked = checkbox.attrs.get("checked") is not None
                should_be_checked = carrier_code in wanted_codes

                LOG.debug("Carrier '%s': checked=%s, wanted=%s", carrier_code, is_checked, should_be_checked)

                if is_checked != should_be_checked:
                    LOG.debug("Toggling carrier '%s'", carrier_code)
                    await self.web_click(By.XPATH, checkbox_xpath, timeout = short_timeout)
        except TimeoutError as ex:
            LOG.debug(ex, exc_info = True)
            raise TimeoutError(_("Failed to configure shipping options in dialog!")) from ex

        try:
            # Click apply button
            await self.web_click(By.XPATH, f'{dialog}//button[contains(., "Fertig")]', timeout = short_timeout)
        except TimeoutError as ex:
            raise TimeoutError(_("Unable to close shipping dialog!")) from ex

    async def __upload_images(self, ad_cfg:Ad) -> None:
        if not ad_cfg.images:
            return

        LOG.info(" -> found %s", pluralize("image", ad_cfg.images))
        hidden_marker_selector = "input[name^='adImages'][name$='.url']"
        quick_dom_timeout = self._timeout("quick_dom")

        # Capture marker baseline before this upload attempt to avoid counting stale values
        baseline_marker_count = 0
        try:
            baseline_markers = await self._web_find_all_once(By.CSS_SELECTOR, hidden_marker_selector, quick_dom_timeout)
            baseline_marker_count = sum(1 for marker in baseline_markers if _get_marker_value(marker))
        except TimeoutError:
            baseline_marker_count = 0

        if baseline_marker_count:
            LOG.debug(" -> detected %d pre-existing image marker(s) before upload", baseline_marker_count)

        for image in ad_cfg.images:
            image_upload:Element = await self.web_find(By.CSS_SELECTOR, "input[type=file]")
            LOG.info(" -> uploading image [%s]", image)
            await image_upload.send_file(image)
            await self.web_sleep()

        # Wait for all images to be processed
        expected_count = len(ad_cfg.images)
        LOG.info(" -> waiting for %s to be processed...", pluralize("image", ad_cfg.images))

        async def count_processed_images() -> int:
            try:
                markers = await self._web_find_all_once(By.CSS_SELECTOR, hidden_marker_selector, quick_dom_timeout)
                marker_count = sum(1 for marker in markers if _get_marker_value(marker))
            except TimeoutError:
                marker_count = 0

            return max(0, marker_count - baseline_marker_count)

        async def check_thumbnails_uploaded() -> bool:
            current_count = await count_processed_images()
            if current_count < expected_count:
                LOG.debug(" -> %d of %d images processed", current_count, expected_count)
            return current_count >= expected_count

        try:
            await self.web_await(check_thumbnails_uploaded, timeout = self._timeout("image_upload"), timeout_error_message = _("Image upload timeout exceeded"))
        except TimeoutError as ex:
            # Get current count for better error message
            current_count = await count_processed_images()
            raise TimeoutError(
                _("Not all images were uploaded within timeout. Expected %(expected)d, found %(found)d processed images.")
                % {"expected": expected_count, "found": current_count}
            ) from ex

        LOG.info(" -> all images uploaded successfully")

    async def download_ads(self) -> None:
        """
        Determines which download mode was chosen with the arguments, and calls the specified download routine.
        This downloads either all, only unsaved(new), or specific ads given by ID.
        """
        # Normalize comma-separated keyword selectors; set deduplication collapses "new,new" → {"new"}
        selector_tokens = {s.strip() for s in self.ads_selector.split(",")}
        if "all" in selector_tokens:
            effective_selector = "all"
        elif len(selector_tokens) == 1:
            effective_selector = next(iter(selector_tokens))  # e.g. "new,new" → "new"
        else:
            effective_selector = self.ads_selector  # numeric IDs: "123,456" — unchanged

        # Fetch published ads once from manage-ads JSON to avoid repetitive API calls during extraction
        # Build lookup dict inline and pass directly to extractor (no cache abstraction needed)
        LOG.info("Fetching ad metadata (status, expiry dates)...")
        published_ads = await self._fetch_published_ads(strict = bool(_NUMERIC_IDS_RE.match(effective_selector)))
        published_ads_by_id:dict[int, dict[str, Any]] = {}
        for published_ad in published_ads:
            try:
                ad_id = published_ad.get("id")
                if ad_id is not None:
                    published_ads_by_id[int(ad_id)] = published_ad
            except (ValueError, TypeError):
                LOG.warning("Skipping ad with non-numeric id: %s", published_ad.get("id"))
        LOG.info("Loaded metadata for %s published ads.", len(published_ads_by_id))

        download_dir = self._resolve_download_dir()
        xdg_paths.ensure_directory(download_dir, "downloaded ads directory")
        LOG.info("Ads download directory: %s", download_dir)
        ad_extractor = extract.AdExtractor(self.browser, self.config, download_dir, published_ads_by_id = published_ads_by_id)

        if effective_selector in {"all", "new"}:  # explore ads overview for these two modes
            LOG.info("Scanning ad overview for navigation URLs...")
            own_ad_urls = await ad_extractor.extract_own_ads_urls()
            LOG.info("Found %s.", pluralize("ad URL", len(own_ad_urls)))

            if effective_selector == "all":  # download all of your ads
                LOG.info("Starting download of all ads...")

                valid_ad_refs:list[tuple[str, int]] = []
                for ad_url in own_ad_urls:
                    ad_id = ad_extractor.extract_ad_id_from_ad_url(ad_url)
                    if ad_id == -1:
                        # Skip ads with invalid URLs (warning already logged by extract_ad_id_from_ad_url)
                        continue
                    valid_ad_refs.append((ad_url, ad_id))

                success_count = 0
                # call download function for each ad page
                for idx, (ad_url, ad_id) in enumerate(valid_ad_refs, start = 1):
                    LOG.info("Downloading %d/%d ads...", idx, len(valid_ad_refs))

                    if await ad_extractor.navigate_to_ad_page(ad_url):
                        await self._download_ad_with_resolved_state(ad_extractor, ad_id, published_ads_by_id)
                        success_count += 1
                LOG.info("%d of %d ads were downloaded from your profile.", success_count, len(valid_ad_refs))

            elif effective_selector == "new":  # download only unsaved ads
                # check which ads already saved
                saved_ad_ids = []
                ads = self.load_ads(ignore_inactive = False, exclude_ads_with_id = False)  # do not skip because of existing IDs
                for ad in ads:
                    saved_ad_id = ad[1].id
                    if saved_ad_id is None:
                        LOG.debug("Skipping saved ad without id (likely unpublished or manually created): %s", ad[0])
                        continue
                    saved_ad_ids.append(int(saved_ad_id))

                # determine ad IDs from links
                ad_id_by_url = {url: ad_extractor.extract_ad_id_from_ad_url(url) for url in own_ad_urls}

                LOG.info("Starting download of not yet downloaded ads...")
                ads_to_download:list[tuple[str, int]] = []
                for ad_url, ad_id in ad_id_by_url.items():
                    # Skip ads with invalid URLs (warning already logged by extract_ad_id_from_ad_url)
                    if ad_id == -1:
                        continue

                    # check if ad with ID already saved
                    if ad_id in saved_ad_ids:
                        LOG.info("The ad with id %d has already been saved.", ad_id)
                        continue
                    ads_to_download.append((ad_url, ad_id))

                new_count = 0
                for idx, (ad_url, ad_id) in enumerate(ads_to_download, start = 1):
                    LOG.info("Downloading %d/%d ads...", idx, len(ads_to_download))

                    if await ad_extractor.navigate_to_ad_page(ad_url):
                        await self._download_ad_with_resolved_state(ad_extractor, ad_id, published_ads_by_id)
                        new_count += 1
                LOG.info("%s were downloaded from your profile.", pluralize("new ad", new_count))

        elif _NUMERIC_IDS_RE.match(effective_selector):  # download ad(s) with specific id(s)
            ids = [int(n) for n in effective_selector.split(",")]
            LOG.info("Starting download of ad(s) with the id(s):")
            LOG.info(" | ".join([str(ad_id) for ad_id in ids]))

            for idx, ad_id in enumerate(ids, start = 1):  # call download routine for every id
                LOG.info("Downloading %d/%d ads...", idx, len(ids))
                exists = await ad_extractor.navigate_to_ad_page(ad_id)
                if exists:
                    resolved = self._resolve_download_ad_activity(ad_id, published_ads_by_id)
                    if not resolved.owned:
                        # Foreign ad - expected for numeric IDs (can download any public ad)
                        LOG.warning("Ad id %d is not in your published profile ads. Saving downloaded ad as inactive.", ad_id)

                    await ad_extractor.download_ad(ad_id, active = resolved.active)
                    LOG.info("Downloaded ad with id %d", ad_id)
                else:
                    LOG.error("The page with the id %d does not exist!", ad_id)

    def __get_description(self, ad_cfg:Ad, *, with_affixes:bool) -> str:
        """Get the ad description optionally with prefix and suffix applied.

        Precedence(highest to lowest):
        1. Direct ad - level affixes(description_prefix / suffix)
        2. Global flattened affixes(ad_defaults.description_prefix / suffix)
        3. Legacy global nested affixes(ad_defaults.description.prefix / suffix)

        Args:
            ad_cfg: The ad configuration dictionary

        Returns:
            The raw or complete description with prefix and suffix applied
        """
        # Get the main description text
        description_text = ""
        if ad_cfg.description:
            description_text = ad_cfg.description

        if with_affixes:
            # Get prefix with precedence
            prefix = (
                # 1. Direct ad-level prefix
                ad_cfg.description_prefix
                if ad_cfg.description_prefix is not None
                # 2. Global prefix from config
                else self.config.ad_defaults.description_prefix or ""  # Default to empty string if all sources are None
            )

            # Get suffix with precedence
            suffix = (
                # 1. Direct ad-level suffix
                ad_cfg.description_suffix
                if ad_cfg.description_suffix is not None
                # 2. Global suffix from config
                else self.config.ad_defaults.description_suffix or ""  # Default to empty string if all sources are None
            )

            # Combine the parts and replace @ with (at)
            final_description = str(prefix) + str(description_text) + str(suffix)
            final_description = final_description.replace("@", "(at)")
        else:
            final_description = description_text

        # Validate length
        ensure(
            len(final_description) <= MAX_DESCRIPTION_LENGTH,
            f"Length of ad description including prefix and suffix exceeds {MAX_DESCRIPTION_LENGTH} chars. Description length: {len(final_description)} chars.",
        )

        return final_description

    def update_content_hashes(self, ads:list[tuple[str, Ad, dict[str, Any]]]) -> None:
        changed = 0

        for idx, (ad_file, ad_cfg, ad_cfg_orig) in enumerate(ads, start = 1):
            LOG.info("Processing %s/%s: '%s' from [%s]...", idx, len(ads), ad_cfg.title, ad_file)
            ad_cfg.update_content_hash()
            if ad_cfg.content_hash != ad_cfg_orig["content_hash"]:
                changed += 1
                ad_cfg_orig["content_hash"] = ad_cfg.content_hash
                dicts.save_dict(ad_file, ad_cfg_orig)

        LOG.info("############################################")
        LOG.info("DONE: Updated [content_hash] in %s", pluralize("ad", changed))
        LOG.info("############################################")


#############################
# main entry point
#############################


def main(args:list[str]) -> None:
    if "version" not in args:
        print(
            textwrap.dedent(rf"""
         _    _      _                           _                       _           _
        | | _| | ___(_)_ __   __ _ _ __  _______(_) __ _  ___ _ __      | |__   ___ | |_
        | |/ / |/ _ \ | '_ \ / _` | '_ \|_  / _ \ |/ _` |/ _ \ '_ \ ____| '_ \ / _ \| __|
        |   <| |  __/ | | | | (_| | | | |/ /  __/ | (_| |  __/ | | |____| |_) | (_) | |_
        |_|\_\_|\___|_|_| |_|\__,_|_| |_/___\___|_|\__, |\___|_| |_|    |_.__/ \___/ \__|
                                                   |___/
                                 https://github.com/Second-Hand-Friends/kleinanzeigen-bot
                                 Version: {__version__}
        """)[1:],
            flush = True,
        )  # [1:] removes the first empty blank line

    loggers.configure_console_logging()

    signal.signal(signal.SIGINT, error_handlers.on_sigint)  # capture CTRL+C

    # sys.excepthook = error_handlers.on_exception
    # -> commented out because it causes PyInstaller to log "[PYI-28040:ERROR] Failed to execute script '__main__' due to unhandled exception!",
    #    despite the exceptions being properly processed by our custom error_handlers.on_exception callback.
    #    We now handle exceptions explicitly using a top-level try/except block.

    atexit.register(loggers.flush_all_handlers)

    try:
        bot = KleinanzeigenBot()
        atexit.register(bot.close_browser_session)
        nodriver.loop().run_until_complete(bot.run(args))  # type: ignore[attr-defined]
    except CaptchaEncountered as ex:
        raise ex
    except Exception:
        error_handlers.on_exception(*sys.exc_info())


if __name__ == "__main__":
    loggers.configure_console_logging()
    LOG.error("Direct execution not supported. Use 'pdm run app'")
    sys.exit(1)
