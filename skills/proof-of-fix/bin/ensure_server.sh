#!/bin/bash
# Ensure the target app is serving and healthy before a capture.
# Usage: ensure_server.sh <before|after>
# Exit 0 = healthy; exit 4 = SERVER_UNREACHABLE.
set -uo pipefail
ROOT="${POF_ROOT:-$PWD}"
CFG="$ROOT/.proof-of-fix/config.json"
PHASE="${1:-before}"
# One read, path passed as argv. Interpolating $CFG into `python3 -c` (as this
# did) breaks on any project path containing a quote, and is the pattern the
# rest of the pipeline forbids. NUL-delimited so any value survives verbatim.
CFGV=()
while IFS= read -r -d '' v; do CFGV+=("$v"); done < <(python3 - "$CFG" <<'PY'
import json, sys
c = json.load(open(sys.argv[1]))
for v in (c["port"], c["base_url"].rstrip("/"), c.get("health_path", "/"),
          c.get("app_dir", "."), c["dev_cmd"], str(c.get("restart_on_after", True)).lower()):
    sys.stdout.write(f"{v}\0")
PY
)
if [[ ${#CFGV[@]} -ne 6 ]]; then
  echo "config unreadable or missing port/base_url/dev_cmd: $CFG" >&2
  exit 4
fi
PORT="${CFGV[0]}"; BASE="${CFGV[1]}"; HEALTH="$BASE${CFGV[2]}"
APP="$ROOT/${CFGV[3]}"; DEV="${CFGV[4]}"; RESTART="${CFGV[5]}"
LOG="$ROOT/.proof-of-fix/server.log"

healthy() { curl -sf -m 3 -o /dev/null "$HEALTH"; }
kill_port() {
  lsof -ti tcp:"$PORT" | xargs -r kill 2>/dev/null || true
  sleep 1
  lsof -ti tcp:"$PORT" | xargs -r kill -9 2>/dev/null || true
}

# 'after' restarts by default so the capture provably runs the fixed code —
# HMR usually suffices, but a corrupted .next after history rewrites does not.
if [[ "$PHASE" == "after" && "$RESTART" == "true" ]]; then
  kill_port
elif healthy; then
  exit 0
else
  kill_port # a listener that fails health is stale — replace it
fi

mkdir -p "$ROOT/.proof-of-fix"
# Pass server_env (e.g. E2E_LOGIN_ENABLED) to the dev server as literal env args
# via `env K=V` — NOT eval — so a value like $(...) in config cannot execute a
# command on server start. Read as NUL-delimited K=V pairs to survive any bytes.
ENVARGS=()
while IFS= read -r -d '' kv; do ENVARGS+=("$kv"); done < <(python3 - "$CFG" <<'PY'
import json, sys
c = json.load(open(sys.argv[1]))
for k, v in c.get("server_env", {}).items():
    sys.stdout.write(f"{k}={v}\0")
PY
)
# The dev server must not inherit this script's stdio. Backgrounding inside a
# subshell leaves that subshell alive holding fd 1/2 of the caller: harmless when
# output goes to a file (it just orphans), fatal when the caller reads phase.sh
# through a pipe — the write end never closes, so the reader hangs long after
# result.json is on disk. Redirect all three descriptors on the subshell itself
# and exec into the server, so nothing survives holding them.
# ${arr[@]+…} guards the empty-array-under-nounset case in macOS bash 3.2
(cd "$APP" && exec nohup env ${ENVARGS[@]+"${ENVARGS[@]}"} bash -c "$DEV") \
  </dev/null >"$LOG" 2>&1 &
SRV=$!
echo "$SRV" >"$ROOT/.proof-of-fix/server.pid"
disown "$SRV" 2>/dev/null || true

for _ in $(seq 1 90); do
  healthy && exit 0
  sleep 2
done
echo "server never became healthy at $HEALTH — see $LOG" >&2
exit 4
