#!/usr/bin/env bash
# Legacy SemStreams capture; not a SemEngine result.
#
# One pilot family, one legacy tier-1 stack (protocol freeze step 1). Exports
# the family workspace at its pinned commit (legacy-count/families.py prepare),
# runs SemSource's shipped Compose stack over that family alone with the HTTP
# embedder and graph clustering on (semantic-profile structural weights,
# semantic edges off), waits for embedding.ready, freezes the structural side
# (legacy-capture cmd/structural: entities, explicit topology, identity edges
# and weights, settled structural-only partition) and the mutual-kNN candidates
# (cmd/mutualknn), then always tears the stack down with `down -v`.
#
# Usage: run-family.sh <family-id>
# Env:   SEMSOURCE_DIR (default ../semsource next to this repo)
#        SEMSOURCE_HTTP_PORT (18080), NATS_HOST_PORT (14222), NATS_MONITOR_HOST_PORT (18222)
#        EMBEDDING_CAP_SECONDS (1800, from containers healthy)
#        PARTITION_SETTLE_SECONDS (35), PARTITION_SETTLE_TIMEOUT_SECONDS (600)
#        LOG_LEVEL (info), SEMEMBED_CPUS (2)
#        ALLOW_SEMSOURCE_DRIFT=1 to run a SemSource checkout other than the pinned commit
set -euo pipefail

FAMILY=${1:?family id (see fixtures/families.json)}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
FIXTURES=$(cd "$HERE/.." && pwd)
LEGACY_COUNT=$FIXTURES/legacy-count
TOOL_DIR=$(cd "$FIXTURES/../legacy-capture" && pwd)
REPO_ROOT=$(git -C "$HERE" rev-parse --show-toplevel)
SEMSOURCE_DIR=${SEMSOURCE_DIR:-$(cd "$REPO_ROOT/../semsource" && pwd)}
FAMILIES=$FIXTURES/families.json

PROJECT=semselect-tier1-capture
SEMSOURCE_CONFIG_NAME=mvp.json
EMBEDDING_CAP_SECONDS=${EMBEDDING_CAP_SECONDS:-1800}
PARTITION_SETTLE_SECONDS=${PARTITION_SETTLE_SECONDS:-35}
PARTITION_SETTLE_TIMEOUT_SECONDS=${PARTITION_SETTLE_TIMEOUT_SECONDS:-600}
POLL_SECONDS=5
HTTP_PORT=${SEMSOURCE_HTTP_PORT:-18080}
NATS_PORT=${NATS_HOST_PORT:-14222}
NATS_MONITOR_PORT=${NATS_MONITOR_HOST_PORT:-18222}
STATUS_URL=http://127.0.0.1:${HTTP_PORT}/source-manifest/status
NATS_URL=nats://127.0.0.1:${NATS_PORT}
DIRECTED_RAW_LIMIT_BYTES=$((20 * 1024 * 1024))
WS_ROOT=/tmp/semselect-families # families.py prepare refuses any other basename

TS=$(date -u +%Y%m%dT%H%M%SZ)
EVIDENCE=$REPO_ROOT/docs/evidence/${TS}-legacy-tier1-capture-${FAMILY}

die() {
	echo "run-family: $*" >&2
	exit 1
}

for tool in docker git go jq curl gzip shasum python3 lsof; do
	command -v "$tool" >/dev/null || die "missing required tool: $tool"
done
[[ -f $SEMSOURCE_DIR/docker-compose.yml ]] || die "no docker-compose.yml under $SEMSOURCE_DIR"
[[ -f $SEMSOURCE_DIR/configs/$SEMSOURCE_CONFIG_NAME ]] || die "missing configs/$SEMSOURCE_CONFIG_NAME"
[[ ! -e $EVIDENCE ]] || die "evidence directory already exists: $EVIDENCE"
jq -e --arg id "$FAMILY" '.families[] | select(.id == $id)' "$FAMILIES" >/dev/null ||
	die "family $FAMILY is not in $FAMILIES"

