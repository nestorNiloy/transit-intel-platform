"""
Smart Public Transport Intelligence Platform -- FastAPI entrypoint.

Wires together the router/controller layer, the background dummy-data
ingestion engine, and global 404/500 exception interceptors that return
structured JSON error envelopes with a server runtime timestamp.
"""

import os
import random
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from . import analytics, crud, services
from .database import AsyncSessionLocal, get_db, init_db
from .models import TrainCategory
from .schemas import (
    DashboardKPIs,
    DayOfWeekBucket,
    ErrorResponse,
    IngestBatchRequest,
    IngestBatchResponse,
    IngestLiveRequest,
    IngestLiveResponse,
    PeakHourBucket,
    RouteBlockBreakdown,
    RouteDelayLogRead,
    SemesterTicketRoute,
    StationMetricRead,
)

app = FastAPI(
    title="Smart Public Transport Intelligence Platform",
    description=(
        "Asynchronous analytics backend that converts GTFS / Deutsche Bahn "
        "timetable records into structured metrics for delay analysis, "
        "station reliability, and semester-ticket regional route optimization."
    ),
    version="1.0.0",
)

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(_APP_DIR, "templates"))
app.mount("/static", StaticFiles(directory=os.path.join(_APP_DIR, "static")), name="static")

# ---------------------------------------------------------------------------
# Sample regional network used by the dummy-data ingestion engine. Each route
# lists the train categories that plausibly run it, so long-distance corridors
# (e.g. Hamburg -> Berlin) skew toward ICE/IC while local corridors skew
# toward RE/RB/S_BAHN.
# ---------------------------------------------------------------------------
SAMPLE_ROUTES: list[tuple[str, str, list[TrainCategory]]] = [
    ("Hamburg Hbf", "Lübeck Hbf", [TrainCategory.RE, TrainCategory.RB]),
    ("Bremen Hbf", "Hannover Hbf", [TrainCategory.IC, TrainCategory.RE]),
    ("Berlin Hbf", "Potsdam Hbf", [TrainCategory.RE, TrainCategory.S_BAHN]),
    ("München Hbf", "Augsburg Hbf", [TrainCategory.RE, TrainCategory.ICE]),
    ("Köln Hbf", "Bonn Hbf", [TrainCategory.RE, TrainCategory.S_BAHN]),
    ("Frankfurt Hbf", "Mainz Hbf", [TrainCategory.RB, TrainCategory.S_BAHN]),
    ("Stuttgart Hbf", "Tübingen Hbf", [TrainCategory.RE, TrainCategory.RB]),
    ("Leipzig Hbf", "Halle Hbf", [TrainCategory.RE, TrainCategory.S_BAHN]),
    ("Dortmund Hbf", "Essen Hbf", [TrainCategory.S_BAHN, TrainCategory.RE]),
    ("Hamburg Hbf", "Berlin Hbf", [TrainCategory.ICE, TrainCategory.IC]),
]

REGION_MAP: dict[str, str] = {
    "Hamburg Hbf": "Norddeutschland",
    "Lübeck Hbf": "Norddeutschland",
    "Bremen Hbf": "Norddeutschland",
    "Hannover Hbf": "Niedersachsen",
    "Berlin Hbf": "Berlin-Brandenburg",
    "Potsdam Hbf": "Berlin-Brandenburg",
    "München Hbf": "Bayern",
    "Augsburg Hbf": "Bayern",
    "Köln Hbf": "Nordrhein-Westfalen",
    "Bonn Hbf": "Nordrhein-Westfalen",
    "Frankfurt Hbf": "Hessen",
    "Mainz Hbf": "Rheinland-Pfalz",
    "Stuttgart Hbf": "Baden-Württemberg",
    "Tübingen Hbf": "Baden-Württemberg",
    "Leipzig Hbf": "Sachsen",
    "Halle Hbf": "Sachsen-Anhalt",
    "Dortmund Hbf": "Nordrhein-Westfalen",
    "Essen Hbf": "Nordrhein-Westfalen",
}


