"""Tests for the Ship24 coordinator and data parsing functions."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from custom_components.ship24.coordinator import (
    Ship24Coordinator,
    _get_courier,
    _parse_tracking,
    make_suppressed_tracker_id_key,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(filename: str) -> dict:
    """Load a JSON fixture file from the fixtures directory."""
    with open(FIXTURES_DIR / filename) as f:
        return json.load(f)


def make_coordinator(
    data: dict | None, tracking_numbers: list | None = None
) -> Ship24Coordinator:
    """Create a Ship24Coordinator with mocked hass and api."""
    hass = MagicMock()
    api = MagicMock()
    coordinator = Ship24Coordinator(
        hass=hass, api=api, tracking_numbers=tracking_numbers or []
    )
    coordinator.data = data
    return coordinator


def make_tracker(
    tracker_id: str = "tracker-A",
    tracking_number: str = "TRACKING-X",
    **kwargs,
) -> dict:
    """Build a Ship24 tracker metadata dict."""
    return {
        "trackerId": tracker_id,
        "trackingNumber": tracking_number,
        "isSubscribed": True,
        "isTracked": True,
        **kwargs,
    }


def make_tracking(tracker: dict, status_code: str = "in_transit") -> dict:
    """Build a minimal tracking result for a tracker."""
    return {
        "tracker": tracker,
        "shipment": {
            "statusCode": status_code,
            "statusCategory": status_code,
            "originCountryCode": tracker.get("originCountryCode", ""),
            "destinationCountryCode": tracker.get("destinationCountryCode", ""),
        },
        "events": [],
    }


def test_parse_tracking_in_transit():
    """Parse a standard in-transit tracking result."""
    data = load_fixture("tracking_in_transit.json")
    result = _parse_tracking(data)

    assert result is not None
    assert result["tracker_id"] == "test-tracker-id-001"
    assert result["tracking_number"] == "1Z999AA10123456784"
    assert result["status"] == "In Transit"
    assert result["status_code"] == "in_transit"
    assert result["last_event"] == "Departed facility"
    assert result["last_location"] == "Frankfurt, DE"
    assert result["last_event_time"] == "2024-03-15T14:30:00.000Z"
    assert result["estimated_delivery"] == "2024-03-16T00:00:00.000Z"
    assert result["origin_country"] == "US"
    assert result["destination_country"] == "DE"
    assert result["courier"] == "ups"
    assert result["friendly_name"] == ""
    assert len(result["events"]) == 2


def test_parse_tracking_delivered():
    """Parse a delivered package result."""
    data = load_fixture("tracking_delivered.json")
    result = _parse_tracking(data)

    assert result is not None
    assert result["tracking_number"] == "RR123456789CN"
    assert result["status"] == "Delivered"
    assert result["status_code"] == "delivered"
    assert result["last_event"] == "Delivered"
    assert result["last_location"] == "Budapest, HU"
    assert result["origin_country"] == "CN"
    assert result["destination_country"] == "HU"
    assert result["estimated_delivery"] == ""


def test_parse_tracking_no_events():
    """Parse a result with no events and null shipment."""
    data = load_fixture("tracking_no_data.json")
    result = _parse_tracking(data)

    assert result is not None
    assert result["tracking_number"] == "JD014600000000"
    assert result["status"] == "Pending"
    assert result["last_event"] == ""
    assert result["last_location"] == ""
    assert result["events"] == []


def test_parse_tracking_with_alias():
    """Alias is included in the parsed result when provided."""
    data = load_fixture("tracking_in_transit.json")
    result = _parse_tracking(data, aliases={"1Z999AA10123456784": "Amazon Order"})
    assert result["friendly_name"] == "Amazon Order"


def test_parse_tracking_alias_not_matching():
    """Unrelated alias does not affect the tracking number."""
    data = load_fixture("tracking_in_transit.json")
    result = _parse_tracking(data, aliases={"OTHER_NUMBER": "Some Name"})
    assert result["friendly_name"] == ""


def test_parse_tracking_missing_tracking_number():
    """Returns None when tracker has no trackingNumber field."""
    result = _parse_tracking({"tracker": {}, "shipment": {}, "events": []})
    assert result is None


def test_parse_tracking_missing_tracker_id():
    """Returns None when tracker has no trackerId field."""
    result = _parse_tracking(
        {"tracker": {"trackingNumber": "TRACKING-X"}, "shipment": {}, "events": []}
    )
    assert result is None


def test_parse_tracking_null_shipment():
    """Handles null shipment gracefully and defaults to Pending status."""
    data = load_fixture("tracking_in_transit.json")
    data = dict(data)
    data["shipment"] = None
    result = _parse_tracking(data)

    assert result is not None
    assert result["status"] == "Pending"
    assert result["estimated_delivery"] == ""


def test_parse_tracking_events_sorted_newest_first():
    """Most recent event is the first in the list and returned as last_event."""
    data = load_fixture("tracking_in_transit.json")
    result = _parse_tracking(data)

    assert result["last_event"] == "Departed facility"
    assert result["events"][0]["status"] == "Departed facility"


def test_get_courier_from_tracker_courierCode():
    """Courier is read from tracker courierCode field."""
    assert _get_courier({"courierCode": "ups"}, {}) == "ups"


def test_get_courier_from_tracker_slug():
    """Courier falls back to slug field when courierCode is absent."""
    assert _get_courier({"slug": "dhl"}, {}) == "dhl"


def test_get_courier_from_event_sourceCode():
    """Courier is read from event sourceCode when tracker has no courier fields."""
    assert _get_courier({}, {"sourceCode": "fedex"}) == "fedex"


def test_get_courier_empty():
    """Returns empty string when no courier info is available."""
    assert _get_courier({}, {}) == ""


def test_spoken_summary_no_data():
    """Returns a safe message when coordinator data is None."""
    coordinator = make_coordinator(None)
    assert coordinator.get_spoken_summary() == "No package data available yet."


def test_spoken_summary_empty_dict():
    """Returns a message when there are no tracked packages."""
    coordinator = make_coordinator({})
    assert coordinator.get_spoken_summary() == "You have no tracked packages."


def test_spoken_summary_single_package_with_alias():
    """Single package summary uses the friendly name, includes location and ETA."""
    coordinator = make_coordinator(
        {
            "test-tracker-id-001": {
                "tracking_number": "1Z999AA10123456784",
                "friendly_name": "Amazon Order",
                "status": "In Transit",
                "last_location": "Frankfurt, DE",
                "estimated_delivery": "2024-03-16T00:00:00.000Z",
            }
        }
    )
    summary = coordinator.get_spoken_summary()
    assert "1 tracked package" in summary
    assert "Amazon Order is In Transit" in summary
    assert "Frankfurt, DE" in summary
    assert "2024-03-16" in summary


def test_spoken_summary_uses_tracking_number_when_no_alias():
    """Tracking number is used in the summary when no friendly name is set."""
    coordinator = make_coordinator(
        {
            "test-tracker-id-001": {
                "tracking_number": "1Z999AA10123456784",
                "friendly_name": "",
                "status": "In Transit",
                "last_location": "",
                "estimated_delivery": "",
            }
        }
    )
    summary = coordinator.get_spoken_summary()
    assert "1Z999AA10123456784 is In Transit" in summary


def test_spoken_summary_delivered_omits_eta():
    """Delivered packages do not include estimated delivery in the summary."""
    coordinator = make_coordinator(
        {
            "test-tracker-id-002": {
                "tracking_number": "RR123456789CN",
                "friendly_name": "AliExpress",
                "status": "Delivered",
                "last_location": "Budapest",
                "estimated_delivery": "2024-03-16T00:00:00.000Z",
            }
        }
    )
    summary = coordinator.get_spoken_summary()
    assert "estimated delivery" not in summary


def test_spoken_summary_multiple_packages():
    """Summary mentions total count and all package names."""
    coordinator = make_coordinator(
        {
            "tracker-A": {
                "tracking_number": "AAA",
                "friendly_name": "Package A",
                "status": "In Transit",
                "last_location": "",
                "estimated_delivery": "",
            },
            "tracker-B": {
                "tracking_number": "BBB",
                "friendly_name": "Package B",
                "status": "Delivered",
                "last_location": "",
                "estimated_delivery": "",
            },
        }
    )
    summary = coordinator.get_spoken_summary()
    assert "2 tracked packages" in summary
    assert "Package A" in summary
    assert "Package B" in summary


async def test_coordinator_async_update_data_parses_results():
    """Coordinator fetches data via API and returns correctly parsed tracking dicts."""
    api = AsyncMock()
    tracker = make_tracker("test-tracker-id-001", "1Z999AA10123456784")
    api.get_all_trackers.return_value = [tracker]
    api.get_tracking_results_for_trackers.return_value = [
        load_fixture("tracking_in_transit.json")
    ]

    coordinator = Ship24Coordinator(
        hass=MagicMock(),
        api=api,
        tracking_numbers=["1Z999AA10123456784"],
        package_aliases={"1Z999AA10123456784": "Amazon Order"},
    )

    result = await coordinator._async_update_data()

    assert "test-tracker-id-001" in result
    assert result["test-tracker-id-001"]["status"] == "In Transit"
    assert result["test-tracker-id-001"]["friendly_name"] == "Amazon Order"
    api.get_tracking_results_for_trackers.assert_called_once_with([tracker])


async def test_coordinator_async_update_data_empty():
    """Coordinator returns empty dict when API returns no results."""
    api = AsyncMock()
    api.get_all_trackers.return_value = []
    api.get_tracking_results_for_trackers.return_value = []

    coordinator = Ship24Coordinator(hass=MagicMock(), api=api, tracking_numbers=[])
    result = await coordinator._async_update_data()
    assert result == {}


async def test_coordinator_async_update_data_skips_missing_tracker():
    """Coordinator skips entries where the tracking number is absent in the response."""
    api = AsyncMock()
    api.get_all_trackers.return_value = []
    api.get_tracking_results_for_trackers.return_value = [
        {"tracker": {}, "shipment": {}, "events": []}
    ]

    coordinator = Ship24Coordinator(hass=MagicMock(), api=api, tracking_numbers=["BAD"])
    result = await coordinator._async_update_data()
    assert result == {}


async def test_existing_enriched_dashboard_tracker_is_read_only():
    """Existing dashboard trackers are polled by trackerId without creating duplicates."""
    tracker = make_tracker(
        "A",
        "X",
        courierCode="dpd",
        destinationPostCode="08008",
        destinationCountryCode="ES",
    )
    api = AsyncMock()
    api.get_all_trackers.return_value = [tracker]
    api.get_tracking_results_for_trackers.return_value = [make_tracking(tracker)]
    api.create_tracker = AsyncMock()

    coordinator = Ship24Coordinator(hass=MagicMock(), api=api, tracking_numbers=[])

    first = await coordinator._async_update_data()
    second = await coordinator._async_update_data()

    assert list(first) == ["A"]
    assert list(second) == ["A"]
    assert second["A"]["courier"] == "dpd"
    assert second["A"]["destination_post_code"] == "08008"
    api.create_tracker.assert_not_called()
    assert api.get_tracking_results_for_trackers.call_count == 2


async def test_remote_tracker_metadata_changes_are_reflected_without_create():
    """A dashboard metadata edit keeps the same trackerId and updates HA data."""
    generic = make_tracker("A", "X", courierCode="dhl")
    corrected = make_tracker("A", "X", courierCode="dhl-de")
    api = AsyncMock()
    api.get_all_trackers.side_effect = [[generic], [corrected]]
    api.get_tracking_results_for_trackers.side_effect = [
        [make_tracking(generic)],
        [make_tracking(corrected)],
    ]
    api.create_tracker = AsyncMock()

    coordinator = Ship24Coordinator(hass=MagicMock(), api=api, tracking_numbers=[])

    first = await coordinator._async_update_data()
    second = await coordinator._async_update_data()

    assert first["A"]["courier"] == "dhl"
    assert second["A"]["courier"] == "dhl-de"
    api.create_tracker.assert_not_called()


async def test_trackers_with_same_tracking_number_are_not_collapsed():
    """Two Ship24 trackers can share a tracking number and remain distinct."""
    tracker_a = make_tracker("A", "X", courierCode="dpd")
    tracker_b = make_tracker("B", "X", courierCode="dhl")
    api = AsyncMock()
    api.get_all_trackers.return_value = [tracker_a, tracker_b]
    api.get_tracking_results_for_trackers.return_value = [
        make_tracking(tracker_a),
        make_tracking(tracker_b),
    ]
    api.create_tracker = AsyncMock()

    coordinator = Ship24Coordinator(hass=MagicMock(), api=api, tracking_numbers=[])
    result = await coordinator._async_update_data()

    assert set(result) == {"A", "B"}
    assert result["A"]["tracking_number"] == "X"
    assert result["B"]["tracking_number"] == "X"
    assert result["A"]["courier"] == "dpd"
    assert result["B"]["courier"] == "dhl"
    api.create_tracker.assert_not_called()


async def test_suppressed_numbers_are_normalized_before_compare():
    """Persisted legacy suppressed tracking numbers are matched case-insensitively."""
    tracker = make_tracker("A", "X")
    api = AsyncMock()
    api.get_all_trackers.return_value = [tracker]
    api.get_tracking_results_for_trackers.return_value = []

    coordinator = Ship24Coordinator(
        hass=MagicMock(),
        api=api,
        tracking_numbers=[],
        suppressed_numbers=["x"],
    )

    result = await coordinator._async_update_data()

    assert result == {}
    assert coordinator.suppressed_numbers == {"X"}
    api.get_tracking_results_for_trackers.assert_awaited_once_with([])


async def test_suppressed_tracker_id_does_not_hide_duplicate_tracking_number():
    """A tracker-id suppression leaves duplicate tracking numbers visible."""
    tracker_a = make_tracker("A", "X", courierCode="dpd")
    tracker_b = make_tracker("B", "X", courierCode="dhl")
    api = AsyncMock()
    api.get_all_trackers.return_value = [tracker_a, tracker_b]
    api.get_tracking_results_for_trackers.return_value = [make_tracking(tracker_b)]

    coordinator = Ship24Coordinator(
        hass=MagicMock(),
        api=api,
        tracking_numbers=[],
        suppressed_numbers=[make_suppressed_tracker_id_key("A")],
    )

    result = await coordinator._async_update_data()

    assert list(result) == ["B"]
    assert result["B"]["tracking_number"] == "X"
    api.get_tracking_results_for_trackers.assert_awaited_once_with([tracker_b])


async def test_integration_reload_remains_read_only():
    """Repeated coordinator setup/update cycles do not create trackers."""
    tracker = make_tracker("A", "X")
    api = AsyncMock()
    api.get_all_trackers.return_value = [tracker]
    api.get_tracking_results_for_trackers.return_value = [make_tracking(tracker)]
    api.create_tracker = AsyncMock()

    first = Ship24Coordinator(hass=MagicMock(), api=api, tracking_numbers=[])
    second = Ship24Coordinator(hass=MagicMock(), api=api, tracking_numbers=[])

    await first._async_update_data()
    await second._async_update_data()

    api.create_tracker.assert_not_called()
    assert api.get_all_trackers.call_count == 2


async def test_explicit_add_reuses_existing_tracker():
    """Explicit package add imports an existing tracker instead of creating one."""
    tracker = make_tracker("A", "X", courierCode="dpd")
    api = AsyncMock()
    api.get_all_trackers.return_value = [tracker]
    api.create_tracker = AsyncMock()

    coordinator = Ship24Coordinator(hass=MagicMock(), api=api, tracking_numbers=[])
    coordinator.async_request_refresh = AsyncMock()

    result = await coordinator.async_add_package("x", "Package X")

    assert result == tracker
    assert coordinator.package_aliases["X"] == "Package X"
    api.create_tracker.assert_not_called()
    coordinator.async_request_refresh.assert_awaited_once()


async def test_explicit_add_creates_tracker_when_missing():
    """Only an explicit add operation creates a new Ship24 tracker."""
    tracker = make_tracker("C", "Y")
    api = AsyncMock()
    api.get_all_trackers.return_value = []
    api.create_tracker.return_value = tracker

    coordinator = Ship24Coordinator(hass=MagicMock(), api=api, tracking_numbers=[])
    coordinator.async_request_refresh = AsyncMock()

    result = await coordinator.async_add_package("y", "Package Y")

    assert result == tracker
    api.create_tracker.assert_awaited_once_with(
        {"trackingNumber": "Y", "title": "Package Y"}
    )
    coordinator.async_request_refresh.assert_awaited_once()
