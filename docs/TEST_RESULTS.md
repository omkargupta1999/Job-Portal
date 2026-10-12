# Test results

Recorded on 2026-10-12 in the build environment (Linux, Python 3.13.16 locally; Python 3.12-slim in the Docker test image; SQLite 3.45).

## Automated tests: 137 passed, 0 failed

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
