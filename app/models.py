"""
SQLAlchemy ORM models for the Smart Public Transport Intelligence Platform.

Two core entities:
  - StationMetric: aggregate, per-station rollups (reliability, avg delay, trip volume)
  - RouteDelayLog: granular, per-trip delay records used to compute all aggregates
"""

import enum
from typing import Optional

from sqlalchemy import DateTime, Enum as SAEnum, Float, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TrainCategory(str, enum.Enum):
    ICE = "ICE"
    IC = "IC"
    RE = "RE"
    RB = "RB"
    S_BAHN = "S_BAHN"


class StationMetric(Base):
    """Aggregate reliability/volume metrics for a single station."""

    __tablename__ = "station_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    station_name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    region: Mapped[str] = mapped_column(String(80), nullable=False, index=True, default="Unbekannt")
    total_tracked_trips: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    average_delay_minutes: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    reliability_score: Mapped[float] = mapped_column(Float, nullable=False, default=100.0)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<StationMetric {self.station_name} region={self.region} reliability={self.reliability_score}>"


class RouteDelayLog(Base):
    """A single scheduled-vs-actual departure record for a trip on a route."""

    __tablename__ = "route_delay_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    origin_station: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    destination_station: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    train_category: Mapped[TrainCategory] = mapped_column(SAEnum(TrainCategory), nullable=False, index=True)
    scheduled_departure: Mapped["DateTime"] = mapped_column(DateTime, nullable=False)
    actual_departure: Mapped["DateTime"] = mapped_column(DateTime, nullable=False)
    delay_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False, index=True)  # 0=Mon .. 6=Sun
    hour_of_day: Mapped[int] = mapped_column(Integer, nullable=False, index=True)  # 0..23

    # Populated only for rows ingested from a real source (e.g. the live
    # db.transport.rest pipeline in services.py). Lets the upsert layer
    # recognize "the same trip, fetched again" instead of duplicating it.
    # Left NULL for synthetic/dummy rows.
    source_trip_id: Mapped[Optional[str]] = mapped_column(
        String(160), nullable=True, unique=True, index=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<RouteDelayLog {self.origin_station}->{self.destination_station} "
            f"{self.train_category} delay={self.delay_seconds}s>"
        )