PINNED_SEMSOURCE=$(jq -r .capture_profile.semsource_commit "$FAMILIES")
SEMSOURCE_COMMIT=$(git -C "$SEMSOURCE_DIR" rev-parse HEAD)
SEMSOURCE_DIRTY=$(git -C "$SEMSOURCE_DIR" status --porcelain | wc -l | tr -d ' ')
if [[ $SEMSOURCE_COMMIT != "$PINNED_SEMSOURCE" || $SEMSOURCE_DIRTY != 0 ]] && [[ ${ALLOW_SEMSOURCE_DRIFT:-0} != 1 ]]; then
	die "SemSource checkout is $SEMSOURCE_COMMIT (${SEMSOURCE_DIRTY} dirty paths); families.json pins $PINNED_SEMSOURCE. Set ALLOW_SEMSOURCE_DRIFT=1 to run anyway."
fi

for port in "$NATS_PORT" "$NATS_MONITOR_PORT" "$HTTP_PORT"; do
	if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
		die "port $port already in use; refusing to start (another stack may own it)"
	fi
done
# The capture must start from an empty graph: refuse to reuse leftover state.
if [[ -n $(docker ps -aq --filter "label=com.docker.compose.project=$PROJECT") ]]; then
	die "compose project $PROJECT already has containers; run: (cd $SEMSOURCE_DIR && docker compose -p $PROJECT down -v)"
fi
if [[ -n $(docker volume ls -q --filter "label=com.docker.compose.project=$PROJECT") ]]; then
	die "compose project $PROJECT already has volumes; run: (cd $SEMSOURCE_DIR && docker compose -p $PROJECT down -v)"
fi

WORK=$(mktemp -d "${TMPDIR:-/tmp}/semselect-tier1-capture.XXXXXX")
CONFIG_DIR=$WORK/config
MILESTONES=$WORK/milestones.jsonl
: >"$MILESTONES"
mkdir -p "$CONFIG_DIR" "$WORK/bin"
STACK_STARTED=0
OUTCOME=failed
FAILURE=""

now_s() { date -u +%s; }
now_iso() { date -u +%Y-%m-%dT%H:%M:%SZ; }

T0=""
milestone() {
	local name=$1 extra=${2:-null} t
	t=$(now_s)
	jq -cn --arg name "$name" --arg utc "$(now_iso)" --argjson t "$t" \
		--argjson t0 "${T0:-null}" --argjson extra "$extra" \
		'{name: $name, utc: $utc, elapsed_s_since_compose_up: (if $t0 == null then null else $t - $t0 end), detail: $extra}' \
		>>"$MILESTONES"
	echo "milestone $name at $(now_iso)${T0:+ (+$((t - T0))s since compose up)}"
}

# compose runs from the SemSource checkout because its build.context is relative.
compose() {
	(cd "$SEMSOURCE_DIR" &&
		TIER1_CONFIG_DIR="$CONFIG_DIR" TIER1_FAMILY_DIR="$FAMILY_DIR" TIER1_FAMILY_ID="$FAMILY" \
			SEMSOURCE_HTTP_PORT="$HTTP_PORT" NATS_HOST_PORT="$NATS_PORT" NATS_MONITOR_HOST_PORT="$NATS_MONITOR_PORT" \
			LOG_LEVEL="${LOG_LEVEL:-info}" SEMEMBED_CPUS="${SEMEMBED_CPUS:-2}" \
			docker compose -p "$PROJECT" -f docker-compose.yml -f "$HERE/compose.tier1.yml" "$@")
}

