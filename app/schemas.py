"""
Pydantic v2 schemas: request payload validation, query parameter shapes,
and response models for every endpoint in the platform.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from .models import TrainCategory


# ---------------- Station Metrics ----------------

class StationMetricBase(BaseModel):
    station_name: str
    region: str
    total_tracked_trips: int = 0
    average_delay_minutes: float = 0.0
    reliability_score: float = 100.0


class StationMetricCreate(StationMetricBase):
    pass


class StationMetricRead(StationMetricBase):
    model_config = ConfigDict(from_attributes=True)
    id: int


# ---------------- Route Delay Logs ----------------

class RouteDelayLogBase(BaseModel):
    origin_station: str
    destination_station: str
    train_category: TrainCategory
    scheduled_departure: datetime
    actual_departure: datetime
    delay_seconds: int = Field(ge=0)
    day_of_week: int = Field(ge=0, le=6)
    hour_of_day: int = Field(ge=0, le=23)
    source_trip_id: Optional[str] = None


class RouteDelayLogCreate(RouteDelayLogBase):
    pass


class RouteDelayLogRead(RouteDelayLogBase):
    model_config = ConfigDict(from_attributes=True)
    id: int


# ---------------- Ingestion ----------------

class IngestBatchRequest(BaseModel):
    num_records: int = Field(default=250, ge=1, le=5000, description="Rows to generate")
    reset: bool = Field(default=False, description="Wipe existing route_delay_logs first")


class IngestBatchResponse(BaseModel):
    status: str
    records_queued: int
    job_id: str


class IngestLiveRequest(BaseModel):
    stations: Optional[list[str]] = Field(
        default=None,
        description="Station names to poll. Defaults to the built-in Hamburg/Schleswig-Holstein set.",
    )
    duration_minutes: int = Field(default=180, ge=10, le=720)


class IngestLiveResponse(BaseModel):
    status: str
    job_id: str
    stations_targeted: list[str]


# ---------------- Analytics ----------------

class PeakHourBucket(BaseModel):
    hour_of_day: int
    total_trips: int
    average_delay_seconds: float
    on_time_rate_pct: float
    congestion_slot: str


class DayOfWeekBucket(BaseModel):
    day_of_week: int
    day_label: str
    total_trips: int
    average_delay_seconds: float
    on_time_rate_pct: float


class RouteBlockBreakdown(BaseModel):
    origin_station: str
    destination_station: str
    total_trips: int
    average_delay_seconds: float
    on_time_rate_pct: float


class SemesterTicketRoute(BaseModel):
    origin_station: str
    destination_station: str
    train_category: TrainCategory
    total_trips: int
    average_delay_seconds: float
    on_time_rate_pct: float


class RouteExtreme(BaseModel):
    origin_station: str
    destination_station: str
    on_time_rate_pct: float
    total_trips: int


class DashboardKPIs(BaseModel):
    average_delay_minutes: float
    best_route: Optional[RouteExtreme]
    worst_route: Optional[RouteExtreme]
    same_route_only: bool = False
    peak_congestion_hour: Optional[int]
    peak_congestion_slot: Optional[str]
    peak_congestion_average_delay_seconds: Optional[float]
    window_days: int
    region: Optional[str]


# ---------------- Errors ----------------

class ErrorResponse(BaseModel):
    error: str
    detail: str
    timestamp: datetime
    path: str
