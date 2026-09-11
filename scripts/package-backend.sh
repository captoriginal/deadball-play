#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

mkdir -p src-tauri/resources
STAGE_DIR="$(mktemp -d)"
trap 'rm -rf "$STAGE_DIR"' EXIT

mkdir -p "$STAGE_DIR/backend"
rsync -a \
  --exclude '.env' \
  --exclude '*.db' \
  --exclude '.cache' \
  --exclude '.pytest_cache' \
  --exclude '__pycache__' \
  --exclude 'data/generated' \
  --exclude 'data/raw' \
  --exclude '.venv' \
  backend/ "$STAGE_DIR/backend/"

if [ ! -d "$STAGE_DIR/backend/.venv" ] && [ -d ".venv" ]; then
  echo "Including repo .venv as backend/.venv in bundle archive"
  cp -R .venv "$STAGE_DIR/backend/.venv"
fi

if [ ! -d "$STAGE_DIR/backend/.venv" ]; then
  echo "Warning: no backend/.venv or repo .venv found; bundled app will rely on system python"
fi

tar -czf src-tauri/resources/backend-template.tar.gz -C "$STAGE_DIR" backend
echo "Wrote src-tauri/resources/backend-template.tar.gz"
