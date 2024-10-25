"""Tests _Connection."""

import json
from uuid import uuid4
import pytest
from mock import AsyncMock, call, Mock, patch

from ._connection import _Connection, ConnectionState, UnknownStateException
from ._socket import _SocketConnection


@patch("pydeako.deako.utils._connection.asyncio")
@patch(
    "pydeako.deako.utils._connection._SocketConnection",
    spec=_SocketConnection,
)
@patch("pydeako.deako.utils._connection._Connection.init_run")
def test_init(init_run_mock, socket_connection_mock, asyncio_mock):
    """Test _Connection.__init__"""
    address, name = Mock(), Mock()
    loop_mock = AsyncMock()
    asyncio_mock.get_running_loop.return_value = loop_mock

    conn = _Connection(address, name, Mock())

    socket_connection_mock.assert_called_once_with(address, loop_mock)

    assert conn is not None
    asyncio_mock.get_running_loop.assert_called_once()
    assert conn.state == ConnectionState.NOT_STARTED
    init_run_mock.assert_called_once()


@pytest.mark.parametrize("raise_error,", [True, False])
@patch("pydeako.deako.utils._connection.asyncio")
@patch(
    "pydeako.deako.utils._connection._SocketConnection",
    spec=_SocketConnection,
)
@pytest.mark.asyncio
async def test_send_data(
    socket_connection_mock,
    asyncio_mock,
    raise_error,
):
    """Test _Connection.send_data."""
    data = str(uuid4())
    data_bytes = str.encode(data)
    address, name = Mock(), Mock()
    loop_mock = Mock()
    asyncio_mock.get_running_loop.return_value = loop_mock

    conn = _Connection(address, name, Mock())

    socket_connection_mock.assert_called_once_with(address, loop_mock)
    socket_connection_mock_instance = socket_connection_mock.return_value

    if raise_error:
        socket_connection_mock_instance.send_bytes.side_effect = Exception()

    await conn.send_data(data)

    socket_connection_mock_instance.send_bytes.assert_called_once_with(
        data_bytes
    )
    assert (
        conn.state == ConnectionState.ERROR
        if raise_error
        else ConnectionState.NOT_STARTED
    )


@pytest.mark.parametrize("raise_read_error", [True, False])
@patch("pydeako.deako.utils._connection.asyncio")
@patch(
    "pydeako.deako.utils._connection._SocketConnection", spec=_SocketConnection
)
@patch("pydeako.deako.utils._connection._Connection.parse_data")
@pytest.mark.asyncio
async def test_read_socket(
    parse_data_mock, socket_connection_mock, asyncio_mock, raise_read_error
):
    """Test _Connection.read_socket."""
    read_bytes = str.encode(str(uuid4()))
    address, name = Mock(), Mock()
    loop_mock = Mock()
    asyncio_mock.get_running_loop.return_value = loop_mock

    conn = _Connection(address, name, Mock())

    socket_connection_mock.assert_called_once_with(address, loop_mock)
    socket_connection_mock_instance = socket_connection_mock.return_value
    socket_connection_mock_instance.read_bytes.return_value = read_bytes

    if raise_read_error:
        socket_connection_mock_instance.read_bytes.side_effect = Exception()

    await conn.read_socket()

    socket_connection_mock_instance.read_bytes.assert_called_once()
    if not raise_read_error:
        parse_data_mock.assert_called_once_with(read_bytes)
    assert (
        conn.state == ConnectionState.ERROR
        if raise_read_error
        else ConnectionState.NOT_STARTED
    )


@patch("pydeako.deako.utils._connection.asyncio")
@patch(
    "pydeako.deako.utils._connection._SocketConnection", spec=_SocketConnection
)
def test_parse_data_one_item(socket_connection_mock, asyncio_mock):
    """Test _Connection.parse_data, one message."""
    json_message = {"key": "value"}
    one_item = str.encode(json.dumps(json_message))
    address, name = Mock(), Mock()
    loop_mock = Mock()
    on_data_callback = Mock()
    asyncio_mock.get_running_loop.return_value = loop_mock

    conn = _Connection(address, name, on_data_callback)

    socket_connection_mock.assert_called_once_with(address, loop_mock)

    conn.parse_data(one_item)

    on_data_callback.assert_called_once_with(json_message)


