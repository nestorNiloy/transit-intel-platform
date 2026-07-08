"""
Core calculation module. This layer owns business rules that sit above raw
database aggregation: congestion-slot classification, semester-ticket
eligibility filtering, dashboard KPI composition, and station-aggregate
refresh orchestration.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from . import crud
from .models import TrainCategory

MORNING_PEAK_HOURS = set(range(6, 9))     # 06:00 - 08:59
EVENING_PEAK_HOURS = set(range(17, 20))   # 17:00 - 19:59
MIDDAY_HOURS = set(range(9, 17))          # 09:00 - 16:59

# Long-distance categories are never eligible for semester-ticket routing,
# regardless of what the raw query already filtered for -- kept here as an
# explicit, easily-audited business rule rather than only a query predicate.
SEMESTER_TICKET_EXCLUDED_CATEGORIES = {TrainCategory.ICE, TrainCategory.IC}

# Rolling window used for "this month"-style dashboard KPIs. A rolling
# 30-day window is used instead of a strict calendar-month boundary so the
# dashboard stays meaningful right after the 1st of a month too.
DASHBOARD_WINDOW_DAYS = 30


def classify_congestion_slot(hour_of_day: int) -> str:
    """Bucket an hour-of-day into a human-readable congestion slot."""
    if hour_of_day in MORNING_PEAK_HOURS:
        return "morning_peak"
    if hour_of_day in EVENING_PEAK_HOURS:
        return "evening_peak"
    if hour_of_day in MIDDAY_HOURS:
        return "midday_offpeak"
    return "night_offpeak"


async def compute_hourly_congestion_report(db: AsyncSession, region: Optional[str] = None) -> list[dict]:
    """Delay/on-time breakdown per hour of day, annotated with congestion slot."""
    buckets = await crud.get_breakdown_by_hour(db, region=region)
    for bucket in buckets:
        bucket["congestion_slot"] = classify_congestion_slot(bucket["hour_of_day"])
    return buckets


async def compute_weekly_reliability_report(db: AsyncSession) -> list[dict]:
    """Delay/on-time breakdown per day of week (Mon=0 .. Sun=6)."""
    return await crud.get_breakdown_by_day_of_week(db)


async def compute_route_block_report(
    db: AsyncSession, limit: int = 50, region: Optional[str] = None
) -> list[dict]:
    """Delay/on-time breakdown per origin -> destination route block."""
    return await crud.get_breakdown_by_route_block(db, limit=limit, region=region)


async def compute_semester_ticket_optimizer(db: AsyncSession, limit: int = 50) -> list[dict]:
    """
    Regional-only commute optimizer: surfaces the most reliable RE/RB/S_BAHN
    route blocks, explicitly re-excluding any long-distance category as a
    defense-in-depth check on top of the database-level filter.
    """
    routes = await crud.get_semester_ticket_routes(db, limit=limit)
    return [r for r in routes if r["train_category"] not in SEMESTER_TICKET_EXCLUDED_CATEGORIES]


async def refresh_station_aggregates(
    db: AsyncSession,
    region_map: Optional[dict[str, str]] = None,
) -> int:
    """Recompute all StationMetric rows from the current RouteDelayLog table."""
    return await crud.upsert_station_metrics_from_logs(db, region_map=region_map)


async def compute_dashboard_kpis(
    db: AsyncSession,
    region: Optional[str] = None,
    window_days: int = DASHBOARD_WINDOW_DAYS,
) -> dict:
    """
    Composes the four dashboard KPI cards: global average delay, this
    window's best/worst-performing route, and the busiest congestion window.
    """
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=window_days)

    avg_delay_minutes = await crud.get_global_average_delay_minutes(db, region=region, since=since)

    route_stats = await crud.get_route_block_stats(db, region=region, since=since)
    best_route = max(route_stats, key=lambda r: r["on_time_rate_pct"], default=None)
    worst_route = min(route_stats, key=lambda r: r["on_time_rate_pct"], default=None)

    # When only one route in the window/region has enough samples, best and
    # worst are trivially the same route -- flag that so the UI can say so
    # instead of silently showing two identical cards.
    same_route = (
        best_route is not None
        and worst_route is not None
        and best_route["origin_station"] == worst_route["origin_station"]
        and best_route["destination_station"] == worst_route["destination_station"]
        and len(route_stats) < 2
    )

    hourly = await crud.get_breakdown_by_hour(db, region=region)
    busiest_hour = max(
        (h for h in hourly if h["total_trips"] > 0),
        key=lambda h: h["average_delay_seconds"],
        default=None,
    )

    return {
        "average_delay_minutes": avg_delay_minutes,
        "best_route": best_route,
        "worst_route": worst_route,
        "same_route_only": same_route,
        "peak_congestion_hour": busiest_hour["hour_of_day"] if busiest_hour else None,
        "peak_congestion_slot": classify_congestion_slot(busiest_hour["hour_of_day"]) if busiest_hour else None,
        "peak_congestion_average_delay_seconds": busiest_hour["average_delay_seconds"] if busiest_hour else None,
        "window_days": window_days,
        "region": region,
    }
