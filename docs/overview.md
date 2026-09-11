# Deadball Web — Project Overview

Deadball Web is a browser-based interface for generating Deadball-compatible game scorecards from MLB data. A React/Vite frontend calls a FastAPI backend that fetches MLB boxscores, converts them via the embedded `deadball_generator` tools, and returns JSON/CSV scorecard data and a filled PDF scorecard. SQLite caches games, raw boxscores, and generated outputs for reuse.

There is also a Tauri desktop wrapper that bundles the frontend and starts the backend from the repo (or a packaged backend) so users can generate/download scorecards without a browser.

---

## 1. Goals
- Simple date → game list → generate → inline scorecard experience.
- Pure-Python backend (FastAPI + embedded `deadball_generator`) with no stub fallbacks—errors surface explicitly.
- Cached storage of raw and generated game artifacts in SQLite.
- Modern frontend stack (React + Vite + Tailwind) with inline scorecard rendering (no popup).
- Single monorepo for ease of development and deployment (Render/Netlify or similar later).

---

## 2. Tech Stack

### Backend
- Python 3.12.x, FastAPI, Uvicorn
- SQLModel / SQLAlchemy
- SQLite
- Embedded `deadball_generator` (local module) using `deadball_generator.cli.game`

### Frontend
- React
- Vite
- Tailwind CSS

### Tooling
- VS Code + Codex/Copilot
- ChatGPT for planning/docs
- Git + GitHub

---

## 3. Monorepo Structure

deadball-play/
├─ backend/  
│  ├─ app/ (FastAPI app, routes, config)  
│  ├─ deadball_generator/ (embedded generator, CLI helpers)  
│  ├─ requirements.txt  
│  └─ .env.example  
├─ frontend/  
│  ├─ package.json  
│  ├─ vite.config.js  
│  ├─ tailwind.config.js  
│  └─ src/ (app code)  
└─ docs/ (this folder)

---

## 4. High-Level Architecture

React + Vite frontend (http://localhost:5173)  
 | fetch() → JSON  
FastAPI backend (http://localhost:8000, prefix `/api`)  
 | persists to SQLite (`deadball_dev.db`)  
 | calls `deadball_generator.cli.game` for conversion  
Deadball scorecard HTML/CSV returned to frontend

Flow: frontend calls `/api/games?date=YYYY-MM-DD` to list games → user clicks Download PDF/HTML → backend fetches boxscore (or uses cached/provided) → converts to Deadball JSON/CSV and fills the PDF template → frontend saves the PDF/HTML (Tauri saves to disk; web downloads via browser).

---

## 5. Data Model (current)

- **Game**: id, game_id (MLB gamePk), game_date, home_team, away_team, description, timestamps  
- **GameRawStats**: id, game_id (FK → Game), payload (raw boxscore JSON text)  
- **GameGenerated**: id, game_id (FK → Game), stats (Deadball JSON string), game_text (Deadball CSV string)  

Legacy roster tables/endpoints remain but are secondary to the game flow.

---

## 6. API Endpoints (current)

- `GET /api/games?date=YYYY-MM-DD` — list games for the date (cached; returns empty with a “no games” message when there were no MLB games).  
- `POST /api/games/{game_id}/generate` — generate Deadball output; returns `{ game, stats (JSON string), game_text (CSV string), cached }`; `force=true` bypasses cached generated output; explicit errors on failure.  
- `GET /api/games/{game_id}/scorecard.pdf?side=home|away` — return the filled two-page PDF scorecard (away page 0, home page 1; `side` hints the requested page).  
- Health: `GET /health`  
- Legacy roster endpoints (`/api/generate`, `/api/rosters`) remain but are not the primary UI flow.

---

## 7. Local Development

Backend  
```
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```
Env: `backend/.env` (see `.env.example`), notably `ALLOW_GENERATOR_NETWORK=true` for boxscore fetches.

Frontend  
```
cd frontend
npm install
npm run dev
```
Env: `frontend/.env.local` with `VITE_API_BASE_URL=http://localhost:8000`.

---

## 8. Coordination

ChatGPT: architecture, API/data design, documentation, planning/roadmaps.  
Codex/VS Code: implementation, refactors, TODOs.

---

## 9. Current Status
- Backend game list + generate endpoints live; conversion uses `deadball_generator.cli.game` and produces Deadball JSON/CSV. No stub fallbacks; errors are explicit.  
- SQLite caching for games, raw stats, generated outputs; force refresh supported.  
- Frontend provides date → games → generate flow with inline scorecard rendering.  
- Traits normalization and HTML scorecard templating wired end-to-end.  
- `ALLOW_GENERATOR_NETWORK` gates boxscore fetch; if disabled and uncached, a 503 is returned.

---

## 10. Roadmap (snapshot)
- Harden game conversion fidelity; add tests around `/api/games/{id}/generate`.  
- Polish scorecard preview UX and error/loading states.  
- Document deployment steps and production settings.  
- Optional: de-emphasize legacy roster UI; keep backend roster endpoints documented but secondary.
