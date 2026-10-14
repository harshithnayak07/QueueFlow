# QueueFlow AI — AGENTS.md (persistent memory for all sessions)

This file auto-loads in every OpenCode session. It is the source of truth for how to work on this repo.
Full requirements + design: `docs/project-context.md`. Current phase status: `docs/session-state.md`.

## What this project is
QueueFlow AI — AI-powered virtual queue management (MVP). Responsive web app for small businesses (hospital counter, service counter).
Problem: customers wait blindly in physical queues, owners can't estimate crowd/wait.
Solution: recorded-video crowd estimate (OpenCV + YOLO) → wait estimate → owner-controlled virtual token queue via link/QR → customer browser tracking page.

Two UIs only: Owner Dashboard (desktop-first, responsive) + Customer Token Tracking page (mobile-first, no app install, browser link).

## Tech stack (MVP, do not change without approval)
- Frontend: plain HTML/CSS/JS, responsive. No framework unless justified.
- Backend: Python + FastAPI + Pydantic + SQLAlchemy + SQLite.
- Vision: OpenCV + YOLO person detection (e.g. YOLOv8n to verify) + ROI + frame-sampling/tracking to avoid double-count.
- Dev: git + GitHub, venv, pytest (or suitable Python framework), .env + .env.example, README setup steps.
- AI = vision only. No LLM for tokens/waits. No paid APIs in MVP. Free/local-first.

## Scope freeze — enforce
In: auth, session mgmt, recorded-video upload/analysis, manual count correction, wait calc, cutoff + virtual activation, token gen/status, secure tracking link, in-app alert while page open, SQLite, tests/docs.
Out: face recognition, auto person-to-token mapping, auto cutoff, live streaming, paid SMS/WhatsApp, LLM chatbot/token-gen, multi-tenant SaaS, separate admin, microservices.
Do not silently expand scope. Label gaps as future enhancements.

## Critical constraints (never violate)
1. Owner in control — system recommends, never auto-decides cutoff/activation.
2. Vision = estimate, never guarantee. Require owner review/correction. Never sum detections across frames naively.
3. YOLO cannot map detected person → token ID. Coordinate via owner rules + token status.
4. Wait formula: `wait_min ≈ people_ahead × avg_service_min / active_counters`. Guard divide-by-zero, invalid inputs. Always label approximate.
5. Security/privacy: hash passwords, protect owner routes + ownership check per session, `tracking_id = secrets.token_urlsafe(32)` (never sequential ID as credential), never expose contacts/passwords/internal IDs on public APIs, validate uploads (type/size/path), secrets in env, gitignore uploads/db/env/venv.
6. Notifications MVP = in-app/browser alert on open page only. No claim of background push when browser closed. No paid SMS/WhatsApp.

## Architecture (modular monolith)
- `frontend/` owner + customer pages
- `app/api/routes/` auth, owner, public
- `app/services/` vision_service, queue_service, waiting_time_service, notification_service (keep vision separate from routes)
- `app/models/` owner, queue_session, token, queue_snapshot
- Dataflow: upload → validate → analyze → owner review → apply cutoff/settings → calc wait → persist → render.

## DB + API quick ref (details in docs/project-context.md)
- Owners 1→N Sessions 1→N Tokens + Snapshots. Token statuses: waiting/called/served/cancelled.
- Endpoints: POST /api/auth/login, POST /api/queue-sessions, POST .../{id}/analyze-video, GET .../{id}/status, PATCH .../{id}/settings, POST .../{id}/activate-virtual-queue, POST /api/public/.../tokens, GET /api/public/tokens/{tracking_id}, PATCH /api/owner/tokens/{token_id}/status. Missing: decide register/logout with auth strategy.
- Open gaps: `people_count` vs `physical_people_remaining`, stored vs computed estimates, session `status` values, QR lib choice.

## Engineering workflow (mandatory)
1. Understand before coding. Explain decisions/trade-offs plainly.
2. One SDLC phase at a time, small reviewable steps. Never one giant generation.
3. Keep modules focused, avoid premature abstraction.
4. Test important logic + edge cases. Never invent passing tests.
5. Diagnose errors, don't hide with quick fixes. Don't install packages/paid APIs without why.
6. Don't rewrite approved architecture silently. Distinguish confirmed vs assumption vs proposal.
7. Inspect repo first, preserve useful work. Explain env-modifying commands before asking to run.
8. Update `docs/session-state.md` at end of each work session (phase done, decisions, next step).

## Current state (update as we move)
- Phase 1-2: requirements + design analysis DONE (2026-10-14).
- S1 Foundation: git init, venv (py3.10.10), requirements (fastapi/sqlalchemy/pytest), FastAPI + /health, SQLite base, pytest 1 passed — DONE
- S2: DB Models + Auth — NEXT
- S2-S7 remaining per 7 reliable sessions plan (see docs/session-state.md).
