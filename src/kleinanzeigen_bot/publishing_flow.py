# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
import re
import urllib.parse as urllib_parse
from dataclasses import replace
from gettext import gettext as _
from pathlib import Path
from typing import Any

from nodriver.core.connection import ProtocolException

from . import captcha_flow
from . import local_path_renaming as _local_path_renaming
from .model.ad_model import Ad, AdPartial, AdUpdateStrategy
from .model.config_model import CaptchaConfig, Config
from .utils import dicts as _dicts
from .utils import loggers as _loggers
from .utils import misc as _misc
from .utils.exceptions import PublishSubmissionUncertainError
from .utils.misc import ainput
from .utils.web_scraping_mixin import By, WebScrapingMixin

LOG = _loggers.get_logger(__name__)


def _log_local_path_rename_result(
    result:_local_path_renaming.LocalPathRenameResult,
    ad_id:int,
    local_path_renaming_mode:str,
) -> None:
    """Log a human-readable summary of local path renaming after a republish."""
    path_old_id = result.path_old_id if result.path_old_id is not None else result.yaml_old_id
    id_label = f"ID {path_old_id} -> ID {ad_id}"
    if result.path_old_id is not None and result.yaml_old_id is not None and result.path_old_id != result.yaml_old_id:
        id_label += f" (YAML had ID {result.yaml_old_id})"

    renamed:list[str] = []
    if result.folder_status == _local_path_renaming.RenameStatus.RENAMED:
        renamed.append(_("folder"))
    if result.file_status == _local_path_renaming.RenameStatus.RENAMED:
        renamed.append(_("ad file"))
    if result.renamed_image_count > 0:
        renamed.append(f"{result.renamed_image_count} {_('image(s)')}")

    blocked:list[str] = []
    if result.file_status in {_local_path_renaming.RenameStatus.TARGET_EXISTS, _local_path_renaming.RenameStatus.ERROR}:
        blocked.append(_("ad file"))
    if result.folder_status in {_local_path_renaming.RenameStatus.TARGET_EXISTS, _local_path_renaming.RenameStatus.ERROR}:
        blocked.append(_("ad folder"))
    if result.blocked_image_count > 0:
        blocked.append(f"{result.blocked_image_count} {_('image(s)')}")

    if renamed:
        LOG.info("Local path renaming (%s): %s", id_label, ", ".join(renamed))
        if _local_path_renaming.RenameStatus.RENAMED in {result.file_status, result.folder_status}:
            LOG.info("Updated ad file: %s", result.ad_file)

    if blocked:
        LOG.warning("Local path renaming (%s): could not rename %s (target exists or error)", id_label, ", ".join(blocked))

    if not renamed and not blocked:
        if (
            result.yaml_old_id is not None
            and result.yaml_old_id != ad_id
            and local_path_renaming_mode == "TEMPLATE_MATCH"
        ):
            LOG.info("Local path renaming (%s): no local paths needed renaming", id_label)


def persist_published_ad(
    ad_file:str,
    ad_cfg:Ad,
    ad_cfg_orig:dict[str, Any],
    old_ad_id:int | None,
    ad_id:int,
    mode:AdUpdateStrategy,
    *,
    config:Config,
) -> None:
    """Write the published ad ID, hash, timestamps, and counters back to the
    YAML file, then rename local paths to match the new ID."""
    ad_cfg_orig["id"] = ad_id
    # Rename referenced images before hashing/saving so the YAML content and
    # content_hash reflect only image file renames that actually succeeded.
    image_result = _local_path_renaming.rename_referenced_local_image_files_after_id_change(
        Path(ad_file),
        ad_cfg_orig.get("images"),
        old_id = old_ad_id,
        new_id = ad_id,
        ad_file_name_template = config.download.ad_file_name_template,
        enabled = config.publishing.local_path_renaming.mode == "TEMPLATE_MATCH",
    )
    if image_result.updated_images is not None:
        ad_cfg_orig["images"] = image_result.updated_images

    # Update content hash after successful publication
    # Calculate hash on original config to ensure consistent comparison on restart
    ad_cfg_orig["content_hash"] = AdPartial.model_validate(ad_cfg_orig).update_content_hash().content_hash
    ad_cfg_orig["updated_on"] = _misc.now().isoformat(timespec = "seconds")
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

    try:
        _dicts.save_dict(ad_file, ad_cfg_orig)
    except Exception:
        for old_path, new_path in image_result.renamed_paths:
            try:
                new_path.rename(old_path)
            except OSError:
                LOG.warning("Failed to rollback image rename: %s -> %s", new_path, old_path)
        raise
    # Rename the YAML file and containing folder after saving, because the
    # saved file itself may move as part of this opt-in local migration.
    file_folder_result = _local_path_renaming.rename_local_ad_file_and_folder_after_id_change(
        Path(ad_file),
        old_id = old_ad_id,
        new_id = ad_id,
        ad_file_name_template = config.download.ad_file_name_template,
        folder_name_template = config.download.folder_name_template,
        enabled = config.publishing.local_path_renaming.mode == "TEMPLATE_MATCH",
    )
    rename_result = replace(
        file_folder_result,
        renamed_image_count = image_result.renamed_count,
        blocked_image_count = image_result.blocked_count,
        yaml_old_id = old_ad_id,
    )
    _log_local_path_rename_result(rename_result, ad_id, local_path_renaming_mode = config.publishing.local_path_renaming.mode)
    # NOTE: ad_file string may differ from rename_result.ad_file after the call above.
    # ad_file is stale at this point (pointing to the pre-rename path), but
    # no code in publish_ad() dereferences it after this line, so the drift
    # has no runtime impact.


