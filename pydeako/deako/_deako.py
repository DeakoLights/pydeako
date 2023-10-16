"""Connection over socket."""
import asyncio

import logging
from typing import Any

from ..models import ResponseType
from ._manager import _Manager

_LOGGER: logging.Logger = logging.getLogger(__package__)


class DeviceListTimeout(Exception):
    """Device list request timed out."""


class FindDevicesTimeout(Exception):
    """Timeout finding devices."""


# 2s per device to be received to be conservative
DEVICE_FOUND_TIME_FACTOR_S = 2
DEFAULT_DEVICE_LIST_TIMEOUT_S = 10
DEVICE_LIST_POLLING_INTERVAL_S = 1
DEVICE_FOUND_POLLING_INTERVAL_S = 1


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
        self, uuid: str, power: bool, dim: int | None = None
    ) -> None:
        """Update an in memory device's state."""
        if uuid not in self.devices:
            return

        self.devices[uuid]["state"]["power"] = power
        self.devices[uuid]["state"]["dim"] = dim

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
                self.record_device(
                    subdata["name"],
                    subdata["uuid"],
                    state["power"],
                    state.get("dim"),
                )
            elif in_data["type"] == ResponseType.EVENT:
                subdata = in_data["data"]
                state = subdata["state"]
                self.update_state(
                    subdata["target"], state["power"], state.get("dim")
                )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            _LOGGER.error("Failed to parse %s: %s", in_data, exc)

    def record_device(
        self, name: str, uuid: str, power: bool, dim: int | None = None
    ) -> None:
        """Store a device in local memory."""
        if uuid not in self.devices:
            self.devices[uuid] = {"state": {}}

        self.devices[uuid]["name"] = name
        self.devices[uuid]["uuid"] = uuid
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
        await self.connection_manager.send_get_device_list()
        remaining = timeout
        while (
            self.expected_devices == 0 and remaining > 0
        ):  # wait for device list
            _LOGGER.debug(
                "waiting for device list... time remaining: %is", remaining
            )
            await asyncio.sleep(DEVICE_LIST_POLLING_INTERVAL_S)
            remaining -= DEVICE_LIST_POLLING_INTERVAL_S

        # if we get a response, expected_devices will be at least 1
        if self.expected_devices == 0:
            raise DeviceListTimeout()

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
            raise FindDevicesTimeout()

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
