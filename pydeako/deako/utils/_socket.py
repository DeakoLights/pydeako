"""
Socket wrapper for connecting to an address, reading, and sending bytes.
"""

import asyncio
import logging
import socket

_LOGGER: logging.Logger = logging.getLogger(__package__)
_MAX_PACKET_SIZE_BYTES = 2048


class NoSocketException(Exception):
    """No socket to perform ops exception"""


class _SocketConnection:
    """Socket wrapper class."""

    address: str
    loop: asyncio.AbstractEventLoop
    sock: socket.socket | None

    def __init__(self, address: str, loop: asyncio.AbstractEventLoop) -> None:
        self.address = address
        self.loop = loop
        self.sock = None

    async def connect_socket(self) -> None:
        """Connect to device with socket."""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setblocking(False)
        _LOGGER.info("Connecting to %s", self.address)
        address, port = self.address.split(":")
        await self.loop.sock_connect(self.sock, (address, port))

    def close_socket(self) -> None:
        """Close socket."""
        if self.sock is not None:
            self.sock.close()
            self.sock = None

    async def send_bytes(self, _bytes: bytes) -> None:
        """Send data to socket."""
        if self.sock is None:
            _LOGGER.error("No socket to send data to")
            raise NoSocketException()

        await self.loop.sock_sendall(self.sock, _bytes)

    async def read_bytes(self) -> bytes:
        """Read data from socket."""
        if self.sock is None:
            _LOGGER.error("No socket to read")
            raise NoSocketException()
        return await self.loop.sock_recv(self.sock, _MAX_PACKET_SIZE_BYTES)
