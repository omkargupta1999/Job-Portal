"""First-run data: initial profile and sources from configuration.

The initial profile is inserted only when no profile row exists. After that,
the database is the single source of truth and the matching engine reads the
saved profile; nothing here is consulted again.
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from app.config import Settings
from app.db import write_session
from app.models import Profile, Source
from app.sources import ADAPTERS, source_key

log = logging.getLogger(__name__)

INITIAL_PROFILE = {
    "headline": "Senior DevOps Engineer",
    "summary": (
        "Senior DevOps Engineer with 6+ years of experience in enterprise CI/CD automation, Kubernetes, "
        "Red Hat OpenShift, GitLab CI/CD, Jenkins, DevSecOps, infrastructure automation, and production "
        "platform operations.\n\n"
        "Experienced in secure CI/CD pipelines, reusable pipeline templates, SAST integration, container image "
        "scanning, Terraform, Helm, Istio service mesh, monitoring and observability, disaster recovery "
        "automation, performance optimization, and production troubleshooting.\n\n"
        "Professional experience includes regulated banking and payment platforms, including SBI EPay 2.0 and "
        "RBI Ekuber 2.0, as well as cloud infrastructure support for healthcare IT workloads."
    ),
    "years_experience": 6.0,
    "skills": [
        "GitLab CI/CD", "Jenkins", "Kubernetes", "Red Hat OpenShift", "Docker", "Helm", "Terraform", "AWS", "GCP",
        "Prometheus", "Grafana", "Loki", "EFK Stack", "Istio", "JFrog Artifactory", "VMware", "Python", "Bash",
        "Linux", "DevSecOps", "SAST", "Container Image Scanning", "CI/CD", "Infrastructure as Code",
        "Observability", "Disaster Recovery", "Performance Tuning", "Incident Management",
    ],
    "past_titles": ["Senior DevOps Engineer", "DevOps Engineer"],
    "target_titles": [
        "Senior DevOps Engineer", "DevOps Engineer", "DevSecOps Engineer", "Platform Engineer",
        "Kubernetes Engineer", "OpenShift Engineer", "Site Reliability Engineer", "Cloud Infrastructure Engineer",
        "CI/CD Engineer", "Infrastructure Automation Engineer", "Build and Release Engineer",
        "Cloud Operations Engineer",
    ],
    "experience_notes": (
        "Regulated banking and payment platforms (SBI EPay 2.0, RBI Ekuber 2.0); cloud infrastructure support "
        "for healthcare IT workloads."
    ),
    "preferred_countries": ["IN"],
    "preferred_cities": ["Mumbai", "Pune", "Bengaluru", "Hyderabad", "Chennai"],
    "work_modes": ["remote", "hybrid", "onsite"],
    "remote_worldwide_ok": True,
    "employment_types": ["full-time"],
    "salary_currency": "INR",
}


def seed_profile() -> None:
    with write_session() as s:
        if s.scalar(select(Profile.id).limit(1)) is not None:
            return
        s.add(Profile(id=1, **INITIAL_PROFILE))
        log.info("Created initial profile")


def sync_sources_from_config(settings: Settings) -> None:
    """Add sources configured in the environment. Never removes or re-enables
    sources the user changed in the UI."""
    wanted: list[tuple[str, str, bool]] = [
        ("arbeitnow", "", settings.enable_arbeitnow),
        ("remotive", "", settings.enable_remotive),
    ]
    wanted += [("greenhouse", t, True) for t in settings.greenhouse_boards]
    wanted += [("lever", t, True) for t in settings.lever_sites]
    wanted += [("lever_eu", t, True) for t in settings.lever_eu_sites]
    with write_session() as s:
        existing = {row.key for row in s.scalars(select(Source))}
        for adapter, ident, enabled in wanted:
            key = source_key(adapter, ident)
            if key in existing:
                continue
            cls = ADAPTERS[adapter]
            name = f"{cls.display_name} · {ident}" if ident else cls.display_name
            s.add(Source(key=key, adapter=adapter, name=name, identifier=ident, enabled=enabled))
            log.info("Registered source %s (enabled=%s)", key, enabled)