cleanup() {
	local rc=$?
	set +e
	trap - EXIT
	# Ignore INT/TERM from here on: a second Ctrl-C must not kill the script
	# before `down -v` has run.
	trap '' INT TERM
	if [[ $rc -ne 0 && -z $FAILURE ]]; then
		FAILURE="exited with status $rc"
	fi
	mkdir -p "$EVIDENCE"
	if ((STACK_STARTED)); then
		compose ps -a >"$EVIDENCE/compose-ps.txt" 2>&1
		compose logs --no-color --timestamps semsource >"$WORK/semsource.log" 2>&1
		compose logs --no-color --timestamps >"$WORK/compose-all.log" 2>&1
		grep -Ei 'graph[-_]?(embedding|clustering)' "$WORK/semsource.log" \
			>"$EVIDENCE/semsource-graph-embedding-clustering.log"
		gzip -9c "$WORK/semsource.log" >"$EVIDENCE/semsource.log.gz"
		gzip -9c "$WORK/compose-all.log" >"$EVIDENCE/compose-all.log.gz"
		echo "tearing down compose project $PROJECT (down -v)"
		compose down -v --remove-orphans >"$EVIDENCE/compose-down.txt" 2>&1
	fi
	jq -s --arg outcome "$OUTCOME" --arg failure "$FAILURE" --arg family "$FAMILY" \
		'{provenance: "legacy SemStreams capture; not a SemEngine result", family: $family, outcome: $outcome,
		  failure: (if $failure == "" then null else $failure end), milestones: .}' \
		"$MILESTONES" >"$EVIDENCE/milestones.json"
	for f in run.json family.json prepare-manifest.json structural.log mutualknn.log last-status.json; do
		[[ -f $WORK/$f ]] && cp "$WORK/$f" "$EVIDENCE/$f"
	done
	[[ -f $CONFIG_DIR/$FAMILY.tier1.json ]] && cp "$CONFIG_DIR/$FAMILY.tier1.json" "$EVIDENCE/$FAMILY.tier1.json"
	if [[ -n $FAILURE ]]; then
		printf '%s\n' "$FAILURE" >"$EVIDENCE/FAILURE.txt"
	fi
	(cd "$EVIDENCE" && find . -type f ! -name SHA256SUMS | sed 's|^\./||' | LC_ALL=C sort |
		xargs shasum -a 256 >SHA256SUMS)
	rm -rf "$WORK"
	echo "evidence: $EVIDENCE (outcome: $OUTCOME)"
	exit "$rc"
}
trap cleanup EXIT
trap 'FAILURE="interrupted"; exit 130' INT TERM

fail() {
	FAILURE=$*
	echo "run-family: FAILED: $FAILURE" >&2
	exit 1
}

# --- Workspace: every family is exported from the sibling checkouts at its
# pinned commit (git archive, committed files only); only this family is mounted.
milestone prepare_start
python3 -I "$LEGACY_COUNT/families.py" prepare --input "$LEGACY_COUNT/families.input.json" \
	--ws-root "$WS_ROOT" --config-out "$WORK/tier0.unused.json" --manifest-out "$WORK/prepare-manifest.json" |
	grep -F "$FAMILY:" || fail "families.py prepare did not export $FAMILY"
python3 -I "$HERE/family_config.py" --families "$FAMILIES" --prepare-manifest "$WORK/prepare-manifest.json" \
	--family "$FAMILY" --mvp "$SEMSOURCE_DIR/configs/$SEMSOURCE_CONFIG_NAME" \
	--config-out "$CONFIG_DIR/$FAMILY.tier1.json" --family-out "$WORK/family.json" ||
	fail "family_config.py refused $FAMILY"
FAMILY_DIR=$(cd "$WS_ROOT/$FAMILY" && pwd -P)
FAMILY_FILES=$(find "$FAMILY_DIR" -type f | wc -l | tr -d ' ')
[[ $FAMILY_FILES == "$(jq '.family.files | length' "$WORK/family.json")" ]] ||
	fail "workspace holds $FAMILY_FILES files, families.json lists $(jq '.family.files | length' "$WORK/family.json")"
milestone prepare_done "$(jq -c '{split: .family.split, commit: .family.commit, files: (.family.files | length), workspace_sha256: .family.workspace_sha256}' "$WORK/family.json")"

# --- Provenance of the stack and the tools.
SEMSTREAMS_VERSION=$(awk '$1 == "github.com/c360studio/semstreams" { print $2 }' "$SEMSOURCE_DIR/go.mod")
[[ -n $SEMSTREAMS_VERSION ]] || die "could not read the semstreams version from $SEMSOURCE_DIR/go.mod"
[[ $SEMSTREAMS_VERSION == "$(jq -r .capture_profile.semstreams_version "$FAMILIES")" ]] ||
	fail "SemSource links semstreams $SEMSTREAMS_VERSION; families.json pins $(jq -r .capture_profile.semstreams_version "$FAMILIES")"
SEMSELECT_COMMIT=$(git -C "$REPO_ROOT" rev-parse HEAD)
SEMSELECT_DIRTY=$(git -C "$REPO_ROOT" status --porcelain | wc -l | tr -d ' ')
CORPUS_REPO=$(jq -r .family.repo "$WORK/family.json")
CORPUS_COMMIT=$(jq -r .family.commit "$WORK/family.json")

