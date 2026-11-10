"""Regression tests for the device identity.

The unique id of a device must NOT depend on the machine it was reached
through: the same phone behind two different agents is still the same phone
and must not show up twice in the device list.
"""

from __future__ import annotations

import pytest

from app.services.registry import build_device_uid


def test_the_hardware_id_wins_over_the_machine():
    """Two agents, one device -> one id."""
    first = build_device_uid("remote", "10.0.0.1:12345", "emulator-5554", "HW-123")
    second = build_device_uid("remote", "10.0.0.2:12345", "emulator-5554", "HW-123")
    assert first == second


def test_without_a_hardware_id_the_machine_is_part_of_the_id():
    """Devices that do not report a serial keep a per machine id."""
    first = build_device_uid("remote", "10.0.0.1:12345", "emulator-5554")
    second = build_device_uid("remote", "10.0.0.2:12345", "emulator-5554")
    assert first != second


def test_the_id_is_stable_for_the_same_device():
    uid = build_device_uid("remote", "10.0.0.1:12345", "emulator-5554", "HW-123")
    assert uid == build_device_uid("remote", "10.0.0.1:12345", "emulator-5554", "HW-123")
    assert len(uid) == 16


def test_different_devices_get_different_ids():
    first = build_device_uid("remote", "10.0.0.1:12345", "a", "HW-A")
    second = build_device_uid("remote", "10.0.0.1:12345", "b", "HW-B")
    assert first != second


def test_an_empty_hardware_id_falls_back_to_the_machine():
    """A blank serial must not collapse every device into one id."""
    blank = build_device_uid("remote", "10.0.0.1:12345", "emulator-5554", "   ")
    fallback = build_device_uid("remote", "10.0.0.1:12345", "emulator-5554")
    assert blank == fallback
