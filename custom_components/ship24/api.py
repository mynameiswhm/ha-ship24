"""Ship24 API client for the Ship24 Package Tracker integration."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from .const import API_BASE_URL, API_TIMEOUT

_LOGGER = logging.getLogger(__name__)


def is_active_tracker(tracker: dict[str, Any]) -> bool:
    """
    Return true if a Ship24 tracker should be monitored.

    Ship24 marks dashboard-archived trackers with isSubscribed=false. Treat a
    missing value as active for compatibility with older or partial responses.
    """
    return tracker.get("isSubscribed") is not False


class Ship24ApiError(Exception):
    """Raised when the Ship24 API returns an error."""


class Ship24AuthError(Ship24ApiError):
    """Raised when the API key is invalid or unauthorized."""


class Ship24Api:
    """Async client for the Ship24 REST API."""

    def __init__(self, api_key: str, session: aiohttp.ClientSession) -> None:
        """
        Initialize the Ship24 API client.

        param api_key: The Ship24 API bearer token.
        param session: An active aiohttp ClientSession to use for requests.
        """
        self._api_key = api_key
        self._session = session

    @property
    def _headers(self) -> dict[str, str]:
        """
        Return the default HTTP headers for API requests.

        :return: Dict with Authorization and Content-Type headers.
        """
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    async def validate_api_key(self) -> bool:
        """
        Validate the API key using the couriers endpoint.

        Ship24 returns 403 (not 401) for invalid/missing API keys.

        :return: True if the API key is valid, raises Ship24AuthError otherwise.
        """
        url = f"{API_BASE_URL}/couriers"
        try:
            async with self._session.get(
                url,
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=API_TIMEOUT),
            ) as response:
                if response.status == 403:
                    raise Ship24AuthError("Invalid or unauthorized API key")
                return True
        except aiohttp.ClientError as err:
            raise Ship24ApiError(f"Connection error during validation: {err}") from err

    async def get_all_trackers(self) -> list[dict[str, Any]]:
        """
        Fetch active trackers registered in the Ship24 account.

        Handles pagination automatically, fetching up to 100 trackers per page.
        Dashboard-archived trackers are skipped because Ship24 reports them as
        isSubscribed=false and no longer uses them for tracking.

        :return: List of active tracker dicts from the account.
        """
        url = f"{API_BASE_URL}/trackers"
        all_trackers: list[dict[str, Any]] = []
        page = 1

        while True:
            try:
                async with self._session.get(
                    url,
                    headers=self._headers,
                    params={"page": page, "limit": 100},
                    timeout=aiohttp.ClientTimeout(total=API_TIMEOUT),
                ) as response:
                    if response.status == 403:
                        raise Ship24AuthError("Invalid or unauthorized API key")
                    if response.status != 200:
                        _LOGGER.warning(
                            "Ship24 GET /trackers returned status %d", response.status
                        )
                        break
                    data = await response.json()
                    trackers = data.get("data", {}).get("trackers", [])
                    if not trackers:
                        break
                    for tracker in trackers:
                        tracker_id = tracker.get("trackerId")
                        tracking_number = tracker.get("trackingNumber")
                        if tracker_id and tracking_number:
                            if not is_active_tracker(tracker):
                                _LOGGER.debug(
                                    "Skipping archived Ship24 tracker: trackerId=%s "
                                    "trackingNumber=%s",
                                    tracker_id,
                                    tracking_number,
                                )
                                continue
                            all_trackers.append(tracker)
                            _LOGGER.debug(
                                "Ship24 tracker discovered: trackerId=%s trackingNumber=%s",
                                tracker_id,
                                tracking_number,
                            )
                    if len(trackers) < 100:
                        break
                    page += 1
            except Ship24AuthError:
                raise
            except aiohttp.ClientError as err:
                _LOGGER.warning("Connection error fetching tracker list: %s", err)
                break

        _LOGGER.debug("Found %d tracker(s) in Ship24 account", len(all_trackers))
        return all_trackers

    async def get_tracking_results_for_trackers(
        self, trackers: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """
        Fetch tracking results for the given list of existing Ship24 trackers.

        Uses the read-only GET /public/v1/trackers/{trackerId}/results endpoint.
        This method must never create or mutate remote Ship24 trackers.

        param trackers: List of tracker metadata dicts returned by Ship24.

        :return: List of tracking result dicts, one per successfully fetched tracker.
        """
        if not trackers:
            return []

        results: list[dict[str, Any]] = []

        for tracker in trackers:
            tracker_id = tracker.get("trackerId")
            tracking_number = tracker.get("trackingNumber", "")
            if not tracker_id:
                continue

            url = f"{API_BASE_URL}/trackers/{tracker_id}/results"
            try:
                async with self._session.get(
                    url,
                    headers=self._headers,
                    timeout=aiohttp.ClientTimeout(total=API_TIMEOUT),
                ) as response:
                    if response.status == 403:
                        raise Ship24AuthError("Invalid or unauthorized API key")
                    if response.status not in (200, 201):
                        text = await response.text()
                        _LOGGER.warning(
                            "Ship24 returned status %d for trackerId=%s trackingNumber=%s: %s",
                            response.status,
                            tracker_id,
                            tracking_number,
                            text,
                        )
                        continue
                    data = await response.json()
                    trackings = data.get("data", {}).get("trackings", [])
                    for tracking in trackings:
                        if tracking:
                            tracking["tracker"] = {
                                **(tracking.get("tracker") or {}),
                                **tracker,
                            }
                            results.append(tracking)
            except Ship24AuthError:
                raise
            except aiohttp.ClientError as err:
                _LOGGER.warning(
                    "Connection error fetching trackerId=%s trackingNumber=%s: %s",
                    tracker_id,
                    tracking_number,
                    err,
                )
                continue

        _LOGGER.debug("Fetched %d tracking result(s) from Ship24", len(results))
        return results

    async def create_tracker(self, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Create a tracker from an explicit user action.

        Uses POST /public/v1/trackers. Polling code must not call this method.

        param payload: Ship24 tracker creation payload.

        :return: Created or existing tracker dict returned by Ship24.
        """
        url = f"{API_BASE_URL}/trackers"
        try:
            async with self._session.post(
                url,
                headers=self._headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=API_TIMEOUT),
            ) as response:
                if response.status == 403:
                    raise Ship24AuthError("Invalid or unauthorized API key")
                if response.status not in (200, 201):
                    text = await response.text()
                    raise Ship24ApiError(
                        f"Ship24 returned status {response.status} creating tracker: {text}"
                    )
                data = await response.json()
                tracker = data.get("data", {}).get("tracker")
                if not tracker:
                    raise Ship24ApiError("Ship24 create tracker response had no tracker")
                _LOGGER.debug(
                    "Ship24 tracker created/reused: trackerId=%s trackingNumber=%s",
                    tracker.get("trackerId"),
                    tracker.get("trackingNumber"),
                )
                return tracker
        except Ship24AuthError:
            raise
        except aiohttp.ClientError as err:
            raise Ship24ApiError(f"Connection error creating tracker: {err}") from err
