# Test results

## v1.1.0 (2026-10-12): 171 passed, 0 failed

| Module | Tests | New in v1.1 |
|---|---:|---|
| `tests/test_config.py` | 16 | Actionable auth-password error that never echoes the secret |
| `tests/test_db.py` | 12 | Upgrade of a v3 (v1.0) database with jobs to v4; relevance re-score runs once; backups are single files |
| `tests/test_directory.py` | 5 | Directory validity, identifier variants (keeps “ServiceNow”, “GrafanaLabs”), careers-URL parsing, probe needs a real board, finder probes every ATS |
| `tests/test_geo.py` | 18 | — |
| `tests/test_matching.py` | 12 | Relocation-aware location scores; role-based relevance independent of location |
| `tests/test_new_sources.py` | 17 | Adzuna (profile queries, predicted salary ignored, bad key), Jobicy, Remote OK, Ashby, SmartRecruiters (details only for matching titles), Workable, LinkedIn alert emails (plain-text and HTML; tokens never kept), auto-disable on 404, age-based expiry, manual source never fetched |
| `tests/test_search.py` | 14 | “Relevant to me” is the default scope |
| `tests/test_sources.py` | 15 | — |
| `tests/test_sync.py` | 15 | Sources needing setup are reported as “setup”, not errors |
| `tests/test_text_prepare.py` | 18 | — |
| `tests/test_web.py` | 29 | Add-job from a LinkedIn URL (canonical URL, scoring, tracking, de-duplication), validation, bookmarklet, relevant/all Discover, Sources page, directory and careers-URL adding, company finder |

```
$ python3 -m pytest -o addopts="" -p no:warnings -q          # local, Python 3.13
171 passed in 13.19s
$ docker run --rm jobhub:test python -m pytest -o addopts="" -q -p no:warnings   # image, Python 3.12
171 passed in 14.43s
$ ruff check app tests
All checks passed!
```

Manual verification for v1.1 (build environment):

- **Upgrade path.** A database created by the v1.0 code (commit 242d5bf, schema v3, 11 jobs) was copied into the `jobhub-data` volume. The v1.1 image was started on it with `docker compose up -d`. Observed:
  - The container reached `healthy`.
  - The log shows `Backed up database before migration … pre-migration-v3.db` and `Applying migration 4`.
  - The 5 new sources were registered.
  - `Scoring rules updated to v2; re-scored 11 jobs`, with 9 of 11 jobs flagged relevant.
  - All pages returned HTTP 200.
- **Bookmarklet.** In headless Chromium, the bookmarklet code from `/jobs/new` was run on a stand-in page titled “(2) Senior Platform Engineer | Globex Cloud | LinkedIn” with text selected. It opened JobHub with title “Senior Platform Engineer”, company “Globex Cloud” and the selected description. It was **not** tested on linkedin.com itself (not reachable from the build environment).
- **Layout.** Pages were checked at 1440 px and 390 px for horizontal overflow; none was found. No console errors in light or dark themes.

Not verified in v1.1: live calls to Adzuna, Jobicy, Remote OK, Ashby, SmartRecruiters and Workable, and real LinkedIn alert emails. The fixtures and synthetic emails follow the documented or secondary-source formats described in README → Job sources.

---

Recorded on 2026-10-12 in the build environment (Linux, Python 3.13.16 locally; Python 3.12-slim in the Docker test image; SQLite 3.45).

## v1.0.0: 137 passed, 0 failed

