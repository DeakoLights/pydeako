"""Test Deako"""

import logging
from uuid import uuid4
import json
import pytest
from mock import ANY, AsyncMock, Mock, patch

from ._deako import Deako, FindDevicesError
from .utils._socket import NoSocketException


@patch("pydeako.deako._deako._Manager")
def test_init(manager_mock):
    """Test Deako.__init__."""
    get_address_mock = Mock()
    client_name = Mock()

    deako = Deako(get_address_mock, client_name=client_name)

    manager_mock.assert_called_once_with(
        get_address_mock,
        deako.incoming_json,
        client_name=client_name,
        on_connection_lost=None,
    )
    assert deako is not None


@patch("pydeako.deako._deako._Manager")
def test_on_connection_lost_forwarded_to_manager(manager_mock):
    """Deako forwards on_connection_lost kwarg to the _Manager."""
    get_address_mock = Mock()
    client_name = Mock()
    callback = Mock()

    Deako(
        get_address_mock,
        client_name=client_name,
        on_connection_lost=callback,
    )

    manager_mock.assert_called_once_with(
        get_address_mock,
        ANY,  # incoming_json bound method
        client_name=client_name,
        on_connection_lost=callback,
    )


def test_update_state_uuid_not_found():
    """Test Deako.update_state, device id not found."""
    deako = Deako(Mock())
    pre_update_state_devices = json.dumps(deako.devices)

    deako.update_state(str(uuid4()), False)

    # expect no changes
    assert pre_update_state_devices == json.dumps(deako.devices)


def test_update_state_no_callback():
    """Test Deako.update_state with no callback."""
    device_id = str(uuid4())

    deako = Deako(Mock())
    deako.devices[device_id] = {
        "state": {
            "power": False,
            "dim": 69,
        },
    }

    deako.update_state(device_id, True, 42)

    assert deako.devices[device_id] == {
        "state": {
            "power": True,
            "dim": 42,
        },
    }


def test_update_state_no_callback_no_dim():
    """
    Test Deako.update_state with no callback,
    no dim for a device that has dim.
    """
    device_id = str(uuid4())

    deako = Deako(Mock())
    deako.devices[device_id] = {
        "state": {
            "power": False,
            "dim": 69,
        },
    }

    deako.update_state(device_id, True)

    assert deako.devices[device_id] == {
        "state": {
            "power": True,
            "dim": 69,
        },
    }


def test_update_state_with_callback():
    """Test Deako.update_state with callback."""
    device_id = str(uuid4())
    callback = Mock()

    deako = Deako(Mock())
    deako.devices[device_id] = {
        "state": {
            "power": False,
            "dim": 69,
        },
        "callback": callback,
    }

    deako.update_state(device_id, True, 42)

    assert deako.devices[device_id] == {
        "state": {
            "power": True,
            "dim": 42,
        },
        "callback": callback,
    }
    callback.assert_called_once()


def test_set_state_callback_no_device():
    """Test Deako.set_state_callback with device id not found."""
    deako = Deako(Mock())
    pre_update_state_devices = json.dumps(deako.devices)

    callback = Mock()

    deako.set_state_callback(str(uuid4()), callback)

    # expect no changes
    assert pre_update_state_devices == json.dumps(deako.devices)


def test_set_state_callback():
    """Test Deako.set_state_callback."""
    device_id = str(uuid4())
    deako = Deako(Mock())
    deako.devices = {device_id: {}}

    callback = Mock()

    deako.set_state_callback(device_id, callback)

    assert deako.devices[device_id] == {
        "callback": callback,
    }


def test_incoming_json_device_list():
    """Test Deako.incoming_json with device list."""
    deako = Deako(Mock())

    incoming = {
        "type": "DEVICE_LIST",
        "data": {"number_of_devices": 42},
    }

    deako.incoming_json(incoming)

    assert deako.expected_devices == 42


