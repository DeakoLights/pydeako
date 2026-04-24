"""Tests for DeakoConnectionPool scaffold, lifecycle, switch,
recovery, and control_device paths.

Covers phase 6 of the failover work: module constants, `_tcp_probe`,
`_KeepAliveSocket`, `ConnectionPoolState`, pool `__init__`, `state()`,
`is_connected()`, `set_state_callback`, device accessors, `start()`
fail-fast and keepalive best-effort, terminal re-entrant `stop()`,
host-name invariants, `_switch_to_failover`, `_attempt_recovery`,
and the section 7.4/7.5 `control_device` paths.
"""
# pylint: disable=too-many-lines

import asyncio
import dataclasses

import pytest
from mock import AsyncMock, MagicMock, Mock, patch

from ._connection_pool import (
    BRIDGE_RECYCLE_TIMEOUT_S,
    ConnectionPoolState,
    DEAKO_DEFAULT_PORT,
    DeakoConnectionPool,
    PROBE_TIMEOUT,
    STEP_TIMEOUT_S,
    SWITCH_CONNECT_BACKOFF_S,
    SWITCH_CONNECT_RETRIES,
    SWITCH_WAIT_TIMEOUT_S,
    _KeepAliveSocket,
    _tcp_probe,
)
from .utils._socket import NoSocketException


# --- module constants ----------------------------------------------

def test_module_constants_are_concrete():
    """Module defines every constant used by the switch contracts."""
    assert DEAKO_DEFAULT_PORT == 23
    assert SWITCH_WAIT_TIMEOUT_S > 0
    assert SWITCH_CONNECT_RETRIES == 3
    assert SWITCH_CONNECT_BACKOFF_S == 1.0
    assert BRIDGE_RECYCLE_TIMEOUT_S > 0
    assert PROBE_TIMEOUT > 0
    assert STEP_TIMEOUT_S == 2.0


# --- _tcp_probe ----------------------------------------------------

@pytest.mark.asyncio
async def test_tcp_probe_returns_true_on_open_connection():
    """_tcp_probe succeeds when open_connection returns cleanly."""
    writer = MagicMock()
    writer.close = MagicMock()
    writer.wait_closed = AsyncMock()
    reader = MagicMock()
    with patch(
        "pydeako.deako._connection_pool.asyncio.open_connection",
        new=AsyncMock(return_value=(reader, writer)),
    ):
        result = await _tcp_probe("10.0.0.1")
    assert result is True
    writer.close.assert_called_once()


@pytest.mark.asyncio
async def test_tcp_probe_returns_false_on_oserror():
    """_tcp_probe returns False when open_connection raises OSError."""
    with patch(
        "pydeako.deako._connection_pool.asyncio.open_connection",
        new=AsyncMock(side_effect=OSError("refused")),
    ):
        result = await _tcp_probe("10.0.0.1")
    assert result is False


@pytest.mark.asyncio
async def test_tcp_probe_returns_false_on_timeout():
    """_tcp_probe returns False when wait_for times out."""
    async def slow_open(*_args, **_kwargs):
        await asyncio.sleep(10)
    with patch(
        "pydeako.deako._connection_pool.asyncio.open_connection",
        new=slow_open,
    ):
        result = await _tcp_probe("10.0.0.1", timeout=0.01)
    assert result is False


# --- _KeepAliveSocket ----------------------------------------------

def test_keepalive_init_records_host_and_port():
    """KeepAlive stores target coordinates without opening anything."""
    ka = _KeepAliveSocket("10.0.0.1")
    assert ka.host == "10.0.0.1"
    assert ka.port == DEAKO_DEFAULT_PORT
    assert ka.is_running() is False


@pytest.mark.asyncio
async def test_keepalive_start_opens_socket_and_marks_running():
    """start() opens the inner socket and flips is_running True."""
    ka = _KeepAliveSocket("10.0.0.1")
    fake_sock = MagicMock()
    fake_sock.connect_socket = AsyncMock()
    fake_sock.sock = MagicMock()  # non-None after connect_socket
    with patch(
        "pydeako.deako._connection_pool._SocketConnection",
        return_value=fake_sock,
    ):
        await ka.start()
    fake_sock.connect_socket.assert_awaited_once()
    assert ka.is_running() is True


@pytest.mark.asyncio
async def test_keepalive_start_propagates_oserror():
    """start() re-raises OSError from connect_socket."""
    ka = _KeepAliveSocket("10.0.0.1")
    fake_sock = MagicMock()
    fake_sock.connect_socket = AsyncMock(side_effect=OSError("boom"))
    with patch(
        "pydeako.deako._connection_pool._SocketConnection",
        return_value=fake_sock,
    ):
        with pytest.raises(OSError):
            await ka.start()
    assert ka.is_running() is False


@pytest.mark.asyncio
async def test_keepalive_stop_is_awaitable():
    """stop() is a coroutine; awaiting it closes the inner socket."""
    ka = _KeepAliveSocket("10.0.0.1")
    inner = MagicMock()
    ka._socket = inner  # pylint: disable=protected-access
    ka._running = True  # pylint: disable=protected-access
    coro = ka.stop()
    assert asyncio.iscoroutine(coro)
    await coro
    inner.close_socket.assert_called_once()
    assert ka.is_running() is False


@pytest.mark.asyncio
async def test_keepalive_stop_is_idempotent():
    """Second stop() is a no-op."""
    ka = _KeepAliveSocket("10.0.0.1")
    await ka.stop()
    await ka.stop()
    assert ka.is_running() is False


# --- ConnectionPoolState -------------------------------------------

def test_connection_pool_state_fields():
    """State has exactly the five fields in decision 23, no more."""
    field_names = {f.name for f in dataclasses.fields(ConnectionPoolState)}
    assert field_names == {
        "primary_host",
        "failover_host",
        "primary_connected",
        "failover_keepalive_active",
        "started",
    }


