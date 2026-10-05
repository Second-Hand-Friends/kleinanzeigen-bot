# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Submit, confirm, and recover published ad IDs.

This module owns the browser-side publish boundary: captcha handling,
submit click, confirmation polling, and fallback recovery when the
confirmation page redirects too fast to inspect the URL directly.
"""

import json
import re
import urllib.parse as urllib_parse
from gettext import gettext as _
from typing import Final

from nodriver.core.connection import ProtocolException

from . import captcha_flow, published_ads
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
            if published_ad.get("title") != title:
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


async def _get_form_validation_errors(web:WebScrapingMixin) -> list[str]:
    """Read visible field errors from the ad form without collecting field values."""
    result = await web.web_execute(r"""
    (() => {
        const visible = element => element && element.getClientRects().length > 0
            && getComputedStyle(element).visibility !== 'hidden';
        const title = document.getElementById('ad-title');
        if (!visible(title)) return [];
        const text = element => (element?.innerText || '').replace(/\s+/g, ' ').trim();
        const nativeForm = title.closest('form');
        const controls = [...document.querySelectorAll('input, select, textarea, button')];
        const attributeInputs = controls.filter(element =>
            /^attributeMap\[.+\]$/.test(element.getAttribute('name') || ''));
        const attributeIds = new Set(attributeInputs.flatMap(element =>
            element.getAttribute('name').slice(13, -1).split('+')));
        const adControls = controls.filter(element => element.id.startsWith('ad-') || attributeIds.has(element.id)
            || attributeInputs.includes(element));
        // Compound backing names can drive one visible combobox whose ID is
        // independent of the attribute key. Associate only a single field-local
        // control; stop before a shared editor/app container with other fields.
        for (const backing of attributeInputs) {
            for (let scope = backing.parentElement; scope && scope !== document.body; scope = scope.parentElement) {
                const localControls = [...scope.querySelectorAll('input, select, textarea, button[role="combobox"]')].filter(visible);
                if (!localControls.length) continue;
                if (localControls.length === 1 && localControls[0].getAttribute('role') === 'combobox'
                    && !adControls.includes(localControls[0])) adControls.push(localControls[0]);
                break;
            }
        }
        let form = nativeForm || title.parentElement;
        // React has no form/main boundary. Find the smallest common editor
        // container; never treat every input in its application root as an ad.
        while (!nativeForm && form && !adControls.every(element => form.contains(element))) form = form.parentElement;
        if (!form) return [];
        const fields = [...form.querySelectorAll('input, select, textarea, button')].filter(element =>
            nativeForm || adControls.includes(element));
        const belongsToField = element => {
            for (let scope = element.parentElement; scope && scope !== form; scope = scope.parentElement) {
                const localControls = [...scope.querySelectorAll('input, select, textarea, button')];
                if (!localControls.length) continue;
                if (localControls.some(control => !fields.includes(control))) return false;
                const labelledIds = new Set(localControls.filter(control => visible(control) && control.id).map(control => control.id));
                // Hidden attribute backing inputs and their one labelled button
                // share a field, whereas an editor-wide notification does not.
                return labelledIds.size === 1;
            }
            return false;
        };
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
        for (const field of fields.filter(element => element.matches('[aria-invalid="true"], input:invalid, select:invalid, textarea:invalid'))) {
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
                    const controls = [...scope.querySelectorAll('input, select, textarea, button')].filter(visible);
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
        // consistently mark their inputs aria-invalid. A native form also owns
        // its explicit error summary; React errors need a field association.
        for (const error of form.querySelectorAll('[id$="-error"]')) {
            if (visible(error) && !reportedElements.has(error) && (nativeForm || belongsToField(error))) add(error, text(error));
        }
        return [...errors];
    })()
    """)
    return [error for error in result if isinstance(error, str) and error.strip()] if isinstance(result, list) else []


_SUBMIT_BUTTON_SCRIPT:Final[str] = r"""
(() => {
    const action = ACTION;
    const visible = element => element.getClientRects().length > 0
        && getComputedStyle(element).visibility !== 'hidden' && !element.disabled;
    const labels = LABELS;
    const buttons = [...document.querySelectorAll('button')].filter(visible);
    const submit = buttons.find(button => labels.some(label => button.textContent.trim().includes(label)));
    if (submit) {
        if (action === 'inspect') return 'submit';
        if (action !== 'submit') return false;
        submit.click();
        return true;
    }
    return false;
})()
""".replace("LABELS", json.dumps(_SUBMIT_LABELS))


async def _find_submit_button(web:WebScrapingMixin) -> object:
    """Wait for a submit control without performing any click inside polling."""
    async def _inspect() -> object:
        """Read the available submit action without clicking any control."""
        return await web.web_execute(_SUBMIT_BUTTON_SCRIPT.replace("ACTION", json.dumps("inspect")))

    return await web.web_await(_inspect, timeout_error_message = str(_("Could not find submit button")))


async def _click_final_submit_button(web:WebScrapingMixin) -> None:
    """Attempt one final click, treating a lost response as an uncertain publish."""
    try:
        submitted = await web.web_execute(_SUBMIT_BUTTON_SCRIPT.replace("ACTION", json.dumps("submit")))
    except (TimeoutError, ProtocolException) as ex:
        raise PublishSubmissionUncertainError(_("submission may have succeeded before failure")) from ex
    if submitted is False:
        # The script explicitly found no button and performed no click.
        raise TimeoutError(_("Could not find submit button"))
    if submitted is not True:
        raise PublishSubmissionUncertainError(_("submission may have succeeded before failure"))


async def _click_submit_button(web:WebScrapingMixin) -> None:
    """Discover the submit control, then attempt one final click outside polling."""
    await _find_submit_button(web)
    await _click_final_submit_button(web)
    try:
        await web.web_sleep()
    except (TimeoutError, ProtocolException) as ex:
        raise PublishSubmissionUncertainError(_("submission may have succeeded before failure")) from ex


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

    #############################
    # submit
    #############################
    # Click is retryable — no submission can have occurred before this point.
    # Edit page uses 'Änderungen speichern' or 'Anzeige speichern'; publish page uses 'Anzeige aufgeben'
    pre_submit_referrer = str(await web.web_execute("document.referrer") or "")
    await _click_submit_button(web)

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
