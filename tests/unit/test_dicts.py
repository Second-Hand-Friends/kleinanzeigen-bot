# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Tests for the dicts utility module."""
from pathlib import Path
from unittest.mock import patch


def test_save_dict_with_unicode_normalization_mismatch(tmp_path:Path) -> None:
    """Test that save_dict handles Unicode normalization mismatches (issue #728).

    This simulates the filesystem behavior where Path.resolve() fails to find
    a directory due to NFC vs NFD Unicode normalization differences.
    """
    import unicodedata  # noqa: PLC0415

    from kleinanzeigen_bot.utils import dicts  # noqa: PLC0415

    # Create a directory using NFC normalization (Normalized Form Composed)
    # This is what sanitize_folder_name() produces
    title_nfc = "KitchenAid Zuhälter - nie benutzt"
    title_nfc = unicodedata.normalize("NFC", title_nfc)
    test_dir = tmp_path / f"ad_12345_{title_nfc}"
    test_dir.mkdir(parents = True)

    # Create the same string but with NFD normalization (Normalized Form Decomposed)
    # On some filesystems (macOS HFS+, Nextcloud), path lookups may convert to NFD
    title_nfd = unicodedata.normalize("NFD", title_nfc)

    # Verify they're different representations
    # NFC: ä is \xc3\xa4 (single composed character)
    # NFD: ä is a\xcc\x88 (base + combining diaeresis)
    assert title_nfc != title_nfd  # Different strings in Python
    assert title_nfc.encode() != title_nfd.encode()  # Different byte representations

    # Construct file path using NFD normalization
    file_path_nfd = tmp_path / f"ad_12345_{title_nfd}" / "ad_12345.yaml"

    # Mock Path.resolve() to simulate filesystem normalization mismatch
    # Without the fix (mkdir before resolve), this would fail
    original_resolve = Path.resolve

    def mock_resolve(self:Path, strict:bool = False) -> Path:
        # Simulate filesystem that can't find NFD path when NFC directory exists
        path_str = str(self)
        if title_nfd in path_str and not self.parent.exists():
            # This simulates the bug: resolve() can't find the parent directory
            # because it's looking for NFD but filesystem has NFC
            raise FileNotFoundError(f"[Errno 2] No such file or directory: '{self}'")
        return original_resolve(self, strict = strict)

    # Test that save_dict handles this correctly
    # The fix (mkdir before resolve) ensures the directory exists in the right form
    test_data = {"test": "data", "title": title_nfc}

    with patch.object(Path, "resolve", mock_resolve):
        # This should NOT raise FileNotFoundError because save_dict calls mkdir first
        dicts.save_dict(str(file_path_nfd), test_data)

    # Verify the file was created successfully
    # On Linux ext4, mkdir creates a new directory with NFD normalization
    # On macOS/Nextcloud, it might normalize to NFC
    # Check both possible locations
    nfc_files = list(test_dir.glob("*.yaml"))
    nfd_dir = tmp_path / f"ad_12345_{title_nfd}"
    nfd_files = list(nfd_dir.glob("*.yaml")) if nfd_dir.exists() else []

    # One of them should have the file
    all_files = nfc_files + nfd_files
    assert len(all_files) == 1, f"Expected 1 file, found {len(all_files)}. NFC dir: {test_dir.exists()}, NFD dir: {nfd_dir.exists()}"
    assert all_files[0].name == "ad_12345.yaml"
