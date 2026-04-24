"""
Connection pool for Deako bridges with primary/failover support.

Manages two bridge connections:
  - An active primary Deako used for live device commands.
  - A standby bridge held warm by a single TCP keepalive socket so
    that a failover switch does not have to wait for the bridge to
    leave idle mode.

Recovery is caller-driven. There is no background health monitor and
no periodic task. The `control_device()` send path triggers at most
one failover switch per call on a real send failure; once the pool
is in a degraded state the next `control_device()` call invokes a
two-host recovery routine that probes and connects to
`primary_host` first, then `failover_host`.

`stop()` is terminal. A stopped pool is not restartable. Callers
that want a fresh pool must construct a new one. Host names
(`primary_host`, `failover_host`) are `str` for the lifetime of the
pool and never nulled out on any path; degraded state is signaled
through `ConnectionPoolState`, not missing host fields.
"""
# pylint: disable=too-many-lines

import asyncio
import logging
from dataclasses import dataclass
from typing import Callable

from ._deako import Deako
from .utils._socket import NoSocketException, _SocketConnection

_LOGGER: logging.Logger = logging.getLogger(__package__)

# Bridge TCP port used by `_tcp_probe`. The protocol layer parses
# "ip:port" address strings in utils/_socket.py and exposes no
# shared port constant, so the pool owns this value at module scope.
DEAKO_DEFAULT_PORT = 23

# Upper bound on waiting for a concurrent switch to complete.
SWITCH_WAIT_TIMEOUT_S = 10.0

# Bounded connect-with-retry budget used by `_switch_to_failover`
# and `_attempt_recovery`.
SWITCH_CONNECT_RETRIES = 3
SWITCH_CONNECT_BACKOFF_S = 1.0

# Upper bound on `_wait_ready` for the failover host to accept
# TCP connections after a recycle.
BRIDGE_RECYCLE_TIMEOUT_S = 10.0

# Upper bound on a single `_tcp_probe` call.
PROBE_TIMEOUT = 3.0

# Per-step teardown bound applied to `keepalive.stop()` and
# `active.disconnect()` in `stop()`, `_switch_to_failover`, and
# `_attempt_recovery`. Small on purpose: teardown never waits long.
STEP_TIMEOUT_S = 2.0


async def _tcp_probe(
    host: str,
    port: int = DEAKO_DEFAULT_PORT,
    timeout: float = PROBE_TIMEOUT,
) -> bool:
    """Bounded TCP reachability check. Module-private.

    Returns True iff a TCP connection to ``host:port`` could be
    established within ``timeout`` seconds. A successful TCP accept
    only proves the port is open—it does not guarantee the bridge is
    ready to speak the Deako protocol. Callers follow this with
    ``Deako.connect()`` + ``find_devices()`` (via ``_connect_primary``)
    as the real readiness gate.
    """
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout,
        )
    except (OSError, asyncio.TimeoutError):
        return False
    try:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
    except OSError:
        pass
    return True


class _KeepAliveSocket:
    """Raw TCP socket held open to a Deako bridge.

    A Deako bridge stays in WiFi-relay mode as long as some TCP
    connection is held open on port 23. The pool keeps one such
    socket open to the current standby bridge so the bridge is
    warm the instant a failover runs.

    `start()` and `stop()` are both awaitable. All pool-level
    callers wrap `stop()` in
    `asyncio.wait_for(..., timeout=STEP_TIMEOUT_S)` after
    checking that the pool's keepalive reference is non-None.
    """

    def __init__(
        self, host: str, port: int = DEAKO_DEFAULT_PORT,
    ) -> None:
        """Record target host and port without opening anything."""
        self.host = host
        self.port = port
        self._address = f"{host}:{port}"
        self._socket: _SocketConnection | None = None
        self._running = False

    async def start(self) -> None:
        """Open the keepalive socket.

        Raises OSError on connect failure. Pool-level callers treat
        that failure as non-fatal: log WARNING and proceed with
        `self._keepalive = None`.
        """
        loop = asyncio.get_running_loop()
        sock = _SocketConnection(self._address, loop)
        await sock.connect_socket()
        self._socket = sock
        self._running = True

    async def stop(self) -> None:
        """Close the keepalive socket. Always safe to await.

        Idempotent: a second call after the first is a no-op.
        Awaitable so pool-level callers can wrap the call in
        `asyncio.wait_for` with `STEP_TIMEOUT_S`. The inner
        `_SocketConnection.close_socket` is a synchronous system
        call with no waitable work today, but the async signature
        is part of the pool contract and lets the inner call add
        awaitable teardown later without changing call sites.
        """
        self._running = False
        if self._socket is not None:
            self._socket.close_socket()
            self._socket = None

    def is_running(self) -> bool:
        """Return True iff the keepalive is currently held open."""
        if not self._running or self._socket is None:
            return False
        return self._socket.sock is not None


