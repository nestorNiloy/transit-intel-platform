# Transit Intelligence Platform

An analytics platform for exploring regional rail reliability and delay patterns in Germany. The application ingests timetable/departure records, stores normalized trip observations, calculates station and route metrics, and presents the results in a web dashboard backed by a REST API.

> **Project status:** Working portfolio project. The current dashboard starts with generated sample data. Real RE/RB departure observations can be fetched manually through the live-ingestion endpoint. Live ingestion is **not** scheduled automatically, so the dashboard should not be described as a continuously refreshed live Deutsche Bahn monitor.

## Highlights

- Asynchronous REST API built with FastAPI and SQLAlchemy 2.0
- Relational data model for individual route-delay observations and station-level aggregates
- Analytics for average delay, on-time rate, station reliability, hourly congestion, weekly patterns, and route comparisons
- Semester-ticket route view restricted to regional services (RE, RB, S-Bahn)
- Server-rendered dashboard with Jinja2, Tailwind CSS, and Chart.js
- External departure-data adapter for the community db.transport.rest HAFAS API
- Input validation, bounded HTTP retries, structured API error responses, and smoke/unit test scripts

## Architecture

The application is a modular monolith: one FastAPI process coordinates the HTTP layer, ingestion adapter, analytics, data-access functions, and SQLAlchemy models.

~~~mermaid
flowchart TD
    Browser["Browser"] -->|HTML page / JSON requests| API["FastAPI application"]
    API --> Routes["HTTP routes and validation"]
    Routes --> Analytics["Analytics and business rules"]
    Routes --> CRUD["CRUD / data-access layer"]
    Routes --> Ingest["HAFAS ingestion service"]
    Ingest --> External["db.transport.rest community API"]
    External --> Ingest
    Analytics --> CRUD
    CRUD --> ORM["SQLAlchemy 2.0 async"]
    ORM --> DB[("SQLite database")]
    DB --> ORM
    API --> Templates["Jinja2 dashboard template"]
    Templates --> Browser
~~~

### Data flow

~~~mermaid
flowchart LR
    A["Sample generator or HAFAS departures"] --> B["Normalize and validate records"]
    B --> C["RouteDelayLog"]
    C --> D["SQL aggregations"]
    D --> E["StationMetric aggregates"]
    C --> F["Analytics endpoints"]
    E --> F
    F --> G["Dashboard KPIs, table and charts"]
~~~

1. **Acquire:** Generate demonstration observations or request upcoming regional departures from the external HAFAS wrapper.
2. **Normalize:** Map supported train products to the platform's train categories and convert timestamps/delay values into the internal record format.
3. **Persist:** Store trip observations in the route_delay_logs table; live observations are upserted by source trip ID when available.
4. **Aggregate:** Recalculate station-level trip counts, average delays, and reliability scores.
5. **Analyze and present:** Query the stored records through analytics endpoints and render the dashboard.

## Tech stack

| Area | Technology | Responsibility |
|---|---|---|
| API | Python 3.11, FastAPI, Uvicorn | HTTP routes, validation, API documentation |
| Data access | SQLAlchemy 2.0 async, aiosqlite | Async ORM and database sessions |
| Database | SQLite | Local relational persistence |
| Schemas | Pydantic v2 | Request and response validation |
| External data | httpx, db.transport.rest | Async HTTP client and HAFAS departure retrieval |
| Dashboard | Jinja2, Tailwind CSS CDN, Chart.js | Server-rendered dashboard and charts |
| Tests | FastAPI TestClient, mocked HAFAS payloads | Endpoint smoke checks and normalization tests |

## Dashboard

The dashboard at / currently includes:

- **Average delay** across the selected dataset and rolling KPI window
- **Most reliable route** and **worst-performing route**, based on routes with at least five observations in the rolling 30-day window
- **Peak congestion hour**, based on the hour with the highest average recorded delay
- **Station reliability table**, with trip count, average delay, and on-time percentage
- **Hourly delay chart**, with morning and evening peak periods highlighted
- **Region filter**, which refreshes the KPIs, station table, and hourly chart without a full page reload

The sample dataset is synthetic and is generated when the database contains no route-delay records. Synthetic values are useful for demonstrating the analytics and UI; they are not evidence of actual Deutsche Bahn performance.

