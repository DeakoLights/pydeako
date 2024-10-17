"""
Test the discovery client, patching the address pool and zeroconf lib.
"""
from uuid import uuid4
from mock import Mock, patch
import pytest
from zeroconf import Zeroconf

from ._discover import DeakoDiscoverer, DeakoListener, DevicesNotFoundException
from ._address_pool import _AddressPool


@patch("pydeako.discover._discover.ServiceBrowser.__init__")
@patch("pydeako.discover._discover.DeakoListener", spec=DeakoListener)
@patch("pydeako.discover._discover._AddressPool", spec=_AddressPool)
def test_deako_discoverer_init(
    mock_address_pool, mock_deako_listener, mock_service_browser_init
):
    """Test init of Discoverer.__init__"""
    zc_mock = Mock(Zeroconf)

    deako_discoverer = DeakoDiscoverer(zc_mock)

    assert deako_discoverer is not None

    mock_address_pool.assert_called_once_with()
    mock_deako_listener.assert_called_once_with(
        mock_address_pool.return_value.add_address,
        mock_address_pool.return_value.remove_address,
    )

    mock_service_browser_init.assert_called_once_with(
        zc_mock,
        "_deako._tcp.local.",
        mock_deako_listener.return_value,
    )


@patch("pydeako.discover._discover.Zeroconf", spec=Zeroconf)
@patch("pydeako.discover._discover.ServiceBrowser.__init__")
@patch("pydeako.discover._discover.DeakoListener", spec=DeakoListener)
@patch("pydeako.discover._discover._AddressPool", spec=_AddressPool)
def test_deako_discoverer_init_no_zc(
    mock_address_pool, mock_deako_listener, mock_service_browser_init, mock_zc
):
    """Test init of Discoverer.__init__ with no zc passed in"""

    deako_discoverer = DeakoDiscoverer()

    assert deako_discoverer is not None

    mock_zc.assert_called_once()
    mock_address_pool.assert_called_once_with()
    mock_deako_listener.assert_called_once_with(
        mock_address_pool.return_value.add_address,
        mock_address_pool.return_value.remove_address,
    )

    mock_service_browser_init.assert_called_once_with(
        mock_zc.return_value,
        "_deako._tcp.local.",
        mock_deako_listener.return_value,
    )


@patch("pydeako.discover._discover.TIMEOUT_S", 1)
@patch("pydeako.discover._discover.ServiceBrowser.__init__")
@patch("pydeako.discover._discover.DeakoListener", spec=DeakoListener)
@patch("pydeako.discover._discover._AddressPool", spec=_AddressPool)
@pytest.mark.asyncio
async def test_deako_discoverer_get_address_success(
    mock_address_pool, mock_deako_listener, mock_service_browser_init
):  # pylint: disable=unused-argument
    """Test Discoverer.get_address success."""
    # use instance as the mock as this is what will be called
    mock_address_pool = mock_address_pool.return_value
    zc_mock = Mock(Zeroconf)
    mock_address, mock_name = str(uuid4()), str(uuid4())

    mock_address_pool.available_addresses.return_value = 1
    mock_address_pool.get_address.return_value = mock_address, mock_name

    deako_discoverer = DeakoDiscoverer(zc_mock)

    assert deako_discoverer is not None

    address = await deako_discoverer.get_address()

    assert address == mock_address


@patch("pydeako.discover._discover.TIMEOUT_S", 1)
@patch("pydeako.discover._discover.ServiceBrowser.__init__")
@patch("pydeako.discover._discover.DeakoListener", spec=DeakoListener)
@patch("pydeako.discover._discover._AddressPool", spec=_AddressPool)
@pytest.mark.asyncio
async def test_deako_discoverer_get_address_timeout(
    mock_address_pool, mock_deako_listener, mock_service_browser_init
):  # pylint: disable=unused-argument
    """
    Test Discoverer.get_address times out and
    throws DevicesNotFoundException.
    """
    # use instance as the mock as this is what will be called
    mock_address_pool = mock_address_pool.return_value
    zc_mock = Mock(Zeroconf)

    mock_address_pool.available_addresses.return_value = 0

    deako_discoverer = DeakoDiscoverer(zc_mock)

    assert deako_discoverer is not None

    with pytest.raises(DevicesNotFoundException):
        await deako_discoverer.get_address()


def test_listener_init():
    """Test Listener.__init__."""
    add_callback = Mock()
    remove_callback = Mock()

    listener = DeakoListener(add_callback, remove_callback)
    assert listener is not None
    add_callback.assert_not_called()
    remove_callback.assert_not_called()


@patch("socket.inet_ntoa")
def test_listener_add_service(mock_inet_ntoa):
    """Test Listener.add_service."""
    address = str(uuid4())
    converted_address = str(uuid4())
    _type = str(uuid4())
    name = str(uuid4())
    port = 69

    mock_inet_ntoa.return_value = converted_address

    add_callback = Mock()
    remove_callback = Mock()
    zc_mock = Mock(Zeroconf)
    mock_info = Mock()
    mock_info.addresses = [address]
    mock_info.port = port

    zc_mock.get_service_info.return_value = mock_info

    listener = DeakoListener(add_callback, remove_callback)

    listener.add_service(zc_mock, _type, name)

    mock_inet_ntoa.assert_called_with(address)
    zc_mock.get_service_info.assert_called_with(_type, name)
    add_callback.assert_called_once_with(f"{converted_address}:{port}")
    remove_callback.assert_not_called()


@patch("socket.inet_ntoa")
def test_listener_remove_service(mock_inet_ntoa):
    """Test Listener.remove_service."""
    address = str(uuid4())
    converted_address = str(uuid4())
    _type = str(uuid4())
    name = str(uuid4())
    port = 69

    mock_inet_ntoa.return_value = converted_address

    add_callback = Mock()
    remove_callback = Mock()
    zc_mock = Mock(Zeroconf)
    mock_info = Mock()
    mock_info.addresses = [address]
    mock_info.port = port

    zc_mock.get_service_info.return_value = mock_info

    listener = DeakoListener(add_callback, remove_callback)

    listener.remove_service(zc_mock, _type, name)

    mock_inet_ntoa.assert_called_with(address)
    zc_mock.get_service_info.assert_called_with(_type, name)
    add_callback.assert_not_called()
    remove_callback.assert_called_once_with(f"{converted_address}:{port}")


def test_listener_update_service():
    """Test Listener.update_service."""
    _type = str(uuid4())
    name = str(uuid4())

    add_callback = Mock()
    remove_callback = Mock()
    zc_mock = Mock(Zeroconf)

    listener = DeakoListener(add_callback, remove_callback)

    listener.update_service(zc_mock, _type, name)

    add_callback.assert_not_called()
    remove_callback.assert_not_called()