# Build the tools before the stack so a compile error costs nothing.
go build -C "$TOOL_DIR" -o "$WORK/bin/mutualknn" ./cmd/mutualknn
go build -C "$TOOL_DIR" -o "$WORK/bin/structural" ./cmd/structural

# --- Stack.
milestone build_start
STACK_STARTED=1
compose build semsource
milestone build_done
compose config >"$WORK/compose.resolved.yml"
T0=$(now_s)
milestone compose_up_start
compose up -d --wait --wait-timeout 600 --build || fail "docker compose up --wait did not reach healthy"
milestone containers_healthy
HEALTHY_AT=$(now_s)

phase_ready=0 index_ready=0 embedding_ready=0 last_report=0 last_body=""
while :; do
	running=$(compose ps --status running --services) || fail "docker compose ps failed"
	for svc in nats semembed semsource; do
		grep -qx "$svc" <<<"$running" || fail "service $svc is not running: $(compose ps -a 2>&1)"
	done
	if body=$(curl -fsS --max-time 5 "$STATUS_URL" 2>/dev/null) && jq -e . >/dev/null 2>&1 <<<"$body"; then
		last_body=$body
		snap=$(jq -c '{phase, total_entities, index: (.index | {ready, state, lag}), embedding: (.embedding | {ready, state, lag})}' <<<"$body")
		if ((!phase_ready)) && [[ $(jq -r '.phase' <<<"$body") == ready ]]; then
			phase_ready=1
			milestone phase_ready "$snap"
		fi
		if ((!index_ready)) && [[ $(jq -r '.index.ready' <<<"$body") == true ]]; then
			index_ready=1
			milestone index_ready "$snap"
		fi
		if ((phase_ready && index_ready && !embedding_ready)) && [[ $(jq -r '.embedding.ready' <<<"$body") == true ]]; then
			embedding_ready=1
			milestone embedding_ready "$snap"
			break
		fi
		if (($(now_s) - last_report >= 30)); then
			echo "status $(now_iso): $snap"
			last_report=$(now_s)
		fi
	fi
	if (($(now_s) - HEALTHY_AT > EMBEDDING_CAP_SECONDS)); then
		printf '%s\n' "$last_body" >"$WORK/last-status.json"
		fail "embedding.ready not true within ${EMBEDDING_CAP_SECONDS}s of containers healthy; last status: ${last_body:-<none>}"
	fi
	sleep "$POLL_SECONDS"
done
STATUS_AT_CAPTURE=$last_body
mkdir -p "$EVIDENCE"
printf '%s\n' "$STATUS_AT_CAPTURE" >"$EVIDENCE/status-at-capture.json"

SEMEMBED_IMAGE=$(compose config --format json | jq -r '.services.semembed.image')
SEMEMBED_CID=$(compose ps -q semembed)
SEMEMBED_MODEL=$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$SEMEMBED_CID" |
	awk -F= '$1 == "SEMEMBED_MODEL" { print $2 }')
SEMEMBED_IMAGE_ID=$(docker inspect --format '{{.Image}}' "$SEMEMBED_CID")
SEMSOURCE_IMAGE_ID=$(docker inspect --format '{{.Image}}' "$(compose ps -q semsource)")
DOCKER_ARCH=$(docker info --format '{{.Architecture}}')

