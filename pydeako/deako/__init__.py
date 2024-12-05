"""Module for controlling deako devices locally."""

from ._deako import Deako, FindDevicesError

__all__ = [
    'Deako',
    'FindDevicesError',
]
