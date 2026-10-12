from app.matching import JobFacts, ProfileSnapshot, score_job, title_score
from app.skills import canonicalize, extract_skills, split_required_preferred


def profile(**kw) -> ProfileSnapshot:
    base = dict(
        skills=["GitLab CI/CD", "Jenkins", "Kubernetes", "Red Hat OpenShift", "Terraform", "Helm", "Python", "Linux"],
        target_titles=["Senior DevOps Engineer", "Site Reliability Engineer", "Platform Engineer"],
        past_titles=["DevOps Engineer"],
        years_experience=6,
        certifications=[],
        preferred_countries=["IN"],
        preferred_cities=["Pune", "Mumbai"],
        work_modes=["remote", "hybrid", "onsite"],
        remote_worldwide_ok=True,
        employment_types=["full-time"],
    )
    base.update(kw)
    return ProfileSnapshot(**base)


def job(**kw) -> JobFacts:
    base = dict(title="Senior DevOps Engineer", skills=["Kubernetes", "Terraform", "Jenkins"], preferred_skills=[],
                years_min=5, years_max=None, experience_level="senior", country_code="IN", countries=["IN"], city="Pune",
                region="Maharashtra", work_mode="hybrid", location_type="physical", remote_scope="",
                employment_type="full-time")
    base.update(kw)
    return JobFacts(**base)


def test_aliases_are_normalised_and_not_double_counted():
    assert canonicalize("k8s") == "Kubernetes"
    assert canonicalize("GitLab pipelines") == "GitLab CI/CD"
    assert canonicalize("continuous delivery") == "CI/CD"
    assert canonicalize("OpenShift") == "Red Hat OpenShift"
    skills = extract_skills("We run K8s. Kubernetes experience required. kubernetes!")
    assert skills.count("Kubernetes") == 1


def test_required_vs_preferred_split():
    text = "Requirements:\n- Kubernetes\n- Terraform\nNice to have:\n- Istio\n- Kubernetes"
    req, pref = split_required_preferred(text)
    assert set(req) == {"Kubernetes", "Terraform"}
    assert pref == ["Istio"]


def test_perfect_match_scores_high_with_explanations():
    result = score_job(job(), profile())
    assert result["score"] >= 90
    assert set(result["matched_skills"]) == {"Kubernetes", "Terraform", "Jenkins"}
    assert result["missing_required"] == []
    assert any("Pune" in r for r in result["reasons"])
    for comp in result["components"].values():
        assert 0 <= comp["score"] <= 100 and comp["detail"]


def test_missing_skills_and_gaps_are_reported():
    result = score_job(job(skills=["Kubernetes", "Go", "Rust"], years_min=10), profile())
    assert result["missing_required"] == ["Go", "Rust"]
    assert any("10+ years" in g for g in result["gaps"])
    assert result["components"]["skills"]["score"] == 33


def test_implications_give_credit_without_job_side_inflation():
    # Candidate with GitLab CI/CD gets credit for "CI/CD" and "GitLab".
    result = score_job(job(skills=["CI/CD", "GitLab"]), profile())
    assert result["components"]["skills"]["score"] == 100
    # Candidate with only generic CI/CD does not get credit for GitLab CI/CD.
    result = score_job(job(skills=["GitLab CI/CD"]), profile(skills=["CI/CD"]))
    assert result["missing_required"] == ["GitLab CI/CD"]


def test_similar_titles_are_not_rejected():
    score, _ = title_score("Platform Reliability Engineer", ["Site Reliability Engineer"], [])
    assert score >= 60
    score, _ = title_score("Cloud Infrastructure Engineer II", ["DevOps Engineer"], [])
    assert score >= 60
    score, reason = title_score("DevOps Recruiter", ["DevOps Engineer"], [])
    assert score < 30 and "non-engineering" in reason


def test_location_preferences():
    outside = score_job(job(country_code="US", countries=["US"], city="Austin"), profile())
    assert outside["components"]["location"]["score"] <= 15
    assert outside["score"] <= 45 and outside["location_capped"]
    worldwide = score_job(job(country_code=None, countries=[], city="", location_type="remote_worldwide",
                              work_mode="remote"), profile())
    assert worldwide["components"]["location"]["score"] == 90
    remote_us = score_job(job(country_code="US", countries=["US"], city="", location_type="remote_country",
                              work_mode="remote"), profile())
    assert remote_us["components"]["location"]["score"] <= 15


def test_weights_change_the_score_deterministically():
    j, p = job(skills=["Kubernetes", "Go"]), profile()
    a = score_job(j, p, {"skills": 100, "title": 0, "experience": 0, "location": 0, "work_mode": 0, "employment": 0})
    b = score_job(j, p, {"skills": 0, "title": 100, "experience": 0, "location": 0, "work_mode": 0, "employment": 0})
    assert a["score"] == 50 and b["score"] == 100
    assert score_job(j, p) == score_job(j, p)


def test_unknown_information_is_neutral_not_fabricated():
    result = score_job(job(skills=[], years_min=None, experience_level="", employment_type="", work_mode="unknown",
                           country_code=None, countries=[], city="", location_type="unknown"), profile())
    comps = result["components"]
    assert comps["skills"]["score"] == 50 and "No recognised" in comps["skills"]["detail"]
    assert "not stated" in comps["experience"]["detail"]
    assert comps["location"]["score"] == 30
    assert result["matched_skills"] == []


def test_overqualified_and_junior_roles():
    assert score_job(job(title="Junior DevOps Engineer", years_min=None, experience_level="junior"),
                     profile())["components"]["experience"]["score"] == 40
    assert score_job(job(years_min=1, years_max=2), profile())["components"]["experience"]["score"] == 75
