"""
pydeako module provides the following:
 - models to interact with Deako devices locally with a socket connection
 - implementation of a Deako socket client
 - mdns discovery client
"""
# pylint: disable=duplicate-code
from .discover import DeakoDiscoverer, DevicesNotFoundException
from .deako import Deako, FindDevicesError
from .models import (
    RequestType,
    ResponseType,
    device_list_request,
    device_ping_request,
    state_change_request,
)

__all__ = [
    'DeakoDiscoverer',
    'DevicesNotFoundException',
    'Deako',
    'FindDevicesError',
    'RequestType',
    'ResponseType',
    'device_list_request',
    'device_ping_request',
    'state_change_request',
]
