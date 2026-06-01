# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

"""Local path renaming helpers for ad files, folders, and referenced images."""

import enum, functools, re  # isort: skip
from dataclasses import dataclass
from dataclasses import field as dc_field
from gettext import gettext as _
from pathlib import Path
from string import Formatter
from typing import Final

from kleinanzeigen_bot.utils import loggers

LOG:Final[loggers.Logger] = loggers.get_logger(__name__)

__all__ = [
    "ImageRenameResult",
    "LocalPathRenameResult",
    "RenamePathResult",
    "RenameStatus",
    "rename_local_ad_file_after_id_change",
    "rename_local_ad_file_and_folder_after_id_change",
    "rename_local_ad_folder_after_id_change",
    "rename_path_if_target_is_free",
    "rename_referenced_local_image_file_after_id_change",
    "rename_referenced_local_image_files_after_id_change",
    "replace_template_id_slot",
]

DOWNLOAD_IMAGE_FILENAME_RE:Final[re.Pattern[str]] = re.compile(r"^(?P<prefix>.+?)(?P<image_suffix>__img\d+(?:\..*)?)$")


@functools.lru_cache(maxsize = 32)
def _build_id_slot_regex(template:str) -> re.Pattern[str]:
    """Build a compiled regex from a download template with named groups.

    Returns a regex where {id} captures any digit run as group 'id'
    and {title} matches any text non-greedily.
    """
    regex_parts:list[str] = []
    parsed_template = list(Formatter().parse(template))
    for literal_text, field_name, _format_spec, _conversion in parsed_template:
        regex_parts.append(re.escape(literal_text))
        if field_name == "id":
            regex_parts.append(r"(?P<id>\d+)")
        elif field_name == "title":
            regex_parts.append(".*?")
    return re.compile("".join(regex_parts))


def replace_template_id_slot(template:str, name:str, new_id:int) -> tuple[str | None, int | None]:
    """Replace the numeric ID in the template-defined {id} slot with new_id.

    The {id} slot matches any sequence of digits; renaming is skipped if the
    matched ID already equals new_id.  The {title} slot uses non-greedy
    matching so the {id} slot greedily captures the maximal digit run — even
    when {title} and {id} are adjacent in the template.  This preserves
    user-edited or previously truncated title fragments instead of re-rendering.

    Returns ``(new_name, old_id)`` where ``new_name`` is None when no rename
    is needed and ``old_id`` is the integer ID extracted from the path.

    .. note::

        The *template* must have already passed download-config validation
        (i.e. it contains an ``{id}`` placeholder and each placeholder
        appears at most once).  Malformed templates may raise ``IndexError``.
    """
    match = _build_id_slot_regex(template).fullmatch(name)
    if match is None:
        return None, None

    old_id_in_path = int(match.group("id"))
    if old_id_in_path == new_id:
        return None, old_id_in_path

    id_start, id_end = match.span("id")
    return f"{name[:id_start]}{new_id}{name[id_end:]}", old_id_in_path


class RenameStatus(enum.Enum):
    """Outcome of an attempted local path rename."""
    RENAMED = enum.auto()
    SAME = enum.auto()
    NO_MATCH = enum.auto()
    TARGET_EXISTS = enum.auto()
    ERROR = enum.auto()


@dataclass(frozen = True)
class RenamePathResult:
    """Result of a local path rename operation."""
    path:Path
    status:RenameStatus


@dataclass(frozen = True)
class LocalPathRenameResult:
    """Aggregate result of local path renaming after a successful republish."""
    ad_file:Path
    file_status:RenameStatus
    folder_status:RenameStatus
    renamed_image_count:int = 0
    blocked_image_count:int = 0
    path_old_id:int | None = None
    yaml_old_id:int | None = None


@dataclass(frozen = True)
class ImageRenameResult:
    """Outcome of renaming referenced local image files."""
    renamed_count:int = 0
    blocked_count:int = 0
    updated_images:list[object] | None = None
    renamed_paths:list[tuple[Path, Path]] = dc_field(default_factory = list)


