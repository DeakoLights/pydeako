"""
Tests our functions to ensure the correct objects are generated.
"""
from uuid import uuid4
import pytest

from . import device_ping_request, device_list_request, state_change_request

TRANSACTION_ID = "some_id"
EXPECTED_BASE = {
    "transactionId": TRANSACTION_ID,
    "dst": "deako",
    "src": "pydeako_default",
}
DEVICE_UUID = "some_uuid"


def test_device_ping_request():
    """Test device ping request."""
    assert device_ping_request(transaction_id=TRANSACTION_ID) == {
        **EXPECTED_BASE,
        "type": "PING",
    }


def test_device_ping_request_with_params():
    """Test device ping request with params."""
    source = str(uuid4())
    destination = str(uuid4())
    assert device_ping_request(
        transaction_id=TRANSACTION_ID,
        source=source,
        destination=destination,
    ) == {
        **EXPECTED_BASE,
        "src": source,
        "dst": destination,
        "type": "PING",
    }


def test_device_list_request():
    """Test device list request."""
    assert device_list_request(transaction_id=TRANSACTION_ID) == {
        **EXPECTED_BASE,
        "type": "DEVICE_LIST",
    }


def test_device_list_request_with_params():
    """Test device list request with params."""
    source = str(uuid4())
    destination = str(uuid4())
    assert device_list_request(
        transaction_id=TRANSACTION_ID,
        source=source,
        destination=destination,
    ) == {
        **EXPECTED_BASE,
        "src": source,
        "dst": destination,
        "type": "DEVICE_LIST",
    }


@pytest.mark.parametrize(
    "test_input,expected_state",
    [
        (
            [DEVICE_UUID, False, None],
            {
                "power": False,
            },
        ),
        (
            [DEVICE_UUID, True, None],
            {
                "power": True,
            },
        ),
        (
            [DEVICE_UUID, True, 100],
            {
                "power": True,
                "dim": 100,
            },
        ),
    ],
)
def test_state_change_request(test_input, expected_state):
    """Test state change request."""
    assert state_change_request(
        *test_input,
        transaction_id=TRANSACTION_ID,
    ) == {
        **EXPECTED_BASE,
        "type": "CONTROL",
        "data": {
            "target": DEVICE_UUID,
            "state": expected_state,
        },
    }


def test_state_change_request_with_params():
    """Test state change request."""
    source = str(uuid4())
    destination = str(uuid4())
    assert state_change_request(
        DEVICE_UUID,
        True,
        69,
        transaction_id=TRANSACTION_ID,
        source=source,
        destination=destination,
    ) == {
        **EXPECTED_BASE,
        "type": "CONTROL",
        "src": source,
        "dst": destination,
        "data": {
            "target": DEVICE_UUID,
            "state": {
                "power": True,
                "dim": 69,
            },
        },
    }
