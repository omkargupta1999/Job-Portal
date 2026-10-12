"""ORM models. Schema changes are applied by app/migrations.py."""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Profile(Base):
    __tablename__ = "profile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(200), default="")
    email: Mapped[str] = mapped_column(String(200), default="")
    phone: Mapped[str] = mapped_column(String(50), default="")
    current_location: Mapped[str] = mapped_column(String(200), default="")
    headline: Mapped[str] = mapped_column(String(300), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    years_experience: Mapped[float | None] = mapped_column(Float, nullable=True)
    skills: Mapped[list] = mapped_column(JSON, default=list)
    preferred_skills: Mapped[list] = mapped_column(JSON, default=list)
    past_titles: Mapped[list] = mapped_column(JSON, default=list)
    target_titles: Mapped[list] = mapped_column(JSON, default=list)
    certifications: Mapped[list] = mapped_column(JSON, default=list)
    education: Mapped[list] = mapped_column(JSON, default=list)
    experience_notes: Mapped[str] = mapped_column(Text, default="")
    preferred_countries: Mapped[list] = mapped_column(JSON, default=list)
    preferred_cities: Mapped[list] = mapped_column(JSON, default=list)
    work_modes: Mapped[list] = mapped_column(JSON, default=list)
    remote_worldwide_ok: Mapped[bool] = mapped_column(Boolean, default=True)
    employment_types: Mapped[list] = mapped_column(JSON, default=list)
    salary_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    salary_currency: Mapped[str] = mapped_column(String(3), default="")
    notice_period: Mapped[str] = mapped_column(String(100), default="")
    work_authorization: Mapped[str] = mapped_column(String(300), default="")
    open_to_relocation: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Resume(Base):
    __tablename__ = "resumes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    stored_name: Mapped[str] = mapped_column(String(80), unique=True)
    file_type: Mapped[str] = mapped_column(String(10))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    extracted_text: Mapped[str] = mapped_column(Text, default="")
    extracted: Mapped[dict] = mapped_column(JSON, default=dict)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(150), unique=True)
    adapter: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(150))
    identifier: Mapped[str] = mapped_column(String(100), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(20), default="never")
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    last_counts: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_job_source_external"),
        Index("ix_jobs_visible", "is_hidden", "is_expired"),
        Index("ix_jobs_score", "match_score"),
        Index("ix_jobs_posted", "posted_at"),
        Index("ix_jobs_first_seen", "first_seen_at"),
        Index("ix_jobs_last_seen", "last_seen_at"),
        Index("ix_jobs_country", "country_code"),
        Index("ix_jobs_work_mode", "work_mode"),
        Index("ix_jobs_company", "company_norm"),
        Index("ix_jobs_fingerprint", "fingerprint"),
        Index("ix_jobs_saved", "is_saved"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="RESTRICT"))
    external_id: Mapped[str] = mapped_column(String(255))
    fingerprint: Mapped[str] = mapped_column(String(64))
    content_hash: Mapped[str] = mapped_column(String(64))

    title: Mapped[str] = mapped_column(String(300))
    company: Mapped[str] = mapped_column(String(200), default="")
    company_norm: Mapped[str] = mapped_column(String(200), default="")
    description_html: Mapped[str] = mapped_column(Text, default="")
    description_text: Mapped[str] = mapped_column(Text, default="")
    listing_url: Mapped[str] = mapped_column(String(1000), default="")
    apply_url: Mapped[str] = mapped_column(String(1000), default="")

    location_raw: Mapped[str] = mapped_column(String(500), default="")
    country_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    country_inferred: Mapped[bool] = mapped_column(Boolean, default=False)
    region: Mapped[str] = mapped_column(String(120), default="")
    city: Mapped[str] = mapped_column(String(120), default="")
    work_mode: Mapped[str] = mapped_column(String(10), default="unknown")
    location_type: Mapped[str] = mapped_column(String(20), default="unknown")
    remote_scope: Mapped[str] = mapped_column(String(120), default="")

    salary_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    salary_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    salary_currency: Mapped[str] = mapped_column(String(3), default="")
    salary_period: Mapped[str] = mapped_column(String(10), default="")
    salary_text: Mapped[str] = mapped_column(String(200), default="")
    salary_annual_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    salary_annual_max: Mapped[int | None] = mapped_column(Integer, nullable=True)

    employment_type: Mapped[str] = mapped_column(String(20), default="")
    experience_level: Mapped[str] = mapped_column(String(20), default="")
    years_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    years_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    visa_sponsorship: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    relocation: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    skills: Mapped[list] = mapped_column(JSON, default=list)
    preferred_skills: Mapped[list] = mapped_column(JSON, default=list)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    department: Mapped[str] = mapped_column(String(200), default="")

    posted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expired_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_expired: Mapped[bool] = mapped_column(Boolean, default=False)

    match_score: Mapped[int] = mapped_column(Integer, default=0)
    match_details: Mapped[dict] = mapped_column(JSON, default=dict)

    is_saved: Mapped[bool] = mapped_column(Boolean, default=False)
    saved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    last_opened_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    source: Mapped[Source] = relationship(lazy="joined")
    countries: Mapped[list[JobCountry]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )
    application: Mapped[Application | None] = relationship(
        back_populates="job", uselist=False, lazy="selectin"
    )