## Data ingestion

### Sample data

On application startup, if the route-delay table is empty, the app creates approximately 1,200 synthetic observations across a small set of German routes. This makes a fresh local or demo deployment immediately explorable.

The batch endpoint can also create more sample records:

~~~bash
curl -X POST http://localhost:8000/api/ingest/batch \
  -H "Content-Type: application/json" \
  -d '{"num_records": 500, "reset": false}'
~~~

**Caution:** reset=true deletes the existing route-delay observations before generating replacement sample data. Do not call it against a dataset you want to preserve.

### Optional HAFAS departure ingestion

The service integrates with [db.transport.rest](https://v6.db.transport.rest), a community-run wrapper around Deutsche Bahn's HAFAS interface. It does not require an API key in the current implementation and is not an official DB API with an uptime guarantee.

Trigger an ingestion run while the API is running:

~~~bash
curl -X POST http://localhost:8000/api/ingest/live \
  -H "Content-Type: application/json" \
  -d '{"duration_minutes": 180}'
~~~

Or use the standalone runner:

~~~bash
python scripts/ingest_live.py
python scripts/ingest_live.py --duration 360
python scripts/ingest_live.py --stations "Hamburg Hbf" "Kiel Hbf"
~~~

The default station set covers Northern Germany. The ingestion service resolves station names, fetches upcoming departures, keeps supported RE/RB products, normalizes usable records, and upserts observations by HAFAS trip ID.

**Important data-freshness distinction:** live ingestion is initiated manually (API request or script). There is currently no scheduler or always-running polling worker. The dashboard displays whatever records are already stored; it does not fetch live departure data every time a visitor opens the page.

## Data model

~~~mermaid
erDiagram
    ROUTE_DELAY_LOG {
        int id PK
        string source_trip_id
        string origin_station
        string destination_station
        string train_category
        datetime scheduled_departure
        datetime actual_departure
        int delay_seconds
        int day_of_week
        int hour_of_day
    }
    STATION_METRIC {
        int id PK
        string station_name UK
        string region
        int total_tracked_trips
        float average_delay_minutes
        float reliability_score
    }
    STATION_METRIC ||..o{ ROUTE_DELAY_LOG : "aggregates by origin_station (logical only)"
~~~

- RouteDelayLog stores the granular scheduled-versus-actual departure observations used by analytics.
- StationMetric stores a derived aggregate for each origin station.
- The relationship between these tables is currently logical (station name), rather than a declared foreign-key relationship.

## API reference

Interactive OpenAPI documentation is available at /docs when the server is running.

| Method | Endpoint | Description |
|---|---|---|
| GET | / | Dashboard HTML; optional region filter |
| GET | /api/health | Liveness check and route-delay row count |
| GET | /api/regions | Distinct station regions |
| GET | /api/stations | Station metrics; optional region, limit, offset |
| GET | /api/routes/delays | Delay observations; optional origin, destination, category, weekday, pagination |
| GET | /api/analytics/hourly-congestion | Delay and on-time breakdown by hour; optional region |
| GET | /api/analytics/weekly-reliability | Delay and on-time breakdown by weekday |
| GET | /api/analytics/route-blocks | Aggregated origin-destination route performance |
| GET | /api/analytics/semester-ticket | Regional route results for RE, RB, and S-Bahn |
| GET | /api/dashboard/kpis | Dashboard KPI summary; optional region and rolling window |
| POST | /api/ingest/batch | Queue synthetic sample-data ingestion |
| POST | /api/ingest/live | Queue external HAFAS departure ingestion |

Both ingestion endpoints return HTTP 202 with a job ID. Work is currently handled through FastAPI in-process background tasks; there is no durable job queue or job-status endpoint.

## Local development

### Requirements

- Python 3.11 or newer
- pip

### Setup

~~~bash
git clone https://github.com/nestorNiloy/transit-intel-platform.git
cd transit-intel-platform

# Windows PowerShell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1

# macOS / Linux: use this instead of the two Windows commands above
# python3.11 -m venv .venv
# source .venv/bin/activate

python -m pip install --upgrade pip
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
~~~

Open:

- Dashboard: http://127.0.0.1:8000/
- API documentation: http://127.0.0.1:8000/docs
- Health check: http://127.0.0.1:8000/api/health

The SQLite database file is created in the process working directory as transit_intel.db. Run Uvicorn from the repository root.

### Tests

~~~bash
python scripts/test_services.py
python scripts/smoke_test.py
~~~

The service tests use mocked HAFAS-shaped payloads and do not need a network connection. The smoke test exercises the API and dashboard through FastAPI's test client. The current scripts are manual checks; a CI workflow is a recommended next engineering improvement.

## Deployment

### Recommended simple deployment: Render Web Service

This is a conventional FastAPI server, so deploy it as a **Render Web Service**, not as a static site. Suggested settings:

| Setting | Value |
|---|---|
| Runtime | Python |
| Branch | main |
| Root directory | Leave blank |
| Build command | pip install -r requirements.txt |
| Start command | uvicorn app.main:app --host 0.0.0.0 --port $PORT |
| Health check path | /api/health |

For an always-on public demo, choose a paid Render web-service instance: Render's free web services spin down after 15 minutes without traffic and can take about a minute to wake. Paid instances do not spin down. See [Render's free-instance limitations](https://render.com/docs/free) and [current compute pricing](https://render.com/pricing).

**SQLite persistence choice:**

- For a disposable portfolio demo, the app can recreate its synthetic dataset when the service starts. The dashboard will work, but manually ingested records may be lost when the host replaces its filesystem.
- To preserve the SQLite file on Render, use a paid service with a persistent disk and configure the database file path to live under the disk's mount directory. The current code uses a relative SQLite URL, so this requires a small configuration change before attaching a disk.
- If the project later needs durable, portable storage, migrate the database to managed PostgreSQL (for example, Neon) and configure the connection URL. Neon is a database provider, not an application host.

A paid web service reduces cold starts; it is not a guarantee of zero downtime. The external HAFAS community API can also be unavailable independently of this application.

### Why not Vercel for this deployment?

The current application is a conventional long-running FastAPI web service with SQLite file storage and in-process background tasks. Vercel's serverless function model would require adapting the deployment and being careful about ephemeral local storage and background execution. Render is the more straightforward fit for the current codebase.

## Current limitations and next improvements

- SQLite currently uses a local relative file path. On hosts with ephemeral filesystems, data does not survive service replacement/redeploy.
- Live HAFAS ingestion is manual; there is no scheduled collection or freshness indicator.
- Ingestion work uses in-process background tasks. A process restart can interrupt a job, and the returned job ID cannot currently be queried for progress or outcome.
- External HAFAS data is best-effort and community-provided; missing or stale upstream data can affect results.
- Station aggregate rows are derived from route logs. Aggregate refresh currently updates or inserts rows but does not remove station rows that are no longer represented in the log table.
- Automated CI, broader database integration tests, structured logging, and ingestion observability would strengthen the project.

Potential next development phases:

1. Make database configuration environment-driven and add a safe persistent deployment configuration.
2. Add GitHub Actions for repeatable tests and deployment checks.
3. Add scheduled ingestion with explicit data-freshness and failure reporting.
4. Add GTFS-Realtime ingestion where a suitable feed is available.
5. Explore delay prediction only after collecting a sufficiently large, representative historical dataset.

## Project structure

~~~text
transit-intel-platform/
├── app/
│   ├── main.py              # FastAPI app, routes, startup seeding, background tasks
│   ├── models.py            # SQLAlchemy ORM models and train categories
│   ├── schemas.py           # Pydantic request/response schemas
│   ├── crud.py              # Async database access and SQL aggregations
│   ├── analytics.py         # Reliability, congestion and KPI calculations
│   ├── services.py          # External HAFAS client and departure normalization
│   ├── database.py          # Async engine, session factory and schema creation
│   ├── templates/
│   │   └── index.html       # Jinja2 dashboard
│   └── static/
├── scripts/
│   ├── ingest_live.py       # Manual/cron-friendly live ingestion runner
│   ├── smoke_test.py        # API and dashboard smoke test
│   └── test_services.py     # HAFAS normalization unit tests
├── Procfile
├── requirements.txt
└── .gitignore
~~~

## License

No license file is currently included. Contact the repository owner before reusing the project beyond the permissions granted by GitHub's default repository terms.