def _generate_dummy_rows(num_records: int) -> list[dict]:
    """Produce realistic-looking RouteDelayLog rows for immediate analytics use."""
    rows = []
    now = datetime.now(timezone.utc)

    for _ in range(num_records):
        origin, destination, categories = random.choice(SAMPLE_ROUTES)
        category = random.choice(categories)

        day_of_week = random.randint(0, 6)
        # Bias sampling toward peak hours so congestion-slot analytics has
        # meaningful signal, while still covering the full 24h range.
        hour_of_day = random.choice(
            list(range(6, 9)) + list(range(17, 20)) + list(range(24))
        )
        minute = random.randint(0, 59)

        days_back = random.randint(0, 90)
        scheduled = (now - timedelta(days=days_back)).replace(
            hour=hour_of_day, minute=minute, second=0, microsecond=0
        )

        # Long-distance (ICE/IC) trains skew toward larger delays than regional trains.
        if category in (TrainCategory.ICE, TrainCategory.IC):
            delay_seconds = max(0, int(random.gauss(420, 380)))
        else:
            delay_seconds = max(0, int(random.gauss(120, 180)))

        # Rush-hour slots see moderately worse delays on average.
        if hour_of_day in range(6, 9) or hour_of_day in range(17, 20):
            delay_seconds = int(delay_seconds * random.uniform(1.1, 1.4))

        actual = scheduled + timedelta(seconds=delay_seconds)

        rows.append(
            {
                "origin_station": origin,
                "destination_station": destination,
                "train_category": category,
                "scheduled_departure": scheduled,
                "actual_departure": actual,
                "delay_seconds": delay_seconds,
                "day_of_week": day_of_week,
                "hour_of_day": hour_of_day,
            }
        )
    return rows


@app.on_event("startup")
async def on_startup() -> None:
    await init_db()

    # On a fresh deploy (empty database), auto-seed sample data so a visitor
    # hitting the public dashboard cold sees a populated demo immediately,
    # instead of an empty page they'd need to know to POST /api/ingest/batch
    # themselves. Never overwrites existing data -- only fires when empty.
    async with AsyncSessionLocal() as db:
        existing = await crud.count_route_delay_logs(db)
        if existing == 0:
            rows = _generate_dummy_rows(1200)
            await crud.bulk_create_route_delay_logs(db, rows)
            await analytics.refresh_station_aggregates(db, region_map=REGION_MAP)
            print("[startup] Database was empty -- auto-seeded 1200 sample records.")


# =============================================================================
# Dashboard (HTML)
# =============================================================================

@app.get("/", include_in_schema=False)
async def dashboard(request: Request, region: str | None = Query(default=None), db: AsyncSession = Depends(get_db)):
    kpis = await analytics.compute_dashboard_kpis(db, region=region)
    hourly = await analytics.compute_hourly_congestion_report(db, region=region)
    stations = await crud.get_station_metrics(db, region=region, limit=200)
    regions = await crud.get_distinct_regions(db)

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "kpis": kpis,
            "hourly": hourly,
            "stations": stations,
            "regions": regions,
            "selected_region": region or "",
        },
    )


# =============================================================================
# Ingestion
# =============================================================================

@app.post("/api/ingest/batch", response_model=IngestBatchResponse, status_code=202)
async def ingest_batch(payload: IngestBatchRequest, background_tasks: BackgroundTasks):
    """
    Queues a background worker that populates route_delay_logs with realistic
    sample data (e.g. "Hamburg -> Lübeck", "Bremen -> Hannover") and then
    refreshes station-level aggregates, so analytics endpoints have
    descriptive data immediately after this call returns.
    """
    job_id = str(uuid.uuid4())
    num_records = payload.num_records
    reset = payload.reset

    async def job() -> None:
        async with AsyncSessionLocal() as db:
            if reset:
                await crud.delete_all_route_delay_logs(db)
            rows = _generate_dummy_rows(num_records)
            await crud.bulk_create_route_delay_logs(db, rows)
            await analytics.refresh_station_aggregates(db, region_map=REGION_MAP)

    background_tasks.add_task(job)

    return IngestBatchResponse(status="queued", records_queued=num_records, job_id=job_id)


