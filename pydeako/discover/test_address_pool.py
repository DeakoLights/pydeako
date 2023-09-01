"""
Test the address pool used by discoverer.
"""

from uuid import uuid4
import pytest

from ._address_pool import _AddressPool, EmptyAddressPool


def test_init():
    """Test init."""
    pool = _AddressPool()
    assert pool is not None


def test_add():
    """Test _AddressPool.add_address."""
    pool = _AddressPool()
    address1 = str(uuid4())
    pool.add_address(address1)
    assert pool.available_addresses() == 1

    address = pool.get_address()
    assert address == address1
    assert pool.available_addresses() == 1


def test_multiple_add():
    """Test _AddressPool.add_address multiple times."""
    pool = _AddressPool()
    address1 = str(uuid4())
    address2 = str(uuid4())
    pool.add_address(address1)
    assert pool.available_addresses() == 1
    pool.add_address(address2)
    assert pool.available_addresses() == 2

    address = pool.get_address()
    assert address == address1

    address = pool.get_address()
    assert address == address2

    # make sure that the first address was put at the back
    address = pool.get_address()
    assert address == address1


def test_get_empty_raises():
    """Test _AddressPool.get_address raises EmptyAddresspool when empty."""
    pool = _AddressPool()

    with pytest.raises(EmptyAddressPool):
        pool.get_address()


def test_removed():
    """Test _AddressPool.remove_address."""
    pool = _AddressPool()

    address = str(uuid4())
    pool.add_address(address)
    assert pool.available_addresses() == 1

    pool.remove_address(address)

    assert pool.available_addresses() == 0

    with pytest.raises(EmptyAddressPool):
        pool.get_address()
