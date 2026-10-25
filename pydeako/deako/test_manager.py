"""
Test the SocketConnection manager.
"""

import asyncio
import json

import pytest
from mock import AsyncMock, Mock, patch

from ..discover import DevicesNotFoundException
from ..models import state_change_request
from ._manager import _Manager, CONNECTION_TIMEOUT_S
from ._request import _Request


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
    request_mock.get_transaction_id.return_value = None

    manager = _Manager(AsyncMock(), Mock(), client_name=client_name)

    manager.connection = connection_mock

    assert await manager.send_request(request_mock)

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

    assert not await manager.send_request(request_mock)

    connection_mock.send_data.assert_not_called()


def _device_connection(manager, replies=(), log=None):
    """
    Mock connection that answers requests like a device. replies says how
    to answer each write in turn: "ok", "busy", "malformed" or None for no
    answer; writes past the end of replies are answered "ok".
    """
    replies = list(replies)
    log = [] if log is None else log

    def answer(response):
        log.append(("response", response["transactionId"]))
        manager.incoming_json(response)

    async def send_data(data):
        body = json.loads(data)
        log.append(("write", body["transactionId"]))
        reply = replies.pop(0) if replies else "ok"
        if reply is None:
            return
        response = {
            "transactionId": body["transactionId"],
            "type": body["type"],
            "status": "ok",
        }
        if reply != "ok":
            code = "DEVICE_BUSY" if reply == "busy" else "REQUEST_MALFORMED"
            response["status"] = "error"
            response["data"] = {"code": code}
        asyncio.get_running_loop().call_later(0.01, answer, response)

    connection = AsyncMock()
    connection.send_data.side_effect = send_data
    return connection, log


def _control_requests(count):
    """Build real state change requests for count devices."""
    return [
        _Request(state_change_request(f"uuid {i}", True, None))
        for i in range(count)
    ]


@pytest.mark.asyncio
async def test_send_request_waits_for_response():
    """Test concurrent requests are each sent after the previous response"""
    manager = _Manager(AsyncMock(), Mock(), client_name=Mock())
    manager.connection, log = _device_connection(manager)
    requests = _control_requests(3)

    results = await asyncio.gather(
        *(manager.send_request(req) for req in requests)
    )

    assert results == [True, True, True]
    expected = []
    for req in requests:
        transaction_id = req.get_transaction_id()
        expected += [("write", transaction_id), ("response", transaction_id)]
    assert log == expected
    assert not manager.pending_responses


@patch("pydeako.deako._manager.RESPONSE_TIMEOUT_S", 0.05)
@pytest.mark.asyncio
async def test_send_request_no_response():
    """Test requests the device doesn't answer don't block the next one"""
    manager = _Manager(AsyncMock(), Mock(), client_name=Mock())
    manager.connection, log = _device_connection(
        manager, replies=[None, None],
    )

    results = await asyncio.gather(
        *(manager.send_request(req) for req in _control_requests(2))
    )

    assert results == [True, True]
    assert [event for event, _ in log] == ["write", "write"]
    assert not manager.pending_responses


@patch("pydeako.deako._manager.BUSY_RETRY_DELAY_S", 0.01)
@pytest.mark.asyncio
async def test_send_request_retries_when_busy():
    """Test a request answered with DEVICE_BUSY is sent again"""
    manager = _Manager(AsyncMock(), Mock(), client_name=Mock())
    manager.connection, log = _device_connection(
        manager, replies=["busy", "ok"],
    )
    request = _control_requests(1)[0]

    assert await manager.send_request(request)

    first, resent = (
        json.loads(call.args[0])
        for call in manager.connection.send_data.call_args_list
    )
    first_id = first.pop("transactionId")
    resent_id = resent.pop("transactionId")
    assert first_id == request.get_transaction_id()
    assert resent_id != first_id
    assert resent == first
    assert log == [
        ("write", first_id),
        ("response", first_id),
        ("write", resent_id),
        ("response", resent_id),
    ]


@patch("pydeako.deako._manager.BUSY_RETRY_DELAY_S", 0.01)
@patch("pydeako.deako._manager.MAX_BUSY_RETRIES", 2)
@pytest.mark.asyncio
async def test_send_request_gives_up_when_busy():
    """Test a request is given up after MAX_BUSY_RETRIES busy answers"""
    manager = _Manager(AsyncMock(), Mock(), client_name=Mock())
    manager.connection, log = _device_connection(
        manager, replies=["busy", "busy", "busy", "ok"],
    )

    assert not await manager.send_request(_control_requests(1)[0])

    assert [event for event, _ in log].count("write") == 3


@pytest.mark.asyncio
async def test_send_request_other_error_not_retried():
    """Test a request answered with an error other than busy isn't resent"""
    manager = _Manager(AsyncMock(), Mock(), client_name=Mock())
    manager.connection, log = _device_connection(
        manager, replies=["malformed"],
    )

    assert await manager.send_request(_control_requests(1)[0])

    assert [event for event, _ in log].count("write") == 1


@pytest.mark.asyncio
async def test_incoming_json_resolves_pending_response():
    """Test _Manager.incoming_json hands a response to its waiting request"""
    callback = Mock()
    manager = _Manager(AsyncMock(), callback, client_name=Mock())
    pending = asyncio.get_running_loop().create_future()
    manager.pending_responses["some id"] = pending
    message = {"transactionId": "some id", "type": "CONTROL", "status": "ok"}

    manager.incoming_json(message)

    assert pending.result() == message
    callback.assert_called_once_with(message)
