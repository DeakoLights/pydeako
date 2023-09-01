"""
Deako local integration constants.
"""

from enum import Enum

SOURCE = "pydeako_default"
DESTINATION = "deako"


class RequestType(str, Enum):
    """Request type enum."""

    CONTROL = "CONTROL"
    DEVICE_LIST = "DEVICE_LIST"
    PING = "PING"


class ResponseType(str, Enum):
    """Response type enum."""

    DEVICE_FOUND = "DEVICE_FOUND"
    DEVICE_LIST = "DEVICE_LIST"
    EVENT = "EVENT"
    PONG = "PING"
