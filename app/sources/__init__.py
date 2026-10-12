"""Source adapter registry."""

from __future__ import annotations

from app.sources.arbeitnow import ArbeitnowAdapter
from app.sources.base import FetchResult, NormalizedJob, SourceAdapter
from app.sources.greenhouse import GreenhouseAdapter
from app.sources.lever import LeverAdapter, LeverEUAdapter
from app.sources.remotive import RemotiveAdapter

ADAPTERS: dict[str, type[SourceAdapter]] = {
    cls.adapter_type: cls
    for cls in (ArbeitnowAdapter, RemotiveAdapter, GreenhouseAdapter, LeverAdapter, LeverEUAdapter)
}


def make_adapter(adapter_type: str, identifier: str = "") -> SourceAdapter:
    try:
        cls = ADAPTERS[adapter_type]
    except KeyError as exc:
        raise ValueError(f"Unknown source adapter: {adapter_type}") from exc
    return cls(identifier) if cls.requires_identifier else cls()


def source_key(adapter_type: str, identifier: str = "") -> str:
    return f"{adapter_type}:{identifier.lower()}" if identifier else adapter_type


__all__ = ["ADAPTERS", "FetchResult", "NormalizedJob", "SourceAdapter", "make_adapter", "source_key"]
