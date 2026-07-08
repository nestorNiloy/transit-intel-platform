# Smart Public Transport Intelligence Platform

> **Live demo:** https://transit-intel-platform-production.up.railway.app  
> **API docs:** https://transit-intel-platform-production.up.railway.app/docs

An asynchronous FastAPI + SQLAlchemy analytics backend that turns raw
Deutsche Bahn-style timetable records into structured metrics: delay
analysis, station reliability scores, hourly congestion slots, and a
semester-ticket (regional-only) route optimizer — with a live Jinja2
dashboard and a real DB HAFAS data feed.

## Stack

- **Backend:** Python 3.11, FastAPI, SQLAlchemy 2.0 async, aiosqlite, Pydantic v2
- **Frontend:** Jinja2 templates, Tailwind CSS (CDN), Chart.js (CDN)
- **Live data:** `v6.db.transport.rest` — unauthenticated community HAFAS wrapper for Deutsche Bahn
- **Deploy:** Railway (auto-seeds on cold start, never shows a blank dashboard)

## Project layout

```
transit-intel-platform/
├── app/                     # Python package — all modules use relative imports
│   ├── __init__.py
│   ├── main.py              # FastAPI app, routes, dashboard page, background jobs, error handlers
│   ├── models.py            # SQLAlchemy ORM: StationMetric, RouteDelayLog
│   ├── schemas.py           # Pydantic request/response schemas
│   ├── crud.py              # Async queries and aggregations
│   ├── analytics.py         # Business logic: congestion slots, semester-ticket rules, KPIs
│   ├── services.py          # Live ingestion client for db.transport.rest (HAFAS)
│   ├── database.py          # Async engine and session setup
│   ├── templates/
│   │   └── index.html       # Dashboard UI
│   └── static/
├── scripts/
│   ├── smoke_test.py        # End-to-end test of every endpoint
│   ├── test_services.py     # Unit tests for the live-data normalizer (mocked HAFAS payloads)
│   └── ingest_live.py       # Standalone live-ingestion runner (cron-friendly)
├── Procfile
├── requirements.txt
├── .python-version
└── .gitignore
```

## Run locally

```bash
# 1 — clone and enter the project root
git clone https://github.com/nestorNiloy/transit-intel-platform.git
cd transit-intel-platform

# 2 — create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3 — install dependencies
pip install -r requirements.txt

# 4 — start the server (always from the project root, not from inside app/)
uvicorn app.main:app --reload --port 8000
```

Open `http://localhost:8000`. The database is created automatically on first
run and auto-seeded with ~1,200 sample records, so the dashboard is never
blank on a fresh start. Interactive API docs at `http://localhost:8000/docs`.

## Dashboard

The live dashboard at `/` provides:

- **4 KPI cards** — global average delay, most/worst reliable route (rolling 30-day window), peak congestion hour
- **Station Reliability Standings** — ranked table with green / yellow / red reliability badges (>90% / 75–90% / <75%)
- **Temporal Delays chart** — average delay per hour (0–23), peak windows (06–09, 17–20) highlighted in red
- **Region filter** — dropdown updates all three components via AJAX without a page reload

## Live data: real Deutsche Bahn regional delays

By default the app runs on synthetic sample data. To pull real upcoming
RE/RB departures for Hamburg, Hamburg-Altona, Lübeck, Bremen, and Hannover:

```bash
# via the API (server must be running)
curl -X POST http://localhost:8000/api/ingest/live \
  -H "Content-Type: application/json" \
  -d '{"duration_minutes": 180}'

# or standalone, e.g. from a cron job
python scripts/ingest_live.py
python scripts/ingest_live.py --duration 360
python scripts/ingest_live.py --stations "Hamburg Hbf" "Kiel Hbf"
```

**Data source:** [`v6.db.transport.rest`](https://v6.db.transport.rest) — a
free, unauthenticated community wrapper around DB's HAFAS backend. No API
key required. RE/RB departures are filtered server-side and re-checked
client-side; ICE/IC/S-Bahn/bus are excluded. Re-polling the same trip
updates its delay record instead of creating a duplicate, matched by HAFAS
`tripId` via `crud.upsert_route_delay_logs_by_trip_id`.

## API reference

| Method | Path | Params | Status | Description |
|--------|------|--------|--------|-------------|
| GET | `/` | `region?` | 200 | HTML dashboard |
| POST | `/api/ingest/batch` | `{num_records, reset}` | 202 | Background dummy-data ingestion |
| POST | `/api/ingest/live` | `{stations?, duration_minutes}` | 202 | Real fetch from db.transport.rest |
| GET | `/api/stations` | `region?, limit?, offset?` | 200 | Per-station reliability metrics |
| GET | `/api/routes/delays` | `origin?, destination?, train_category?, day_of_week?, limit?` | 200 | Filtered delay log rows |
| GET | `/api/analytics/hourly-congestion` | `region?` | 200 | Delay % per hour with congestion slot |
| GET | `/api/analytics/weekly-reliability` | — | 200 | Delay % per day of week |
| GET | `/api/analytics/route-blocks` | `limit?, region?` | 200 | Delay % per origin→destination |
| GET | `/api/analytics/semester-ticket` | `limit?` | 200 | Best RE/RB/S_BAHN routes; ICE/IC excluded |
| GET | `/api/dashboard/kpis` | `region?, window_days?` | 200 | KPI summary for the dashboard cards |
| GET | `/api/regions` | — | 200 | Distinct regions for the filter dropdown |
| GET | `/api/health` | — | 200 | Liveness check + row count |

All unmatched routes and unhandled errors return a structured JSON envelope:
`{ error, detail, timestamp, path }`.

## Business rules

| Rule | Value |
|------|-------|
| On-time threshold | `delay_seconds ≤ 300` (5 min) |
| Morning peak | 06:00 – 08:59 |
| Evening peak | 17:00 – 19:59 |
| Semester-ticket eligible | `RE`, `RB`, `S_BAHN` only — `ICE`/`IC` excluded at query *and* service layer |
| Dashboard rolling window | 30 days |

## Testing

```bash
pip install httpx
python scripts/test_services.py   # unit tests — no network required
python scripts/smoke_test.py      # end-to-end against a live in-process server
```
