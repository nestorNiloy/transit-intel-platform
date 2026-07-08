"""
Standalone live-ingestion runner. Fetches real RE/RB departures for the
target Northern German stations and upserts them into transit_intel.db,
then refreshes station aggregates -- without needing the FastAPI server
running. Useful for a cron job / scheduled task.

Usage (from the project root):
    python scripts/ingest_live.py
    python scripts/ingest_live.py --duration 360
    python scripts/ingest_live.py --stations "Hamburg Hbf" "Kiel Hbf"
"""

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import analytics, crud, services  # noqa: E402
from app.database import AsyncSessionLocal, init_db  # noqa: E402


async def run(stations: list[str] | None, duration_minutes: int) -> None:
    await init_db()

    print(f"Fetching live regional departures (duration={duration_minutes}min)...")
    rows = await services.collect_live_regional_delays(
        station_names=stations, duration_minutes=duration_minutes
    )
    print(f"Fetched and normalized {len(rows)} RE/RB departures.")

    if not rows:
        print("Nothing to upsert (empty feed or all stations unreachable). Exiting.")
        return

    async with AsyncSessionLocal() as db:
        created, updated = await crud.upsert_route_delay_logs_by_trip_id(db, rows)
        print(f"Upserted: {created} created, {updated} updated.")

        refreshed = await analytics.refresh_station_aggregates(db)
        print(f"Refreshed aggregates for {refreshed} stations.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Live Deutsche Bahn regional delay ingestion")
    parser.add_argument(
        "--stations",
        nargs="+",
        default=None,
        help="Station names to poll (defaults to the built-in Hamburg/Schleswig-Holstein set)",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=180,
        help="How many minutes ahead to fetch departures for (default: 180)",
    )
    args = parser.parse_args()

    asyncio.run(run(args.stations, args.duration))


if __name__ == "__main__":
    main()
