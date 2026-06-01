# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
from pathlib import Path

import pytest

from kleinanzeigen_bot.local_path_renaming import (
    DOWNLOAD_IMAGE_FILENAME_RE,
    ImageRenameResult,
    LocalPathRenameResult,
    RenamePathResult,
    RenameStatus,
    rename_local_ad_file_after_id_change,
    rename_local_ad_folder_after_id_change,
    rename_path_if_target_is_free,
    rename_referenced_local_image_file_after_id_change,
    rename_referenced_local_image_files_after_id_change,
    replace_template_id_slot,
)


@pytest.mark.parametrize(
    ("template", "name", "new_id", "expected_name", "expected_old_id"),
    [
        ("ad_{id}", "ad_123", 456, "ad_456", 123),
        ("ad_{id}_{title}", "ad_123_User edited title", 789, "ad_789_User edited title", 123),
        ("{title} ({id})", "User edited title (123)", 456, "User edited title (456)", 123),
        ("{id}", "123", 456, "456", 123),
        ("{title}{id}", "Bike123", 456, "Bike456", 123),
        ("{title}{id}", "123", 456, "456", 123),
        ("{id}{title}", "123Bike", 456, "456Bike", 123),
    ],
)
def test_replace_template_id_slot(template:str, name:str, new_id:int, expected_name:str, expected_old_id:int) -> None:
    result = replace_template_id_slot(template, name, new_id)
    assert result == (expected_name, expected_old_id)


def test_replace_template_id_slot_skips_non_matching_name() -> None:
    assert replace_template_id_slot("ad_{id}_{title}", "manual_123_Title", 456)[0] is None


def test_rename_path_if_target_is_free_treats_broken_symlink_as_collision(tmp_path:Path) -> None:
    source = tmp_path / "source.txt"
    target = tmp_path / "target.txt"
    source.write_text("source", encoding = "utf-8")
    try:
        target.symlink_to(tmp_path / "missing.txt")
    except (OSError, NotImplementedError):
        pytest.skip("symlink not supported on this platform")

    result = rename_path_if_target_is_free(source, target, label = "test file")

    assert result.path == source
    assert result.status == RenameStatus.TARGET_EXISTS
    assert source.exists()
    assert target.is_symlink()


def test_download_image_filename_re_matches_image_suffix() -> None:
    """DOWNLOAD_IMAGE_FILENAME_RE matches filenames with __imgN suffix."""
    assert DOWNLOAD_IMAGE_FILENAME_RE.fullmatch("ad_123__img1.jpeg") is not None
    assert DOWNLOAD_IMAGE_FILENAME_RE.fullmatch("ad_123__img42.png") is not None
    assert DOWNLOAD_IMAGE_FILENAME_RE.fullmatch("manual_123.txt") is None
    assert DOWNLOAD_IMAGE_FILENAME_RE.fullmatch("ad_123.jpeg") is None


def test_public_api_types_are_exported() -> None:
    """All result types are importable from kleinanzeigen_bot.local_path_renaming."""
    assert RenameStatus.RENAMED is not None
    assert RenamePathResult is not None
    assert LocalPathRenameResult is not None
    assert ImageRenameResult is not None


def test_rename_local_ad_file_change_renames_matching_file(tmp_path:Path) -> None:
    folder = tmp_path / "ad_123_Title"
    folder.mkdir()
    ad_file = folder / "ad_123.yaml"
    ad_file.write_text("id: 456\n", encoding = "utf-8")

    result = rename_local_ad_file_after_id_change(ad_file, new_id = 456, ad_file_name_template = "ad_{id}")

    assert result.status == RenameStatus.RENAMED
    assert result.path == folder / "ad_456.yaml"
    assert result.path.exists()
    assert not ad_file.exists()


def test_rename_local_ad_file_change_skips_when_id_already_matches(tmp_path:Path) -> None:
    folder = tmp_path / "ad_456_Title"
    folder.mkdir()
    ad_file = folder / "ad_456.yaml"
    ad_file.write_text("id: 456\n", encoding = "utf-8")

    result = rename_local_ad_file_after_id_change(ad_file, new_id = 456, ad_file_name_template = "ad_{id}")

    assert result.status == RenameStatus.SAME
    assert result.path == ad_file
    assert ad_file.exists()


def test_rename_local_ad_file_change_skips_non_template_name(tmp_path:Path) -> None:
    folder = tmp_path / "manual_123"
    folder.mkdir()
    ad_file = folder / "manual_123.yaml"
    ad_file.write_text("id: 456\n", encoding = "utf-8")

    result = rename_local_ad_file_after_id_change(ad_file, new_id = 456, ad_file_name_template = "ad_{id}")

    assert result.status == RenameStatus.NO_MATCH
    assert result.path == ad_file
    assert ad_file.exists()


