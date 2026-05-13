# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Live DOM probe runner for kleinanzeigen.de publish/manage flows.

Purpose
=======
This script checks live-page assumptions that the bot relies on for login,
publish, shipping, pagination, condition handling, and delete flows. It is a
maintainer-only local diagnostic tool, not a test suite: it talks to the live
site, uses the local configuration, and records what the DOM actually looks
like.

Privacy note
=============
Runs can capture live ad titles, addresses, phone numbers, and raw HTML into
JSON/log/HTML artifacts. Keep the generated files local and do not attach them
to issues or pull requests.

Safety
======
The ``run`` subcommand performs live-site interaction and may open browser
pages, submit non-destructive probes, and save HTML snapshots for snapshot-
capable probes by default. Use ``--no-save-dom`` to disable DOM snapshots.
Each run writes its JSON report and log to dedicated timestamped files in
``.temp/verify_dom_assumptions/``, and snapshot HTML files live under the
matching snapshot directory ``.temp/verify_dom_assumptions/dom-snapshots/``.
Files older than 60 days are cleaned up automatically. The script never
intentionally deletes real ads; delete checks use a safe non-existent ID.

Core concepts
=============
* **probe** – one named diagnostic action with a stable summary and raw payload
* **preset** – a reusable ordered list of probes
* **artifact** – an output file produced by a probe or the runner, such as a DOM
  snapshot or the final JSON report

CLI examples
============
* ``pdm run verify-dom-assumptions list-probes``
* ``pdm run verify-dom-assumptions list-presets``
* ``pdm run verify-dom-assumptions run --preset full``
* ``pdm run verify-dom-assumptions run --preset download``
* ``pdm run verify-dom-assumptions run --no-save-dom --preset full``
* ``pdm run verify-dom-assumptions run --probe pagination-api --max-pages 3``
* ``pdm run verify-dom-assumptions run --probe shipping-live --dom-dir .temp/verify_dom_assumptions/dom-snapshots``

Report shape
============
The JSON report uses stable top-level keys:
``meta``, ``inputs``, ``summary``, ``probes``, ``warnings``, ``errors``, and
``artifacts``. Each probe entry contains ``name``, ``status``, ``summary``,
``raw``, ``errors``, ``warnings``, and ``artifacts``.

Extension workflow
==================
1. Add or wrap a probe function.
2. Register it in the probe registry with a stable name.
3. Add it to a preset if it should run by default.
4. Keep the probe focused and prefer reusing the existing bot helpers.

Maintenance guidelines
======================
* This script is intended for local developer use only.
* The ``run`` subcommand refuses to execute in CI environments.
* Do not add repository test coverage for this script; validate changes manually.
* Keep probes small, explicit, and safe by default.
* Prefer wrapper code over large rewrites of existing probe internals.
* Keep report keys stable so downstream tooling can compare runs.
* Avoid changing observable behavior unless a probe is intentionally renamed or
  a CLI surface is being modernized.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Final, cast

from kleinanzeigen_bot import AdUpdateStrategy, KleinanzeigenBot
from kleinanzeigen_bot.model.ad_model import CARRIER_CODES_BY_SIZE, Ad
from kleinanzeigen_bot.utils import dicts, xdg_paths
from kleinanzeigen_bot.utils.web_scraping_mixin import By, Element, Is

SCRIPT_PATH:Final[Path] = Path(__file__).resolve()
ROOT:Final[Path] = SCRIPT_PATH.parent.parent
RUN_OUTPUT_DIR:Final[Path] = ROOT / ".temp" / "verify_dom_assumptions"
DEFAULT_DOM_DIR:Final[str] = ".temp/verify_dom_assumptions/dom-snapshots"
RUN_RETENTION_DAYS:Final[int] = 60
RUN_TIMESTAMP_FORMAT:Final[str] = "%Y%m%dT%H%M%S%fZ"
DEFAULT_AD_FILE:Final[str] = "tests/fixtures/demo_ads/ad_000300_Special_Attributes_Issue_938/ad_000300_Special_Attributes_Issue_938.yaml"
DOWNLOAD_CREATION_DATE_SELECTOR:Final[str] = "#viewad-extra-info > div:nth-child(1) > span:nth-child(2)"
CONDITION_API_VALUES:Final[frozenset[str]] = frozenset({"new", "like_new", "ok", "alright", "defect"})
CONDITION_GERMAN_TO_API:Final[dict[str, str]] = {
    "neu": "new",
    "wie_neu": "like_new",
    "sehr_gut": "like_new",
    "gut": "ok",
    "in_ordnung": "alright",
    "defekt": "defect",
}
CONDITION_API_TO_DISPLAY_CANDIDATES:Final[dict[str, tuple[str, ...]]] = {
    "new": ("Neu",),
    "like_new": ("Sehr Gut", "Wie neu"),
    "ok": ("Gut",),
    "alright": ("In Ordnung",),
    "defect": ("Defekt",),
}
DOWNLOAD_REQUIRED_CHECKS:Final[frozenset[str]] = frozenset({"creation_date"})
DOWNLOAD_OPTIONAL_CHECKS:Final[frozenset[str]] = frozenset({"vap_ovrly_secure", "galleryimage_large", "street_address", "viewad_contact_phone"})
if set(CONDITION_API_TO_DISPLAY_CANDIDATES) != CONDITION_API_VALUES:
    raise ValueError("CONDITION_API_TO_DISPLAY_CANDIDATES must cover the canonical condition API values exactly")


class ProbeStatus(str, Enum):
    """Normalized probe execution status."""

    PASSED = "passed"
    FAILED = "failed"
    PARTIAL = "partial"
    SKIPPED = "skipped"


@dataclass(slots = True)
class ArtifactRef:
    """Reference to a generated artifact."""

    name:str
    path:str
    kind:str = "file"
    description:str | None = None


@dataclass(slots = True)
class RunConfig:
    """Resolved runtime inputs for a verification run."""

    config_path:Path
    output_dir:Path
    run_id:str
    report_path:Path
    log_path:Path
    max_pages:int
    ad_file:Path
    save_dom:bool
    dom_dir:Path
    probe_login_page:bool = False
    exercise_fields:bool = False
    preset:str | None = None
    probes:tuple[str, ...] = ()
    condition_values:tuple[str, ...] = ()
    category_override:str | None = None


@dataclass(slots = True)
class RunContext:
    """Runtime state shared across probes."""

    bot:KleinanzeigenBot
    config:RunConfig
    ad_cfg:Ad | None
    ad_file_path:Path


@dataclass(slots = True)
class ProbeResult:
    """Normalized probe result used in the JSON report."""

    name:str
    status:ProbeStatus
    summary:str
    raw:Any = None
    errors:list[dict[str, Any]] = field(default_factory = list)
    warnings:list[dict[str, Any]] = field(default_factory = list)
    artifacts:list[ArtifactRef] = field(default_factory = list)


@dataclass(slots = True)
class ProbeSpec:
    """Registry entry for a named probe."""

    name:str
    description:str
    runner:Callable[[RunContext], Awaitable[ProbeResult]]
    needs_ad:bool = False
    prelogin:bool = False


def summarize_carrier_checkbox_defaults(checkboxes:list[dict[str, Any]], expected_codes:list[str]) -> dict[str, Any]:
    """Summarize default carrier checkbox states after clicking 'Weiter'."""
    found_codes:set[str] = set()
    checked_codes:set[str] = set()

    for checkbox in checkboxes:
        if not isinstance(checkbox, dict):
            continue
        carrier_code = checkbox.get("carrierCode")
        if not isinstance(carrier_code, str):
            continue
        if not checkbox.get("found"):
            continue
        found_codes.add(carrier_code)
        if checkbox.get("checked") is True:
            checked_codes.add(carrier_code)

    unexpected_codes = sorted(found_codes - set(expected_codes))
    unchecked_codes = sorted(found_codes - checked_codes)
    expected_codes_set = set(expected_codes)
    all_expected_present = expected_codes_set.issubset(found_codes)
    all_expected_prechecked = all_expected_present and expected_codes_set.issubset(checked_codes)

    return {
        "found_carrier_codes": sorted(found_codes),
        "checked_carrier_codes_after_weiter": sorted(checked_codes),
        "unchecked_carrier_codes_after_weiter": unchecked_codes,
        "all_expected_present": all_expected_present,
        "all_expected_prechecked_after_weiter": all_expected_prechecked,
        "unexpected_codes": unexpected_codes,
    }


def _xpath_literal(value:str) -> str:
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    return "concat(" + ', "\'", '.join(f"'{part}'" for part in value.split("'")) + ")"


def _build_special_attribute_xpath(original_key:str, normalized_key:str) -> str:
    id_suffix_literal = _xpath_literal(f".{normalized_key}")
    name_suffix_literal = _xpath_literal(f".{normalized_key}]")
    name_plus_literal = _xpath_literal(f".{normalized_key}+")
    bare_id_literal = _xpath_literal(normalized_key)
    bare_name_literal = _xpath_literal(f"attributeMap[{normalized_key}]")
    original_key_literal = _xpath_literal(original_key)

    return (
        "//*["
        f"@id={bare_id_literal}"
        f" or (contains(@id, '.') and substring(@id, string-length(@id) - string-length({id_suffix_literal}) + 1) = {id_suffix_literal})"
        f" or @name={bare_name_literal}"
        f" or (contains(@name, '.') and substring(@name, string-length(@name) - string-length({name_suffix_literal}) + 1) = {name_suffix_literal})"
        f" or contains(@name, {name_plus_literal})"
        f" or contains(@name, {original_key_literal})"
        "]"
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description = "Registry-based live DOM probe runner for kleinanzeigen.de (maintainer-only; artifacts may contain live personal data).")
    subparsers = parser.add_subparsers(dest = "command", required = True)

    list_probes = subparsers.add_parser("list-probes", help = "List all available probes")
    list_probes.set_defaults(command = "list-probes")

    list_presets = subparsers.add_parser("list-presets", help = "List available probe presets")
    list_presets.set_defaults(command = "list-presets")

    run = subparsers.add_parser("run", help = "Run one or more probes against the live site")
    run.add_argument(
        "--config",
        default = "config.yaml",
        help = "Path to config file, resolved relative to the repository root unless absolute (default: config.yaml)",
    )
    run.add_argument("--max-pages", type = _positive_int, default = 5, help = "Maximum pagination pages to inspect via paging.next")
    run.add_argument(
        "--ad-file",
        default = DEFAULT_AD_FILE,
        help = "Ad YAML used for ad-driven probes, resolved relative to the repository root unless absolute (default: tracked demo fixture)",
    )
    run.add_argument("--no-save-dom", action = "store_true", help = "Disable saving full HTML snapshots for snapshot-capable probes")
    run.add_argument(
        "--dom-dir",
        default = DEFAULT_DOM_DIR,
        help = (
            "Directory used for HTML snapshots, resolved relative to the repository root unless absolute "
            "(default: .temp/verify_dom_assumptions/dom-snapshots)"
        ),
    )
    run.add_argument("--probe", action = "append", default = [], help = "Probe name to run (repeatable). If omitted, --preset or the full preset is used.")
    run.add_argument("--preset", default = None, help = "Preset name to expand into probes. Defaults to full when neither --probe nor --preset is provided.")
    run.add_argument("--exercise-fields", action = "store_true", help = "Also run the intrusive field-exercise probe")
    run.add_argument("--probe-login-page", action = "store_true", help = "Also run the login-selectors probe before login")
    run.add_argument("--condition-value", action = "append", default = [], help = "Override condition_s for condition-flow (repeatable)")
    run.add_argument("--category-override", default = None, help = "Override the category used by the condition-flow probe")
    run.set_defaults(command = "run")
    return parser


