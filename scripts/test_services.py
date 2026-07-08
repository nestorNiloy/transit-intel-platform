"""
Unit tests for services.normalize_departure using realistic mocked HAFAS
payloads (shaped per https://v6.db.transport.rest/api.html), since hitting
the real third-party API in CI would be flaky and rate-limited.

Run with: python scripts/test_services.py (from the project root).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.models import TrainCategory  # noqa: E402
from app.services import normalize_departure  # noqa: E402


def test_regional_express_with_explicit_delay() -> None:
    raw = {
        "tripId": "1|12345|0|80|05072026",
        "direction": "Lübeck Hbf",
        "line": {"product": "regionalExpress", "name": "RE 8"},
        "when": "2026-07-05T08:03:00+02:00",
        "plannedWhen": "2026-07-05T08:00:00+02:00",
        "delay": 180,
    }
    row = normalize_departure(raw, origin_station="Hamburg Hbf")
    assert row is not None
    assert row["train_category"] == TrainCategory.RE
    assert row["delay_seconds"] == 180
    assert row["origin_station"] == "Hamburg Hbf"
    assert row["destination_station"] == "Lübeck Hbf"
    assert row["hour_of_day"] == 8


def test_regional_bahn_computes_delay_from_timestamps() -> None:
    raw = {
        "tripId": "1|99999|0|80|05072026",
        "direction": "Bremen Hbf",
        "line": {"product": "regional", "name": "RB 31"},
        "when": "2026-07-05T17:45:00+02:00",
        "plannedWhen": "2026-07-05T17:40:00+02:00",
        # no "delay" field -> must fall back to a timestamp diff
    }
    row = normalize_departure(raw, origin_station="Hamburg Hbf")
    assert row is not None
    assert row["train_category"] == TrainCategory.RB
    assert row["delay_seconds"] == 300


def test_long_distance_categories_are_dropped() -> None:
    for product in ("nationalExpress", "national", "suburban", "bus"):
        raw = {
            "tripId": "1|11111|0|80|05072026",
            "direction": "Berlin Hbf",
            "line": {"product": product},
            "when": "2026-07-05T09:00:00+02:00",
            "plannedWhen": "2026-07-05T09:00:00+02:00",
            "delay": 0,
        }
        assert normalize_departure(raw, origin_station="Hamburg Hbf") is None, product


def test_missing_timestamps_are_dropped_not_crashed_on() -> None:
    raw = {
        "tripId": "1|22222|0|80|05072026",
        "direction": "Kiel Hbf",
        "line": {"product": "regional"},
        "when": None,
        "plannedWhen": None,
    }
    assert normalize_departure(raw, origin_station="Hamburg Hbf") is None


def main() -> None:
    tests = [
        test_regional_express_with_explicit_delay,
        test_regional_bahn_computes_delay_from_timestamps,
        test_long_distance_categories_are_dropped,
        test_missing_timestamps_are_dropped_not_crashed_on,
    ]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print("\nAll services unit tests passed.")


if __name__ == "__main__":
    main()