class JobCountry(Base):
    """All countries a job is associated with (multi-location postings)."""

    __tablename__ = "job_countries"
    __table_args__ = (Index("ix_job_countries_code", "country_code", "job_id"),)

    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)
    country_code: Mapped[str] = mapped_column(String(2), primary_key=True)


APPLICATION_STATUSES = [
    "Discovered",
    "Interested",
    "Saved",
    "Application Prepared",
    "Applied",
    "Assessment",
    "Recruiter Screen",
    "Interview",
    "Technical Interview",
    "Final Interview",
    "Offer",
    "Rejected",
    "Withdrawn",
    "No Response",
]
ACTIVE_PIPELINE = ["Applied", "Assessment", "Recruiter Screen", "Interview", "Technical Interview",
                   "Final Interview"]
INTERVIEW_STATUSES = ["Interview", "Technical Interview", "Final Interview"]
SUBMITTED_STATUSES = ["Applied", "Assessment", "Recruiter Screen", "Interview", "Technical Interview",
                      "Final Interview", "Offer", "Rejected", "No Response"]


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (
        Index("ix_app_status", "status"),
        Index("ix_app_followup", "follow_up_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Unique: at most one tracking record per job. SET NULL keeps the record
    # if a job row is ever purged.
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), unique=True, nullable=True
    )
    company: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(300))
    job_url: Mapped[str] = mapped_column(String(1000), default="")
    country: Mapped[str] = mapped_column(String(100), default="")
    status: Mapped[str] = mapped_column(String(30), default="Interested")
    applied_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    follow_up_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    interview_dates: Mapped[str] = mapped_column(Text, default="")
    recruiter_notes: Mapped[str] = mapped_column(Text, default="")
    salary_details: Mapped[str] = mapped_column(String(300), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    confirmation_ref: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    job: Mapped[Job | None] = relationship(back_populates="application", lazy="joined")
    events: Mapped[list[ApplicationEvent]] = relationship(
        cascade="all, delete-orphan", order_by="ApplicationEvent.at", lazy="selectin"
    )


class ApplicationEvent(Base):
    __tablename__ = "application_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    from_status: Mapped[str] = mapped_column(String(30), default="")
    to_status: Mapped[str] = mapped_column(String(30))
    note: Mapped[str] = mapped_column(String(500), default="")
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Preparation(Base):
    __tablename__ = "preparations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), unique=True)
    cover_letter: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    highlights: Mapped[str] = mapped_column(Text, default="")
    answers: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trigger: Mapped[str] = mapped_column(String(20))
    scope: Mapped[str] = mapped_column(String(150), default="all")
    status: Mapped[str] = mapped_column(String(20), default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    current_source: Mapped[str] = mapped_column(String(150), default="")
    sources_total: Mapped[int] = mapped_column(Integer, default=0)
    sources_done: Mapped[int] = mapped_column(Integer, default=0)
    fetched: Mapped[int] = mapped_column(Integer, default=0)
    added: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    expired: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text, default="")


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    query: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_viewed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    total_matches: Mapped[int] = mapped_column(Integer, default=0)
    new_matches: Mapped[int] = mapped_column(Integer, default=0)
    last_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
