"""
SPDX-FileCopyrightText: © Jens Bergmann and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

This module contains tests for the net.py utility module.
"""
import socket
from unittest.mock import MagicMock, patch

from kleinanzeigen_bot.utils import net


def test_is_port_open_success() -> None:
    """Test is_port_open when the port is open."""
    # Mock socket to simulate a successful connection
    mock_socket = MagicMock()
    mock_socket.connect.return_value = None

    with patch('socket.socket', return_value=mock_socket):
        assert net.is_port_open('localhost', 8080) is True
        mock_socket.connect.assert_called_once_with(('localhost', 8080))
        mock_socket.close.assert_called_once()


def test_is_port_open_failure() -> None:
    """Test is_port_open when the port is closed."""
    # Mock socket to simulate a failed connection
    mock_socket = MagicMock()
    mock_socket.connect.side_effect = socket.error("Connection refused")

    with patch('socket.socket', return_value=mock_socket):
        assert net.is_port_open('localhost', 8080) is False
        mock_socket.connect.assert_called_once_with(('localhost', 8080))
        mock_socket.close.assert_called_once()


def test_is_port_open_timeout() -> None:
    """Test is_port_open when the connection times out."""
    # Mock socket to simulate a timeout
    mock_socket = MagicMock()
    mock_socket.connect.side_effect = socket.timeout("Connection timed out")

    with patch('socket.socket', return_value=mock_socket):
        assert net.is_port_open('localhost', 8080) is False
        mock_socket.connect.assert_called_once_with(('localhost', 8080))
        mock_socket.close.assert_called_once()


def test_is_port_open_other_exception() -> None:
    """Test is_port_open when another exception occurs."""
    # Mock socket to simulate another exception
    mock_socket = MagicMock()
    mock_socket.connect.side_effect = Exception("Unexpected error")

    with patch('socket.socket', return_value=mock_socket):
        assert net.is_port_open('localhost', 8080) is False
        mock_socket.connect.assert_called_once_with(('localhost', 8080))
        mock_socket.close.assert_called_once()


def test_is_port_open_socket_creation_failure() -> None:
    """Test is_port_open when socket creation fails."""
    # Mock socket.socket to raise an exception
    with patch('socket.socket', side_effect=Exception("Failed to create socket")):
        assert net.is_port_open('localhost', 8080) is False