def rename_path_if_target_is_free(source:Path, target:Path, *, label:str) -> RenamePathResult:
    if source == target:
        return RenamePathResult(source, RenameStatus.SAME)
    if target.exists() or target.is_symlink():
        LOG.debug("Skipping local %s rename because target already exists: %s", label, target)
        return RenamePathResult(source, RenameStatus.TARGET_EXISTS)
    try:
        source.rename(target)
    except OSError as ex:
        LOG.warning("Could not rename local %s from %s to %s: %s", label, source, target, ex)
        return RenamePathResult(source, RenameStatus.ERROR)
    LOG.debug("Renamed local %s from %s to %s", label, source, target)
    return RenamePathResult(target, RenameStatus.RENAMED)


def rename_local_ad_file_after_id_change(ad_file:Path, *, new_id:int, ad_file_name_template:str) -> RenamePathResult:
    """Rename a local ad file when the ad ID changes.

    Matches the file stem against the configured download template, replaces
    the {id} slot, and renames the file when the target name is free.
    """
    renamed_stem, path_id = replace_template_id_slot(ad_file_name_template, ad_file.stem, new_id)
    if renamed_stem is None:
        if path_id == new_id:
            LOG.debug("Skipping local ad file rename because name already contains new ID: %s", ad_file)
            return RenamePathResult(ad_file, RenameStatus.SAME)
        LOG.debug("Skipping local ad file rename because name does not match configured template: %s", ad_file)
        return RenamePathResult(ad_file, RenameStatus.NO_MATCH)
    return rename_path_if_target_is_free(ad_file, ad_file.with_name(f"{renamed_stem}{ad_file.suffix}"), label = _("ad file"))


def rename_referenced_local_image_file_after_id_change(
    ad_file:Path,
    image_ref:object,
    *,
    new_id:int,
    ad_file_name_template:str,
) -> tuple[object, RenameStatus | None]:
    """Rename a single referenced local image when the ad ID changes.

    Only renames images whose filename matches the DOWNLOAD_IMAGE_FILENAME_RE
    pattern and that live within the ad directory.  Returns
    ``(post_rename_image_ref, status)``.
    """
    if not isinstance(image_ref, str):
        return image_ref, None

    original_path = Path(image_ref)
    if original_path.is_absolute():
        return image_ref, None

    image_path = ad_file.parent / original_path
    if not image_path.is_file():
        return image_ref, None
    if not image_path.resolve().is_relative_to(ad_file.parent.resolve()):
        return image_ref, None

    match = DOWNLOAD_IMAGE_FILENAME_RE.fullmatch(image_path.name)
    if match is None:
        return image_ref, None

    renamed_prefix, path_id = replace_template_id_slot(ad_file_name_template, match.group("prefix"), new_id)
    if renamed_prefix is None:
        return image_ref, RenameStatus.SAME if path_id == new_id else None

    renamed_name = f"{renamed_prefix}{match.group('image_suffix')}"
    rename_result = rename_path_if_target_is_free(image_path, image_path.with_name(renamed_name), label = _("image file"))
    if rename_result.status != RenameStatus.RENAMED:
        return image_ref, rename_result.status

    return original_path.with_name(renamed_name).as_posix(), RenameStatus.RENAMED


