import io
import time
import zipfile

from sqlalchemy import func, select

from app.db import read_session, write_session
from app.models import Application, ApplicationEvent, Job, Preparation, Profile, Resume, SyncRun

PAGES = ["/", "/discover", "/saved", "/hidden", "/applications", "/applications/new", "/profile", "/sources", "/alerts",
         "/settings"]


def wait_for_sync(client, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = client.get("/api/sync/status").json()
        if not data["running"] and data["run"] and data["run"]["status"] != "running":
            return data
        time.sleep(0.05)
    raise AssertionError("sync did not finish")


def sync_now(client):
    r = client.post("/api/sync", headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200 and r.json()["ok"]
    return wait_for_sync(client)


def job_id(external_id: str) -> int:
    with read_session() as s:
        return s.scalar(select(Job.id).where(Job.external_id == external_id))


def test_all_pages_render_empty(client):
    for path in PAGES:
        r = client.get(path)
        assert r.status_code == 200, path
        assert "JobHub" in r.text
    assert "Welcome to JobHub" in client.get("/").text


def test_health_check(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok" and r.json()["checks"]["database"] == "ok"


def test_security_headers_and_host_check(client):
    r = client.get("/")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert client.get("/", headers={"Host": "evil.example.com"}).status_code == 400


def test_cross_site_post_is_blocked(client):
    r = client.post("/api/sync", headers={"Origin": "https://evil.example.com"})
    assert r.status_code == 403
    r = client.post("/settings/backup", headers={"Referer": "https://evil.example.com/page"})
    assert r.status_code == 403
    r = client.post("/settings/backup", headers={"Origin": "http://localhost"}, follow_redirects=False)
    assert r.status_code == 303


def test_manual_sync_populates_dashboard(client):
    status = sync_now(client)
    assert status["run"]["status"] == "success" and status["run"]["added"] == 11
    assert status["last_success_at"]
    page = client.get("/").text
    assert "Best opportunities" in page and "Welcome to JobHub" not in page
    assert "Kubernetes Engineer" in page
    r = client.post("/api/sync", headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200  # a second run is allowed once the first finished
    wait_for_sync(client)


def test_discover_filters_via_http(client):
    sync_now(client)
    page = client.get("/discover?countries=IN&work_mode=hybrid").text
    assert "Kubernetes Engineer" in page and "Senior DevOps Engineer (m/w/d)" not in page
    page = client.get("/discover?location_type=remote_worldwide").text
    assert "Site Reliability Engineer" in page and "1 job match" in page


def test_job_detail_shows_explanation_and_sanitised_description(client):
    sync_now(client)
    jid = job_id("senior-devops-engineer-berlin-123")
    page = client.get(f"/jobs/{jid}").text
    assert "Why this score" in page and "Missing required skills" in page
    assert "<script>alert" not in page and "javascript:alert" not in page
    assert "Responsibilities" in page and "Operate Kubernetes" in page
    assert "Visa sponsorship" in page


def test_save_hide_toggle(client):
    sync_now(client)
    jid = job_id("1900001")
    r = client.post(f"/jobs/{jid}/save", data={"action": "save"}, headers={"X-Requested-With": "fetch"})
    assert r.json() == {"ok": True, "saved": True}
    title = f'href="/jobs/{jid}">Site Reliability Engineer<'
    assert title in client.get("/saved").text
    client.post(f"/jobs/{jid}/hide", data={"action": "hide", "next": "/discover"})
    assert title not in client.get("/discover?sort=newest").text
    assert title in client.get("/hidden").text
    client.post(f"/jobs/{jid}/hide", data={"action": "unhide"})
    with read_session() as s:
        job = s.get(Job, jid)
        assert job.is_saved and not job.is_hidden


def test_apply_redirects_without_marking_applied(client):
    sync_now(client)
    jid = job_id("aaaa-1111")
    r = client.get(f"/jobs/{jid}/apply", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "https://jobs.lever.co/samplesite/aaaa-1111/apply"
    with read_session() as s:
        job = s.get(Job, jid)
        assert job.last_opened_at is not None
        assert job.application is None  # opening is not applying


def test_apply_with_invalid_url_shows_fallback(client):
    sync_now(client)
    jid = job_id("aaaa-1111")
    with write_session() as s:
        s.get(Job, jid).apply_url = "javascript:alert(1)"
    r = client.get(f"/jobs/{jid}/apply", follow_redirects=False)
    assert r.status_code == 200
    assert "No valid application link" in r.text and "https://jobs.lever.co/samplesite/aaaa-1111" in r.text


def test_mark_as_applied_creates_single_record_with_history(client):
    sync_now(client)
    jid = job_id("4000002")
    for _ in range(2):
        r = client.post(f"/jobs/{jid}/track", data={"status": "Applied", "applied_on": "2026-10-10",
                                                     "confirmation_ref": "REF-1", "notes": "via portal"},
                        follow_redirects=False)
        assert r.status_code == 303
    client.post(f"/jobs/{jid}/track", data={"status": "Interview"})
    with read_session() as s:
        apps = list(s.scalars(select(Application).where(Application.job_id == jid)))
        assert len(apps) == 1
        a = apps[0]
        assert a.status == "Interview" and str(a.applied_on) == "2026-10-10" and a.confirmation_ref == "REF-1"
        events = [e.to_status for e in s.scalars(select(ApplicationEvent).where(ApplicationEvent.application_id == a.id))]
        assert events == ["Applied", "Interview"]
    assert client.post(f"/jobs/{jid}/track", data={"status": "Bogus"}).status_code == 400


def test_tracker_crud_filters_and_csv(client):
    r = client.post("/applications/new", data={"company": "=cmd|' /C calc'!A0", "title": "SRE", "status": "Applied",
                                                "country": "India", "job_url": "https://example.com/job/1",
                                                "follow_up_date": "2026-10-20"}, follow_redirects=False)
    assert r.status_code == 303
    app_id = int(r.headers["location"].split("/")[-1].split("?")[0])
    assert client.post("/applications/new", data={"company": "", "title": ""}).status_code == 400
    assert client.post("/applications/new", data={"company": "x", "title": "y", "job_url": "javascript:1"}).status_code == 400
    r = client.post(f"/applications/{app_id}", data={"company": "Acme", "title": "SRE", "status": "Offer",
                                                     "salary_details": "40 LPA", "notes": "great"})
    assert r.status_code == 200
    with read_session() as s:
        a = s.get(Application, app_id)
        assert a.status == "Offer" and a.applied_on is not None and a.salary_details == "40 LPA"
    assert "Acme" in client.get("/applications?group=offers").text
    assert "Acme" not in client.get("/applications?group=closed").text
    assert "Acme" in client.get("/applications?q=acm").text
    client.post(f"/applications/{app_id}/status", data={"status": "Rejected"})
    csv_text = client.get("/applications/export.csv").text
    assert csv_text.splitlines()[0].startswith("Company,Job title,Status")
    assert "Rejected" in csv_text
    client.post("/applications/new", data={"company": "=HYPERLINK(1)", "title": "x"})
    assert "'=HYPERLINK(1)" in client.get("/applications/export.csv").text  # formula injection neutralised
    client.post(f"/applications/{app_id}/delete", data={"confirm": "nope"})
    assert client.get(f"/applications/{app_id}").status_code == 200
    client.post(f"/applications/{app_id}/delete", data={"confirm": "DELETE"})
    assert client.get(f"/applications/{app_id}").status_code == 404


def test_application_survives_job_expiry(client):
    sync_now(client)
    jid = job_id("4000003")
    client.post(f"/jobs/{jid}/track", data={"status": "Applied"})
    with write_session() as s:
        s.get(Job, jid).is_expired = True
    page = client.get("/applications").text
    assert "listing expired" in page


def test_prepare_generates_honest_draft_and_downloads(client):
    sync_now(client)
    jid = job_id("senior-devops-engineer-berlin-123")
    page = client.get(f"/jobs/{jid}/prepare").text
    assert "Draft — review before use" in page and "Dear Hiring Team at Acme Cloud GmbH" in page
    r = client.post(f"/jobs/{jid}/prepare", data={"action": "regenerate"}, follow_redirects=False)
    assert r.status_code == 303
    with read_session() as s:
        prep = s.scalar(select(Preparation).where(Preparation.job_id == jid))
        profile = s.get(Profile, 1)
        job = s.get(Job, jid)
    # Only skills that are both in the profile and the job are claimed.
    for skill in job.match_details["missing_required"]:
        assert f"- {skill}\n" not in prep.highlights + "\n"
    assert "Go" not in prep.highlights.split()
    assert "6+ years" in prep.cover_letter and "[Add" in prep.cover_letter
    assert profile.summary.split("\n\n")[0] in prep.cover_letter
    client.post(f"/jobs/{jid}/prepare", data={"action": "save", "cover_letter": "Edited letter", "summary": "s",
                                              "highlights": "h", "answers": "a", "notes": "n", "mark_prepared": "1"})
    txt = client.get(f"/jobs/{jid}/prepare/download?format=txt")
    assert txt.text == "Edited letter" and "attachment" in txt.headers["content-disposition"]
    docx = client.get(f"/jobs/{jid}/prepare/download?format=docx")
    assert docx.content[:2] == b"PK"
    with read_session() as s:
        assert s.get(Job, jid).application.status == "Application Prepared"


def test_profile_save_normalises_and_rescores(client):
    sync_now(client)
    data = {"full_name": "Asha", "years_experience": "7", "skills": "k8s\nKubernetes, GitLab pipelines",
            "target_titles": "Platform Engineer", "preferred_countries": ["DE"], "preferred_cities": "Berlin",
            "work_modes": ["onsite", "hybrid"], "employment_types": ["full-time"], "salary_currency": "eur"}
    r = client.post("/profile", data=data, follow_redirects=False)
    assert r.status_code == 303
    with read_session() as s:
        p = s.get(Profile, 1)
        assert p.skills == ["Kubernetes", "GitLab CI/CD"]
        assert p.preferred_countries == ["DE"] and p.salary_currency == "EUR"
    from app.scoring import rescore_running

    for _ in range(100):
        if not rescore_running():
            break
        time.sleep(0.05)
    with read_session() as s:
        berlin = s.scalar(select(Job).where(Job.external_id == "senior-devops-engineer-berlin-123"))
        assert berlin.match_details["components"]["location"]["score"] == 100
    r = client.post("/profile", data={"years_experience": "abc"}, follow_redirects=False)
    assert "error=" in r.headers["location"]


def _docx_bytes(text: str) -> bytes:
    from docx import Document

    buf = io.BytesIO()
    doc = Document()
    for line in text.split("\n"):
        doc.add_paragraph(line)
    doc.save(buf)
    return buf.getvalue()


RESUME = """Asha Rao
asha@example.com
Professional Summary
DevOps engineer with 7 years of experience running OpenShift and GitLab CI.
Skills
Kubernetes, Terraform, Ansible, Prometheus
Experience
Senior DevOps Engineer - Bank
Certifications
Certified Kubernetes Administrator (CKA)
Education
B.Tech in Computer Science
"""


def test_resume_upload_txt_docx_and_merge(client, settings):
    r = client.post("/profile/resume", files={"file": ("cv.txt", RESUME.encode(), "text/plain")}, follow_redirects=False)
    assert r.status_code == 303 and "review=" in r.headers["location"]
    with read_session() as s:
        res = s.scalars(select(Resume)).one()
        assert "Ansible" in res.extracted["skills"]
        assert res.extracted["years_experience"] == 7
        assert any("CKA" in c for c in res.extracted["certifications"])
        assert "Senior DevOps Engineer" in res.extracted["titles"]
        stored = settings.uploads_dir / res.stored_name
        assert stored.exists() and stored.name != "cv.txt"
    assert "Merge selected" in client.get(f"/profile?review={res.id}").text
    client.post(f"/profile/resume/{res.id}/merge", data={"fields": ["skills", "certifications"]})
    with read_session() as s:
        p = s.get(Profile, 1)
        assert "Ansible" in p.skills and any("CKA" in c for c in p.certifications)
        assert p.years_experience == 6  # not merged unless chosen
    r = client.post("/profile/resume", files={"file": ("cv.docx", _docx_bytes(RESUME),
                                                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                    follow_redirects=False)
    assert "review=" in r.headers["location"]
    d = client.get(f"/profile/resume/{res.id}/download")
    assert d.status_code == 200 and "attachment" in d.headers["content-disposition"]
    assert client.get(f"/static/../data/uploads/{res.stored_name}").status_code == 404


def test_resume_upload_validation(client, settings):
    def upload(name, data):
        r = client.post("/profile/resume", files={"file": (name, data, "application/octet-stream")}, follow_redirects=False)
        return r.headers["location"]

    assert "error=" in upload("cv.exe", b"MZ....")
    assert "error=" in upload("cv.pdf", b"not a pdf")
    assert "error=" in upload("cv.docx", b"PK\x03\x04garbage")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("hello.txt", "x")
    assert "error=" in upload("cv.docx", buf.getvalue())
    assert "error=" in upload("cv.txt", b"")
    assert "error=" in upload("cv.txt", b"\x00\x01binary")
    big = b"a" * (settings.max_upload_mb * 1024 * 1024 + 10)
    assert "error=" in upload("cv.txt", big)
    assert "error=" not in upload("../../etc/passwd.txt", b"plain text resume")
    with read_session() as s:
        names = [r.original_filename for r in s.scalars(select(Resume))]
        stored = [r.stored_name for r in s.scalars(select(Resume))]
    assert names == ["passwd.txt"] and "/" not in stored[0]
    assert (settings.uploads_dir / stored[0]).exists()


def test_sources_management(client):
    r = client.post("/sources", data={"adapter": "greenhouse", "identifier": "https://boards.greenhouse.io/newco/"},
                    follow_redirects=False)
    assert "notice=" in r.headers["location"]
    assert "Greenhouse · newco" in client.get("/sources").text
    assert "error=" in client.post("/sources", data={"adapter": "greenhouse", "identifier": "newco"},
                                   follow_redirects=False).headers["location"]
    assert "error=" in client.post("/sources", data={"adapter": "lever", "identifier": "bad id!"},
                                   follow_redirects=False).headers["location"]
    with read_session() as s:
        from app.models import Source

        src = s.scalar(select(Source).where(Source.key == "greenhouse:newco"))
    client.post(f"/sources/{src.id}/toggle")
    with read_session() as s:
        from app.models import Source

        assert s.get(Source, src.id).enabled is False
    client.post(f"/sources/{src.id}/delete")
    assert "Greenhouse · newco" not in client.get("/sources").text
    page = client.get("/sources").text
    assert "LinkedIn" in page and "Not supported" in page


def test_source_specific_refresh(client):
    with read_session() as s:
        from app.models import Source

        sid = s.scalar(select(Source.id).where(Source.key == "remotive"))
    r = client.post(f"/sources/{sid}/sync", headers={"X-Requested-With": "fetch"})
    assert r.json()["ok"]
    wait_for_sync(client)
    with read_session() as s:
        assert s.scalar(select(func.count(Job.id))) == 3
        assert s.scalar(select(SyncRun.scope).order_by(SyncRun.id.desc())) == str(sid)


def test_alerts_flow(client):
    r = client.post("/alerts", data={"name": "India high", "query": "?countries=IN&min_score=0&bogus=1"},
                    follow_redirects=False)
    assert r.status_code == 303
    sync_now(client)
    page = client.get("/alerts").text
    assert "India high" in page and "4 new" in page
    with read_session() as s:
        from app.models import Alert

        alert = s.scalars(select(Alert)).one()
        assert "bogus" not in alert.query and alert.new_matches == 4
    r = client.get(f"/alerts/{alert.id}/open", follow_redirects=False)
    assert r.headers["location"].startswith("/discover?countries=IN")
    assert "4 new" not in client.get("/alerts").text


def test_settings_validation_and_backup(client, settings):
    good = {"sync_interval_hours": "12", "max_jobs_per_source": "200", "expired_retention_days": "30",
            "new_job_days": "5", "page_size": "25", "strict_location": "on", "w_skills": "50", "w_title": "20",
            "w_experience": "10", "w_location": "10", "w_work_mode": "5", "w_employment": "5"}
    r = client.post("/settings", data=good, follow_redirects=False)
    assert "notice=" in r.headers["location"]
    bad = dict(good, sync_interval_hours="0")
    assert "error=" in client.post("/settings", data=bad, follow_redirects=False).headers["location"]
    zero = {**good, **{k: "0" for k in good if k.startswith("w_")}}
    assert "error=" in client.post("/settings", data=zero, follow_redirects=False).headers["location"]
    from app.store import runtime

    with read_session() as s:
        rt = runtime(s)
    assert rt["sync_interval_hours"] == 12 and rt["match_weights"]["skills"] == 50
    client.post("/settings/backup")
    backups = list(settings.backups_dir.glob("jobhub-*.db"))
    assert backups
    assert client.get(f"/settings/backups/{backups[0].name}").status_code == 200
    assert client.get("/settings/backups/..%2Fjobhub.db").status_code == 404


def test_basic_auth_when_configured(settings, mock_feeds):
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from app.main import create_app

    secured = replace(settings, auth_user="admin", auth_password="correct-horse-battery")
    with TestClient(create_app(secured, start_scheduler=False), base_url="http://localhost") as c:
        assert c.get("/").status_code == 401
        assert c.get("/health").status_code == 200
        assert c.get("/", auth=("admin", "wrong-password-123")).status_code == 401
        assert c.get("/", auth=("admin", "correct-horse-battery")).status_code == 200
