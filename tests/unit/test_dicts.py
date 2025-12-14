# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Tests for the dicts utility module."""
import unicodedata
from pathlib import Path
from unittest.mock import patch


def test_save_dict_with_unicode_normalization_mismatch(tmp_path:Path) -> None:
    """save_dict should work even when the filesystem normalizes paths differently."""
    from kleinanzeigen_bot.utils import dicts  # noqa: PLC0415

    title_nfc = unicodedata.normalize("NFC", "KitchenAid Zuhälter - nie benutzt")
    title_nfd = unicodedata.normalize("NFD", title_nfc)

    test_dir = tmp_path / f"ad_12345_{title_nfc}"
    test_dir.mkdir(parents = True)

    file_path_nfd = tmp_path / f"ad_12345_{title_nfd}" / "ad_12345.yaml"
    original_resolve = Path.resolve

    def mock_resolve(self:Path, strict:bool = False) -> Path:
        if title_nfd in str(self) and not self.parent.exists():
            raise FileNotFoundError(f"[Errno 2] No such file or directory: '{self}'")
        return original_resolve(self, strict = strict)

    with patch.object(Path, "resolve", mock_resolve):
        dicts.save_dict(str(file_path_nfd), {"test": "data", "title": title_nfc})

    nfc_files = list(test_dir.glob("*.yaml"))
    nfd_dir = tmp_path / f"ad_12345_{title_nfd}"
    nfd_files = list(nfd_dir.glob("*.yaml")) if nfd_dir.exists() else []
    all_files = nfc_files + nfd_files

    assert len(all_files) == 1, f"Expected 1 file, found {len(all_files)}. NFC dir: {test_dir.exists()}, NFD dir: {nfd_dir.exists()}"
    assert all_files[0].name == "ad_12345.yaml"