def rename_referenced_local_image_files_after_id_change(
    ad_file:Path,
    images:object,
    *,
    old_id:int | None,
    new_id:int,
    ad_file_name_template:str,
    enabled:bool,
) -> ImageRenameResult:
    """Rename all referenced local image files when the ad ID changes.

    ``images`` is the image-ref list from the ad config.  ``enabled`` should
    be ``True`` when ``publishing.local_path_renaming.mode == "TEMPLATE_MATCH"``.
    """
    if old_id is None or old_id == new_id or not enabled:
        return ImageRenameResult()
    if images is None or not isinstance(images, list):
        return ImageRenameResult()

    updated_images:list[object] = []
    renamed_count = 0
    blocked_count = 0
    renamed_paths:list[tuple[Path, Path]] = []
    for image_ref in images:
        updated_image_ref, status = rename_referenced_local_image_file_after_id_change(
            ad_file,
            image_ref,
            new_id = new_id,
            ad_file_name_template = ad_file_name_template,
        )
        updated_images.append(updated_image_ref)
        if status == RenameStatus.RENAMED:
            renamed_count += 1
            renamed_paths.append(
                (ad_file.parent / Path(str(image_ref)), ad_file.parent / Path(str(updated_image_ref)))
            )
        elif status in {RenameStatus.TARGET_EXISTS, RenameStatus.ERROR}:
            blocked_count += 1

    if renamed_count > 0:
        return ImageRenameResult(
            renamed_count = renamed_count,
            blocked_count = blocked_count,
            updated_images = updated_images,
            renamed_paths = renamed_paths,
        )

    return ImageRenameResult(renamed_count = renamed_count, blocked_count = blocked_count)


def rename_local_ad_folder_after_id_change(ad_file:Path, *, new_id:int, folder_name_template:str) -> RenamePathResult:
    """Rename the parent folder of an ad file when the ad ID changes.

    The returned ``RenamePathResult.path`` always points to the (possibly
    renamed) ad file inside its (possibly renamed) parent folder.
    """
    parent = ad_file.parent
    renamed_folder_name, path_id = replace_template_id_slot(folder_name_template, parent.name, new_id)
    if renamed_folder_name is None:
        if path_id == new_id:
            LOG.debug("Skipping local ad folder rename because name already contains new ID: %s", parent)
            return RenamePathResult(ad_file, RenameStatus.SAME)
        LOG.debug("Skipping local ad folder rename because name does not match configured template: %s", parent)
        return RenamePathResult(ad_file, RenameStatus.NO_MATCH)

    result = rename_path_if_target_is_free(parent, parent.with_name(renamed_folder_name), label = _("ad folder"))
    if result.status != RenameStatus.RENAMED:
        return RenamePathResult(ad_file, result.status)
    return RenamePathResult(result.path / ad_file.name, RenameStatus.RENAMED)


def rename_local_ad_file_and_folder_after_id_change(
    ad_file:Path,
    *,
    old_id:int | None,
    new_id:int,
    ad_file_name_template:str,
    folder_name_template:str,
    enabled:bool,
) -> LocalPathRenameResult:
    """Rename an ad file and its parent folder when the ad ID changes.

    This is the main coordinator: it gates on ``enabled`` / ``old_id``,
    resolves symlinks, extracts the old path ID, then renames the file
    and folder in sequence.
    """
    if old_id is None or old_id == new_id or not enabled:
        return LocalPathRenameResult(
            ad_file = ad_file,
            file_status = RenameStatus.SAME,
            folder_status = RenameStatus.SAME,
        )

    # resolve() is used intentionally: if the ad file path contains symlinks,
    # we rename the actual target path, not the symlink.
    ad_file = ad_file.resolve()

    # Extract the old ID from the path before renaming (used for logging provenance).
    # Try the file stem first; fall back to the parent folder name.
    __, path_old_id = replace_template_id_slot(ad_file_name_template, ad_file.stem, new_id)
    if path_old_id is None:
        __, path_old_id = replace_template_id_slot(folder_name_template, ad_file.parent.name, new_id)

    file_result = rename_local_ad_file_after_id_change(ad_file, new_id = new_id, ad_file_name_template = ad_file_name_template)
    folder_result = rename_local_ad_folder_after_id_change(file_result.path, new_id = new_id, folder_name_template = folder_name_template)
    return LocalPathRenameResult(
        ad_file = folder_result.path,
        file_status = file_result.status,
        folder_status = folder_result.status,
        path_old_id = path_old_id,
    )
