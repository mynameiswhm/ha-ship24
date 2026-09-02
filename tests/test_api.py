"""Tests for the Ship24 API client."""

from __future__ import annotations

from typing import Any

from custom_components.ship24.api import Ship24Api


class FakeResponse:
    """Minimal aiohttp response test double."""

    def __init__(self, status: int, payload: dict[str, Any]) -> None:
        """Initialize the fake response."""
        self.status = status
        self._payload = payload

    async def __aenter__(self) -> "FakeResponse":
        """Enter the async context manager."""
        return self

    async def __aexit__(self, *_args: object) -> None:
        """Exit the async context manager."""

    async def json(self) -> dict[str, Any]:
        """Return the JSON payload."""
        return self._payload

    async def text(self) -> str:
        """Return a text representation of the payload."""
        return str(self._payload)


class FakeSession:
    """Minimal aiohttp session test double."""

    def __init__(self, get_payloads: list[dict[str, Any]] | None = None) -> None:
        """Initialize the fake session."""
        self.get_payloads = get_payloads or []
        self.post_payloads: list[dict[str, Any]] = []
        self.get_calls: list[dict[str, Any]] = []
        self.post_calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        """Record a GET call and return the next configured payload."""
        self.get_calls.append({"url": url, **kwargs})
        return FakeResponse(200, self.get_payloads.pop(0))

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        """Record a POST call and return the next configured payload."""
        self.post_calls.append({"url": url, **kwargs})
        return FakeResponse(201, self.post_payloads.pop(0))


async def test_tracking_results_use_tracker_id_read_endpoint():
    """Polling existing trackers uses GET by trackerId and does not POST."""
    session = FakeSession(
        [
            {
                "data": {
                    "trackings": [
                        {
                            "tracker": {
                                "trackerId": "A",
                                "trackingNumber": "X",
                            },
                            "shipment": {},
                            "events": [],
                        }
                    ]
                }
            }
        ]
    )
    api = Ship24Api("api_key", session)  # type: ignore[arg-type]

    results = await api.get_tracking_results_for_trackers(
        [{"trackerId": "A", "trackingNumber": "X", "courierCode": "dpd"}]
    )

    assert len(results) == 1
    assert session.get_calls[0]["url"].endswith("/trackers/A/results")
    assert session.post_calls == []


async def test_get_all_trackers_skips_archived_trackers():
    """Dashboard-archived trackers are not returned for monitoring."""
    session = FakeSession(
        [
            {
                "data": {
                    "trackers": [
                        {
                            "trackerId": "archived",
                            "trackingNumber": "X",
                            "isSubscribed": False,
                        },
                        {
                            "trackerId": "active",
                            "trackingNumber": "X",
                            "isSubscribed": True,
                        },
                    ]
                }
            }
        ]
    )
    api = Ship24Api("api_key", session)  # type: ignore[arg-type]

    trackers = await api.get_all_trackers()

    assert [tracker["trackerId"] for tracker in trackers] == ["active"]


async def test_create_tracker_uses_create_endpoint():
    """Explicit creation uses POST /trackers, not POST /trackers/track."""
    session = FakeSession()
    session.post_payloads.append(
        {
            "data": {
                "tracker": {
                    "trackerId": "A",
                    "trackingNumber": "X",
                }
            }
        }
    )
    api = Ship24Api("api_key", session)  # type: ignore[arg-type]

    tracker = await api.create_tracker({"trackingNumber": "X"})

    assert tracker["trackerId"] == "A"
    assert session.post_calls[0]["url"].endswith("/trackers")
    assert not session.post_calls[0]["url"].endswith("/trackers/track")
