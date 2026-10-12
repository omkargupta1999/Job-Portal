"""Deterministic, explainable job matching.

Each job gets six component scores (0-100). The overall score is the
weighted average using user-configurable weights stored in the database.
Every number is derived from data present in the job record and the saved
profile; when the job does not state something the component falls back to a
documented neutral value and the explanation says so.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.geo import country_name
from app.skills import CERTIFICATIONS, canonicalize_list, expand_with_implications

DEFAULT_WEIGHTS = {"skills": 35, "title": 25, "experience": 15, "location": 15, "work_mode": 5, "employment": 5}
COMPONENT_LABELS = {
    "skills": "Skills",
    "title": "Job title",
    "experience": "Experience",
    "location": "Location",
    "work_mode": "Work preference",
    "employment": "Employment type",
}
NEUTRAL = {"skills": 50, "experience": 70, "location": 60, "work_mode": 60, "employment": 70}
HIGH_MATCH = 75
GOOD_MATCH = 60
SCORING_VERSION = 1

ROLE_FAMILIES: dict[str, list[str]] = {
    "devops": [r"devops", r"dev ops"],
    "devsecops": [r"devsecops", r"dev sec ops", r"security automation"],
    "sre": [r"site reliability", r"\bsre\b", r"reliability engineer", r"production engineer"],
    "platform": [r"platform engineer", r"platform engineering", r"\bplatform\b"],
    "cloud": [r"cloud engineer", r"cloud infrastructure", r"cloud operations", r"cloud ops", r"cloud platform",
              r"cloud architect", r"\bcloud\b"],
    "infrastructure": [r"infrastructure", r"\binfra\b"],
    "kubernetes": [r"kubernetes", r"openshift", r"\bk8s\b", r"container platform"],
    "cicd": [r"ci/cd", r"\bcicd\b", r"build (and|&) release", r"release engineer", r"build engineer",
             r"pipeline engineer"],
    "automation": [r"automation engineer", r"infrastructure automation"],
    "systems": [r"systems engineer", r"linux engineer", r"system administrator", r"sysadmin", r"linux administrator"],
}
_RELATED = {"devops", "devsecops", "sre", "platform", "cloud", "infrastructure", "kubernetes", "cicd", "automation"}
_NON_ENGINEERING = re.compile(
    r"\b(recruiter|recruiting|sales|account executive|account manager|marketing|talent|sourcer|"
    r"customer success|business development|copywriter|designer|accountant|paralegal)\b",
    re.I,
)
_SENIORITY_WORDS = re.compile(
    r"\b(senior|sr|junior|jr|lead|staff|principal|head|chief|associate|mid|level|i{1,3}|iv|[1-5])\b", re.I
)
_GENDER_TAGS = re.compile(r"\((?:m|f|w|d|x|all genders?)(?:\s*/\s*(?:m|f|w|d|x|div))*\)|\b[mfw]/[mfw]/[dx]\b", re.I)
_STOP = {"engineer", "and", "the", "of", "for", "with", "remote", "hybrid", "a", "an", "to", "in", "at", "team"}

_LEVEL_YEARS = {"intern": 0, "junior": 0, "mid": 2, "senior": 5, "lead": 7, "principal": 8}


@dataclass
class ProfileSnapshot:
    skills: list[str] = field(default_factory=list)
    target_titles: list[str] = field(default_factory=list)
    past_titles: list[str] = field(default_factory=list)
    years_experience: float | None = None
    certifications: list[str] = field(default_factory=list)
    preferred_countries: list[str] = field(default_factory=list)
    preferred_cities: list[str] = field(default_factory=list)
    work_modes: list[str] = field(default_factory=list)
    remote_worldwide_ok: bool = True
    employment_types: list[str] = field(default_factory=list)

    @classmethod
    def from_model(cls, p) -> ProfileSnapshot:
        return cls(
            skills=list(p.skills or []),
            target_titles=list(p.target_titles or []),
            past_titles=list(p.past_titles or []),
            years_experience=p.years_experience,
            certifications=list(p.certifications or []),
            preferred_countries=[c.upper() for c in (p.preferred_countries or [])],
            preferred_cities=list(p.preferred_cities or []),
            work_modes=list(p.work_modes or []),
            remote_worldwide_ok=bool(p.remote_worldwide_ok),
            employment_types=list(p.employment_types or []),
        )

    def skill_set(self) -> set[str]:
        base = canonicalize_list(self.skills + self.certifications)
        return {s.lower() for s in expand_with_implications(base)}


@dataclass
class JobFacts:
    title: str
    skills: list[str]
    preferred_skills: list[str]
    years_min: int | None
    years_max: int | None
    experience_level: str
    country_code: str | None
    countries: list[str]
    city: str
    region: str
    work_mode: str
    location_type: str
    remote_scope: str
    employment_type: str

    @classmethod
    def from_model(cls, j) -> JobFacts:
        return cls(
            title=j.title or "",
            skills=list(j.skills or []),
            preferred_skills=list(j.preferred_skills or []),
            years_min=j.years_min,
            years_max=j.years_max,
            experience_level=j.experience_level or "",
            country_code=j.country_code,
            countries=[c.country_code for c in (j.countries or [])] or ([j.country_code] if j.country_code else []),
            city=j.city or "",
            region=j.region or "",
            work_mode=j.work_mode or "unknown",
            location_type=j.location_type or "unknown",
            remote_scope=j.remote_scope or "",
            employment_type=j.employment_type or "",
        )


def normalize_weights(raw: dict | None) -> dict[str, int]:
    weights = dict(DEFAULT_WEIGHTS)
    for key, value in (raw or {}).items():
        if key in weights:
            try:
                weights[key] = max(0, min(100, int(value)))
            except (TypeError, ValueError):
                pass
    if sum(weights.values()) == 0:
        weights = dict(DEFAULT_WEIGHTS)
    return weights


# ---------------------------------------------------------------- titles
def _core_title(title: str) -> str:
    t = _GENDER_TAGS.sub(" ", title or "")
    t = t.lower().replace("&", " and ")
    t = re.sub(r"[^a-z0-9/+ ]+", " ", t)
    t = _SENIORITY_WORDS.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


def _families(title: str) -> set[str]:
    t = (title or "").lower()
    return {fam for fam, patterns in ROLE_FAMILIES.items() if any(re.search(p, t) for p in patterns)}


def _tokens(title: str) -> set[str]:
    return {w for w in _core_title(title).replace("/", " ").split() if w not in _STOP}


def title_score(job_title: str, targets: list[str], past: list[str]) -> tuple[int, str]:
    if not targets and not past:
        return 50, "No target job titles in profile; title score neutral."
    core = _core_title(job_title)
    best, best_reason = 0, "Title is not related to your target roles."
    job_fams = _families(job_title)
    job_tokens = _tokens(job_title)
    for target in targets + past:
        tcore = _core_title(target)
        weight = 1.0 if target in targets else 0.85
        if tcore and (tcore == core or re.search(rf"\b{re.escape(tcore)}\b", core)):
            score, reason = 100, f"Title matches target role “{target}”."
        else:
            score, reason = 0, ""
            t_tokens = _tokens(target)
            if job_tokens and t_tokens:
                overlap = len(job_tokens & t_tokens) / len(job_tokens | t_tokens)
                if overlap > 0:
                    score, reason = int(30 + 60 * overlap), f"Title shares keywords with “{target}”."
            fams = _families(target)
            if job_fams & fams and score < 85:
                score, reason = 85, f"Same role family as “{target}”."
            elif job_fams & _RELATED and fams & _RELATED and score < 60:
                score, reason = 60, f"Adjacent role family to “{target}”."
        score = int(score * weight)
        if score > best:
            best, best_reason = score, reason
    if _NON_ENGINEERING.search(job_title or ""):
        best = int(best * 0.2)
        best_reason = "Title indicates a non-engineering role."
    return best, best_reason


# ---------------------------------------------------------------- skills
def skills_component(job: JobFacts, profile: ProfileSnapshot) -> tuple[int, dict]:
    have = profile.skill_set()
    required = job.skills
    preferred = job.preferred_skills
    matched_req = [s for s in required if s.lower() in have]
    missing_req = [s for s in required if s.lower() not in have]
    matched_pref = [s for s in preferred if s.lower() in have]
    missing_pref = [s for s in preferred if s.lower() not in have]
    if not required and not preferred:
        return NEUTRAL["skills"], {
            "matched": [], "missing_required": [], "matched_preferred": [], "missing_preferred": [],
            "detail": "No recognised technical skills in the posting; skills score is neutral.",
        }
    parts = []
    if required:
        parts.append((0.8 if preferred else 1.0, len(matched_req) / len(required)))
    if preferred:
        parts.append((0.2 if required else 1.0, len(matched_pref) / len(preferred)))
    score = int(round(100 * sum(w * r for w, r in parts)))
    detail = f"{len(matched_req)} of {len(required)} required skills matched"
    if preferred:
        detail += f"; {len(matched_pref)} of {len(preferred)} preferred"
    return score, {
        "matched": matched_req,
        "missing_required": missing_req,
        "matched_preferred": matched_pref,
        "missing_preferred": missing_pref,
        "detail": detail + ".",
    }


# ---------------------------------------------------------------- experience
def experience_score(job: JobFacts, profile: ProfileSnapshot) -> tuple[int, str, list[str]]:
    years = profile.years_experience
    gaps: list[str] = []
    if years is None:
        return NEUTRAL["experience"], "Add your years of experience to the profile to score this.", gaps
    if job.years_min is not None:
        lo, hi = job.years_min, job.years_max
        req = f"{lo}–{hi} years" if hi else f"{lo}+ years"
        if years >= lo:
            if hi is not None and years > hi + 3:
                return 75, f"Posting asks for {req}; you have {years:g}. You may be overqualified.", gaps
            return 100, f"Posting asks for {req}; you have {years:g}.", gaps
        gap = lo - years
        gaps.append(f"Posting asks for {req}; profile lists {years:g}.")
        return max(0, int(100 - gap * 25)), f"{gap:g} year(s) below the stated requirement ({req}).", gaps
    level = job.experience_level
    if level:
        expected = _LEVEL_YEARS.get(level, 0)
        if level in {"intern", "junior"} and years >= 4:
            gaps.append(f"Role appears to be {level}-level.")
            return 40, f"Title suggests a {level} role; you have {years:g} years.", gaps
        if years >= expected:
            return 100, f"Title suggests {level} level; your {years:g} years fit.", gaps
        gaps.append(f"Title suggests {level} level (typically {expected}+ years).")
        return max(30, int(100 - (expected - years) * 20)), f"Title suggests {level} level; you have {years:g} years.", gaps
    return NEUTRAL["experience"], "Experience requirement not stated; score is neutral.", gaps


# ---------------------------------------------------------------- location
def location_score(job: JobFacts, profile: ProfileSnapshot) -> tuple[int, str]:
    pref = set(profile.preferred_countries)
    cities = {c.lower() for c in profile.preferred_cities}
    job_countries = set(job.countries) | ({job.country_code} if job.country_code else set())
    if not pref:
        if job.location_type == "remote_worldwide":
            return 90, "Remote worldwide."
        return NEUTRAL["location"], "No preferred countries in profile; location score neutral."
    pref_names = ", ".join(country_name(c) for c in sorted(pref))
    lt = job.location_type
    if lt == "remote_worldwide":
        if profile.remote_worldwide_ok:
            return 90, "Remote worldwide - open to candidates in your countries."
        return 50, "Remote worldwide, but worldwide remote is disabled in your preferences."
    if lt == "remote_country":
        if job_countries & pref:
            return 100, f"Remote within {country_name(job.country_code)}."
        return 10, f"Remote restricted to {job.remote_scope or country_name(job.country_code)}, outside {pref_names}."
    if lt == "remote_region":
        return 45, f"Remote restricted to {job.remote_scope}; check eligibility from {pref_names}."
    if lt == "remote_unspecified":
        return 50, "Remote, but the eligible countries are not stated."
    if lt == "physical":
        if job_countries & pref:
            if job.city and job.city.lower() in cities:
                return 100, f"Located in {job.city}, a preferred city."
            if not job.city:
                return 80, f"Located in {country_name(job.country_code)} (city not stated)."
            if cities:
                return 65, f"In {country_name(job.country_code)}, but {job.city} is not a preferred city."
            return 85, f"Located in {country_name(job.country_code)}."
        return 5, f"Located in {country_name(job.country_code) or job.city}, outside {pref_names}."
    return 30, "Location not stated in the posting."


def work_mode_score(job: JobFacts, profile: ProfileSnapshot) -> tuple[int, str]:
    modes = {m.lower() for m in profile.work_modes}
    if not modes:
        return 100, "No work-mode preference set."
    if job.work_mode == "unknown":
        return NEUTRAL["work_mode"], "Work mode not stated."
    if job.work_mode in modes:
        return 100, f"{job.work_mode.capitalize()} matches your preference."
    return 10, f"{job.work_mode.capitalize()} is not one of your preferred work modes."


def employment_score(job: JobFacts, profile: ProfileSnapshot) -> tuple[int, str]:
    prefs = {e.lower() for e in profile.employment_types}
    if not prefs:
        return 100, "No employment-type preference set."
    if not job.employment_type:
        return NEUTRAL["employment"], "Employment type not stated."
    if job.employment_type in prefs:
        return 100, f"{job.employment_type.capitalize()} matches your preference."
    return 20, f"{job.employment_type.capitalize()} is not a preferred employment type."


# ---------------------------------------------------------------- overall
def score_job(job: JobFacts, profile: ProfileSnapshot, weights: dict | None = None,
              strict_location: bool = True) -> dict:
    w = normalize_weights(weights)
    s_skills, skill_info = skills_component(job, profile)
    s_title, title_reason = title_score(job.title, profile.target_titles, profile.past_titles)
    s_exp, exp_reason, gaps = experience_score(job, profile)
    s_loc, loc_reason = location_score(job, profile)
    s_mode, mode_reason = work_mode_score(job, profile)
    s_emp, emp_reason = employment_score(job, profile)

    components = {
        "skills": {"score": s_skills, "detail": skill_info["detail"]},
        "title": {"score": s_title, "detail": title_reason},
        "experience": {"score": s_exp, "detail": exp_reason},
        "location": {"score": s_loc, "detail": loc_reason},
        "work_mode": {"score": s_mode, "detail": mode_reason},
        "employment": {"score": s_emp, "detail": emp_reason},
    }
    total_w = sum(w.values())
    overall = sum(components[k]["score"] * w[k] for k in components) / total_w
    for k in components:
        components[k]["weight"] = w[k]
        components[k]["label"] = COMPONENT_LABELS[k]

    capped = False
    if strict_location and s_loc <= 15 and overall > 45:
        overall, capped = 45, True

    profile_certs = {c.lower() for c in canonicalize_list(profile.certifications)}
    cert_mentions = [s for s in job.skills + job.preferred_skills if s in CERTIFICATIONS]
    cert_matched = [c for c in cert_mentions if c.lower() in profile_certs]
    cert_missing = [c for c in cert_mentions if c.lower() not in profile_certs]

    if skill_info["missing_required"]:
        gaps.append("Missing required skills: " + ", ".join(skill_info["missing_required"][:8]) + ".")
    for cert in cert_missing:
        gaps.append(f"Posting mentions {cert}, which is not in your certifications.")

    reasons: list[str] = []
    if skill_info["matched"]:
        reasons.append("Matches " + ", ".join(skill_info["matched"][:6]) + ".")
    if s_title >= 60:
        reasons.append(title_reason)
    if s_loc >= 80:
        reasons.append(loc_reason)
    if s_exp == 100:
        reasons.append(exp_reason)
    if cert_matched:
        reasons.append("Your certifications are relevant: " + ", ".join(cert_matched) + ".")
    if capped:
        reasons.append("Score capped at 45 because the location is outside your preferences.")

    return {
        "score": int(round(overall)),
        "components": components,
        "matched_skills": skill_info["matched"],
        "missing_required": skill_info["missing_required"],
        "matched_preferred": skill_info["matched_preferred"],
        "missing_preferred": skill_info["missing_preferred"],
        "certifications_matched": cert_matched,
        "gaps": gaps,
        "reasons": reasons,
        "location_capped": capped,
        "version": SCORING_VERSION,
    }


def score_band(score: int) -> str:
    if score >= HIGH_MATCH:
        return "high"
    if score >= GOOD_MATCH:
        return "good"
    if score >= 40:
        return "fair"
    return "low"
