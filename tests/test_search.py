from starlette.datastructures import QueryParams

from app.db import read_session, write_session
from app.models import Application, Job
from app.search import JobFilters, search_jobs


def find(qs: str, **kw) -> list[str]:
    f = JobFilters.from_params(QueryParams(qs))
    with read_session() as s:
        page = search_jobs(s, f, per_page=kw.get("per_page", 50))
        return [j.external_id for j in page.items]


def test_country_filter_india(synced):
    ids = set(find("countries=IN"))
    # Bengaluru (physical), Remote-India (greenhouse), Remotive India-only, Lever Pune
    assert ids == {"4000001", "4000002", "1900002", "aaaa-1111"}


def test_multiple_countries_and_include_worldwide(synced):
    assert set(find("countries=DE,US")) == {"senior-devops-engineer-berlin-123", "frontend-developer-remote-456",
                                             "platform-engineer-munich-789", "1900003"}
    with_ww = set(find("countries=IN&include_worldwide=1"))
    assert "1900001" in with_ww and "4000001" in with_ww


def test_multi_location_job_matches_secondary_country(synced):
    # Lever posting lists Pune and Mumbai; both Indian. GB comes from the Greenhouse London listing.
    assert find("countries=GB") == ["4000003"]


def test_remote_worldwide_quick_filter(synced):
    assert find("location_type=remote_worldwide") == ["1900001"]


def test_remote_restricted_to_country(synced):
    assert set(find("remote_country=IN")) == {"1900002", "4000002"}
    assert set(find("work_mode=remote&countries=IN")) == {"1900002", "4000002"}


def test_work_mode_and_city_filters(synced):
    assert find("work_mode=hybrid&countries=IN") == ["aaaa-1111"]
    assert set(find("city=Pune")) == {"aaaa-1111"}
    assert set(find("region=Karnataka")) == {"4000001"}


def test_combined_filters(synced):
    assert find("countries=IN&employment=contract&skills=SAST") == ["1900002"]
    assert find("countries=IN&employment=contract&skills=Helm") == []
    assert find("q=terraform&countries=IN&min_score=50") == [j for j in find("q=terraform&countries=IN")
                                                              if j in find("min_score=50")]


def test_salary_and_currency_filters(synced):
    assert find("currency=INR") == ["aaaa-1111"]
    assert set(find("salary_min=100000&currency=USD")) == {"1900001"}
    assert "senior-devops-engineer-berlin-123" in find("salary_min=80000&salary_max=90000")


def test_experience_filters(synced):
    ids = find("exp_min=5")
    assert "senior-devops-engineer-berlin-123" in ids and "4000001" in ids
    assert "senior-devops-engineer-berlin-123" not in find("exp_max=4")
    assert "senior-devops-engineer-berlin-123" in find("level=senior")


def test_visa_saved_hidden_unapplied_filters(synced):
    assert find("visa=1") == ["senior-devops-engineer-berlin-123"]
    with write_session() as s:
        jobs = {j.external_id: j for j in s.query(Job).all()}
        jobs["4000001"].is_saved = True
        jobs["4000003"].is_hidden = True
        s.add(Application(job_id=jobs["1900002"].id, company="Initech", title="DevSecOps", status="Applied"))
    assert find("saved=1") == ["4000001"]
    assert "4000003" not in find("")
    assert find("hidden_only=1") == ["4000003"]
    assert "1900002" not in find("unapplied=1") and "1900002" in find("")


def test_keyword_search_escapes_wildcards(synced):
    assert find("q=%25") == []
    assert find("q=_") == []
    assert "aaaa-1111" in find("q=openshift")


def test_sorting_and_pagination(synced):
    f = JobFilters.from_params(QueryParams("sort=company"))
    with read_session() as s:
        companies = [j.company.lower() for j in search_jobs(s, f, per_page=50).items]
    assert companies == sorted(companies)
    f = JobFilters.from_params(QueryParams("sort=match"))
    with read_session() as s:
        scores = [j.match_score for j in search_jobs(s, f, per_page=50).items]
    assert scores == sorted(scores, reverse=True)
    f = JobFilters.from_params(QueryParams("sort=salary"))
    with read_session() as s:
        first = search_jobs(s, f, per_page=50).items[0]
    assert first.salary_annual_max is not None
    f = JobFilters.from_params(QueryParams("page=2"))
    with read_session() as s:
        page = search_jobs(s, f, per_page=4)
    assert page.page == 2 and len(page.items) == 4 and page.pages == 3


def test_invalid_params_ignored_and_roundtrip():
    f = JobFilters.from_params(QueryParams("countries=XX,in&work_mode=moon&sort=evil&page=-3&min_score=500&source=1;drop"))
    assert f.countries == ["IN"] and f.work_mode == [] and f.sort == "match" and f.page == 1
    assert f.min_score == 100 and f.source == []
    again = JobFilters.from_params(QueryParams(f.query_string()))
    assert again == f
