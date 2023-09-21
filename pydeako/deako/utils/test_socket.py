"""
Test the handler for socket connections.
"""

from uuid import uuid4
from mock import AsyncMock, Mock, patch
import pytest

from ._socket import _SocketConnection, NoSocketException


def test_init():
    """Test _SocketConnection.__init__."""
    address = str(uuid4())
    loop_mock = Mock()

    socket_connection = _SocketConnection(address, loop_mock)

    assert socket_connection is not None
    assert socket_connection.address == address
    assert socket_connection.loop == loop_mock
    assert socket_connection.sock is None


@patch("pydeako.deako.utils._socket.socket")
@pytest.mark.asyncio
async def test_connect_socket(socket_mock):
    """Test _SocketConnection.connect_socket."""
    address = str(uuid4())
    port = str(uuid4())
    full_address = f"{address}:{port}"
    loop_mock = AsyncMock()

    socket_connection = _SocketConnection(full_address, loop_mock)

    await socket_connection.connect_socket()

    socket_mock.socket.assert_called_once_with(
        socket_mock.AF_INET, socket_mock.SOCK_STREAM
    )
    socket_mock.socket.return_value.setblocking.assert_called_once_with(False)
    loop_mock.sock_connect.assert_called_once_with(
        socket_mock.socket.return_value, (address, port)
    )


def test_close_socket_no_socket():
    """
    Test _SocketConnection.close_socket when
    the socket hasn't been created.
    """
    loop_mock = AsyncMock()

    socket_connection = _SocketConnection(Mock(), loop_mock)

    socket_connection.close_socket()


def test_close_socket():
    """Test _SocketConnection.close_socket."""
    loop_mock = AsyncMock()
    sock_mock = Mock()

    socket_connection = _SocketConnection(Mock(), loop_mock)

    socket_connection.sock = sock_mock

    socket_connection.close_socket()

    sock_mock.close.assert_called_once()


@pytest.mark.asyncio
async def test_send_bytes_no_socket():
    """
    Test _SocketConnection.send_bytes when
    the socket hasn't been created.
    """
    loop_mock = AsyncMock()

    socket_connection = _SocketConnection(Mock(), loop_mock)

    with pytest.raises(NoSocketException):
        await socket_connection.send_bytes(bytes([]))


@pytest.mark.asyncio
async def test_send_bytes():
    """Test _SocketConnection.send_bytes."""
    loop_mock = AsyncMock()
    sock_mock = Mock()
    bytes_to_send = str.encode(str(uuid4()))

    socket_connection = _SocketConnection(Mock(), loop_mock)
    socket_connection.sock = sock_mock

    await socket_connection.send_bytes(bytes_to_send)

    loop_mock.sock_sendall.assert_called_once_with(sock_mock, bytes_to_send)


@pytest.mark.asyncio
async def test_read_bytes_no_socket():
    """
    Test _SocketConnection.read_bytes when
    the socket hasn't been created.
    """
    loop_mock = AsyncMock()

    socket_connection = _SocketConnection(Mock(), loop_mock)

    with pytest.raises(NoSocketException):
        await socket_connection.read_bytes()


@pytest.mark.asyncio
async def test_read_bytes():
    """Test _SocketConnection.read_bytes."""
    loop_mock = AsyncMock()
    sock_mock = Mock()
    bytes_to_recv = str.encode(str(uuid4()))

    socket_connection = _SocketConnection(Mock(), loop_mock)
    socket_connection.sock = sock_mock
    loop_mock.sock_recv.return_value = bytes_to_recv

    bytes_recvd = await socket_connection.read_bytes()

    loop_mock.sock_recv.assert_called_once_with(sock_mock, 2048)
    assert bytes_recvd == bytes_to_recv
