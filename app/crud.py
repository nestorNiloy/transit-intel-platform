"""
Repository / Data Access Object layer.

Every function here talks directly to the database via SQLAlchemy async
sessions. Business rules (e.g. what counts as "on time", which categories
qualify for a semester ticket) are deliberately kept out of this file and
live in analytics.py instead -- this layer only knows how to query and
aggregate rows.
"""

from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import RouteDelayLog, StationMetric, TrainCategory

# A trip is considered "on time" if it departs within this many seconds
# of its scheduled departure. Deutsche Bahn itself typically uses a
# 5:59-minute (359s) grace window for long-distance punctuality reporting;
# we use a slightly stricter, rounder 300s (5 min) threshold here.
ON_TIME_THRESHOLD_SECONDS = 300

DAY_LABELS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _on_time_rate_expr():
    """SQL expression: percentage of rows within the on-time threshold."""
    return (
        func.sum(case((RouteDelayLog.delay_seconds <= ON_TIME_THRESHOLD_SECONDS, 1), else_=0))
        * 100.0
        / func.count(RouteDelayLog.id)
    )


# ==================== StationMetric ====================

async def create_station_metric(db: AsyncSession, data: dict) -> StationMetric:
    obj = StationMetric(**data)
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


async def get_station_metrics(
    db: AsyncSession,
    region: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> Sequence[StationMetric]:
    stmt = select(StationMetric)
    if region:
        stmt = stmt.where(StationMetric.region == region)
    stmt = stmt.order_by(StationMetric.reliability_score.desc()).limit(limit).offset(offset)
    result = await db.execute(stmt)
    return result.scalars().all()


async def upsert_station_metrics_from_logs(
    db: AsyncSession,
    region_map: Optional[dict[str, str]] = None,
) -> int:
    """Recompute every StationMetric row from the current RouteDelayLog data."""
    region_map = region_map or {}

    stmt = select(
        RouteDelayLog.origin_station.label("station_name"),
        func.count(RouteDelayLog.id).label("total_trips"),
        func.avg(RouteDelayLog.delay_seconds).label("avg_delay"),
        func.sum(
            case((RouteDelayLog.delay_seconds <= ON_TIME_THRESHOLD_SECONDS, 1), else_=0)
        ).label("on_time_count"),
    ).group_by(RouteDelayLog.origin_station)

    result = await db.execute(stmt)
    rows = result.all()

    updated = 0
    for row in rows:
        avg_delay_minutes = float(row.avg_delay or 0.0) / 60.0
        reliability = (row.on_time_count / row.total_trips * 100.0) if row.total_trips else 100.0

        existing = await db.execute(
            select(StationMetric).where(StationMetric.station_name == row.station_name)
        )
        station = existing.scalar_one_or_none()

        if station:
            station.total_tracked_trips = row.total_trips
            station.average_delay_minutes = round(avg_delay_minutes, 2)
            station.reliability_score = round(reliability, 2)
        else:
            db.add(
                StationMetric(
                    station_name=row.station_name,
                    region=region_map.get(row.station_name, "Unbekannt"),
                    total_tracked_trips=row.total_trips,
                    average_delay_minutes=round(avg_delay_minutes, 2),
                    reliability_score=round(reliability, 2),
                )
            )
        updated += 1

    await db.commit()
    return updated


# ==================== RouteDelayLog ====================

async def create_route_delay_log(db: AsyncSession, data: dict) -> RouteDelayLog:
    obj = RouteDelayLog(**data)
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


async def bulk_create_route_delay_logs(db: AsyncSession, rows: list[dict]) -> int:
    objs = [RouteDelayLog(**row) for row in rows]
    db.add_all(objs)
    await db.commit()
    return len(objs)


async def delete_all_route_delay_logs(db: AsyncSession) -> int:
    count_result = await db.execute(select(func.count(RouteDelayLog.id)))
    count = count_result.scalar_one()
    await db.execute(RouteDelayLog.__table__.delete())
    await db.commit()
    return count


async def get_route_delay_logs(
    db: AsyncSession,
    origin: Optional[str] = None,
    destination: Optional[str] = None,
    train_category: Optional[TrainCategory] = None,
    day_of_week: Optional[int] = None,
    limit: int = 100,
    offset: int = 0,
) -> Sequence[RouteDelayLog]:
    stmt = select(RouteDelayLog)
    conditions = []
    if origin:
        conditions.append(RouteDelayLog.origin_station == origin)
    if destination:
        conditions.append(RouteDelayLog.destination_station == destination)
    if train_category:
        conditions.append(RouteDelayLog.train_category == train_category)
    if day_of_week is not None:
        conditions.append(RouteDelayLog.day_of_week == day_of_week)
    if conditions:
        stmt = stmt.where(and_(*conditions))
    stmt = stmt.order_by(RouteDelayLog.scheduled_departure.desc()).limit(limit).offset(offset)
    result = await db.execute(stmt)
    return result.scalars().all()


async def count_route_delay_logs(db: AsyncSession) -> int:
    result = await db.execute(select(func.count(RouteDelayLog.id)))
    return result.scalar_one()


# ==================== Aggregations ====================

async def get_breakdown_by_hour(db: AsyncSession, region: Optional[str] = None) -> list[dict]:
    stmt = select(
        RouteDelayLog.hour_of_day,
        func.count(RouteDelayLog.id).label("total_trips"),
        func.avg(RouteDelayLog.delay_seconds).label("avg_delay"),
        _on_time_rate_expr().label("on_time_rate"),
    )
    if region:
        stmt = stmt.join(
            StationMetric, StationMetric.station_name == RouteDelayLog.origin_station
        ).where(StationMetric.region == region)
    stmt = stmt.group_by(RouteDelayLog.hour_of_day).order_by(RouteDelayLog.hour_of_day)

    result = await db.execute(stmt)
    return [
        {
            "hour_of_day": row.hour_of_day,
            "total_trips": row.total_trips,
            "average_delay_seconds": round(float(row.avg_delay or 0.0), 2),
            "on_time_rate_pct": round(float(row.on_time_rate or 0.0), 2),
        }
        for row in result.all()
    ]


async def get_breakdown_by_day_of_week(db: AsyncSession) -> list[dict]:
    stmt = (
        select(
            RouteDelayLog.day_of_week,
            func.count(RouteDelayLog.id).label("total_trips"),
            func.avg(RouteDelayLog.delay_seconds).label("avg_delay"),
            _on_time_rate_expr().label("on_time_rate"),
        )
        .group_by(RouteDelayLog.day_of_week)
        .order_by(RouteDelayLog.day_of_week)
    )
    result = await db.execute(stmt)
    return [
        {
            "day_of_week": row.day_of_week,
            "day_label": DAY_LABELS[row.day_of_week] if 0 <= row.day_of_week <= 6 else "Unknown",
            "total_trips": row.total_trips,
            "average_delay_seconds": round(float(row.avg_delay or 0.0), 2),
            "on_time_rate_pct": round(float(row.on_time_rate or 0.0), 2),
        }
        for row in result.all()
    ]


async def get_breakdown_by_route_block(
    db: AsyncSession, limit: int = 50, region: Optional[str] = None
) -> list[dict]:
    stmt = select(
        RouteDelayLog.origin_station,
        RouteDelayLog.destination_station,
        func.count(RouteDelayLog.id).label("total_trips"),
        func.avg(RouteDelayLog.delay_seconds).label("avg_delay"),
        _on_time_rate_expr().label("on_time_rate"),
    )
    if region:
        stmt = stmt.join(
            StationMetric, StationMetric.station_name == RouteDelayLog.origin_station
        ).where(StationMetric.region == region)
    stmt = (
        stmt.group_by(RouteDelayLog.origin_station, RouteDelayLog.destination_station)
        .order_by(func.count(RouteDelayLog.id).desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    return [
        {
            "origin_station": row.origin_station,
            "destination_station": row.destination_station,
            "total_trips": row.total_trips,
            "average_delay_seconds": round(float(row.avg_delay or 0.0), 2),
            "on_time_rate_pct": round(float(row.on_time_rate or 0.0), 2),
        }
        for row in result.all()
    ]


async def get_route_block_stats(
    db: AsyncSession,
    region: Optional[str] = None,
    since: Optional[datetime] = None,
    min_trips: int = 5,
) -> list[dict]:
    """
    Unlimited, unsorted route-block stats used to derive dashboard extremes
    (best/worst on-time route). `min_trips` filters out routes with too few
    samples to be a meaningful "most/worst reliable" claim.
    """
    stmt = select(
        RouteDelayLog.origin_station,
        RouteDelayLog.destination_station,
        func.count(RouteDelayLog.id).label("total_trips"),
        _on_time_rate_expr().label("on_time_rate"),
    )
    conditions = []
    if since is not None:
        conditions.append(RouteDelayLog.scheduled_departure >= since)
    if region:
        stmt = stmt.join(
            StationMetric, StationMetric.station_name == RouteDelayLog.origin_station
        )
        conditions.append(StationMetric.region == region)
    if conditions:
        stmt = stmt.where(and_(*conditions))
    stmt = stmt.group_by(RouteDelayLog.origin_station, RouteDelayLog.destination_station).having(
        func.count(RouteDelayLog.id) >= min_trips
    )
    result = await db.execute(stmt)
    return [
        {
            "origin_station": row.origin_station,
            "destination_station": row.destination_station,
            "total_trips": row.total_trips,
            "on_time_rate_pct": round(float(row.on_time_rate or 0.0), 2),
        }
        for row in result.all()
    ]


async def get_global_average_delay_minutes(
    db: AsyncSession, region: Optional[str] = None, since: Optional[datetime] = None
) -> float:
    stmt = select(func.avg(RouteDelayLog.delay_seconds))
    conditions = []
    if since is not None:
        conditions.append(RouteDelayLog.scheduled_departure >= since)
    if region:
        stmt = stmt.join(
            StationMetric, StationMetric.station_name == RouteDelayLog.origin_station
        )
        conditions.append(StationMetric.region == region)
    if conditions:
        stmt = stmt.where(and_(*conditions))
    result = await db.execute(stmt)
    avg_seconds = result.scalar_one_or_none() or 0.0
    return round(float(avg_seconds) / 60.0, 2)


async def get_distinct_regions(db: AsyncSession) -> list[str]:
    result = await db.execute(
        select(StationMetric.region).distinct().order_by(StationMetric.region)
    )
    return [row[0] for row in result.all()]


async def upsert_route_delay_logs_by_trip_id(db: AsyncSession, rows: list[dict]) -> tuple[int, int]:
    """
    Insert-or-update RouteDelayLog rows sourced from a live feed, matched by
    `source_trip_id`. Rows without a trip id are always inserted (there's
    nothing to de-duplicate against). Returns (created_count, updated_count).
    """
    created = 0
    updated = 0

    for row in rows:
        trip_id = row.get("source_trip_id")
        existing = None
        if trip_id:
            result = await db.execute(
                select(RouteDelayLog).where(RouteDelayLog.source_trip_id == trip_id)
            )
            existing = result.scalar_one_or_none()

        if existing is not None:
            existing.actual_departure = row["actual_departure"]
            existing.delay_seconds = row["delay_seconds"]
            existing.hour_of_day = row["hour_of_day"]
            existing.day_of_week = row["day_of_week"]
            updated += 1
        else:
            db.add(RouteDelayLog(**row))
            created += 1

    await db.commit()
    return created, updated


async def get_semester_ticket_routes(db: AsyncSession, limit: int = 50) -> list[dict]:
    """
    Regional-only route blocks, restricted at the query level to
    RE / RB / S_BAHN -- the categories covered by a German semester ticket.
    ICE and IC (long-distance) are excluded entirely.
    """
    eligible = [TrainCategory.RE, TrainCategory.RB, TrainCategory.S_BAHN]
    stmt = (
        select(
            RouteDelayLog.origin_station,
            RouteDelayLog.destination_station,
            RouteDelayLog.train_category,
            func.count(RouteDelayLog.id).label("total_trips"),
            func.avg(RouteDelayLog.delay_seconds).label("avg_delay"),
            _on_time_rate_expr().label("on_time_rate"),
        )
        .where(RouteDelayLog.train_category.in_(eligible))
        .group_by(
            RouteDelayLog.origin_station,
            RouteDelayLog.destination_station,
            RouteDelayLog.train_category,
        )
        .order_by(_on_time_rate_expr().desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    return [
        {
            "origin_station": row.origin_station,
            "destination_station": row.destination_station,
            "train_category": row.train_category,
            "total_trips": row.total_trips,
            "average_delay_seconds": round(float(row.avg_delay or 0.0), 2),
            "on_time_rate_pct": round(float(row.on_time_rate or 0.0), 2),
        }
        for row in result.all()
    ]
