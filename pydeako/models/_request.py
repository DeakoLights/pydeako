"""
Deako local integration models.
"""

from typing import Any
from uuid import uuid4

from . import _const


def __base_request(
    destination: str | None = None,
    source: str | None = None,
    transaction_id: str | None = None,
) -> dict:
    return {
        "transactionId": transaction_id or str(uuid4()),
        "dst": destination or _const.DESTINATION,
        "src": source or _const.SOURCE,
    }


def device_ping_request(**kwargs) -> dict:
    """Generate a ping request."""
    return {
        **__base_request(**kwargs),
        "type": _const.RequestType.PING,
    }


def device_list_request(**kwargs) -> dict:
    """Generate a device list request."""
    return {
        **__base_request(**kwargs),
        "type": _const.RequestType.DEVICE_LIST,
    }


def state_change_request(
    device_uuid: str, power: bool, dim: int | None, **kwargs
) -> dict[str, Any]:
    """Generate a state change request."""
    state: dict[str, str | int] = {
        "power": power,
    }
    if dim is not None:
        state["dim"] = dim
    return {
        **__base_request(**kwargs),
        "type": _const.RequestType.CONTROL,
        "data": {
            "target": device_uuid,
            "state": state,
        },
    }
