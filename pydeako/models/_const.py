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


class ResponseStatus(str, Enum):
    """Response status enum."""

    OK = "ok"
    ERROR = "error"


class ErrorCode(str, Enum):
    """Error code enum, sent with an error response status."""

    DEVICE_BUSY = "DEVICE_BUSY"
    DEVICE_UNKNOWN = "DEVICE_UNKNOWN"
    REQUEST_UNKNOWN = "REQUEST_UNKNOWN"
    REQUEST_MALFORMED = "REQUEST_MALFORMED"
    REQUEST_INVALID = "REQUEST_INVALID"
