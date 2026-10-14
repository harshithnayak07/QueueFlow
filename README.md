# QueueFlow AI — AI-Powered Virtual Queue Management (MVP)

Responsive web app: recorded-video crowd estimate (OpenCV + YOLO) → wait estimate → owner-controlled virtual token queue via link/QR.

> Full spec: `docs/project-context.md` | AI session memory: `AGENTS.md` | Progress: `docs/session-state.md`

## Status
Phase 1-2 (requirements + design) done. Phase 3 (backend foundation) next — awaiting approval. No app code yet.

## Planned stack
Frontend: plain HTML/CSS/JS. Backend: Python + FastAPI + SQLAlchemy + SQLite. Vision: OpenCV + YOLO.

## How sessions keep context
OpenCode auto-loads `AGENTS.md` + `opencode.json` instructions on every new session. Keep them updated. Update `docs/session-state.md` at end of each work block.

## Setup (to be added in Phase 3)
- `python -m venv venv`, install `requirements.txt`, copy `.env.example` → `.env`, run FastAPI, run tests.
