"""Template-based application material (works fully offline).

Only facts stored in the profile and the job posting are used. Anything the
generator cannot know is left as a clearly marked [placeholder] for the user
to complete. Output is always labelled as a draft that needs review.
"""

from __future__ import annotations

from app.geo import country_name

DRAFT_NOTICE = "DRAFT - generated from your profile and the job posting. Review and edit before sending."


def _first_sentence(text: str) -> str:
    text = (text or "").strip().replace("\n", " ")
    if not text:
        return ""
    end = text.find(". ")
    return text if end == -1 else text[: end + 1]


def _join(items: list[str]) -> str:
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def generate(profile, job) -> dict[str, str]:
    details = job.match_details or {}
    matched = list(details.get("matched_skills") or [])
    matched_pref = list(details.get("matched_preferred") or [])
    missing = list(details.get("missing_required") or [])
    certs_relevant = list(details.get("certifications_matched") or [])
    company = job.company or "[Company]"
    name = profile.full_name or "[Your name]"
    years = profile.years_experience
    headline = profile.headline or (profile.past_titles[0] if profile.past_titles else "")
    years_phrase = f"{years:g}+ years of" if years else "[X] years of"

    location_bits = [b for b in (job.city, country_name(job.country_code)) if b]
    location_phrase = f" in {', '.join(location_bits)}" if location_bits and job.work_mode != "remote" else ""
    if job.work_mode == "remote":
        location_phrase = " (remote)"

    # ---------------------------------------------------------------- summary
    top = (matched + matched_pref)[:6]
    summary_parts = []
    if headline:
        summary_parts.append(f"{headline} with {years_phrase} experience.")
    first = _first_sentence(profile.summary)
    if first and first not in summary_parts[0:1]:
        summary_parts.append(first)
    if top:
        summary_parts.append(f"Hands-on with {_join(top)}, which this {job.title} role calls for.")
    summary = " ".join(summary_parts) or "[Write a short professional summary]"

    # ---------------------------------------------------------------- highlights
    highlights_lines = [f"- {s}" for s in matched]
    highlights_lines += [f"- {s} (listed as preferred)" for s in matched_pref]
    highlights_lines += [f"- Certification: {c}" for c in certs_relevant]
    if profile.experience_notes:
        highlights_lines.append(f"- Background: {profile.experience_notes.strip()}")
    highlights = "\n".join(highlights_lines) or "- [No overlapping skills were detected; add your own highlights]"

    # ---------------------------------------------------------------- cover letter
    paragraphs = [f"Dear Hiring Team at {company},"]
    intro = f"I am applying for the {job.title} position{location_phrase}."
    if headline:
        intro += f" I am a {headline} with {years_phrase} experience."
    paragraphs.append(intro)
    if matched:
        paragraphs.append(
            f"Your posting highlights {_join(matched[:6])}. These are part of my day-to-day work. "
            "[Add one concrete example of a result you achieved with them.]"
        )
    if profile.summary:
        paragraphs.append(profile.summary.strip().split("\n\n")[0])
    if profile.experience_notes:
        paragraphs.append(f"Relevant background: {profile.experience_notes.strip()}")
    paragraphs.append(
        f"[Add a sentence on why {company} and this role interest you specifically.] "
        f"I would welcome the opportunity to discuss how I can contribute to your team."
    )
    paragraphs.append(f"Kind regards,\n{name}")
    contact = " · ".join(x for x in (profile.email, profile.phone) if x)
    if contact:
        paragraphs[-1] += f"\n{contact}"
    cover_letter = "\n\n".join(paragraphs)

    # ---------------------------------------------------------------- answers
    qa: list[tuple[str, str]] = []
    qa.append((
        f"Why are you interested in this role at {company}?",
        (f"The role focuses on {_join(top[:4])}, which matches my experience. " if top else "")
        + f"[Add what attracts you to {company}.]",
    ))
    if years:
        qa.append(("How many years of relevant experience do you have?", f"{years:g} years."))
    for skill in (matched[:3]):
        qa.append((f"Describe your experience with {skill}.",
                   f"{skill} is part of my core skill set. [Add years and a specific project.]"))
    if profile.notice_period:
        qa.append(("What is your notice period?", profile.notice_period))
    if profile.salary_min:
        qa.append(("What are your salary expectations?",
                   f"From {profile.salary_min:,} {profile.salary_currency or ''} per year, negotiable.".replace("  ", " ")))
    if profile.work_authorization:
        qa.append(("Are you authorised to work in this location?", profile.work_authorization))
    if profile.open_to_relocation is not None and job.work_mode != "remote" and job.city:
        qa.append((f"Are you willing to relocate to {job.city}?",
                   "Yes." if profile.open_to_relocation else "I am not looking to relocate."))
    answers = "\n\n".join(f"Q: {q}\nA: {a}" for q, a in qa)

    # ---------------------------------------------------------------- notes
    notes_lines = [f"Match score: {job.match_score}/100."]
    if missing:
        notes_lines.append("Required skills not found in your profile: " + ", ".join(missing) +
                           ". Do not claim these unless you have them; address honestly if asked.")
    for gap in details.get("gaps") or []:
        if not gap.startswith("Missing required skills"):
            notes_lines.append(gap)
    if job.apply_url:
        notes_lines.append(f"Apply at: {job.apply_url}")
    notes = "\n".join(notes_lines)

    return {"cover_letter": cover_letter, "summary": summary, "highlights": highlights, "answers": answers,
            "notes": notes}
