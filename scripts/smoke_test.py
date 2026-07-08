"""
Standalone smoke test: exercises every endpoint against a throwaway SQLite
DB. Run with: python scripts/smoke_test.py (from the project root).
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def main() -> None:
    with TestClient(app) as client:
        # 1. Health check (auto-seed may already have populated data on startup).
        r = client.get("/api/health")
        assert r.status_code == 200, r.text
        print("health (on startup):", r.json())

        # 2. Trigger ingestion (reset=True so counts below are deterministic).
        r = client.post("/api/ingest/batch", json={"num_records": 500, "reset": True})
        assert r.status_code == 202, r.text
        print("ingest:", r.json())

        time.sleep(1.0)

        r = client.get("/api/health")
        assert r.status_code == 200, r.text
        print("health (post-ingest):", r.json())
        assert r.json()["total_route_delay_logs"] == 500

        # 3. Stations.
        r = client.get("/api/stations")
        assert r.status_code == 200, r.text
        print(f"stations: {len(r.json())} rows, sample: {r.json()[:1]}")

        # 4. Route delay logs with filter.
        r = client.get("/api/routes/delays", params={"train_category": "RE", "limit": 5})
        assert r.status_code == 200, r.text
        print(f"routes/delays (RE): {len(r.json())} rows")

        # 5. Hourly congestion.
        r = client.get("/api/analytics/hourly-congestion")
        assert r.status_code == 200, r.text
        print(f"hourly-congestion: {len(r.json())} buckets, sample: {r.json()[:1]}")

        # 5b. Hourly congestion, region-filtered.
        r = client.get("/api/regions")
        assert r.status_code == 200, r.text
        regions = r.json()
        print(f"regions: {regions}")
        if regions:
            r = client.get("/api/analytics/hourly-congestion", params={"region": regions[0]})
            assert r.status_code == 200, r.text
            print(f"hourly-congestion ({regions[0]}): {len(r.json())} buckets")

        # 5c. Dashboard KPIs.
        r = client.get("/api/dashboard/kpis")
        assert r.status_code == 200, r.text
        print("dashboard kpis:", r.json())

        # 5d. Dashboard HTML page renders.
        r = client.get("/")
        assert r.status_code == 200, r.text
        assert "Transit Intelligence" in r.text
        assert "hourly-chart" in r.text
        print(f"dashboard page: {len(r.text)} bytes rendered")

        if regions:
            r = client.get("/", params={"region": regions[0]})
            assert r.status_code == 200, r.text
            print(f"dashboard page (region={regions[0]}): OK")

        # 6. Weekly reliability.
        r = client.get("/api/analytics/weekly-reliability")
        assert r.status_code == 200, r.text
        print(f"weekly-reliability: {len(r.json())} buckets, sample: {r.json()[:1]}")

        # 7. Route blocks.
        r = client.get("/api/analytics/route-blocks")
        assert r.status_code == 200, r.text
        print(f"route-blocks: {len(r.json())} rows, sample: {r.json()[:1]}")

        # 8. Semester ticket optimizer -- verify no ICE/IC leaks through.
        r = client.get("/api/analytics/semester-ticket")
        assert r.status_code == 200, r.text
        categories = {row["train_category"] for row in r.json()}
        assert categories.issubset({"RE", "RB", "S_BAHN"}), categories
        print(f"semester-ticket: {len(r.json())} rows, categories: {categories}")

        # 9. 404 handler.
        r = client.get("/api/does-not-exist")
        assert r.status_code == 404, r.text
        body = r.json()
        assert "timestamp" in body and "path" in body
        print("404 handler:", body)

        print("\nAll smoke tests passed.")


if __name__ == "__main__":
    main()
