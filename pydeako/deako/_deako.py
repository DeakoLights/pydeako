"""Connection over socket."""
import asyncio

import logging
from typing import Any

from ..models import ResponseType
from ._manager import _Manager

_LOGGER: logging.Logger = logging.getLogger(__package__)


class FindDevicesError(Exception):
    """Unable to find devices."""

    def __init__(self, reason: str = "unknown") -> None:
        """Initialize with optional reason string."""
        self.reason = reason
        super().__init__()

    def __str__(self):
        return f"Failed to find devices: {self.reason}"


# 2s per device to be received to be conservative
DEVICE_FOUND_TIME_FACTOR_S = 2
DEFAULT_DEVICE_LIST_TIMEOUT_S = 10
DEVICE_LIST_POLLING_INTERVAL_S = 1
DEVICE_FOUND_POLLING_INTERVAL_S = 1

CAPABILITY_DIMMABLE = "dim"


class Deako:
    """Deako specific socket api."""

    connection_manager: _Manager
    devices: dict[str, Any]
    expected_devices: int

    def __init__(self, get_address, client_name: str | None = None) -> None:
        """Init manager for Deako local integration."""
        self.connection_manager = _Manager(
            get_address,
            self.incoming_json,
            client_name=client_name,
        )
        self.devices: dict[str, Any] = {}
        self.expected_devices = 0

    def update_state(
        self, uuid: str, power: bool, dim: int | None = None,
    ) -> None:
        """Update an in memory device's state."""
        if uuid not in self.devices:
            return

        self.devices[uuid]["state"]["power"] = power
        # dimmables don't always send dim
        self.devices[uuid]["state"]["dim"] = (
            dim or self.devices[uuid]["state"]["dim"]
        )

        if "callback" in self.devices[uuid]:
            self.devices[uuid]["callback"]()

    def set_state_callback(self, uuid: str, callback) -> None:
        """Add a state update listener."""
        if uuid in self.devices:
            self.devices[uuid]["callback"] = callback

    def incoming_json(self, in_data: dict) -> None:
        """Parse incoming socket data which is json."""
        try:
            if in_data["type"] == ResponseType.DEVICE_LIST:
                subdata = in_data["data"]
                self.expected_devices = subdata["number_of_devices"]
            elif in_data["type"] == ResponseType.DEVICE_FOUND:
                subdata = in_data["data"]
                state = subdata["state"]
                if subdata.get("capabilities") is not None:
                    dimmable = CAPABILITY_DIMMABLE in subdata["capabilities"]
                else:
                    # support older local api versions
                    dimmable = state.get("dim") is not None
                self.record_device(
                    subdata["name"],
                    subdata["uuid"],
                    dimmable,
                    state["power"],
                    state.get("dim"),
                )
            elif in_data["type"] == ResponseType.EVENT:
                subdata = in_data["data"]
                state = subdata["state"]
                self.update_state(
                    subdata["target"], state["power"], state.get("dim"),
                )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            _LOGGER.error("Failed to parse %s: %s", in_data, exc)

    # pylint: disable-next=too-many-arguments
    def record_device(
        self, name: str, uuid: str, dimmable: bool,
        power: bool, dim: int | None = None,
    ) -> None:
        """Store a device in local memory."""
        if uuid not in self.devices:
            self.devices[uuid] = {"state": {}}

        self.devices[uuid]["name"] = name
        self.devices[uuid]["uuid"] = uuid
        self.devices[uuid]["dimmable"] = dimmable
        self.devices[uuid]["state"]["power"] = power
        self.devices[uuid]["state"]["dim"] = dim

    async def connect(self) -> None:
        """Initiate the connection sequence."""
        await self.connection_manager.init_connection()

    async def disconnect(self) -> None:
        """Close the connection."""
        self.connection_manager.close()

    def get_devices(self) -> dict:
        """Get the devices that have been recorded."""
        return self.devices

    async def find_devices(
        self,
        timeout=DEFAULT_DEVICE_LIST_TIMEOUT_S,
    ) -> None:
        """Request the device list."""
        _LOGGER.info("Finding devices")
        success = await self.connection_manager.send_get_device_list()
        if not success:
            raise FindDevicesError("Failed to send device list request")
        remaining = timeout
        # wait for device list
        while self.expected_devices == 0 and remaining > 0:
            _LOGGER.debug(
                "waiting for device list... time remaining: %is", remaining,
            )
            await asyncio.sleep(DEVICE_LIST_POLLING_INTERVAL_S)
            remaining -= DEVICE_LIST_POLLING_INTERVAL_S

        # if we get a response, expected_devices will be at least 1
        if self.expected_devices == 0:
            raise FindDevicesError(
                "Timed out waiting for device list response",
            )

        remaining = self.expected_devices * DEVICE_FOUND_TIME_FACTOR_S
        while len(self.devices) != self.expected_devices and remaining > 0:
            _LOGGER.debug(
                "waiting for devices... expected: %i, received: "
                + "%i, time remaining: %is",
                self.expected_devices,
                len(self.devices),
                remaining,
            )
            await asyncio.sleep(DEVICE_FOUND_POLLING_INTERVAL_S)
            remaining -= DEVICE_FOUND_POLLING_INTERVAL_S
        _LOGGER.debug("found %i devices", len(self.devices))

        if len(self.devices) != self.expected_devices and remaining == 0:
            raise FindDevicesError(
                f"Timed out waiting for devices to be found. Expected "
                f"{self.expected_devices} devices but only found "
                f"{len(self.devices)}",
            )

    async def control_device(
        self, uuid: str, power: bool, dim: int | None = None
    ) -> None:
        """Add control request to queue."""

        def completed_callback():
            self.update_state(uuid, power, dim)

        await self.connection_manager.send_state_change(
            uuid, power, dim, completed_callback=completed_callback
        )

    def get_name(self, uuid: str) -> str | None:
        """Get a device's name by uuid."""
        device_data = self.devices.get(uuid)
        if device_data is None:
            return None

        # name should exist if we have data on this device
        return device_data["name"]

    def get_state(self, uuid: str) -> dict | None:
        """Get a device's state by uuid."""
        device_data = self.devices.get(uuid)
        if device_data is None:
            return None

        # state should exist if we have data on this device
        return device_data["state"]

    def is_dimmable(self, uuid: str) -> bool | None:
        """Get whether a device is dimmable by uuid."""
        device_data = self.devices.get(uuid)
        if device_data is None:
            return None

        # dimmable should exist if we have data on this device
        return device_data["dimmable"]
