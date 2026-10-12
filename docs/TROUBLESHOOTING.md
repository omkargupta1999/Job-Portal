# JobHub troubleshooting

Start with these four commands. Most problems show up in one of them.

```bash
docker compose ps                                   # is it running / healthy?
docker compose logs --tail=200 jobhub               # startup errors, sync errors
curl -s http://127.0.0.1:8080/health                # {"status":"ok", ...}
docker compose exec jobhub python -m app.cli check-config
```

## The container exits immediately

| Log message | Cause | Fix |
|---|---|---|
| `Configuration error: JOBHUB_… must be …` | A value in `.env` is invalid. | Correct it; the valid ranges are listed in `.env.example`. Then run `docker compose up -d`. `docker compose restart` keeps the old environment, and `docker logs` still shows lines from earlier failed starts. |
| `JOBHUB_AUTH_PASSWORD must be at least 12 characters, but JobHub received N` | The login password is too short, **or** Docker Compose shortened it. In `.env`, an unquoted or double-quoted `$name` is expanded as a variable (usually to nothing) and ` #` starts a comment. | Use 12+ characters and wrap the value in single quotes: `JOBHUB_AUTH_PASSWORD='my$ecret-pass-2026'`. To run without a login (fine on `127.0.0.1`), leave both `JOBHUB_AUTH_USER` and `JOBHUB_AUTH_PASSWORD` empty. Then run `docker compose up -d`. |
| `Database schema version N is newer than this JobHub build` | You downgraded the image. | Run the newer image again, or restore a backup taken with the older version. |
| `PermissionError: … /data` | The volume was created by another user ID. | Run `docker run --rm --user 0 -v jobhub-data:/data --entrypoint chown jobhub:latest -R 10001:10001 /data`. |
| `failed to bind port` / `address already in use` | Another service is using 8080. | Change `JOBHUB_PORT` in `.env`, then run `docker compose up -d`. |

## `http://127.0.0.1:8080` does not load

- Run `docker compose ps`. The state should be `running (healthy)`. While it is still `starting`, wait about 20 seconds.
- **Accessing from another machine:** this is blocked by design, because the port is bound to `127.0.0.1`. Read README → *Exposing JobHub* first.
- **"Invalid host header" (HTTP 400):** the host name you used is not in `JOBHUB_ALLOWED_HOSTS`.
- **HTTP 401:** Basic auth is enabled. The credentials are `JOBHUB_AUTH_USER` and `JOBHUB_AUTH_PASSWORD` from `.env`.

## No jobs appear / a source shows "Error"

Open **Job Sources** to see each source's status, last success and last error. Every source fails independently.

