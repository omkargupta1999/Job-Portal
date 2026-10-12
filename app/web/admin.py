"""Job sources, alerts, settings, backups, sync API and health check."""

from __future__ import annotations

from urllib.parse import parse_qs, urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import func, select, text

from app import __version__
from app.backup import backup_path, create_backup, list_backups
from app.config import get_settings
from app.db import read_session, write_session
from app.directory import find_career_pages, load_directory, parse_board_url
from app.http import HttpClient
from app.migrations import LATEST_VERSION
from app.models import Alert, Job, Profile, Source, SyncRun, utcnow
from app.quickadd import bookmarklet
from app.scheduler import get_scheduler
from app.scoring import request_rescore, rescore_running
from app.search import JobFilters, count_jobs
from app.sources import ADAPTERS, BOARD_ADAPTERS, source_key
from app.sources.base import IDENTIFIER_RE
from app.sources.search_links import SEARCH_SITES, search_links_for_profile
from app.store import runtime, set_value, validate_runtime_update
from app.sync import SyncBusy, start_sync_thread
from app.web.common import iso, json_ok, redirect, render, sync_state, wants_json

router = APIRouter()


# ------------------------------------------------------------------ sources
def _sources_context(request: Request) -> dict:
    with read_session() as s:
        profile = s.get(Profile, 1)
        sources = list(s.scalars(select(Source).order_by(Source.adapter, Source.name)))
        active = dict(s.execute(select(Job.source_id, func.count(Job.id)).where(Job.is_expired.is_(False))
                                .group_by(Job.source_id)).all())
        relevant = dict(s.execute(select(Job.source_id, func.count(Job.id)).where(
            Job.is_expired.is_(False), Job.is_relevant.is_(True)).group_by(Job.source_id)).all())
        total = dict(s.execute(select(Job.source_id, func.count(Job.id)).group_by(Job.source_id)).all())
    by_key = {src.key: src for src in sources}
    directory = [{**entry, "source": by_key.get(entry["key"])} for entry in load_directory()]
    return {"sources": sources, "active_counts": active, "relevant_counts": relevant, "total_counts": total,
            "adapters": ADAPTERS, "search_sites": SEARCH_SITES, "directory": directory,
            "board_adapters": BOARD_ADAPTERS, "settings_obj": get_settings(), "by_key": set(by_key),
            "bookmarklet": bookmarklet(str(request.base_url)),
            "search_links": search_links_for_profile(profile) if profile else []}


@router.get("/sources")
def sources_page(request: Request):
    return render(request, "sources.html", "sources", **_sources_context(request))


def _add_board(s, adapter: str, identifier: str, name: str = "") -> Source | None:
    """Create a source row if missing. Returns the row (new or existing)."""
    cls = ADAPTERS[adapter]
    key = source_key(adapter, identifier)
    existing = s.scalar(select(Source).where(Source.key == key))
    if existing:
        return existing
    label = f"{name} · {cls.display_name}" if name else (f"{cls.display_name} · {identifier}" if identifier
                                                        else cls.display_name)
    src = Source(key=key, adapter=adapter, name=label[:150], identifier=identifier, enabled=True)
    s.add(src)
    s.flush()
    return src


def _start_fetch(source_ids: list[int]) -> str:
    try:
        start_sync_thread("manual", source_ids)
        return "Fetching now - watch the progress bar."
    except SyncBusy:
        return "A sync is already running; they will be fetched on the next sync."


@router.post("/sources")
def add_source(adapter: str = Form(""), identifier: str = Form(""), name: str = Form(""),
               fetch: str = Form("")):
    identifier = identifier.strip()
    parsed = parse_board_url(identifier) if "/" in identifier or "." in identifier else None
    if parsed:
        adapter, identifier = parsed
    cls = ADAPTERS.get(adapter)
    if cls is None or not cls.fetchable:
        return redirect("/sources", error="Unknown source type. Paste the company's careers URL or choose a platform.")
    if cls.requires_identifier:
        if not IDENTIFIER_RE.match(identifier):
            return redirect("/sources", error=f"Invalid {cls.identifier_label.lower()}. "
                                              "Use letters, digits, '-', '_' or '.', or paste the careers URL.")
    else:
        identifier = ""
    with write_session() as s:
        if s.scalar(select(Source.id).where(Source.key == source_key(adapter, identifier))):
            return redirect("/sources", error="That source is already configured.")
        sid = _add_board(s, adapter, identifier, name.strip()[:100]).id
    msg = f"Added {cls.display_name} · {identifier}." if identifier else f"Added {cls.display_name}."
    if fetch:
        msg += " " + _start_fetch([sid])
    else:
        msg += " Use Refresh to fetch it now."
    return redirect("/sources", notice=msg)


