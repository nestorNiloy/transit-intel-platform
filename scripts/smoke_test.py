"""
Standalone endpoint smoke test using an isolated temporary SQLite database.

Run from the project root:
    python scripts/smoke_test.py

The test sets DATABASE_URL before importing the application, so reset=True
can never touch the developer's normal transit_intel.db file.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    admin_key = "local-smoke-test-key"

    with tempfile.TemporaryDirectory(prefix="transit-intel-smoke-") as temp_dir:
        database_path = Path(temp_dir) / "smoke_test.db"
        # Configure the database before app.main imports app.database.
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{database_path.as_posix()}"
        os.environ["ADMIN_API_KEY"] = admin_key
        os.environ["ENVIRONMENT"] = "test"

        from fastapi.testclient import TestClient
        from app.main import app

        with TestClient(app) as client:
            # 1. Public health endpoint.
            response = client.get("/api/health")
            assert response.status_code == 200, response.text
            print("health (on startup):", response.json())

            # 2. Ingestion must reject missing credentials.
            response = client.post(
                "/api/ingest/batch",
                json={"num_records": 500, "reset": True},
            )
            assert response.status_code == 401, response.text
            print("ingestion authentication: missing key rejected")

            # 3. Authorized reset + ingestion. Starlette's TestClient waits
            # for the response background task before returning.
            response = client.post(
                "/api/ingest/batch",
                json={"num_records": 500, "reset": True},
                headers={"X-Admin-API-Key": admin_key},
            )
            assert response.status_code == 202, response.text
            print("ingest:", response.json())

            response = client.get("/api/health")
            assert response.status_code == 200, response.text
            assert response.json()["total_route_delay_logs"] == 500, response.json()
            print("health (post-ingest):", response.json())

            # 4. Stations and route data.
            response = client.get("/api/stations")
            assert response.status_code == 200, response.text
            print(f"stations: {len(response.json())} rows")

            response = client.get(
                "/api/routes/delays", params={"train_category": "RE", "limit": 5}
            )
            assert response.status_code == 200, response.text
            print(f"routes/delays (RE): {len(response.json())} rows")

            # 5. Analytics and region filtering.
            for endpoint in (
                "/api/analytics/hourly-congestion",
                "/api/analytics/weekly-reliability",
                "/api/analytics/route-blocks",
                "/api/analytics/semester-ticket",
                "/api/dashboard/kpis",
                "/api/regions",
            ):
                response = client.get(endpoint)
                assert response.status_code == 200, f"{endpoint}: {response.text}"

            categories = {
                row["train_category"]
                for row in client.get("/api/analytics/semester-ticket").json()
            }
            assert categories.issubset({"RE", "RB", "S_BAHN"}), categories

            regions = client.get("/api/regions").json()
            if regions:
                response = client.get(
                    "/api/analytics/hourly-congestion", params={"region": regions[0]}
                )
                assert response.status_code == 200, response.text
                response = client.get("/", params={"region": regions[0]})
                assert response.status_code == 200, response.text

            # 6. Dashboard HTML and JSON 404 envelope.
            response = client.get("/")
            assert response.status_code == 200, response.text
            assert "Transit Intelligence" in response.text
            assert "hourly-chart" in response.text

            response = client.get("/api/does-not-exist")
            assert response.status_code == 404, response.text
            assert "timestamp" in response.json() and "path" in response.json()

            print("\nAll smoke tests passed. Temporary database will now be removed.")


if __name__ == "__main__":
    main()
