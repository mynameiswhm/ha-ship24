"""Tests for Ship24 integration setup helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ship24 import _register_services
from custom_components.ship24.const import (
    CONF_SUPPRESSED_NUMBERS,
    DOMAIN,
    SERVICE_REMOVE_PACKAGE,
)
from custom_components.ship24.coordinator import Ship24Coordinator


async def test_remove_delivered_packages_suppresses_tracker_ids(hass):
    """Delivered auto-removal suppresses only the delivered duplicate tracker."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="entry-id",
        options={CONF_SUPPRESSED_NUMBERS: ["legacy-lower"]},
    )
    entry.add_to_hass(hass)

    coordinator = Ship24Coordinator(
        hass=hass,
        api=MagicMock(),
        tracking_numbers=[],
        suppressed_numbers=entry.options[CONF_SUPPRESSED_NUMBERS],
    )
    coordinator.data = {
        "A": {
            "tracker_id": "A",
            "tracking_number": "X",
            "status_code": "delivered",
        },
        "B": {
            "tracker_id": "B",
            "tracking_number": "X",
            "status_code": "in_transit",
        },
    }
    hass.data[DOMAIN] = {entry.entry_id: coordinator}

    _register_services(hass, entry)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_REMOVE_PACKAGE,
        blocking=True,
    )

    assert entry.options[CONF_SUPPRESSED_NUMBERS] == ["LEGACY-LOWER", "tracker:A"]
    assert list(coordinator.data) == ["B"]
    assert coordinator.suppressed_numbers == {"LEGACY-LOWER"}
    assert coordinator.suppressed_tracker_ids == {"A"}
