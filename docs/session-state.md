# QueueFlow AI — Session State (update every session)

This is how future sessions know where we left off. Update at end of each work block.

## Last updated
2026-10-14 — S4 Vision Pipeline DONE (53 pytest passed)

## SDLC progress
- [x] Phase 1: Requirements Analysis + Scope Freeze — DONE (see docs/project-context.md §5-7)
- [x] Phase 2: System Design — DONE (arch, DB, API, vision constraints, see docs/project-context.md §9-12)
- [x] S1 Foundation: git init, venv (py3.10.10), requirements (fastapi/sqlalchemy/pytest), FastAPI + /health, SQLite base, pytest 1 passed — DONE
- [x] S2: DB Models + Auth — DONE (owner/session/token/snapshot models, bcrypt + JWT Bearer, register/login/me, ownership guard, 14 pytest passed)
- [ ] S3: Queue Sessions + Wait Formula — DONE (waiting_time_service, session CRUD/settings/status/activate, 32 pytest passed)
- [ ] S4: Vision Pipeline (upload + YOLO) — DONE (vision_service median aggregation, analyze-video endpoint, owner-review contract, 53 pytest passed)
- [ ] S5: Virtual Queue + Tokens
- [ ] S6: Frontend Owner + Customer
- [ ] S7: Integration + Docs + Deploy

## Decisions made
- Stack frozen: FastAPI + SQLite + plain HTML/CSS/JS + OpenCV/YOLO, free/local-first.
- Owner in control, vision = estimate, no auto person→token mapping.
- Context persistence: AGENTS.md auto-loads, full spec in docs/project-context.md, this file tracks progress.
- Old file `# QueueFlow AI — Project Context, R.txt` (em-dash, leading #) migrated to `docs/project-context.md` — safe name, same 670 lines. Old file to be removed to avoid duplication.
- S2 auth: `bcrypt` (direct; `passlib` avoided — unmaintained, bcrypt-compat bugs) + `PyJWT` HS256 (no `python-multipart`: login takes JSON). Deps added to requirements.txt.
- S2 auth strategy decided: stateless JWT Bearer, 12h expiry; endpoints `POST /api/auth/register` (201/409), `POST /api/auth/login`, `GET /api/auth/me`. No logout endpoint (client discards token) — assumption.
- S2 schema gaps resolved: `people_count` = last vision estimate (nullable until first analysis) vs `physical_people_remaining` = owner-confirmed, authoritative for waits; session `status` = `open`/`closed` + separate `virtual_queue_enabled`; live waits computed (S3), only snapshots store `estimated_wait_minutes` (nullable).
- S2 guards: ownership helper `get_owned_session()` returns 404 for foreign/missing sessions (no existence leak); `tracking_id` default `secrets.token_urlsafe(32)`, unique; token_number unique per session only.
- S2 note: PyJWT warns dev-default SECRET_KEY <32 bytes; `.env.example` now says production needs >=32 chars.
- S2 test fix: `test_tracking_ids_unique_and_unguessable` used a substring check that failed when the random ID contained the digit 1; replaced with exact inequality (`tracking_id != str(id)`). Suite now 14 passed.
- S3 DONE: `app/services/waiting_time_service.py` (pure `estimate_wait_minutes` with zero-counter→None guard, negatives→ValueError, missing→None; `session_people_ahead` = physical + active virtual; advisory-only `recommend_virtual_queue`, threshold param default 10); `app/schemas/queue.py` (SessionCreate/SettingsUpdate/Out/StatusOut, StatusOut carries estimate_note + threshold); `app/api/routes/queue_sessions.py` (POST/GET list/GET status/PATCH settings/POST activate, all owner-auth + `get_owned_session` 404 guard). 32 pytest passed (14 S2 + 8 wait-unit + 10 session-API).
- S3 assumptions: recommendation threshold default 10 people-ahead (no spec value; param-overridable); people_ahead counts token statuses waiting/called only (served/cancelled excluded); wait rounded to 2dp; activate is idempotent 200 but 409 on closed sessions; settings cannot toggle virtual flag (explicit endpoint only); deactivation endpoint left as future gap.
- S4 DONE: `app/services/vision_service.py` (pure `aggregate_counts` median — never a sum; `validate_video_extension`; `count_boxes_in_roi` normalized-box helper; `analyze_video` OpenCV sampling with injectable detector + lazy YOLOv8n backend; `InvalidVideoError`→422 / `VisionUnavailableError`→503); `app/api/routes/vision.py` POST `/api/queue-sessions/{id}/analyze-video` (owner-auth + `get_owned_session` 404 guard, multipart `file` + `frame_stride`/`max_frames`/`confidence` query params, streamed 50MB cap→413, server-generated safe filename under `settings.upload_dir`, threadpool analysis, sets `people_count` + `QueueSnapshot(source="vision")` but NEVER `physical_people_remaining`); `app/schemas/vision.py` AnalyzeVideoOut (`needs_owner_review=True` + estimate note). 53 pytest passed (32 prior + 13 vision-unit + 8 vision-API).
- S4 assumptions: 50MB cap, stride 30 / max 60 frames / conf 0.5 defaults; uploads kept on disk (`data/`, gitignored) with `video_filename` returned — retention policy is a future gap; snapshot wait = vision estimate + active virtual through S3 formula (historical "what-if", not authoritative).
- S4 YOLO decision: YOLOv8n selected (smallest YOLOv8, CPU-runnable); license = Ultralytics AGPL-3.0 — OK for local MVP, must re-check before commercial hosting; `ultralytics` (torch ~200MB+) NOT installed yet — backend lazy-loads and API returns 503 with install hint until then; unit/API tests inject a stub detector so CI stays light. Installed: `python-multipart` + `opencv-python-headless` (headless = no GUI deps).
- S4 note: switched new route to FastAPI-0.143 canonical constants (`HTTP_422_UNPROCESSABLE_CONTENT`, `HTTP_413_CONTENT_TOO_LARGE`) — old aliases emit deprecation warnings.

## Open questions for Phase 3+
- ~~YOLO model version + hardware check (propose YOLOv8n, verify license/install).~~ Resolved S4: YOLOv8n (Ultralytics AGPL-3.0); `ultralytics` install deferred (503-guarded) — needs decision on when to pull torch-weight deps.
- QR generation library choice.

## Next step
S5 NEXT: Virtual Queue + Tokens (secure token creation, public tracking endpoint, owner status transitions, recalculation).
