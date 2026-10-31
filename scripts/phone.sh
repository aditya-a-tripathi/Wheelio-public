#!/usr/bin/env bash
# Open Wheelio on a real phone over HTTPS (phone browsers only allow location over HTTPS).
# Starts the app on port 8000 with auto-reload (unless it is already running) and a public
# https tunnel to it, then prints the link. No account and nothing to install: it uses
# Cloudflare's quick tunnel if cloudflared is installed, otherwise localhost.run over the ssh
# that every Mac already has. Ctrl+C stops both.
#
# The link shows the app running on THIS laptop, so edits saved here show up on the phone
# after a refresh. Each teammate runs this on their own laptop to see their own changes.
#
#   scripts/phone.sh            # real API
#   scripts/phone.sh mock       # link opens /?mock=1
#
# Anyone with the link can open the app while this runs, so stop it when you are done.
set -euo pipefail
cd "$(dirname "$0")/.."
PORT=8000
SUFFIX=""; [[ "${1:-}" == "mock" ]] && SUFFIX="/?mock=1"


pids=()
cleanup() { for p in "${pids[@]:-}"; do kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT INT TERM

if curl -s -o /dev/null "http://localhost:$PORT/"; then
  echo "App already running on port $PORT, reusing it."
else
  echo "Starting app on port $PORT..."
  uv run uvicorn backend.app.main:app --reload --port "$PORT" > /tmp/wheelio-server.log 2>&1 &
  pids+=($!)
  for _ in $(seq 1 40); do curl -s -o /dev/null "http://localhost:$PORT/" && break; sleep 0.5; done
  curl -s -o /dev/null "http://localhost:$PORT/" || { echo "App did not start, see /tmp/wheelio-server.log"; exit 1; }
fi

LOG=/tmp/wheelio-tunnel.log
: > "$LOG"
if command -v cloudflared >/dev/null; then
  cloudflared tunnel --no-autoupdate --url "http://localhost:$PORT" > "$LOG" 2>&1 &
  PATTERN='https://[a-z0-9-]+\.trycloudflare\.com'
else
  echo "(cloudflared not installed, using localhost.run over ssh)"
  echo "WARNING: free localhost.run links drop and change address after a while. If the app says"
  echo "  'Could not reach the route server', the link died: rerun this script and use the new link."
  echo "  For a stable link (recommended for testing navigation and the recording):"
  echo "  brew install cloudflared   (one time, then rerun this script)"
  ssh -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 -o ExitOnForwardFailure=yes \
    -R 80:localhost:$PORT nokey@localhost.run > "$LOG" 2>&1 < /dev/null &
  PATTERN='https://[a-z0-9]+\.lhr\.life'
fi
pids+=($!)

URL=""
for _ in $(seq 1 60); do
  URL=$(grep -oE "$PATTERN" "$LOG" | head -1 || true)
  [[ -n "$URL" ]] && break; sleep 0.5
done
[[ -z "$URL" ]] && { echo "Tunnel did not start, see $LOG"; exit 1; }
echo
echo "  Open on your phone:  $URL$SUFFIX"
echo
if command -v qrencode >/dev/null; then qrencode -t ANSIUTF8 "$URL$SUFFIX"; fi
echo "(The link can take ~10-30 s to start working. Ctrl+C stops the tunnel.)"
wait
