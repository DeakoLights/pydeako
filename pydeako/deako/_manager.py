"""
Manager to SocketConnection, ensuring that there's always an
active connection. Uses two workers, one to check connectivity
through pinging, and one to check for messages to send.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Callable

from ..discover import DevicesNotFoundException
from ..models import (
    device_list_request,
    device_ping_request,
    state_change_request,
    RequestType,
    ResponseType,
)
from .utils import _Connection
from ._request import _Request

_LOGGER: logging.Logger = logging.getLogger(__package__)

CONNECTED_POLLING_INTERVAL_S = 1
CONNECTION_TIMEOUT_S = 10
WORKER_WAIT_S = 0.5
PING_WORKER_WAIT_S = 10


@dataclass
class _ManagerState:
    """State for _Manager, used by workers."""

    connecting: bool = False
    canceled: bool = False
    logged_send_error = False


# pylint: disable-next=too-many-instance-attributes
class _Manager:
    """Manage the socket connection to Deako local integrations."""

    maintain_worker: asyncio.Task | None = None
    worker: asyncio.Task | None = None
    connection: _Connection | None = None
    message_queue: asyncio.Queue[_Request]
    tasks: set[asyncio.Task]
    client_name: str | None
    state: _ManagerState

    def __init__(
        self,
        get_address,
        incoming_json_callback,
        client_name: str | None = None,
    ) -> None:
        """Initialize with get address function and incoming json callback."""
        self.get_address = get_address
        self.incoming_json_callback = incoming_json_callback
        self.pong_received = False
        self.message_queue = asyncio.Queue()
        self.tasks = set()
        self.client_name = client_name
        self.state = _ManagerState()

    async def init_connection(self) -> None:
        """Initialize the connection process."""
        if self.state.connecting:
            _LOGGER.error("Already attempting to connect")
            return
        self.state.connecting = True
        self.worker = asyncio.create_task(self.control_device_worker())
        try:
            address = await self.get_address()
        except DevicesNotFoundException:
            self.create_connection_task()
            return
        connection = _Connection(address, self.incoming_json)
        timeout = 0
        while not connection.is_connected() and timeout < CONNECTION_TIMEOUT_S:
            await asyncio.sleep(CONNECTED_POLLING_INTERVAL_S)
            timeout += CONNECTED_POLLING_INTERVAL_S
        if timeout == CONNECTION_TIMEOUT_S:
            _LOGGER.error("Timeout attempting to connect. Trying again")
            self.create_connection_task()
            return
        self.connection = connection
        # init connection watching
        if self.maintain_worker is None:
            self.maintain_worker = asyncio.create_task(
                self.maintain_connection_worker()
            )
        self.state.connecting = False

    def close(self) -> None:
        """Close connection."""
        _LOGGER.debug("Closing connection and canceling workers")
        self.state.canceled = True
        if self.worker is not None:
            self.worker.cancel()
            self.worker = None
        if self.maintain_worker is not None:
            self.maintain_worker.cancel()
            self.maintain_worker = None
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def create_connection_task(self):
        """Create an async task to initiate connection."""
        # RUF006
        # pylint: disable-next=line-too-long
        # noqa keep reference via: https://stackoverflow.com/questions/71938799/python-asyncio-create-task-really-need-to-keep-a-reference
        # even if we don't care
        task = asyncio.create_task(self.init_connection())
        self.tasks.add(task)

        def remove_task(_task):
            self.tasks.remove(_task)

        task.add_done_callback(remove_task)

    async def maintain_connection_worker(self) -> None:
        """Monitor connection and restart if there's a failure."""
        await asyncio.sleep(PING_WORKER_WAIT_S)
        while True:
            if self.state.canceled:
                break
            self.pong_received = False
            _LOGGER.debug("Pinging for responsiveness")
            await self.message_queue.put(
                _Request(device_ping_request(source=self.client_name)),
            )
            await asyncio.sleep(PING_WORKER_WAIT_S)
            if self.pong_received:
                _LOGGER.debug("Pong received")
            else:
                _LOGGER.warning("Never received pong! Dumping this connection")
                self.close()
                self.create_connection_task()
                break

    def incoming_json(self, incoming_json: dict) -> None:
        """Handle incoming json."""
        response_type = incoming_json.get("type")
        if response_type == ResponseType.PONG:
            self.pong_received = True
        else:
            self.incoming_json_callback(incoming_json)

    async def send_get_device_list(self) -> None:
        """Queue the device list request."""
        await self.message_queue.put(
            _Request(device_list_request(source=self.client_name)),
        )

    async def send_state_change(
        self,
        uuid,
        power,
        dim=None,
        completed_callback: Callable | None = None,
    ) -> None:
        """Queue a state change request."""
        req = _Request(
            state_change_request(uuid, power, dim, source=self.client_name),
            completed_callback=completed_callback,
        )
        await self.message_queue.put(req)

    async def control_device_worker(self) -> None:
        """Forever running until canceled worker that checks queue."""
        while True:
            if self.state.canceled:
                break
            await self.process_queue_item()
            await asyncio.sleep(WORKER_WAIT_S)

    async def process_queue_item(self):
        """Dequeue message and send if there's a connection"""
        request = await self.message_queue.get()
        if self.connection is not None:
            # if we have a connection, we can log this error again
            # if things go south
            self.state.logged_send_error = False
            await self.connection.send_data(request.get_body_str())
            self.message_queue.task_done()
            request.complete_callback()
        else:
            if not self.state.logged_send_error:  # don't spam these logs
                _LOGGER.error("No connection to send data to")
                self.state.logged_send_error = True
            if request.get_type() == RequestType.PING:
                # dump this request, no longer needed
                self.message_queue.task_done()
            else:
                await self.message_queue.put(request)
