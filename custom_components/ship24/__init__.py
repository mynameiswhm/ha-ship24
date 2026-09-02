"""The Ship24 Package Tracker integration."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv

from .api import Ship24Api
from .const import (
    CONF_PACKAGE_ALIASES,
    CONF_SUPPRESSED_NUMBERS,
    CONF_TRACKING_NUMBERS,
    DOMAIN,
    SERVICE_ADD_PACKAGE,
    SERVICE_REMOVE_PACKAGE,
)
from .coordinator import (
    Ship24Coordinator,
    make_suppressed_tracker_id_key,
    normalize_suppressed_entry,
)
from .intent import async_setup_intents

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

REMOVE_PACKAGE_SCHEMA = vol.Schema(
    {
        vol.Optional("tracking_number"): cv.string,
    }
)

ADD_PACKAGE_SCHEMA = vol.Schema(
    {
        vol.Required("tracking_number"): cv.string,
        vol.Optional("friendly_name", default=""): cv.string,
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Set up Ship24 from a config entry.

    Creates the API client, coordinator, registers services, and forwards
    platform setup to the sensor platform.

    param hass: The Home Assistant instance.
    param entry: The config entry to set up.

    :return: True if setup succeeded.
    """
    hass.data.setdefault(DOMAIN, {})

    api_key: str = entry.data[CONF_API_KEY]
    tracking_numbers: list[str] = entry.options.get(CONF_TRACKING_NUMBERS, [])
    package_aliases: dict[str, str] = entry.options.get(CONF_PACKAGE_ALIASES, {})
    suppressed_numbers: list[str] = entry.options.get(CONF_SUPPRESSED_NUMBERS, [])

    session = async_get_clientsession(hass)
    api = Ship24Api(api_key=api_key, session=session)

    coordinator = Ship24Coordinator(
        hass=hass,
        api=api,
        tracking_numbers=tracking_numbers,
        package_aliases=package_aliases,
        suppressed_numbers=suppressed_numbers,
    )

    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _register_services(hass, entry)
    await async_setup_intents(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Unload a Ship24 config entry and clean up resources.

    param hass: The Home Assistant instance.
    param entry: The config entry to unload.

    :return: True if unload succeeded.
    """
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok


def _register_services(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """
    Register Ship24 services for managing tracked packages.

    Services are only registered once per HA instance (idempotent check).

    param hass: The Home Assistant instance.
    param entry: The config entry used to persist suppressed tracking numbers.

    :return: None
    """
    async def handle_remove_package(call: ServiceCall) -> None:
        """
        Handle the ship24.remove_package service call.

        If tracking_number is provided, suppresses that single package.
        If omitted, suppresses all currently delivered packages.

        Updates the coordinator in memory immediately and persists the suppressed
        list to config entry options — no integration reload is needed.

        param call: The service call data, optionally containing 'tracking_number'.

        :return: None
        """
        coordinator: Ship24Coordinator | None = hass.data.get(DOMAIN, {}).get(
            entry.entry_id
        )

        raw_tn: str = call.data.get("tracking_number", "").strip().upper()

        if raw_tn:
            to_suppress = [raw_tn]
        else:
            if not coordinator or not coordinator.data:
                _LOGGER.warning(
                    "No coordinator data available to find delivered packages"
                )
                return
            to_suppress = [
                make_suppressed_tracker_id_key(pkg.get("tracker_id"))
                for pkg in coordinator.data.values()
                if pkg.get("status_code") == "delivered"
            ]
            to_suppress = [entry for entry in to_suppress if entry]
            if not to_suppress:
                _LOGGER.info("No delivered packages found to suppress")
                return

        suppressed: list[str] = [
            normalized_entry
            for suppressed_entry in entry.options.get(CONF_SUPPRESSED_NUMBERS, [])
            if (normalized_entry := normalize_suppressed_entry(suppressed_entry))
        ]
        added = [
            suppressed_entry
            for suppressed_entry in to_suppress
            if suppressed_entry not in suppressed
        ]

        if not added:
            _LOGGER.info("All requested package(s) already suppressed")
            return

        suppressed.extend(added)

        # Update coordinator in memory immediately so sensors reflect the change
        if coordinator:
            coordinator.add_suppressed_entries(added)
            if coordinator.data:
                new_data = {
                    package_id: pkg
                    for package_id, pkg in coordinator.data.items()
                    if not coordinator.is_package_suppressed(pkg)
                }
                coordinator.async_set_updated_data(new_data)

        # Persist suppressed list to config entry options (no reload needed)
        hass.config_entries.async_update_entry(
            entry,
            options={**entry.options, CONF_SUPPRESSED_NUMBERS: suppressed},
        )

        _LOGGER.info("Suppressed %d package(s): %s", len(added), added)

    async def handle_add_package(call: ServiceCall) -> None:
        """
        Handle the ship24.add_package service call.

        This is the only integration path that creates a Ship24 tracker. If
        Ship24 already has a tracker for the tracking number, the service imports
        that tracker and preserves any dashboard metadata instead.
        """
        coordinator: Ship24Coordinator | None = hass.data.get(DOMAIN, {}).get(
            entry.entry_id
        )
        if coordinator is None:
            _LOGGER.warning("No coordinator data available to add package")
            return

        tracking_number: str = call.data["tracking_number"].strip().upper()
        friendly_name: str = call.data.get("friendly_name", "").strip()

        tracker = await coordinator.async_add_package(tracking_number, friendly_name)

        package_aliases: dict[str, str] = dict(
            entry.options.get(CONF_PACKAGE_ALIASES, {})
        )
        if friendly_name:
            package_aliases[tracking_number] = friendly_name

        tracking_numbers: list[str] = list(
            entry.options.get(CONF_TRACKING_NUMBERS, [])
        )
        if tracking_number not in tracking_numbers:
            tracking_numbers.append(tracking_number)

        suppressed_tracker_id = make_suppressed_tracker_id_key(tracker.get("trackerId"))
        suppressed: list[str] = []
        for suppressed_entry in entry.options.get(CONF_SUPPRESSED_NUMBERS, []):
            normalized_entry = normalize_suppressed_entry(suppressed_entry)
            if normalized_entry in (tracking_number, suppressed_tracker_id):
                continue
            suppressed.append(normalized_entry)

        hass.config_entries.async_update_entry(
            entry,
            options={
                **entry.options,
                CONF_TRACKING_NUMBERS: tracking_numbers,
                CONF_PACKAGE_ALIASES: package_aliases,
                CONF_SUPPRESSED_NUMBERS: suppressed,
            },
        )

        _LOGGER.info(
            "Added Ship24 package trackingNumber=%s trackerId=%s",
            tracking_number,
            tracker.get("trackerId"),
        )

    if not hass.services.has_service(DOMAIN, SERVICE_ADD_PACKAGE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_ADD_PACKAGE,
            handle_add_package,
            schema=ADD_PACKAGE_SCHEMA,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_REMOVE_PACKAGE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_REMOVE_PACKAGE,
            handle_remove_package,
            schema=REMOVE_PACKAGE_SCHEMA,
        )
    _LOGGER.debug("Ship24 services registered")
