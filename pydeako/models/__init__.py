"""
This module provides the types and functions to generate requests
to deako devices. Source reference:
https://github.com/DeakoLights/local-integrations/blob/master/API.md
"""
from ._const import ErrorCode, RequestType, ResponseStatus, ResponseType
from ._request import (
    device_list_request,
    device_ping_request,
    state_change_request,
)

__all__ = [
    'ErrorCode',
    'RequestType',
    'ResponseStatus',
    'ResponseType',
    'device_list_request',
    'device_ping_request',
    'state_change_request',
]
