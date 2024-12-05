"""
Test the SocketConnection manager.
"""

import pytest
from mock import AsyncMock, Mock, patch

from ..discover import DevicesNotFoundException
from ._manager import _Manager, CONNECTION_TIMEOUT_S


def test_init():
    """Test _Manager.__init__"""
    manager = _Manager(AsyncMock(), Mock())

    assert manager is not None


@patch("pydeako.deako._manager._Manager.create_connection_task")
@patch("pydeako.deako._manager.asyncio")
@pytest.mark.asyncio
async def test_init_connection_already_started(
    asyncio_mock,
    create_connection_mock,
):
    """
    Test _Manager.init_connection when the connection
    sequence has already been initiated.
    """
    get_address = AsyncMock()

    manager = _Manager(get_address, Mock())
    manager.state.connecting = True

    await manager.init_connection()

    asyncio_mock.create_task.assert_not_called()

    create_connection_mock.assert_not_called()


@patch("pydeako.deako._manager._Manager.create_connection_task")
@pytest.mark.asyncio
async def test_init_connection_get_address_no_devices(
    create_connection_mock,
):
    """
    Test _Manager.init_connection with no devices found
    which restarts the connection. If we have an address,
    devices should be found.
    """
    get_address = AsyncMock()

    manager = _Manager(get_address, Mock())

    get_address.side_effect = DevicesNotFoundException()

    await manager.init_connection()

    create_connection_mock.assert_called_once()

    assert manager.state.connecting is False


@patch("pydeako.deako._manager._Manager.create_connection_task")
@patch("pydeako.deako._manager._Connection")
@patch("pydeako.deako._manager.asyncio", autospec=True)
@pytest.mark.asyncio
async def test_init_connection_timeout_connecting(
    asyncio_mock,
    connection_mock,
    create_connection_mock,
):
    """Test _Manager.init_connection with timeout."""
    address, name = Mock(), Mock()
    get_address = AsyncMock()

    manager = _Manager(get_address, Mock())

    get_address.return_value = address, name
    connection_mock_instance = connection_mock.return_value
    connection_mock_instance.is_connected.return_value = False

    await manager.init_connection()

    connection_mock.assert_called_once_with(
        address,
        name,
        manager.incoming_json,
    )

    assert asyncio_mock.sleep.call_count == CONNECTION_TIMEOUT_S

    create_connection_mock.assert_called_once()  # this is the retry


@patch("pydeako.deako._manager._Manager.maintain_connection_worker")
@patch("pydeako.deako._manager._Connection")
@patch("pydeako.deako._manager.asyncio", autospec=True)
@pytest.mark.asyncio
async def test_init_connection(
    asyncio_mock,
    connection_mock,
    maintain_connection_worker_mock,
):
    """Test _Manager.init_connection, success"""
    address, name = Mock(), Mock()
    get_address = AsyncMock()

    manager = _Manager(get_address, Mock())

    get_address.return_value = address, name
    connection_mock_instance = connection_mock.return_value
    connection_mock_instance.is_connected.return_value = True

    await manager.init_connection()

    assert asyncio_mock.create_task.call_count == 1
    maintain_connection_worker_mock.assert_called_once()

    connection_mock.assert_called_once_with(
        address,
        name,
        manager.incoming_json,
    )

    assert not manager.state.connecting


def test_close():
    """Test _Manager.close."""
    worker = Mock()
    maintain_worker = Mock()
    connection = Mock()

    manager = _Manager(AsyncMock(), Mock())
    manager.worker = worker
    manager.maintain_worker = maintain_worker
    manager.connection = connection

    manager.close()

    worker.cancel.assert_called_once()
    maintain_worker.cancel.assert_called_once()
    connection.close.assert_called_once()

    assert manager.state.canceled


@patch("pydeako.deako._manager.asyncio")
@patch("pydeako.deako._manager._Manager.init_connection")
def test_create_connection_task(init_connection_mock, asyncio_mock):
    """Test _Manager.create_connection_task."""
    manager = _Manager(AsyncMock(), Mock())

    manager.create_connection_task()

    asyncio_mock.create_task.assert_called_once()
    task = asyncio_mock.create_task.return_value
    task.add_done_callback.assert_called_once()
    init_connection_mock.assert_called_once()
    assert len(manager.tasks) == 1


@patch("pydeako.deako._manager._Manager.close")
@patch("pydeako.deako._manager._Manager.create_connection_task")
@patch("pydeako.deako._manager.asyncio", autospec=True)
@pytest.mark.asyncio
async def test_maintain_connection_worker_canceled(
    asyncio_mock, create_connection_mock, close_mock
):
    """
    Test _Manager.maintain_connection_worker
    doesn't proceed when canceled.
    """
    manager = _Manager(AsyncMock(), Mock())
    manager.state.canceled = True

    await manager.maintain_connection_worker()

    asyncio_mock.sleep.assert_called_once_with(10)
    close_mock.assert_not_called()
    create_connection_mock.assert_not_called()


