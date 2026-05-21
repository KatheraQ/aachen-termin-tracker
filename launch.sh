#!/usr/bin/env bash
# One-shot launcher: installs deps if needed, then runs server + scraper together.
# Works on macOS and Linux. Idempotent — re-run anytime.

set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

log() { printf "\033[36m[launch]\033[0m %s\n" "$*"; }
err() { printf "\033[31m[error]\033[0m %s\n" "$*" >&2; }

open_url() {
  if command -v open >/dev/null 2>&1; then open "$1"
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$1"
  fi
}

# ---------- 1. Python check ----------
if ! command -v python3 >/dev/null 2>&1; then
  err "python3 not found. Install Python 3.10+ first."
  exit 1
fi

# ---------- 2. venv + deps ----------
if [ ! -f .venv/bin/activate ]; then
  log "Creating Python virtual env..."
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

if ! python -c "import playwright, fastapi, aiosmtplib, yaml" >/dev/null 2>&1; then
  log "Installing Python dependencies (one-time, ~1 min)..."
  pip install --quiet --upgrade pip
  pip install --quiet -r requirements.txt
fi

# ---------- 3. Playwright Chromium ----------
if ! python -c "
import sys, pathlib
from playwright.sync_api import sync_playwright
try:
    with sync_playwright() as p:
        sys.exit(0 if pathlib.Path(p.chromium.executable_path).exists() else 1)
except Exception:
    sys.exit(1)
" >/dev/null 2>&1; then
  log "Installing Chromium (one-time, ~150MB)..."
  python -m playwright install chromium
fi

mkdir -p data

# ---------- 4. Read port from config ----------
PORT=$(python -c "from app.config import load_config; print(load_config().app.port)")

# ---------- 5. Stop previous processes ----------
pkill -f "run_server.py"  2>/dev/null && log "Stopped previous server"  || true
pkill -f "run_scraper.py" 2>/dev/null && log "Stopped previous scraper" || true
sleep 1

# ---------- 6. Start both in background ----------
log "Starting web server on port $PORT..."
nohup python run_server.py > data/server.log 2>&1 &
SERVER_PID=$!

log "Starting scraper loop..."
nohup python run_scraper.py > data/scraper.log 2>&1 &
SCRAPER_PID=$!

# wait for server to come up
for _ in {1..30}; do
  if curl -s -o /dev/null "http://127.0.0.1:$PORT/healthz" 2>/dev/null; then break; fi
  sleep 0.3
done

if ! curl -s -o /dev/null "http://127.0.0.1:$PORT/healthz" 2>/dev/null; then
  err "Server didn't come up. Last 30 lines of data/server.log:"
  tail -30 data/server.log
  kill $SERVER_PID $SCRAPER_PID 2>/dev/null || true
  exit 1
fi

log "Server  ready at http://127.0.0.1:$PORT/  (PID $SERVER_PID)"
log "Scraper running                         (PID $SCRAPER_PID)"
open_url "http://127.0.0.1:$PORT/"

echo ""
echo "============================================================"
echo "  Aachen Termin Tracker is running"
echo ""
echo "  Web UI:    http://127.0.0.1:$PORT/"
echo "  Logs:      data/server.log + data/scraper.log"
echo "  Stop:      close this window, or Ctrl+C"
echo "============================================================"
echo ""

trap "echo ''; log 'Stopping...'; kill $SERVER_PID $SCRAPER_PID 2>/dev/null; exit 0" EXIT INT TERM

# Tail both logs combined, prefixed with [server]/[scraper]
tail -F -n 0 data/server.log data/scraper.log 2>/dev/null | awk '
  /^==> data\/server\.log <==$/  {p="\033[35m[server] \033[0m"; next}
  /^==> data\/scraper\.log <==$/ {p="\033[33m[scraper]\033[0m"; next}
  { print p, $0; fflush() }
'
