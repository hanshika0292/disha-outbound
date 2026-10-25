#!/usr/bin/env bash
# dev.sh — boot the full Disha stack for local development.
#
# Starts:
#   - FastAPI on :8000  (uvicorn, --reload)
#   - Next.js  on :3000 (next dev, --port 3000)
#
# Both run in this terminal. Ctrl+C kills both cleanly.
#
# Prereqs: postgres up (`docker compose up -d`), .env present at repo root,
# ui/.env.local present, ui/node_modules installed.

set -e

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# Sanity checks
if [ ! -f ".env" ]; then
  echo "✗ .env missing at repo root. Copy .env.example or read the README."
  exit 1
fi
if [ ! -f "ui/.env.local" ]; then
  echo "✗ ui/.env.local missing. Copy the template:"
  echo "    cp ui/.env.example ui/.env.local"
  exit 1
fi
if [ ! -d "ui/node_modules" ]; then
  echo "✗ ui/node_modules missing. Run: (cd ui && npm install)"
  exit 1
fi
if [ ! -d ".venv" ]; then
  echo "✗ .venv missing. Run: uv sync"
  exit 1
fi

PIDS=()
cleanup() {
  echo
  echo "stopping…"
  for pid in "${PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
  exit 0
}
trap cleanup INT TERM

# FastAPI
echo "→ api  http://localhost:8000"
.venv/bin/uvicorn disha.api.app:app --reload --port 8000 --log-level warning &
PIDS+=($!)

# Next.js
echo "→ ui   http://localhost:3000"
(cd ui && npm run dev -- --port 3000) &
PIDS+=($!)

echo
echo "Ctrl+C to stop both."
wait