@dataclass(frozen=True)
class ConnectionPoolState:
    """Immutable snapshot of the pool's state.

    Exactly five fields. Pull-only via `DeakoConnectionPool.state()`.
    `primary_host` and `failover_host` are `str` for the lifetime
    of the pool and are never set to None on any code path; degraded
    state is represented by the boolean flags.
    """

    primary_host: str
    failover_host: str
    primary_connected: bool
    failover_keepalive_active: bool
    started: bool


class DeakoConnectionPool:
    """Primary-failover connection pool for Deako bridges.

    Recovery is caller-driven: there is no background health monitor.
    A failed send on the active primary triggers exactly one
    `_switch_to_failover` attempt inside the same `control_device`
    call, plus one retry on the new active. Once the pool enters a
    degraded state (no connected active), the next `control_device`
    call runs `_attempt_recovery` which tries both hosts in
    deterministic order and raises `NoSocketException` with a
    host-annotated message on failure.

    `stop()` is terminal. Calling `start()` after `stop()` raises
    `RuntimeError`. `control_device()` after `stop()` raises
    `NoSocketException`.

    State callbacks registered via `set_state_callback` are stored
    on the pool itself and replayed onto the new active `Deako`
    after every successful switch or recovery, so user callbacks
    survive bridge failover without re-registration.
    """

    # pylint: disable=too-many-instance-attributes
    active: Deako | None

    def __init__(
        self,
        primary_host: str,
        failover_host: str,
        client_name: str | None = None,
        on_failover_switch: Callable[[str, str], None] | None = None,
    ) -> None:
        """Initialize the pool.

        Args:
            primary_host: IP address of the primary bridge. Required.
            failover_host: IP address of the failover bridge. Required;
                this PR does not support single-bridge pools (use
                `Deako` directly for that).
            client_name: Optional client name sent in protocol
                messages; forwarded to every `Deako` the pool owns.
            on_failover_switch: Optional sync callback invoked after
                a successful switch or recovery with the new primary
                host and the new failover host. Exceptions raised by
                the callback are logged at WARNING and swallowed so
                they never destabilize the pool.

        Concurrent `start()` calls on the same pool are unsupported:
        callers must serialize setup. Home Assistant's single-thread
        async setup satisfies this naturally.
        """
        if primary_host == failover_host:
            raise ValueError(
                "DeakoConnectionPool: primary_host and "
                "failover_host must be distinct; use Deako "
                "directly for single-bridge setups",
            )
        self.primary_host: str = primary_host
        self.failover_host: str = failover_host
        self._client_name = client_name
        self._on_failover_switch = on_failover_switch

        self.active: Deako | None = None
        self._keepalive: _KeepAliveSocket | None = None

        # Pool-owned callback registry. Replayed onto the new active
        # after every successful switch / recovery.
        self._state_callbacks: dict[str, Callable[[], None]] = {}

        # Switch serialization primitives.
        self._switch_lock: asyncio.Lock = asyncio.Lock()
        self._switch_event: asyncio.Event = asyncio.Event()
        self._switch_event.set()

        self._started: bool = False
        self._stopped: bool = False

        # Scratch reference for `_cleanup_partial_connect`, populated
        # by `_connect_primary` while a connect is in flight and
        # cleared on success. Defined in __init__ so pylint is happy
        # and the attribute exists even if a caller invokes
        # `_cleanup_partial_connect` before any connect attempt.
        self._partial_deako: Deako | None = None

        # Tasks scheduled by `_on_active_connection_lost` when the
        # Manager's ping-timeout fires on the active. Held so asyncio
        # does not GC them mid-switch; discarded on completion.
        self._on_lost_tasks: set[asyncio.Task] = set()

    # ----- Observability ---------------------------------------

    def state(self) -> ConnectionPoolState:
        """Return an immutable snapshot of pool state."""
        primary_connected = (
            self.active is not None and self.active.is_connected()
        )
        keepalive_active = (
            self._keepalive is not None
            and self._keepalive.is_running()
        )
        return ConnectionPoolState(
            primary_host=self.primary_host,
            failover_host=self.failover_host,
            primary_connected=primary_connected,
            failover_keepalive_active=keepalive_active,
            started=self._started and not self._stopped,
        )

    def is_connected(self) -> bool:
        """Return True iff the active primary is currently connected.

        Uses `Deako.is_connected()` so the pool does not reach
        through manager internals to check socket state.
        """
        return self.active is not None and self.active.is_connected()

    # ----- State callbacks -------------------------------------

    def set_state_callback(
        self, uuid: str, callback: Callable[[], None],
    ) -> None:
        """Register a sync state-change callback for a device.

        The callback is stored on the pool and automatically replayed
        onto the new active `Deako` after every successful failover
        switch or recovery, so user callbacks survive bridge swaps
        without re-registration. Callback shape matches
        `Deako.set_state_callback`: sync only, zero arguments.
        """
        self._state_callbacks[uuid] = callback
        if self.active is not None:
            self.active.set_state_callback(uuid, callback)

    def _replay_callbacks(self, deako: Deako) -> None:
        """Register all stored callbacks on a fresh `Deako`.

        Called on the success path of `_switch_to_failover` and
        `_attempt_recovery` so the new active picks up every
        previously-registered state-change listener.
        """
        for uuid, callback in self._state_callbacks.items():
            deako.set_state_callback(uuid, callback)

    # ----- Device accessors (proxied to active) ----------------

    def get_devices(self) -> dict:
        """Return known devices from the active connection, or {}."""
        if self.active is None:
            return {}
        return self.active.get_devices()

    def get_state(self, uuid: str) -> dict | None:
        """Get a device's state from the active connection."""
        if self.active is None:
            return None
        return self.active.get_state(uuid)

    def get_name(self, uuid: str) -> str | None:
        """Get a device's name from the active connection."""
        if self.active is None:
            return None
        return self.active.get_name(uuid)

    def is_dimmable(self, uuid: str) -> bool | None:
        """Return whether a device is dimmable, via active."""
        if self.active is None:
            return None
        return self.active.is_dimmable(uuid)

    # ----- Lifecycle -------------------------------------------

    async def start(self) -> None:
        """Connect to the primary and attach a warm standby.

        Fail-fast: raises `NoSocketException` if the primary cannot
        be reached. A best-effort `_KeepAliveSocket` is then started
        on `failover_host`; keepalive failure is non-fatal and leaves
        `self._keepalive = None`.

        Idempotent after first success: a subsequent `start()` on
        an already-started pool is a no-op. A failed initial
        `start()` leaves the pool unstarted (`_started=False`,
        `active=None`, `_keepalive=None`) so a later retry can
        succeed normally. `start()` after `stop()` raises
        `RuntimeError`; the pool is single-use.

        Concurrent `start()` calls are unsupported.
        """
        if self._stopped:
            raise RuntimeError(
                "DeakoConnectionPool: start() after stop() is not "
                "supported; construct a new pool",
            )
        if self._started:
            return
        # Connect primary first. Failure is fatal to start().
        try:
            new_active = await self._connect_primary(self.primary_host)
        except (OSError, NoSocketException) as exc:
            # Leave the pool unstarted so a retry may succeed.
            await self._cleanup_partial_connect()
            raise NoSocketException(
                f"start: primary {self.primary_host} unreachable: "
                f"{exc}",
            ) from exc
        # Post-await stopped re-check. If stop() ran while the long
        # connect was in flight, discard the freshly connected Deako
        # rather than installing it on a pool the caller has already
        # terminated.
        if await self._discard_if_stopped(new_active):
            return
        # Primary is live. Latch started BEFORE best-effort keepalive
        # so a keepalive failure does not un-set it.
        self.active = new_active
        self._started = True
        # Replay any callbacks that were registered before start().
        self._replay_callbacks(new_active)
        # Best-effort warm standby on failover_host.
        try:
            await self._start_keepalive(self.failover_host)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            _LOGGER.warning(
                "keepalive start on %s failed at start: %s; "
                "pool proceeds without warm standby",
                self.failover_host, exc,
            )
            self._keepalive = None

    async def stop(self) -> None:
        """Terminal shutdown. Re-entrant-safe.

        Sets `_stopped=True` first so any in-flight switch method
        sees it on its next await boundary and bails out. Sets
        `_switch_event` to unblock any waiters. Then tears down
        the keepalive and the active connection under
        `asyncio.wait_for(..., timeout=STEP_TIMEOUT_S)`. Each
        teardown is None-guarded because `stop()` may run before
        `start()` completed, after a failed recovery, or
        mid-partial-connect.
        """
        if self._stopped:
            return
        self._stopped = True
        # Release any waiters on the switch completion event.
        self._switch_event.set()
        # Cancel any switch task scheduled by a stale
        # `_on_active_connection_lost` firing. Tasks discard
        # themselves on completion so this collection only drains
        # those still in flight.
        for task in list(self._on_lost_tasks):
            task.cancel()
        self._on_lost_tasks.clear()
        if self._keepalive is not None:
            try:
                await asyncio.wait_for(
                    self._keepalive.stop(),
                    timeout=STEP_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                _LOGGER.warning(
                    "keepalive.stop() timed out after %ss; "
                    "proceeding",
                    STEP_TIMEOUT_S,
                )
            self._keepalive = None
        if self.active is not None:
            try:
                await asyncio.wait_for(
                    self.active.disconnect(),
                    timeout=STEP_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                _LOGGER.warning(
                    "active.disconnect() timed out after %ss; "
                    "proceeding",
                    STEP_TIMEOUT_S,
                )
            self.active = None

    # ----- Connect helpers -------------------------------------

    async def _connect_primary(self, host: str) -> Deako:
        """Open and populate a fresh `Deako` on ``host``.

        Used by `start()`, `_switch_to_failover`, and
        `_attempt_recovery`. Creates a `Deako`, awaits its
        connect, and calls `find_devices` to populate the device
        cache. Raises on any failure; the caller is responsible
        for calling `_cleanup_partial_connect` on the partial
        object if one was created. This method never mutates pool
        state beyond returning the new `Deako`; the caller
        installs it on `self.active` only after success.
        """
        port = DEAKO_DEFAULT_PORT
        address = f"{host}:{port}"
        client = self._client_name or "pydeako"

        async def get_address():
            return address, client

        deako = Deako(
            get_address,
            client_name=self._client_name,
            on_connection_lost=self._on_active_connection_lost,
        )
        # The pool owns reconnect via failover; disable the Manager's
        # built-in auto_reconnect so it cannot race the pool by
        # reopening the same host while a switch is in flight.
        deako.connection_manager.auto_reconnect = False
        # Track the in-progress object so a mid-connect failure can
        # be cleaned up. The caller invokes
        # `_cleanup_partial_connect()` on any raise to tear it down.
        self._partial_deako = deako
        await deako.connect()
        await deako.find_devices()
        self._partial_deako = None
        return deako

    async def _cleanup_partial_connect(self) -> None:
        """Tear down a half-initialized `Deako` from `_connect_primary`.

        Called from the caller of `_connect_primary` after any raise.
        Defensive None-guard: the partial reference may not exist
        (e.g. `start()` failed before `_connect_primary` was even
        called). Disconnect under `asyncio.wait_for` with
        `STEP_TIMEOUT_S`; swallow `TimeoutError` with the unified
        warning wording.
        """
        partial = self._partial_deako
        if partial is None:
            return
        self._partial_deako = None
        try:
            await asyncio.wait_for(
                partial.disconnect(), timeout=STEP_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            _LOGGER.warning(
                "active.disconnect() timed out after %ss; "
                "proceeding",
                STEP_TIMEOUT_S,
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            _LOGGER.debug(
                "partial-connect cleanup error on disconnect: %s",
                exc,
            )

    async def _start_keepalive(self, host: str) -> None:
        """Open a fresh `_KeepAliveSocket` to ``host``.

        Stores the result in `self._keepalive` on success. Raises
        on failure; all pool-level callers wrap the call in
        `try/except Exception` and set `self._keepalive = None`
        on failure.
        """
        keepalive = _KeepAliveSocket(host)
        await keepalive.start()
        self._keepalive = keepalive

    async def _wait_ready(
        self,
        host: str,
        timeout: float = BRIDGE_RECYCLE_TIMEOUT_S,
    ) -> bool:
        """Bounded poll: return True once the host accepts TCP.

        Used by `_switch_to_failover` step 9 to wait out the brief
        window where the failover bridge is transitioning from
        standby into active mode. Short-circuits on `_stopped`.
        """
        async def _poll() -> bool:
            while True:
                if self._stopped:
                    return False
                if await _tcp_probe(
                    host, DEAKO_DEFAULT_PORT, PROBE_TIMEOUT,
                ):
                    return True
                await asyncio.sleep(1.0)

        try:
            return await asyncio.wait_for(_poll(), timeout=timeout)
        except asyncio.TimeoutError:
            return False

    async def _discard_if_stopped(self, deako: Deako) -> bool:
        """If the pool is stopped, disconnect ``deako`` and return True.

        Used after an async connect returns to check whether stop()
        ran while the connect was in flight. If so, the freshly
        connected Deako is disconnected rather than installed.
        """
        if not self._stopped:
            return False
        try:
            await asyncio.wait_for(
                deako.disconnect(), timeout=STEP_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            _LOGGER.warning(
                "active.disconnect() timed out after %ss; "
                "proceeding",
                STEP_TIMEOUT_S,
            )
        return True

    async def _teardown_keepalive(self) -> None:
        """Stop and clear the keepalive socket with bounded timeout."""
        if self._keepalive is not None:
            try:
                await asyncio.wait_for(
                    self._keepalive.stop(),
                    timeout=STEP_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                _LOGGER.warning(
                    "keepalive.stop() timed out after %ss; "
                    "proceeding",
                    STEP_TIMEOUT_S,
                )
            self._keepalive = None

    async def _teardown_active(self) -> None:
        """Disconnect and clear the active Deako with bounded timeout."""
        if self.active is not None:
            try:
                await asyncio.wait_for(
                    self.active.disconnect(),
                    timeout=STEP_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                _LOGGER.warning(
                    "active.disconnect() timed out after %ss; "
                    "proceeding",
                    STEP_TIMEOUT_S,
                )
            self.active = None

    # ----- on_connection_lost hook -----------------------------

    def _on_active_connection_lost(self) -> None:
        """Sync callback wired onto every active `Deako`.

        Invoked by `_Manager.maintain_connection_worker` when the
        ping-timeout path fires. Schedules one
        `_switch_to_failover` task with `failed_host` set to the
        current primary. The concurrent-caller path inside
        `_switch_to_failover` handles re-entry if another switch
        is already running.
        """
        if self._stopped:
            return
        failed = self.primary_host
        try:
            task = asyncio.create_task(
                self._switch_to_failover(failed_host=failed),
            )
        except RuntimeError:
            # Called from a context with no running loop; this
            # can happen in edge cases during shutdown. Swallow
            # so the Manager worker is not destabilized by a
            # raise inside the sync callback.
            _LOGGER.debug(
                "no running loop when scheduling switch for %s; "
                "ignoring",
                failed,
            )
            return
        self._on_lost_tasks.add(task)
        task.add_done_callback(self._on_lost_tasks.discard)

    # ----- Switch and recovery ---------------------------------

    async def _wait_for_in_flight_switch(
        self, failed_host: str | None,
    ) -> bool | None:
        """Wait for an already-running switch to finish.

        Returns the result (bool) if a switch was in flight, or
        None if the lock was free and the caller should proceed.
        """
        if not self._switch_lock.locked():
            return None
        try:
            await asyncio.wait_for(
                self._switch_event.wait(),
                timeout=SWITCH_WAIT_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            return False
        if failed_host is None:
            return (
                self.active is not None
                and self.active.is_connected()
            )
        return self.primary_host != failed_host

    async def _execute_failover_switch(self) -> bool:
        """Core failover logic after guards pass. Lock must be held.

        Tears down the current keepalive and active, waits for the
        failover host to accept TCP, connects with retry, and
        installs the new active on success.
        """
        # Release the standby keepalive before connecting. The
        # bridge only accepts one TCP session, so the keepalive
        # must be dropped first or it blocks the real Deako
        # connection to the failover host.
        await self._teardown_keepalive()
        await self._teardown_active()
        if self._stopped:
            return False
        target = self.failover_host
        ready = await self._wait_ready(
            target, timeout=BRIDGE_RECYCLE_TIMEOUT_S,
        )
        if not ready:
            _LOGGER.warning(
                "switch: host_not_ready on %s", target,
            )
            return False
        new_active = await self._connect_with_retry(target)
        if new_active is None:
            _LOGGER.warning(
                "switch: connect_exhausted on %s", target,
            )
            return False
        if await self._discard_if_stopped(new_active):
            return False
        await self._install_new_active(
            target, new_active, "failover switch",
        )
        return True

    async def _switch_to_failover(
        self, failed_host: str | None,
    ) -> bool:
        """Switch the active connection to the failover bridge.

        Called from the `on_connection_lost` path and from the
        `control_device` hot-failover path with
        `failed_host = self.primary_host`.

        Returns True on success, False on any failure (host not
        ready, connect exhausted, stopped mid-flight). The host
        map is only mutated on the success branch.

        The concurrent-caller path waits on `_switch_event` up to
        `SWITCH_WAIT_TIMEOUT_S`; if `failed_host` is None, success
        is determined by actual connected state of the new active,
        otherwise by whether the primary has moved off
        `failed_host`.
        """
        in_flight = await self._wait_for_in_flight_switch(
            failed_host,
        )
        if in_flight is not None:
            return in_flight

        async with self._switch_lock:
            self._switch_event.clear()
            try:
                if (
                    failed_host is not None
                    and failed_host != self.primary_host
                ):
                    return True
                if self._stopped:
                    return False
                return await self._execute_failover_switch()
            finally:
                self._switch_event.set()

    async def _connect_with_retry(self, host: str) -> Deako | None:
        """Bounded connect loop used by switch and recovery.

        Tries up to `SWITCH_CONNECT_RETRIES + 1` times with
        `SWITCH_CONNECT_BACKOFF_S` between attempts. Each failed
        attempt runs `_cleanup_partial_connect` so we never leave
        a half-constructed Deako behind. Returns the new Deako on
        success, or None on exhaustion / stopped.
        """
        for attempt in range(SWITCH_CONNECT_RETRIES + 1):
            if self._stopped:
                return None
            try:
                return await self._connect_primary(host)
            except Exception:  # pylint: disable=broad-exception-caught
                await self._cleanup_partial_connect()
                if attempt == SWITCH_CONNECT_RETRIES:
                    return None
                await asyncio.sleep(SWITCH_CONNECT_BACKOFF_S)
        return None

    def _fire_on_failover_switch(self) -> None:
        """Invoke the user switch callback, swallowing errors.

        Sync-only. Callback errors are logged at
        WARNING so they never destabilize the pool.
        """
        if self._on_failover_switch is None:
            return
        try:
            self._on_failover_switch(
                self.primary_host, self.failover_host,
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            _LOGGER.warning(
                "on_failover_switch callback error: %s", exc,
            )

    async def _install_new_active(
        self, target: str, new_active: Deako, context: str,
    ) -> None:
        """Install a freshly connected Deako as the active.

        Swaps the host roles so ``target`` becomes the new primary,
        replays stored callbacks onto ``new_active``, and starts a
        best-effort keepalive on the other host. ``context`` labels
        log messages (e.g. ``"failover switch"`` or ``"recovery"``).
        """
        other = (
            self.failover_host
            if target == self.primary_host
            else self.primary_host
        )
        self.primary_host = target
        self.failover_host = other
        self.active = new_active
        self._replay_callbacks(new_active)
        try:
            await self._start_keepalive(other)
        # pylint: disable-next=broad-exception-caught
        except Exception as exc:
            _LOGGER.warning(
                "keepalive start on %s failed after "
                "%s: %s; pool proceeds without warm "
                "standby",
                other, context, exc,
            )
            self._keepalive = None
        _LOGGER.info(
            "%s succeeded: primary=%s failover=%s",
            context, self.primary_host, self.failover_host,
        )
        self._fire_on_failover_switch()

    def _fill_both_reasons(
        self, token: str,
    ) -> dict[str, str]:
        """Map both hosts to the same reason token."""
        return {
            self.primary_host: token,
            self.failover_host: token,
        }

    async def _wait_for_in_flight_recovery(
        self,
    ) -> tuple[bool, dict[str, str]] | None:
        """Wait for an already-running switch/recovery to finish.

        Returns ``(bool, reasons)`` if a switch was in flight, or
        None if the lock was free and the caller should proceed.
        """
        if not self._switch_lock.locked():
            return None
        try:
            await asyncio.wait_for(
                self._switch_event.wait(),
                timeout=SWITCH_WAIT_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            return False, self._fill_both_reasons(
                "switch_wait_timeout",
            )
        if (
            self.active is not None
            and self.active.is_connected()
        ):
            return True, {}
        return False, self._fill_both_reasons(
            "in_flight_switch_failed",
        )

    async def _try_recovery_candidate(
        self, target: str,
    ) -> bool | str:
        """Probe and connect to a single candidate during recovery.

        Returns True on success (new active installed), False if the
        pool was stopped mid-attempt, or a reason-token string on
        failure (``"tcp_probe_failed"`` or ``"connect_exhausted"``).
        """
        if not await _tcp_probe(
            target, DEAKO_DEFAULT_PORT, PROBE_TIMEOUT,
        ):
            return "tcp_probe_failed"
        new_deako = await self._connect_with_retry(target)
        if new_deako is None:
            if self._stopped:
                return False
            return "connect_exhausted"
        if await self._discard_if_stopped(new_deako):
            return False
        await self._install_new_active(
            target, new_deako, "recovery",
        )
        return True

    async def _attempt_recovery(
        self,
    ) -> tuple[bool, dict[str, str]]:
        """Two-host recovery path used only by the no-primary send.

        Returns `(True, {})` on success and `(False, reasons)` on
        failure, where `reasons` maps each host to its last reason
        token (`tcp_probe_failed`, `connect_exhausted`, `stopped`,
        `switch_wait_timeout`, or `in_flight_switch_failed`).
        `_ensure_primary_or_raise` uses the reasons dict to build
        the host-annotated `NoSocketException` message.

        Probe order is deterministic: `self.primary_host` first,
        `self.failover_host` second. The winner becomes the new
        active; the loser becomes the warm standby.
        """
        in_flight = await self._wait_for_in_flight_recovery()
        if in_flight is not None:
            return in_flight

        async with self._switch_lock:
            self._switch_event.clear()
            try:
                if self._stopped:
                    return False, self._fill_both_reasons("stopped")
                # Disconnect stale active and release keepalive
                # before probing. The bridge only accepts one TCP
                # session, so recovery must not fight its own
                # sockets.
                await self._teardown_active()
                await self._teardown_keepalive()
                reasons: dict[str, str] = {}
                for target in [
                    self.primary_host, self.failover_host,
                ]:
                    if self._stopped:
                        return (
                            False,
                            self._fill_both_reasons("stopped"),
                        )
                    result = await self._try_recovery_candidate(
                        target,
                    )
                    if result is True:
                        return True, {}
                    if result is False:
                        return (
                            False,
                            self._fill_both_reasons("stopped"),
                        )
                    reasons[target] = result
                return False, reasons
            finally:
                self._switch_event.set()

    # ----- control_device send path ----------------------------

    async def _ensure_primary_or_raise(self) -> None:
        """If no connected primary, run recovery.

        Raises `NoSocketException` with a host-annotated message
        on recovery failure. Called from `control_device` when
        `self.active` is None or not connected.
        """
        if self._stopped:
            raise NoSocketException("pool stopped")
        if (
            self.active is not None
            and self.active.is_connected()
        ):
            return
        success, reasons = await self._attempt_recovery()
        if success:
            return
        msg = (
            f"no primary after recovery: "
            f"tried primary={self.primary_host} "
            f"({reasons.get(self.primary_host, 'unknown')}), "
            f"failover={self.failover_host} "
            f"({reasons.get(self.failover_host, 'unknown')})"
        )
        _LOGGER.error(msg)
        raise NoSocketException(msg)

    async def control_device(
        self, uuid: str, power: bool, dim: int | None = None,
    ) -> None:
        """Send a device control command through the active pool.

        The pool calls `Deako._control_device_strict` (not the
        public `control_device`) so send failures raise instead
        of being swallowed, letting the pool drive failover.

        On `OSError` or `NoSocketException` from the active send,
        performs exactly one `_switch_to_failover(failed_host=
        self.primary_host)` and exactly one retry of the strict
        send on the new active. If the switch returns False, or
        the retry raises, raises `NoSocketException` naming the
        host(s) attempted. Does not cascade into
        `_attempt_recovery`; the next call enters the no-primary
        path and runs recovery naturally.
        """
        if self._stopped:
            raise NoSocketException("pool stopped")
        # No connected primary -> run recovery.
        if (
            self.active is None
            or not self.active.is_connected()
        ):
            await self._ensure_primary_or_raise()
        # Hot path: send via strict.
        assert self.active is not None
        failed_host = self.primary_host
        try:
            # pylint: disable-next=protected-access
            await self.active._control_device_strict(
                uuid, power, dim,
            )
            return
        except OSError:
            # NoSocketException subclasses OSError.
            _LOGGER.warning(
                "control_device send failed on active primary "
                "%s; switching to failover",
                failed_host,
            )
        # Single switch attempt.
        switched = await self._switch_to_failover(
            failed_host=failed_host,
        )
        if not switched:
            msg = (
                f"control_device failed and failover switch "
                f"failed; last active host was {failed_host}"
            )
            _LOGGER.error(msg)
            raise NoSocketException(msg)
        # Exactly one retry on the new active.
        new_host = self.primary_host
        assert self.active is not None
        try:
            # pylint: disable-next=protected-access
            await self.active._control_device_strict(
                uuid, power, dim,
            )
        except OSError as exc:
            msg = (
                f"control_device failed twice across failover: "
                f"first active={failed_host}, "
                f"second active={new_host}: {exc}"
            )
            _LOGGER.error(msg)
            raise NoSocketException(msg) from exc
