"""Sensor platform for the Ship24 Package Tracker integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_CLIENT_TRACKER_ID,
    ATTR_COURIER,
    ATTR_DESTINATION_COUNTRY,
    ATTR_DESTINATION_POST_CODE,
    ATTR_ESTIMATED_DELIVERY,
    ATTR_EVENTS,
    ATTR_FRIENDLY_NAME,
    ATTR_LAST_EVENT,
    ATTR_LAST_EVENT_TIME,
    ATTR_LAST_LOCATION,
    ATTR_ORIGIN_COUNTRY,
    ATTR_PACKAGE_COUNT,
    ATTR_SHIPMENT_REFERENCE,
    ATTR_SPOKEN_SUMMARY,
    ATTR_STATUS_CODE,
    ATTR_TITLE,
    ATTR_TRACKER_ID,
    ATTR_TRACKING_NUMBER,
    DOMAIN,
)
from .coordinator import Ship24Coordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """
    Set up Ship24 sensor entities from a config entry.

    Creates a summary sensor plus one sensor per tracked package. Dynamically
    adds new package sensors as new tracking numbers appear after coordinator
    updates (e.g. packages added on the Ship24 website).

    param hass: The Home Assistant instance.
    param entry: The config entry for this integration instance.
    param async_add_entities: Callback to register new sensor entities.

    :return: None
    """
    coordinator: Ship24Coordinator = hass.data[DOMAIN][entry.entry_id]
    known_package_ids: set[str] = set()

    async_add_entities([Ship24SummarySensor(coordinator, entry)])

    def _add_new_package_sensors() -> None:
        """Add sensors for any tracker IDs not yet registered."""
        current_package_ids = set(coordinator.data or {})
        new_package_ids = current_package_ids - known_package_ids
        if new_package_ids:
            _migrate_legacy_unique_ids(hass, entry, coordinator.data or {})
            known_package_ids.update(new_package_ids)
            async_add_entities(
                [
                    Ship24PackageSensor(coordinator, package_id)
                    for package_id in new_package_ids
                ]
            )

    _add_new_package_sensors()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_package_sensors))


def _migrate_legacy_unique_ids(
    hass: HomeAssistant,
    entry: ConfigEntry,
    packages: dict[str, dict[str, Any]],
) -> None:
    """
    Move old tracking-number unique IDs to trackerId unique IDs when unambiguous.

    Previous releases used ship24_<tracking_number>. That is ambiguous when
    Ship24 has multiple trackers for the same number, so migration is only
    attempted for one-to-one tracker number mappings.
    """
    registry = er.async_get(hass)
    packages_by_number: dict[str, list[dict[str, Any]]] = {}
    for package in packages.values():
        tracking_number = package.get("tracking_number")
        if tracking_number:
            packages_by_number.setdefault(tracking_number, []).append(package)

    for tracking_number, matching_packages in packages_by_number.items():
        if len(matching_packages) != 1:
            _LOGGER.debug(
                "Not migrating legacy Ship24 unique ID for trackingNumber=%s; "
                "%d trackers share that number",
                tracking_number,
                len(matching_packages),
            )
            continue

        package = matching_packages[0]
        tracker_id = package.get("tracker_id")
        if not tracker_id:
            continue

        legacy_unique_id = f"{DOMAIN}_{tracking_number}"
        new_unique_id = f"{DOMAIN}_{tracker_id}"
        legacy_entity_id = registry.async_get_entity_id(
            "sensor", DOMAIN, legacy_unique_id
        )
        new_entity_id = registry.async_get_entity_id("sensor", DOMAIN, new_unique_id)
        if not legacy_entity_id or new_entity_id:
            continue

        entity_entry = registry.async_get(legacy_entity_id)
        if not entity_entry or entity_entry.config_entry_id != entry.entry_id:
            continue

        registry.async_update_entity(legacy_entity_id, new_unique_id=new_unique_id)
        _LOGGER.debug(
            "Migrated Ship24 entity unique ID from trackingNumber=%s to trackerId=%s",
            tracking_number,
            tracker_id,
        )


class Ship24SummarySensor(CoordinatorEntity[Ship24Coordinator], SensorEntity):
    """Sensor providing a spoken summary of all tracked packages for voice assistants."""

    _attr_icon = "mdi:package-variant-closed-shipping"
    _attr_has_entity_name = False

    def __init__(
        self,
        coordinator: Ship24Coordinator,
        entry: ConfigEntry,
    ) -> None:
        """
        Initialize the Ship24 summary sensor.

        param coordinator: The Ship24 data coordinator.
        param entry: The config entry this sensor belongs to.
        """
        super().__init__(coordinator)
        self._entry_id = entry.entry_id
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_summary"
        self._attr_name = "Ship24 Package Summary"

    @property
    def native_value(self) -> str:
        """
        Return the spoken summary as the sensor state (capped at 255 chars).

        Voice assistants read this state when the sensor is queried.

        :return: Spoken summary string, truncated if needed.
        """
        summary = self.coordinator.get_spoken_summary()
        if len(summary) > 255:
            summary = summary[:252] + "..."
        return summary

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """
        Return the full spoken summary and package count as attributes.

        :return: Dict with spoken_summary (full text) and package_count.
        """
        return {
            ATTR_SPOKEN_SUMMARY: self.coordinator.get_spoken_summary(),
            ATTR_PACKAGE_COUNT: len(self.coordinator.data or {}),
        }

    @property
    def device_info(self) -> dict[str, Any]:
        """
        Group the summary sensor under the main Ship24 integration device.

        :return: Dict with device identifiers and metadata.
        """
        return {
            "identifiers": {(DOMAIN, self._entry_id)},
            "name": "Ship24 Package Tracker",
            "manufacturer": "Ship24",
            "model": "Package Tracker",
        }


class Ship24PackageSensor(CoordinatorEntity[Ship24Coordinator], SensorEntity):
    """
    Sensor representing a single tracked package.

    Each package is its own HA device. With has_entity_name=True and name=None,
    the entity name equals the device name (friendly name or tracking number),
    showing a clean single label in the UI without duplication.
    """

    _attr_icon = "mdi:package-variant-closed"
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: Ship24Coordinator,
        package_id: str,
    ) -> None:
        """
        Initialize a Ship24 package sensor.

        param coordinator: The Ship24 data coordinator.
        param package_id: The Ship24 tracker ID this sensor represents.
        """
        super().__init__(coordinator)
        self._package_id = package_id
        self._attr_unique_id = f"{DOMAIN}_{package_id}"
        self._attr_name = None

    @property
    def _package_data(self) -> dict[str, Any] | None:
        """
        Return the latest parsed data for this package.

        :return: Dict with package fields, or None if not yet available.
        """
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self._package_id)

    @property
    def _display_name(self) -> str:
        """
        Return the friendly name if configured, otherwise the tracking number.

        :return: Human-readable identifier for this package.
        """
        data = self._package_data
        if data:
            alias = data.get("friendly_name", "")
            if alias:
                return alias
            tracking_number = data.get("tracking_number")
            if tracking_number:
                return tracking_number
        return self._package_id

    @property
    def native_value(self) -> str | None:
        """
        Return the current delivery status as the sensor state.

        :return: Human-readable status string, or None if data is unavailable.
        """
        data = self._package_data
        if data is None:
            return None
        return data.get("status")

    @property
    def icon(self) -> str:
        """
        Return a status-specific MDI icon.

        :return: MDI icon string.
        """
        data = self._package_data
        if data is None:
            return "mdi:package-variant-closed"
        return {
            "delivered": "mdi:package-variant-closed-check",
            "in_transit": "mdi:truck-fast",
            "out_for_delivery": "mdi:truck-delivery",
            "failed_attempt": "mdi:package-variant-remove",
            "exception": "mdi:alert-circle",
            "available_for_pickup": "mdi:store",
        }.get(data.get("status_code", ""), "mdi:package-variant-closed")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """
        Return additional state attributes for this package sensor.

        :return: Dict of attribute name to value.
        """
        data = self._package_data
        if data is None:
            return {}
        return {
            ATTR_TRACKER_ID: data.get("tracker_id", ""),
            ATTR_TRACKING_NUMBER: data.get("tracking_number", ""),
            ATTR_FRIENDLY_NAME: data.get("friendly_name", ""),
            ATTR_TITLE: data.get("title", ""),
            ATTR_CLIENT_TRACKER_ID: data.get("client_tracker_id", ""),
            ATTR_SHIPMENT_REFERENCE: data.get("shipment_reference", ""),
            ATTR_STATUS_CODE: data.get("status_code", ""),
            ATTR_COURIER: data.get("courier", ""),
            ATTR_DESTINATION_POST_CODE: data.get("destination_post_code", ""),
            ATTR_LAST_EVENT: data.get("last_event", ""),
            ATTR_LAST_EVENT_TIME: data.get("last_event_time", ""),
            ATTR_LAST_LOCATION: data.get("last_location", ""),
            ATTR_ESTIMATED_DELIVERY: data.get("estimated_delivery", ""),
            ATTR_ORIGIN_COUNTRY: data.get("origin_country", ""),
            ATTR_DESTINATION_COUNTRY: data.get("destination_country", ""),
            ATTR_EVENTS: data.get("events", []),
        }

    @property
    def device_info(self) -> dict[str, Any]:
        """
        Expose each package as its own HA device.

        :return: Dict with device identifiers and metadata.
        """
        return {
            "identifiers": {(DOMAIN, self._package_id)},
            "name": self._display_name,
            "manufacturer": "Ship24",
            "model": "Package Tracker",
        }
