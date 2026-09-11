# Deadball Desktop (Tauri)

This app wraps the web frontend in a Tauri shell and starts the backend for you so you can generate/download Deadball scorecards without a browser.

## Prerequisites
- Rust toolchain (for Tauri)
- Node.js + npm
- Python backend deps installed in a venv (repo-root `.venv` is recommended and auto-bundled as `backend/.venv`):
  ```bash
  python3.12 -m venv .venv
  source .venv/bin/activate
  pip install -r backend/requirements.txt
  pip install -e backend/deadball_generator
  ```
- Icon source: `src-tauri/icons/icon.png` (already converted to RGBA)

## Run in Dev
```bash
cd src-tauri
cargo tauri dev
```
This:
- builds/serves the frontend with Vite (auto-picks an open port)
- starts the backend (from the repo backend if present)
- opens the desktop window

If the backend path changes, update the backend path logic in `src-tauri/src/main.rs` or add a configurable env var.

## Build Bundles
```bash
cd src-tauri
npx @tauri-apps/cli icon icons/icon.png   # only when icon changes
npx @tauri-apps/cli build
```
Before building, regenerate the bundled backend archive:
```bash
# from repo root
bash scripts/package-backend.sh
cd src-tauri
npx @tauri-apps/cli build
```
`scripts/package-backend.sh` bundles `backend/` and, if present, bundles repo-root `.venv` as `backend/.venv` so the app can start its own backend without relying on an externally running backend.
Outputs (macOS):
- `.app`: `src-tauri/target/release/bundle/macos/Deadball Desktop.app`
- `.dmg`: `src-tauri/target/release/bundle/dmg/Deadball Desktop_1.0.0-rc.1_x64.dmg` (requires `hdiutil` and a GUI-capable macOS; headless/sandboxed envs may fail)
- Zip fallback if DMG is blocked:
  ```bash
  cd src-tauri/target/release/bundle/macos
  ditto -c -k --sequesterRsrc --keepParent "Deadball Desktop.app" "../Deadball-Desktop-macos.zip"
  ```

Build from repo root (macOS):
```bash
bash scripts/package-backend.sh
cd src-tauri
npx @tauri-apps/cli build
```

## Behavior Notes
- A copy of `backend` (and bundled `.venv` when available) is packed into `Resources/backend-template.tar.gz` and copied to the user’s app data dir on first run; the backend then runs from that writable location. Dev mode still allows repo-backend fallbacks.
- The backend runtime forces `DATABASE_URL` to `<app-data>/backend/deadball_dev.db` so SQLite is always writable in packaged mode.
- Desktop frontend network calls are proxied through a Rust command that targets `http://127.0.0.1:8000`, avoiding WebView loopback networking inconsistencies.
- Logs go to stdout; backend helper logs can write to `/tmp/deadball-backend.log`.
- Download buttons:
  - PDF/HTML downloads save directly to `~/Downloads` in Tauri; browser fallback if native save fails.
  - Filenames use `YYYY-MM-DD - Away @ Home - Deadball.(pdf|html)`.
- CORS is permissive by default to allow the Tauri origin; set `CORS_ORIGINS` to tighten if needed.

## Troubleshooting
- “Load failed” / games don’t load: verify backend is found/running. Run the bundled binary directly to see logs:
  ```bash
  src-tauri/target/release/bundle/macos/Deadball\ Desktop.app/Contents/MacOS/deadball-desktop
  ```
  Check `/tmp/deadball-backend.log` if present.
- Icon errors: ensure `src-tauri/icons/icon.png` is RGBA, then regenerate with `npx @tauri-apps/cli icon icons/icon.png`.
- Ports in use: the Vite dev server will auto-increment; backend runs on 8000.
