#!/usr/bin/env bash
# One legacy family-count iteration: build workspaces, ingest them with the
# legacy SemSource stack at tier 0, wait for structural readiness, count
# ENTITY_STATES / OUTGOING_INDEX keys, and always tear the stack down.
#
# Usage: run.sh <iteration-label> <evidence-dir>
# The fixture repositories are exported from sibling checkouts with git archive
# (families.py prepare); the SemSource stack itself is built from SEMSOURCE_DIR.
# Env:   SEMSOURCE_DIR (default: sibling ../semsource checkout)
#        READY_CAP_SECONDS (default 900, measured from semsource healthy)
set -euo pipefail

label=${1:?iteration label}
evidence=${2:?evidence dir}
here=$(cd "$(dirname "$0")" && pwd)
semsource=${SEMSOURCE_DIR:-$(cd "$here/../../../../.." && pwd)/semsource}
out="$evidence/$label"
project=semselect-family-count
ready_cap=${READY_CAP_SECONDS:-900}
status_url=http://localhost:18080/source-manifest/status

mkdir -p "$out"
out=$(cd "$out" && pwd) # the counter runs from $here, so a relative path would break
log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$out/run.log"; }

export NATS_HOST_PORT=14222 NATS_MONITOR_HOST_PORT=18222 SEMSOURCE_HTTP_PORT=18080
export FAMILY_COUNT_CONFIG_DIR="$here"
mkdir -p /tmp/semselect-families
FAMILY_WORKSPACE_ROOT=$(cd /tmp/semselect-families && pwd -P)
export FAMILY_WORKSPACE_ROOT

compose() {
  (cd "$semsource" && docker compose -p "$project" -f docker-compose.yml \
    -f "$here/compose.family-count.yml" "$@")
}

for port in 14222 18222 18080; do
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    log "port $port already in use; refusing to start (another stack may own it)"
    exit 2
  fi
done

log "iteration $label: semsource $(git -C "$semsource" rev-parse HEAD)$(git -C "$semsource" diff --quiet || echo ' (dirty)')"
python3 -I "$here/families.py" prepare --input "$here/families.input.json" \
  --config-out "$here/family-count.tier0.json" --manifest-out "$out/manifest.json" | tee -a "$out/run.log"
python3 -I "$here/families.py" dupcheck --manifest "$out/manifest.json" \
  --out "$out/dupcheck.json" | tee -a "$out/run.log"
cp "$here/families.input.json" "$out/families.input.json"
cp "$here/family-count.tier0.json" "$out/family-count.tier0.json"
compose config > "$out/compose.resolved.yml"

teardown() {
  rc=$?
  compose logs --no-color --timestamps > "$out/compose.log" 2>&1 || true
  compose down -v --remove-orphans >> "$out/run.log" 2>&1 || true
  log "teardown complete (exit $rc)"
}
trap teardown EXIT

start=$(date +%s)
compose up -d >> "$out/run.log" 2>&1
cid=$(compose ps -q semsource)
log "image $(docker inspect --format '{{.Image}}' "$cid")"

until [ "$(docker inspect --format '{{.State.Health.Status}}' "$cid")" = healthy ]; do
  if [ $(( $(date +%s) - start )) -gt 300 ]; then log "semsource not healthy after 300s"; exit 3; fi
  sleep 5
done
healthy=$(date +%s)
log "semsource healthy after $((healthy - start))s"

while :; do
  payload=$(curl -fsS "$status_url" || true)
  if [ -n "$payload" ]; then
    echo "$payload" > "$out/status.last.json"
    line=$(jq -c '{phase, total_entities, index_ready: .index.ready, embedding_ready: .embedding.ready}' <<<"$payload")
    log "status $line"
    if [ "$(jq -r '.phase == "ready" and .index.ready == true' <<<"$payload")" = true ]; then break; fi
  fi
  if [ $(( $(date +%s) - healthy )) -gt "$ready_cap" ]; then
    log "index.ready not reached within ${ready_cap}s of healthy"
    exit 4
  fi
  sleep 10
done
ready=$(date +%s)
log "ready after $((ready - healthy))s from healthy"
cp "$out/status.last.json" "$out/status.ready.json"

(cd "$here" && GOTOOLCHAIN=local go run . -nats nats://localhost:14222 -out "$out/counts.json")
sleep 15
(cd "$here" && GOTOOLCHAIN=local go run . -nats nats://localhost:14222 -out "$out/counts.recheck.json")
curl -fsS "$status_url" > "$out/status.recheck.json"
first=$(jq .entity_keys "$out/counts.json")
second=$(jq .entity_keys "$out/counts.recheck.json")
log "entity keys: $first, recheck after 15s: $second"
jq -r '.systems | to_entries[] | "\(.key)\t\(.value.entities)"' "$out/counts.json" | tee -a "$out/run.log"
log "iteration wall-clock $(( $(date +%s) - start ))s"