jq -n \
	--arg project "$PROJECT" --arg family "$FAMILY" \
	--slurpfile fam "$WORK/family.json" \
	--arg corpus_repo "$CORPUS_REPO" --arg corpus_commit "$CORPUS_COMMIT" \
	--arg semsource_commit "$SEMSOURCE_COMMIT" --argjson semsource_dirty "$SEMSOURCE_DIRTY" \
	--arg semstreams_version "$SEMSTREAMS_VERSION" \
	--arg semselect_commit "$SEMSELECT_COMMIT" --argjson semselect_dirty "$SEMSELECT_DIRTY" \
	--arg semembed_image "$SEMEMBED_IMAGE" --arg semembed_model "$SEMEMBED_MODEL" \
	--arg semembed_image_id "$SEMEMBED_IMAGE_ID" --arg semsource_image_id "$SEMSOURCE_IMAGE_ID" \
	--arg semembed_cpus "${SEMEMBED_CPUS:-2}" --arg log_level "${LOG_LEVEL:-info}" \
	--arg docker_os "$(docker info --format '{{.OperatingSystem}}')" \
	--arg docker_version "$(docker info --format '{{.ServerVersion}}')" --arg docker_arch "$DOCKER_ARCH" \
	--argjson docker_ncpu "$(docker info --format '{{.NCPU}}')" \
	--argjson docker_mem "$(docker info --format '{{.MemTotal}}')" \
	--arg host_cpu "$(sysctl -n machdep.cpu.brand_string 2>/dev/null || uname -m)" \
	--argjson status "$STATUS_AT_CAPTURE" \
	--slurpfile milestones "$MILESTONES" \
	'{
		provenance: "legacy SemStreams capture; not a SemEngine result",
		compose_project: $project,
		family: {id: $family, split: $fam[0].family.split, role: $fam[0].family.role, repo: $corpus_repo,
			commit: $corpus_commit, license: $fam[0].family.license,
			workspace_sha256: $fam[0].family.workspace_sha256, files: ($fam[0].family.files | length),
			tier0_entities_total: $fam[0].family.entities_total},
		config: $fam[0].config,
		semsource: {commit: $semsource_commit, dirty_paths: $semsource_dirty, image_id: $semsource_image_id,
			semstreams_version: $semstreams_version, log_level: $log_level},
		semembed: {image: $semembed_image, image_id: $semembed_image_id, model: $semembed_model, cpus: $semembed_cpus},
		semselect: {commit: $semselect_commit, dirty_paths: $semselect_dirty},
		docker: {os: $docker_os, server_version: $docker_version, architecture: $docker_arch,
			ncpu: $docker_ncpu, mem_total_bytes: $docker_mem},
		host_cpu: $host_cpu,
		status_at_capture: $status,
		milestones: $milestones
	}' >"$WORK/run.json"

# --- Structural freeze: waits for a settled whole-graph partition first.
BASELINE=$(jq -c .config.structural_baseline "$WORK/family.json")
milestone structural_start
"$WORK/bin/structural" \
	-nats "$NATS_URL" -output "$EVIDENCE/structural" \
	-settle "${PARTITION_SETTLE_SECONDS}s" -settle-timeout "${PARTITION_SETTLE_TIMEOUT_SECONDS}s" \
	-include-siblings="$(jq -r .include_siblings <<<"$BASELINE")" \
	-include-system-peers="$(jq -r .include_system_peers <<<"$BASELINE")" \
	-sibling-weight "$(jq -r .sibling_weight <<<"$BASELINE")" -max-siblings "$(jq -r .max_siblings <<<"$BASELINE")" \
	-system-peer-weight "$(jq -r .system_peer_weight <<<"$BASELINE")" -max-system-peers "$(jq -r .max_system_peers <<<"$BASELINE")" \
	-run-metadata "$WORK/run.json" \
	2>&1 | tee "$WORK/structural.log" || fail "structural capture failed: $(tail -n 5 "$WORK/structural.log")"
milestone structural_done "$(jq -c '{entities: .entities.total, embedded: .entities.embedded, explicit_edges: .explicit.edges, voting_edges: .voting.edges, level0_communities: (.partition.levels[] | select(.level == 0) | .communities), checks}' "$EVIDENCE/structural/structural.json")"

# --- Mutual-kNN candidates (the legacy SemanticEdgeProvider replay).
milestone mutualknn_start
"$WORK/bin/mutualknn" \
	-nats "$NATS_URL" -output "$EVIDENCE/mutualknn" \
	-corpus-repo "$CORPUS_REPO" -corpus-commit "$CORPUS_COMMIT" \
	-semsource-commit "$SEMSOURCE_COMMIT" -semstreams-version "$SEMSTREAMS_VERSION" \
	-semembed-image "$SEMEMBED_IMAGE" -semembed-model "$SEMEMBED_MODEL" \
	-config-path "$CONFIG_DIR/$FAMILY.tier1.json" \
	-compose-file "$SEMSOURCE_DIR/docker-compose.yml" -compose-override "$HERE/compose.tier1.yml" \
	-docker-arch "$DOCKER_ARCH" -run-metadata "$WORK/run.json" \
	2>&1 | tee "$WORK/mutualknn.log" || fail "mutualknn failed: $(tail -n 5 "$WORK/mutualknn.log")"