def test_rename_local_ad_folder_change_renames_matching_folder(tmp_path:Path) -> None:
    folder = tmp_path / "ad_123_Title"
    folder.mkdir()
    ad_file = folder / "ad_123.yaml"
    ad_file.write_text("id: 456\n", encoding = "utf-8")

    result = rename_local_ad_folder_after_id_change(ad_file, new_id = 456, folder_name_template = "ad_{id}_{title}")

    assert result.status == RenameStatus.RENAMED
    assert result.path == tmp_path / "ad_456_Title" / "ad_123.yaml"
    assert result.path.parent.exists()
    assert not folder.exists()


def test_rename_local_ad_folder_change_skips_when_id_already_matches(tmp_path:Path) -> None:
    folder = tmp_path / "ad_456_Title"
    folder.mkdir()
    ad_file = folder / "ad_123.yaml"
    ad_file.write_text("id: 456\n", encoding = "utf-8")

    result = rename_local_ad_folder_after_id_change(ad_file, new_id = 456, folder_name_template = "ad_{id}_{title}")

    assert result.status == RenameStatus.SAME
    assert result.path == ad_file
    assert folder.exists()


def test_rename_referenced_local_image_files_updates_config_paths(tmp_path:Path) -> None:
    folder = tmp_path / "ad_123_Title"
    nested = folder / "nested"
    nested.mkdir(parents = True)
    ad_file = folder / "ad_123.yaml"
    (folder / "ad_123__img1.jpeg").write_bytes(b"img1")
    (nested / "ad_123__img2.png").write_bytes(b"img2")
    (folder / "manual_123__img3.jpeg").write_bytes(b"manual")
    images:list[object] = ["ad_123__img1.jpeg", "nested/ad_123__img2.png", "manual_123__img3.jpeg"]

    image_result = rename_referenced_local_image_files_after_id_change(
        ad_file,
        images,
        old_id = 123,
        new_id = 456,
        ad_file_name_template = "ad_{id}",
        enabled = True,
    )

    assert image_result.updated_images == ["ad_456__img1.jpeg", "nested/ad_456__img2.png", "manual_123__img3.jpeg"]
    assert (folder / "ad_456__img1.jpeg").exists()
    assert (nested / "ad_456__img2.png").exists()
    assert (folder / "manual_123__img3.jpeg").exists()
    assert image_result.renamed_count == 2
    assert image_result.blocked_count == 0


def test_rename_referenced_local_image_files_ignores_unreferenced_images(tmp_path:Path) -> None:
    folder = tmp_path / "ad_123_Title"
    folder.mkdir()
    ad_file = folder / "ad_123.yaml"
    (folder / "ad_123__img1.jpeg").write_bytes(b"referenced")
    (folder / "ad_123__img2.jpeg").write_bytes(b"unreferenced")
    images:list[object] = ["ad_123__img1.jpeg"]

    image_result = rename_referenced_local_image_files_after_id_change(
        ad_file,
        images,
        old_id = 123,
        new_id = 456,
        ad_file_name_template = "ad_{id}",
        enabled = True,
    )

    assert image_result.updated_images == ["ad_456__img1.jpeg"]
    assert (folder / "ad_456__img1.jpeg").exists()
    assert (folder / "ad_123__img2.jpeg").exists()
    assert image_result.renamed_count == 1
    assert image_result.blocked_count == 0


@pytest.mark.parametrize(
    "scenario",
    ["target_exists", "absolute_path", "outside_ad_folder"],
)
def test_rename_referenced_local_image_files_skips_when_unsafe(
    tmp_path:Path,
    scenario:str,
) -> None:
    """Image renames that could affect non-owned files are safely skipped."""
    folder = tmp_path / "ad_123_Title"
    folder.mkdir()
    ad_file = folder / "ad_123.yaml"

    source_file:Path | None = None
    target_file:Path | None = None
    if scenario == "target_exists":
        (folder / "ad_123__img1.jpeg").write_bytes(b"old")
        (folder / "ad_456__img1.jpeg").write_bytes(b"existing")
        images_config = ["ad_123__img1.jpeg"]
        source_file = folder / "ad_123__img1.jpeg"
        target_file = folder / "ad_456__img1.jpeg"
    elif scenario == "absolute_path":
        img = tmp_path / "ad_123__img1.jpeg"
        img.write_bytes(b"img")
        images_config = [str(img)]
        source_file = img
    elif scenario == "outside_ad_folder":
        img = tmp_path / "other" / "ad_123__img1.jpeg"
        img.parent.mkdir(parents = True)
        img.write_bytes(b"img")
        images_config = ["../other/ad_123__img1.jpeg"]
        source_file = img
    else:
        raise AssertionError(f"Unknown scenario: {scenario}")

    image_result = rename_referenced_local_image_files_after_id_change(
        ad_file,
        images_config,
        old_id = 123,
        new_id = 456,
        ad_file_name_template = "ad_{id}",
        enabled = True,
    )

    assert image_result.updated_images is None
    assert source_file is not None
    assert source_file.exists()
    if target_file is not None:
        assert target_file.exists()
    assert image_result.renamed_count == 0
    if scenario == "target_exists":
        assert image_result.blocked_count == 1
    else:
        assert image_result.blocked_count == 0


