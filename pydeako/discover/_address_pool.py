"""
Address pool that is used for DeakoDiscoverer.
"""
from collections import OrderedDict


class EmptyAddressPool(Exception):
    """Empty address pool"""


class _AddressPool:
    """Address pool of advertised addresses."""

    addresses: OrderedDict[str, str]

    def __init__(self) -> None:
        self.addresses = OrderedDict()

    def available_addresses(self) -> int:
        """Return the number of available addresses."""
        return len(self.addresses)

    def add_address(self, address: str, name: str) -> None:
        """Add address to the pool."""
        self.addresses[address] = name

    def get_address(self) -> tuple[str, str]:
        """Get an address from the pool."""
        if len(self.addresses) == 0:
            raise EmptyAddressPool()
        return self.addresses.popitem(last=True)

    def remove_address(self, address: str) -> None:
        """
        Remove an address from the pool.
        """
        del self.addresses[address]
