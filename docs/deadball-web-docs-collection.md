# Deadball Web Docs Collection

## overview.md

# Deadball Web — Project Overview

Deadball Web is a browser-based interface for generating Deadball-compatible game scorecards from MLB data. A React/Vite frontend calls a FastAPI backend that fetches MLB boxscores, converts them via the embedded `deadball_generator` tools, and returns both JSON and CSV scorecard data. SQLite caches games, raw boxscores, and generated outputs for reuse.

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

Flow: frontend calls `/api/games?date=YYYY-MM-DD` to list games → user clicks Generate → backend fetches boxscore (or uses cached/provided) → converts to Deadball JSON/CSV → frontend renders scorecard inline.

---

## 5. Data Model (current)

- **Game**: id, game_id (MLB gamePk), game_date, home_team, away_team, description, timestamps  
- **GameRawStats**: id, game_id (FK → Game), payload (raw boxscore JSON text)  
- **GameGenerated**: id, game_id (FK → Game), stats (Deadball JSON string), game_text (Deadball CSV string)  

Legacy roster tables/endpoints remain but are secondary to the game flow.

---

## 6. API Endpoints (current)

- `GET /api/games?date=YYYY-MM-DD` — list games for the date (cached; stub only if no network and nothing cached).  
- `POST /api/games/{game_id}/generate` — generate Deadball output; returns `{ game, stats (JSON string), game_text (CSV string), cached }`; `force=true` bypasses cached generated output; explicit errors on failure.  
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
uvicorn app.main:app --reload
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

---

## architecture.md

# Architecture

Deadball Web is a two-tier app: a Vite/React client that talks to a FastAPI backend which persists to SQLite and runs Deadball game conversion logic.

## Components
- **Frontend**: React + Vite + Tailwind; fetches JSON from the backend; default dev port `5173`; renders scorecard inline (no popup).
- **Backend**: FastAPI (`backend/app/main.py`); router prefix defaults to `/api`; uses SQLModel/SQLAlchemy for persistence; deadball conversion uses `backend/deadball_generator/cli/game.py`.
- **Database**: SQLite file (`deadball_dev.db` by default) created via SQLModel metadata on startup.
- **Config**: Env-driven in `backend/app/core/config.py` (`APP_NAME`, `API_PREFIX`, `DATABASE_URL`, `DEBUG`, `ALLOW_GENERATOR_NETWORK`) with `.env` loading.

## Data Flow (Dev)
1) UI calls `GET /api/games?date=YYYY-MM-DD` to list games; results are cached in SQLite.
2) User clicks Generate → `POST /api/games/{game_id}/generate`; backend fetches or uses cached boxscore, converts via `deadball_generator.cli.game`, stores raw + generated outputs.
3) API returns JSON (stats) and CSV strings; frontend renders scorecard inline.

## Code Layout
- `backend/app/main.py`: FastAPI app setup, lifespan hook, routes.
- `backend/app/api/routes.py`: API endpoints (games list, game generate, legacy roster endpoints).
- `backend/app/db.py`: Engine + table creation.
- `backend/app/schemas.py`: Pydantic request/response shapes.
- `frontend/src`: React app (date → games → generate → inline scorecard).
- `docs/*.md`: Project documentation set.

---

## api.md

# API

Base URL (dev): `http://localhost:8000`  
Prefix: `/api` (configurable via `API_PREFIX`).  
CORS: `CORS_ORIGINS` env (comma-separated), default includes `http://localhost:5173`.  
SQLite tables are created on startup via `db.init_db`.

## Health
- `GET /health` → `{"status": "ok"}`

## Games

### List Games by Date
- `GET /api/games?date=YYYY-MM-DD`
- Response: `{ items: Game[], count, date, cached }`
- Behavior: uses cached games if fresh; refetches from MLB schedule when stale/missing; only returns stub games if no network and nothing cached.

### Generate Deadball Output for a Game
- `POST /api/games/{game_id}/generate`
- Body:
  - `force` (bool, default `false`): bypass cached generated output.
  - `payload` (optional string): raw boxscore JSON to use instead of fetching.
- Response: `{ game, stats, game_text, cached }`
  - `stats`: JSON string with `players` and `teams`.
  - `game_text`: CSV string of the Deadball scorecard.
- Behavior: fetches or uses cached/provided boxscore, converts via `deadball_generator.cli.game`, stores raw + generated outputs. No stub fallbacks; explicit errors (e.g., fetch failure, unknown team code, generator returned no rows).

