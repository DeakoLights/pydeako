"""
This module provides the types and functions to generate requests
to deako devices. Source reference:
https://github.com/DeakoLights/local-integrations/blob/master/API.md
"""
from ._const import RequestType, ResponseType
from ._request import (
    device_list_request,
    device_ping_request,
    state_change_request,
)

__all__ = [
    'RequestType',
    'ResponseType',
    'device_list_request',
    'device_ping_request',
    'state_change_request',
]