milestone mutualknn_done

# The graph must not have moved underneath the captures: status after the
# sweeps must equal the status that gated them, and both tools must have seen
# the same ENTITY_STATES count.
curl -fsS --max-time 5 "$STATUS_URL" >"$EVIDENCE/status-after-capture.json" ||
	fail "could not read status after capture"
graph_check=$(jq -cn --argjson before "$STATUS_AT_CAPTURE" \
	--slurpfile after "$EVIDENCE/status-after-capture.json" \
	--slurpfile summary "$EVIDENCE/mutualknn/summary.json" \
	--slurpfile structural "$EVIDENCE/structural/structural.json" '
	def sig: {total_entities, embedding_ready: .embedding.ready,
		embedding_indexed_revision: .embedding.indexed_revision,
		embedding_target_revision: .embedding.target_revision, embedding_revision: .embedding.revision};
	{before: ($before | sig), after: ($after[0] | sig), mutualknn_total_entities: $summary[0].total_entities,
		structural_total_entities: $structural[0].entities.total}
	| .unchanged = (.before == .after and .after.embedding_ready == true
		and .before.total_entities != null and .before.embedding_indexed_revision != null
		and .before.total_entities == .mutualknn_total_entities
		and .before.total_entities == .structural_total_entities)') ||
	fail "could not compare status before and after capture"
milestone graph_unchanged_check "$graph_check"
[[ $(jq -r .unchanged <<<"$graph_check") == true ]] || fail "graph moved during capture: $graph_check"

cp "$WORK/compose.resolved.yml" "$EVIDENCE/compose.resolved.yml"
directed_bytes=$(wc -c <"$EVIDENCE/mutualknn/directed.jsonl" | tr -d ' ')
if ((directed_bytes >= DIRECTED_RAW_LIMIT_BYTES)); then
	gzip -9 "$EVIDENCE/mutualknn/directed.jsonl"
	echo "directed.jsonl is $directed_bytes bytes; kept gzip only"
fi

# Per-family roll-up for the fixture freeze.
jq -n --slurpfile fam "$WORK/family.json" --slurpfile s "$EVIDENCE/structural/structural.json" \
	--slurpfile m "$EVIDENCE/mutualknn/summary.json" --arg evidence "${EVIDENCE#"$REPO_ROOT"/}" '
	{
		provenance: "legacy SemStreams capture; not a SemEngine result",
		family: $fam[0].family.id, split: $fam[0].family.split, commit: $fam[0].family.commit,
		workspace_sha256: $fam[0].family.workspace_sha256, evidence: $evidence,
		tier0_entities_total: $fam[0].family.entities_total,
		entities: $s[0].entities.total, embedded: $s[0].entities.embedded,
		explicit_edges: $s[0].explicit.edges, explicit_undirected_pairs: $s[0].explicit.undirected_pairs,
		explicit_indexes_consistent: $s[0].explicit.indexes_consistent,
		voting_edges: $s[0].voting.edges, voting_by_tier: $s[0].voting.by_tier,
		partition_level0: ($s[0].partition.levels[] | select(.level == 0)),
		partition_hash: $s[0].partition.hash, structural_checks: $s[0].checks,
		structural_baseline: $fam[0].config.structural_baseline,
		mutualknn: {k: $m[0].parameters.k, threshold: $m[0].parameters.threshold,
			queried: $m[0].queried, failed: $m[0].failed, directed_pairs_at_threshold: $m[0].directed_pairs_at_threshold,
			mutual_pairs: $m[0].mutual_pairs, explicit_dominated: $m[0].mutual_pairs_explicit_dominated,
			review_candidates: $m[0].mutual_pairs_review_candidates, cross_type: $m[0].mutual_pairs_cross_type,
			lower_bound: $m[0].mutual_pairs_is_lower_bound}
	}' >"$EVIDENCE/summary.json"

OUTCOME=ok
jq . "$EVIDENCE/summary.json"
