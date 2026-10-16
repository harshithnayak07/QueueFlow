# QueueFlow AI — AI-Powered Virtual Queue Management (MVP)

Responsive web app: recorded image/video crowd estimate (OpenCV + YOLOv8n) → owner-reviewed wait estimate → owner-controlled virtual token queue via link/QR → customer browser tracking page. No app install, no paid APIs, free/local-first.

> Full spec: `docs/project-context.md` · Agent memory: `AGENTS.md` · Progress: `docs/session-state.md`

## What it does

1. Owner signs up / logs in (email + password, or Google Sign-In).
2. Owner creates a queue session, uploads a queue **image or recorded video**.
3. YOLOv8n estimates people → owner **reviews and confirms** the count (AI recommends, owner decides).
4. Owner activates the virtual queue → customers join at `join.html?session=N` (or scan the QR) → get a token.
5. Tokens **auto-advance**: each serving token completes after the configured average service time; customers track live status, ahead-count, wait, and return time.
6. One phone number = one token per session; called tokens expire per the validity window.

## Setup (local)

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env        # edit SECRET_KEY for anything beyond local dev
.\venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Open `http://127.0.0.1:8000/` (signup), `/login.html`, `/dashboard.html`.
First run downloads `yolov8n.pt` (~6 MB, gitignored) automatically.

Run tests: `.\venv\Scripts\python.exe -m pytest -q` (127 passed, 1 skipped).

## Configuration (`.env`)

| Key | Default | Notes |
|---|---|---|
| `SECRET_KEY` | `change-me-in-env` | **Must be random ≥32 chars in prod** — server refuses to boot with `ENV=prod` otherwise |
| `ENV` | `dev` | `prod` enforces secret + CORS allowlist |
| `CORS_ORIGINS` | local origins / `*` in dev | Exact deployed origin(s) in prod |
| `DATABASE_URL` | `sqlite:///./queueflow.db` | Any SQLAlchemy URL (e.g. PostgreSQL) for prod |
| `GOOGLE_CLIENT_ID` | shipped default | Public identifier; add your origin to its Authorized JavaScript origins in Google Cloud Console |
| `UPLOAD_DIR` / `MAX_VIDEO_MB` / `MAX_IMAGE_MB` | `data/uploads` / 50 / 10 | Upload caps enforced while streaming |

## Deploying (production checklist)

```powershell
# prod example (single worker — see note below)
$env:ENV="prod"; $env:SECRET_KEY="<random 32+ chars>"; $env:CORS_ORIGINS="https://queue.example.com"
.\venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Deploy to Render (free blueprint included)

`render.yaml` + `.python-version` are committed. In the Render Dashboard:
**New → Blueprint → select harshithnayak07/QueueFlow → Apply.**
`SECRET_KEY` auto-generates; health check is `/health`.

- **Free tier reality check:** 512 MB RAM runs auth/queue/tokens/tracking, but YOLO analysis loads torch and will likely OOM-crash the worker. Upgrade the service to a 2 GB plan if you need Analyze online, or accept 503s there.
- Free services **sleep after 15 min idle** (~1 min wake-up) and **restart anytime**; SQLite data resets on redeploy/restart — fine for demo, not for real queues. Add Render Postgres + set `DATABASE_URL` for persistence.
- After deploy, add your `https://<you>.onrender.com` origin to the Google Client ID's Authorized JavaScript origins (else the Google button is blocked), and update `CORS_ORIGINS` if the hostname differs.
- First visit: sign up the owner at `/` — `yolov8n.pt` downloads itself on first analysis.

- **Run one worker** (`--workers 1`): join-capacity locks and the progression sweeper are single-process. Multi-worker needs a DB-level guard (documented in code).
- **Database**: SQLite is fine for local/small use; point `DATABASE_URL` at PostgreSQL for real traffic. No Alembic yet — schema evolves via `ensure_schema_updates()`; adopt real migrations before commercial hosting.
- **Behind HTTPS + reverse proxy** in production; never expose `--reload`.
- **YOLO license**: Ultralytics YOLOv8 is AGPL-3.0 — fine for local/internal MVP use, re-check before commercial hosting.
- **Google Sign-In**: add the deployed origin to the Client ID's Authorized JavaScript origins, or Google blocks the button (401/origin error is Google-side).
- **First-run**: hit `/health`, create an owner, confirm `yolov8n.pt` downloaded (watch server log once).

## Security posture (audited)

- bcrypt password hashing (72-byte limit enforced at schema); JWT HS256, 12h expiry, no sensitive data in payloads.
- All owner routes require JWT + per-session ownership check (foreign/missing → bare 404, no existence leak).
- Customer tracking uses unguessable `token_urlsafe(32)` IDs; public APIs never expose contacts, passwords, internal IDs, or other customers' tokens.
- Uploads validated by extension + streamed size cap, stored under server-generated names outside git.
- One token per phone number (canonicalized) enforced app-wide + DB unique index; capacity-gated joins under per-session locks.
- Secrets via env only (`.env` gitignored); committed `.env.example` documents everything.
- Known local-MVP trade-offs (documented, not hidden): CORS-`*` in dev only, sequential session IDs enumerable, SQLite, CPU-only YOLO, no rate limiting yet.

## Known limitations

- Owner token Call/Serve/Cancel buttons were removed from the dashboard per owner decision — queue advancement is currently automatic only (manual override API still exists).
- Customer name is UI-only (no DB column); token validity display text follows the per-token value.
- Background alerts only work while a customer page is open (no push/SMS).
- See `docs/session-state.md` for the full decision log.
