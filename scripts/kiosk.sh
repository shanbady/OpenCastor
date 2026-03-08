#!/usr/bin/env bash
# scripts/kiosk.sh — OpenCastor kiosk launcher
# Waits for gateway + Streamlit dashboard, then opens Chromium in kiosk mode.
set -euo pipefail

GATEWAY_URL="${OPENCASTOR_GATEWAY_URL:-http://localhost:8000}"
DASH_PORT="${OPENCASTOR_DASH_PORT:-8501}"
FACE_URL="${GATEWAY_URL}/face"
DISPLAY="${DISPLAY:-:0}"

log() { echo "[kiosk] $*"; }

wait_for() {
  local url="$1" label="$2" tries=0
  log "Waiting for $label at $url ..."
  until curl -sf "$url" >/dev/null 2>&1; do
    tries=$((tries+1))
    [ $tries -gt 60 ] && { log "Timeout waiting for $label"; exit 1; }
    sleep 2
  done
  log "$label ready."
}

wait_for "${GATEWAY_URL}/health" "gateway"
wait_for "http://localhost:${DASH_PORT}/_stcore/health" "dashboard"

log "Launching Chromium kiosk -> $FACE_URL"
exec chromium-browser \
  --kiosk \
  --noerrdialogs \
  --disable-infobars \
  --disable-session-crashed-bubble \
  --disable-restore-session-state \
  --no-first-run \
  --disable-features=Translate \
  --app="${FACE_URL}" \
  --display="${DISPLAY}"
