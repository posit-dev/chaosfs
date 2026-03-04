#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
PROJECT_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
MOUNT_BASE=${MOUNT_BASE:-$PROJECT_ROOT/runtime/mnt}
LOG_DIR=${LOG_DIR:-$PROJECT_ROOT/demo/logs}
CLIENT_A_NAME=${CLIENT_A_NAME:-clientA}
CLIENT_B_NAME=${CLIENT_B_NAME:-clientB}
CLIENT_A="$MOUNT_BASE/$CLIENT_A_NAME"
CLIENT_B="$MOUNT_BASE/$CLIENT_B_NAME"
WATCH_LOG="$LOG_DIR/demo_race.log"
DEMO_DIR=${DEMO_DIR:-venv-demo}

if [[ ! -d "$CLIENT_A" || ! -d "$CLIENT_B" ]]; then
  echo "Please mount two clients (e.g., \"chaosfs mount ...\") before demo_race.sh" >&2
  exit 1
fi
if command -v mountpoint >/dev/null 2>&1; then
  if ! mountpoint -q "$CLIENT_A" >/dev/null 2>&1 || ! mountpoint -q "$CLIENT_B" >/dev/null 2>&1; then
    echo "Both $CLIENT_A and $CLIENT_B must be active mounts; mount them with \"chaosfs mount\" first" >&2
    exit 1
  fi
fi

mkdir -p "$LOG_DIR"
rm -rf "$CLIENT_A/$DEMO_DIR" "$CLIENT_B/$DEMO_DIR"
mkdir -p "$CLIENT_A/$DEMO_DIR"

echo "Demo log: $WATCH_LOG"
: >"$WATCH_LOG"

listing_loop() {
  for i in {1..10}; do
    if [[ -d "$CLIENT_B/$DEMO_DIR" ]]; then
      entries=$(ls -A "$CLIENT_B/$DEMO_DIR" 2>/dev/null || true)
      entries=${entries//$'\n'/ }  # flatten lines
    else
      entries="<missing>"
    fi
    printf "[%02d %s] %s\n" "$i" "$(date +%T)" "${entries:-<empty>}"
    sleep 0.7
  done
}

listing_loop >"$WATCH_LOG" &
WATCHER_PID=$!
trap 'kill "$WATCHER_PID" >/dev/null 2>&1 || true' EXIT

sleep 1

printf "[%s] Client A writes file\n" "$(date +%T)"
echo "initial content" >"$CLIENT_A/$DEMO_DIR/source.txt"
sleep 1

printf "[%s] Client A renames file\n" "$(date +%T)"
mv "$CLIENT_A/$DEMO_DIR/source.txt" "$CLIENT_A/$DEMO_DIR/final.txt"
sleep 1

printf "[%s] Client A removes file\n" "$(date +%T)"
rm -f "$CLIENT_A/$DEMO_DIR/final.txt"
sleep 1

kill "$WATCHER_PID" >/dev/null 2>&1 || true
wait "$WATCHER_PID" 2>/dev/null || true
trap - EXIT

printf -- "--- demo log ---\n"
cat "$WATCH_LOG"
