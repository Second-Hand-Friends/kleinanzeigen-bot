# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Tests for the dicts utility module."""
import unicodedata
from pathlib import Path


def test_save_dict_normalizes_unicode_paths(tmp_path:Path) -> None:
    """Test that save_dict normalizes paths to NFC, preventing duplicate directories (issue #728).

    When a directory is created with NFC normalization (e.g., "ä" as single character),
    but save_dict is called with an NFD path (e.g., "ä" as "a" + combining diacritic),
    it should normalize to NFC and use the existing directory instead of creating a duplicate.
    """
    from kleinanzeigen_bot.utils import dicts  # noqa: PLC0415

    # Create directory with NFC normalization (as sanitize_folder_name does)
    title_nfc = unicodedata.normalize("NFC", "KitchenAid Zuhälter - nie benutzt")
    nfc_dir = tmp_path / f"ad_12345_{title_nfc}"
    nfc_dir.mkdir(parents = True)

    # Call save_dict with NFD path (different normalization)
    title_nfd = unicodedata.normalize("NFD", title_nfc)
    assert title_nfc != title_nfd, "NFC and NFD should be different strings"

    nfd_path = tmp_path / f"ad_12345_{title_nfd}" / "ad_12345.yaml"
    dicts.save_dict(str(nfd_path), {"test": "data", "title": title_nfc})

    # Verify file was saved successfully
    nfc_files = list(nfc_dir.glob("*.yaml"))
    assert len(nfc_files) == 1, "Should have exactly one file in NFC directory"
    assert nfc_files[0].name == "ad_12345.yaml"

    # On macOS/APFS, the filesystem normalizes both NFC and NFD to the same directory
    # On Linux ext4, NFC normalization in save_dict ensures it uses the existing directory
    # Either way, we should have exactly one YAML file total (no duplicates)
    all_yaml_files = list(tmp_path.rglob("*.yaml"))
    assert len(all_yaml_files) == 1, f"Expected exactly 1 YAML file total, found {len(all_yaml_files)}: {all_yaml_files}"
