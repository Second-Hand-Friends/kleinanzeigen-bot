# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
from dataclasses import replace
from gettext import gettext as _
from pathlib import Path
from typing import Any

from . import local_path_renaming as _local_path_renaming
from .model.ad_model import Ad, AdPartial, AdUpdateStrategy
from .model.config_model import Config
from .utils import dicts as _dicts
from .utils import loggers as _loggers
from .utils import misc as _misc

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
    is_first_publish = old_ad_id is None
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
    published_at = _misc.now().isoformat(timespec = "seconds")
    ad_cfg_orig["updated_on"] = published_at
    if is_first_publish and not ad_cfg.created_on:
        ad_cfg_orig["created_on"] = published_at

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
    except Exception:  # noqa: BLE001 — intentional broad catch for image rename rollback on save failure
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
