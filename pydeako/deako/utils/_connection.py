"""
Class that manages a socket connection to a deako device.
"""

import asyncio
import json
import logging
from enum import Enum
from typing import Callable

from ._socket import _SocketConnection

_LOGGER: logging.Logger = logging.getLogger(__package__)


class UnknownStateException(Exception):
    """Unknown state."""


class ConnectionState(Enum):
    """Enum for connection states."""

    UNKNOWN = -1
    NOT_STARTED = 0
    CONNECTED = 1
    ERROR = 2
    CLOSED = 3


class _Connection:
    """
    Representation of a local socket connection to a Deako device.
    Continuously reads socket for messages async.
    """

    # pylint: disable=too-many-instance-attributes

    address: str
    name: str
    message_buffer: str
    loop: asyncio.AbstractEventLoop
    state: ConnectionState
    socket: _SocketConnection
    tasks: set[asyncio.Task]

    def __init__(
        self, address: str, name: str, on_data_callback: Callable[[dict], None]
    ) -> None:
        """Setup and start a socket connection."""
        self.address = address
        self.name = name
        self.loop = asyncio.get_running_loop()
        self.state = ConnectionState.NOT_STARTED
        self.on_data_callback = on_data_callback
        self.message_buffer = ""
        self.socket = _SocketConnection(address, self.loop)
        self.tasks = set()
        self.init_run()

    async def send_data(self, data_to_send: str) -> None:
        """Send data to socket."""
        _LOGGER.debug("[%s] Sending data: %s", self.address, data_to_send)
        try:
            await self.socket.send_bytes(str.encode(data_to_send))
        except Exception as exc:  # pylint: disable=broad-exception-caught
            _LOGGER.error("Error sending data: %s", exc)
            self.state = ConnectionState.ERROR

    async def read_socket(self) -> None:
        """Read data from socket."""
        try:
            data = await self.socket.read_bytes()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            _LOGGER.error("Error receiving data: %s", exc)
            self.state = ConnectionState.ERROR
            return

        self.parse_data(data)

    def parse_data(self, data: bytes) -> None:
        """
        Parse incoming bytes into json as expected. Possible to have
        data come in multiple chunks and multiple messages.
        """
        raw_string = data.decode("utf-8")
        _LOGGER.debug(
            "[%s] Raw message received: %s",
            self.format_name(),
            raw_string,
        )
        messages = raw_string.strip().split("\r\n")
        for message_str in messages:
            self.message_buffer = self.message_buffer + message_str
            try:
                message_json = json.loads(self.message_buffer)
                self.on_data_callback(message_json)
                self.message_buffer = ""
            except json.decoder.JSONDecodeError:
                _LOGGER.debug("Got partial message: %s", self.message_buffer)

    def init_run(self) -> None:
        """Init the run sequence and store run task."""
        # RUF006
        # pylint: disable-next=line-too-long
        # noqa keep reference via: https://stackoverflow.com/questions/71938799/python-asyncio-create-task-really-need-to-keep-a-reference
        # even if we don't care
        task = self.loop.create_task(self.run())

        def remove_task(_task):
            self.tasks.remove(_task)

        task.add_done_callback(remove_task)
        self.tasks.add(task)

    def close(self) -> None:
        """Close our socket and cancel all pending tasks."""
        self.socket.close_socket()
        for task in self.tasks:
            task.cancel()

    def is_connected(self) -> bool:
        """Return whether or not connected."""
        return self.state == ConnectionState.CONNECTED

    async def run(self) -> None:
        """State machine."""
        while True:
            if self.state == ConnectionState.NOT_STARTED:
                try:
                    await self.socket.connect_socket()
                    self.state = ConnectionState.CONNECTED
                    _LOGGER.info(
                        "Connected to Deako local integrations with %s",
                        self.format_name(),
                    )
                # pylint: disable-next=broad-exception-caught
                except Exception as exc:
                    _LOGGER.error(
                        "Failed to connect %s because %s",
                        self.format_name(),
                        exc,
                    )
                    self.state = ConnectionState.ERROR
            elif self.state == ConnectionState.CONNECTED:
                try:
                    await self.read_socket()
                # pylint: disable-next=broad-exception-caught
                except Exception as exc:
                    _LOGGER.error(
                        "Failed to read socket %s because %s",
                        self.format_name(),
                        exc,
                    )
                    self.state = ConnectionState.ERROR
            elif self.state == ConnectionState.ERROR:
                try:
                    self.close()
                    self.state = ConnectionState.CLOSED
                # pylint: disable-next=broad-exception-caught
                except Exception as exc:
                    _LOGGER.error(
                        "Failed to close socket %s because %s",
                        self.format_name(),
                        exc,
                    )
                    self.state = ConnectionState.CLOSED
            elif self.state == ConnectionState.CLOSED:
                # this socket is toast
                break
            else:
                _LOGGER.error("Unknown state: %s", self.state)
                raise UnknownStateException(f"Unknown state: {self.state}")

    def format_name(self) -> str:
        """Format name."""
        return f"{self.name}@{self.address}"