@patch("pydeako.deako.utils._connection.asyncio")
@patch(
    "pydeako.deako.utils._connection._SocketConnection", spec=_SocketConnection
)
def test_parse_data_multiple_item(socket_connection_mock, asyncio_mock):
    """Test _Connection.parse_data, multiple messages."""
    json_message = {"key": "value"}
    json_message2 = {"key2": "value2"}

    items = str.encode(
        "\r\n".join(
            [json.dumps(item) for item in [json_message, json_message2]]
        )
    )
    address, name = Mock(), Mock()
    loop_mock = Mock()
    on_data_callback = Mock()
    asyncio_mock.get_running_loop.return_value = loop_mock

    conn = _Connection(address, name, on_data_callback)

    socket_connection_mock.assert_called_once_with(address, loop_mock)

    conn.parse_data(items)

    on_data_callback.assert_has_calls(
        [
            call(json_message),
            call(json_message2),
        ]
    )


@patch("pydeako.deako.utils._connection.asyncio")
@patch(
    "pydeako.deako.utils._connection._SocketConnection", spec=_SocketConnection
)
def test_parse_data_partial(socket_connection_mock, asyncio_mock):
    """Test _Connection.parse_data, one message split up."""
    json_message = {"key": "value"}
    json_message_data = json.dumps(json_message)

    items = str.encode(
        "\r\n".join(
            [
                json_message_data[0: len(json_message_data) // 2],
                json_message_data[len(json_message_data) // 2:],
            ]
        )
    )
    address, name = Mock(), Mock()
    loop_mock = Mock()
    on_data_callback = Mock()
    asyncio_mock.get_running_loop.return_value = loop_mock

    conn = _Connection(address, name, on_data_callback)

    socket_connection_mock.assert_called_once_with(address, loop_mock)

    conn.parse_data(items)

    on_data_callback.assert_has_calls(
        [
            call(json_message),
        ]
    )


@patch("pydeako.deako.utils._connection.asyncio")
@patch(
    "pydeako.deako.utils._connection._SocketConnection",
    spec=_SocketConnection,
)
@patch("pydeako.deako.utils._connection._Connection.run")
def test_init_run(run_mock, socket_connection_mock, asyncio_mock):
    """Test _Connection.__init__"""
    address, name = Mock(), Mock()
    loop_mock = Mock()
    task_mock = Mock()
    asyncio_mock.get_running_loop.return_value = loop_mock

    loop_mock.create_task.return_value = task_mock

    conn = _Connection(address, name, Mock())

    socket_connection_mock.assert_called_once_with(address, loop_mock)

    run_mock.assert_called_once()
    task_mock.add_done_callback.assert_called_once()
    assert task_mock in conn.tasks


@patch("pydeako.deako.utils._connection.asyncio")
@patch(
    "pydeako.deako.utils._connection._SocketConnection", spec=_SocketConnection
)
def test_close(socket_connection_mock, asyncio_mock):
    """Test _Connection.close."""
    address, name = Mock(), Mock()
    loop_mock = Mock()
    asyncio_mock.get_running_loop.return_value = loop_mock

    conn = _Connection(address, name, Mock())

    socket_connection_mock.assert_called_once_with(address, loop_mock)

    task1 = Mock()
    task2 = Mock()

    conn.tasks = set([task1, task2])

    conn.close()

    socket_connection_mock.return_value.close_socket.assert_called_once()
    task1.cancel.assert_called_once()
    task2.cancel.assert_called_once()


@pytest.mark.parametrize(
    "state,expected",
    [
        (ConnectionState.CONNECTED, True),
        (ConnectionState.CLOSED, False),
        (ConnectionState.ERROR, False),
        (ConnectionState.NOT_STARTED, False),
    ],
)
@patch("pydeako.deako.utils._connection.asyncio")
@patch(
    "pydeako.deako.utils._connection._SocketConnection", spec=_SocketConnection
)
def test_is_connected(socket_connection_mock, asyncio_mock, state, expected):
    """Test _Connection.is_connected."""
    address, name = Mock(), Mock()
    loop_mock = Mock()
    asyncio_mock.get_running_loop.return_value = loop_mock

    conn = _Connection(address, name, Mock())

    socket_connection_mock.assert_called_once_with(address, loop_mock)

    conn.state = state

    assert conn.is_connected() == expected


@patch("pydeako.deako.utils._connection.asyncio")
@patch(
    "pydeako.deako.utils._connection._SocketConnection", spec=_SocketConnection
)
@pytest.mark.asyncio
async def test_state_machine_connect_failure(
    socket_connection_mock, asyncio_mock
):
    """Test _Connection.run, connect exception calls close."""
    address, name = Mock(), Mock()
    loop_mock = Mock()
    asyncio_mock.get_running_loop.return_value = loop_mock

    conn = _Connection(address, name, Mock())

    socket_connection_mock.assert_called_once_with(address, loop_mock)

    socket_connection_instance = socket_connection_mock.return_value

    socket_connection_instance.connect_socket.side_effect = Exception()

    await conn.run()

    socket_connection_instance.close_socket.assert_called_once()


@patch("pydeako.deako.utils._connection.asyncio")
@patch(
    "pydeako.deako.utils._connection._SocketConnection", spec=_SocketConnection
)
@pytest.mark.asyncio
async def test_state_machine_read_failure(
    socket_connection_mock, asyncio_mock
):
    """Test _Connection.run, read exception calls close."""
    address, name = Mock(), Mock()
    loop_mock = Mock()
    asyncio_mock.get_running_loop.return_value = loop_mock

    conn = _Connection(address, name, Mock())
    conn.state = ConnectionState.CONNECTED

    socket_connection_mock.assert_called_once_with(address, loop_mock)

    socket_connection_instance = socket_connection_mock.return_value

    socket_connection_instance.read_bytes.side_effect = Exception()

    await conn.run()

    socket_connection_instance.close_socket.assert_called_once()


@patch("pydeako.deako.utils._connection.asyncio")
@patch(
    "pydeako.deako.utils._connection._SocketConnection", spec=_SocketConnection
)
@pytest.mark.asyncio
async def test_state_machine_close_failure(
    socket_connection_mock, asyncio_mock
):
    """Test _Connection.run, error calling close."""
    address, name = Mock(), Mock()
    loop_mock = Mock()
    asyncio_mock.get_running_loop.return_value = loop_mock

    conn = _Connection(address, name, Mock())
    conn.state = ConnectionState.ERROR

    socket_connection_mock.assert_called_once_with(address, loop_mock)

    socket_connection_instance = socket_connection_mock.return_value

    socket_connection_instance.close_socket.side_effect = Exception()

    await conn.run()

    socket_connection_instance.close_socket.assert_called_once()


@patch("pydeako.deako.utils._connection.asyncio")
@patch(
    "pydeako.deako.utils._connection._SocketConnection", spec=_SocketConnection
)
@pytest.mark.asyncio
async def test_state_machine_closed(socket_connection_mock, asyncio_mock):
    """Test _Connection.run, error state results in closing."""
    address, name = Mock(), Mock()
    loop_mock = Mock()
    asyncio_mock.get_running_loop.return_value = loop_mock

    conn = _Connection(address, name, Mock())
    conn.state = ConnectionState.ERROR

    socket_connection_mock.assert_called_once_with(address, loop_mock)

    await conn.run()

    assert conn.state == ConnectionState.CLOSED


@patch("pydeako.deako.utils._connection.asyncio")
@patch(
    "pydeako.deako.utils._connection._SocketConnection", spec=_SocketConnection
)
@pytest.mark.asyncio
async def test_state_machine_unknown_state_raises(
    socket_connection_mock, asyncio_mock
):
    """Test _Connection.run, unknown state raises."""
    address, name = Mock(), Mock()
    loop_mock = Mock()
    asyncio_mock.get_running_loop.return_value = loop_mock

    conn = _Connection(address, name, Mock())
    conn.state = ConnectionState.UNKNOWN

    socket_connection_mock.assert_called_once_with(address, loop_mock)

    with pytest.raises(UnknownStateException):
        await conn.run()
