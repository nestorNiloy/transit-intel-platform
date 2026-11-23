"""Tests for generated demo records and station aggregate consistency."""
import asyncio
from datetime import datetime

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import crud
from app.main import REGION_MAP, _generate_dummy_rows
from app.models import Base, RouteDelayLog, StationMetric


def test_generated_weekday_matches_scheduled_departure() -> None:
    rows = _generate_dummy_rows(100)
    assert rows
    for row in rows:
        assert row["day_of_week"] == row["scheduled_departure"].weekday()


def test_station_aggregate_refresh_removes_orphans_and_applies_region() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        async with sessions() as db:
            db.add(
                RouteDelayLog(
                    origin_station="Hamburg-Altona",
                    destination_station="Lübeck Hbf",
                    train_category="RE",
                    scheduled_departure=datetime(2026, 1, 5, 8, 0),
                    actual_departure=datetime(2026, 1, 5, 8, 2),
                    delay_seconds=120,
                    day_of_week=0,
                    hour_of_day=8,
                )
            )
            db.add(
                StationMetric(
                    station_name="Old Station",
                    region="Unbekannt",
                    total_tracked_trips=3,
                    average_delay_minutes=2,
                    reliability_score=90,
                )
            )
            await db.commit()

            await crud.upsert_station_metrics_from_logs(db, region_map=REGION_MAP)
            stations = await crud.get_station_metrics(db)
            by_name = {station.station_name: station for station in stations}

            assert "Old Station" not in by_name
            assert by_name["Hamburg-Altona"].region == "Norddeutschland"
            assert by_name["Hamburg-Altona"].total_tracked_trips == 1

        await engine.dispose()

    asyncio.run(scenario())
