# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Tests for the files utility module."""
import os
import tempfile

from kleinanzeigen_bot.utils.files import abspath


class TestFiles:
    """Test suite for files utility functions."""

    def test_abspath_without_relative_to(self) -> None:
        """Test abspath function without relative_to parameter."""
        # Test with a simple path
        result = abspath("test/path")
        assert os.path.isabs(result)
        # Use os.path.normpath to handle path separators correctly on all platforms
        assert os.path.normpath(result).endswith(os.path.normpath("test/path"))

        # Test with an absolute path
        abs_path = os.path.abspath("test/path")
        result = abspath(abs_path)
        assert result == abs_path

    def test_abspath_with_file_reference(self) -> None:
        """Test abspath function with a file as relative_to."""
        with tempfile.NamedTemporaryFile() as temp_file:
            # Test with a relative path
            result = abspath("test/path", temp_file.name)
            expected = os.path.normpath(os.path.join(os.path.dirname(temp_file.name), "test/path"))
            assert result == expected

            # Test with an absolute path
            abs_path = os.path.abspath("test/path")
            result = abspath(abs_path, temp_file.name)
            assert result == abs_path

    def test_abspath_with_directory_reference(self) -> None:
        """Test abspath function with a directory as relative_to."""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Test with a relative path
            result = abspath("test/path", temp_dir)
            expected = os.path.normpath(os.path.join(temp_dir, "test/path"))
            assert result == expected

            # Test with an absolute path
            abs_path = os.path.abspath("test/path")
            result = abspath(abs_path, temp_dir)
            assert result == abs_path

    def test_abspath_with_nonexistent_reference(self) -> None:
        """Test abspath function with a nonexistent file/directory as relative_to."""
        nonexistent_path = "nonexistent/path"

        # Test with a relative path; should still yield an absolute path
        result = abspath("test/path", nonexistent_path)
        expected = os.path.normpath(os.path.join(os.path.abspath(nonexistent_path), "test/path"))
        assert result == expected

        # Test with an absolute path
        abs_path = os.path.abspath("test/path")
        result = abspath(abs_path, nonexistent_path)
        assert result == abs_path

    def test_abspath_with_special_paths(self) -> None:
        """Test abspath function with special path cases."""
        # Test with empty path
        result = abspath("")
        assert os.path.isabs(result)
        assert result == os.path.abspath("")

        # Test with current directory
        result = abspath(".")
        assert os.path.isabs(result)
        assert result == os.path.abspath(".")

        # Test with parent directory
        result = abspath("..")
        assert os.path.isabs(result)
        assert result == os.path.abspath("..")

        # Test with path containing ../
        result = abspath("../test/path")
        assert os.path.isabs(result)
        assert result == os.path.abspath("../test/path")
