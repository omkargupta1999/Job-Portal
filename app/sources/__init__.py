"""Source adapter registry."""

from __future__ import annotations

from app.sources.adzuna import AdzunaAdapter
from app.sources.arbeitnow import ArbeitnowAdapter
from app.sources.ashby import AshbyAdapter
from app.sources.base import FetchResult, NormalizedJob, SearchContext, SourceAdapter, SourceNotConfigured
from app.sources.email_alerts import EmailAlertsAdapter
from app.sources.greenhouse import GreenhouseAdapter
from app.sources.jobicy import JobicyAdapter
from app.sources.lever import LeverAdapter, LeverEUAdapter
from app.sources.manual import ManualAdapter
from app.sources.remoteok import RemoteOKAdapter
from app.sources.remotive import RemotiveAdapter
from app.sources.smartrecruiters import SmartRecruitersAdapter
from app.sources.workable import WorkableAdapter

ADAPTERS: dict[str, type[SourceAdapter]] = {
    cls.adapter_type: cls
    for cls in (
        AdzunaAdapter, ArbeitnowAdapter, RemotiveAdapter, JobicyAdapter, RemoteOKAdapter,
        GreenhouseAdapter, LeverAdapter, LeverEUAdapter, AshbyAdapter, SmartRecruitersAdapter, WorkableAdapter,
        EmailAlertsAdapter, ManualAdapter,
    )
}
BOARD_ADAPTERS = [k for k, cls in ADAPTERS.items() if cls.kind == "board"]


def make_adapter(adapter_type: str, identifier: str = "") -> SourceAdapter:
    try:
        cls = ADAPTERS[adapter_type]
    except KeyError as exc:
        raise ValueError(f"Unknown source adapter: {adapter_type}") from exc
    return cls(identifier) if cls.requires_identifier else cls()


def source_key(adapter_type: str, identifier: str = "") -> str:
    return f"{adapter_type}:{identifier.lower()}" if identifier else adapter_type


__all__ = ["ADAPTERS", "BOARD_ADAPTERS", "FetchResult", "NormalizedJob", "SearchContext", "SourceAdapter",
           "SourceNotConfigured", "make_adapter", "source_key"]