def _positive_int(value:str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(f"invalid integer value: {value!r}") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("--max-pages must be >= 1")
    return parsed


def _env_flag_enabled(name:str) -> bool:
    value = os.getenv(name)
    if value is None:
        return False
    return value.strip().lower() not in {"", "0", "false", "no", "off"}


async def _collect_login_selector_presence(bot:KleinanzeigenBot) -> dict[str, Any]:
    await bot.web_open(f"{bot.root_url}/m-einloggen-sso.html")
    js = """
(() => {
  const selectors = {
    username: '#username',
    loginEmail: '#login-email',
    password: '#login-password'
  }
  const result = {}
  for (const [name, selector] of Object.entries(selectors)) {
    const node = document.querySelector(selector)
    result[name] = {
      selector,
      found: !!node,
      tagName: node ? node.tagName.toLowerCase() : null,
      type: node && 'type' in node ? node.type : null
    }
  }
  return result
})()
"""
    raw = await bot.web_execute(js)
    return dict(raw) if isinstance(raw, dict) else {"raw": raw}


async def _collect_publish_selector_presence(bot:KleinanzeigenBot) -> dict[str, Any]:
    await bot.web_open(f"{bot.root_url}/p-anzeige-aufgeben-schritt2.html")
    js = """
(() => {
  const checks = {
    ad_type_old_wanted: '#adType2',
    ad_type_new_wanted: '#ad-type-WANTED',
    title_old: '#postad-title',
    title_new: '#ad-title',
    description_old: '#pstad-descrptn',
    description_new: '#ad-description',
    submit_old: '#pstad-submit',
    submit_generic: "button[type='submit']",
    price_old: 'input#pstad-price',
    price_react_old: 'input#post-ad-frontend-price',
    price_micro_old: 'input#micro-frontend-price',
    price_new: 'input#ad-price-amount',
    price_type_select_old: 'select#price-type-react',
    price_type_button_new: '#ad-price-type',
    price_type_menu_option_0: '#ad-price-type-menu-option-0',
    price_type_menu_option_1: '#ad-price-type-menu-option-1',
    price_type_menu_option_2: '#ad-price-type-menu-option-2',
    zip_old: '#pstad-zip',
    zip_new: '#ad-zip-code',
    city_old: '#pstad-citychsr',
    city_new: '#ad-city',
    street_old: '#pstad-street',
    street_new: '#ad-street',
    address_visibility_old: '#addressVisibility',
    address_visibility_new: '#ad-address-visibility',
    contact_name_old: '#postad-contactname',
    contact_name_new: '#ad-name',
    phone_old: '#postad-phonenumber',
    phone_new: '#ad-phone',
    phone_visibility_old: '#phoneNumberVisibility',
    phone_visibility_new: '#ad-phone-visibility',
    category_change_old: '#pstad-lnk-chngeCtgry',
    category_auto_path_old: '#postad-category-path',
    category_auto_path_new: '#ad-category-path',
    category_step_submit_old_container: '#postad-step1-sbmt',
    category_step_submit_old_button: '#postad-step1-sbmt button',
    pickup_old: '#radio-pickup',
    pickup_new_ad: '#ad-pickup',
    pickup_new_radio_button: '#radio-button-pickup',
    buy_now_yes_old: '#radio-buy-now-yes',
    buy_now_no_old: '#radio-buy-now-no',
    buy_now_yes_new_ad: '#ad-buy-now-yes',
    buy_now_no_new_ad: '#ad-buy-now-no',
    buy_now_yes_new_radio_button: '#radio-button-buy-now-yes',
    buy_now_no_new_radio_button: '#radio-button-buy-now-no'
  }

  const result = {}
  for (const [name, selector] of Object.entries(checks)) {
    const node = document.querySelector(selector)
    result[name] = {
      selector,
      found: !!node,
      tagName: node ? node.tagName.toLowerCase() : null,
      id: node ? node.id || null : null,
      name: node && 'name' in node ? node.name : null,
      type: node && 'type' in node ? node.type : null,
      role: node ? node.getAttribute('role') : null,
      checked: node && 'checked' in node ? !!node.checked : null,
      snippet: node ? node.outerHTML.slice(0, 220) : null
    }
  }

  const candidateElements = Array.from(document.querySelectorAll('[id],[name],label,button,[role]'))
  const termMatches = candidateElements
    .filter((node) => {
      const id = (node.id || '').toLowerCase()
      const name = (node.getAttribute('name') || '').toLowerCase()
      const htmlFor = (node.getAttribute('for') || '').toLowerCase()
      const text = (node.textContent || '').toLowerCase().replace(/\\s+/g, ' ').trim()
      return (
        id.includes('pickup') ||
        id.includes('buy-now') ||
        id.includes('buynow') ||
        id.includes('shipping') ||
        id.includes('price-type') ||
        id.includes('phone') ||
        id.includes('kategorie') ||
        name.includes('pickup') ||
        name.includes('buy-now') ||
        name.includes('buynow') ||
        name.includes('shipping') ||
        name.includes('price') ||
        name.includes('phone') ||
        htmlFor.includes('pickup') ||
        htmlFor.includes('buy-now') ||
        htmlFor.includes('buynow') ||
        htmlFor.includes('price') ||
        htmlFor.includes('phone') ||
        text.includes('nur abholung') ||
        text.includes('abholung') ||
        text.includes('direkt kaufen') ||
        text.includes('sicher bezahlen') ||
        text.includes('preisart') ||
        text.includes('telefon') ||
        text.includes('kategorie')
      )
    })
    .slice(0, 30)
    .map((node) => {
      const associatedInput = node.tagName.toLowerCase() === 'label' ? node.querySelector('input') : null
      return {
        tagName: node.tagName.toLowerCase(),
        id: node.id || null,
        name: node.getAttribute('name'),
        type: 'type' in node ? node.type : null,
        role: node.getAttribute('role'),
        htmlFor: node.getAttribute('for'),
        text: (node.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 140),
        associatedInputId: associatedInput ? associatedInput.id || null : null,
        associatedInputName: associatedInput ? associatedInput.getAttribute('name') : null,
        snippet: node.outerHTML.slice(0, 280)
      }
    })
  result.shipping_and_buy_now_candidates = {
    selector: '[id],[name],label,button,[role] + keyword filter',
    found: termMatches.length > 0,
    count: termMatches.length,
    candidates: termMatches,
  }

  const phoneCandidates = Array.from(document.querySelectorAll("[id*='phone' i], [name*='phone' i], label[for*='phone' i]"))
    .slice(0, 30)
    .map((node) => ({
      tagName: node.tagName.toLowerCase(),
      id: node.id || null,
      name: node.getAttribute('name'),
      htmlFor: node.getAttribute('for'),
      text: (node.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 140),
      snippet: node.outerHTML.slice(0, 260),
    }))
  result.phone_candidates = {
    selector: "[id*='phone' i], [name*='phone' i], label[for*='phone' i]",
    found: phoneCandidates.length > 0,
    count: phoneCandidates.length,
    candidates: phoneCandidates,
  }

  const categoryCandidates = Array.from(document.querySelectorAll("[id*='category' i], [name*='category' i], a, button"))
    .filter((node) => {
      const id = (node.id || '').toLowerCase()
      const name = (node.getAttribute('name') || '').toLowerCase()
      const text = (node.textContent || '').toLowerCase().replace(/\\s+/g, ' ').trim()
      return id.includes('category') || name.includes('category') || text.includes('kategorie')
    })
    .slice(0, 30)
    .map((node) => ({
      tagName: node.tagName.toLowerCase(),
      id: node.id || null,
      name: node.getAttribute('name'),
      text: (node.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 160),
      snippet: node.outerHTML.slice(0, 260),
    }))
  result.category_candidates = {
    selector: "[id*='category' i], [name*='category' i], a, button + category text filter",
    found: categoryCandidates.length > 0,
    count: categoryCandidates.length,
    candidates: categoryCandidates,
  }

  const submitByText = Array.from(document.querySelectorAll('button')).find((button) =>
    button.textContent && button.textContent.includes('Anzeige aufgeben')
  )
  result.submit_text_contains_anzeige_aufgeben = {
    selector: "button text contains 'Anzeige aufgeben'",
    found: !!submitByText,
    tagName: submitByText ? submitByText.tagName.toLowerCase() : null,
    snippet: submitByText ? submitByText.outerHTML.slice(0, 220) : null
  }

  const categoryByText = Array.from(document.querySelectorAll('a,button')).find((node) =>
    node.textContent && node.textContent.includes('Wähle deine Kategorie')
  )
  result.category_change_text_contains = {
    selector: "a/button text contains 'Wähle deine Kategorie'",
    found: !!categoryByText,
    tagName: categoryByText ? categoryByText.tagName.toLowerCase() : null,
    snippet: categoryByText ? categoryByText.outerHTML.slice(0, 220) : null
  }

  return {
    checks: result,
    url: location.href,
    title: document.title
  }
})()
"""
    raw = await bot.web_execute(js)
    return dict(raw) if isinstance(raw, dict) else {"raw": raw}


async def _collect_download_selector_presence(bot:KleinanzeigenBot, ad_cfg:"Ad") -> dict[str, Any]:
    ad_id = getattr(ad_cfg, "id", None)
    if not isinstance(ad_id, int):
        return {"skipped": True, "reason": "ad id unavailable"}

    await bot.web_open(f"{bot.root_url}/s-suchanfrage.html?keywords={ad_id}")
    await bot.web_sleep(2200, 2400)

    checks:dict[str, Any] = {}
    missing_required:list[str] = []
    missing_optional:list[str] = []

    async def record(name:str, selector_type:By, selector_value:str, *, capture_text:bool = False) -> Element | None:
        element = await bot.web_probe(selector_type, selector_value, timeout = bot._timeout("quick_dom"))  # noqa: SLF001
        entry:dict[str, Any] = {
            "selector_type": selector_type.name,
            "selector": selector_value,
            "found": element is not None,
        }
        if element is not None and capture_text:
            try:
                entry["text"] = (await bot._extract_visible_text(element)).strip()  # noqa: SLF001
            except Exception as exc:  # noqa: BLE001
                entry["error"] = {"type": type(exc).__name__, "message": str(exc)}
        if element is None:
            if name in DOWNLOAD_REQUIRED_CHECKS:
                missing_required.append(name)
            elif name in DOWNLOAD_OPTIONAL_CHECKS:
                missing_optional.append(name)
            else:
                raise ValueError(f"Unexpected download selector check: {name}")
        checks[name] = entry
        return element

    popup = await record("vap_ovrly_secure", By.ID, "vap-ovrly-secure")
    if popup is not None:
        try:
            await bot.web_click(By.CLASS_NAME, "mfp-close")
            checks["vap_ovrly_secure"]["closed"] = True
        except Exception as exc:  # noqa: BLE001
            checks["vap_ovrly_secure"]["close_error"] = {"type": type(exc).__name__, "message": str(exc)}

    await record("galleryimage_large", By.CLASS_NAME, "galleryimage-large")
    await record("street_address", By.ID, "street-address", capture_text = True)
    await record("viewad_contact_phone", By.ID, "viewad-contact-phone", capture_text = True)
    await record("creation_date", By.CSS_SELECTOR, DOWNLOAD_CREATION_DATE_SELECTOR, capture_text = True)

    present_count = sum(1 for item in checks.values() if isinstance(item, dict) and item.get("found"))
    total_count = len(checks)

    return {
        "ad_id": ad_id,
        "url": bot._current_page_url(),  # noqa: SLF001
        "checks": checks,
        "present_count": present_count,
        "missing_required_selectors": missing_required,
        "missing_optional_selectors": missing_optional,
        "required_checks": sorted(DOWNLOAD_REQUIRED_CHECKS),
        "optional_checks": sorted(DOWNLOAD_OPTIONAL_CHECKS),
        "total_count": total_count,
    }


async def _collect_download_creation_date_layout(bot:KleinanzeigenBot) -> dict[str, Any]:
    js = """
    (() => {
      const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
      const dateRegex = /[0-9]{2}\\.[0-9]{2}\\.[0-9]{4}/;
      const container = document.querySelector('#viewad-extra-info');
      const inspectNode = (node, index = null) => {
        if (!node) return null;
        const text = normalize(node.textContent);
        return {
          index,
          tagName: node.tagName ? node.tagName.toLowerCase() : null,
          id: node.id || null,
          className: typeof node.className === 'string' ? node.className : null,
          childCount: node.children ? node.children.length : null,
          text: text.slice(0, 220),
          containsDate: dateRegex.test(text),
          snippet: node.outerHTML ? node.outerHTML.slice(0, 320) : null,
        };
      };

      const children = container ? Array.from(container.children).map((node, index) => inspectNode(node, index + 1)) : [];
      const spans = container ? Array.from(container.querySelectorAll('span')).map((node, index) => inspectNode(node, index + 1)) : [];
      const dateLikeChildren = children.filter((item) => item && item.containsDate);
      const dateLikeSpans = spans.filter((item) => item && item.containsDate);

      return {
        found: !!container,
        id: container ? container.id || null : null,
        className: container ? (typeof container.className === 'string' ? container.className : null) : null,
        childCount: container ? container.children.length : null,
        text: container ? normalize(container.textContent).slice(0, 260) : null,
        snippet: container ? container.outerHTML.slice(0, 480) : null,
        children,
        spans,
        dateLikeChildren,
        dateLikeSpans,
      };
    })()
    """
    raw = await bot.web_execute(js)
    return dict(raw) if isinstance(raw, dict) else {"raw": raw}


def _normalize_json_payload(response:dict[str, Any]) -> dict[str, Any] | None:
    content = response.get("content")
    if isinstance(content, str):
        try:
            loaded = json.loads(content)
            return loaded if isinstance(loaded, dict) else None
        except json.JSONDecodeError:
            return None
    if isinstance(content, (bytes, bytearray)):
        try:
            loaded = json.loads(content.decode("utf-8"))
            return loaded if isinstance(loaded, dict) else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
    if isinstance(content, dict):
        return content
    return None


async def _collect_pagination_shape(bot:KleinanzeigenBot, max_pages:int) -> dict[str, Any]:
    pages:list[dict[str, Any]] = []
    page_num = 1
    visited:set[int] = set()

    while len(pages) < max_pages and page_num not in visited:
        visited.add(page_num)
        url = f"{bot.root_url}/m-meine-anzeigen-verwalten.json?sort=DEFAULT&pageNum={page_num}"
        response = await bot.web_request(url)

        if not isinstance(response, dict):
            pages.append(
                {
                    "page_requested": page_num,
                    "url": url,
                    "response_type": type(response).__name__,
                    "error": "response is not a dict",
                }
            )
            break

        data = _normalize_json_payload(response)
        if data is None:
            pages.append(
                {
                    "page_requested": page_num,
                    "url": url,
                    "response_keys": sorted(response.keys()),
                    "error": "unable to parse JSON payload",
                }
            )
            break

        paging = data.get("paging")
        page_entry:dict[str, Any] = {
            "page_requested": page_num,
            "url": url,
            "top_level_keys": sorted(data.keys()),
            "ads_count": len(data.get("ads", [])) if isinstance(data.get("ads"), list) else None,
            "paging": paging,
            "paging_keys": sorted(paging.keys()) if isinstance(paging, dict) else None,
        }
        pages.append(page_entry)

        if not isinstance(paging, dict):
            break

        next_page = paging.get("next")
        if not isinstance(next_page, int):
            break
        page_num = next_page

    has_next_without_last = any(
        isinstance(page.get("paging"), dict) and page["paging"].get("next") is not None and page["paging"].get("last") is None for page in pages
    )

    recommendation = (
        "include pagination fix: observed paging.next without paging.last"
        if has_next_without_last
        else "pagination fix optional: no missing paging.last observed in sampled pages"
    )

    return {
        "pages": pages,
        "has_next_without_last": has_next_without_last,
        "recommendation": recommendation,
    }


async def _collect_overview_pagination_dom(bot:KleinanzeigenBot) -> dict[str, Any]:
    await bot.web_open(f"{bot.root_url}/m-meine-anzeigen.html")
    await bot._dismiss_consent_banner()  # noqa: SLF001
    await bot.web_sleep(1500, 2200)

    js = """
(() => {
  const snippet = (node, size = 260) => (node ? node.outerHTML.slice(0, size) : null)
  const normalize = (text) => (text || '').replace(/\\s+/g, ' ').trim()
  const adList = document.querySelector('#my-manageitems-adlist')

  const selectorChecks = {
    pagination_class_exact: '.Pagination',
    pagination_class_contains_ci: "[class*='pagination' i]",
    pagination_data_testid_ci: "[data-testid*='pagination' i]",
    next_button_aria_exact: "button[aria-label='Nächste']",
    next_button_aria_contains_ci: "button[aria-label*='Nächste' i]",
    next_anchor_aria_contains_ci: "a[aria-label*='Nächste' i]",
    rel_next: "[rel='next']",
    aria_current_page: "[aria-current='page']"
  }

  const selectorResults = {}
  for (const [name, selector] of Object.entries(selectorChecks)) {
    const nodes = Array.from(document.querySelectorAll(selector))
    selectorResults[name] = {
      selector,
      count: nodes.length,
      found: nodes.length > 0,
      firstTag: nodes[0] ? nodes[0].tagName.toLowerCase() : null,
      firstId: nodes[0] ? nodes[0].id || null : null,
      firstClass: nodes[0] ? nodes[0].className || null : null,
      firstText: nodes[0] ? normalize(nodes[0].textContent).slice(0, 120) : null,
      firstSnippet: snippet(nodes[0]),
    }
  }

  const nextLikeControls = Array.from(document.querySelectorAll('button,a'))
    .filter((node) => {
      const aria = normalize(node.getAttribute('aria-label')).toLowerCase()
      const text = normalize(node.textContent).toLowerCase()
      const rel = normalize(node.getAttribute('rel')).toLowerCase()
      return (
        aria.includes('nächste') ||
        aria.includes('next') ||
        text.includes('nächste') ||
        text.includes('weiter') ||
        text === '>' ||
        rel === 'next'
      )
    })
    .slice(0, 30)
    .map((node) => ({
      tagName: node.tagName.toLowerCase(),
      id: node.id || null,
      className: node.className || null,
      role: node.getAttribute('role'),
      ariaLabel: node.getAttribute('aria-label'),
      rel: node.getAttribute('rel'),
      text: normalize(node.textContent).slice(0, 120),
      disabled: 'disabled' in node ? !!node.disabled : null,
      ariaDisabled: node.getAttribute('aria-disabled'),
      snippet: snippet(node),
    }))

  const paginationCandidates = Array.from(document.querySelectorAll('nav,section,div,ul,ol'))
    .filter((node) => {
      const id = normalize(node.id).toLowerCase()
      const klass = normalize(node.className).toLowerCase()
      const aria = normalize(node.getAttribute('aria-label')).toLowerCase()
      const text = normalize(node.textContent).toLowerCase()
      const hasKeyword =
        id.includes('pagination') ||
        klass.includes('pagination') ||
        aria.includes('pagination') ||
        aria.includes('seite') ||
        text.includes('nächste')
      if (!hasKeyword) return false
      return text.length <= 240
    })
    .slice(0, 20)
    .map((node) => ({
      tagName: node.tagName.toLowerCase(),
      id: node.id || null,
      className: node.className || null,
      ariaLabel: node.getAttribute('aria-label'),
      text: normalize(node.textContent).slice(0, 160),
      snippet: snippet(node),
    }))

  const cardboxesInList = adList ? adList.querySelectorAll('.cardbox').length : null

  return {
    url: location.href,
    title: document.title,
    ad_list_container: {
      found: !!adList,
      id: adList ? adList.id || null : null,
      className: adList ? adList.className || null : null,
      snippet: snippet(adList),
    },
    ad_cardbox_count_global: document.querySelectorAll('.cardbox').length,
    ad_cardbox_count_in_list: cardboxesInList,
    selector_results: selectorResults,
    next_like_controls: {
      count: nextLikeControls.length,
      controls: nextLikeControls,
    },
    pagination_candidates: {
      count: paginationCandidates.length,
      nodes: paginationCandidates,
    },
  }
})()
"""

    raw = await bot.web_execute(js)
    result = dict(raw) if isinstance(raw, dict) else {"raw": raw}

    selector_probe:dict[str, Any] = {
        "pagination_find": {
            "selector": ".Pagination",
            "timeout": bot._timeout("pagination_initial"),  # noqa: SLF001
        },
        "next_buttons_in_pagination": {
            "selector": 'button[aria-label="Nächste"]',
        },
    }

    try:
        pagination_section = await bot.web_find(
            By.CSS_SELECTOR,
            ".Pagination",
            timeout = selector_probe["pagination_find"]["timeout"],
        )
        selector_probe["pagination_find"]["found"] = True
        selector_probe["pagination_find"]["section_class"] = pagination_section.attrs.get("class")

        next_buttons = await bot.web_find_all(
            By.CSS_SELECTOR,
            'button[aria-label="Nächste"]',
            parent = pagination_section,
        )
        enabled_buttons = [button for button in next_buttons if not button.attrs.get("disabled")]
        selector_probe["next_buttons_in_pagination"].update(
            {
                "found": bool(next_buttons),
                "count": len(next_buttons),
                "enabled_count": len(enabled_buttons),
            }
        )
    except TimeoutError as exc:
        selector_probe["pagination_find"].update(
            {
                "found": False,
                "error": {
                    "type": type(exc).__name__,
                    "message": str(exc),
                },
            }
        )
        selector_probe["next_buttons_in_pagination"].update(
            {
                "found": False,
                "count": 0,
                "enabled_count": 0,
            }
        )

    result["helper_selector_probe"] = selector_probe
    return result


def _load_ad_for_verification(bot:KleinanzeigenBot, ad_file:Path) -> "Ad":
    ad_cfg_orig = dicts.load_dict(str(ad_file), "ad")
    return bot.load_ad(ad_cfg_orig)


def _override_condition_probe_ad(ad_cfg:"Ad", condition_value:str | None, category_override:str | None) -> "Ad":
    special_attributes = dict(ad_cfg.special_attributes or {})
    if condition_value is not None:
        special_attributes["condition_s"] = condition_value
    updated = ad_cfg.model_copy(update = {"special_attributes": special_attributes})
    if category_override is not None:
        updated = updated.model_copy(update = {"category": category_override})
    return updated


def _normalize_condition_value(condition_value:str) -> str:
    return CONDITION_GERMAN_TO_API.get(condition_value, condition_value)


def _condition_display_candidates(condition_value:str) -> list[str]:
    return list(CONDITION_API_TO_DISPLAY_CANDIDATES.get(_normalize_condition_value(condition_value), (condition_value,)))


def _infer_condition_route(result_item:dict[str, Any]) -> str:
    set_call = result_item.get("set_call", {}) if isinstance(result_item, dict) else {}
    bot_selection = result_item.get("bot_selection") if isinstance(result_item, dict) else None
    if not isinstance(set_call, dict) or not set_call.get("ok"):
        return "failed"
    if not isinstance(bot_selection, dict):
        return "dialog"

    tag = str(bot_selection.get("tag") or "").lower()
    role = str(bot_selection.get("role") or "").lower()
    elem_type = str(bot_selection.get("type") or "").lower()
    if tag == "select":
        return "generic_select"
    if tag == "button" and role == "combobox":
        return "generic_button_combobox"
    if tag == "input" and role == "combobox" and elem_type == "text":
        return "generic_text_combobox"
    if tag == "input":
        return "generic_text_input"
    return "generic_fallback"


def _slugify_filename(value:str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_")
    return slug or "snapshot"


async def _dismiss_consent(bot:KleinanzeigenBot) -> dict[str, Any]:
    try:
        await bot._dismiss_consent_banner()  # noqa: SLF001
        return {"ok": True}
    except TimeoutError as exc:
        return {"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}}


async def _set_category(bot:KleinanzeigenBot, category:str | None, ad_file_path:Path) -> dict[str, Any]:
    set_category = cast(
        Callable[[str | None, str], Awaitable[Any]] | None,
        getattr(bot, "_KleinanzeigenBot__set_category", None),
    )
    if not callable(set_category):
        return {"ok": False, "skipped": True, "reason": "private helper __set_category is not available"}
    try:
        await set_category(category, str(ad_file_path))
        return {"ok": True}
    except TimeoutError as exc:
        return {"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}}


async def _set_shipping(bot:KleinanzeigenBot, ad_cfg:Ad) -> dict[str, Any]:
    set_shipping = cast(
        Callable[[Ad, AdUpdateStrategy], Awaitable[Any]] | None,
        getattr(bot, "_KleinanzeigenBot__set_shipping", None),
    )
    if not callable(set_shipping):
        return {"ok": False, "skipped": True, "reason": "private helper __set_shipping is not available"}
    try:
        await set_shipping(ad_cfg, AdUpdateStrategy.REPLACE)
        return {"ok": True}
    except TimeoutError as exc:
        return {"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}}


async def _set_special_attributes(bot:KleinanzeigenBot, ad_cfg:Ad) -> dict[str, Any]:
    set_special_attributes = cast(
        Callable[[Any], Awaitable[Any]] | None,
        getattr(bot, "_KleinanzeigenBot__set_special_attributes", None),
    )
    if not callable(set_special_attributes):
        return {"ok": False, "skipped": True, "reason": "private helper __set_special_attributes is not available"}
    try:
        await set_special_attributes(ad_cfg)
        return {"ok": True}
    except TimeoutError as exc:
        return {"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}}


async def _fetch_published_ads(bot:KleinanzeigenBot) -> list[dict[str, Any]]:
    fetch_published_ads = cast(Callable[[], Awaitable[Any]] | None, getattr(bot, "_fetch_published_ads", None))
    if not callable(fetch_published_ads):
        return []
    raw = await fetch_published_ads()
    return raw if isinstance(raw, list) else []


async def _capture_dom_snapshot(bot:KleinanzeigenBot, label:str, run_id:str, dom_dir:Path) -> dict[str, Any]:
    """Capture full page HTML plus key selector state to disk for offline analysis."""
    js = """
(() => {
  const inspect = (selector) => {
    const node = document.querySelector(selector)
    return {
      selector,
      found: !!node,
      id: node ? node.id || null : null,
      name: node && 'name' in node ? node.name : null,
      type: node && 'type' in node ? node.type : null,
      role: node ? node.getAttribute('role') : null,
      checked: node && 'checked' in node ? !!node.checked : null,
      text: node ? (node.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 140) : null,
      snippet: node ? node.outerHTML.slice(0, 320) : null,
    }
  }

  return {
    url: location.href,
    title: document.title,
    readyState: document.readyState,
    selectors: {
      shipping_enabled_yes: inspect('#ad-shipping-enabled-yes'),
      shipping_enabled_no: inspect('#ad-shipping-enabled-no'),
      shipping_fieldset: inspect('#ad-shipping-enabled'),
      shipping_options: inspect('#ad-shipping-options'),
      buy_now_true: inspect('#ad-buy-now-true'),
      buy_now_false: inspect('#ad-buy-now-false'),
      buy_now_fieldset: inspect('#ad-buy-now'),
    },
    html: document.documentElement.outerHTML,
  }
})()
"""
    raw = await bot.web_execute(js)
    parsed = dict(raw) if isinstance(raw, dict) else {"raw": raw}

    html = parsed.pop("html", None)
    os.makedirs(dom_dir, exist_ok = True)
    file_name = f"dom-assumptions-{run_id}_{_slugify_filename(label)}.html"
    file_path = dom_dir / file_name

    if isinstance(html, str):
        file_path.write_text(html, encoding = "utf-8")
        saved = True
    else:
        saved = False

    result = {
        "label": label,
        "saved": saved,
        "meta": parsed,
    }
    if saved:
        result["html_path"] = str(file_path)
    return result


async def _collect_selector_state_snapshot(bot:KleinanzeigenBot) -> dict[str, Any]:
    """Collect lightweight selector state for shipping + buy-now controls."""
    js = """
(() => {
  const inspect = (selector) => {
    const node = document.querySelector(selector)
    return {
      selector,
      found: !!node,
      id: node ? node.id || null : null,
      name: node && 'name' in node ? node.name : null,
      value: node && 'value' in node ? node.value : null,
      type: node && 'type' in node ? node.type : null,
      checked: node && 'checked' in node ? !!node.checked : null,
      role: node ? node.getAttribute('role') : null,
      snippet: node ? node.outerHTML.slice(0, 280) : null,
    }
  }
  return {
    url: location.href,
    shipping_yes: inspect('#ad-shipping-enabled-yes'),
    shipping_no: inspect('#ad-shipping-enabled-no'),
    shipping_options: inspect('#ad-shipping-options'),
    shipping_fieldset: inspect('#ad-shipping-enabled'),
    buy_now_true: inspect('#ad-buy-now-true'),
    buy_now_false: inspect('#ad-buy-now-false'),
    buy_now_fieldset: inspect('#ad-buy-now'),
  }
})()
"""
    raw = await bot.web_execute(js)
    return dict(raw) if isinstance(raw, dict) else {"raw": raw}


async def _probe_shipping_and_sell_directly_live(
    bot:KleinanzeigenBot,
    ad_cfg:"Ad",
    ad_file_path:Path,
    *,
    run_id:str,
    save_dom:bool = False,
    dom_dir:Path | None = None,
) -> dict[str, Any]:
    """Exercise live OFFER shipping + sell_directly logic and capture evidence.

    This probe calls the same private helpers used by ``publish_ad`` and records
    selector state before/after to verify issue assumptions against real DOM.
    """
    result:dict[str, Any] = {
        "setup": {},
        "selector_state": {},
        "set_shipping": {},
        "sell_directly": {},
        "dom_snapshots": [],
    }

    async def _maybe_snapshot(label:str) -> None:
        if save_dom and dom_dir is not None:
            result["dom_snapshots"].append(await _capture_dom_snapshot(bot, label, run_id, dom_dir))

    await bot.web_open(f"{bot.root_url}/p-anzeige-aufgeben-schritt2.html")
    await bot._dismiss_consent_banner()  # noqa: SLF001

    ad_type_radio_id = f"ad-type-{ad_cfg.type}"
    try:
        quick_timeout = bot._timeout("quick_dom")  # noqa: SLF001
        if not await bot.web_check(By.ID, ad_type_radio_id, Is.SELECTED, timeout = quick_timeout):
            await bot.web_click(By.ID, ad_type_radio_id, timeout = quick_timeout)
            await bot.web_sleep(1200, 1600)
        result["setup"]["set_ad_type"] = {"ok": True, "radio": ad_type_radio_id}
    except TimeoutError as exc:
        result["setup"]["set_ad_type"] = {
            "ok": False,
            "radio": ad_type_radio_id,
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }

    set_category = cast(
        Callable[[str | None, str], Awaitable[Any]] | None,
        getattr(bot, "_KleinanzeigenBot__set_category", None),
    )
    if callable(set_category):
        try:
            await set_category(ad_cfg.category, str(ad_file_path))
            result["setup"]["set_category"] = {"ok": True}
        except TimeoutError as exc:
            result["setup"]["set_category"] = {
                "ok": False,
                "error": {"type": type(exc).__name__, "message": str(exc)},
            }
    else:
        result["setup"]["set_category"] = {
            "ok": False,
            "error": {"type": "AttributeError", "message": "__set_category helper not available"},
        }

    # Ensure we inspect and probe on the publish step form itself.
    await bot.web_open(f"{bot.root_url}/p-anzeige-aufgeben-schritt2.html")
    await bot._dismiss_consent_banner()  # noqa: SLF001
    try:
        quick_timeout = bot._timeout("quick_dom")  # noqa: SLF001
        if not await bot.web_check(By.ID, ad_type_radio_id, Is.SELECTED, timeout = quick_timeout):
            await bot.web_click(By.ID, ad_type_radio_id, timeout = quick_timeout)
            await bot.web_sleep(1200, 1600)
        result["setup"]["set_ad_type_after_nav"] = {"ok": True, "radio": ad_type_radio_id}
    except TimeoutError as exc:
        result["setup"]["set_ad_type_after_nav"] = {
            "ok": False,
            "radio": ad_type_radio_id,
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }

    # Re-apply category on the probe page to mirror publish_ad ordering and
    # ensure category-dependent shipping controls are rendered before probing.
    if callable(set_category):
        try:
            await set_category(ad_cfg.category, str(ad_file_path))
            result["setup"]["set_category_after_nav"] = {"ok": True}
        except TimeoutError as exc:
            result["setup"]["set_category_after_nav"] = {
                "ok": False,
                "error": {"type": type(exc).__name__, "message": str(exc)},
            }

    await bot.web_sleep(1200, 1600)

    result["selector_state"]["before"] = await _collect_selector_state_snapshot(bot)
    await _maybe_snapshot(f"{ad_cfg.type}_{ad_cfg.shipping_type}_before_set_shipping")

    set_shipping = cast(
        Callable[["Ad", AdUpdateStrategy], Awaitable[Any]] | None,
        getattr(bot, "_KleinanzeigenBot__set_shipping", None),
    )
    if not callable(set_shipping):
        result["set_shipping"] = {
            "ok": False,
            "skipped": True,
            "reason": "private helper __set_shipping is not available",
        }
    elif ad_cfg.type == "WANTED" or ad_cfg.shipping_type == "NOT_APPLICABLE":
        result["set_shipping"] = {
            "ok": True,
            "skipped": True,
            "reason": "shipping setter probe only applies to OFFER with explicit shipping type",
        }
    else:
        try:
            await set_shipping(ad_cfg, AdUpdateStrategy.REPLACE)
            result["set_shipping"] = {"ok": True}
        except TimeoutError as exc:
            result["set_shipping"] = {
                "ok": False,
                "error": {"type": type(exc).__name__, "message": str(exc)},
            }

    result["selector_state"]["after_set_shipping"] = await _collect_selector_state_snapshot(bot)
    await _maybe_snapshot(f"{ad_cfg.type}_{ad_cfg.shipping_type}_after_set_shipping")

    # Probe sell_directly selector behavior using the same logic as publish_ad.
    if ad_cfg.type == "WANTED":
        result["sell_directly"] = {
            "ok": True,
            "skipped": True,
            "reason": "sell_directly is only evaluated for OFFER ads",
        }
    else:
        try:
            short_timeout = bot._timeout("quick_dom")  # noqa: SLF001
            path = ""
            if ad_cfg.shipping_type == "SHIPPING":
                if ad_cfg.sell_directly and ad_cfg.price_type in {"FIXED", "NEGOTIABLE"}:
                    if not await bot.web_check(By.ID, "ad-buy-now-true", Is.SELECTED, timeout = short_timeout):
                        await bot.web_click(By.ID, "ad-buy-now-true", timeout = short_timeout)
                    path = "shipping_enable_true"
                else:
                    if not await bot.web_check(By.ID, "ad-buy-now-false", Is.SELECTED, timeout = short_timeout):
                        await bot.web_click(By.ID, "ad-buy-now-false", timeout = short_timeout)
                    path = "shipping_enable_false"
            else:
                try:
                    if not await bot.web_check(By.ID, "ad-buy-now-false", Is.SELECTED, timeout = short_timeout):
                        await bot.web_click(By.ID, "ad-buy-now-false", timeout = short_timeout)
                    path = "pickup_or_other_force_false"
                except TimeoutError:
                    # Mirrors publish_ad behavior: buy-now controls may be absent
                    # for PICKUP/other non-shipping flows and should not fail.
                    path = "pickup_or_other_buy_now_not_present"

            result["sell_directly"] = {"ok": True, "path": path}
        except TimeoutError as exc:
            result["sell_directly"] = {
                "ok": False,
                "error": {"type": type(exc).__name__, "message": str(exc)},
            }

    result["selector_state"]["after_sell_directly"] = await _collect_selector_state_snapshot(bot)
    await _maybe_snapshot(f"{ad_cfg.type}_{ad_cfg.shipping_type}_after_sell_directly")

    return result


async def _exercise_ad_form_fields(bot:KleinanzeigenBot, ad_cfg:"Ad") -> dict[str, Any]:
    await bot.web_open(f"{bot.root_url}/p-anzeige-aufgeben-schritt2.html")
    await bot._dismiss_consent_banner()  # noqa: SLF001

    result:dict[str, Any] = {"steps": {}}

    async def set_title() -> dict[str, Any]:
        await bot.web_input(By.CSS_SELECTOR, "#ad-title, #postad-title", ad_cfg.title)
        state = await bot.web_execute(
            """
(() => {
  const elem = document.querySelector('#ad-title, #postad-title')
  if (!elem) return { found: false }
  return { found: true, id: elem.id, value: elem.value }
})()
"""
        )
        return dict(state) if isinstance(state, dict) else {"raw": state}

    async def set_description() -> dict[str, Any]:
        description = ad_cfg.description or ""
        state = await bot.web_execute(
            """
((value) => {
  const elem = document.querySelector('#ad-description,#pstad-descrptn')
  if (!elem) return { found: false }
  elem.value = value
  elem.dispatchEvent(new Event('input', { bubbles: true }))
  return { found: true, id: elem.id, readOnly: !!elem.readOnly, valueLength: elem.value.length }
})(arguments[0])
""".replace("arguments[0]", json.dumps(description))
        )
        return dict(state) if isinstance(state, dict) else {"raw": state}

    async def set_price() -> dict[str, Any]:
        if ad_cfg.price is None:
            return {"skipped": True, "reason": "ad has no price"}
        selector = "input#ad-price-amount, input#post-ad-frontend-price, input#micro-frontend-price, input#pstad-price"
        await bot.web_input(By.CSS_SELECTOR, selector, str(ad_cfg.price))
        state = await bot.web_execute(
            """
(() => {
  const elem = document.querySelector('input#ad-price-amount, input#post-ad-frontend-price, input#micro-frontend-price, input#pstad-price')
  if (!elem) return { found: false }
  return { found: true, id: elem.id, value: elem.value }
})()
"""
        )
        return dict(state) if isinstance(state, dict) else {"raw": state}

    async def set_zip_and_probe_city() -> dict[str, Any]:
        if not ad_cfg.contact.zipcode:
            return {"skipped": True, "reason": "ad has no zipcode"}
        await bot.web_input(By.CSS_SELECTOR, "#ad-zip-code, #pstad-zip", ad_cfg.contact.zipcode)
        await bot.web_sleep(500)
        state = await bot.web_execute(
            """
(() => {
  const zipElem = document.querySelector('#ad-zip-code, #pstad-zip')
  const cityElem = document.querySelector('#ad-city, #pstad-citychsr')
  if (!zipElem) return { zipFound: false }
  return {
    zipFound: true,
    zipId: zipElem.id,
    zipValue: zipElem.value,
    cityFound: !!cityElem,
    cityId: cityElem ? cityElem.id : null,
    cityTag: cityElem ? cityElem.tagName.toLowerCase() : null,
    cityReadOnly: cityElem ? !!cityElem.readOnly : null,
    cityDisabled: cityElem ? !!cityElem.disabled : null,
    cityValue: cityElem ? ('value' in cityElem ? cityElem.value : cityElem.textContent) : null
  }
})()
"""
        )
        return dict(state) if isinstance(state, dict) else {"raw": state}

    async def set_contact_location_via_bot_helper() -> dict[str, Any]:
        if not ad_cfg.contact.zipcode:
            return {"skipped": True, "reason": "ad has no zipcode"}
        if not ad_cfg.contact.location:
            return {"skipped": True, "reason": "ad has no location"}

        set_contact_location = cast(
            Callable[[str], Awaitable[Any]] | None,
            getattr(bot, "_KleinanzeigenBot__set_contact_location", None),
        )
        read_city_selection_text = cast(
            Callable[[], Awaitable[str]] | None,
            getattr(bot, "_KleinanzeigenBot__read_city_selection_text", None),
        )
        location_matches_target = cast(
            Callable[[str, str | None], bool] | None,
            getattr(bot, "_KleinanzeigenBot__location_matches_target", None),
        )
        if not callable(set_contact_location) or not callable(read_city_selection_text) or not callable(location_matches_target):
            return {
                "skipped": True,
                "reason": (
                    "contact-location helper methods are not available on this bot build "
                    "(expected private methods: __set_contact_location, __read_city_selection_text, __location_matches_target)"
                ),
            }

        await bot.web_input(By.ID, "ad-zip-code", ad_cfg.contact.zipcode)
        await bot.web_sleep(500)

        await set_contact_location(ad_cfg.contact.location)
        await bot.web_sleep(300)

        selected_city = await read_city_selection_text()
        match_ok = location_matches_target(ad_cfg.contact.location, selected_city)

        dropdown_probe:dict[str, Any] = {"opened": False, "option_count": 0, "sample": []}
        option_selector = "[role='option'], li[aria-selected='true'], li[aria-selected='false'], button[aria-selected='true'], button[aria-selected='false']"
        try:
            await bot.web_click(By.ID, "ad-city", timeout = bot._timeout("quick_dom"))  # noqa: SLF001
            options = await bot.web_find_all(By.CSS_SELECTOR, option_selector, timeout = bot._timeout("quick_dom"))  # noqa: SLF001
            sample:list[str] = []
            for option in options[:10]:
                text = str(getattr(option, "text", "") or "").strip()
                if not text:
                    text = (await bot._extract_visible_text(option)).strip()  # noqa: SLF001
                if text:
                    sample.append(text)
            dropdown_probe = {
                "opened": True,
                "option_count": len(options),
                "sample": sample,
            }
        except TimeoutError as exc:
            dropdown_probe = {
                "opened": False,
                "option_count": 0,
                "sample": [],
                "error": {
                    "type": type(exc).__name__,
                    "message": str(exc),
                },
            }

        state = await bot.web_execute(
            """
(() => {
  const zipElem = document.querySelector('#ad-zip-code')
  const cityElem = document.querySelector('#ad-city')
  const locationIdElem = document.querySelector('input[name="locationId"]')
  return {
    zipFound: !!zipElem,
    zipValue: zipElem ? zipElem.value : null,
    cityFound: !!cityElem,
    cityTag: cityElem ? cityElem.tagName.toLowerCase() : null,
    cityReadOnly: cityElem ? !!cityElem.readOnly : null,
    cityValue: cityElem ? ('value' in cityElem ? cityElem.value : cityElem.textContent) : null,
    locationIdFound: !!locationIdElem,
    locationIdValue: locationIdElem ? locationIdElem.value : null,
  }
})()
"""
        )
        state_dict = dict(state) if isinstance(state, dict) else {"raw": state}
        state_dict.update(
            {
                "targetLocation": ad_cfg.contact.location,
                "selectedCityText": selected_city,
                "targetMatch": bool(match_ok),
                "dropdown": dropdown_probe,
            }
        )
        return state_dict

    checks:list[tuple[str, Any]] = [
        ("set_title", set_title),
        ("set_description", set_description),
        ("set_price", set_price),
        ("set_zip_and_probe_city", set_zip_and_probe_city),
        ("set_contact_location_via_bot_helper", set_contact_location_via_bot_helper),
    ]

    for name, operation in checks:
        try:
            result["steps"][name] = await operation()
        except TimeoutError as exc:
            result["steps"][name] = {"error": {"type": type(exc).__name__, "message": str(exc)}}

    return result


async def _probe_button_combobox_options(bot:KleinanzeigenBot, ad_cfg:"Ad", ad_file_path:Path) -> dict[str, Any]:
    """Probe all <button role='combobox'> special-attribute dropdowns for issue #930.

    For each button combobox on the publish form (after category is set), this:
    1. Opens the dropdown by clicking the button
    2. Captures all <li role='option'> elements' attributes (data-value, value, id, etc.)
    3. Captures React fiber's options[] mapping (api_value → display_label) for comparison
    4. Captures the hidden input state (name, current value)
    5. Closes the dropdown by clicking the button again (or pressing Escape)

    The output determines whether a DOM-attribute-based migration (Branch A) is viable
    or whether a click-and-verify approach (Branch B) is needed.
    """
    short_timeout = bot._timeout("quick_dom")  # noqa: SLF001

    await bot.web_open(f"{bot.root_url}/p-anzeige-aufgeben-schritt2.html")
    await bot._dismiss_consent_banner()  # noqa: SLF001

    setup:dict[str, Any] = {}

    # Set ad type
    ad_type_radio_id = f"ad-type-{ad_cfg.type}"
    try:
        if not await bot.web_check(By.ID, ad_type_radio_id, Is.SELECTED, timeout = short_timeout):
            await bot.web_click(By.ID, ad_type_radio_id, timeout = short_timeout)
            await bot.web_sleep(1200, 1600)
        setup["set_ad_type"] = {"radio": ad_type_radio_id, "clicked": True}
    except TimeoutError as exc:
        setup["set_ad_type"] = {"radio": ad_type_radio_id, "error": {"type": type(exc).__name__, "message": str(exc)}}

    # Set category to ensure category-dependent attributes render
    set_category = cast(
        Callable[[str | None, str], Awaitable[Any]] | None,
        getattr(bot, "_KleinanzeigenBot__set_category", None),
    )
    if callable(set_category):
        try:
            await set_category(ad_cfg.category, str(ad_file_path))
            setup["set_category"] = "ok"
        except TimeoutError as exc:
            setup["set_category"] = {"error": {"type": type(exc).__name__, "message": str(exc)}}
    else:
        setup["set_category"] = {"error": {"type": "AttributeError", "message": "__set_category helper not available"}}

    # Navigate back to publish form after category flow
    await bot.web_open(f"{bot.root_url}/p-anzeige-aufgeben-schritt2.html")
    await bot._dismiss_consent_banner()  # noqa: SLF001

    # Re-select ad type after navigation
    try:
        if not await bot.web_check(By.ID, ad_type_radio_id, Is.SELECTED, timeout = short_timeout):
            await bot.web_click(By.ID, ad_type_radio_id, timeout = short_timeout)
            await bot.web_sleep(1200, 1600)
        setup["set_ad_type_after_nav"] = {"radio": ad_type_radio_id, "clicked": True}
    except TimeoutError as exc:
        setup["set_ad_type_after_nav"] = {"radio": ad_type_radio_id, "error": {"type": type(exc).__name__, "message": str(exc)}}

    # Re-apply category
    if callable(set_category):
        try:
            await set_category(ad_cfg.category, str(ad_file_path))
            setup["set_category_after_nav"] = "ok"
        except TimeoutError as exc:
            setup["set_category_after_nav"] = {"error": {"type": type(exc).__name__, "message": str(exc)}}

    await bot.web_sleep(1200, 1600)

    # ── Discover and probe all button comboboxes ─────────────────────────────
    discovery_js = """
    (() => {
        const normText = (v) => (v || '').replace(/\\s+/g, ' ').trim();
        const buttons = Array.from(document.querySelectorAll('button[role="combobox"]'));
        const hiddenInputs = Array.from(document.querySelectorAll(
            "input[type='hidden'][name^='attributeMap[']"
        ));

        return {
            buttonComboboxes: buttons.map((btn) => ({
                id: btn.id || null,
                name: btn.getAttribute('name') || null,
                ariaControls: btn.getAttribute('aria-controls') || null,
                text: normText(btn.textContent).slice(0, 120),
                ariaExpanded: btn.getAttribute('aria-expanded'),
                ariaHasPopup: btn.getAttribute('aria-haspopup'),
            })),
            hiddenInputs: hiddenInputs.map((inp) => ({
                id: inp.id || null,
                name: inp.getAttribute('name') || null,
                value: inp.value || null,
            })),
        };
    })()
    """

    discovery_raw = await bot.web_execute(discovery_js)
    discovery = dict(discovery_raw) if isinstance(discovery_raw, dict) else {"raw": discovery_raw}

    button_comboboxes = discovery.get("buttonComboboxes", []) if isinstance(discovery, dict) else []
    hidden_inputs = discovery.get("hiddenInputs", []) if isinstance(discovery, dict) else []

    setup["discovery"] = discovery

    # ── Probe each button combobox individually ──────────────────────────────
    probe_js_template = """
    (async (btnId) => {
        const btn = document.getElementById(btnId);
        if (!btn) return {error: "button not found", btnId};

        // Capture all attributes on the button for diagnostics
        const btnAttrs = {};
        for (const attr of btn.attributes) {
            btnAttrs[attr.name] = attr.value;
        }

        // Open dropdown
        btn.click();

        // Wait a moment for the dropdown to render (React may need a tick)
        await new Promise(r => setTimeout(r, 300));

        // Try multiple strategies to find the listbox:
        // 1. aria-controls on the button
        // 2. {btnId}-menu convention
        // 3. Any visible [role="listbox"] in the document (React portal)
        // 4. Popover API: btn.popoverTargetElement or [popover] related to btn
        const ariaControls = btn.getAttribute('aria-controls');
        let listbox = null;
        let listboxSource = null;

        if (ariaControls) {
            listbox = document.getElementById(ariaControls);
            if (listbox) listboxSource = 'aria-controls';
        }
        if (!listbox) {
            const fallbackId = btnId + '-menu';
            listbox = document.getElementById(fallbackId);
            if (listbox) listboxSource = 'fallback-menu-id';
        }
        if (!listbox) {
            // Look for any visible listbox (React portals render outside the component tree)
            const allListboxes = Array.from(document.querySelectorAll('[role="listbox"]'));
            // Prefer listboxes that are visible (not display:none)
            const visibleListboxes = allListboxes.filter(lb => {
                const style = window.getComputedStyle(lb);
                return style.display !== 'none' && style.visibility !== 'hidden';
            });
            if (visibleListboxes.length === 1) {
                listbox = visibleListboxes[0];
                listboxSource = 'single-visible-listbox';
            } else if (visibleListboxes.length > 1) {
                // Multiple visible listboxes – return diagnostics
                return {
                    btnId,
                    listboxFound: false,
                    listboxSource: 'multiple-visible-listboxes',
                    visibleListboxCount: visibleListboxes.length,
                    totalListboxCount: allListboxes.length,
                    visibleListboxIds: visibleListboxes.map(lb => lb.id || null),
                    btnText: (btn.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 120),
                    btnAttrs,
                };
            }
        }
        if (!listbox) {
            // Also check for popover panels or floating UI elements
            const popovers = Array.from(document.querySelectorAll('[role="listbox"], [data-radix-popper-content-wrapper], [data-popover], [data-floating]'));
            const visiblePopovers = popovers.filter(el => {
                const style = window.getComputedStyle(el);
                return style.display !== 'none' && style.visibility !== 'hidden';
            });
            if (visiblePopovers.length > 0) {
                return {
                    btnId,
                    listboxFound: false,
                    listboxSource: 'found-popovers-but-no-listbox',
                    popoverCount: visiblePopovers.length,
                    popoverInfo: visiblePopovers.slice(0, 5).map(el => ({
                        tag: el.tagName,
                        role: el.getAttribute('role'),
                        id: el.id || null,
                        classes: el.className.slice(0, 100),
                    })),
                    btnText: (btn.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 120),
                    btnAttrs,
                };
            }

            // Close dropdown (may not have opened)
            btn.click();

            return {
                btnId,
                listboxFound: false,
                listboxSource: 'none',
                ariaControls: ariaControls,
                btnText: (btn.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 120),
                btnAttrs,
                allListboxCount: document.querySelectorAll('[role="listbox"]').length,
            };
        }

        const items = Array.from(listbox.querySelectorAll('[role="option"]'));

        // Capture ALL attributes on each <li role="option">
        const domOptions = items.map((li, idx) => {
            const attrs = {};
            for (const attr of li.attributes) {
                attrs[attr.name] = attr.value;
            }
            return {
                index: idx,
                textContent: (li.textContent || '').replace(/\\s+/g, ' ').trim(),
                innerText: (li.innerText || '').replace(/\\s+/g, ' ').trim(),
                id: li.id || null,
                role: li.getAttribute('role'),
                ariaSelected: li.getAttribute('aria-selected'),
                tabIndex: li.getAttribute('tabIndex'),
                dataValue: li.getAttribute('data-value') || null,
                valueAttr: li.getAttribute('value') || null,
                dataKey: li.getAttribute('data-key') || null,
                dataOptionValue: li.getAttribute('data-option-value') || null,
                allAttributes: attrs,
                dataset: {...li.dataset},
            };
        });

        // Capture React fiber options mapping (value → label)
        let fiberOptions = null;
        let fiberDepth = null;
        const fiberKey = Object.keys(btn).find(k => k.startsWith('__reactFiber'));
        let fiber = fiberKey ? btn[fiberKey] : null;
        for (let i = 0; i < 25 && fiber; i++, fiber = fiber.return) {
            if (fiber.memoizedProps && Array.isArray(fiber.memoizedProps.options)) {
                fiberOptions = fiber.memoizedProps.options.map((opt) => ({
                    value: opt && 'value' in opt ? opt.value : null,
                    label: opt && 'label' in opt ? opt.label : null,
                }));
                fiberDepth = i;
                break;
            }
        }

        // Find hidden input associated with this combobox
        // The hidden input name is typically attributeMap[{btnId}] or attributeMap[{btnId without dot-prefix}]
        const hiddenInputCandidates = Array.from(document.querySelectorAll(
            "input[type='hidden'][name^='attributeMap[']"
        )).filter((inp) => {
            const name = inp.getAttribute('name') || '';
            // Match by button id as suffix in attributeMap name
            if (name.includes(btnId)) return true;
            // Match by the last segment of button id (e.g., "art" from "autoteile_reifen.art")
            const lastSegment = btnId.includes('.') ? btnId.split('.').pop() : null;
            return lastSegment && name.includes(lastSegment);
        });

        const hiddenInputInfo = hiddenInputCandidates.map((inp) => ({
            id: inp.id || null,
            name: inp.getAttribute('name'),
            value: inp.value || null,
        }));

        // Close dropdown by clicking the button again
        btn.click();

        return {
            btnId,
            listboxId: listbox.id || null,
            listboxSource,
            listboxFound: true,
            optionCount: items.length,
            btnText: (btn.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 120),
            domOptions,
            fiberOptions,
            fiberDepth,
            fiberFound: fiberOptions !== null,
            hiddenInputs: hiddenInputInfo,
            btnAttrs,
        };
    })
    """

    combobox_probes:list[dict[str, Any]] = []
    if isinstance(button_comboboxes, list):
        for combo in button_comboboxes:
            if not isinstance(combo, dict):
                continue
            combo_id = combo.get("id")
            if not combo_id or not isinstance(combo_id, str):
                continue

            try:
                await bot.web_sleep(300, 500)
                raw_result = await bot.web_execute(f"{probe_js_template}({json.dumps(combo_id)})")
                probe_result = dict(raw_result) if isinstance(raw_result, dict) else {"raw": raw_result}
                combobox_probes.append(probe_result)
            except TimeoutError as exc:
                combobox_probes.append(
                    {
                        "btnId": combo_id,
                        "error": {"type": type(exc).__name__, "message": str(exc)},
                    }
                )

    # ── Analysis: determine Branch A vs Branch B viability ───────────────────
    analysis:dict[str, Any] = {
        "total_button_comboboxes": len(button_comboboxes) if isinstance(button_comboboxes, list) else 0,
        "total_hidden_inputs": len(hidden_inputs) if isinstance(hidden_inputs, list) else 0,
        "probes_attempted": len(combobox_probes),
        "probes_succeeded": sum(1 for p in combobox_probes if isinstance(p, dict) and p.get("listboxFound")),
        "fiber_data_available": sum(1 for p in combobox_probes if isinstance(p, dict) and p.get("fiberFound")),
    }

    # Check if any <li> elements carry data-value or value attributes with API values
    has_data_value = False
    has_value_attr = False
    for probe in combobox_probes:
        if not isinstance(probe, dict) or not probe.get("domOptions"):
            continue
        for opt in probe["domOptions"]:
            if not isinstance(opt, dict):
                continue
            if opt.get("dataValue") is not None:
                has_data_value = True
            if opt.get("valueAttr") is not None:
                has_value_attr = True

    # Cross-reference: do data-value/value attributes match fiber API values?
    attribute_matches_fiber = False
    attribute_details:list[dict[str, Any]] = []
    for probe in combobox_probes:
        if not isinstance(probe, dict) or not isinstance(probe.get("domOptions"), list) or not isinstance(probe.get("fiberOptions"), list):
            continue
        dom_opts = probe["domOptions"]
        fiber_opts = probe["fiberOptions"]
        for idx, dom_opt in enumerate(dom_opts):
            if not isinstance(dom_opt, dict):
                continue
            if idx < len(fiber_opts) and isinstance(fiber_opts[idx], dict):
                fiber_val = fiber_opts[idx].get("value")
                fiber_lbl = fiber_opts[idx].get("label")
                dom_dv = dom_opt.get("dataValue")
                dom_va = dom_opt.get("valueAttr")
                dom_txt = dom_opt.get("textContent")
                match_info:dict[str, Any] = {
                    "index": idx,
                    "fiber_value": fiber_val,
                    "fiber_label": fiber_lbl,
                    "dom_data_value": dom_dv,
                    "dom_value_attr": dom_va,
                    "dom_text": dom_txt,
                }
                if dom_dv is not None and dom_dv == fiber_val:
                    attribute_matches_fiber = True
                    match_info["data_value_matches_fiber"] = True
                if dom_va is not None and dom_va == fiber_val:
                    attribute_matches_fiber = True
                    match_info["value_attr_matches_fiber"] = True
                # Check text normalization feasibility
                if fiber_val is not None and dom_txt is not None:
                    # Simple normalization: lowercase, replace _/- with space, collapse whitespace
                    norm_api = " ".join(str(fiber_val).replace("_", " ").replace("-", " ").lower().split())
                    norm_txt = " ".join(str(dom_txt).replace("_", " ").replace("-", " ").lower().split())
                    match_info["text_match_feasible"] = norm_api == norm_txt
                attribute_details.append(match_info)

    analysis["has_data_value_attr"] = has_data_value
    analysis["has_value_attr"] = has_value_attr
    analysis["attribute_matches_fiber"] = attribute_matches_fiber
    analysis["attribute_cross_reference"] = attribute_details
    analysis["recommendation"] = (
        "branch_a: data-value or value attribute found on <li> options, DOM-attribute matching is viable"
        if attribute_matches_fiber
        else (
            "branch_a_possible: data-value attributes exist but no fiber cross-reference available"
            if has_data_value or has_value_attr
            else "branch_b: no data-value/value attributes on <li> options, click-and-verify or text normalization needed"
        )
    )

    return {
        "setup": setup,
        "combobox_probes": combobox_probes,
        "analysis": analysis,
    }


async def _collect_shipping_radio_inventory(bot:KleinanzeigenBot, ad_cfg:"Ad", ad_file_path:Path) -> dict[str, Any]:
    await bot.web_open(f"{bot.root_url}/p-anzeige-aufgeben-schritt2.html")
    await bot._dismiss_consent_banner()  # noqa: SLF001

    setup:dict[str, Any] = {}

    # Switch ad type to match the ad config (OFFER is default; WANTED must be clicked explicitly).
    # This is critical because the shipping section is conditionally rendered based on ad type.
    ad_type_radio_id = f"ad-type-{ad_cfg.type}"
    try:
        if not await bot.web_check(By.ID, ad_type_radio_id, Is.SELECTED, timeout = bot._timeout("quick_dom")):  # noqa: SLF001
            await bot.web_click(By.ID, ad_type_radio_id, timeout = bot._timeout("quick_dom"))  # noqa: SLF001
            await bot.web_sleep(1500)
        setup["set_ad_type"] = {"radio": ad_type_radio_id, "clicked": True}
    except TimeoutError as exc:
        setup["set_ad_type"] = {"radio": ad_type_radio_id, "error": {"type": type(exc).__name__, "message": str(exc)}}

    set_category = cast(
        Callable[[str | None, str], Awaitable[Any]] | None,
        getattr(bot, "_KleinanzeigenBot__set_category", None),
    )
    if callable(set_category):
        try:
            await set_category(ad_cfg.category, str(ad_file_path))
            setup["set_category"] = "ok"
        except TimeoutError as exc:
            setup["set_category"] = {"error": {"type": type(exc).__name__, "message": str(exc)}}
    else:
        setup["set_category"] = {"error": {"type": "AttributeError", "message": "__set_category helper not available"}}

    # Ensure we inspect the publish form itself (category flow may leave us on intermediate route).
    await bot.web_open(f"{bot.root_url}/p-anzeige-aufgeben-schritt2.html")
    await bot._dismiss_consent_banner()  # noqa: SLF001

    # Re-select the ad type radio after page navigation (category flow may reset the form).
    try:
        if not await bot.web_check(By.ID, ad_type_radio_id, Is.SELECTED, timeout = bot._timeout("quick_dom")):  # noqa: SLF001
            await bot.web_click(By.ID, ad_type_radio_id, timeout = bot._timeout("quick_dom"))  # noqa: SLF001
            await bot.web_sleep(1500)
        setup["set_ad_type_after_nav"] = {"radio": ad_type_radio_id, "clicked": True}
    except TimeoutError as exc:
        setup["set_ad_type_after_nav"] = {"radio": ad_type_radio_id, "error": {"type": type(exc).__name__, "message": str(exc)}}

    js = """
(() => {
  const toSnippet = (node, size = 280) => (node ? node.outerHTML.slice(0, size) : null)
  const normText = (value) => (value || '').replace(/\\s+/g, ' ').trim()
  const keywordRegex = /abholung|versand|direkt kaufen|sicher bezahlen|buy now|pickup/i
  const expectedSpecialAttributeKeywords = {
    art: ['art'],
    condition: ['condition', 'zustand'],
    brand: ['brand', 'marke'],
    color: ['color', 'farbe'],
    groesse: ['groesse', 'größe', 'size']
  }
  const normalizedText = (node) => normText(node && 'textContent' in node ? node.textContent : '').toLowerCase()
  const normalizedAttr = (node, attr) => (node && node.getAttribute(attr) ? node.getAttribute(attr).toLowerCase() : '')

  const radios = Array.from(document.querySelectorAll("input[type='radio']")).map((node) => ({
    id: node.id || null,
    name: node.getAttribute('name'),
    value: node.value || null,
    checked: !!node.checked,
    disabled: !!node.disabled,
    ariaLabel: node.getAttribute('aria-label'),
    ariaChecked: node.getAttribute('aria-checked'),
    role: node.getAttribute('role'),
    dataTestId: node.getAttribute('data-testid'),
    snippet: toSnippet(node),
  }))

  const labels = Array.from(document.querySelectorAll('label'))
    .filter((node) => keywordRegex.test(normText(node.textContent).toLowerCase()) || keywordRegex.test((node.htmlFor || '').toLowerCase()))
    .slice(0, 30)
    .map((node) => {
      const target = node.htmlFor ? document.getElementById(node.htmlFor) : null
      return {
        htmlFor: node.htmlFor || null,
        text: normText(node.textContent).slice(0, 220),
        targetId: target ? target.id || null : null,
        targetName: target ? target.getAttribute('name') : null,
        targetType: target && 'type' in target ? target.type : null,
        snippet: toSnippet(node),
      }
    })

  const hiddenInputs = Array.from(document.querySelectorAll("input[type='hidden']"))
    .filter((node) => {
      const name = (node.getAttribute('name') || '').toLowerCase()
      const id = (node.id || '').toLowerCase()
      return keywordRegex.test(name) || keywordRegex.test(id)
    })
    .map((node) => ({
      id: node.id || null,
      name: node.getAttribute('name'),
      value: node.getAttribute('value'),
      snippet: toSnippet(node),
    }))

  const keywordNodes = Array.from(document.querySelectorAll('fieldset, section, div, button, span, a'))
    .filter((node) => {
      const text = normText(node.textContent)
      if (!text) return false
      if (text.length > 300) return false
      return keywordRegex.test(text)
    })
    .slice(0, 40)
    .map((node) => ({
      tagName: node.tagName.toLowerCase(),
      id: node.id || null,
      role: node.getAttribute('role'),
      text: normText(node.textContent).slice(0, 220),
      snippet: toSnippet(node),
    }))

  const specialAttributeControls = Array.from(document.querySelectorAll("input, select, textarea, button, label, fieldset"))
    .filter((node) => {
      const id = (node.id || '').toLowerCase()
      const name = normalizedAttr(node, 'name')
      const htmlFor = normalizedAttr(node, 'for')
      const text = normalizedText(node)
      if (id.includes('attributemap') || name.includes('attributemap') || htmlFor.includes('attributemap')) {
        return true
      }
      return Object.values(expectedSpecialAttributeKeywords).flat().some((token) =>
        id.includes(token) || name.includes(token) || htmlFor.includes(token) || text.includes(token)
      )
    })
    .slice(0, 120)
    .map((node) => ({
      tagName: node.tagName.toLowerCase(),
      id: node.id || null,
      name: node.getAttribute('name'),
      htmlFor: node.getAttribute('for'),
      type: 'type' in node ? node.type : null,
      role: node.getAttribute('role'),
      value: 'value' in node ? node.value : null,
      checked: 'checked' in node ? !!node.checked : null,
      text: normText(node.textContent).slice(0, 180),
      snippet: toSnippet(node),
    }))

  const attributeMapKeys = Array.from(document.querySelectorAll("[name^='attributeMap[']"))
    .map((node) => {
      const name = node.getAttribute('name') || ''
      const match = name.match(/^attributeMap\\[(.+)\\]$/)
      return {
        name,
        key: match ? match[1] : null,
        id: node.id || null,
        type: 'type' in node ? node.type : null,
        checked: 'checked' in node ? !!node.checked : null,
        value: 'value' in node ? node.value : null,
      }
    })

  const expectedSpecialAttributes = Object.fromEntries(
    Object.entries(expectedSpecialAttributeKeywords).map(([attr, tokens]) => {
      const found = specialAttributeControls.some((entry) => {
        const haystack = [entry.id, entry.name, entry.htmlFor, entry.text]
          .map((value) => (value || '').toLowerCase())
          .join(' ')
        return tokens.some((token) => haystack.includes(token))
      })
      return [attr, {
        expectedTokens: tokens,
        found,
      }]
    })
  )

  return {
    url: location.href,
    radios,
    labels,
    hiddenInputs,
    keywordNodes,
    specialAttributeControls,
    attributeMapKeys,
    expectedSpecialAttributes,
  }
})()
"""
    raw = await bot.web_execute(js)
    inventory = dict(raw) if isinstance(raw, dict) else {"raw": raw}

    return {
        "setup": setup,
        "inventory": inventory,
    }


def _make_bot_selection_recorder(bot:KleinanzeigenBot) -> tuple[Callable[[], list[dict[str, Any]]], Callable[[], None]]:
    """Install a temporary wrapper around ``__pick_special_attribute_candidate`` that
    records what the bot actually selected for each special-attribute key.

    Returns ``(get_recordings, restore)``:
    - ``get_recordings()`` returns a list of dicts with per-attribute selection evidence.
    - ``restore()`` removes the wrapper and puts the original method back.

    The wrapper is *read-only*: it delegates to the original method without changing
    arguments or return values, so production bot behavior is unaffected.
    """
    mangled = "_KleinanzeigenBot__pick_special_attribute_candidate"
    original = getattr(bot, mangled)
    recordings:list[dict[str, Any]] = []

    def _wrapper(candidates:Any, special_attribute_key:str) -> Any:
        selected = original(candidates, special_attribute_key)
        recordings.append(
            {
                "key": special_attribute_key,
                "candidate_count": len(candidates) if hasattr(candidates, "__len__") else None,
                "tag": getattr(selected, "local_name", None),
                "id": (selected.attrs.get("id") if hasattr(selected, "attrs") else None),
                "name": (selected.attrs.get("name") if hasattr(selected, "attrs") else None),
                "type": (selected.attrs.get("type") if hasattr(selected, "attrs") else None),
                "role": (selected.attrs.get("role") if hasattr(selected, "attrs") else None),
            }
        )
        return selected

    setattr(bot, mangled, _wrapper)

    def _restore() -> None:
        setattr(bot, mangled, original)

    def _get_recordings() -> list[dict[str, Any]]:
        return list(recordings)

    return _get_recordings, _restore


async def _verify_special_attribute_set_readback(
    bot:KleinanzeigenBot,
    ad_cfg:"Ad",
    ad_file_path:Path,
    expected_value_overrides:dict[str, str] | None = None,
) -> dict[str, Any]:
    if not ad_cfg.special_attributes:
        return {"skipped": True, "reason": "ad has no special_attributes"}

    await bot.web_open(f"{bot.root_url}/p-anzeige-aufgeben-schritt2.html")
    await bot._dismiss_consent_banner()  # noqa: SLF001

    setup:dict[str, Any] = {}
    set_category = cast(
        Callable[[str | None, str], Awaitable[Any]] | None,
        getattr(bot, "_KleinanzeigenBot__set_category", None),
    )
    if callable(set_category):
        try:
            await set_category(ad_cfg.category, str(ad_file_path))
            setup["set_category"] = "ok"
        except TimeoutError as exc:
            setup["set_category"] = {"error": {"type": type(exc).__name__, "message": str(exc)}}
    else:
        setup["set_category"] = {"error": {"type": "AttributeError", "message": "__set_category helper not available"}}

    # Ensure we are back on the publish form before setting/reading attributes.
    await bot.web_open(f"{bot.root_url}/p-anzeige-aufgeben-schritt2.html")
    await bot._dismiss_consent_banner()  # noqa: SLF001

    set_special_attributes = cast(
        Callable[[Any], Awaitable[Any]] | None,
        getattr(bot, "_KleinanzeigenBot__set_special_attributes", None),
    )
    if not callable(set_special_attributes):
        return {
            "setup": setup,
            "skipped": True,
            "reason": "private helper __set_special_attributes is not available on this bot build",
        }

    # Enhanced per-attribute probe for fix planning:
    # - captures which element matches the bot XPath (and inferred setter branch)
    # - runs set() per special attribute key
    # - captures event deltas (click/input/change) to detect option clicks
    # - captures hidden/visible read-back + available options (fiber/dom)
    init_events_js = """
(() => {
  if (window.__kabotAttrEventProbe && Array.isArray(window.__kabotAttrEventProbe.events)) {
    return { installed: true, event_count: window.__kabotAttrEventProbe.events.length }
  }
  const events = []
  const trimText = (value, limit = 120) => (value || '').replace(/\\s+/g, ' ').trim().slice(0, limit)
  const pushEvent = (type, target) => {
    const node = target instanceof Element ? target : null
    const closestRoleNode = node ? node.closest('[role]') : null
    events.push({
      type,
      ts: Date.now(),
      tagName: node ? node.tagName.toLowerCase() : null,
      id: node ? (node.id || null) : null,
      name: node ? node.getAttribute('name') : null,
      role: node ? node.getAttribute('role') : null,
      closestRole: closestRoleNode ? closestRoleNode.getAttribute('role') : null,
      text: node ? trimText(node.textContent || '') : null,
    })
    if (events.length > 5000) {
      events.splice(0, events.length - 5000)
    }
  }
  ;['click', 'input', 'change'].forEach((eventType) => {
    document.addEventListener(eventType, (event) => pushEvent(eventType, event.target), true)
  })
  window.__kabotAttrEventProbe = { installed: true, events }
  return { installed: true, event_count: events.length }
})()
"""
    await bot.web_execute(init_events_js)

    def _event_cursor_script() -> str:
        return "(() => (window.__kabotAttrEventProbe && Array.isArray(window.__kabotAttrEventProbe.events) ? window.__kabotAttrEventProbe.events.length : 0))()"

    def _event_delta_script(start_index:int) -> str:
        script = """
(() => {
  const startIndex = __START__
  const events = window.__kabotAttrEventProbe && Array.isArray(window.__kabotAttrEventProbe.events)
    ? window.__kabotAttrEventProbe.events
    : []
  const delta = events.slice(startIndex)
  const dropdownOptionClickObserved = delta.some((entry) => {
    if (entry.type !== 'click') return false
    const id = (entry.id || '').toLowerCase()
    const role = (entry.role || '').toLowerCase()
    const closestRole = (entry.closestRole || '').toLowerCase()
    return role === 'option' || closestRole === 'option' || closestRole === 'listbox' || id.includes('menu-option') || id.includes('-menu')
  })
  return {
    start_index: startIndex,
    end_index: events.length,
    delta_count: delta.length,
    dropdown_option_click_observed: dropdownOptionClickObserved,
    sample: delta.slice(0, 80),
  }
})()
"""
        return script.replace("__START__", str(start_index))

    def _selector_probe_script(payload:dict[str, Any]) -> str:
        script = """
(() => {
  const args = __ARGS__
  const key = args.normalized_key
  const expectedValue = args.expected_value
  const xpathExpr = args.xpath

  const norm = (v) => (v == null ? '' : String(v)).toLowerCase().replace(/\\s+/g, ' ').trim()
  const simplify = (v) => norm(v).replace(/[_-]+/g, ' ')
  const matchesExpected = (actual, expected) => {
    const a = simplify(actual)
    const e = simplify(expected)
    if (!a || !e) return false
    return a === e || a.includes(e) || e.includes(a)
  }
  const toSnippet = (node, size = 280) => (node ? node.outerHTML.slice(0, size) : null)
  const textValue = (node, size = 180) => (node ? (node.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, size) : null)

  const readFiberOptions = (node) => {
    if (!node) return { source: 'missing-node', scope: 'none', options: [] }
    const fiberKey = Object.keys(node).find((k) => k.startsWith('__reactFiber'))
    let fiber = fiberKey ? node[fiberKey] : null
    for (let i = 0; i < 25 && fiber; i++, fiber = fiber.return) {
      if (fiber.memoizedProps && Array.isArray(fiber.memoizedProps.options)) {
        return fiber.memoizedProps.options.slice(0, 120).map((opt) => ({
          value: opt && 'value' in opt ? opt.value : null,
          label: opt && 'label' in opt ? opt.label : null,
        }))
      }
    }
    return []
  }

  const readDomOptions = (node) => {
    if (!node) return { source: 'missing-node', scope: 'none', options: [] }
    const tag = node.tagName.toLowerCase()
    if (tag === 'select') {
      return {
        source: 'select',
        scope: 'scoped',
        options: Array.from(node.options || []).slice(0, 120).map((opt) => ({
          value: opt.value,
          label: (opt.textContent || '').replace(/\\s+/g, ' ').trim(),
          selected: !!opt.selected,
        })),
      }
    }

    const isVisible = (element) => {
      const style = window.getComputedStyle(element)
      return style.display !== 'none' && style.visibility !== 'hidden'
    }

    const buildResult = (source, scope, nodes, extras = {}) => ({
      source,
      scope,
      options: nodes.slice(0, 120).map((opt) => ({
        id: opt.id || null,
        value: opt.getAttribute('data-value') || opt.getAttribute('value') || null,
        label: (opt.textContent || '').replace(/\\s+/g, ' ').trim(),
        ariaSelected: opt.getAttribute('aria-selected'),
      })),
      ...extras,
    })

    const ids = []
    if (node.id) ids.push(`${node.id}-menu`)
    const ariaControls = node.getAttribute('aria-controls')
    if (ariaControls) ids.push(ariaControls)

    for (const id of ids) {
      const menu = document.getElementById(id)
      if (!menu) continue
      const options = Array.from(menu.querySelectorAll('[role="option"], li, button[aria-selected]'))
      if (options.length) {
        return buildResult(`menu:${id}`, 'scoped', options)
      }
    }

    const nearbyContainer = node.closest('fieldset, section, [role="group"], [role="region"], [role="dialog"]') || node.parentElement
    if (nearbyContainer) {
      const nearbyListbox = nearbyContainer.querySelector('[role="listbox"], [role="menu"], ul, ol')
      if (nearbyListbox) {
        const options = Array.from(nearbyListbox.querySelectorAll('[role="option"], li, button[aria-selected]'))
        if (options.length) {
          return buildResult('nearby-container', 'scoped', options, {
            containerTag: nearbyContainer.tagName.toLowerCase(),
            containerId: nearbyContainer.id || null,
          })
        }
      }
    }

    const visibleListboxes = Array.from(document.querySelectorAll('[role="listbox"]')).filter(isVisible)
    if (visibleListboxes.length === 1) {
      const listbox = visibleListboxes[0]
      const options = Array.from(listbox.querySelectorAll('[role="option"], li, button[aria-selected]'))
      if (options.length) {
        return buildResult('single-visible-listbox', 'scoped', options, {
          listboxId: listbox.id || null,
        })
      }
    }

    if ((node.getAttribute('role') || '').toLowerCase() === 'combobox') {
      return buildResult('global-option-fallback', 'ambiguous', Array.from(document.querySelectorAll('[role="option"]')).slice(0, 120), {
        visibleListboxCount: visibleListboxes.length,
      })
    }

    return buildResult('no-options', 'none', [])
  }

  const nodeInfo = (node) => {
    if (!node) return null
    return {
      tagName: node.tagName.toLowerCase(),
      id: node.id || null,
      name: node.getAttribute('name'),
      type: 'type' in node ? node.type : null,
      role: node.getAttribute('role'),
      value: 'value' in node ? node.value : node.getAttribute('value'),
      text: textValue(node),
      snippet: toSnippet(node),
    }
  }

  const inferBranch = (node) => {
    if (!node) return 'not_found'
    const tag = node.tagName.toLowerCase()
    const type = ('type' in node ? String(node.type || '') : '').toLowerCase()
    const role = (node.getAttribute('role') || '').toLowerCase()
    if (tag === 'select') return 'select'
    if (type === 'checkbox') return 'checkbox'
    if (tag === 'button' && role === 'combobox') return 'button_combobox'
    if (type === 'text' && role === 'combobox') return 'text_combobox'
    return 'text_input_fallback'
  }

  const hiddenCandidates = Array.from(document.querySelectorAll("[name^='attributeMap[']"))
    .filter((node) => {
      const name = node.getAttribute('name') || ''
      return name === `attributeMap[${key}]` || name.endsWith(`.${key}]`)
    })

  const visibleCandidates = Array.from(document.querySelectorAll('[id]'))
    .filter((node) => {
      const id = node.id || ''
      if (!(id === key || id.endsWith(`.${key}`))) return false
      const tag = node.tagName.toLowerCase()
      return ['input', 'select', 'textarea', 'button'].includes(tag)
    })

  const hidden = hiddenCandidates.map((node) => ({ ...nodeInfo(node) }))
  const visible = visibleCandidates.map((node) => {
    const domOptions = readDomOptions(node)
    return {
      ...nodeInfo(node),
      dom_options: domOptions,
      available_options: {
        fiber: readFiberOptions(node),
        dom: domOptions.options,
        dom_source: domOptions.source,
        dom_scope: domOptions.scope,
        dom_warning: domOptions.scope === 'ambiguous' ? 'DOM option fallback is globally scoped and low confidence' : null,
      },
    }
  })

  const toFullInfo = (node) => {
    if (!node) return null
    const cs = window.getComputedStyle(node)
    return {
      tagName: node.tagName.toLowerCase(),
      id: node.id || null,
      name: node.getAttribute('name'),
      type: 'type' in node ? node.type : null,
      role: node.getAttribute('role'),
      value: 'value' in node ? node.value : node.getAttribute('value'),
      ariaLabel: node.getAttribute('aria-label'),
      placeholder: node.getAttribute('placeholder') || null,
      text: textValue(node),
      visibility: cs ? cs.visibility : null,
      display: cs ? cs.display : null,
      hidden: node.hidden || false,
      outerHTML: node.outerHTML,
    }
  }

  const findNearbyContainer = (node) => {
    if (!node) return null
    const container = node.closest('fieldset, section, [role="group"], [role="region"]')
      || node.parentElement
    if (!container) return null
    return {
      tagName: container.tagName.toLowerCase(),
      id: container.id || null,
      className: container.className || null,
      role: container.getAttribute('role'),
      ariaLabel: container.getAttribute('aria-label'),
      outerHTML: container.outerHTML,
    }
  }

  const collectListboxState = (node) => {
    if (!node) return { options: [], listboxOuterHTML: null }
    let listbox = null
    const ariaControls = node.getAttribute('aria-controls')
    if (ariaControls) {
      listbox = document.getElementById(ariaControls)
    }
    if (!listbox && node.id) {
      const menuById = document.getElementById(node.id + '-menu')
      if (menuById) listbox = menuById
    }
    if (!listbox) {
      const parent = node.closest('[role="group"], fieldset, section, div')
      if (parent) {
        listbox = parent.querySelector('[role="listbox"], [role="menu"], ul, ol')
      }
    }
    if (!listbox) return { options: [], listboxOuterHTML: null }

    const optionNodes = Array.from(listbox.querySelectorAll('[role="option"], li, button[aria-selected]'))
    const options = optionNodes.slice(0, 120).map((opt) => ({
      id: opt.id || null,
      tagName: opt.tagName.toLowerCase(),
      label: (opt.textContent || '').replace(/\\s+/g, ' ').trim(),
      value: opt.getAttribute('data-value') || opt.getAttribute('value') || null,
      ariaSelected: opt.getAttribute('aria-selected'),
    }))
    return {
      options,
      listboxOuterHTML: listbox.outerHTML,
    }
  }

  const xpathSnapshot = document.evaluate(xpathExpr, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null)
  const xpathCount = xpathSnapshot.snapshotLength
  const xpathFirst = xpathCount > 0 ? xpathSnapshot.snapshotItem(0) : null

  const xpathAllMatches = []
  for (let i = 0; i < xpathCount; i++) {
    const match = xpathSnapshot.snapshotItem(i)
    xpathAllMatches.push({
      index: i,
      ...toFullInfo(match),
      container: findNearbyContainer(match),
      listboxState: collectListboxState(match),
    })
  }

  const hiddenMatch = hidden.some((entry) => matchesExpected(entry.value, expectedValue))
  const visibleMatch = visible.some((entry) => matchesExpected(entry.value, expectedValue) || matchesExpected(entry.text, expectedValue))

  return {
    xpath: xpathExpr,
    xpath_match_count: xpathCount,
    xpath_first: nodeInfo(xpathFirst),
    xpath_all_matches: xpathAllMatches,
    branch_guess: inferBranch(xpathFirst),
    hidden,
    visible,
    hidden_match: hiddenMatch,
    visible_match: visibleMatch,
    any_match: hiddenMatch || visibleMatch,
  }
})()
"""
        return script.replace("__ARGS__", json.dumps(payload, ensure_ascii = False))

    class _SingleAttributeAd:
        def __init__(self, key:str, value:Any) -> None:
            self.special_attributes = {key: value}

    results:list[dict[str, Any]] = []
    recategorize_retry_used = False
    for original_key, raw_value in ad_cfg.special_attributes.items():
        expected_value = (expected_value_overrides or {}).get(original_key, str(raw_value))
        normalized_key = re.sub(r"_[a-z]+$", "", original_key).rsplit(".", maxsplit = 1)[-1]
        xpath = _build_special_attribute_xpath(original_key, normalized_key)
        payload = {
            "original_key": original_key,
            "normalized_key": normalized_key,
            "expected_value": expected_value,
            "xpath": xpath,
        }

        before_raw = await bot.web_execute(_selector_probe_script(payload))
        before_probe = dict(before_raw) if isinstance(before_raw, dict) else {"raw": before_raw}

        if isinstance(before_probe, dict) and int(before_probe.get("xpath_match_count") or 0) == 0 and not recategorize_retry_used and callable(set_category):
            # Some category-specific controls occasionally disappear after intermediate navigation.
            # Retry category apply once to capture actionable selector data.
            try:
                await set_category(ad_cfg.category, str(ad_file_path))
                await bot.web_open(f"{bot.root_url}/p-anzeige-aufgeben-schritt2.html")
                await bot._dismiss_consent_banner()  # noqa: SLF001
                await bot.web_sleep(250, 450)
                retry_raw = await bot.web_execute(_selector_probe_script(payload))
                before_probe = dict(retry_raw) if isinstance(retry_raw, dict) else {"raw": retry_raw}
                recategorize_retry_used = True
                setup["set_category_retry"] = "ok"
            except TimeoutError as exc:
                recategorize_retry_used = True
                setup["set_category_retry"] = {"error": {"type": type(exc).__name__, "message": str(exc)}}

        cursor_raw = await bot.web_execute(_event_cursor_script())
        try:
            start_index = int(cursor_raw)
        except (TypeError, ValueError):
            start_index = 0

        # ── Install recorder to capture which candidate __pick_special_attribute_candidate selects ──
        get_recordings, restore_pick = _make_bot_selection_recorder(bot)
        bot_selection:dict[str, Any] | None = None

        set_call:dict[str, Any]
        try:
            await set_special_attributes(_SingleAttributeAd(original_key, raw_value))
            set_call = {"ok": True}
        except TimeoutError as exc:
            set_call = {"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}}
        finally:
            recordings = get_recordings()
            restore_pick()
            # Expect exactly one recording per single-attribute call.
            if recordings:
                bot_selection = recordings[-1]

        await bot.web_sleep(250, 450)

        after_raw = await bot.web_execute(_selector_probe_script(payload))
        after_probe = dict(after_raw) if isinstance(after_raw, dict) else {"raw": after_raw}

        events_raw = await bot.web_execute(_event_delta_script(start_index))
        event_delta = dict(events_raw) if isinstance(events_raw, dict) else {"raw": events_raw}

        result_item:dict[str, Any] = {
            "original_key": original_key,
            "normalized_key": normalized_key,
            "expected_value": expected_value,
            "selector_probe_before": before_probe,
            "set_call": set_call,
            "selector_probe_after": after_probe,
            "events": event_delta,
            "hidden_match": bool(after_probe.get("hidden_match")) if isinstance(after_probe, dict) else False,
            "visible_match": bool(after_probe.get("visible_match")) if isinstance(after_probe, dict) else False,
            "any_match": bool(after_probe.get("any_match")) if isinstance(after_probe, dict) else False,
        }
        if bot_selection is not None:
            result_item["bot_selection"] = bot_selection
        results.append(result_item)

    all_matched = all(bool(item.get("any_match")) for item in results)
    all_set_calls_ok = all(bool(item.get("set_call", {}).get("ok")) for item in results)

    return {
        "setup": setup,
        "set_result": {
            "ok": all_set_calls_ok,
            "all_set_calls_ok": all_set_calls_ok,
            "set_calls": [
                {
                    "original_key": item.get("original_key"),
                    "ok": item.get("set_call", {}).get("ok"),
                    "error": item.get("set_call", {}).get("error"),
                }
                for item in results
            ],
        },
        "readback": {
            "url": f"{bot.root_url}/p-anzeige-aufgeben-schritt2.html",
            "all_matched": all_matched,
            "all_set_calls_ok": all_set_calls_ok,
            "results": results,
        },
    }


async def _probe_condition_flow(
    bot:KleinanzeigenBot,
    ad_cfg:"Ad",
    ad_file_path:Path,
    condition_value:str | None,
) -> dict[str, Any]:
    probe_value = condition_value or str((ad_cfg.special_attributes or {}).get("condition_s") or "like_new")
    normalized_value = _normalize_condition_value(probe_value)
    probe_ad_cfg = _override_condition_probe_ad(ad_cfg, probe_value, ad_cfg.category)
    probe_report = await _verify_special_attribute_set_readback(
        bot,
        probe_ad_cfg,
        ad_file_path,
        expected_value_overrides = {"condition_s": normalized_value},
    )

    if probe_report.get("skipped"):
        return {
            "skipped": True,
            "reason": probe_report.get("reason"),
            "configured_condition_value": probe_value,
            "normalized_condition_value": _normalize_condition_value(probe_value),
            "expected_display_candidates": _condition_display_candidates(probe_value),
        }

    readback = probe_report.get("readback", {}) if isinstance(probe_report, dict) else {}
    results = readback.get("results", []) if isinstance(readback, dict) else []
    first_result = results[0] if results and isinstance(results[0], dict) else {}
    before_probe = first_result.get("selector_probe_before", {}) if isinstance(first_result, dict) else {}
    after_probe = first_result.get("selector_probe_after", {}) if isinstance(first_result, dict) else {}
    bot_selection = first_result.get("bot_selection") if isinstance(first_result, dict) else None
    route_taken = _infer_condition_route(first_result if isinstance(first_result, dict) else {})
    display_candidates = _condition_display_candidates(probe_value)
    set_call = first_result.get("set_call", {}) if isinstance(first_result, dict) else {}
    has_match = bool(first_result.get("any_match")) if isinstance(first_result, dict) else False
    set_ok = bool(set_call.get("ok")) if isinstance(set_call, dict) else False

    return {
        "configured_condition_value": probe_value,
        "normalized_condition_value": normalized_value,
        "legacy_alias_used": probe_value != normalized_value,
        "expected_display_candidates": display_candidates,
        "route_taken": route_taken,
        "trigger_probe": {
            "branch_guess": before_probe.get("branch_guess") if isinstance(before_probe, dict) else None,
            "xpath_first": before_probe.get("xpath_first") if isinstance(before_probe, dict) else None,
            "visible_match": before_probe.get("visible_match") if isinstance(before_probe, dict) else None,
            "hidden_match": before_probe.get("hidden_match") if isinstance(before_probe, dict) else None,
            "any_match": before_probe.get("any_match") if isinstance(before_probe, dict) else None,
        },
        "before_probe": before_probe,
        "set_call": set_call,
        "after_probe": after_probe,
        "bot_selection": bot_selection,
        "events": first_result.get("events") if isinstance(first_result, dict) else None,
        "pass": bool(set_ok and has_match),
        "failure_reason": (
            None
            if set_ok and has_match
            else (
                {
                    "type": "ReadbackMismatch",
                    "message": "special-attribute read-back did not match the expected value",
                    "expected_display_candidates": display_candidates,
                    "after_probe": {
                        "branch_guess": after_probe.get("branch_guess") if isinstance(after_probe, dict) else None,
                        "xpath_first": after_probe.get("xpath_first") if isinstance(after_probe, dict) else None,
                        "visible_match": after_probe.get("visible_match") if isinstance(after_probe, dict) else None,
                        "hidden_match": after_probe.get("hidden_match") if isinstance(after_probe, dict) else None,
                        "any_match": after_probe.get("any_match") if isinstance(after_probe, dict) else None,
                    },
                }
                if set_ok
                else (set_call.get("error") if isinstance(set_call, dict) else None)
            )
        ),
        "raw": probe_report,
    }


async def _collect_shipping_dialog_flow(bot:KleinanzeigenBot, ad_cfg:"Ad", ad_file_path:Path) -> dict[str, Any]:
    """Probe ALL shipping dialog variants for issue #956.

    Tests every size group (Klein/Mittel/Groß) with their carrier
    checkboxes, plus individual shipping.  Each variant is exercised in a
    fresh dialog session (open → configure → close) so cross-variant state
    leakage is impossible.  Captures DOM state at each step for
    comprehensive diagnostics.
    """
    _RADIO_BY_SIZE:dict[str, str] = {"Klein": "SMALL", "Mittel": "MEDIUM", "Groß": "LARGE"}
    short_timeout = bot._timeout("quick_dom")  # noqa: SLF001
    dialog_xpath = '//*[self::dialog or @role="dialog"]'

    await bot.web_open(f"{bot.root_url}/p-anzeige-aufgeben-schritt2.html")
    await bot._dismiss_consent_banner()  # noqa: SLF001

    setup:dict[str, Any] = {}

    # Set ad type to OFFER (default for shipping)
    try:
        if not await bot.web_check(By.ID, "ad-type-OFFER", Is.SELECTED, timeout = short_timeout):
            await bot.web_click(By.ID, "ad-type-OFFER", timeout = short_timeout)
            await bot.web_sleep(1500)
        setup["set_ad_type"] = {"radio": "ad-type-OFFER", "clicked": True}
    except TimeoutError as exc:
        setup["set_ad_type"] = {"radio": "ad-type-OFFER", "error": {"type": type(exc).__name__, "message": str(exc)}}

    # Set category so shipping fields render
    set_category = cast(
        Callable[[str | None, str], Awaitable[Any]] | None,
        getattr(bot, "_KleinanzeigenBot__set_category", None),
    )
    if callable(set_category):
        try:
            await set_category(ad_cfg.category, str(ad_file_path))
            setup["set_category"] = "ok"
        except TimeoutError as exc:
            setup["set_category"] = {"error": {"type": type(exc).__name__, "message": str(exc)}}
    else:
        setup["set_category"] = {"error": {"type": "AttributeError", "message": "__set_category helper not available"}}

    # Navigate back to publish form after category flow
    await bot.web_open(f"{bot.root_url}/p-anzeige-aufgeben-schritt2.html")
    await bot._dismiss_consent_banner()  # noqa: SLF001

    # Re-select ad type after navigation
    try:
        if not await bot.web_check(By.ID, "ad-type-OFFER", Is.SELECTED, timeout = short_timeout):
            await bot.web_click(By.ID, "ad-type-OFFER", timeout = short_timeout)
            await bot.web_sleep(1500)
        setup["set_ad_type_after_nav"] = {"radio": "ad-type-OFFER", "clicked": True}
    except TimeoutError as exc:
        setup["set_ad_type_after_nav"] = {"radio": "ad-type-OFFER", "error": {"type": type(exc).__name__, "message": str(exc)}}

    # ── Shared JS capture scripts ─────────────────────────────────────────────

    size_view_js = """
    (() => {
      const snippet = (node, size = 300) => (node ? node.outerHTML.slice(0, size) : null);
      const openDialog = document.querySelector('dialog[open]');

      // Size radios by value attribute
      const sizeRadios = ['SMALL', 'MEDIUM', 'LARGE'].map((val) => {
        const xpath = `//input[@type="radio" and @value="${val}"]`;
        const ctx = openDialog || document;
        const result = document.evaluate(xpath, ctx, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
        const node = result.singleNodeValue;
        return {
          value: val,
          found: !!node,
          id: node ? node.id || null : null,
          checked: node ? !!node.checked : null,
          snippet: snippet(node),
        };
      });

      // Weiter button(s)
      const weiterButtons = Array.from((openDialog || document).querySelectorAll('button'))
        .filter((b) => (b.textContent || '').replace(/\\s+/g, ' ').trim().includes('Weiter'))
        .map((b) => ({
          text: (b.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 80),
          disabled: 'disabled' in b ? !!b.disabled : null,
          snippet: snippet(b),
        }));

      // Individual shipping elements
      const indCb = document.getElementById('ad-individual-shipping-checkbox-control');
      const indPrice = document.getElementById('ad-individual-shipping-price');

      return {
        dialogOpen: !!openDialog,
        sizeRadios,
        weiterButtons,
        individualShipping: {
          checkbox: {
            found: !!indCb,
            checked: indCb ? !!indCb.checked : null,
            type: indCb ? indCb.type : null,
            snippet: snippet(indCb),
          },
          priceInput: {
            found: !!indPrice,
            value: indPrice ? indPrice.value : null,
            type: indPrice ? indPrice.type : null,
            disabled: indPrice ? !!indPrice.disabled : null,
            visible: indPrice ? window.getComputedStyle(indPrice).display !== 'none' : null,
            snippet: snippet(indPrice),
          },
        },
      };
    })()
    """

    checkbox_view_js = """
    (() => {
      const snippet = (node, size = 400) => (node ? node.outerHTML.slice(0, size) : null);
      const openDialog = document.querySelector('dialog[open]');

      // Carrier checkboxes by value attribute
      const carrierCodes = [
        'HERMES_001', 'HERMES_002', 'HERMES_003', 'HERMES_004',
        'DHL_001', 'DHL_002', 'DHL_003', 'DHL_004', 'DHL_005'
      ];
      const carrierCheckboxes = carrierCodes.map((code) => {
        const xpath = `//input[@type="checkbox" and @value="${code}"]`;
        const ctx = openDialog || document;
        const result = document.evaluate(xpath, ctx, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
        const node = result.singleNodeValue;
        const labelEl = node ? node.closest('label, [class*="checkbox"], [class*="check"]') : null;
        return {
          carrierCode: code,
          found: !!node,
          checked: node ? !!node.checked : null,
          snippet: snippet(node),
          labelText: labelEl
            ? (labelEl.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 200)
            : null,
        };
      });

      // Fertig and Zurück buttons
      const norm = (v) => (v || '').replace(/\\s+/g, ' ').trim();
      const fertigButtons = Array.from((openDialog || document).querySelectorAll('button'))
        .filter((b) => norm(b.textContent).includes('Fertig'))
        .map((b) => ({
          text: norm(b.textContent).slice(0, 80),
          disabled: 'disabled' in b ? !!b.disabled : null,
          snippet: snippet(b),
        }));

      const zurueckButtons = Array.from((openDialog || document).querySelectorAll('button'))
        .filter((b) => norm(b.textContent).includes('Zurück'))
        .map((b) => ({
          text: norm(b.textContent).slice(0, 80),
          snippet: snippet(b),
        }));

      // Individual shipping elements (may also be present in this view)
      const indCb = document.getElementById('ad-individual-shipping-checkbox-control');
      const indPrice = document.getElementById('ad-individual-shipping-price');

      return {
        dialogOpen: !!openDialog,
        carrierCheckboxes,
        fertigButtons,
        zurueckButtons,
        individualShipping: {
          checkbox: {found: !!indCb, checked: indCb ? !!indCb.checked : null},
          priceInput: {found: !!indPrice, value: indPrice ? indPrice.value : null},
        },
      };
    })()
    """

    individual_shipping_js = """
    (() => {
      const snippet = (node, size = 300) => (node ? node.outerHTML.slice(0, size) : null);
      const openDialog = document.querySelector('dialog[open]');

      const indCb = document.getElementById('ad-individual-shipping-checkbox-control');
      const indPrice = document.getElementById('ad-individual-shipping-price');

      // Size radios for context
      const sizeRadios = ['SMALL', 'MEDIUM', 'LARGE'].map((val) => {
        const xpath = `//input[@type="radio" and @value="${val}"]`;
        const ctx = openDialog || document;
        const result = document.evaluate(xpath, ctx, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
        const node = result.singleNodeValue;
        return {value: val, found: !!node, checked: node ? !!node.checked : null};
      });

      // Fertig button
      const fertigButtons = Array.from((openDialog || document).querySelectorAll('button'))
        .filter((b) => (b.textContent || '').replace(/\\s+/g, ' ').trim().includes('Fertig'))
        .map((b) => ({text: (b.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 80), snippet: snippet(b)}));

      return {
        dialogOpen: !!openDialog,
        individualCheckbox: {
          found: !!indCb,
          checked: indCb ? !!indCb.checked : null,
          type: indCb ? indCb.type : null,
          snippet: snippet(indCb),
        },
        priceInput: {
          found: !!indPrice,
          value: indPrice ? indPrice.value : null,
          type: indPrice ? indPrice.type : null,
          visible: indPrice ? window.getComputedStyle(indPrice).display !== 'none' : null,
          snippet: snippet(indPrice),
        },
        sizeRadios,
        fertigButtons,
      };
    })()
    """

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _try_click(
        by:Any,
        selector:str,
        *,
        sleep_range:tuple[int, int] = (500, 800),
    ) -> dict[str, Any]:
        try:
            await bot.web_click(by, selector, timeout = short_timeout)
            await bot.web_sleep(*sleep_range)
            return {"clicked": True}
        except TimeoutError as exc:
            return {"clicked": False, "error": {"type": type(exc).__name__, "message": str(exc)}}

    async def _try_find(by:Any, selector:str) -> bool:
        try:
            await bot.web_find(by, selector, timeout = short_timeout)
            return True
        except TimeoutError:
            return False

    async def _navigate_to_andere_versandmethoden() -> dict[str, Any]:
        """Click 'Andere Versandmethoden', navigating back with Zurück if needed.

        Mirrors the bot's __set_shipping() logic: when the dialog reopens after
        a previous Fertig, it may land on a summary/confirmation view instead of
        the size-selection view.  Clicking Zurück (up to 2 times) gets us back
        to the view where 'Andere Versandmethoden' is visible.
        """
        if await _try_find(By.XPATH, '//button[contains(., "Andere Versandmethoden")]'):
            return await _try_click(
                By.XPATH,
                '//button[contains(., "Andere Versandmethoden")]',
                sleep_range = (1000, 1500),
            )

        zurueck_steps:list[dict[str, Any]] = []
        for _ in range(2):
            zurueck = await _try_click(
                By.XPATH,
                '//button[contains(., "Zurück")]',
                sleep_range = (500, 800),
            )
            zurueck_steps.append(zurueck)
            if zurueck["clicked"] and await _try_find(By.XPATH, '//button[contains(., "Andere Versandmethoden")]'):
                return await _try_click(
                    By.XPATH,
                    '//button[contains(., "Andere Versandmethoden")]',
                    sleep_range = (1000, 1500),
                )

        return {
            "clicked": False,
            "error": {"type": "TimeoutError", "message": "Andere Versandmethoden not found after Zurück navigation"},
            "zurueck_steps": zurueck_steps,
        }

    async def _js_capture(js:str) -> dict[str, Any]:
        raw = await bot.web_execute(js)
        return dict(raw) if isinstance(raw, dict) else {"raw": raw}

    # ── Phase 1: Test all size groups ─────────────────────────────────────────

    size_group_results:list[dict[str, Any]] = []

    for size_display, expected_codes in CARRIER_CODES_BY_SIZE.items():
        radio_value = _RADIO_BY_SIZE[size_display]
        variant:dict[str, Any] = {
            "size_display": size_display,
            "radio_value": radio_value,
            "expected_carrier_codes": expected_codes,
        }

        # Enable shipping
        variant["enable_shipping"] = await _try_click(By.ID, "ad-shipping-enabled-yes")
        if not variant["enable_shipping"]["clicked"]:
            size_group_results.append(variant)
            continue

        # Open dialog
        variant["open_dialog"] = await _try_click(By.ID, "ad-shipping-options", sleep_range = (1000, 1500))
        if not variant["open_dialog"]["clicked"]:
            size_group_results.append(variant)
            continue

        # Click "Andere Versandmethoden" (with Zurück fallback for reopened dialogs)
        variant["click_andere"] = await _navigate_to_andere_versandmethoden()
        if not variant["click_andere"]["clicked"]:
            size_group_results.append(variant)
            continue

        # Capture size selection view
        variant["size_view"] = await _js_capture(size_view_js)

        # Select size radio
        size_xpath = f'{dialog_xpath}//input[@type="radio" and @value="{radio_value}"]'
        variant["select_size"] = await _try_click(By.XPATH, size_xpath)
        if not variant["select_size"]["clicked"]:
            variant["select_size_fallback"] = await _try_click(
                By.XPATH,
                f'//input[@type="radio" and @value="{radio_value}"]',
            )

        # Click Weiter
        variant["click_weiter"] = await _try_click(
            By.XPATH,
            f'{dialog_xpath}//button[contains(., "Weiter")]',
            sleep_range = (1000, 1500),
        )
        if not variant["click_weiter"]["clicked"]:
            variant["click_weiter_fallback"] = await _try_click(
                By.XPATH,
                '//button[contains(., "Weiter")]',
                sleep_range = (1000, 1500),
            )

        # Capture checkbox view
        variant["checkbox_view"] = await _js_capture(checkbox_view_js)

        # Analyse which carrier codes were found
        cb_view = variant["checkbox_view"]
        if isinstance(cb_view, dict):
            summary = summarize_carrier_checkbox_defaults(
                [cb for cb in cb_view.get("carrierCheckboxes", []) if isinstance(cb, dict)],
                expected_codes,
            )
            variant.update(summary)

        # Close dialog via Fertig
        variant["click_fertig"] = await _try_click(
            By.XPATH,
            f'{dialog_xpath}//button[contains(., "Fertig")]',
        )
        if not variant["click_fertig"]["clicked"]:
            variant["click_fertig_fallback"] = await _try_click(
                By.XPATH,
                '//button[contains(., "Fertig")]',
            )

        size_group_results.append(variant)

    # ── Phase 2: Test individual shipping ─────────────────────────────────────

    individual:dict[str, Any] = {}

    # Enable shipping (may already be enabled)
    individual["enable_shipping"] = await _try_click(By.ID, "ad-shipping-enabled-yes")

    # Open dialog
    individual["open_dialog"] = await _try_click(By.ID, "ad-shipping-options", sleep_range = (1000, 1500))

    if individual["open_dialog"]["clicked"]:
        # Click "Andere Versandmethoden" (with Zurück fallback for reopened dialogs)
        individual["click_andere"] = await _navigate_to_andere_versandmethoden()

        if individual["click_andere"]["clicked"]:
            # Capture initial state (before clicking individual shipping)
            individual["initial_state"] = await _js_capture(individual_shipping_js)

            # Click individual shipping checkbox
            individual["click_checkbox"] = await _try_click(By.ID, "ad-individual-shipping-checkbox-control")

            if individual["click_checkbox"]["clicked"]:
                await bot.web_sleep(500, 800)

                # Capture state after clicking checkbox
                individual["after_checkbox"] = await _js_capture(individual_shipping_js)

                # Enter a test price
                try:
                    await bot.web_input(By.ID, "ad-individual-shipping-price", "4,99")
                    individual["enter_price"] = {"entered": True, "value": "4,99"}
                except TimeoutError as exc:
                    individual["enter_price"] = {"entered": False, "error": {"type": type(exc).__name__, "message": str(exc)}}

                # Capture final state
                individual["after_price"] = await _js_capture(individual_shipping_js)

            # Close dialog via Fertig
            individual["click_fertig"] = await _try_click(
                By.XPATH,
                f'{dialog_xpath}//button[contains(., "Fertig")]',
            )
            if not individual["click_fertig"]["clicked"]:
                individual["click_fertig_fallback"] = await _try_click(
                    By.XPATH,
                    '//button[contains(., "Fertig")]',
                )

    # ── Phase 3: Test buy-now state after individual shipping ──────────────────

    buy_now_js = """
    (() => {
      const snippet = (node, size = 400) => (node ? node.outerHTML.slice(0, size) : null);

      // Known buy-now selectors from the codebase
      const selectors = {
        ad_buy_now_true: '#ad-buy-now-true',
        ad_buy_now_false: '#ad-buy-now-false',
        radio_button_buy_now_yes: '#radio-button-buy-now-yes',
        radio_button_buy_now_no: '#radio-button-buy-now-no',
        buy_now_eligible: '[name="buyNowEligible"]',
      };

      const found = {};
      for (const [key, sel] of Object.entries(selectors)) {
        const node = document.querySelector(sel);
        found[key] = {
          selector: sel,
          found: !!node,
          tagName: node ? node.tagName.toLowerCase() : null,
          type: node ? (node.type || null) : null,
          value: node && 'value' in node ? node.value : null,
          checked: node && 'checked' in node ? !!node.checked : null,
          snippet: snippet(node),
        };
      }

      // Also search broadly for any element with 'buy-now' or 'buyNow' in id/name
      const allElements = document.querySelectorAll('[id*="buy-now"],[id*="buyNow"],[id*="buy_now"],[name*="buyNow"],[name*="buy-now"],[name*="buy_now"]');
      const broadMatches = Array.from(allElements).map((el) => ({
        tagName: el.tagName.toLowerCase(),
        id: el.id || null,
        name: el.name || null,
        type: el.type || null,
        value: el && 'value' in el ? el.value : null,
        checked: el && 'checked' in el ? !!el.checked : null,
        snippet: snippet(el),
      }));

      // Check for "Direkt kaufen" or "Sicher bezahlen" sections
      const sections = document.querySelectorAll('section, fieldset, [class*="section"], [class*="group"]');
      const buyNowSections = Array.from(sections).filter((s) => {
        const text = (s.textContent || '').replace(/\\s+/g, ' ').trim().toLowerCase();
        return text.includes('direkt kaufen') || text.includes('sicher bezahlen') || text.includes('buy now');
      }).map((s) => ({
        tagName: s.tagName.toLowerCase(),
        text: (s.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 200),
        snippet: snippet(s, 600),
      }));

      return { knownSelectors: found, broadMatches, buyNowSections };
    })()
    """

    buy_now_after_individual:dict[str, Any] = {}
    await bot.web_sleep(500, 800)
    buy_now_after_individual["state"] = await _js_capture(buy_now_js)

    # Try to click buy-now-true if it exists
    buy_now_after_individual["click_buy_now_true"] = await _try_click(By.ID, "ad-buy-now-true")
    if buy_now_after_individual["click_buy_now_true"]["clicked"]:
        await bot.web_sleep(300, 500)
        buy_now_after_individual["state_after_click"] = await _js_capture(buy_now_js)

    return {
        "setup": setup,
        "size_group_variants": size_group_results,
        "individual_shipping": individual,
        "buy_now_after_individual_shipping": buy_now_after_individual,
    }


async def _collect_price_type_controls(bot:KleinanzeigenBot) -> dict[str, Any]:
    await bot.web_open(f"{bot.root_url}/p-anzeige-aufgeben-schritt2.html")
    await bot._dismiss_consent_banner()  # noqa: SLF001

    snapshot_js = """
(() => {
  const get = (selector) => {
    const node = document.querySelector(selector)
    return {
      selector,
      found: !!node,
      tagName: node ? node.tagName.toLowerCase() : null,
      id: node ? node.id || null : null,
      value: node && 'value' in node ? node.value : null,
      text: node ? (node.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 120) : null,
      snippet: node ? node.outerHTML.slice(0, 240) : null,
    }
  }

  return {
    price_type_select_old: get('select#price-type-react'),
    price_type_button_new: get('#ad-price-type'),
    menu_option_0: get('#ad-price-type-menu-option-0'),
    menu_option_1: get('#ad-price-type-menu-option-1'),
    menu_option_2: get('#ad-price-type-menu-option-2'),
  }
})()
"""

    result:dict[str, Any] = {"before_click": {}, "after_click": {}, "click_result": {}}
    before_click = await bot.web_execute(snapshot_js)
    result["before_click"] = dict(before_click) if isinstance(before_click, dict) else {"raw": before_click}

    try:
        await bot.web_click(By.ID, "ad-price-type", timeout = bot._timeout("quick_dom"))  # noqa: SLF001
        result["click_result"] = {"clicked": True}
        await bot.web_sleep(300)
    except TimeoutError as exc:
        result["click_result"] = {"clicked": False, "error": {"type": type(exc).__name__, "message": str(exc)}}

    after_click = await bot.web_execute(snapshot_js)
    result["after_click"] = dict(after_click) if isinstance(after_click, dict) else {"raw": after_click}
    return result


async def _collect_category_step_page_presence(bot:KleinanzeigenBot, category:str | None) -> dict[str, Any]:
    if not category:
        return {"skipped": True, "reason": "ad has no category"}

    js = """
(() => {
  const checks = {
    step_submit_old_container: '#postad-step1-sbmt',
    step_submit_old_button: '#postad-step1-sbmt button',
    step_submit_generic_button: "button[type='submit']",
    category_path_old: '#postad-category-path',
    category_path_new: '#ad-category-path',
  }

  const result = {}
  for (const [name, selector] of Object.entries(checks)) {
    const node = document.querySelector(selector)
    result[name] = {
      selector,
      found: !!node,
      tagName: node ? node.tagName.toLowerCase() : null,
      id: node ? node.id || null : null,
      text: node ? (node.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 160) : null,
      snippet: node ? node.outerHTML.slice(0, 260) : null,
    }
  }

  const submitByText = Array.from(document.querySelectorAll('button,a')).find((node) => {
    const text = (node.textContent || '').replace(/\\s+/g, ' ').trim()
    return text.includes('Weiter') || text.includes('Kategorie') || text.includes('Bestätigen')
  })

  result.submit_candidate_by_text = {
    selector: "button/a text contains Weiter|Kategorie|Bestätigen",
    found: !!submitByText,
    tagName: submitByText ? submitByText.tagName.toLowerCase() : null,
    id: submitByText ? submitByText.id || null : null,
    text: submitByText ? (submitByText.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 160) : null,
    snippet: submitByText ? submitByText.outerHTML.slice(0, 260) : null,
  }

  return {
    checks: result,
    url: location.href,
    title: document.title,
  }
})()
"""
    category_candidates = [category]
    numeric_only = "/".join(part for part in category.split("/") if part.isdigit())
    if numeric_only and numeric_only not in category_candidates:
        category_candidates.append(numeric_only)
    category_candidates.append("")

    attempts:list[dict[str, Any]] = []
    for path_candidate in category_candidates:
        category_url = f"{bot.root_url}/p-kategorie-aendern.html"
        if path_candidate:
            category_url = f"{category_url}#?path={path_candidate}"
        await bot.web_open(category_url)
        await bot._dismiss_consent_banner()  # noqa: SLF001
        raw = await bot.web_execute(js)
        parsed = dict(raw) if isinstance(raw, dict) else {"raw": raw}

        if isinstance(parsed, dict):
            parsed["path_candidate"] = path_candidate
            attempts.append(parsed)

            checks_raw = parsed.get("checks")
            checks:dict[str, Any] = checks_raw if isinstance(checks_raw, dict) else {}
            found_any = any(bool(item.get("found")) for item in checks.values() if isinstance(item, dict))
            title = str(parsed.get("title", ""))
            if found_any or "400" not in title:
                parsed["attempts"] = attempts
                return parsed

    return {"attempts": attempts, "error": "no category-step selector candidate found"}


async def _install_dialog_auto_accept(bot:KleinanzeigenBot) -> Callable[[], None]:
    """Register a CDP event handler that auto-accepts JavaScript dialogs.

    Handles the "Wollen Sie die Seite wirklich verlassen?" beforeunload dialog
    that appears when navigating away from the publish form with unsaved data.

    Uses a synchronous callback that enqueues the CDP ``handleJavaScriptDialog``
    call via ``asyncio.ensure_future`` to avoid deadlocking the CDP event loop.
    """
    import nodriver.cdp.page as cdp_page  # noqa: PLC0415

    def _on_javascript_dialog(_event:Any) -> None:
        asyncio.ensure_future(bot.page.send(cdp_page.handle_java_script_dialog(accept = True)))

    handlers = bot.page.handlers.setdefault(cdp_page.JavascriptDialogOpening, [])
    handlers.append(_on_javascript_dialog)

    def _remove_handler() -> None:
        try:
            handlers.remove(_on_javascript_dialog)
        except ValueError:
            pass

    return _remove_handler


async def _probe_delete_flow(bot:KleinanzeigenBot) -> dict[str, Any]:
    """Non-destructive probe for the delete-ad flow (issue #991).

    Verifies:
    1. CSRF token presence and format on m-meine-anzeigen.html
    2. Published-ads API response structure (ad entry keys)
    3. Delete API endpoint response shape for a non-existent ID (no real ads deleted)

    Returns:
        Dict with probe results keyed by verification step.
    """
    result:dict[str, Any] = {}

    # ── Step 1: Open manage-ads page and check CSRF token ──────────────────
    await bot.web_open(f"{bot.root_url}/m-meine-anzeigen.html")
    await bot._dismiss_consent_banner()  # noqa: SLF001
    await bot.web_sleep(1500, 2200)

    csrf_js = """
    (() => {
        const meta = document.querySelector('meta[name=_csrf]');
        return {
            found: !!meta,
            tagName: meta ? meta.tagName : null,
            nameAttr: meta ? meta.getAttribute('name') : null,
            contentAttr: meta ? meta.getAttribute('content') : null,
            contentLength: meta ? (meta.getAttribute('content') || '').length : 0,
            contentPreview: meta ? (meta.getAttribute('content') || '').slice(0, 40) : null,
            // Also check for alternative CSRF patterns
            otherCsrfMetas: Array.from(document.querySelectorAll('meta'))
                .filter(m => /csrf/i.test(m.getAttribute('name') || ''))
                .map(m => ({
                    name: m.getAttribute('name'),
                    contentLength: (m.getAttribute('content') || '').length,
                    contentPreview: (m.getAttribute('content') || '').slice(0, 40),
                })),
        };
    })()
    """
    csrf_data = await bot.web_execute(csrf_js)
    result["csrf_token"] = dict(csrf_data) if isinstance(csrf_data, dict) else {"raw": csrf_data}

    # Verify with web_find (same path as delete_ad)
    try:
        csrf_token_elem = await bot.web_find(By.CSS_SELECTOR, "meta[name=_csrf]")
        csrf_token_value = csrf_token_elem.attrs.get("content")
        result["csrf_token"]["web_find"] = {
            "found": True,
            "content_is_none": csrf_token_value is None,
            "content_length": len(csrf_token_value) if isinstance(csrf_token_value, str) else 0,
        }
    except TimeoutError as exc:
        result["csrf_token"]["web_find"] = {
            "found": False,
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }

    # ── Step 2: Fetch published ads and inspect structure ───────────────────
    published_ads = await bot._fetch_published_ads()  # noqa: SLF001
    total_ads = len(published_ads)
    ads_result:dict[str, Any] = {
        "total_count": total_ads,
    }
    result["published_ads"] = ads_result

    if total_ads > 0:
        # Collect all unique keys across all ad entries
        all_keys:set[str] = set()
        key_counts:dict[str, int] = {}
        id_types:dict[str, int] = {}
        sample_entries:list[dict[str, Any]] = []

        for entry in published_ads[:10]:  # inspect first 10 entries max
            if isinstance(entry, dict):
                all_keys.update(entry.keys())
                for k in entry:
                    key_counts[k] = key_counts.get(k, 0) + 1
                # Track id value types
                raw_id = entry.get("id")
                id_type = type(raw_id).__name__
                id_types[id_type] = id_types.get(id_type, 0) + 1
                # Collect sample (with title/id/state only for privacy)
                sample_entries.append(
                    {
                        "id": entry.get("id"),
                        "id_type": type(entry.get("id")).__name__,
                        "state": entry.get("state"),
                        "title": str(entry.get("title", ""))[:80] if entry.get("title") else None,
                    }
                )

        ads_result.update(
            {
                "all_keys": sorted(all_keys),
                "key_counts": dict(sorted(key_counts.items())),
                "id_value_types": dict(sorted(id_types.items())),
                "sample_entries": sample_entries,
                "has_id_key": "id" in all_keys,
                "has_state_key": "state" in all_keys,
                "has_title_key": "title" in all_keys,
                "required_keys_present": {"id": "id" in all_keys, "state": "state" in all_keys},
            }
        )

        # Check for entries with missing required keys (shouldn't happen after filtering)
        missing_id = sum(1 for e in published_ads if not isinstance(e, dict) or "id" not in e)
        missing_state = sum(1 for e in published_ads if not isinstance(e, dict) or "state" not in e)
        ads_result["entries_missing_id"] = missing_id
        ads_result["entries_missing_state"] = missing_state
    else:
        ads_result["note"] = "No published ads found — structure inspection skipped"

    # ── Step 3: Test delete endpoint with a safe non-existent ID ────────────
    # Use ID 0 which is never a valid ad ID. This verifies:
    # - endpoint URL is reachable
    # - response shape matches expectations
    # - 404 handling works
    # without deleting any real ads.
    SAFE_NON_EXISTENT_ID:Final[int] = 0
    csrf_token = None
    csrf_meta = result.get("csrf_token", {})
    if isinstance(csrf_meta.get("web_find"), dict) and csrf_meta["web_find"].get("found"):
        # Get CSRF token via JS (already on the page)
        csrf_token_raw = await bot.web_execute("document.querySelector('meta[name=_csrf]')?.getAttribute('content')")
        csrf_token = str(csrf_token_raw) if csrf_token_raw else None

    if csrf_token:
        try:
            delete_response = await bot.web_request(
                url = f"{bot.root_url}/m-anzeigen-loeschen.json?ids={SAFE_NON_EXISTENT_ID}",
                method = "POST",
                headers = {"x-csrf-token": csrf_token},
                valid_response_codes = [200, 404],
            )
            result["delete_endpoint_probe"] = {
                "tested_id": SAFE_NON_EXISTENT_ID,
                "status_code": delete_response.get("statusCode"),
                "status_message": delete_response.get("statusMessage"),
                "has_content": "content" in delete_response,
                "content_type": type(delete_response.get("content")).__name__,
                "response_keys": sorted(delete_response.keys()) if isinstance(delete_response, dict) else None,
                "note": "Used non-existent ID 0 — no real ads were deleted",
            }

            # Try to parse response content as JSON
            content = delete_response.get("content")
            if isinstance(content, (str, bytes, bytearray)):
                raw = content.decode("utf-8", errors = "replace") if isinstance(content, (bytes, bytearray)) else content
                try:
                    parsed = json.loads(raw)
                    result["delete_endpoint_probe"]["parsed_content"] = {
                        "type": type(parsed).__name__,
                        "keys": sorted(parsed.keys()) if isinstance(parsed, dict) else None,
                        "preview": str(parsed)[:300],
                    }
                except (ValueError, TypeError):
                    result["delete_endpoint_probe"]["parsed_content"] = {"error": "not valid JSON", "raw_preview": str(raw)[:200]}
        except TimeoutError as exc:
            result["delete_endpoint_probe"] = {
                "tested_id": SAFE_NON_EXISTENT_ID,
                "error": {"type": type(exc).__name__, "message": str(exc)},
            }
    else:
        result["delete_endpoint_probe"] = {
            "tested_id": SAFE_NON_EXISTENT_ID,
            "skipped": True,
            "reason": "CSRF token not available — cannot test delete endpoint",
        }

    # ── Summary of assumptions ──────────────────────────────────────────────
    csrf_ok = isinstance(result["csrf_token"].get("web_find"), dict) and result["csrf_token"]["web_find"].get("found")
    csrf_has_value = csrf_ok and not result["csrf_token"]["web_find"].get("content_is_none", True)
    ads_struct_ok = total_ads == 0 or (ads_result.get("has_id_key", False) and ads_result.get("has_state_key", False))
    delete_probe = result.get("delete_endpoint_probe", {})
    delete_endpoint_ok = not delete_probe.get("skipped", False) and "error" not in delete_probe

    result["assumptions"] = {
        "csrf_token_found": csrf_ok,
        "csrf_token_has_value": csrf_has_value,
        "published_ads_structure_valid": ads_struct_ok,
        "published_ads_has_required_keys": (ads_result.get("required_keys_present") if total_ads > 0 else None),
        "delete_endpoint_reachable": delete_endpoint_ok,
        "delete_response_status": delete_probe.get("status_code"),
        "all_ok": csrf_ok and csrf_has_value and ads_struct_ok and delete_endpoint_ok,
    }

    return result


def _probe_spec_map() -> dict[str, ProbeSpec]:
    async def _login_selectors(ctx:RunContext) -> ProbeResult:
        raw = await _collect_login_selector_presence(ctx.bot)
        return ProbeResult("login-selectors", ProbeStatus.PASSED, "Checked login-page selectors.", raw = raw)

    async def _publish_selectors(ctx:RunContext) -> ProbeResult:
        raw = await _collect_publish_selector_presence(ctx.bot)
        return ProbeResult("publish-selectors", ProbeStatus.PASSED, "Checked publish-form selectors.", raw = raw)

    async def _download_selectors(ctx:RunContext) -> ProbeResult:
        if ctx.ad_cfg is None:
            return ProbeResult(
                "download-selectors",
                ProbeStatus.SKIPPED,
                "Skipped: ad file unavailable.",
                raw = {"skipped": True, "reason": "ad file unavailable"},
            )
        raw = await _collect_download_selector_presence(ctx.bot, ctx.ad_cfg)
        if not raw.get("skipped"):
            raw["creation_date_layout"] = await _collect_download_creation_date_layout(ctx.bot)
        status = _probe_status_from_download_selector_presence(raw)
        missing_required_selectors = raw.get("missing_required_selectors", []) if isinstance(raw, dict) else []
        missing_optional_selectors = raw.get("missing_optional_selectors", []) if isinstance(raw, dict) else []
        present_count = raw.get("present_count", 0) if isinstance(raw, dict) else 0
        total_count = raw.get("total_count", 0) if isinstance(raw, dict) else 0
        missing_required_count = len(missing_required_selectors) if isinstance(missing_required_selectors, list) else 0
        missing_optional_count = len(missing_optional_selectors) if isinstance(missing_optional_selectors, list) else 0
        creation_layout = raw.get("creation_date_layout", {}) if isinstance(raw, dict) else {}
        date_like_spans = creation_layout.get("dateLikeSpans", []) if isinstance(creation_layout, dict) else []
        creation_layout_count = len(date_like_spans) if isinstance(date_like_spans, list) else 0
        summary = (
            f"Checked {total_count} download selectors: {present_count} present, "
            f"{missing_required_count} required missing, {missing_optional_count} optional missing. "
            f"Creation-date layout: {creation_layout_count} date-like span node(s)."
        )
        warnings = []
        if isinstance(missing_required_selectors, list) and missing_required_selectors:
            warnings.append({"message": f"Missing required download selectors: {', '.join(missing_required_selectors)}"})
        if isinstance(missing_optional_selectors, list) and missing_optional_selectors:
            warnings.append({"message": f"Missing optional download selectors: {', '.join(missing_optional_selectors)}"})
        return ProbeResult("download-selectors", status, summary, raw = raw, warnings = warnings)

    async def _pagination_api(ctx:RunContext) -> ProbeResult:
        raw = await _collect_pagination_shape(ctx.bot, ctx.config.max_pages)
        return ProbeResult("pagination-api", ProbeStatus.PASSED, "Checked manage-ads pagination payload shape.", raw = raw)

    async def _pagination_dom(ctx:RunContext) -> ProbeResult:
        raw = await _collect_overview_pagination_dom(ctx.bot)
        return ProbeResult("pagination-dom", ProbeStatus.PASSED, "Checked manage-ads pagination DOM.", raw = raw)

    async def _price_type_controls(ctx:RunContext) -> ProbeResult:
        raw = await _collect_price_type_controls(ctx.bot)
        return ProbeResult("price-type-controls", ProbeStatus.PASSED, "Checked price-type controls.", raw = raw)

    async def _category_step_page(_ctx:RunContext) -> ProbeResult:
        return ProbeResult(
            "category-step-page",
            ProbeStatus.SKIPPED,
            "Category-step probe remains intentionally disabled.",
            raw = {"skipped": True, "reason": "intentionally disabled"},
        )

    async def _shipping_radio_inventory(ctx:RunContext) -> ProbeResult:
        if ctx.ad_cfg is None:
            return ProbeResult(
                "shipping-radio-inventory", ProbeStatus.SKIPPED, "Skipped: ad file unavailable.", raw = {"skipped": True, "reason": "ad file unavailable"}
            )
        raw = await _collect_shipping_radio_inventory(ctx.bot, ctx.ad_cfg, ctx.ad_file_path)
        return ProbeResult("shipping-radio-inventory", ProbeStatus.PASSED, "Collected shipping radio inventory.", raw = raw)

    async def _shipping_dialog(ctx:RunContext) -> ProbeResult:
        if ctx.ad_cfg is None:
            return ProbeResult(
                "shipping-dialog",
                ProbeStatus.SKIPPED,
                "Skipped: ad file unavailable.",
                raw = {
                    "skipped": True,
                    "reason": "ad file unavailable"})
        raw = await _collect_shipping_dialog_flow(ctx.bot, ctx.ad_cfg, ctx.ad_file_path)
        return ProbeResult("shipping-dialog", ProbeStatus.PASSED, "Collected shipping dialog flow.", raw = raw)

    async def _shipping_live(ctx:RunContext) -> ProbeResult:
        if ctx.ad_cfg is None:
            return ProbeResult("shipping-live", ProbeStatus.SKIPPED, "Skipped: ad file unavailable.", raw = {"skipped": True, "reason": "ad file unavailable"})
        raw = await _probe_shipping_and_sell_directly_live(
            ctx.bot,
            ctx.ad_cfg,
            ctx.ad_file_path,
            run_id = ctx.config.run_id,
            save_dom = ctx.config.save_dom,
            dom_dir = ctx.config.dom_dir,
        )
        artifacts:list[ArtifactRef] = []
        dom_snapshots = raw.get("dom_snapshots", []) if isinstance(raw, dict) else []
        if isinstance(dom_snapshots, list):
            for snapshot in dom_snapshots:
                if not isinstance(snapshot, dict):
                    continue
                if not snapshot.get("saved"):
                    continue
                html_path = snapshot.get("html_path")
                if isinstance(html_path, str):
                    artifacts.append(
                        ArtifactRef(
                            name = snapshot.get("label", "dom-snapshot"),
                            path = html_path,
                            description = "Saved DOM snapshot from shipping-live probe",
                        )
                    )
        status = _probe_status_from_shipping_live(raw)
        return ProbeResult("shipping-live", status, "Ran live shipping/sell-directly probe.", raw = raw, artifacts = artifacts)

    async def _field_exercise(ctx:RunContext) -> ProbeResult:
        if not ctx.config.exercise_fields and "field-exercise" not in ctx.config.probes:
            return ProbeResult(
                "field-exercise",
                ProbeStatus.SKIPPED,
                "Skipped: field exercise not explicitly enabled.",
                raw = {"skipped": True, "reason": "pass --exercise-fields or --probe field-exercise to enable"},
            )
        if ctx.ad_cfg is None:
            return ProbeResult("field-exercise", ProbeStatus.SKIPPED, "Skipped: ad file unavailable.", raw = {"skipped": True, "reason": "ad file unavailable"})
        raw = await _exercise_ad_form_fields(ctx.bot, ctx.ad_cfg)
        return ProbeResult("field-exercise", ProbeStatus.PASSED, "Exercised ad form fields.", raw = raw)

    async def _condition_flow(ctx:RunContext) -> ProbeResult:
        if ctx.ad_cfg is None:
            return ProbeResult("condition-flow", ProbeStatus.SKIPPED, "Skipped: ad file unavailable.", raw = {"skipped": True, "reason": "ad file unavailable"})
        probe_values = ctx.config.condition_values or (str((ctx.ad_cfg.special_attributes or {}).get("condition_s") or "like_new"),)
        scenarios:list[dict[str, Any]] = []
        passed = 0
        failed = 0
        skipped = 0
        warnings:list[dict[str, Any]] = []
        errors:list[dict[str, Any]] = []
        for condition_value in probe_values:
            scenario_ad_cfg = _override_condition_probe_ad(ctx.ad_cfg, condition_value, ctx.config.category_override or ctx.ad_cfg.category)
            try:
                scenario = await _probe_condition_flow(ctx.bot, scenario_ad_cfg, ctx.ad_file_path, condition_value)
            except TimeoutError as exc:
                scenario = {
                    "configured_condition_value": condition_value,
                    "normalized_condition_value": _normalize_condition_value(condition_value),
                    "expected_display_candidates": _condition_display_candidates(condition_value),
                    "route_taken": "failed",
                    "pass": False,
                    "failure_reason": {"type": type(exc).__name__, "message": str(exc)},
                }
                errors.append({"condition_value": condition_value, "type": type(exc).__name__, "message": str(exc)})
            if isinstance(scenario, dict) and scenario.get("skipped"):
                skipped += 1
            elif isinstance(scenario, dict) and scenario.get("pass"):
                passed += 1
            else:
                failed += 1
            scenarios.append(scenario)
        executed = passed + failed
        all_skipped = bool(scenarios) and skipped == len(scenarios)
        all_passed = bool(scenarios) and executed == len(scenarios) and passed == len(scenarios)
        all_failed = bool(scenarios) and executed == len(scenarios) and failed == len(scenarios)
        if all_skipped:
            status = ProbeStatus.SKIPPED
        elif all_passed:
            status = ProbeStatus.PASSED
        elif all_failed:
            status = ProbeStatus.FAILED
        else:
            status = ProbeStatus.PARTIAL
        raw = {
            "setup": {"category_override": ctx.config.category_override, "requested_values": list(probe_values)},
            "scenarios": scenarios,
            "scenarios_passed": passed,
            "scenarios_failed": failed,
            "scenarios_skipped": skipped,
            "scenarios_executed": executed,
            "overall_pass": all_passed,
        }
        return ProbeResult("condition-flow", status, "Checked condition read-back flow.", raw = raw, errors = errors, warnings = warnings)

    async def _special_attributes_readback(ctx:RunContext) -> ProbeResult:
        if ctx.ad_cfg is None:
            return ProbeResult(
                "special-attributes-readback", ProbeStatus.SKIPPED, "Skipped: ad file unavailable.", raw = {"skipped": True, "reason": "ad file unavailable"}
            )
        raw = await _verify_special_attribute_set_readback(ctx.bot, ctx.ad_cfg, ctx.ad_file_path)
        status = _probe_status_from_special_attributes_readback(raw)
        summary = "Verified special-attribute set/read-back."
        return ProbeResult("special-attributes-readback", status, summary, raw = raw)

    async def _button_combobox(ctx:RunContext) -> ProbeResult:
        if ctx.ad_cfg is None:
            return ProbeResult(
                "button-combobox",
                ProbeStatus.SKIPPED,
                "Skipped: ad file unavailable.",
                raw = {
                    "skipped": True,
                    "reason": "ad file unavailable"})
        raw = await _probe_button_combobox_options(ctx.bot, ctx.ad_cfg, ctx.ad_file_path)
        return ProbeResult("button-combobox", ProbeStatus.PASSED, "Probed button-combobox special attributes.", raw = raw)

    async def _delete_flow(ctx:RunContext) -> ProbeResult:
        raw = await _probe_delete_flow(ctx.bot)
        status = ProbeStatus.PASSED if isinstance(raw, dict) and raw.get("assumptions", {}).get("all_ok") else ProbeStatus.PARTIAL
        warnings = []
        if isinstance(raw, dict) and not raw.get("published_ads", {}).get("total_count"):
            warnings.append({"message": "published ads helper returned no entries"})
        return ProbeResult("delete-flow", status, "Checked delete flow without touching real ads.", raw = raw, warnings = warnings)

    return {
        "login-selectors": ProbeSpec("login-selectors", "Inspect login-page selector presence.", _login_selectors, prelogin = True),
        "publish-selectors": ProbeSpec("publish-selectors", "Inspect publish-form selector presence.", _publish_selectors),
        "download-selectors": ProbeSpec("download-selectors", "Inspect download-flow selector presence.", _download_selectors, needs_ad = True),
        "pagination-api": ProbeSpec("pagination-api", "Inspect the manage-ads pagination JSON payload.", _pagination_api),
        "pagination-dom": ProbeSpec("pagination-dom", "Inspect the manage-ads pagination DOM.", _pagination_dom),
        "price-type-controls": ProbeSpec("price-type-controls", "Inspect price-type controls on the publish form.", _price_type_controls),
        "category-step-page": ProbeSpec("category-step-page", "Probe the category-step page (currently disabled).", _category_step_page),
        "shipping-radio-inventory": ProbeSpec(
            "shipping-radio-inventory",
            "Inspect shipping radio inventory and labels.",
            _shipping_radio_inventory,
            needs_ad = True,
        ),
        "shipping-dialog": ProbeSpec(
            "shipping-dialog",
            "Inspect the shipping dialog flow.",
            _shipping_dialog,
            needs_ad = True,
        ),
        "shipping-live": ProbeSpec(
            "shipping-live",
            "Run the live shipping/sell-directly probe.",
            _shipping_live,
            needs_ad = True,
        ),
        "field-exercise": ProbeSpec(
            "field-exercise",
            "Exercise form fields for ad-driven diagnostics.",
            _field_exercise,
            needs_ad = True,
        ),
        "condition-flow": ProbeSpec(
            "condition-flow",
            "Exercise condition special-attribute flow.",
            _condition_flow,
            needs_ad = True,
        ),
        "special-attributes-readback": ProbeSpec(
            "special-attributes-readback",
            "Probe special-attribute set/read-back.",
            _special_attributes_readback,
            needs_ad = True,
        ),
        "button-combobox": ProbeSpec(
            "button-combobox",
            "Inspect button-combobox special-attribute controls.",
            _button_combobox,
            needs_ad = True,
        ),
        "delete-flow": ProbeSpec("delete-flow", "Probe the non-destructive delete flow.", _delete_flow),
    }


PROBE_SPECS:Final[dict[str, ProbeSpec]] = _probe_spec_map()
PRESETS:Final[dict[str, tuple[str, ...]]] = {
    "full": (
        "login-selectors",
        "publish-selectors",
        "download-selectors",
        "pagination-api",
        "pagination-dom",
        "price-type-controls",
        "category-step-page",
        "shipping-radio-inventory",
        "shipping-dialog",
        "shipping-live",
        "condition-flow",
        "special-attributes-readback",
        "button-combobox",
        "delete-flow",
    ),
    "publish-core": (
        "login-selectors",
        "publish-selectors",
        "price-type-controls",
        "pagination-api",
        "pagination-dom",
        "category-step-page",
    ),
    "download": ("download-selectors",),
    "shipping": (
        "shipping-radio-inventory",
        "shipping-dialog",
        "shipping-live",
    ),
    "condition": (
        "condition-flow",
        "special-attributes-readback",
    ),
    "delete-flow": ("delete-flow",),
    "button-combobox": ("button-combobox",),
}


def _make_artifact(name:str, path:str, *, kind:str = "file", description:str | None = None) -> ArtifactRef:
    return ArtifactRef(name = name, path = path, kind = kind, description = description)


def _probe_result_to_dict(result:ProbeResult) -> dict[str, Any]:
    return {
        "name": result.name,
        "status": result.status.value,
        "summary": result.summary,
        "raw": result.raw,
        "errors": result.errors,
        "warnings": result.warnings,
        "artifacts": [asdict(item) for item in result.artifacts],
    }


def _probe_step_ok(container:Any, key:str) -> bool:
    if not isinstance(container, dict):
        return False
    step = container.get(key)
    return isinstance(step, dict) and step.get("ok") is True


def _probe_status_from_shipping_live(raw:Any) -> ProbeStatus:
    if not isinstance(raw, dict):
        return ProbeStatus.FAILED

    setup = raw.get("setup", {})
    setup_ok = all(_probe_step_ok(setup, key) for key in ("set_ad_type", "set_category", "set_ad_type_after_nav", "set_category_after_nav"))
    shipping_ok = _probe_step_ok(raw, "set_shipping")
    sell_directly_ok = _probe_step_ok(raw, "sell_directly")

    if setup_ok and shipping_ok and sell_directly_ok:
        return ProbeStatus.PASSED
    if setup_ok or shipping_ok or sell_directly_ok:
        return ProbeStatus.PARTIAL
    return ProbeStatus.FAILED


def _probe_status_from_special_attributes_readback(raw:Any) -> ProbeStatus:
    if not isinstance(raw, dict):
        return ProbeStatus.FAILED
    if raw.get("skipped"):
        return ProbeStatus.SKIPPED

    set_result = raw.get("set_result", {})
    readback = raw.get("readback", {})
    results = readback.get("results", []) if isinstance(readback, dict) else []
    result_items = [item for item in results if isinstance(item, dict)]

    set_ok = bool(set_result.get("all_set_calls_ok")) or bool(set_result.get("ok"))
    all_matched = bool(readback.get("all_matched"))
    any_set_ok = any(bool(item.get("set_call", {}).get("ok")) for item in result_items)
    any_match = any(bool(item.get("any_match")) for item in result_items)

    if set_ok and all_matched:
        return ProbeStatus.PASSED
    if any_set_ok or any_match:
        return ProbeStatus.PARTIAL
    return ProbeStatus.FAILED


def _probe_status_from_download_selector_presence(raw:Any) -> ProbeStatus:
    if not isinstance(raw, dict):
        return ProbeStatus.FAILED
    if raw.get("skipped"):
        return ProbeStatus.SKIPPED

    missing_required_selectors = raw.get("missing_required_selectors", [])
    if isinstance(missing_required_selectors, list) and not missing_required_selectors:
        return ProbeStatus.PASSED
    if isinstance(missing_required_selectors, list):
        return ProbeStatus.PARTIAL
    return ProbeStatus.FAILED


def _resolve_path(root:Path, value:Path) -> Path:
    return value if value.is_absolute() else (root / value).resolve()


def _build_run_timestamp() -> str:
    return datetime.now(tz = timezone.utc).strftime(RUN_TIMESTAMP_FORMAT)


def _build_run_paths(run_id:str) -> tuple[Path, Path]:
    stem = f"dom-assumptions-{run_id}"
    return RUN_OUTPUT_DIR / f"{stem}.json", RUN_OUTPUT_DIR / f"{stem}.log"


def _cleanup_old_run_outputs(
    output_dir:Path, retention_days:int = RUN_RETENTION_DAYS, patterns:tuple[str, ...] = ("dom-assumptions-*.json", "dom-assumptions-*.log")
) -> int:
    if not output_dir.exists():
        return 0

    cutoff = datetime.now(tz = timezone.utc).timestamp() - retention_days * 86400
    removed = 0
    for pattern in patterns:
        for path in output_dir.glob(pattern):
            try:
                if not path.is_file():
                    continue
                if path.stat().st_mtime >= cutoff:
                    continue
                path.unlink()
            except OSError:
                continue
            removed += 1

    return removed


def _build_run_config(args:argparse.Namespace) -> RunConfig:
    config_path = _resolve_path(ROOT, Path(args.config))
    ad_file = _resolve_path(ROOT, Path(args.ad_file))
    dom_dir = _resolve_path(ROOT, Path(args.dom_dir))
    run_id = _build_run_timestamp()
    report_path, log_path = _build_run_paths(run_id)
    return RunConfig(
        config_path = config_path,
        output_dir = RUN_OUTPUT_DIR,
        run_id = run_id,
        report_path = report_path,
        log_path = log_path,
        max_pages = args.max_pages,
        ad_file = ad_file,
        save_dom = not bool(args.no_save_dom),
        dom_dir = dom_dir,
        probe_login_page = bool(args.probe_login_page),
        exercise_fields = bool(args.exercise_fields),
        preset = args.preset,
        probes = tuple(args.probe) if isinstance(args.probe, list) else (),
        condition_values = tuple(args.condition_value) if isinstance(args.condition_value, list) else (),
        category_override = args.category_override,
    )


def _resolve_probe_names(config:RunConfig) -> list[str]:
    requested:list[str] = []
    if config.preset:
        if config.preset not in PRESETS:
            raise ValueError(f"Unknown preset: {config.preset}")
        requested.extend(PRESETS[config.preset])
    if config.probes:
        requested.extend(config.probes)
    if not requested:
        requested.extend(PRESETS["full"])
    if config.probe_login_page:
        requested.append("login-selectors")
    if config.exercise_fields:
        requested.append("field-exercise")
    ordered:list[str] = []
    seen:set[str] = set()
    for name in requested:
        if name not in PROBE_SPECS or name in seen:
            continue
        seen.add(name)
        ordered.append(name)
    return ordered


def _load_ad_if_available(bot:KleinanzeigenBot, ad_file_path:Path) -> Ad | None:
    if not ad_file_path.exists():
        return None
    try:
        return _load_ad_for_verification(bot, ad_file_path)
    except (OSError, ValueError, KeyError):
        return None


async def run_probe_safe(ctx:RunContext, spec:ProbeSpec) -> ProbeResult:
    try:
        if spec.needs_ad and ctx.ad_cfg is None:
            return ProbeResult(spec.name, ProbeStatus.SKIPPED, "Skipped: ad file unavailable.", raw = {"skipped": True, "reason": "ad file unavailable"})
        result = await spec.runner(ctx)
        if result.status == ProbeStatus.PASSED and result.errors:
            result.status = ProbeStatus.PARTIAL
        return result
    except TimeoutError as exc:
        return ProbeResult(
            spec.name,
            ProbeStatus.FAILED,
            f"{spec.name} timed out.",
            raw = None,
            errors = [{"type": type(exc).__name__, "message": str(exc)}],
        )
    except Exception as exc:  # noqa: BLE001
        return ProbeResult(
            spec.name,
            ProbeStatus.FAILED,
            f"{spec.name} failed.",
            raw = None,
            errors = [{"type": type(exc).__name__, "message": str(exc)}],
        )


async def _run_registry(config:RunConfig) -> dict[str, Any]:
    bot = _prepare_bot(config.config_path, log_path = config.log_path)
    selected_probe_names = _resolve_probe_names(config)
    selected_specs = [PROBE_SPECS[name] for name in selected_probe_names]
    prelogin_specs = [spec for spec in selected_specs if spec.prelogin]
    postlogin_specs = [spec for spec in selected_specs if not spec.prelogin]

    report:dict[str, Any] = {
        "meta": {
            "timestamp": datetime.now(tz = timezone.utc).isoformat(timespec = "seconds"),
            "script": str(SCRIPT_PATH),
            "run_id": config.run_id,
            "preset": config.preset,
            "report_path": str(config.report_path),
            "log_path": str(config.log_path),
            "output_dir": str(config.output_dir),
        },
        "inputs": {
            "config_path": str(config.config_path),
            "report_path": str(config.report_path),
            "log_path": str(config.log_path),
            "output_dir": str(config.output_dir),
            "ad_file": str(config.ad_file),
            "max_pages": config.max_pages,
            "save_dom": config.save_dom,
            "dom_dir": str(config.dom_dir),
            "preset": config.preset,
            "probes": list(config.probes),
            "selected_probes": selected_probe_names,
            "probe_login_page": config.probe_login_page,
            "exercise_fields": config.exercise_fields,
            "condition_values": list(config.condition_values),
            "category_override": config.category_override,
        },
        "summary": {"total": 0, "passed": 0, "partial": 0, "failed": 0, "skipped": 0},
        "probes": [],
        "warnings": [],
        "errors": [],
        "artifacts": [],
    }

    report["artifacts"].append(asdict(_make_artifact("report", str(config.report_path), description = "JSON report")))
    report["artifacts"].append(asdict(_make_artifact("log", str(config.log_path), description = "Run log")))

    def _finalize_report(target:dict[str, Any]) -> None:
        summary = target["summary"]
        for probe in target["probes"]:
            status = probe.get("status")
            if status in summary:
                summary[status] += 1
            summary["total"] += 1
            target["warnings"].extend(probe.get("warnings", []))
            target["errors"].extend(probe.get("errors", []))
            for artifact in probe.get("artifacts", []):
                target["artifacts"].append(artifact)

    def _append_skipped_probe(spec:ProbeSpec, reason:str, summary:str) -> None:
        report["probes"].append(
            _probe_result_to_dict(
                ProbeResult(
                    spec.name,
                    ProbeStatus.SKIPPED,
                    summary,
                    raw = {"skipped": True, "reason": reason},
                )
            )
        )

    try:
        await bot.create_browser_session()
    except Exception as exc:  # noqa: BLE001
        report["errors"].append({"step": "create_browser_session", "type": type(exc).__name__, "message": str(exc)})
        for spec in selected_specs:
            _append_skipped_probe(spec, "browser session could not be created", "Skipped: browser session could not be created.")
        try:
            bot.close_browser_session()
        except Exception as close_exc:  # noqa: BLE001
            report["errors"].append({"step": "close_browser_session", "type": type(close_exc).__name__, "message": str(close_exc)})
        _finalize_report(report)
        return report

    needs_ad = any(spec.needs_ad for spec in selected_specs)
    ad_cfg:Ad | None = None
    if needs_ad:
        ad_cfg = _load_ad_if_available(bot, config.ad_file)
        if not config.ad_file.exists():
            report["warnings"].append({"step": "load_ad", "message": f"ad file not found: {config.ad_file}"})
        elif ad_cfg is None:
            report["errors"].append({"step": "load_ad", "type": "ValueError", "message": f"unable to load ad file: {config.ad_file}"})
    ctx = RunContext(bot = bot, config = config, ad_cfg = ad_cfg, ad_file_path = config.ad_file)
    remove_dialog_auto_accept:Callable[[], None] | None = None

    try:
        for spec in prelogin_specs:
            result = await run_probe_safe(ctx, spec)
            report["probes"].append(_probe_result_to_dict(result))

        try:
            await bot.login()
        except Exception as exc:  # noqa: BLE001
            report["errors"].append({"step": "login", "type": type(exc).__name__, "message": str(exc)})
            for spec in postlogin_specs:
                _append_skipped_probe(spec, "login failed", "Skipped: login failed.")
            _finalize_report(report)
            return report

        remove_dialog_auto_accept = await _install_dialog_auto_accept(bot)

        for spec in postlogin_specs:
            result = await run_probe_safe(ctx, spec)
            report["probes"].append(_probe_result_to_dict(result))

    except Exception as exc:  # noqa: BLE001
        report["errors"].append({"step": "run_registry", "type": type(exc).__name__, "message": str(exc)})
    finally:
        if remove_dialog_auto_accept is not None:
            try:
                remove_dialog_auto_accept()
            except Exception as exc:  # noqa: BLE001
                report["errors"].append({"step": "remove_dialog_auto_accept", "type": type(exc).__name__, "message": str(exc)})
        try:
            bot.close_browser_session()
        except Exception as exc:  # noqa: BLE001
            report["errors"].append({"step": "close_browser_session", "type": type(exc).__name__, "message": str(exc)})

    _finalize_report(report)

    return report


def _print_run_report(report:dict[str, Any]) -> None:
    print(f"Report: {report.get('meta', {}).get('report_path')}")
    print(f"Log: {report.get('meta', {}).get('log_path')}")
    summary = report.get("summary", {})
    print(
        "Summary: "
        f"{summary.get('passed', 0)} passed, "
        f"{summary.get('partial', 0)} partial, "
        f"{summary.get('failed', 0)} failed, "
        f"{summary.get('skipped', 0)} skipped"
    )
    for probe in report.get("probes", []):
        if isinstance(probe, dict):
            print(f"- {probe.get('name')}: {probe.get('status')} — {probe.get('summary')}")
    if report.get("warnings"):
        print(f"Warnings: {len(report['warnings'])}")
    if report.get("errors"):
        print(f"Errors: {len(report['errors'])}")


def _prepare_bot(config_path:Path, *, log_path:Path | None = None) -> KleinanzeigenBot:
    bot = KleinanzeigenBot()
    bot.command = "diagnose"
    bot.config_file_path = str(config_path)
    bot.workspace = xdg_paths.resolve_workspace(
        config_arg = str(config_path),
        logfile_arg = str(log_path) if log_path is not None else None,
        workspace_mode = None,
        logfile_explicitly_provided = log_path is not None,
        log_basename = "verify_dom_assumptions",
    )
    bot.config_file_path = str(bot.workspace.config_file)
    bot.log_file_path = str(bot.workspace.log_file) if bot.workspace.log_file else None
    bot.configure_file_logging()
    bot.load_config()
    return bot


def _print_probe_catalog() -> None:
    print("Available probes:")
    for name, spec in PROBE_SPECS.items():
        suffix = " [prelogin]" if spec.prelogin else ""
        if spec.needs_ad:
            suffix += " [ad]"
        print(f"- {name}{suffix}: {spec.description}")


def _print_preset_catalog() -> None:
    print("Available presets:")
    for name, probes in PRESETS.items():
        print(f"- {name}: {', '.join(probes)}")


def main(argv:list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command == "list-probes":
        _print_probe_catalog()
        return 0

    if args.command == "list-presets":
        _print_preset_catalog()
        return 0

    if args.command != "run":
        return 2

    if _env_flag_enabled("CI") or _env_flag_enabled("GITHUB_ACTIONS"):
        print("Refusing to run verify-dom-assumptions in CI; this tool touches the live site.", file = sys.stderr)
        return 2

    config = _build_run_config(args)
    try:
        selected_probe_names = _resolve_probe_names(config)
    except ValueError as exc:
        print(str(exc))
        return 2

    unknown_probes = [name for name in config.probes if name not in PROBE_SPECS]
    if unknown_probes:
        print(f"Unknown probe(s): {', '.join(unknown_probes)}")
        return 2
    if config.category_override is not None and "condition-flow" not in selected_probe_names:
        print("--category-override can only be used when condition-flow is selected")
        return 2

    removed_run_files = _cleanup_old_run_outputs(config.output_dir)
    removed_snapshot_files = _cleanup_old_run_outputs(config.dom_dir, patterns = ("dom-assumptions-*.html",))
    os.makedirs(config.output_dir, exist_ok = True)
    if config.save_dom:
        os.makedirs(config.dom_dir, exist_ok = True)
    removed = removed_run_files + removed_snapshot_files
    if removed:
        print(f"Cleaned up {removed} expired run file(s) from {config.output_dir} and {config.dom_dir}")

    report = asyncio.run(_run_registry(config))

    class _SafeEncoder(json.JSONEncoder):
        def default(self, o:Any) -> Any:
            try:
                return super().default(o)
            except TypeError:
                return f"<unserializable {type(o).__name__}>"

    with open(config.report_path, "w", encoding = "utf-8") as report_file:
        report_file.write(json.dumps(report, indent = 2, ensure_ascii = False, cls = _SafeEncoder))
    _print_run_report(report)
    unsuccessful_statuses = {ProbeStatus.FAILED.value, ProbeStatus.PARTIAL.value}
    has_unsuccessful_probe = any(isinstance(probe, dict) and probe.get("status") in unsuccessful_statuses for probe in report.get("probes", []))
    return 1 if report.get("errors") or has_unsuccessful_probe else 0


if __name__ == "__main__":
    raise SystemExit(main())
