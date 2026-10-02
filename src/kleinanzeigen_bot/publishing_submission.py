# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Submit, confirm, and recover published ad IDs.

This module owns the browser-side publish boundary: captcha handling,
submit click, confirmation polling, and fallback recovery when the
confirmation page redirects too fast to inspect the URL directly.
"""

import html
import json
import re
import urllib.parse as urllib_parse
from gettext import gettext as _
from typing import Final, Literal

from nodriver.core.connection import ProtocolException

from . import captcha_flow, published_ads, publishing_form
from .model.ad_model import Ad, AdUpdateStrategy
from .model.config_model import CaptchaConfig
from .utils import loggers as _loggers
from .utils.exceptions import AdFormValidationError, PublishSubmissionUncertainError
from .utils.misc import ainput
from .utils.web_scraping_mixin import By, WebScrapingMixin

LOG = _loggers.get_logger(__name__)
_PUBLISHED_AD_RECOVERY_DELAYS_MS:Final[tuple[int, ...]] = (0, 1_000, 2_000, 4_000)


async def _is_idless_publish_success_page(web:WebScrapingMixin) -> bool:
    """Detect the redesigned successful publish page that exposes no ad ID."""
    try:
        result = await web.web_execute(r"""
(() => {
    const bodyText = (document.body?.innerText || '').replace(/\s+/g, ' ').trim();
    const hasManageAdsControl = [...document.querySelectorAll('a, button')].some((element) =>
        (element.innerText || '').replace(/\s+/g, ' ').trim().includes('Zu meinen Anzeigen')
    );
    return bodyText.includes('Geschafft!') && hasManageAdsControl;
})()
""")
    except (TimeoutError, ProtocolException):
        return False
    return result is True


async def _try_recover_ad_id_from_published_ads(
    web:WebScrapingMixin,
    *,
    root_url:str,
    title:str,
    known_published_ad_ids:frozenset[int] | None,
) -> int | None:
    """Recover one newly published exact-title ad from a complete list.

    Recovery makes four strict fetch attempts, after delays of 0, 1, 2, and 4
    seconds. It returns ``None`` when the pre-submit baseline is unavailable,
    no unique match appears, or multiple matching candidates make the result
    ambiguous.
    """
    if known_published_ad_ids is None:
        LOG.warning("Published-ad ID recovery skipped because the pre-submit list was incomplete")
        return None

    for delay_ms in _PUBLISHED_AD_RECOVERY_DELAYS_MS:
        if delay_ms:
            await web.web_sleep(delay_ms)
        try:
            current_ads = await published_ads.fetch_published_ads(web, root_url, strict = True)
        except published_ads.PublishedAdsFetchIncompleteError as ex:
            LOG.debug("Strict published-ad recovery fetch failed: %s", ex)
            continue

        candidates:set[int] = set()
        for published_ad in current_ads:
            raw_title = published_ad.get("title")
            if raw_title != title and (not isinstance(raw_title, str) or html.unescape(raw_title) != title):
                continue
            raw_id = published_ad.get("id")
            if raw_id is None:
                continue
            try:
                candidate_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            if candidate_id not in known_published_ad_ids:
                candidates.add(candidate_id)

        if len(candidates) == 1:
            return next(iter(candidates))
        if len(candidates) > 1:
            LOG.warning(
                "Published-ad ID recovery was ambiguous for '%s'; refusing candidate IDs: %s",
                title,
                sorted(candidates),
            )
            return None

    return None


async def _try_recover_ad_id_from_redirect(
    web:WebScrapingMixin,
    *,
    pre_submit_referrer:str | None = None,
) -> int | None:
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
        referrer = str(await web.web_execute("document.referrer") or "")
    except (TimeoutError, ProtocolException) as ex:
        LOG.debug("document.referrer lookup failed (%s), skipping to script scan", type(ex).__name__)
        referrer = ""

    if pre_submit_referrer is not None and referrer == pre_submit_referrer:
        LOG.debug("document.referrer did not change after submit; skipping referrer fallback")
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
        script_content = str(await web.web_execute(
            "[...document.querySelectorAll('script')].map(s => s.textContent).join('\\n')"
        ) or "")
        matches = {
            int(match)
            for match in re.findall(r"p-anzeige-aufgeben-bestaetigung\.html\?adId=(\d+)", script_content)
        }
        if len(matches) == 1:
            ad_id = next(iter(matches))
            LOG.debug("Extracted ad ID %s from inline script fallback", ad_id)
            return ad_id
        if len(matches) > 1:
            LOG.debug("Inline script fallback was ambiguous; refusing matches: %s", sorted(matches))
    except (TimeoutError, ProtocolException, ValueError, TypeError) as ex:
        LOG.debug("Script content scan failed (%s): %s", type(ex).__name__, ex)

    return None


_SUBMIT_LABELS:Final[tuple[str, ...]] = (
    "Anzeige aufgeben",
    "\u00c4nderungen speichern",
    "Anzeige speichern",
)


_FREE_BASIS_PACKAGE_SCRIPT:Final[str] = r"""
(() => {
    const action = ACTION;
    const visible = element => element && element.getClientRects().length > 0
        && getComputedStyle(element).visibility !== 'hidden';
    const text = element => (element?.innerText || '').replace(/\s+/g, ' ').trim();
    if (!window.location.pathname.startsWith('/p-anzeigentypauswahl/')) return {state: 'waiting'};
    const radios = [...document.querySelectorAll('button[role="radio"]')].filter(visible);
    const candidates = radios.filter(element => text(element) === 'Basis Paket');
    if (!candidates.length) return {state: 'waiting'};
    if (candidates.length !== 1) return {state: 'unsafe'};
    const basis = candidates[0];
    let card = basis.parentElement;
    let free = false;
    while (card && card !== document.body) {
        // Stop before the shared container: another package's price cannot prove
        // that the Basis option is free, or invalidate its own free price.
        if (radios.some(element => element !== basis && card.contains(element))) break;
        const cardText = text(card);
        const freePrice = [...card.querySelectorAll('*')].some(element => visible(element)
            && text(element) === 'Kostenlos' && ![...element.children].some(child => text(child) === 'Kostenlos'));
        if (cardText.includes('Kostenlos inserieren – ohne zusätzliche Vorteile.') && freePrice) {
            free = !/\d[\d.,]*\s*(?:€|EUR)/i.test(cardText) && !cardText.includes('Plus Paket');
            break;
        }
        card = card.parentElement;
    }
    if (!free || basis.disabled) return {state: 'unsafe'};
    if (action === 'select' && basis.getAttribute('aria-checked') !== 'true') {
        basis.click();
        return {state: 'waiting'};
    }
    const selected = basis.getAttribute('aria-checked') === 'true';
    if (selected && radios.some(element => element !== basis && element.getAttribute('aria-checked') === 'true')) {
        return {state: 'unsafe'};
    }
    const buttons = [...document.querySelectorAll('button')].filter(element => visible(element)
        && !element.disabled && text(element) === 'Anzeige aufgeben');
    if (action === 'submit') {
        // Recheck the chosen free option atomically with the final click.
        if (!selected || buttons.length !== 1) return {state: 'unsafe'};
        buttons[0].click();
        return {state: 'submitted'};
    }
    return {state: 'ready', selected, submitReady: buttons.length === 1};
})()
"""


async def _submit_free_basis_package(web:WebScrapingMixin) -> None:
    """Choose the verified free package, then submit once outside retry polling."""
    failure_message = _("The free Basis package could not be verified; the ad was not published")

    async def _wait_for_package(*, selected:bool = False) -> None:
        """Poll read-only state; carry rejection out of the retry predicate."""
        async def _check_state() -> dict[str, object] | None:
            errors = await _get_form_validation_errors(web)
            if errors:
                return {"errors": errors}
            result = await web.web_execute(_FREE_BASIS_PACKAGE_SCRIPT.replace("ACTION", json.dumps("inspect")))
            if not isinstance(result, dict):
                return None
            if result.get("state") == "unsafe" or (
                result.get("state") == "ready" and (not selected or (result.get("selected") and result.get("submitReady")))
            ):
                return result
            return None

        try:
            state = await web.web_await(_check_state)
        except (TimeoutError, ProtocolException) as ex:
            raise AdFormValidationError(failure_message) from ex
        if state is None:
            raise AdFormValidationError(failure_message)
        errors = state.get("errors")
        if isinstance(errors, list):
            raise AdFormValidationError(_("Ad form validation failed: %s") % "; ".join(errors))
        if state.get("state") == "unsafe":
            raise AdFormValidationError(failure_message)

    await _wait_for_package()
    try:
        result = await web.web_execute(_FREE_BASIS_PACKAGE_SCRIPT.replace("ACTION", json.dumps("select")))
    except (TimeoutError, ProtocolException) as ex:
        raise AdFormValidationError(failure_message) from ex
    if not isinstance(result, dict) or result.get("state") == "unsafe":
        raise AdFormValidationError(failure_message)
    await _wait_for_package(selected = True)
    try:
        result = await web.web_execute(_FREE_BASIS_PACKAGE_SCRIPT.replace("ACTION", json.dumps("submit")))
    except (TimeoutError, ProtocolException) as ex:
        # The final click may already have published the ad; never retry it.
        raise PublishSubmissionUncertainError(_("submission may have succeeded before failure")) from ex
    if isinstance(result, dict) and result.get("state") == "unsafe":
        raise AdFormValidationError(failure_message)
    if not isinstance(result, dict) or result.get("state") != "submitted":
        raise PublishSubmissionUncertainError(_("submission may have succeeded before failure"))


async def _wait_for_manual_package_submission(web:WebScrapingMixin) -> None:
    """Let the user select and publish; refuse to confirm a remaining draft."""
    failure_message = _("Manual package publication was not confirmed; check your listings before retrying")
    try:
        await ainput(_("Choose a package and finish publishing in the browser, then press Enter to continue..."))
        still_editing = await web.web_execute(r"""
        (() => window.location.pathname.startsWith('/p-anzeigentypauswahl/')
            || window.location.pathname === '/p-anzeige-aufgeben-schritt2.html')()
        """)
    except (EOFError, OSError, TimeoutError, ProtocolException) as ex:
        # A human may have submitted already. Never retry an uncertain publish.
        raise PublishSubmissionUncertainError(failure_message) from ex
    if still_editing is not False:
        raise PublishSubmissionUncertainError(failure_message)


async def _get_form_validation_errors(web:WebScrapingMixin) -> list[str]:
    """Read visible field errors from the ad form without collecting field values."""
    result = await web.web_execute(r"""
    (() => {
        const title = document.getElementById('ad-title');
        if (!title) return [];
        let form = title.closest('form, main, [role="main"]');
        if (!form) {
            // The React listing editor has no form element. Limit the fallback
            // to the editor's top-level container instead of unrelated page UI.
            form = title.parentElement;
            while (form?.parentElement && form.parentElement !== document.body) form = form.parentElement;
        }
        if (!form) return [];
        const visible = element => element && element.getClientRects().length > 0
            && getComputedStyle(element).visibility !== 'hidden';
        const text = element => (element?.innerText || '').replace(/\s+/g, ' ').trim();
        const labels = [...form.querySelectorAll('label')];
        const labelFor = element => {
            let scope = element;
            for (let depth = 0; depth < 6 && scope && scope !== form; depth++, scope = scope.parentElement) {
                const controls = [scope, ...scope.querySelectorAll('input[id], select[id], textarea[id], button[id]')];
                const matches = new Set();
                for (const control of controls) {
                    const label = labels.find(candidate => control.id && candidate.htmlFor === control.id);
                    if (label && text(label)) matches.add(text(label));
                }
                if (matches.size === 1) return [...matches][0];
                if (matches.size > 1) break;
                const localLabels = scope.querySelectorAll('label');
                if (localLabels.length === 1) return text(localLabels[0]);
            }
            return element.matches('input, select, textarea, button') ? element.getAttribute('name') || element.id || '' : '';
        };
        const errors = new Set();
        const reportedElements = new Set();
        const add = (element, message) => {
            if (!message) return;
            const label = labelFor(element);
            errors.add(label ? `${label}: ${message}` : message);
        };
        for (const field of form.querySelectorAll('[aria-invalid="true"], input:invalid, select:invalid, textarea:invalid')) {
            if (!visible(field)) continue;
            const errorReferences = field.getAttribute('aria-errormessage');
            const references = errorReferences
                || (field.getAttribute('aria-invalid') === 'true' ? field.getAttribute('aria-describedby') : '') || '';
            const messages = references.trim().split(/\s+/).map(id => document.getElementById(id))
                .filter(element => element && form.contains(element) && visible(element))
                // Descriptions can be hints; the live brand control marks its error text-critical.
                .filter(element => errorReferences || element.matches('[id$="-error"], [role="alert"], [role="status"], .text-critical'))
                .map(element => { reportedElements.add(element); return text(element); }).filter(Boolean);
            if (!messages.length && !field.validationMessage && field.getAttribute('aria-invalid') === 'true') {
                // Some React fields expose an adjacent status without an ARIA
                // reference. Only inspect a container holding this one input.
                for (let scope = field.parentElement; scope && scope !== form; scope = scope.parentElement) {
                    const controls = [...scope.querySelectorAll('input, select, textarea')].filter(visible);
                    if (controls.length !== 1) break;
                    const statuses = [...scope.querySelectorAll('[role="status"], [role="alert"]')].filter(visible);
                    if (statuses.length) {
                        for (const status of statuses) {
                            reportedElements.add(status);
                            if (text(status)) messages.push(text(status));
                        }
                        break;
                    }
                }
            }
            add(field, messages.join(' ') || field.validationMessage);
        }
        // The redesigned category controls reuse id="attribute-error" and do not
        // consistently mark their inputs aria-invalid. Read every visible error.
        for (const error of form.querySelectorAll('[id$="-error"]')) {
            if (visible(error) && !reportedElements.has(error)) add(error, text(error));
        }
        return [...errors];
    })()
    """)
    return [error for error in result if isinstance(error, str) and error.strip()] if isinstance(result, list) else []


async def _click_submit_button(
    web:WebScrapingMixin,
    *,
    package_selection:Literal["BASIS", "MANUAL"] = "MANUAL",
) -> None:
    """Find and click the submit button across page variants.

    Package selection stays manual unless BASIS is explicitly configured.
    Uses JS to locate the <button> element (not a child span/div) containing
    the label text, ensuring the click reaches the actual button.  Wrapped in
    ``web_await`` to retry because React may not have re-rendered the submit
    button after a deferred title update or captcha solve.
    """

    next_step_available = False
    package_step_available = False

    async def _find_and_click() -> bool:
        """Attempt to find and click a submit button for any known label."""
        nonlocal next_step_available, package_step_available
        for label in _SUBMIT_LABELS:
            clicked = await web.web_execute(f"""
            (() => {{
                if (window.location.pathname.startsWith('/p-anzeigentypauswahl/')) return 'package';
                const buttons = document.querySelectorAll('button');
                for (const btn of buttons) {{
                    if (btn.textContent.trim().includes('{label}')) {{
                        btn.click();
                        return true;
                    }}
                }}
                return false;
            }})()
            """)
            if clicked == "package":
                package_step_available = True
                return True
            if clicked:
                return True
        next_step_available = await web.web_execute(r"""
        (() => [...document.querySelectorAll('button')].some(button =>
            button.getClientRects().length > 0 && getComputedStyle(button).visibility !== 'hidden'
            && !button.disabled && button.textContent.replace(/\s+/g, ' ').trim() === 'Nächster Schritt'))()
        """) is True
        return next_step_available

    try:
        await web.web_await(
            _find_and_click,
            timeout_error_message = str(_("Could not find submit button")),
        )
    except TimeoutError:
        raise TimeoutError(_("Could not find submit button")) from None
    if next_step_available:
        try:
            continued = await web.web_execute(r"""
            (() => {
                const button = [...document.querySelectorAll('button')].find(element =>
                    element.getClientRects().length > 0 && getComputedStyle(element).visibility !== 'hidden'
                    && !element.disabled && element.textContent.replace(/\s+/g, ' ').trim() === 'Nächster Schritt');
                if (!button) return false;
                button.click();
                return true;
            })()
            """)
        except (TimeoutError, ProtocolException) as ex:
            raise AdFormValidationError(_("Could not continue to package selection; the ad was not published")) from ex
        if continued is not True:
            raise AdFormValidationError(_("Could not continue to package selection; the ad was not published"))
    if next_step_available or package_step_available:
        if package_selection == "BASIS":
            await _submit_free_basis_package(web)
        else:
            await _wait_for_manual_package_submission(web)
    await web.web_sleep()


async def _handle_post_submit_prompts(web:WebScrapingMixin, *, has_images:bool) -> None:
    """Dismiss optional prompts before waiting for publication confirmation."""
    quick_dom = web.timeout("quick_dom")

    # PostListingForm v2 may show an "Effektiver verkaufen" upsell
    # dialog after clicking submit.  Dismiss it so the actual form
    # POST can proceed.
    upsell_dialog_text = await web.web_probe(By.TEXT, "Effektiver verkaufen", timeout = quick_dom)
    if upsell_dialog_text is not None:
        LOG.info("Dismissing upsell dialog...")
        dismiss_btn = await web.web_find(By.TEXT, "Ohne Hochschieben weiter", timeout = quick_dom)
        await dismiss_btn.click()
        await web.web_sleep(500)  # let the dialog close animation finish

    imprint_btn = await web.web_probe(By.ID, "imprint-guidance-submit", timeout = quick_dom)
    if imprint_btn is not None:
        await imprint_btn.click()

    # check for no image question
    if not has_images:
        image_hint_button = await web.web_probe(By.TEXT, "Ohne Bild ver\u00f6ffentlichen", timeout = quick_dom)
        if image_hint_button is not None:
            await image_hint_button.click()

    #############################
    # wait for payment form if commercial account is used
    #############################
    payment_form = await web.web_probe(By.ID, "myftr-shppngcrt-frm", timeout = quick_dom)
    if payment_form is not None:
        LOG.warning("############################################")
        LOG.warning("# Payment form detected! Please proceed with payment.")
        LOG.warning("############################################")
        await web.web_scroll_page_down()
        await ainput(_("Press a key to continue..."))


async def submit_and_confirm_ad(
    web:WebScrapingMixin,
    ad_file:str,
    ad_cfg:Ad,
    mode:AdUpdateStrategy,
    *,
    captcha_config:CaptchaConfig,
    root_url:str,
    package_selection:Literal["BASIS", "MANUAL"] = "MANUAL",
    known_published_ad_ids:frozenset[int] | None = None,
) -> int:
    """Submit the ad form, handle post-submit dialogs, wait for confirmation,
    and extract the published ad ID.

    Returns:
        The published ad ID.

    Raises:
        AdFormValidationError: The form explicitly rejected the submitted values.
        PublishSubmissionUncertainError: The submission may have succeeded
            but the ad ID could not be recovered.
        RuntimeError: An internal invariant was violated (ad_id is None
            despite the recovery path).
    """

    #############################
    # wait for captcha
    #############################
    operation_label = {
        AdUpdateStrategy.REPLACE: "publish",
        AdUpdateStrategy.MODIFY: "update",
    }.get(mode, mode.name.lower())
    await captcha_flow.check_and_wait_for_captcha(web, captcha_config, is_login_page = False, page_context = f"{operation_label} operation")

    #############################
    # set title (right before submit to prevent React re-render clearing it)
    #############################
    LOG.debug("Setting title '%s' (deferred to prevent React re-render clearing it)", ad_cfg.title)
    await web.web_set_input_value("ad-title", ad_cfg.title)
    await publishing_form.verify_text_special_attributes(web, ad_cfg)

    #############################
    # submit
    #############################
    # Click is retryable — no submission can have occurred before this point.
    # Edit page uses 'Änderungen speichern' or 'Anzeige speichern'; publish page uses 'Anzeige aufgeben'
    pre_submit_referrer = str(await web.web_execute("document.referrer") or "")
    await _click_submit_button(web, package_selection = package_selection)

    # After the first click, only explicit form rejection proves submission failed.
    ad_id:int | None = None
    idless_success_detected = False
    validation_errors:list[str] = []
    try:
        validation_errors = await _get_form_validation_errors(web)
        if validation_errors:
            raise AdFormValidationError(_("Ad form validation failed: %s") % "; ".join(validation_errors))

        await _handle_post_submit_prompts(web, has_images = bool(ad_cfg.images))

        confirmation_timeout = web.timeout("publishing_confirmation")

        async def _check_confirmation_state() -> bool:
            """Stop polling on confirmation or explicit form rejection."""
            nonlocal idless_success_detected, validation_errors
            url = str(await web.web_execute("window.location.href"))
            if "p-anzeige-aufgeben-bestaetigung.html?adId=" in url:
                return True
            if await _is_idless_publish_success_page(web):
                idless_success_detected = True
                return True
            validation_errors = await _get_form_validation_errors(web)
            # web_await retries predicate exceptions. Return a terminal state
            # and raise outside it so deterministic rejection fails promptly.
            return bool(validation_errors)

        await web.web_await(_check_confirmation_state, timeout = confirmation_timeout)

        if validation_errors:
            raise AdFormValidationError(_("Ad form validation failed: %s") % "; ".join(validation_errors))

        if idless_success_detected:
            if mode == AdUpdateStrategy.MODIFY:
                ad_id = ad_cfg.id
                if ad_id is None:
                    raise PublishSubmissionUncertainError(
                        _("update succeeded but the configured ad ID is missing")
                    )
                LOG.warning(
                    "Update confirmation page exposed no ad ID; using configured ad ID %s",
                    ad_id,
                )
            else:
                try:
                    ad_id = await _try_recover_ad_id_from_published_ads(
                        web,
                        root_url = root_url,
                        title = ad_cfg.title,
                        known_published_ad_ids = known_published_ad_ids,
                    )
                except Exception as recovery_ex:  # noqa: BLE001
                    LOG.debug("Published-ad list fallback failed: %s", recovery_ex)
                    raise PublishSubmissionUncertainError(
                        "publish succeeded but no ad ID could be recovered"
                    ) from recovery_ex
                if ad_id is None:
                    raise PublishSubmissionUncertainError(
                        "publish succeeded but no ad ID could be recovered"
                    )
                LOG.warning(
                    "Confirmation page exposed no ad ID; recovered ad ID %s from the published ads list",
                    ad_id,
                )
        else:
            # Use the live URL because the page object URL may be stale after redirects.
            current_url = str(await web.web_execute("window.location.href"))
            current_url_query_params = urllib_parse.parse_qs(urllib_parse.urlparse(current_url).query)
            ad_id = int(current_url_query_params.get("adId", [])[0])

    except (TimeoutError, ProtocolException, IndexError, ValueError, TypeError) as ex:
        # The confirmation page may have auto-redirected before we could poll it,
        # or the URL was redirected between polling and extraction (race condition).
        # Try to recover the ad ID from tracking data on the current page.
        LOG.debug("Confirmation URL polling or extraction failed (%s), attempting tracking data fallback...", type(ex).__name__)
        recovered_from_tracking = False
        try:
            ad_id = await _try_recover_ad_id_from_redirect(web, pre_submit_referrer = pre_submit_referrer)
            recovered_from_tracking = ad_id is not None
        except Exception as fallback_ex:  # noqa: BLE001
            LOG.debug("Tracking data fallback failed: %s", fallback_ex)

        if ad_id is None:
            raise PublishSubmissionUncertainError("submission may have succeeded before failure") from ex

        if recovered_from_tracking:
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

    return ad_id
