"""
Tests our functions to ensure the correct objects are generated.
"""
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


def test_device_list_request():
    """Test device list request."""
    assert device_list_request(transaction_id=TRANSACTION_ID) == {
        **EXPECTED_BASE,
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
