"""DataUpdateCoordinator for the Ship24 Package Tracker integration."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import Ship24Api, Ship24ApiError, is_active_tracker
from .const import (
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    STATUS_MAP,
    SUPPRESSED_TRACKER_ID_PREFIX,
)

_LOGGER = logging.getLogger(__name__)


class Ship24Coordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator that fetches Ship24 data for all tracked packages."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: Ship24Api,
        tracking_numbers: list[str],
        package_aliases: dict[str, str] | None = None,
        suppressed_numbers: list[str] | None = None,
    ) -> None:
        """
        Initialize the Ship24 coordinator.

        param hass: The Home Assistant instance.
        param api: An initialized Ship24Api client.
        param tracking_numbers: List of manually configured tracking numbers.
        param package_aliases: Optional dict mapping tracking number to friendly name.
        param suppressed_numbers: List of tracking numbers or tracker-id
            suppression keys to exclude from results.

        :return: None
        """
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.api = api
        self.tracking_numbers: list[str] = list(tracking_numbers)
        self.package_aliases: dict[str, str] = package_aliases or {}
        self.suppressed_numbers, self.suppressed_tracker_ids = (
            _split_suppressed_entries(suppressed_numbers or [])
        )
        self.trackers: dict[str, dict[str, Any]] = {}

    async def _async_update_data(self) -> dict[str, Any]:
        """
        Fetch updated tracking data from the Ship24 API.

        Imports trackers from the Ship24 account and fetches results by
        trackerId. This refresh path is read-only and must not create trackers.

        :return: Dict keyed by Ship24 trackerId with parsed package data.
        """
        try:
            account_trackers = [
                tracker
                for tracker in await self.api.get_all_trackers()
                if is_active_tracker(tracker)
            ]
            trackers = [
                tracker
                for tracker in account_trackers
                if not self.is_tracker_suppressed(tracker)
            ]
            self.trackers = {
                tracker["trackerId"]: tracker
                for tracker in trackers
                if tracker.get("trackerId")
            }
            raw_trackings = await self.api.get_tracking_results_for_trackers(trackers)
        except Ship24ApiError as err:
            raise UpdateFailed(f"Error communicating with Ship24 API: {err}") from err

        fetched_ids: set[str] = set()
        result: dict[str, Any] = {}
        for tracking in raw_trackings:
            parsed = _parse_tracking(tracking, self.package_aliases)
            if parsed:
                tracker_id = parsed["tracker_id"]
                fetched_ids.add(tracker_id)
                result[tracker_id] = parsed

        for tracker_id, tracker in self.trackers.items():
            if tracker_id in fetched_ids:
                continue
            parsed = _parse_tracking({"tracker": tracker}, self.package_aliases)
            if parsed:
                result[tracker_id] = parsed

        configured_numbers = {number.upper() for number in self.tracking_numbers}
        remote_numbers = {
            str(tracker.get("trackingNumber", "")).upper()
            for tracker in account_trackers
            if tracker.get("trackingNumber")
        }
        missing_numbers = configured_numbers - remote_numbers - self.suppressed_numbers
        if missing_numbers:
            _LOGGER.debug(
                "Configured Ship24 tracking number(s) have no existing tracker and "
                "were not created during polling: %s",
                sorted(missing_numbers),
            )

        return result

    async def async_add_package(
        self,
        tracking_number: str,
        friendly_name: str = "",
    ) -> dict[str, Any]:
        """
        Add or import a package from an explicit user action.

        Existing Ship24 trackers with the same tracking number are reused so
        enriched dashboard metadata is not replaced by a bare API tracker.

        param tracking_number: The package tracking number to add.
        param friendly_name: Optional local friendly name.

        :return: The reused or created Ship24 tracker.
        """
        normalized_tracking_number = tracking_number.strip().upper()
        trackers = [
            tracker
            for tracker in await self.api.get_all_trackers()
            if is_active_tracker(tracker)
        ]
        matching_trackers = [
            tracker
            for tracker in trackers
            if str(tracker.get("trackingNumber", "")).upper()
            == normalized_tracking_number
        ]

        if matching_trackers:
            tracker = matching_trackers[0]
            if len(matching_trackers) > 1:
                _LOGGER.debug(
                    "Found %d existing Ship24 trackers for trackingNumber=%s; "
                    "reusing trackerId=%s",
                    len(matching_trackers),
                    normalized_tracking_number,
                    tracker.get("trackerId"),
                )
        else:
            payload: dict[str, Any] = {"trackingNumber": normalized_tracking_number}
            if friendly_name:
                payload["title"] = friendly_name
            tracker = await self.api.create_tracker(payload)

        if friendly_name:
            self.package_aliases[normalized_tracking_number] = friendly_name
        self.suppressed_numbers.discard(normalized_tracking_number)
        tracker_id = tracker.get("trackerId")
        if tracker_id:
            self.suppressed_tracker_ids.discard(str(tracker_id))
        await self.async_request_refresh()
        return tracker

    def add_suppressed_entries(self, entries: list[str]) -> None:
        """Add persisted suppression entries to the in-memory suppression sets."""
        numbers, tracker_ids = _split_suppressed_entries(entries)
        self.suppressed_numbers.update(numbers)
        self.suppressed_tracker_ids.update(tracker_ids)

    def is_tracker_suppressed(self, tracker: dict[str, Any]) -> bool:
        """Return true if a raw Ship24 tracker is suppressed."""
        tracker_id = str(tracker.get("trackerId") or "")
        tracking_number = str(tracker.get("trackingNumber") or "").upper()
        return (
            bool(tracker_id and tracker_id in self.suppressed_tracker_ids)
            or bool(tracking_number and tracking_number in self.suppressed_numbers)
        )

    def is_package_suppressed(self, package: dict[str, Any]) -> bool:
        """Return true if a parsed package is suppressed."""
        tracker_id = str(package.get("tracker_id") or "")
        tracking_number = str(package.get("tracking_number") or "").upper()
        return (
            bool(tracker_id and tracker_id in self.suppressed_tracker_ids)
            or bool(tracking_number and tracking_number in self.suppressed_numbers)
        )

    def get_spoken_summary(self) -> str:
        """
        Build a human-readable summary sentence for all tracked packages.

        Suitable for use as TTS output or a voice assistant response.

        :return: A spoken-language summary string of all package statuses.
        """
        if self.data is None:
            return "No package data available yet."

        packages = list(self.data.values())
        count = len(packages)

        if count == 0:
            return "You have no tracked packages."

        parts: list[str] = []
        for pkg in packages:
            name = pkg.get("friendly_name") or pkg["tracking_number"]
            status = pkg.get("status", "Unknown")
            last_event = pkg.get("last_event", "")
            location = pkg.get("last_location", "")
            last_event_time = pkg.get("last_event_time", "")
            eta = pkg.get("estimated_delivery", "")

            sentence = f"{name} is {status}"
            if last_event:
                sentence += f", last status: {last_event}"
            if location:
                sentence += f" in {location}"
            if last_event_time:
                event_date = (
                    last_event_time[:10]
                    if len(last_event_time) >= 10
                    else last_event_time
                )
                sentence += f" on {event_date}"
            if eta and "delivered" not in status.lower():
                eta_date = eta[:10] if len(eta) >= 10 else eta
                sentence += f", estimated delivery {eta_date}"
            parts.append(sentence)

        intro = (
            "You have 1 tracked package."
            if count == 1
            else f"You have {count} tracked packages."
        )
        return intro + " " + ". ".join(parts) + "."


def _parse_tracking(
    tracking: dict[str, Any],
    aliases: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """
    Parse a raw Ship24 tracking result into a flat dict for use by sensors.

    param tracking: A single tracking dict from the Ship24 API response.
    param aliases: Optional dict mapping tracking number to a friendly name.

    :return: Parsed dict with normalized fields, or None if tracking number missing.
    """
    tracker: dict[str, Any] = tracking.get("tracker", {})
    shipment: dict[str, Any] = tracking.get("shipment") or {}
    events: list[dict[str, Any]] = tracking.get("events", []) or []

    tracking_number: str | None = tracker.get("trackingNumber")
    tracker_id: str | None = tracker.get("trackerId")
    if not tracking_number or not tracker_id:
        return None

    raw_status = (
        shipment.get("statusCode")
        or shipment.get("statusCategory")
        or "pending"
    )
    friendly_status = STATUS_MAP.get(
        raw_status, raw_status.replace("_", " ").title()
    )

    sorted_events = sorted(
        events,
        key=lambda e: e.get("occurrenceDatetime", ""),
        reverse=True,
    )

    last_event: dict[str, Any] = sorted_events[0] if sorted_events else {}
    delivery: dict[str, Any] = shipment.get("delivery") or {}

    event_list = [
        {
            "status": e.get("status", ""),
            "datetime": e.get("occurrenceDatetime", ""),
            "location": e.get("location", ""),
        }
        for e in sorted_events[:10]
    ]

    aliases = aliases or {}
    friendly_name = (
        aliases.get(tracking_number, "")
        or aliases.get(tracking_number.upper(), "")
        or tracker.get("title", "")
    )

    return {
        "tracker_id": tracker_id,
        "tracking_number": tracking_number,
        "friendly_name": friendly_name,
        "status": friendly_status,
        "status_code": raw_status,
        "courier": _get_courier(tracker, last_event),
        "title": tracker.get("title", ""),
        "client_tracker_id": tracker.get("clientTrackerId") or "",
        "shipment_reference": tracker.get("shipmentReference") or "",
        "destination_post_code": tracker.get("destinationPostCode") or "",
        "last_event": last_event.get("status", ""),
        "last_event_time": last_event.get("occurrenceDatetime", ""),
        "last_location": last_event.get("location", ""),
        "estimated_delivery": delivery.get("estimatedDeliveryDate") or "",
        "origin_country": (
            shipment.get("originCountryCode") or tracker.get("originCountryCode") or ""
        ),
        "destination_country": (
            shipment.get("destinationCountryCode")
            or tracker.get("destinationCountryCode")
            or ""
        ),
        "events": event_list,
    }


def make_suppressed_tracker_id_key(tracker_id: Any) -> str:
    """Build a persisted suppression key for one Ship24 tracker ID."""
    normalized_tracker_id = str(tracker_id or "").strip()
    if not normalized_tracker_id:
        return ""
    return f"{SUPPRESSED_TRACKER_ID_PREFIX}{normalized_tracker_id}"


def normalize_suppressed_entry(entry: Any) -> str:
    """Normalize a persisted suppression entry for stable comparisons."""
    normalized_entry = str(entry or "").strip()
    if not normalized_entry:
        return ""

    prefix_length = len(SUPPRESSED_TRACKER_ID_PREFIX)
    if normalized_entry[:prefix_length].lower() == SUPPRESSED_TRACKER_ID_PREFIX:
        tracker_id = normalized_entry[prefix_length:].strip()
        return make_suppressed_tracker_id_key(tracker_id)

    return normalized_entry.upper()


def _split_suppressed_entries(entries: list[str]) -> tuple[set[str], set[str]]:
    """Split persisted suppression entries into tracking numbers and tracker IDs."""
    suppressed_numbers: set[str] = set()
    suppressed_tracker_ids: set[str] = set()

    for entry in entries:
        normalized_entry = normalize_suppressed_entry(entry)
        if not normalized_entry:
            continue
        if normalized_entry.startswith(SUPPRESSED_TRACKER_ID_PREFIX):
            suppressed_tracker_ids.add(
                normalized_entry[len(SUPPRESSED_TRACKER_ID_PREFIX) :]
            )
        else:
            suppressed_numbers.add(normalized_entry)

    return suppressed_numbers, suppressed_tracker_ids


def _get_courier(tracker: dict[str, Any], last_event: dict[str, Any]) -> str:
    """
    Extract the courier name from tracker or event data.

    param tracker: The tracker dict from Ship24 response.
    param last_event: The most recent tracking event dict.

    :return: Courier name string, empty string if unknown.
    """
    for field in ("courierCode", "slug", "sourceCode"):
        value = tracker.get(field) or last_event.get(field)
        if value:
            if isinstance(value, list):
                return ", ".join(str(item) for item in value if item)
            return str(value)
    return ""