async def _try_recover_ad_id_from_redirect(web:WebScrapingMixin) -> int | None:
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


async def submit_and_confirm_ad(
    web:WebScrapingMixin,
    ad_file:str,
    ad_cfg:Ad,
    mode:AdUpdateStrategy,
    *,
    captcha_config:CaptchaConfig,
) -> int:
    """Submit the ad form, handle post-submit dialogs, wait for confirmation,
    and extract the published ad ID.

    Returns:
        The published ad ID.

    Raises:
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
    await web.web_click(By.XPATH, "//button[contains(., 'Anzeige aufgeben') or contains(., 'Änderungen speichern') or contains(., 'Anzeige speichern')]")

    # Everything after the first click is uncertain: the ad may already have been submitted.
    ad_id:int | None = None
    try:
        quick_dom = web.timeout("quick_dom")

        # PostListingForm v2 may show an "Effektiver verkaufen" upsell
        # dialog after clicking submit.  Dismiss it so the actual form
        # POST can proceed.
        upsell_dialog = await web.web_probe(
            By.XPATH, "//dialog[@open and contains(., 'Effektiver verkaufen')]", timeout = quick_dom
        )
        if upsell_dialog is not None:
            LOG.info("Dismissing upsell dialog...")
            await web.web_click(
                By.XPATH, "//dialog[@open]//button[contains(., 'Ohne Hochschieben weiter')]",
                timeout = quick_dom,
            )
            await web.web_sleep(500)  # let the dialog close animation finish

        imprint_btn = await web.web_probe(By.ID, "imprint-guidance-submit", timeout = quick_dom)
        if imprint_btn is not None:
            await imprint_btn.click()

        # check for no image question
        if not ad_cfg.images:
            image_hint_xpath = '//button[contains(., "Ohne Bild veröffentlichen")]'
            image_hint_button = await web.web_probe(By.XPATH, image_hint_xpath, timeout = quick_dom)
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

        confirmation_timeout = web.timeout("publishing_confirmation")

        async def _check_confirmation_url() -> bool:
            url = str(await web.web_execute("window.location.href"))
            return "p-anzeige-aufgeben-bestaetigung.html?adId=" in url

        await web.web_await(_check_confirmation_url, timeout = confirmation_timeout)

        # extract the ad id from the URL's query parameter (use JS for fresh URL, not stale page url)
        current_url = str(await web.web_execute("window.location.href"))
        current_url_query_params = urllib_parse.parse_qs(urllib_parse.urlparse(current_url).query)
        ad_id = int(current_url_query_params.get("adId", [])[0])

    except (TimeoutError, ProtocolException, IndexError, ValueError, TypeError) as ex:
        # The confirmation page may have auto-redirected before we could poll it,
        # or the URL was redirected between polling and extraction (race condition).
        # Try to recover the ad ID from tracking data on the current page.
        LOG.debug("Confirmation URL polling or extraction failed (%s), attempting tracking data fallback...", type(ex).__name__)
        try:
            ad_id = await _try_recover_ad_id_from_redirect(web)
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

    return ad_id