@router.post("/sources/directory")
async def add_from_directory(request: Request):
    form = await request.form()
    entries = {e["key"]: e for e in load_directory()}
    region = form.get("region", "")
    if form.get("all"):
        chosen = [e for e in entries.values() if not region or region in e["regions"]]
    else:
        chosen = [entries[k] for k in form.getlist("keys") if k in entries]
    if not chosen:
        return redirect("/sources", error="Select at least one company.")
    ids = []
    with write_session() as s:
        for e in chosen:
            src = _add_board(s, e["ats"], e["id"], e["name"])
            if src.enabled:
                ids.append(src.id)
    return redirect("/sources", notice=f"Added {len(chosen)} career page(s). {_start_fetch(ids)} "
                                       "Pages that no longer exist are disabled automatically.")


def _probe_client() -> HttpClient:
    return HttpClient(timeout=8, max_retries=0)


@router.post("/sources/find")
def find_company(request: Request, company: str = Form(...)):
    company = company.strip()[:80]
    ctx = _sources_context(request)
    if not company:
        return render(request, "sources.html", "sources", error="Enter a company name.", **ctx)
    with _probe_client() as client:
        results = find_career_pages(client, company)
    return render(request, "sources.html", "sources", find_query=company, find_results=results, **ctx)


@router.post("/sources/{source_id}/toggle")
def toggle_source(request: Request, source_id: int):
    with write_session() as s:
        src = s.get(Source, source_id)
        if src is None:
            raise HTTPException(404, "Source not found")
        src.enabled = not src.enabled
        if not src.enabled and src.status != "running":
            src.status = "disabled"
        elif src.enabled and src.status == "disabled":
            src.status = "never" if not src.last_success_at else "ok"
        enabled = src.enabled
    if wants_json(request):
        return json_ok(enabled=enabled)
    return redirect("/sources", notice="Source enabled" if enabled else "Source disabled")


@router.post("/sources/{source_id}/delete")
def delete_source(source_id: int):
    with write_session() as s:
        src = s.get(Source, source_id)
        if src is None:
            raise HTTPException(404, "Source not found")
        if s.scalar(select(func.count(Job.id)).where(Job.source_id == source_id)):
            return redirect("/sources", error="This source has stored jobs. Disable it instead to keep history.")
        s.delete(src)
    return redirect("/sources", notice="Source removed")


@router.post("/sources/{source_id}/sync")
def sync_source_now(request: Request, source_id: int):
    with read_session() as s:
        if s.get(Source, source_id) is None:
            raise HTTPException(404, "Source not found")
    try:
        run_id = start_sync_thread("manual", [source_id])
    except SyncBusy as exc:
        if wants_json(request):
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=409)
        return redirect("/sources", error=str(exc))
    if wants_json(request):
        return json_ok(run_id=run_id)
    return redirect("/sources", notice="Refresh started")


# ------------------------------------------------------------------ sync API
@router.post("/api/sync")
def api_sync(request: Request):
    try:
        run_id = start_sync_thread("manual")
    except SyncBusy as exc:
        if wants_json(request):
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=409)
        return redirect("/", error=str(exc))
    sched = get_scheduler()
    if sched:
        sched.reschedule()
    if wants_json(request):
        return json_ok(run_id=run_id)
    return redirect("/", notice="Synchronisation started")


@router.get("/api/sync/status")
def api_sync_status():
    with read_session() as s:
        st = sync_state(s)
    run = st["run"]
    data = {
        "running": st["running"],
        "last_success_at": iso(st["last_success_at"]),
        "next_run_at": iso(st["next_run_at"]),
        "scheduler_active": st["scheduler_active"],
        "rescoring": rescore_running(),
        "run": None,
    }
    if run is not None:
        data["run"] = {
            "id": run.id, "status": run.status, "trigger": run.trigger, "current_source": run.current_source,
            "sources_done": run.sources_done, "sources_total": run.sources_total, "fetched": run.fetched,
            "added": run.added, "updated": run.updated, "skipped": run.skipped, "expired": run.expired,
            "errors": run.errors, "message": run.message, "started_at": iso(run.started_at),
            "finished_at": iso(run.finished_at),
        }
    return JSONResponse(data, headers={"Cache-Control": "no-store"})


# ------------------------------------------------------------------ alerts
@router.get("/alerts")
def alerts_page(request: Request):
    with read_session() as s:
        alerts = list(s.scalars(select(Alert).order_by(Alert.created_at.desc())))
        rt = runtime(s)
        live = {}
        for a in alerts:
            params = {k: v[-1] for k, v in parse_qs(a.query or "").items()}
            f = JobFilters.from_params(params)
            live[a.id] = (count_jobs(s, f, rt["new_job_days"]),
                          count_jobs(s, f, rt["new_job_days"], since=a.last_viewed_at), f)
    return render(request, "alerts.html", "alerts", alerts=alerts, live=live,
                  prefill=request.query_params.get("q_from", ""))