@app.post("/api/ingest/live", response_model=IngestLiveResponse, status_code=202)
async def ingest_live(payload: IngestLiveRequest, background_tasks: BackgroundTasks):
    """
    Queues a background worker that fetches *real* upcoming RE/RB departures
    from the public db.transport.rest wrapper around Deutsche Bahn's HAFAS
    API for the target Hamburg/Schleswig-Holstein-area stations, normalizes
    them, and upserts them into route_delay_logs (matched by trip id so
    re-polling the same trip updates its delay instead of duplicating it).

    This is a best-effort call to a free, unauthenticated, community-run
    third-party service -- not an official/SLA-backed Deutsche Bahn API.
    Network errors are logged and swallowed rather than failing the request,
    since it runs in the background after the 202 has already been returned.
    """
    job_id = str(uuid.uuid4())
    stations = payload.stations or services.TARGET_STATIONS
    duration_minutes = payload.duration_minutes

    async def job() -> None:
        try:
            rows = await services.collect_live_regional_delays(
                station_names=stations, duration_minutes=duration_minutes
            )
        except Exception as exc:  # belt-and-suspenders: never let a background task die silently
            print(f"[ingest_live] live fetch failed entirely: {exc}")
            return

        if not rows:
            print("[ingest_live] live fetch returned no usable rows")
            return

        async with AsyncSessionLocal() as db:
            created, updated = await crud.upsert_route_delay_logs_by_trip_id(db, rows)
            await analytics.refresh_station_aggregates(db)
            print(f"[ingest_live] created={created} updated={updated}")

    background_tasks.add_task(job)

    return IngestLiveResponse(status="queued", job_id=job_id, stations_targeted=stations)


# =============================================================================
# Stations
# =============================================================================

@app.get("/api/stations", response_model=list[StationMetricRead])
async def list_stations(
    region: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    return await crud.get_station_metrics(db, region=region, limit=limit, offset=offset)


# =============================================================================
# Route Delay Logs
# =============================================================================

@app.get("/api/routes/delays", response_model=list[RouteDelayLogRead])
async def list_route_delays(
    origin: str | None = Query(default=None),
    destination: str | None = Query(default=None),
    train_category: TrainCategory | None = Query(default=None),
    day_of_week: int | None = Query(default=None, ge=0, le=6),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    return await crud.get_route_delay_logs(
        db,
        origin=origin,
        destination=destination,
        train_category=train_category,
        day_of_week=day_of_week,
        limit=limit,
        offset=offset,
    )


# =============================================================================
# Analytics
# =============================================================================

@app.get("/api/analytics/hourly-congestion", response_model=list[PeakHourBucket])
async def hourly_congestion(
    region: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    return await analytics.compute_hourly_congestion_report(db, region=region)


@app.get("/api/analytics/weekly-reliability", response_model=list[DayOfWeekBucket])
async def weekly_reliability(db: AsyncSession = Depends(get_db)):
    return await analytics.compute_weekly_reliability_report(db)


@app.get("/api/analytics/route-blocks", response_model=list[RouteBlockBreakdown])
async def route_blocks(
    limit: int = Query(default=50, ge=1, le=200),
    region: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    return await analytics.compute_route_block_report(db, limit=limit, region=region)


@app.get("/api/analytics/semester-ticket", response_model=list[SemesterTicketRoute])
async def semester_ticket_routes(
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    return await analytics.compute_semester_ticket_optimizer(db, limit=limit)


@app.get("/api/regions", response_model=list[str])
async def list_regions(db: AsyncSession = Depends(get_db)):
    return await crud.get_distinct_regions(db)


@app.get("/api/dashboard/kpis", response_model=DashboardKPIs)
async def dashboard_kpis(
    region: str | None = Query(default=None),
    window_days: int = Query(default=30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    return await analytics.compute_dashboard_kpis(db, region=region, window_days=window_days)


# =============================================================================
# Health
# =============================================================================

@app.get("/api/health")
async def health_check(db: AsyncSession = Depends(get_db)):
    total_logs = await crud.count_route_delay_logs(db)
    return {
        "status": "ok",
        "total_route_delay_logs": total_logs,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# =============================================================================
# Global Exception Interceptors
# =============================================================================

def _error_payload(error: str, detail: str, path: str) -> dict:
    return ErrorResponse(
        error=error,
        detail=detail,
        timestamp=datetime.now(timezone.utc),
        path=path,
    ).model_dump(mode="json")


# Starlette's router returns a plain-text 404 for genuinely unmatched paths
# *without* raising HTTPException, so it would otherwise bypass our JSON
# error envelope. This catch-all re-routes any unmatched path/method into an
# explicit HTTPException(404), which the handler below then formats.
@app.api_route(
    "/{full_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    include_in_schema=False,
)
async def catch_all(full_path: str):
    raise HTTPException(status_code=404, detail=f"The requested resource '/{full_path}' was not found.")


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_payload(
            error=exc.__class__.__name__ if exc.status_code != 404 else "Not Found",
            detail=str(exc.detail),
            path=str(request.url.path),
        ),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content=_error_payload(
            error="Validation Error",
            detail=str(exc.errors()),
            path=str(request.url.path),
        ),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content=_error_payload(
            error="Internal Server Error",
            detail=str(exc) or exc.__class__.__name__,
            path=str(request.url.path),
        ),
    )
