"""
services.py -- live ingestion client for real Deutsche Bahn regional delay data.

Talks to https://v6.db.transport.rest, a free, unauthenticated community
wrapper around DB's HAFAS backend (see https://github.com/derhuerst/db-rest).
It is *not* an official Deutsche Bahn product: no API key is required, but
it is run best-effort and rate-limited (roughly 100 req/min). Treat every
call as something that can time out, 429, or 5xx, and degrade gracefully.

Pipeline:
  1. resolve each target station name -> HAFAS stop id            (/locations)
  2. fetch upcoming departures at that stop, filtered server-side
     to RE/RB only                                                 (/stops/:id/departures)
  3. normalize each raw departure into a RouteDelayLog-shaped dict,
     computing delay_seconds from scheduled vs. realtime timestamps
  4. hand the normalized rows to crud.upsert_route_delay_logs_by_trip_id
     (done by the caller in main.py / scripts/ingest_live.py)
"""

import asyncio
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from .models import TrainCategory

BASE_URL = "https://v6.db.transport.rest"
REQUEST_TIMEOUT_SECONDS = 10.0
MAX_RETRIES = 2

# Target regional network: Hamburg / Schleswig-Holstein plus the connecting
# hubs named in the brief (Bremen, Hannover).
TARGET_STATIONS: list[str] = [
    "Hamburg Hbf",
    "Hamburg-Altona",
    "Lübeck Hbf",
    "Bremen Hbf",
    "Hannover Hbf",
]

# HAFAS `line.product` string -> our TrainCategory enum. Only RE/RB are
# mapped on purpose: anything else (nationalExpress, national, suburban,
# bus, ...) normalizes to None and gets dropped, per the brief's explicit
# instruction to bypass long-distance ICE/IC traffic and stay regional-only.
PRODUCT_TO_CATEGORY: dict[str, TrainCategory] = {
    "regionalExpress": TrainCategory.RE,
    "regional": TrainCategory.RB,
}

# Ask the server to only send back RE/RB departures in the first place, so
# we don't pay the bandwidth/parsing cost for ICE/IC/S-Bahn/bus/etc.
REGIONAL_ONLY_FILTERS: dict[str, str] = {
    "nationalExpress": "false",
    "national": "false",
    "regionalExpress": "true",
    "regional": "true",
    "suburban": "false",
    "bus": "false",
    "ferry": "false",
    "subway": "false",
    "tram": "false",
    "taxi": "false",
}


class DBTransportClient:
    """Thin async client for the db.transport.rest wrapper API."""

    def __init__(self, base_url: str = BASE_URL, timeout: float = REQUEST_TIMEOUT_SECONDS):
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers={
                "Accept": "application/json",
                "User-Agent": "transit-intel-platform/1.0 (+https://github.com/)",
            },
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "DBTransportClient":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.close()

    async def _get(self, path: str, params: dict[str, Any]) -> Optional[Any]:
        """GET with bounded retries. Returns None (never raises) on final failure."""
        last_error: Optional[Exception] = None

        for attempt in range(MAX_RETRIES + 1):
            try:
                response = await self._client.get(path, params=params)
                response.raise_for_status()
                return response.json()
            except httpx.TimeoutException as exc:
                last_error = exc
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                # Retry rate limiting / server errors; don't retry 4xx like 404.
                if status in (429, 500, 502, 503, 504) and attempt < MAX_RETRIES:
                    last_error = exc
                else:
                    print(f"[services] HTTP {status} for GET {path} params={params}: {exc}")
                    return None
            except httpx.RequestError as exc:
                last_error = exc

            await asyncio.sleep(0.5 * (attempt + 1))

        print(f"[services] Giving up on GET {path} after {MAX_RETRIES + 1} attempts: {last_error}")
        return None

    async def resolve_stop_id(self, station_name: str) -> Optional[dict]:
        """Resolve a human station name to a HAFAS stop id via /locations."""
        data = await self._get(
            "/locations",
            {
                "query": station_name,
                "results": 1,
                "stops": "true",
                "addresses": "false",
                "poi": "false",
            },
        )
        if not data:
            return None
        for item in data:
            if item.get("type") == "stop" and item.get("id"):
                return {"id": item["id"], "name": item.get("name", station_name)}
        return None

    async def fetch_regional_departures(
        self, stop_id: str, duration_minutes: int = 180, results: int = 60
    ) -> list[dict]:
        """Upcoming RE/RB departures at a stop, including realtime delay data."""
        params: dict[str, Any] = {
            "duration": duration_minutes,
            "results": results,
            "remarks": "false",
            **REGIONAL_ONLY_FILTERS,
        }
        data = await self._get(f"/stops/{stop_id}/departures", params)
        return data or []


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def normalize_departure(raw: dict, origin_station: str) -> Optional[dict]:
    """
    Convert one raw HAFAS departure record into a RouteDelayLog-shaped dict,
    or None if the record isn't usable (wrong category, missing timestamps).

    delay_seconds is computed by diffing the realtime timestamp (`when`)
    against the scheduled timestamp (`plannedWhen`); the API's own `delay`
    field (already in seconds) is preferred when present, since it reflects
    upstream HAFAS's own realtime computation more precisely than a naive
    timestamp diff would in edge cases (e.g. day rollovers, cancellations).
    """
    line = raw.get("line") or {}
    category = PRODUCT_TO_CATEGORY.get(line.get("product"))
    if category is None:
        return None  # not RE/RB -- defense-in-depth beyond the server-side filter

    planned = _parse_iso(raw.get("plannedWhen"))
    actual = _parse_iso(raw.get("when")) or planned
    if planned is None or actual is None:
        return None  # can't compute a delay without a scheduled timestamp

    delay_seconds = raw.get("delay")
    if delay_seconds is None:
        delay_seconds = (actual - planned).total_seconds()
    delay_seconds = max(0, int(delay_seconds))

    destination = raw.get("direction") or "Unknown"

    return {
        "source_trip_id": raw.get("tripId"),
        "origin_station": origin_station,
        "destination_station": destination,
        "train_category": category,
        "scheduled_departure": planned.astimezone(timezone.utc).replace(tzinfo=None),
        "actual_departure": actual.astimezone(timezone.utc).replace(tzinfo=None),
        "delay_seconds": delay_seconds,
        "day_of_week": planned.weekday(),  # 0=Monday .. 6=Sunday, matches our schema
        "hour_of_day": planned.hour,
    }


async def collect_live_regional_delays(
    station_names: Optional[list[str]] = None,
    duration_minutes: int = 180,
) -> list[dict]:
    """
    Resolve each target station, fetch its regional (RE/RB) departures,
    normalize them, and return a flat list of RouteDelayLog-shaped dicts.
    De-duplication/upserting by tripId happens later, in crud.py.
    """
    station_names = station_names or TARGET_STATIONS
    normalized_rows: list[dict] = []

    async with DBTransportClient() as client:
        for name in station_names:
            stop = await client.resolve_stop_id(name)
            if stop is None:
                print(f"[services] Could not resolve station: {name!r} -- skipping")
                continue

            departures = await client.fetch_regional_departures(
                stop["id"], duration_minutes=duration_minutes
            )
            for raw in departures:
                row = normalize_departure(raw, origin_station=stop["name"])
                if row is not None and row["source_trip_id"] is not None:
                    normalized_rows.append(row)

    return normalized_rows