def test_rename_referenced_local_image_files_disabled_returns_empty(tmp_path:Path) -> None:
    """enabled=False is an immediate no-op."""
    folder = tmp_path / "ad_123_Title"
    folder.mkdir()
    (folder / "ad_123__img1.jpeg").write_bytes(b"img")
    images = ["ad_123__img1.jpeg"]

    result = rename_referenced_local_image_files_after_id_change(
        folder / "ad_123.yaml",
        images,
        old_id = 123,
        new_id = 456,
        ad_file_name_template = "ad_{id}",
        enabled = False,
    )

    assert result.renamed_count == 0
    assert result.blocked_count == 0
    assert result.updated_images is None


def test_rename_referenced_local_image_files_old_id_none_returns_empty(tmp_path:Path) -> None:
    """old_id=None is an immediate no-op."""
    folder = tmp_path / "ad_123_Title"
    folder.mkdir()
    (folder / "ad_123__img1.jpeg").write_bytes(b"img")
    images = ["ad_123__img1.jpeg"]

    result = rename_referenced_local_image_files_after_id_change(
        folder / "ad_123.yaml",
        images,
        old_id = None,
        new_id = 456,
        ad_file_name_template = "ad_{id}",
        enabled = True,
    )

    assert result.renamed_count == 0
    assert result.blocked_count == 0
    assert result.updated_images is None


def test_rename_path_if_target_is_free_skips_when_source_equals_target(tmp_path:Path) -> None:
    source = tmp_path / "file.txt"
    source.write_text("data", encoding = "utf-8")

    result = rename_path_if_target_is_free(source, source, label = "test")

    assert result.status == RenameStatus.SAME
    assert result.path == source


def test_rename_referenced_local_image_file_skips_non_string_ref(tmp_path:Path) -> None:
    folder = tmp_path / "ad_123_Title"
    folder.mkdir()
    ad_file = folder / "ad_123.yaml"

    result, status = rename_referenced_local_image_file_after_id_change(
        ad_file, 42, new_id = 456, ad_file_name_template = "ad_{id}",
    )

    assert result == 42
    assert status is None


def test_rename_referenced_local_image_file_skips_non_matching_filename(tmp_path:Path) -> None:
    folder = tmp_path / "ad_123_Title"
    folder.mkdir()
    ad_file = folder / "ad_123.yaml"
    (folder / "manual.txt").write_bytes(b"data")

    result, status = rename_referenced_local_image_file_after_id_change(
        ad_file, "manual.txt", new_id = 456, ad_file_name_template = "ad_{id}",
    )

    assert result == "manual.txt"
    assert status is None


def test_rename_referenced_local_image_files_skips_non_list_images(tmp_path:Path) -> None:
    folder = tmp_path / "ad_123_Title"
    folder.mkdir()

    result = rename_referenced_local_image_files_after_id_change(
        folder / "ad_123.yaml",
        "not_a_list",
        old_id = 123,
        new_id = 456,
        ad_file_name_template = "ad_{id}",
        enabled = True,
    )

    assert result.renamed_count == 0
    assert result.blocked_count == 0
    assert result.updated_images is None


def test_rename_local_ad_folder_change_skips_when_target_exists(tmp_path:Path) -> None:
    folder = tmp_path / "ad_123_Title"
    folder.mkdir()
    ad_file = folder / "ad_123.yaml"
    ad_file.write_text("id: 456\n", encoding = "utf-8")
    (tmp_path / "ad_456_Title").mkdir()

    result = rename_local_ad_folder_after_id_change(ad_file, new_id = 456, folder_name_template = "ad_{id}_{title}")

    assert result.status == RenameStatus.TARGET_EXISTS
    assert result.path == ad_file
    assert folder.exists()
