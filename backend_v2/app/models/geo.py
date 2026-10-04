"""
Geo reference tables (BLUEPRINT.md §15 "Data additions").

Storage is GeoJSON-in-JSONB, not PostGIS -- BLUEPRINT.md §3's technology
table specifies "PostgreSQL 16 with pg_trgm, jsonb", no PostGIS extension.
Phase 9 adds district and constituency areas (each with a representative
point computed inside its licensed polygon), per-authority and per-work
location, and the per-run map tables (map_build, map_work, geo_metric) the
six map endpoints read. Map building only READS risk_result (the published
default config) and copies values into map_work; it never writes it.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base

GEO_LEVELS = ("national", "state", "district", "constituency")


class GeoArea(Base):
    __tablename__ = "geo_area"
    __table_args__ = (
        UniqueConstraint("level", "key", name="uq_geo_area_level_key"),
        CheckConstraint(f"level IN {GEO_LEVELS}", name="ck_geo_area_level"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    level: Mapped[str] = mapped_column(String(20), nullable=False)
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("geo_area.id"))
    geometry: Mapped[dict] = mapped_column(JSONB, nullable=False)
    source: Mapped[str] = mapped_column(String(300), nullable=False)
    licence: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[str | None] = mapped_column(String(100))
    # Phase 9: one point guaranteed to lie inside `geometry` (shapely
    # representative_point), the single shared marker location for every
    # work in the area. Never a geocode, never offset.
    rep_lat: Mapped[float | None] = mapped_column(Float)
    rep_lon: Mapped[float | None] = mapped_column(Float)
    attrs: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")


class GeoNameCrosswalk(Base):
    """Unmatched portal names are still inserted here (geo_area_id=NULL,
    method='unmatched') so the match rate is reported, never silently
    dropped, per the Phase 1 brief."""

    __tablename__ = "geo_name_crosswalk"
    __table_args__ = (UniqueConstraint("portal_name", "level", name="uq_geo_crosswalk_name_level"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portal_name: Mapped[str] = mapped_column(String(200), nullable=False)
    level: Mapped[str] = mapped_column(String(20), nullable=False)
    geo_area_id: Mapped[int | None] = mapped_column(ForeignKey("geo_area.id"))
    method: Mapped[str] = mapped_column(String(40), nullable=False, default="unmatched")
    review_status: Mapped[str] = mapped_column(String(20), nullable=False, default="unreviewed")
    portal_state: Mapped[str | None] = mapped_column(String(200))  # Phase 9
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")  # Phase 9


class AuthorityGeo(Base):
    """Phase 9: where each district authority actually IS (district polygon and
    state). Until Phase 13.y the stored district_authority.state_id was the
    first MP row's state, wrong for 52 authorities; the ingest now stores the
    resolved state, so state_differs is false for every authority (a test
    enforces it) and stored_state_id mirrors the stored value."""

    __tablename__ = "authority_geo"

    district_authority_id: Mapped[int] = mapped_column(ForeignKey("district_authority.id"), primary_key=True)
    district_key: Mapped[str | None] = mapped_column(String(200))
    stored_state_id: Mapped[int | None] = mapped_column(ForeignKey("state.id"))
    resolved_state_id: Mapped[int | None] = mapped_column(ForeignKey("state.id"))
    district_area_id: Mapped[int | None] = mapped_column(ForeignKey("geo_area.id"))
    method: Mapped[str] = mapped_column(String(40), nullable=False)
    state_differs: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")


class WorkGeo(Base):
    """Phase 9: each work's location. Constituency (Lok Sabha only) from the
    work's own LS file rows; district/state from its authority's corrected
    location. NULL = not located at that level, never guessed."""

    __tablename__ = "work_geo"
    __table_args__ = (Index("ix_work_geo_constituency_area", "constituency_area_id"),)

    work_key: Mapped[str] = mapped_column(ForeignKey("work.work_key"), primary_key=True)
    house: Mapped[str] = mapped_column(String(2), nullable=False)
    constituency_id: Mapped[int | None] = mapped_column(ForeignKey("constituency.id"))
    constituency_area_id: Mapped[int | None] = mapped_column(ForeignKey("geo_area.id"))
    constituency_status: Mapped[str] = mapped_column(String(40), nullable=False)
    district_authority_id: Mapped[int | None] = mapped_column(ForeignKey("district_authority.id"))
    district_area_id: Mapped[int | None] = mapped_column(ForeignKey("geo_area.id"))
    location_state_id: Mapped[int | None] = mapped_column(ForeignKey("state.id"))
    location_method: Mapped[str] = mapped_column(String(40), nullable=False)


MAP_BUILD_STATUSES = ("building", "complete", "failed")


class MapBuild(Base):
    """One row per run whose map tables were (being) built. The map serves the
    published run only if its build is complete; otherwise the most recent
    complete build, labelled with its date (stale-data rule)."""

    __tablename__ = "map_build"
    __table_args__ = (CheckConstraint(f"status IN {MAP_BUILD_STATUSES}", name="ck_map_build_status"),)

    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_run.id"), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    config_name: Mapped[str] = mapped_column(String(20), nullable=False)
    data_as_of: Mapped[dt.date | None] = mapped_column(Date)
    counts: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    built_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MapWork(Base):
    """Per-run denormalised, indexed work rows for getMapWorks and the
    drill-down list. Filters and search hit this table's indexes (pg_trgm on
    search_text), never a scan of work / work_state / risk_result."""

    __tablename__ = "map_work"
    __table_args__ = (
        Index("ix_map_work_run_state", "run_id", "state"),
        Index("ix_map_work_run_constituency", "run_id", "constituency_state", "constituency"),
        Index("ix_map_work_run_tier", "run_id", "tier"),
        Index("ix_map_work_run_district_area", "run_id", "district_area_id"),
        Index("ix_map_work_run_constituency_area", "run_id", "constituency_area_id"),
        Index(
            "ix_map_work_search_trgm",
            "search_text",
            postgresql_using="gin",
            postgresql_ops={"search_text": "gin_trgm_ops"},
        ),
    )

    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_run.id"), primary_key=True)
    work_key: Mapped[str] = mapped_column(ForeignKey("work.work_key"), primary_key=True)
    house: Mapped[str] = mapped_column(String(2), nullable=False)
    state: Mapped[str | None] = mapped_column(String(200))  # location state (portal name)
    constituency: Mapped[str | None] = mapped_column(String(200))
    constituency_state: Mapped[str | None] = mapped_column(String(200))  # the marker's (constituency) state
    constituency_area_id: Mapped[int | None] = mapped_column(ForeignKey("geo_area.id"))
    district_area_id: Mapped[int | None] = mapped_column(ForeignKey("geo_area.id"))
    district_name: Mapped[str | None] = mapped_column(String(200))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    tier: Mapped[str] = mapped_column(String(15), nullable=False)
    risk: Mapped[float | None] = mapped_column(Float)  # 0..1
    confidence: Mapped[float | None] = mapped_column(Float)
    active_signals: Mapped[int | None] = mapped_column(Integer)
    amount: Mapped[float | None] = mapped_column(Float)
    stage: Mapped[str | None] = mapped_column(String(20))
    category: Mapped[str | None] = mapped_column(String(300))
    mp: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    record_date: Mapped[dt.date | None] = mapped_column(Date)
    search_text: Mapped[str] = mapped_column(Text, nullable=False)


class GeoMetric(Base):
    """Per-run pre-aggregated area metrics at the grain (area, House, tier,
    stage), so any combination of the map's house / risk-level / stage
    filters is a SUM over a few thousand rows, never a works scan. Sums, not
    rates, so parents are exact sums of children; rates and the small-number
    rule are applied when read."""

    __tablename__ = "geo_metric"
    __table_args__ = (
        UniqueConstraint("run_id", "level", "area_key", "house", "tier", "stage", name="uq_geo_metric_cell"),
        Index("ix_geo_metric_run_level", "run_id", "level"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_run.id"), nullable=False)
    level: Mapped[str] = mapped_column(String(20), nullable=False)  # national|state|district|constituency
    area_key: Mapped[str] = mapped_column(String(200), nullable=False)  # geo key or "unlocated:<parent>"
    area_name: Mapped[str] = mapped_column(String(200), nullable=False)
    state: Mapped[str | None] = mapped_column(String(200))
    house: Mapped[str] = mapped_column(String(2), nullable=False)
    tier: Mapped[str] = mapped_column(String(15), nullable=False)
    stage: Mapped[str] = mapped_column(String(20), nullable=False)
    n: Mapped[int] = mapped_column(Integer, nullable=False)
    amount_sum: Mapped[float] = mapped_column(Float, nullable=False)
    risk_sum: Mapped[float] = mapped_column(Float, nullable=False)
    risk_n: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence_sum: Mapped[float] = mapped_column(Float, nullable=False)
    signals_sum: Mapped[float] = mapped_column(Float, nullable=False)