| Module | Tests | What it covers |
|---|---:|---|
| `tests/test_config.py` | 15 | Defaults, 12 classes of invalid configuration that are rejected, list and boolean parsing, `.env` that never overrides the environment or rewrites the file |
| `tests/test_db.py` | 11 | Schema and seed, WAL/foreign-key/busy-timeout pragmas, idempotent initialisation, persistence across engine restarts, 8 concurrent writer threads with no lock errors, an external writer holding the lock, rollback, a v1 → v3 migration with automatic backup, refusing a newer schema, 4 concurrent initialisations, backup and restore, rejecting a foreign database |
| `tests/test_geo.py` | 18 | All 249 ISO countries, 14 location-parsing cases (physical, country-, region- and worldwide-remote, inferred, ambiguous), never inventing a country, structured hints, multi-location postings |
| `tests/test_matching.py` | 10 | Alias normalisation without double counting, required vs. preferred skills, explanations, gaps, skill implications, similar titles, location cap, configurable weights, neutral handling of unknowns, junior and overqualified roles |
| `tests/test_search.py` | 13 | Country (single and multiple, plus worldwide), multi-location, remote worldwide, remote restricted to a country, work mode, city and region, combined filters, salary and currency, experience, visa, saved, hidden and unapplied, wildcard escaping, all sorts, pagination, invalid parameters |
| `tests/test_sources.py` | 15 | Arbeitnow pagination and normalisation, Remotive, Greenhouse (HTML unescaping), Lever (pagination, salary, unsafe apply URL), identifier validation, retry with backoff, bounded retries, `Retry-After` cap, timeouts, no retry on 4xx, malformed payloads, SSRF (scheme, private IP, credentials, redirect, DNS), DNS retry |
| `tests/test_sync.py` | 15 | Full sync with scoring, unchanged vs. updated listings, no duplicates within a source, duplicates across sources skipped, expiry with application history preserved, revival, no expiry on partial fetch, failing sources isolated, contained adapter bugs, source rate-limit interval, overlapping runs refused, background thread, interrupted-run recovery, purge keeps saved and applied jobs, single scheduler instance, next-run calculation |
| `tests/test_text_prepare.py` | 18 | Application URL validation (12 cases), HTML sanitiser, text helpers, cover letters using only profile facts, placeholders, answers, scheduler-driven sync at startup |
| `tests/test_web.py` | 22 | All pages render, health check, security headers, host check, CSRF, sync from the HTTP API, filters over HTTP, detail page explanations and sanitisation, save and hide, apply redirect without marking as applied, invalid-URL fallback, single tracking record with history, tracker CRUD/filters/CSV (formula injection), records kept after expiry, preparation and downloads, profile save and re-scoring, resume upload (TXT/DOCX) and merge, upload validation (8 rejection cases), sources management, per-source refresh, alerts, settings validation and backup, Basic auth |

Commands and output:

```
$ python3 -m pytest -o addopts="" -p no:warnings -q
137 passed in 10.41s

$ docker run --rm jobhub:test python -m pytest -o addopts="" -q -p no:warnings
136 passed in 10.96s        # run before the DNS-retry test was added; 137 locally afterwards

$ ruff check app tests
All checks passed!

$ python3 -m compileall -q app tests   # OK
```

All external job feeds are mocked with `httpx.MockTransport`. No test touches the network.

## Docker verification (manual, in the build environment)

- `docker build --target runtime` and `--target test` succeeded. The runtime image is 291 MB on disk (70 MB compressed). The base image was pulled from `mirror.gcr.io` because Docker Hub returned HTTP 429.
- `docker compose config` was valid. After `docker compose up -d`, the container reached the `healthy` state within about 8 seconds.
- The container runs as uid 10001 with a read-only root filesystem (writing to `/app` fails as expected). The port is published only on `127.0.0.1:8080`.
- The startup sync ran automatically. Both public sources failed with `DNS resolution failed (temporary)` because the sandbox container has no internet access. The failures were recorded per source and the app stayed healthy.
- A cross-origin POST was rejected with 403. A same-origin application create returned 303.
- Data survived `docker compose restart` and `docker compose down` followed by `up`.
- `app.cli backup` created a backup. `scripts/restore.sh` restored it, wrote a pre-restore safety copy, and the data matched the backup. A restore attempted while the app was running was refused (exit 1).

## UI verification

Pages were rendered in headless Chromium at 1440 px and 390 px, in light and dark themes, using data loaded through the mocked feeds. No console errors were reported. Screenshots are in `docs/screenshots/`.

## Not verified

- **Live job APIs.** Arbeitnow, Remotive, Greenhouse and Lever were blocked by the build sandbox's network policy (HTTP 403 from the egress proxy). The adapters follow the documented contracts (direct evidence for Lever and Remotive from their official READMEs; indirect evidence for Greenhouse and Arbeitnow) and are tested against fixtures with the same shape. Validate on first real use via the **Job Sources** page.