@pytest.mark.parametrize(
    "capabilities_param",
    [("power", False), ("dim+power", True)],
)
@pytest.mark.parametrize("dim", [None, 69])
@patch("pydeako.deako._deako.Deako.record_device")
def test_incoming_json_device_found(
    record_device_mock, dim, capabilities_param,
):
    """Test deako.incoming_json device with capabilities."""
    [capabilities, dimmable] = capabilities_param
    device_id = str(uuid4())
    device_name = str(uuid4())
    deako = Deako(Mock())

    incoming = {
        "type": "DEVICE_FOUND",
        "data": {
            "name": device_name,
            "capabilities": capabilities,
            "uuid": device_id,
            "state": {
                "power": True,
            },
        },
    }

    if dim is not None:
        incoming["data"]["state"]["dim"] = dim

    deako.incoming_json(incoming)

    record_device_mock.assert_called_once_with(
        device_name, device_id, dimmable, True, dim
    )


@pytest.mark.parametrize("dim", [None, 69])
@patch("pydeako.deako._deako.Deako.record_device")
def test_incoming_json_device_found_no_capabilities(
    record_device_mock, dim,
):
    """Test deako.incoming_json device, no capabilities."""
    device_id = str(uuid4())
    device_name = str(uuid4())
    deako = Deako(Mock())

    incoming = {
        "type": "DEVICE_FOUND",
        "data": {
            "name": device_name,
            "uuid": device_id,
            "state": {
                "power": True,
            },
        },
    }

    if dim is not None:
        incoming["data"]["state"]["dim"] = dim

    deako.incoming_json(incoming)

    record_device_mock.assert_called_once_with(
        device_name, device_id, dim is not None, True, dim
    )


@pytest.mark.parametrize("dim", [None, 69])
@patch("pydeako.deako._deako.Deako.update_state")
def test_incoming_json_event(update_state_mock, dim):
    """Test Deako.incoming_json state event."""
    device_id = str(uuid4())
    deako = Deako(Mock())

    incoming = {
        "type": "EVENT",
        "data": {
            "target": device_id,
            "state": {
                "power": True,
            },
        },
    }

    if dim is not None:
        incoming["data"]["state"]["dim"] = dim

    deako.incoming_json(incoming)

    update_state_mock.assert_called_once_with(device_id, True, dim)


@patch("pydeako.deako._deako.Deako.update_state")
def test_incoming_json_exception_ignored(update_state_mock):
    """Test Deako.incoming_json parse failure ignored."""
    device_id = str(uuid4())
    deako = Deako(Mock())

    incoming = {
        "type": "EVENT",
        "data": {
            "target": device_id,
            "state": {
                "power": True,
            },
        },
    }

    update_state_mock.side_effect = Exception()

    deako.incoming_json(incoming)

    update_state_mock.assert_called_once_with(device_id, True, None)


@pytest.mark.parametrize("dimmable", [True, False])
@pytest.mark.parametrize("dim", [None, 69])
def test_incoming_record_device(dim, dimmable):
    """Test Deako.incoming_json record device."""
    device_id = str(uuid4())
    device_name = str(uuid4())
    deako = Deako(Mock())

    deako.record_device(device_name, device_id, dimmable, False, dim)

    assert deako.devices[device_id] == {
        "name": device_name,
        "uuid": device_id,
        "dimmable": dimmable,
        "state": {
            "power": False,
            "dim": dim,
        },
    }


@patch("pydeako.deako._deako._Manager.init_connection")
@pytest.mark.asyncio
async def test_connect(manager_init_connection_mock):
    """Test Deako.connect."""
    deako = Deako(Mock())

    await deako.connect()

    manager_init_connection_mock.assert_called_once()


@patch("pydeako.deako._deako._Manager.close")
@pytest.mark.asyncio
async def test_disconnect(manager_close_mock):
    """Test Deako.disconnect."""
    deako = Deako(Mock())

    await deako.disconnect()

    manager_close_mock.assert_called_once()


def test_get_devices():
    """Test Deako.get_devices."""
    deako = Deako(Mock())
    devices_mock = Mock()
    deako.devices = devices_mock

    assert deako.get_devices() == devices_mock


@patch("pydeako.deako._deako._Manager", autospec=True)
@patch("pydeako.deako._deako.asyncio", autospec=True)
@pytest.mark.asyncio
# pylint: disable-next=unused-argument
async def test_find_devices_no_expected_devices(asyncio_mock, manager_mock):
    """Test Deako.find_devices no expected devices raises."""
    deako = Deako(Mock())

    with pytest.raises(FindDevicesError) as exc:
        await deako.find_devices()

    assert str(exc.value) == (
        "Failed to find devices: Timed out waiting for device list response"
    )
    manager_mock.return_value.send_get_device_list.assert_called_once()


