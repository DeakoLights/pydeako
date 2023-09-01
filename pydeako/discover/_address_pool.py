"""
Address pool that is used for DeakoDiscoverer.
"""

from queue import Queue, Empty


class EmptyAddressPool(Exception):
    """Empty address pool"""


class _AddressPool:
    """Address pool of advertised addresses."""

    queue: Queue[str]
    removed: set[str]  # devices that have advertised removal

    def __init__(self) -> None:
        self.queue = Queue()
        self.removed = set()

    def available_addresses(self) -> int:
        """Return the number of available addresses."""
        return self.queue.qsize() - len(self.removed)

    def add_address(self, address: str) -> None:
        """Add address to the pool."""
        self._put(address)

    def _get(self) -> str:
        return self.queue.get(block=False)

    def _put(self, address: str) -> None:
        self.queue.put(address, block=False)

    def get_address(self) -> str:
        """Get an address from the pool."""
        try:
            address = self._get()
            while address in self.removed:
                self.removed.remove(address)
                address = self._get()
            # put address at bottom of pool so if another device is needed,
            # this one is less likely to be picked
            # the address is still available as it hasn't advertised removal
            self._put(address)
            return address
        except Empty as exc:
            raise EmptyAddressPool from exc

    def remove_address(self, address: str) -> None:
        """
        Mark an address as removed when it advertises
        it's no longer available.
        """
        self.removed.add(address)
