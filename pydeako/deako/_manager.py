"""
Manager to SocketConnection, ensuring that there's always an
active connection. Uses two workers, one to check connectivity
through pinging, and one to check for messages to send.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable

from ..discover import DevicesNotFoundException
from ..models import (
    device_list_request,
    device_ping_request,
    state_change_request,
    ErrorCode,
    ResponseStatus,
    ResponseType,
)
from .utils import _Connection
from ._request import _Request

_LOGGER: logging.Logger = logging.getLogger(__package__)

CONNECTED_POLLING_INTERVAL_S = 1
CONNECTION_TIMEOUT_S = 10
WORKER_WAIT_S = 0.5
PING_WORKER_WAIT_S = 10
# Max time to wait for the device to answer a request before sending the next.
RESPONSE_TIMEOUT_S = 1.0
# A request answered with DEVICE_BUSY is sent again after this delay.
BUSY_RETRY_DELAY_S = 0.25
MAX_BUSY_RETRIES = 3


def _is_device_busy(response: dict[str, Any] | None) -> bool:
    """Return whether a response is a DEVICE_BUSY error."""
    if not isinstance(response, dict):
        return False
    if response.get("status") != ResponseStatus.ERROR:
        return False
    return (response.get("data") or {}).get("code") == ErrorCode.DEVICE_BUSY


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
    tasks: set[asyncio.Task]
    client_name: str | None
    state: _ManagerState
    send_lock: asyncio.Lock
    pending_responses: dict[str, asyncio.Future[dict[str, Any]]]

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
        self.tasks = set()
        self.client_name = client_name
        self.state = _ManagerState()
        self.send_lock = asyncio.Lock()
        self.pending_responses = {}

    async def init_connection(self) -> None:
        """Initialize the connection process."""
        if self.state.connecting:
            _LOGGER.error("Already attempting to connect")
            return
        self.state.connecting = True
        try:
            address, name = await self.get_address()
        except DevicesNotFoundException:
            _LOGGER.warning("No devices to connect to")
            self.create_connection_task()
            self.state.connecting = False
            return
        connection = _Connection(address, name, self.incoming_json)
        timeout = 0
        while not connection.is_connected() and timeout < CONNECTION_TIMEOUT_S:
            await asyncio.sleep(CONNECTED_POLLING_INTERVAL_S)
            timeout += CONNECTED_POLLING_INTERVAL_S
        if timeout == CONNECTION_TIMEOUT_S:
            _LOGGER.error("Timeout attempting to connect. Trying again")
            self.state.connecting = False
            connection.close()
            self.create_connection_task()
            return
        self.connection = connection
        # init connection watching
        if self.maintain_worker is None:
            self.state.canceled = False
            self.maintain_worker = asyncio.create_task(
                self.maintain_connection_worker()
            )
        self.state.connecting = False

    def close(self) -> None:
        """Close connection."""
        _LOGGER.debug("Closing connection and canceling workers")
        self.state.canceled = True

        # Cancel and clear all pending tasks
        for task in self.tasks:
            task.cancel()
        self.tasks.clear()

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
            try:
                self.tasks.remove(_task)
            except KeyError:
                pass  # already removed

        task.add_done_callback(remove_task)

    async def maintain_connection_worker(self) -> None:
        """Monitor connection and restart if there's a failure."""
        await asyncio.sleep(PING_WORKER_WAIT_S)
        while True:
            if self.state.canceled:
                break
            self.pong_received = False
            _LOGGER.debug("Pinging for responsiveness")
            await self.send_request(
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
        transaction_id = incoming_json.get("transactionId", "")
        pending = self.pending_responses.get(transaction_id)
        if pending is not None and not pending.done():
            pending.set_result(incoming_json)
        response_type = incoming_json.get("type")
        if response_type == ResponseType.PONG:
            self.pong_received = True
        else:
            self.incoming_json_callback(incoming_json)

    async def send_get_device_list(self) -> bool:
        """Send the device list request."""
        return await self.send_request(
            _Request(device_list_request(source=self.client_name)),
        )

    async def send_state_change(
        self,
        uuid,
        power,
        dim=None,
        completed_callback: Callable | None = None,
    ) -> None:
        """Send a state change request."""
        await self.send_request(
            _Request(
                state_change_request(
                    uuid, power, dim, source=self.client_name,
                ),
                completed_callback=completed_callback,
            )
        )

    async def send_request(self, req: _Request) -> bool:
        """
        Send a request. Requests go out one at a time: the next one is sent
        once the device answers (or RESPONSE_TIMEOUT_S passes). Requests
        answered with DEVICE_BUSY are retried.
        """
        async with self.send_lock:
            for attempt in range(MAX_BUSY_RETRIES + 1):
                if attempt:
                    _LOGGER.debug("Device busy, retrying request")
                    await asyncio.sleep(BUSY_RETRY_DELAY_S)
                    req = req.with_new_transaction_id()
                if self.connection is None:
                    _LOGGER.warning("No connection to send data to")
                    return False
                response = await self._send_and_wait(self.connection, req)
                if not _is_device_busy(response):
                    return True

        _LOGGER.warning("Device still busy after %d retries", MAX_BUSY_RETRIES)
        return False

    async def _send_and_wait(
        self, connection: _Connection, req: _Request,
    ) -> dict[str, Any] | None:
        """Send a request and return its response, or None if none came."""
        transaction_id = req.get_transaction_id()
        if transaction_id is None:
            await connection.send_data(req.get_body_str())
            return None

        response = asyncio.get_running_loop().create_future()
        self.pending_responses[transaction_id] = response
        try:
            await connection.send_data(req.get_body_str())
            return await asyncio.wait_for(response, RESPONSE_TIMEOUT_S)
        except asyncio.TimeoutError:
            _LOGGER.debug("No response to request %s", transaction_id)
            return None
        finally:
            self.pending_responses.pop(transaction_id, None)