@patch("pydeako.deako._deako._Manager", autospec=True)
@patch("pydeako.deako._deako.asyncio", autospec=True)
@pytest.mark.asyncio
# pylint: disable-next=unused-argument
async def test_find_devices_devices_timeout(asyncio_mock, manager_mock):
    """Test Deako.find_devices times out and raises."""
    deako = Deako(Mock())

    deako.expected_devices = 42
    with pytest.raises(FindDevicesError) as exc:
        await deako.find_devices()

    assert str(exc.value) == (
        "Failed to find devices: Timed out waiting for devices to be found. "
        "Expected 42 devices but only found 0"
    )
    manager_mock.return_value.send_get_device_list.assert_called_once()


@patch("pydeako.deako._deako._Manager", autospec=True)
@patch("pydeako.deako._deako.asyncio", autospec=True)
@pytest.mark.asyncio
# pylint: disable-next=unused-argument
async def test_find_devices(asyncio_mock, manager_mock):
    """Test Deako.find_devices."""
    deako = Deako(Mock())

    deako.expected_devices = 42
    deako.devices = {}
    for i in range(42):
        deako.devices[str(i)] = i

    await deako.find_devices()

    manager_mock.return_value.send_get_device_list.assert_called_once()


@patch("pydeako.deako._deako._Manager", autospec=True)
@patch("pydeako.deako._deako.asyncio", autospec=True)
@pytest.mark.asyncio
# pylint: disable-next=unused-argument
async def test_find_devices_send_request_fails(asyncio_mock, manager_mock):
    """Test Deako.find_devices when sending device list request fails."""
    deako = Deako(Mock())

    # Make send_get_device_list return False to simulate failure
    manager_mock.return_value.send_get_device_list.return_value = False

    with pytest.raises(FindDevicesError) as exc:
        await deako.find_devices()
    assert str(exc.value) == (
        "Failed to find devices: Failed to send device list request"
    )
    manager_mock.return_value.send_get_device_list.assert_called_once()


@patch("pydeako.deako._deako._Manager", autospec=True)
@pytest.mark.asyncio
async def test_control_device(manager_mock):
    """Test Deako.control_device."""
    device_id = Mock()
    power = Mock()
    dim = Mock()
    deako = Deako(Mock())

    await deako.control_device(device_id, power, dim)

    manager_mock.return_value.send_state_change.assert_called_once_with(
        device_id, power, dim, completed_callback=ANY
    )


@pytest.mark.parametrize("device_exists", [True, False])
@pytest.mark.asyncio
async def test_get_name(device_exists):
    """Test Deako.get_name."""
    device_id = str(uuid4())
    device_name = str(uuid4())
    deako = Deako(Mock())

    if device_exists:
        deako.devices[device_id] = {
            "name": device_name,
        }

    name = deako.get_name(device_id)

    if device_exists:
        assert name == device_name
    else:
        assert name is None


@pytest.mark.parametrize("device_exists", [True, False])
@pytest.mark.asyncio
async def test_get_state(device_exists):
    """Test Deako.get_state"""
    device_id = str(uuid4())
    device_state = Mock()
    deako = Deako(Mock())

    if device_exists:
        deako.devices[device_id] = {
            "state": device_state,
        }

    state = deako.get_state(device_id)

    if device_exists:
        assert state == device_state
    else:
        assert state is None


@pytest.mark.parametrize("dimmable", [True, False])
@pytest.mark.parametrize("device_exists", [True, False])
@pytest.mark.asyncio
async def test_get_dimmable(device_exists, dimmable):
    """Test Deako.get_state"""
    device_id = str(uuid4())
    deako = Deako(Mock())

    if device_exists:
        deako.devices[device_id] = {
            "dimmable": dimmable,
        }

    is_dimmable = deako.is_dimmable(device_id)

    if device_exists:
        assert is_dimmable == dimmable
    else:
        assert is_dimmable is None


# ---------------------------------------------------------------------------
# Phase 5 coverage: two-layer control_device contract
# (private _control_device_strict raises; public control_device preserves
# 0.x behavior with a narrow catch), plus Deako.is_connected() wrapper.
# ---------------------------------------------------------------------------