| Error | Meaning | Fix |
|---|---|---|
| `HTTP 404 from boards-api.greenhouse.io` / `api.lever.co` | The board identifier does not exist or the company left that ATS. | Check the company's careers URL. Lever EU boards need the **Lever (EU)** source type. |
| `HTTP 429` | The provider is rate-limiting you. | JobHub has already backed off. Increase the sync interval. |
| `Request timed out after 20s` | The network is slow or the API is unreachable. | Raise `JOBHUB_REQUEST_TIMEOUT_SECONDS`, or check the firewall and proxy. |
| `Network error: ConnectError` / `DNS resolution failed` | There is no outbound internet access. | Set `HTTPS_PROXY` in `.env`, or allow egress to the hosts listed in README → Job sources. |
| `resolves to non-public address` | DNS returned a private IP, which the SSRF guard blocks. | Fix DNS. If you must use a proxy, set `HTTPS_PROXY` so the proxy resolves names instead. |
| `CERTIFICATE_VERIFY_FAILED` | A TLS-intercepting proxy is in the path. | Put the proxy CA into the volume (e.g. `/data/corp-ca.pem`) and set `SSL_CERT_FILE=/data/corp-ca.pem`. |
| `Unexpected … response` | The provider changed its API format. | Disable the source and report or fix the adapter in `app/sources/`. |
| `Needs setup` (Adzuna or LinkedIn job-alert emails) | The source needs a key or a mailbox. | Follow the steps shown on **Job Sources**, then run `docker compose up -d`. |
| `Adzuna rejected the API key (HTTP 401/403)` | Wrong or inactive App ID/Key. | Copy both values again from developer.adzuna.com into `.env`, then run `docker compose up -d`. |
| `IMAP login failed` | Gmail and most providers reject your normal password over IMAP. | Enable 2-Step Verification, create an **App Password**, put it in `JOBHUB_IMAP_PASSWORD` (single-quoted), and check that IMAP is enabled in Gmail settings. |
| LinkedIn alerts imported 0 jobs | No alert emails within `JOBHUB_IMAP_DAYS`, a different sender address, or a changed email layout. | Check the alerts arrive from `jobalerts-noreply@linkedin.com` (or set `JOBHUB_IMAP_SENDERS`). The parser is experimental; if the layout changed, use the Save to JobHub bookmarklet meanwhile. |
| `Career page not found (HTTP 404) - disabled automatically` | The company no longer uses that platform, or the identifier is wrong. | Use **Find any company's career page** to look it up again, or remove the source. |
| Few or no jobs under **Relevant to me** | Relevance follows your profile's target titles and skills. | Check **Resume & Profile** → target titles and skills, add career pages or an Adzuna key, or switch Discover to **All jobs**. |
| `skipped to respect the source's rate limit` | This is not an error. Remotive is fetched at most every 6 hours; other sources at most every 30 minutes. | Wait. From the CLI, `python -m app.cli sync --force` ignores these intervals. Use it sparingly. |

If sync succeeds but the dashboard shows few matches, check the **Profile**. Preferred countries strongly affect scores: by default, jobs outside your countries are capped at 45. You can turn that cap off in **Settings**.

## "Fetch Jobs Now" does nothing / stays disabled

- Another sync is already running; overlapping runs are refused by design. Progress appears in the bar under the top bar.
- A sync left in the `running` state by a crash is marked `interrupted` on the next start.
- Check the browser console. JavaScript is optional, and without it the button submits a normal form.

## `database is locked`

JobHub serialises its own writes, so this should not occur during normal use. If it does:

1. Make sure only **one** JobHub container or process uses the volume. Run `docker ps | grep jobhub`.
2. Do not open `jobhub.db` with another tool while JobHub is writing. Use `python -m app.cli backup` and inspect the copy instead.
3. Do not put the data directory on a network file system (NFS, SMB). SQLite locking is unreliable there.

## Resume upload fails

- Only PDF, DOCX and TXT files up to `JOBHUB_MAX_UPLOAD_MB` are accepted, and the content must match the extension.
- "No text could be extracted" means the PDF is a scanned image or is encrypted. JobHub does not do OCR, so enter the details manually or upload a DOCX/TXT export.

## The scheduler is "inactive"

- `JOBHUB_SCHEDULER_ENABLED=false` is set, or
- another process holds `/data/.scheduler.lock`, for example a second container on the same volume. Stop the duplicate.

## Backup and restore problems

- `JobHub appears to be running. Stop it first`: run `docker compose stop jobhub` before restoring.
- `Integrity check failed` / `is not a JobHub database`: the file is corrupt or is not a JobHub backup. Nothing was changed.
- Every restore first writes `jobhub-…-pre-restore.db`, so you can always go back.

## Resetting everything (destructive)

This permanently deletes all jobs, applications, resumes and backups:

```bash
scripts/backup.sh               # keep a copy first
docker compose down
docker volume rm jobhub-data
docker compose up -d
```

## Getting debug output

Set `JOBHUB_LOG_LEVEL=DEBUG` in `.env` and run `docker compose up -d`. Debug logs include access logs and each retry attempt. Logs are rotated at 3 × 10 MB.