## Legacy Roster Endpoints (secondary)
- `POST /api/generate` (roster)
- `GET /api/rosters`
- `GET /api/rosters/{slug}`

---

## data-model.md

# Data Model

## Core (games)
- **Game**: `id`, `game_id` (MLB gamePk), `game_date`, `home_team`, `away_team`, `description`, `created_at`, `updated_at`
- **GameRawStats**: `id`, `game_id` (FK → Game), `payload` (raw boxscore JSON text)
- **GameGenerated**: `id`, `game_id` (FK → Game), `stats` (Deadball JSON string with `players`/`teams`), `game_text` (Deadball CSV string)

## Legacy (rosters; secondary)
- **Roster**: `id`, `slug`, `name`, `description`, `source_type`, `source_ref`, `public`, `created_at`
- **Player**: `id`, `roster_id` (FK → Roster), `name`, `team`, `role`, `positions`, `bt`, `obt`, `traits`, `pd`

## Schemas (backend/app/schemas.py)
- `Game`, `GameListResponse`, `GameGenerateRequest/Response`
- `Roster`, `Player`, `GenerateRequest/Response` (legacy roster endpoints)

## Persistence
- SQLite (`DATABASE_URL`, default `sqlite:///deadball_dev.db`)
- Tables created on startup via SQLModel metadata (`db.init_db`)
- Caching:
  - Games by date
  - Raw boxscores per game
  - Generated Deadball outputs per game (stats JSON + CSV)

---

## game-flow.md

# Game-by-Date Flow

Goal: let users pick a date, see games for that day, choose a game, and generate Deadball scorecards with caching and explicit errors (no stub fallbacks).

## Backend
- `GET /api/games?date=YYYY-MM-DD`  
  - Returns games for the date; uses cache if fresh; refetches schedule when stale/missing; stubs only when no network and nothing cached.
- `POST /api/games/{game_id}/generate`  
  - Fetches or accepts raw boxscore, caches it, converts via `deadball_generator.cli.game`, stores JSON/CSV outputs.  
  - `force=true` bypasses cached generated output.  
  - Errors surface (fetch failure, unknown team code, generator returned no rows).
- Storage: SQLite tables for games, raw stats, generated outputs.

## Frontend
- Date picker to select a day.
- Calls `/api/games?date=...` to list games.
- On Generate, calls `/api/games/{game_id}/generate`, shows progress, and renders the Deadball scorecard inline (two teams, hitters/bench/pitchers).
- Displays backend errors in-line (scorecard area) for transparency.

## Notes
- Team code normalization uses `deadball_generator.cli.game.team_code_from_name`.
- `ALLOW_GENERATOR_NETWORK` gate controls whether boxscores can be fetched; if disabled with no cache, API returns 503.
- CSV and JSON are both returned; frontend prefers JSON but can fall back to CSV parsing.

---

## generator.md

# Embedded Deadball Generator

The generator lives in `backend/deadball_generator/` and mirrors the upstream project, with CLI helpers used by the API.

## Game conversion
- Entry: `deadball_generator.deadball_api.convert_game`
- Uses `deadball_generator.cli.game.build_deadball_for_game` and `team_code_from_name`.
- Inputs: MLB boxscore JSON (string), game id, game date, home/away team names/codes.
- Outputs: `{ "players": [...], "teams": {...} }` (as JSON string) plus CSV (`game_text`).
- Strict: raises errors if raw stats can’t be parsed, team code is unknown, or generator returns no rows. No stub fallbacks.

## Frontend usage
- `POST /api/games/{game_id}/generate` returns both JSON (`stats`) and CSV (`game_text`); the React app renders the scorecard inline.

## Legacy roster helpers
- `convert_roster` (season/box_score/manual) remains but is secondary to the game flow.

---

## roadmap.md

# Roadmap

## Done / Current
- Monorepo, FastAPI backend, SQLite, React/Vite/Tailwind frontend scaffold.
- Game list + generate endpoints with caching and strict error handling (no stubs).
- Embedded `deadball_generator` wired for boxscore → Deadball conversion (JSON/CSV).
- Inline scorecard rendering in the frontend.

## Near Term
- Harden game conversion fidelity; add tests around `/api/games/{id}/generate`.
- Improve scorecard UX (loading/error states, styling polish).
- Document deployment (Render/Netlify) and production env settings.

## Later
- Revisit/retire legacy roster UI (backend endpoints remain for now).
- Add pagination/search if game/roster data grows.
- Add ops polish: logging, metrics, error reporting.
