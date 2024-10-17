"""
Deako device discovery via zeroconf.
"""

from asyncio import sleep
import logging
import socket

from zeroconf import ServiceBrowser, ServiceListener, Zeroconf

from ._address_pool import _AddressPool

DEAKO_TYPE = "_deako._tcp.local."
TIMEOUT_S = 10
POLLING_INTERVAL_S = 0.5

_LOGGER = logging.getLogger(__package__)


class DevicesNotFoundException(Exception):
    """No devices found."""

    def __init__(
        self,
        *args,
        msg=f"No devices found via {DEAKO_TYPE}",
        **kwargs,
    ):
        super().__init__(msg, *args, **kwargs)


class DeakoDiscoverer(ServiceBrowser):
    """Zeroconf browser with stored addresses."""

    address_pool: _AddressPool
    zeroconf: Zeroconf

    def __init__(self, zeroconf: Zeroconf | None = None) -> None:
        """Initialize Deako mdns discovery."""
        self.zeroconf = zeroconf or Zeroconf()
        self.address_pool = _AddressPool()
        # Note that this begins discovering on init
        super().__init__(
            self.zeroconf,
            DEAKO_TYPE,
            DeakoListener(
                self.address_pool.add_address,
                self.address_pool.remove_address,
            ),
        )

    async def get_address(self) -> tuple[str, str]:
        """
        Get an address from the pool of available addresses.
        Times out after TIMEOUT_S.
        """
        total_time = 0.0
        while (
            total_time < TIMEOUT_S
            and self.address_pool.available_addresses() < 1
        ):
            total_time += POLLING_INTERVAL_S
            await sleep(POLLING_INTERVAL_S)
        if self.address_pool.available_addresses() == 0:
            raise DevicesNotFoundException()
        address, name = self.address_pool.get_address()
        _LOGGER.debug("Got address %s, with device name %s", address, name)
        return address

    def stop(self) -> None:
        """Stop using zeroconf."""
        self.zeroconf.close()


class DeakoListener(ServiceListener):
    """Listener class that satisfies the interface for zeroconf browsers."""

    def __init__(
        self, device_address_callback, device_address_removed_callback
    ) -> None:
        """Save callbacks."""
        self.device_address_callback = device_address_callback
        self.device_address_removed_callback = device_address_removed_callback

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """Add services associated with service."""
        _LOGGER.debug("Add service with address type %s, name %s", type_, name)
        addresses = self.__get_addresses(zc, type_, name)
        for address in addresses:
            self.device_address_callback(address)

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """Remove addresses associated with service."""
        _LOGGER.debug(
            "Remove service with address type %s, name %s",
            type_,
            name,
        )
        addresses = self.__get_addresses(zc, type_, name)
        for address in addresses:
            self.device_address_removed_callback(address)

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """Update service."""
        _LOGGER.debug(
            "Update for service with address type %s, name %s",
            type_,
            name,
        )

    def __get_addresses(
        self, zeroconf: Zeroconf, address_type: str, name: str
    ) -> list[str]:
        """
        Get the service info for a specified address type and name.
        Convert to local ips with port.
        """
        info = zeroconf.get_service_info(address_type, name)
        if info is not None:
            addresses = info.addresses
            port = info.port
            return [
                f"{socket.inet_ntoa(address)}:{port}" for address in addresses
            ]
        return []
