# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
"""Tests for the network utilities module.

Covers port availability checking functionality.
"""

import socket
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest

from kleinanzeigen_bot.utils.net import is_port_open

# --------------------------------------------------------------------------- #
# Test fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def mock_socket() -> Generator[MagicMock, None, None]:
    """Create a mock socket for testing."""
    with patch("socket.socket") as mock:
        yield mock


# --------------------------------------------------------------------------- #
# Test cases
# --------------------------------------------------------------------------- #


class TestIsPortOpen:
    """Test port availability checking functionality."""

    def test_port_open(self, mock_socket:MagicMock) -> None:
        """Test when port is open."""
        mock_socket.return_value.connect.return_value = None
        assert is_port_open("localhost", 8080) is True
        mock_socket.return_value.connect.assert_called_once_with(("localhost", 8080))
        mock_socket.return_value.close.assert_called_once()

    def test_port_closed(self, mock_socket:MagicMock) -> None:
        """Test when port is closed."""
        mock_socket.return_value.connect.side_effect = socket.error
        assert is_port_open("localhost", 8080) is False
        mock_socket.return_value.connect.assert_called_once_with(("localhost", 8080))
        mock_socket.return_value.close.assert_called_once()

    def test_connection_timeout(self, mock_socket:MagicMock) -> None:
        """Test when connection times out."""
        mock_socket.return_value.connect.side_effect = socket.timeout
        assert is_port_open("localhost", 8080) is False
        mock_socket.return_value.connect.assert_called_once_with(("localhost", 8080))
        mock_socket.return_value.close.assert_called_once()

    def test_socket_creation_failure(self, mock_socket:MagicMock) -> None:
        """Test when socket creation fails."""
        mock_socket.side_effect = socket.error
        assert is_port_open("localhost", 8080) is False
        mock_socket.assert_called_once()
        # Ensure no close is called since socket creation failed
        mock_socket.return_value.close.assert_not_called()