@patch("pydeako.deako._manager._Manager.close")
@patch("pydeako.deako._manager._Manager.create_connection_task")
@patch("pydeako.deako._manager.asyncio", autospec=True)
@pytest.mark.asyncio
async def test_maintain_connection_worker_no_pong(
    asyncio_mock, create_connection_mock, close_mock
):
    """Test _Manager.maintain_connection_worker doesn't receive pong."""
    manager = _Manager(AsyncMock(), Mock())

    await manager.maintain_connection_worker()

    assert len(asyncio_mock.sleep.mock_calls) == 2
    assert asyncio_mock.sleep.mock_calls[0].args[0] == 10
    assert asyncio_mock.sleep.mock_calls[1].args[0] == 10
    close_mock.assert_called_once()
    create_connection_mock.assert_called_once()


def test_incoming_json_pong():
    """Test _Manager.incoming_json with ping response."""
    incoming_json = {"type": "PING"}

    incoming_json_callback = Mock()

    manager = _Manager(AsyncMock(), incoming_json_callback)
    manager.pong_received = False

    manager.incoming_json(incoming_json)

    assert manager.pong_received


def test_incoming_json():
    """Test _Manager.incoming_json."""
    incoming_json = {"key": "value"}
    incoming_json_callback = Mock()

    manager = _Manager(AsyncMock(), incoming_json_callback)
    manager.pong_received = False

    manager.incoming_json(incoming_json)

    incoming_json_callback.assert_called_once_with(incoming_json)
    assert manager.pong_received is False


@patch("pydeako.deako._manager._Request")
@patch("pydeako.deako._manager.device_list_request")
@pytest.mark.asyncio
async def test_send_get_device_list(device_list_request_mock, request_mock):
    """Test _Manager.send_get_device_list."""
    client_name = Mock()
    request_mock_ret = Mock()

    request_mock.return_value = request_mock_ret

    manager = _Manager(AsyncMock(), Mock(), client_name=client_name)

    # Test with connection
    manager.connection = AsyncMock()
    result = await manager.send_get_device_list()
    device_list_request_mock.assert_called_once_with(source=client_name)
    request_mock.assert_called_once_with(device_list_request_mock.return_value)
    assert result is True

    # Test without connection
    manager.connection = None
    result = await manager.send_get_device_list()
    assert result is False


@pytest.mark.parametrize("completed_callback", [None, "some_callback"])
@patch("pydeako.deako._manager._Request")
@patch("pydeako.deako._manager.state_change_request")
@patch("pydeako.deako._manager._Manager.send_request")
@pytest.mark.asyncio
async def test_send_state_change(
    send_request_mock, state_change_request_mock, request_mock,
    completed_callback,
):
    """Test _Manager.send_state_change."""
    client_name = Mock()
    uuid = Mock()
    power = Mock()
    dim = Mock()
    request_mock_ret = Mock()

    request_mock.return_value = request_mock_ret

    manager = _Manager(AsyncMock(), Mock(), client_name=client_name)

    await manager.send_state_change(
        uuid, power, dim, completed_callback=completed_callback
    )

    request_mock.assert_called_once_with(
        state_change_request_mock.return_value,
        completed_callback=completed_callback,
    )
    state_change_request_mock.assert_called_once_with(
        uuid, power, dim, source=client_name
    )
    send_request_mock.assert_called_once_with(request_mock_ret)


@patch("pydeako.deako._manager._Request")
@pytest.mark.asyncio
async def test_send_request(
    request_mock,
):
    """Test _Manager.send_request with connection"""
    client_name = Mock()
    connection_mock = AsyncMock()

    request_mock.get_body_str.return_value = "some message"

    manager = _Manager(AsyncMock(), Mock(), client_name=client_name)

    manager.connection = connection_mock

    await manager.send_request(request_mock)

    connection_mock.send_data.assert_called_once_with("some message")


@patch("pydeako.deako._manager._Request")
@pytest.mark.asyncio
async def test_send_request_no_connection(
    request_mock,
):
    """Test _Manager.send_request without connection"""
    client_name = Mock()
    connection_mock = AsyncMock()

    request_mock.get_body_str.return_value = "some message"

    manager = _Manager(AsyncMock(), Mock(), client_name=client_name)

    await manager.send_request(request_mock)

    connection_mock.send_data.assert_not_called()