@router.post("/alerts")
def create_alert(name: str = Form(...), query: str = Form("")):
    name = name.strip()[:120]
    if not name:
        return redirect("/alerts", error="Give the alert a name.")
    query = query.lstrip("?")[:4000]
    params = {k: v[-1] for k, v in parse_qs(query).items()}
    f = JobFilters.from_params(params)
    clean = urlencode(f.to_params(page=1)) if query else ""
    with write_session() as s:
        s.add(Alert(name=name, query=clean, last_viewed_at=utcnow()))
    return redirect("/alerts", notice=f"Alert “{name}” created")


@router.get("/alerts/{alert_id}/open")
def open_alert(alert_id: int):
    with write_session() as s:
        a = s.get(Alert, alert_id)
        if a is None:
            raise HTTPException(404, "Alert not found")
        query = a.query
        since = a.last_viewed_at
        a.last_viewed_at = utcnow()
        a.new_matches = 0
    extra = "&" if query else ""
    return redirect(f"/discover?{query}{extra}sort=newest" if "sort=" not in query else f"/discover?{query}",
                    notice=f"Showing alert results (new since {since:%d %b %H:%M} UTC are marked New)")


@router.post("/alerts/{alert_id}/toggle")
def toggle_alert(alert_id: int):
    with write_session() as s:
        a = s.get(Alert, alert_id)
        if a is None:
            raise HTTPException(404, "Alert not found")
        a.enabled = not a.enabled
    return redirect("/alerts", notice="Alert updated")


@router.post("/alerts/{alert_id}/delete")
def delete_alert(alert_id: int):
    with write_session() as s:
        a = s.get(Alert, alert_id)
        if a is None:
            raise HTTPException(404, "Alert not found")
        s.delete(a)
    return redirect("/alerts", notice="Alert deleted")


# ------------------------------------------------------------------ settings
@router.get("/settings")
def settings_page(request: Request):
    settings = get_settings()
    with read_session() as s:
        rt = runtime(s)
        runs = list(s.scalars(select(SyncRun).order_by(SyncRun.id.desc()).limit(10)))
        job_total = s.scalar(select(func.count(Job.id))) or 0
    db_size = settings.db_path.stat().st_size if settings.db_path.exists() else 0
    return render(request, "settings.html", "settings", rt=rt, runs=runs, backups=list_backups(settings.backups_dir),
                  db_size=db_size, job_total=job_total, schema_version=LATEST_VERSION)


@router.post("/settings")
async def save_settings(request: Request):
    form = await request.form()
    try:
        values = validate_runtime_update({k: str(v) for k, v in form.items()})
    except ValueError as exc:
        return redirect("/settings", error=str(exc))
    with write_session() as s:
        old = runtime(s)
        for k, v in values.items():
            set_value(s, k, v)
    if old["match_weights"] != values["match_weights"] or old["strict_location"] != values["strict_location"]:
        request_rescore()
    sched = get_scheduler()
    if sched:
        sched.reschedule()
    return redirect("/settings", notice="Settings saved")


@router.post("/settings/backup")
def make_backup():
    settings = get_settings()
    path = create_backup(settings.db_path, settings.backups_dir, "manual")
    return redirect("/settings", notice=f"Backup created: {path.name}")


@router.get("/settings/backups/{name}")
def download_backup(name: str):
    settings = get_settings()
    try:
        path = backup_path(settings.backups_dir, name)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Backup not found") from exc
    return FileResponse(path, media_type="application/octet-stream", filename=name,
                        headers={"Cache-Control": "no-store"})


@router.post("/settings/rescore")
def rescore():
    request_rescore()
    return redirect("/settings", notice="Re-scoring started in the background")


# ------------------------------------------------------------------ health
@router.get("/health")
def health():
    checks = {"database": "ok"}
    status = 200
    try:
        with read_session() as s:
            s.execute(text("SELECT 1"))
            version = s.execute(text("SELECT MAX(version) FROM schema_version")).scalar()
    except Exception as exc:  # noqa: BLE001
        checks["database"] = f"error: {exc.__class__.__name__}"
        version = None
        status = 503
    sched = get_scheduler()
    return JSONResponse({
        "status": "ok" if status == 200 else "degraded",
        "version": __version__,
        "schema_version": version,
        "scheduler": "running" if sched and sched.running else "inactive",
        "checks": checks,
    }, status_code=status)