@patch("pydeako.deako._deako._Manager")
@pytest.mark.asyncio
async def test_control_device_strict_raises_on_no_connection(manager_mock):
    """_control_device_strict raises NoSocketException when there is no
    active connection (send_state_change returned False)."""
    manager_instance = manager_mock.return_value
    manager_instance.send_state_change = AsyncMock(return_value=False)

    deako = Deako(Mock())

    with pytest.raises(NoSocketException):
        # pylint: disable-next=protected-access
        await deako._control_device_strict(
            uuid="uuid-1", power=True, dim=None,
        )

    manager_instance.send_state_change.assert_called_once_with(
        "uuid-1", True, None, completed_callback=ANY,
    )


@patch("pydeako.deako._deako._Manager")
@pytest.mark.asyncio
async def test_control_device_strict_propagates_send_oserror(manager_mock):
    """_control_device_strict propagates OSError from a real send failure."""
    manager_instance = manager_mock.return_value
    manager_instance.send_state_change = AsyncMock(
        side_effect=OSError("broken pipe"),
    )

    deako = Deako(Mock())

    with pytest.raises(OSError):
        # pylint: disable-next=protected-access
        await deako._control_device_strict(
            uuid="uuid-1", power=True, dim=None,
        )


@patch("pydeako.deako._deako._Manager")
@pytest.mark.asyncio
async def test_control_device_public_returns_none_on_no_connection(
    manager_mock,
):
    """Public control_device swallows NoSocketException, returns None."""
    manager_instance = manager_mock.return_value
    manager_instance.send_state_change = AsyncMock(return_value=False)

    deako = Deako(Mock())

    result = await deako.control_device("uuid-1", True, None)

    assert result is None


@patch("pydeako.deako._deako._Manager")
@pytest.mark.asyncio
async def test_control_device_public_returns_none_on_send_oserror(
    manager_mock,
):
    """Public control_device swallows OSError, returns None."""
    manager_instance = manager_mock.return_value
    manager_instance.send_state_change = AsyncMock(
        side_effect=OSError("broken pipe"),
    )

    deako = Deako(Mock())

    result = await deako.control_device("uuid-1", True, None)

    assert result is None


@pytest.mark.asyncio
async def test_control_device_public_does_not_catch_unrelated_exceptions():
    """Public control_device's catch is narrow: unrelated exceptions
    (e.g. a ValueError from an upstream validation path) propagate
    unchanged. Proves the wrapper is not a bare except Exception."""
    deako = Deako(Mock())

    with patch.object(
        deako,
        "_control_device_strict",
        AsyncMock(side_effect=ValueError("bad uuid")),
    ):
        with pytest.raises(ValueError):
            await deako.control_device("uuid-1", True, None)


@patch("pydeako.deako._deako._Manager")
@pytest.mark.asyncio
async def test_control_device_public_logs_at_debug_on_failure(
    manager_mock, caplog,
):
    """Public control_device logs send failures at DEBUG, not WARNING."""
    manager_instance = manager_mock.return_value
    manager_instance.send_state_change = AsyncMock(return_value=False)

    deako = Deako(Mock())

    caplog.set_level(logging.DEBUG, logger="pydeako.deako")

    await deako.control_device("uuid-1", True, None)

    debug_hits = [
        record for record in caplog.records
        if record.levelno == logging.DEBUG
        and "control_device send failed" in record.message
    ]
    assert debug_hits, "expected a DEBUG log on swallowed failure"
    # Nothing at WARNING or above for this path.
    assert not any(
        record.levelno >= logging.WARNING
        and "control_device send failed" in record.message
        for record in caplog.records
    )


def test_is_connected_no_connection():
    """is_connected is False when connection_manager.connection is None."""
    deako = Deako(Mock())
    deako.connection_manager.connection = None

    assert deako.is_connected() is False


def test_is_connected_connection_not_connected():
    """is_connected is False when the underlying _Connection reports False."""
    deako = Deako(Mock())
    connection_mock = Mock()
    connection_mock.is_connected.return_value = False
    deako.connection_manager.connection = connection_mock

    assert deako.is_connected() is False
    connection_mock.is_connected.assert_called_once()


def test_is_connected_connected():
    """is_connected is True only when the _Connection reports CONNECTED."""
    deako = Deako(Mock())
    connection_mock = Mock()
    connection_mock.is_connected.return_value = True
    deako.connection_manager.connection = connection_mock

    assert deako.is_connected() is True
    connection_mock.is_connected.assert_called_once()