def test_connection_pool_state_is_frozen():
    """ConnectionPoolState is frozen: assignment raises."""
    s = ConnectionPoolState(
        primary_host="10.0.0.1",
        failover_host="10.0.0.2",
        primary_connected=False,
        failover_keepalive_active=False,
        started=False,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.primary_connected = True  # type: ignore[misc]


# --- Pool __init__ and state ---------------------------------------

def test_pool_init_stores_hosts_and_starts_unstarted():
    """Init holds both host names and switch_event begins set."""
    pool = DeakoConnectionPool(
        primary_host="10.0.0.1", failover_host="10.0.0.2",
    )
    assert pool.primary_host == "10.0.0.1"
    assert pool.failover_host == "10.0.0.2"
    assert pool.active is None
    # pylint: disable-next=protected-access
    assert pool._keepalive is None
    # pylint: disable-next=protected-access
    assert pool._started is False
    # pylint: disable-next=protected-access
    assert pool._stopped is False


def test_switch_event_set_on_init():
    """`_switch_event` starts set so pre-switch waiters do not block."""
    pool = DeakoConnectionPool(
        primary_host="10.0.0.1", failover_host="10.0.0.2",
    )
    # pylint: disable-next=protected-access
    assert pool._switch_event.is_set() is True


def test_state_pre_start_reports_degraded():
    """Before start(), state shows unconnected primary and no keepalive."""
    pool = DeakoConnectionPool(
        primary_host="10.0.0.1", failover_host="10.0.0.2",
    )
    s = pool.state()
    assert s.primary_host == "10.0.0.1"
    assert s.failover_host == "10.0.0.2"
    assert s.primary_connected is False
    assert s.failover_keepalive_active is False
    assert s.started is False


def test_is_connected_false_with_no_active():
    """is_connected() is False when the pool has no active Deako."""
    pool = DeakoConnectionPool(
        primary_host="10.0.0.1", failover_host="10.0.0.2",
    )
    assert pool.is_connected() is False


def test_is_connected_uses_deako_is_connected():
    """is_connected() delegates to Deako.is_connected (decision 21)."""
    pool = DeakoConnectionPool(
        primary_host="10.0.0.1", failover_host="10.0.0.2",
    )
    active = MagicMock()
    active.is_connected.return_value = True
    pool.active = active
    assert pool.is_connected() is True
    active.is_connected.assert_called_once()


# --- set_state_callback --------------------------------------------

def test_set_state_callback_stores_on_pool():
    """Callbacks are stored in the pool registry."""
    pool = DeakoConnectionPool(
        primary_host="10.0.0.1", failover_host="10.0.0.2",
    )
    cb = Mock()
    pool.set_state_callback("uuid-a", cb)
    # pylint: disable-next=protected-access
    assert pool._state_callbacks["uuid-a"] is cb


def test_set_state_callback_forwards_to_active_if_present():
    """When active is set, set_state_callback also forwards to it."""
    pool = DeakoConnectionPool(
        primary_host="10.0.0.1", failover_host="10.0.0.2",
    )
    active = MagicMock()
    pool.active = active
    cb = Mock()
    pool.set_state_callback("uuid-a", cb)
    active.set_state_callback.assert_called_once_with("uuid-a", cb)


def test_replay_callbacks_registers_all_on_new_deako():
    """Replay installs every stored callback onto the new Deako."""
    pool = DeakoConnectionPool(
        primary_host="10.0.0.1", failover_host="10.0.0.2",
    )
    cb_a = Mock()
    cb_b = Mock()
    pool.set_state_callback("uuid-a", cb_a)
    pool.set_state_callback("uuid-b", cb_b)
    new_deako = MagicMock()
    # pylint: disable-next=protected-access
    pool._replay_callbacks(new_deako)
    assert new_deako.set_state_callback.call_count >= 2


# --- Device accessor proxies ---------------------------------------

def test_get_devices_empty_when_no_active():
    """get_devices returns {} when no active Deako."""
    pool = DeakoConnectionPool(
        primary_host="10.0.0.1", failover_host="10.0.0.2",
    )
    assert pool.get_devices() == {}


def test_get_devices_proxies_to_active():
    """get_devices proxies to the active connection."""
    pool = DeakoConnectionPool(
        primary_host="10.0.0.1", failover_host="10.0.0.2",
    )
    active = MagicMock()
    active.get_devices.return_value = {"u": {}}
    pool.active = active
    assert pool.get_devices() == {"u": {}}


def test_get_state_and_name_and_dimmable_proxy():
    """get_state / get_name / is_dimmable proxy to active."""
    pool = DeakoConnectionPool(
        primary_host="10.0.0.1", failover_host="10.0.0.2",
    )
    assert pool.get_state("u") is None
    assert pool.get_name("u") is None
    assert pool.is_dimmable("u") is None
    active = MagicMock()
    active.get_state.return_value = {"power": True, "dim": 50}
    active.get_name.return_value = "Kitchen"
    active.is_dimmable.return_value = True
    pool.active = active
    assert pool.get_state("u") == {"power": True, "dim": 50}
    assert pool.get_name("u") == "Kitchen"
    assert pool.is_dimmable("u") is True


# --- start() -------------------------------------------------------

def _fake_deako_connected(connected: bool = True) -> MagicMock:
    """Build a MagicMock that looks like a live Deako for the pool."""
    deako = MagicMock()
    deako.is_connected.return_value = connected
    deako.connect = AsyncMock()
    deako.find_devices = AsyncMock()
    deako.disconnect = AsyncMock()
    deako.set_state_callback = MagicMock()
    deako.get_devices.return_value = {}
    # Pool uses strict (decision 29); make it awaitable by default.
    # pylint: disable-next=protected-access
    deako._control_device_strict = AsyncMock()
    # Mirror the connection_manager attribute used in
    # _connect_primary when wiring auto_reconnect off.
    deako.connection_manager = MagicMock()
    deako.connection_manager.auto_reconnect = True
    return deako


@pytest.mark.asyncio
async def test_start_connects_primary_and_starts_keepalive():
    """Happy path: primary connects, keepalive on failover starts."""
    pool = DeakoConnectionPool(
        primary_host="10.0.0.1", failover_host="10.0.0.2",
    )
    new_active = _fake_deako_connected()
    with patch(
        "pydeako.deako._connection_pool.Deako",
        return_value=new_active,
    ):
        with patch.object(
            _KeepAliveSocket, "start", new=AsyncMock(),
        ):
            await pool.start()
    assert pool.active is new_active
    # pylint: disable-next=protected-access
    assert pool._started is True
    s = pool.state()
    assert s.started is True
    assert s.primary_connected is True


@pytest.mark.asyncio
async def test_start_raises_when_primary_unreachable():
    """Fail-fast: primary connect raises and pool stays unstarted."""
    pool = DeakoConnectionPool(
        primary_host="10.0.0.1", failover_host="10.0.0.2",
    )
    broken = _fake_deako_connected(connected=False)
    broken.connect = AsyncMock(side_effect=OSError("no route"))
    with patch(
        "pydeako.deako._connection_pool.Deako",
        return_value=broken,
    ):
        with pytest.raises(NoSocketException):
            await pool.start()
    assert pool.active is None
    # pylint: disable-next=protected-access
    assert pool._started is False
    # pylint: disable-next=protected-access
    assert pool._keepalive is None


@pytest.mark.asyncio
async def test_failed_start_leaves_pool_unstarted():
    """Failed start leaves _started False, active None, keepalive None."""
    pool = DeakoConnectionPool(
        primary_host="10.0.0.1", failover_host="10.0.0.2",
    )
    broken = _fake_deako_connected(connected=False)
    broken.find_devices = AsyncMock(
        side_effect=NoSocketException("no socket"),
    )
    with patch(
        "pydeako.deako._connection_pool.Deako",
        return_value=broken,
    ):
        with pytest.raises(NoSocketException):
            await pool.start()
    s = pool.state()
    assert s.started is False
    assert s.primary_connected is False
    assert s.failover_keepalive_active is False


@pytest.mark.asyncio
async def test_start_retries_after_failed_start():
    """A second start() after a failed first attempt may succeed."""
    pool = DeakoConnectionPool(
        primary_host="10.0.0.1", failover_host="10.0.0.2",
    )
    bad = _fake_deako_connected(connected=False)
    bad.connect = AsyncMock(side_effect=OSError("down"))
    good = _fake_deako_connected()
    calls = {"n": 0}

    def factory(*_args, **_kwargs):
        calls["n"] += 1
        return bad if calls["n"] == 1 else good

    with patch(
        "pydeako.deako._connection_pool.Deako", side_effect=factory,
    ):
        with pytest.raises(NoSocketException):
            await pool.start()
        with patch.object(
            _KeepAliveSocket, "start", new=AsyncMock(),
        ):
            await pool.start()
    # pylint: disable-next=protected-access
    assert pool._started is True
    assert pool.state().started is True


@pytest.mark.asyncio
async def test_start_is_idempotent():
    """A second start() on a running pool is a no-op."""
    pool = DeakoConnectionPool(
        primary_host="10.0.0.1", failover_host="10.0.0.2",
    )
    active = _fake_deako_connected()
    with patch(
        "pydeako.deako._connection_pool.Deako", return_value=active,
    ):
        with patch.object(
            _KeepAliveSocket, "start", new=AsyncMock(),
        ) as ka_start:
            await pool.start()
            await pool.start()
    # Deako factory may be called only during the first start; no
    # second keepalive.start coroutine on the second call.
    assert ka_start.await_count == 1


@pytest.mark.asyncio
async def test_start_succeeds_when_keepalive_start_fails():
    """Keepalive failure at start is non-fatal (decision 27)."""
    pool = DeakoConnectionPool(
        primary_host="10.0.0.1", failover_host="10.0.0.2",
    )
    active = _fake_deako_connected()
    with patch(
        "pydeako.deako._connection_pool.Deako", return_value=active,
    ):
        with patch.object(
            _KeepAliveSocket,
            "start",
            new=AsyncMock(side_effect=OSError("kaput")),
        ):
            await pool.start()
    # pylint: disable-next=protected-access
    assert pool._started is True
    # pylint: disable-next=protected-access
    assert pool._keepalive is None
    s = pool.state()
    assert s.primary_connected is True
    assert s.failover_keepalive_active is False


@pytest.mark.asyncio
async def test_state_reflects_degraded_keepalive_after_failure():
    """After a keepalive-start failure, state flags are correct."""
    pool = DeakoConnectionPool(
        primary_host="10.0.0.1", failover_host="10.0.0.2",
    )
    active = _fake_deako_connected()
    with patch(
        "pydeako.deako._connection_pool.Deako", return_value=active,
    ):
        with patch.object(
            _KeepAliveSocket,
            "start",
            new=AsyncMock(side_effect=OSError("kaput")),
        ):
            await pool.start()
    s = pool.state()
    assert s.primary_connected is True
    assert s.failover_keepalive_active is False
    assert s.started is True


# --- stop() --------------------------------------------------------

@pytest.mark.asyncio
async def test_stop_before_start_is_safe():
    """stop() on a pool that never started completes without raising."""
    pool = DeakoConnectionPool(
        primary_host="10.0.0.1", failover_host="10.0.0.2",
    )
    await pool.stop()
    # pylint: disable-next=protected-access
    assert pool._stopped is True


@pytest.mark.asyncio
async def test_stop_is_reentrant():
    """A second stop() is a no-op (no double-disconnect)."""
    pool = DeakoConnectionPool(
        primary_host="10.0.0.1", failover_host="10.0.0.2",
    )
    active = _fake_deako_connected()
    pool.active = active
    # pylint: disable-next=protected-access
    pool._started = True
    await pool.stop()
    await pool.stop()
    assert active.disconnect.await_count == 1


@pytest.mark.asyncio
async def test_stop_releases_event_waiters():
    """Tasks parked on _switch_event.wait() wake after stop()."""
    pool = DeakoConnectionPool(
        primary_host="10.0.0.1", failover_host="10.0.0.2",
    )
    # pylint: disable-next=protected-access
    pool._switch_event.clear()

    async def waiter():
        # pylint: disable-next=protected-access
        await pool._switch_event.wait()
        return "awake"

    task = asyncio.create_task(waiter())
    await asyncio.sleep(0)  # let waiter park
    await pool.stop()
    result = await asyncio.wait_for(task, timeout=1.0)
    assert result == "awake"


@pytest.mark.asyncio
async def test_stop_tears_down_keepalive_and_active():
    """stop() disconnects active and stops keepalive."""
    pool = DeakoConnectionPool(
        primary_host="10.0.0.1", failover_host="10.0.0.2",
    )
    active = _fake_deako_connected()
    pool.active = active
    # pylint: disable-next=protected-access
    pool._started = True
    keepalive = MagicMock()
    keepalive.stop = AsyncMock()
    # pylint: disable-next=protected-access
    pool._keepalive = keepalive
    await pool.stop()
    active.disconnect.assert_awaited_once()
    keepalive.stop.assert_awaited_once()
    assert pool.active is None
    # pylint: disable-next=protected-access
    assert pool._keepalive is None


@pytest.mark.asyncio
async def test_stop_swallows_keepalive_stop_timeout():
    """Hung keepalive.stop() is bounded by STEP_TIMEOUT_S and swallowed."""
    pool = DeakoConnectionPool(
        primary_host="10.0.0.1", failover_host="10.0.0.2",
    )

    async def hang():
        await asyncio.sleep(30)

    keepalive = MagicMock()
    keepalive.stop = hang
    # pylint: disable-next=protected-access
    pool._keepalive = keepalive
    with patch(
        "pydeako.deako._connection_pool.asyncio.wait_for",
        new=AsyncMock(side_effect=asyncio.TimeoutError()),
    ):
        await pool.stop()
    # pylint: disable-next=protected-access
    assert pool._stopped is True
    # pylint: disable-next=protected-access
    assert pool._keepalive is None


@pytest.mark.asyncio
async def test_stop_swallows_active_disconnect_timeout():
    """Hung active.disconnect() is bounded and swallowed."""
    pool = DeakoConnectionPool(
        primary_host="10.0.0.1", failover_host="10.0.0.2",
    )
    active = _fake_deako_connected()
    pool.active = active
    # pylint: disable-next=protected-access
    pool._started = True
    with patch(
        "pydeako.deako._connection_pool.asyncio.wait_for",
        new=AsyncMock(side_effect=asyncio.TimeoutError()),
    ):
        await pool.stop()
    assert pool.active is None


@pytest.mark.asyncio
async def test_start_after_stop_raises():
    """start() after stop() raises RuntimeError (pool is single-use)."""
    pool = DeakoConnectionPool(
        primary_host="10.0.0.1", failover_host="10.0.0.2",
    )
    await pool.stop()
    with pytest.raises(RuntimeError):
        await pool.start()


# --- Host-name invariants ------------------------------------------

@pytest.mark.asyncio
async def test_host_names_never_none_through_failure_paths():
    """Failed start() path does not null out the host fields."""
    pool = DeakoConnectionPool(
        primary_host="10.0.0.1", failover_host="10.0.0.2",
    )
    broken = _fake_deako_connected(connected=False)
    broken.connect = AsyncMock(side_effect=OSError("down"))
    with patch(
        "pydeako.deako._connection_pool.Deako", return_value=broken,
    ):
        with pytest.raises(NoSocketException):
            await pool.start()
    s = pool.state()
    assert isinstance(s.primary_host, str) and s.primary_host
    assert isinstance(s.failover_host, str) and s.failover_host


@pytest.mark.asyncio
async def test_host_names_never_none_after_stop():
    """stop() preserves both host names on state()."""
    pool = DeakoConnectionPool(
        primary_host="10.0.0.1", failover_host="10.0.0.2",
    )
    await pool.stop()
    s = pool.state()
    assert s.primary_host == "10.0.0.1"
    assert s.failover_host == "10.0.0.2"


# --- Helpers for switch / recovery tests ---------------------------

def _started_pool(
    primary: str = "10.0.0.1",
    failover: str = "10.0.0.2",
    active_connected: bool = True,
) -> tuple[DeakoConnectionPool, MagicMock, MagicMock]:
    """Build a pool in the started state without running start()."""
    pool = DeakoConnectionPool(
        primary_host=primary, failover_host=failover,
    )
    active = _fake_deako_connected(connected=active_connected)
    pool.active = active
    # pylint: disable-next=protected-access
    pool._started = True
    keepalive = MagicMock()
    keepalive.stop = AsyncMock()
    keepalive.is_running.return_value = True
    # pylint: disable-next=protected-access
    pool._keepalive = keepalive
    return pool, active, keepalive


# --- _switch_event lifecycle ---------------------------------------

@pytest.mark.asyncio
async def test_switch_event_set_after_success():
    """Event is re-set after a successful _switch_to_failover."""
    pool, _active, _ka = _started_pool()
    new_active = _fake_deako_connected()
    with patch.object(pool, "_wait_ready", AsyncMock(return_value=True)):
        with patch.object(
            pool, "_connect_primary",
            AsyncMock(return_value=new_active),
        ):
            with patch.object(
                pool, "_start_keepalive", AsyncMock(),
            ):
                # pylint: disable-next=protected-access
                ok = await pool._switch_to_failover(
                    failed_host="10.0.0.1",
                )
    assert ok is True
    # pylint: disable-next=protected-access
    assert pool._switch_event.is_set() is True


@pytest.mark.asyncio
async def test_switch_event_set_after_failure():
    """Event is re-set after a switch failure (host_not_ready)."""
    pool, _active, _ka = _started_pool()
    with patch.object(
        pool, "_wait_ready", AsyncMock(return_value=False),
    ):
        # pylint: disable-next=protected-access
        ok = await pool._switch_to_failover(failed_host="10.0.0.1")
    assert ok is False
    # pylint: disable-next=protected-access
    assert pool._switch_event.is_set() is True


@pytest.mark.asyncio
async def test_switch_event_set_after_exception():
    """Event is re-set even if the switch body raises."""
    pool, _active, _ka = _started_pool()

    async def boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    with patch.object(pool, "_wait_ready", boom):
        with pytest.raises(RuntimeError):
            # pylint: disable-next=protected-access
            await pool._switch_to_failover(failed_host="10.0.0.1")
    # pylint: disable-next=protected-access
    assert pool._switch_event.is_set() is True


@pytest.mark.asyncio
async def test_switch_event_set_on_stop():
    """stop() sets _switch_event so parked waiters wake promptly."""
    pool = DeakoConnectionPool(
        primary_host="10.0.0.1", failover_host="10.0.0.2",
    )
    # pylint: disable-next=protected-access
    pool._switch_event.clear()
    await pool.stop()
    # pylint: disable-next=protected-access
    assert pool._switch_event.is_set() is True


# --- _switch_to_failover direct paths ------------------------------

@pytest.mark.asyncio
async def test_switch_success_swaps_hosts():
    """A successful switch swaps primary_host with failover_host."""
    pool, _active, _ka = _started_pool(
        primary="10.0.0.1", failover="10.0.0.2",
    )
    new_active = _fake_deako_connected()
    with patch.object(pool, "_wait_ready", AsyncMock(return_value=True)):
        with patch.object(
            pool, "_connect_primary",
            AsyncMock(return_value=new_active),
        ):
            with patch.object(
                pool, "_start_keepalive", AsyncMock(),
            ):
                # pylint: disable-next=protected-access
                ok = await pool._switch_to_failover(
                    failed_host="10.0.0.1",
                )
    assert ok is True
    assert pool.primary_host == "10.0.0.2"
    assert pool.failover_host == "10.0.0.1"
    assert pool.active is new_active


@pytest.mark.asyncio
async def test_switch_failure_preserves_host_mapping():
    """A failed switch leaves the host map untouched."""
    pool, _active, _ka = _started_pool(
        primary="10.0.0.1", failover="10.0.0.2",
    )
    with patch.object(pool, "_wait_ready", AsyncMock(return_value=False)):
        # pylint: disable-next=protected-access
        ok = await pool._switch_to_failover(failed_host="10.0.0.1")
    assert ok is False
    assert pool.primary_host == "10.0.0.1"
    assert pool.failover_host == "10.0.0.2"


@pytest.mark.asyncio
async def test_switch_retry_exhaustion():
    """Connect-with-retry exhausts and the switch returns False."""
    pool, _active, _ka = _started_pool()
    with patch.object(pool, "_wait_ready", AsyncMock(return_value=True)):
        with patch.object(
            pool, "_connect_primary",
            AsyncMock(side_effect=OSError("nope")),
        ):
            with patch.object(
                pool, "_cleanup_partial_connect", AsyncMock(),
            ):
                # Fast-forward the retry backoff sleeps.
                with patch(
                    "pydeako.deako._connection_pool.asyncio.sleep",
                    new=AsyncMock(),
                ):
                    # pylint: disable-next=protected-access
                    ok = await pool._switch_to_failover(
                        failed_host="10.0.0.1",
                    )
    assert ok is False


@pytest.mark.asyncio
async def test_switch_stops_target_keepalive_before_probe():
    """Keepalive.stop() is awaited before _wait_ready runs."""
    pool, _active, keepalive = _started_pool()
    order: list[str] = []

    async def fake_ka_stop():
        order.append("keepalive.stop")

    async def fake_wait_ready(_host, **_kwargs):
        order.append("wait_ready")
        return False

    keepalive.stop = fake_ka_stop
    with patch.object(pool, "_wait_ready", fake_wait_ready):
        # pylint: disable-next=protected-access
        await pool._switch_to_failover(failed_host="10.0.0.1")
    assert order[0] == "keepalive.stop"
    assert "wait_ready" in order


@pytest.mark.asyncio
async def test_switch_disconnects_old_primary_before_probe():
    """active.disconnect() runs before _wait_ready is called."""
    pool, active, _keepalive = _started_pool()
    order: list[str] = []

    async def fake_disconnect():
        order.append("active.disconnect")

    async def fake_wait_ready(_host, **_kwargs):
        order.append("wait_ready")
        return False

    active.disconnect = fake_disconnect
    with patch.object(pool, "_wait_ready", fake_wait_ready):
        # pylint: disable-next=protected-access
        await pool._switch_to_failover(failed_host="10.0.0.1")
    idx_disc = order.index("active.disconnect")
    idx_wait = order.index("wait_ready")
    assert idx_disc < idx_wait


@pytest.mark.asyncio
async def test_switch_teardown_uses_step_timeout():
    """keepalive.stop() and active.disconnect() are bounded."""
    pool, active, keepalive = _started_pool()

    async def hang_ka():
        await asyncio.sleep(30)

    async def hang_disc():
        await asyncio.sleep(30)

    keepalive.stop = hang_ka
    active.disconnect = hang_disc
    with patch(
        "pydeako.deako._connection_pool.asyncio.wait_for",
        new=AsyncMock(side_effect=asyncio.TimeoutError()),
    ):
        with patch.object(
            pool, "_wait_ready", AsyncMock(return_value=False),
        ):
            # pylint: disable-next=protected-access
            ok = await pool._switch_to_failover(failed_host="10.0.0.1")
    # Even though both teardowns hung, the switch proceeded and
    # returned False via host_not_ready without wedging.
    assert ok is False


@pytest.mark.asyncio
async def test_switch_concurrent_callers_wait_on_event():
    """Second caller waits on _switch_event and returns the outcome."""
    pool, _active, _ka = _started_pool(
        primary="10.0.0.1", failover="10.0.0.2",
    )
    gate = asyncio.Event()
    new_active = _fake_deako_connected()

    async def slow_wait_ready(_host, **_kwargs):
        await gate.wait()
        return True

    with patch.object(pool, "_wait_ready", slow_wait_ready):
        with patch.object(
            pool, "_connect_primary",
            AsyncMock(return_value=new_active),
        ):
            with patch.object(
                pool, "_start_keepalive", AsyncMock(),
            ):
                first = asyncio.create_task(
                    # pylint: disable-next=protected-access
                    pool._switch_to_failover(failed_host="10.0.0.1"),
                )
                await asyncio.sleep(0)  # let first take the lock
                second = asyncio.create_task(
                    # pylint: disable-next=protected-access
                    pool._switch_to_failover(failed_host="10.0.0.1"),
                )
                await asyncio.sleep(0)  # let second park
                gate.set()
                r1 = await first
                r2 = await second
    assert r1 is True
    # Second caller sees primary has moved off failed_host.
    assert r2 is True


@pytest.mark.asyncio
async def test_switch_failed_host_none_checks_connected_state():
    """failed_host=None path returns actual connected state."""
    pool, _active, _ka = _started_pool()
    gate = asyncio.Event()
    new_active = _fake_deako_connected()

    async def slow_wait_ready(_host, **_kwargs):
        await gate.wait()
        return True

    with patch.object(pool, "_wait_ready", slow_wait_ready):
        with patch.object(
            pool, "_connect_primary",
            AsyncMock(return_value=new_active),
        ):
            with patch.object(
                pool, "_start_keepalive", AsyncMock(),
            ):
                first = asyncio.create_task(
                    # pylint: disable-next=protected-access
                    pool._switch_to_failover(failed_host="10.0.0.1"),
                )
                await asyncio.sleep(0)
                second = asyncio.create_task(
                    # pylint: disable-next=protected-access
                    pool._switch_to_failover(failed_host=None),
                )
                await asyncio.sleep(0)
                gate.set()
                r1 = await first
                r2 = await second
    assert r1 is True
    assert r2 is True


@pytest.mark.asyncio
async def test_switch_failed_host_stale_guard():
    """If failed_host != primary_host, the switch returns True as no-op."""
    pool, active, _ka = _started_pool(
        primary="10.0.0.1", failover="10.0.0.2",
    )
    # Pretend the switch has already happened; primary_host moved.
    pool.primary_host = "10.0.0.2"
    pool.failover_host = "10.0.0.1"
    # pylint: disable-next=protected-access
    ok = await pool._switch_to_failover(failed_host="10.0.0.1")
    assert ok is True
    # Old active is still in place; switch was a no-op.
    assert pool.active is active


@pytest.mark.asyncio
async def test_switch_succeeds_when_new_keepalive_start_fails():
    """Keepalive-start failure on the former primary is non-fatal."""
    pool, _active, _ka = _started_pool(
        primary="10.0.0.1", failover="10.0.0.2",
    )
    new_active = _fake_deako_connected()
    with patch.object(pool, "_wait_ready", AsyncMock(return_value=True)):
        with patch.object(
            pool, "_connect_primary",
            AsyncMock(return_value=new_active),
        ):
            with patch.object(
                pool, "_start_keepalive",
                AsyncMock(side_effect=OSError("ka boom")),
            ):
                # pylint: disable-next=protected-access
                ok = await pool._switch_to_failover(
                    failed_host="10.0.0.1",
                )
    assert ok is True
    assert pool.primary_host == "10.0.0.2"
    # pylint: disable-next=protected-access
    assert pool._keepalive is None


@pytest.mark.asyncio
async def test_stop_cancels_in_flight_switch():
    """An in-flight switch observes _stopped and bails out."""
    pool, _active, _ka = _started_pool()

    async def slow_wait_ready(_host, **_kwargs):
        # Simulate the switch sleeping during the probe window so
        # stop() can flip _stopped before the next await.
        await asyncio.sleep(0.1)
        return True

    with patch.object(pool, "_wait_ready", slow_wait_ready):
        with patch.object(
            pool, "_connect_primary",
            AsyncMock(return_value=_fake_deako_connected()),
        ):
            switch = asyncio.create_task(
                # pylint: disable-next=protected-access
                pool._switch_to_failover(failed_host="10.0.0.1"),
            )
            await asyncio.sleep(0)  # yield to let the switch start
            await pool.stop()
            ok = await switch
    # The switch observed _stopped after the _wait_ready await and
    # bailed out with False; stopped pools never declare success.
    assert ok is False


# --- _attempt_recovery ---------------------------------------------

@pytest.mark.asyncio
async def test_attempt_recovery_tries_primary_first_then_failover():
    """Recovery probes primary first, then failover on miss."""
    pool, _active, _ka = _started_pool(
        primary="10.0.0.1", failover="10.0.0.2",
    )
    pool.active = None  # degraded
    probed: list[str] = []

    async def fake_probe(host, *_args, **_kwargs):
        probed.append(host)
        return False

    with patch(
        "pydeako.deako._connection_pool._tcp_probe",
        new=fake_probe,
    ):
        # pylint: disable-next=protected-access
        ok, reasons = await pool._attempt_recovery()
    assert ok is False
    assert probed == ["10.0.0.1", "10.0.0.2"]
    assert reasons.get("10.0.0.1") == "tcp_probe_failed"
    assert reasons.get("10.0.0.2") == "tcp_probe_failed"


@pytest.mark.asyncio
async def test_attempt_recovery_succeeds_when_only_primary_responds():
    """Recovery picks primary when only primary responds."""
    pool, _active, _ka = _started_pool(
        primary="10.0.0.1", failover="10.0.0.2",
    )
    pool.active = None

    async def fake_probe(host, *_args, **_kwargs):
        return host == "10.0.0.1"

    new_active = _fake_deako_connected()
    with patch(
        "pydeako.deako._connection_pool._tcp_probe",
        new=fake_probe,
    ):
        with patch.object(
            pool, "_connect_primary",
            AsyncMock(return_value=new_active),
        ):
            with patch.object(
                pool, "_start_keepalive", AsyncMock(),
            ):
                # pylint: disable-next=protected-access
                ok, reasons = await pool._attempt_recovery()
    assert ok is True
    assert reasons == {}
    assert pool.primary_host == "10.0.0.1"
    assert pool.failover_host == "10.0.0.2"
    assert pool.active is new_active


@pytest.mark.asyncio
async def test_attempt_recovery_succeeds_when_only_failover_responds():
    """Recovery promotes the failover when only it responds."""
    pool, _active, _ka = _started_pool(
        primary="10.0.0.1", failover="10.0.0.2",
    )
    pool.active = None

    async def fake_probe(host, *_args, **_kwargs):
        return host == "10.0.0.2"

    new_active = _fake_deako_connected()
    with patch(
        "pydeako.deako._connection_pool._tcp_probe",
        new=fake_probe,
    ):
        with patch.object(
            pool, "_connect_primary",
            AsyncMock(return_value=new_active),
        ):
            with patch.object(
                pool, "_start_keepalive", AsyncMock(),
            ):
                # pylint: disable-next=protected-access
                ok, _reasons = await pool._attempt_recovery()
    assert ok is True
    # Hosts swapped; new primary is the responsive one.
    assert pool.primary_host == "10.0.0.2"
    assert pool.failover_host == "10.0.0.1"


@pytest.mark.asyncio
async def test_attempt_recovery_raises_when_neither_responds():
    """Recovery returns False with per-host reasons on total failure."""
    pool, _active, _ka = _started_pool(
        primary="10.0.0.1", failover="10.0.0.2",
    )
    pool.active = None

    async def fake_probe(*_args, **_kwargs):
        return False

    with patch(
        "pydeako.deako._connection_pool._tcp_probe",
        new=fake_probe,
    ):
        # pylint: disable-next=protected-access
        ok, reasons = await pool._attempt_recovery()
    assert ok is False
    assert reasons == {
        "10.0.0.1": "tcp_probe_failed",
        "10.0.0.2": "tcp_probe_failed",
    }


@pytest.mark.asyncio
async def test_attempt_recovery_stops_keepalive_before_probing():
    """Keepalive.stop() is awaited before any TCP probe runs."""
    pool, _active, keepalive = _started_pool()
    pool.active = None
    order: list[str] = []

    async def fake_ka_stop():
        order.append("keepalive.stop")

    async def fake_probe(host, *_args, **_kwargs):
        order.append(f"probe:{host}")
        return False

    keepalive.stop = fake_ka_stop
    with patch(
        "pydeako.deako._connection_pool._tcp_probe",
        new=fake_probe,
    ):
        # pylint: disable-next=protected-access
        await pool._attempt_recovery()
    assert order[0] == "keepalive.stop"
    assert any(entry.startswith("probe:") for entry in order[1:])


@pytest.mark.asyncio
async def test_attempt_recovery_disconnects_stale_active_before_probe():
    """Stale active is disconnected before any probe runs."""
    pool, active, _keepalive = _started_pool()
    # Half-dead active: present but not connected.
    active.is_connected.return_value = False
    order: list[str] = []

    async def fake_disconnect():
        order.append("active.disconnect")

    async def fake_probe(host, *_args, **_kwargs):
        order.append(f"probe:{host}")
        return False

    active.disconnect = fake_disconnect
    with patch(
        "pydeako.deako._connection_pool._tcp_probe",
        new=fake_probe,
    ):
        # pylint: disable-next=protected-access
        await pool._attempt_recovery()
    assert "active.disconnect" in order
    disc_idx = order.index("active.disconnect")
    probe_idx = next(
        i for i, v in enumerate(order) if v.startswith("probe:")
    )
    assert disc_idx < probe_idx
    assert pool.active is None


@pytest.mark.asyncio
async def test_recovery_succeeds_when_new_keepalive_start_fails():
    """Recovery still returns True when new keepalive start raises."""
    pool, _active, _ka = _started_pool()
    pool.active = None

    async def fake_probe(host, *_args, **_kwargs):
        return host == "10.0.0.1"

    with patch(
        "pydeako.deako._connection_pool._tcp_probe",
        new=fake_probe,
    ):
        with patch.object(
            pool, "_connect_primary",
            AsyncMock(return_value=_fake_deako_connected()),
        ):
            with patch.object(
                pool, "_start_keepalive",
                AsyncMock(side_effect=OSError("boom")),
            ):
                # pylint: disable-next=protected-access
                ok, _reasons = await pool._attempt_recovery()
    assert ok is True
    # pylint: disable-next=protected-access
    assert pool._keepalive is None


# --- control_device ------------------------------------------------

@pytest.mark.asyncio
async def test_control_device_after_stop_raises():
    """control_device() after stop() raises NoSocketException."""
    pool = DeakoConnectionPool(
        primary_host="10.0.0.1", failover_host="10.0.0.2",
    )
    await pool.stop()
    with pytest.raises(NoSocketException):
        await pool.control_device("u", True, 100)


@pytest.mark.asyncio
async def test_control_device_sends_without_switch_when_active_healthy():
    """Happy path: send succeeds, no switch, strict path called once."""
    pool, active, _ka = _started_pool()
    await pool.control_device("u", True, 100)
    # pylint: disable-next=protected-access
    active._control_device_strict.assert_awaited_once_with(
        "u", True, 100,
    )


@pytest.mark.asyncio
async def test_pool_uses_control_device_strict_not_public():
    """Pool invokes _control_device_strict, not public control_device."""
    pool, active, _ka = _started_pool()
    await pool.control_device("u", True, 100)
    active.control_device.assert_not_called()
    # pylint: disable-next=protected-access
    active._control_device_strict.assert_awaited_once()


@pytest.mark.asyncio
async def test_control_device_triggers_switch_on_send_oserror():
    """OSError from strict triggers one _switch_to_failover call."""
    pool, active, _ka = _started_pool(
        primary="10.0.0.1", failover="10.0.0.2",
    )
    # pylint: disable-next=protected-access
    active._control_device_strict = AsyncMock(
        side_effect=OSError("send broke"),
    )
    new_active = _fake_deako_connected()

    async def switch_stub(**_kwargs):
        # Simulate successful switch: host swap and new active.
        pool.primary_host, pool.failover_host = (
            pool.failover_host, pool.primary_host,
        )
        pool.active = new_active
        return True

    with patch.object(
        pool, "_switch_to_failover", side_effect=switch_stub,
    ) as switch_mock:
        await pool.control_device("u", True, 100)
    switch_mock.assert_called_once()
    kwargs = switch_mock.call_args.kwargs
    assert kwargs.get("failed_host") == "10.0.0.1"


@pytest.mark.asyncio
async def test_control_device_triggers_switch_on_send_nosocketexception():
    """NoSocketException from strict also triggers one switch."""
    pool, active, _ka = _started_pool()
    # pylint: disable-next=protected-access
    active._control_device_strict = AsyncMock(
        side_effect=NoSocketException("no socket"),
    )
    new_active = _fake_deako_connected()

    async def switch_stub(**_kwargs):
        pool.primary_host, pool.failover_host = (
            pool.failover_host, pool.primary_host,
        )
        pool.active = new_active
        return True

    with patch.object(
        pool, "_switch_to_failover", side_effect=switch_stub,
    ) as switch_mock:
        await pool.control_device("u", True, 100)
    switch_mock.assert_called_once()


@pytest.mark.asyncio
async def test_control_device_retries_once_on_new_active_after_switch():
    """After a successful switch, strict is called once on new active."""
    pool, active, _ka = _started_pool(
        primary="10.0.0.1", failover="10.0.0.2",
    )
    # pylint: disable-next=protected-access
    active._control_device_strict = AsyncMock(
        side_effect=OSError("send broke"),
    )
    new_active = _fake_deako_connected()

    async def switch_stub(**_kwargs):
        pool.primary_host, pool.failover_host = (
            pool.failover_host, pool.primary_host,
        )
        pool.active = new_active
        return True

    with patch.object(
        pool, "_switch_to_failover", side_effect=switch_stub,
    ):
        await pool.control_device("u", True, 100)
    # pylint: disable-next=protected-access
    new_active._control_device_strict.assert_awaited_once_with(
        "u", True, 100,
    )


@pytest.mark.asyncio
async def test_control_device_raises_when_retry_on_new_active_fails():
    """Retry OSError after switch names both hosts in the message."""
    pool, active, _ka = _started_pool(
        primary="10.0.0.1", failover="10.0.0.2",
    )
    # pylint: disable-next=protected-access
    active._control_device_strict = AsyncMock(
        side_effect=OSError("first"),
    )
    new_active = _fake_deako_connected()
    # pylint: disable-next=protected-access
    new_active._control_device_strict = AsyncMock(
        side_effect=OSError("second"),
    )

    async def switch_stub(**_kwargs):
        pool.primary_host, pool.failover_host = (
            pool.failover_host, pool.primary_host,
        )
        pool.active = new_active
        return True

    with patch.object(
        pool, "_switch_to_failover", side_effect=switch_stub,
    ):
        with pytest.raises(NoSocketException) as excinfo:
            await pool.control_device("u", True, 100)
    msg = str(excinfo.value)
    assert "10.0.0.1" in msg
    assert "10.0.0.2" in msg


@pytest.mark.asyncio
async def test_control_device_raises_when_switch_fails_no_cascade():
    """Switch returns False; _attempt_recovery is not invoked."""
    pool, active, _ka = _started_pool(
        primary="10.0.0.1", failover="10.0.0.2",
    )
    # pylint: disable-next=protected-access
    active._control_device_strict = AsyncMock(
        side_effect=OSError("broke"),
    )
    with patch.object(
        pool, "_switch_to_failover", AsyncMock(return_value=False),
    ):
        with patch.object(
            pool, "_attempt_recovery", AsyncMock(),
        ) as rec_mock:
            with pytest.raises(NoSocketException) as excinfo:
                await pool.control_device("u", True, 100)
    rec_mock.assert_not_called()
    assert "10.0.0.1" in str(excinfo.value)


@pytest.mark.asyncio
async def test_control_device_next_call_runs_recovery_via_no_primary_path():
    """After a failed switch, the next call enters section 7.5."""
    pool, active, _ka = _started_pool(
        primary="10.0.0.1", failover="10.0.0.2",
    )
    # pylint: disable-next=protected-access
    active._control_device_strict = AsyncMock(
        side_effect=OSError("broke"),
    )
    # Simulate the post-failure degraded state: no connected active.
    pool.active = None
    with patch.object(
        pool, "_attempt_recovery",
        AsyncMock(return_value=(False, {
            "10.0.0.1": "tcp_probe_failed",
            "10.0.0.2": "tcp_probe_failed",
        })),
    ) as rec_mock:
        with pytest.raises(NoSocketException):
            await pool.control_device("u", True, 100)
    rec_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_control_device_triggers_recovery_when_no_primary():
    """No-primary entry: _attempt_recovery is called, then send."""
    pool, _active, _ka = _started_pool()
    pool.active = None
    new_active = _fake_deako_connected()

    async def recover():
        pool.active = new_active
        return True, {}

    with patch.object(
        pool, "_attempt_recovery", side_effect=recover,
    ) as rec_mock:
        await pool.control_device("u", True, 100)
    rec_mock.assert_awaited_once()
    # pylint: disable-next=protected-access
    new_active._control_device_strict.assert_awaited_once()


@pytest.mark.asyncio
async def test_control_device_raises_when_no_primary_after_failed_recovery():
    """Recovery failure surfaces NoSocketException with both hosts."""
    pool, _active, _ka = _started_pool(
        primary="10.0.0.1", failover="10.0.0.2",
    )
    pool.active = None
    with patch.object(
        pool, "_attempt_recovery",
        AsyncMock(return_value=(False, {
            "10.0.0.1": "tcp_probe_failed",
            "10.0.0.2": "connect_exhausted",
        })),
    ):
        with pytest.raises(NoSocketException) as excinfo:
            await pool.control_device("u", True, 100)
    msg = str(excinfo.value)
    assert "10.0.0.1" in msg
    assert "tcp_probe_failed" in msg
    assert "10.0.0.2" in msg
    assert "connect_exhausted" in msg


# --- Decision-25 messages ------------------------------------------

@pytest.mark.asyncio
async def test_nosocketexception_message_includes_hosts_tried():
    """The raised NoSocketException names both hosts and reasons."""
    pool, _active, _ka = _started_pool(
        primary="10.0.0.1", failover="10.0.0.2",
    )
    pool.active = None
    with patch.object(
        pool, "_attempt_recovery",
        AsyncMock(return_value=(False, {
            "10.0.0.1": "connect_exhausted",
            "10.0.0.2": "tcp_probe_failed",
        })),
    ):
        with pytest.raises(NoSocketException) as excinfo:
            await pool.control_device("u", True, 100)
    msg = str(excinfo.value)
    assert "primary=10.0.0.1" in msg
    assert "connect_exhausted" in msg
    assert "failover=10.0.0.2" in msg
    assert "tcp_probe_failed" in msg


@pytest.mark.asyncio
async def test_nosocketexception_message_on_switch_wait_timeout():
    """Recovery's concurrent-wait timeout produces switch_wait_timeout."""
    pool, _active, _ka = _started_pool()
    pool.active = None

    # Grab the lock and park.
    with patch.object(
        pool, "_wait_ready", AsyncMock(return_value=True),
    ):
        with patch.object(
            pool, "_connect_primary",
            AsyncMock(return_value=_fake_deako_connected()),
        ):
            with patch.object(
                pool, "_start_keepalive", AsyncMock(),
            ):
                # Start a switch that never completes.
                held = asyncio.Event()

                async def held_wait_ready(_host, **_kwargs):
                    held.set()
                    await asyncio.sleep(30)
                    return True

                with patch.object(
                    pool, "_wait_ready", held_wait_ready,
                ):
                    task = asyncio.create_task(
                        # pylint: disable-next=protected-access
                        pool._switch_to_failover(
                            failed_host="10.0.0.1",
                        ),
                    )
                    await held.wait()
                    # Now call recovery via low-level path.
                    with patch(
                        "pydeako.deako._connection_pool."
                        "SWITCH_WAIT_TIMEOUT_S",
                        0.01,
                    ):
                        # pylint: disable-next=protected-access
                        ok, reasons = await pool._attempt_recovery()
                    task.cancel()
                    try:
                        await task
                    # pylint: disable-next=broad-exception-caught
                    except (asyncio.CancelledError, Exception):
                        pass
    assert ok is False
    assert reasons.get(pool.primary_host) == "switch_wait_timeout"
    assert reasons.get(pool.failover_host) == "switch_wait_timeout"


@pytest.mark.asyncio
async def test_nosocketexception_message_on_in_flight_switch_failed():
    """Recovery waits for in-flight switch that yields no active."""
    pool, _active, _ka = _started_pool()
    pool.active = None

    gate = asyncio.Event()

    async def slow_wait_ready(_host, **_kwargs):
        await gate.wait()
        return False  # causes switch failure

    with patch.object(pool, "_wait_ready", slow_wait_ready):
        task = asyncio.create_task(
            # pylint: disable-next=protected-access
            pool._switch_to_failover(failed_host="10.0.0.1"),
        )
        await asyncio.sleep(0)  # let switch take the lock
        recovery = asyncio.create_task(
            # pylint: disable-next=protected-access
            pool._attempt_recovery(),
        )
        await asyncio.sleep(0)  # let recovery park
        gate.set()
        _ = await task
        ok, reasons = await recovery
    assert ok is False
    for host in (pool.primary_host, pool.failover_host):
        assert reasons[host] == "in_flight_switch_failed"


@pytest.mark.asyncio
async def test_nosocketexception_message_on_stopped_mid_loop():
    """Setting _stopped mid-recovery yields 'stopped' for both hosts."""
    pool, _active, _ka = _started_pool()
    pool.active = None

    first_probe_done = asyncio.Event()

    async def fake_probe(host, *_args, **_kwargs):
        if host == "10.0.0.1":
            first_probe_done.set()
            # Let the test flip _stopped after the first probe.
            await asyncio.sleep(0)
            return False
        return False

    with patch(
        "pydeako.deako._connection_pool._tcp_probe",
        new=fake_probe,
    ):
        task = asyncio.create_task(
            # pylint: disable-next=protected-access
            pool._attempt_recovery(),
        )
        await first_probe_done.wait()
        # pylint: disable-next=protected-access
        pool._stopped = True
        ok, reasons = await task
    assert ok is False
    assert reasons == {
        "10.0.0.1": "stopped",
        "10.0.0.2": "stopped",
    }


# --- _connect_primary partial-failure cleanup ----------------------

@pytest.mark.asyncio
async def test_connect_primary_partial_failure_cleanup():
    """A failed _connect_primary leaves no partial Deako live."""
    pool = DeakoConnectionPool(
        primary_host="10.0.0.1", failover_host="10.0.0.2",
    )
    broken = _fake_deako_connected()
    broken.find_devices = AsyncMock(side_effect=OSError("boom"))
    with patch(
        "pydeako.deako._connection_pool.Deako", return_value=broken,
    ):
        with pytest.raises(OSError):
            # pylint: disable-next=protected-access
            await pool._connect_primary("10.0.0.1")
        # Partial deako is still tracked; cleanup clears it and
        # disconnects the half-constructed object.
        # pylint: disable-next=protected-access
        assert pool._partial_deako is broken
        # pylint: disable-next=protected-access
        await pool._cleanup_partial_connect()
    broken.disconnect.assert_awaited_once()
    # pylint: disable-next=protected-access
    assert pool._partial_deako is None


# --- on_connection_lost hook ---------------------------------------

@pytest.mark.asyncio
async def test_on_connection_lost_schedules_switch():
    """The sync hook schedules a _switch_to_failover task."""
    pool, _active, _ka = _started_pool()
    with patch.object(
        pool, "_switch_to_failover", AsyncMock(return_value=True),
    ) as switch_mock:
        # pylint: disable-next=protected-access
        pool._on_active_connection_lost()
        # Let the scheduled task run.
        await asyncio.sleep(0)
        # Drain: the task is tracked in _on_lost_tasks.
        # pylint: disable-next=protected-access
        for task in list(pool._on_lost_tasks):
            await task
    switch_mock.assert_awaited_once()
    assert (
        switch_mock.call_args.kwargs.get("failed_host")
        == pool.primary_host
    )


def test_on_connection_lost_noop_when_stopped():
    """Hook is a no-op once the pool is stopped."""
    pool = DeakoConnectionPool(
        primary_host="10.0.0.1", failover_host="10.0.0.2",
    )
    # pylint: disable-next=protected-access
    pool._stopped = True
    # Does not raise; does not schedule anything.
    # pylint: disable-next=protected-access
    pool._on_active_connection_lost()


# --- fix(pool): stop-race and same-host guards --------------------

def test_init_rejects_same_host_primary_and_failover():
    """Constructor rejects single-bridge configuration per docstring.

    The pool docstring promises single-bridge pools are unsupported;
    constructing one must raise ValueError rather than silently
    returning a pool whose failover target equals its primary.
    """
    with pytest.raises(ValueError):
        DeakoConnectionPool(
            primary_host="10.0.0.1",
            failover_host="10.0.0.1",
        )


@pytest.mark.asyncio
async def test_start_discards_new_active_if_stopped_mid_connect():
    """stop() during start()'s connect must not install late Deako.

    If _connect_primary is in flight when stop() sets _stopped=True,
    the freshly connected Deako returned after the await must be
    disconnected and discarded, not installed on self.active.
    """
    pool = DeakoConnectionPool(
        primary_host="10.0.0.1", failover_host="10.0.0.2",
    )
    new_active = _fake_deako_connected()
    release = asyncio.Event()

    async def blocked(_host):
        await release.wait()
        return new_active

    with patch.object(
        DeakoConnectionPool,
        "_connect_primary",
        new=AsyncMock(side_effect=blocked),
    ):
        start_task = asyncio.create_task(pool.start())
        # Yield so start_task enters the connect await.
        await asyncio.sleep(0)
        await pool.stop()
        release.set()
        await start_task
    assert pool.active is None
    # pylint: disable-next=protected-access
    assert pool._started is False
    new_active.disconnect.assert_awaited()


@pytest.mark.asyncio
async def test_switch_discards_new_active_if_stopped_mid_connect():
    """stop() during _switch_to_failover must not install late Deako.

    If _connect_with_retry is in flight when stop() sets _stopped,
    the switch must disconnect the freshly connected Deako, return
    False, and leave the host map untouched.
    """
    pool, _active, _ka = _started_pool(
        primary="10.0.0.1", failover="10.0.0.2",
    )
    new_active = _fake_deako_connected()
    release = asyncio.Event()

    async def blocked(_host):
        await release.wait()
        return new_active

    with patch.object(
        pool, "_wait_ready", AsyncMock(return_value=True),
    ):
        with patch.object(
            pool, "_connect_with_retry",
            AsyncMock(side_effect=blocked),
        ):
            # pylint: disable-next=protected-access
            switch_task = asyncio.create_task(
                pool._switch_to_failover(failed_host="10.0.0.1"),
            )
            await asyncio.sleep(0)
            await pool.stop()
            release.set()
            ok = await switch_task
    assert ok is False
    assert pool.active is None
    assert pool.primary_host == "10.0.0.1"
    assert pool.failover_host == "10.0.0.2"
    new_active.disconnect.assert_awaited()


@pytest.mark.asyncio
async def test_recovery_discards_new_active_if_stopped_mid_connect():
    """stop() during _attempt_recovery must not install late Deako.

    If _connect_with_retry is in flight when stop() sets _stopped,
    recovery must disconnect the freshly connected Deako, return
    (False, reasons) with "stopped" tokens, and leave host map alone.
    """
    pool, _active, _ka = _started_pool(
        primary="10.0.0.1", failover="10.0.0.2",
    )
    new_deako = _fake_deako_connected()
    release = asyncio.Event()

    async def blocked(_host):
        await release.wait()
        return new_deako

    with patch(
        "pydeako.deako._connection_pool._tcp_probe",
        new=AsyncMock(return_value=True),
    ):
        with patch.object(
            pool, "_connect_with_retry",
            AsyncMock(side_effect=blocked),
        ):
            # pylint: disable-next=protected-access
            rec_task = asyncio.create_task(pool._attempt_recovery())
            await asyncio.sleep(0)
            await pool.stop()
            release.set()
            ok, reasons = await rec_task
    assert ok is False
    assert reasons == {
        "10.0.0.1": "stopped",
        "10.0.0.2": "stopped",
    }
    assert pool.active is None
    assert pool.primary_host == "10.0.0.1"
    assert pool.failover_host == "10.0.0.2"
    new_deako.disconnect.assert_awaited()
