#!/usr/bin/env bash
# Legacy SemStreams capture; not a SemEngine result.
#
# Builds the recorded osh-core corpus (sensorhub-core/ + README.md, the SemSource
# tier-baselines reproduction), runs SemSource's shipped tier-1 Compose stack over
# it under a dedicated project name, waits for embedding.ready, then runs
# cmd/mutualknn to reproduce the legacy SemanticEdgeProvider's mutual-kNN
# candidate generation. The stack is always torn down with `down -v` on exit.
#
# Environment overrides: SEMSOURCE_DIR (default ../semsource next to this repo),
# SEMSOURCE_HTTP_PORT (8080), NATS_HOST_PORT (4222), NATS_MONITOR_HOST_PORT (8222),
# EMBEDDING_CAP_SECONDS (1800, measured from containers healthy).
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
TOOL_DIR=$(cd "$SCRIPT_DIR/.." && pwd)
REPO_ROOT=$(git -C "$TOOL_DIR" rev-parse --show-toplevel)
SEMSOURCE_DIR=${SEMSOURCE_DIR:-$(cd "$REPO_ROOT/../semsource" && pwd)}

PROJECT=semselect-legacy-capture
CORPUS_REPO=https://github.com/opensensorhub/osh-core
SEMSOURCE_CONFIG_NAME=mvp.json
EMBEDDING_CAP_SECONDS=${EMBEDDING_CAP_SECONDS:-1800}
POLL_SECONDS=5
HTTP_PORT=${SEMSOURCE_HTTP_PORT:-8080}
NATS_PORT=${NATS_HOST_PORT:-4222}
STATUS_URL=http://127.0.0.1:${HTTP_PORT}/source-manifest/status
NATS_URL=nats://127.0.0.1:${NATS_PORT}
DIRECTED_RAW_LIMIT_BYTES=$((20 * 1024 * 1024))

TS=$(date -u +%Y%m%dT%H%M%SZ)
EVIDENCE=$REPO_ROOT/docs/evidence/${TS}-legacy-capture-osh-mutualknn

die() {
	echo "run-osh-capture: $*" >&2
	exit 1
}

for tool in docker git go jq curl gzip shasum awk; do
	command -v "$tool" >/dev/null || die "missing required tool: $tool"
done
[[ -f $SEMSOURCE_DIR/docker-compose.yml ]] || die "no docker-compose.yml under $SEMSOURCE_DIR"
[[ -f $SEMSOURCE_DIR/configs/$SEMSOURCE_CONFIG_NAME ]] || die "missing configs/$SEMSOURCE_CONFIG_NAME"
[[ ! -e $EVIDENCE ]] || die "evidence directory already exists: $EVIDENCE"

# The capture must start from an empty graph: refuse to reuse leftover state.
if [[ -n $(docker ps -aq --filter "label=com.docker.compose.project=$PROJECT") ]]; then
	die "compose project $PROJECT already has containers; run: (cd $SEMSOURCE_DIR && docker compose -p $PROJECT down -v)"
fi
if [[ -n $(docker volume ls -q --filter "label=com.docker.compose.project=$PROJECT") ]]; then
	die "compose project $PROJECT already has volumes; run: (cd $SEMSOURCE_DIR && docker compose -p $PROJECT down -v)"
fi

WORK=$(mktemp -d "${TMPDIR:-/tmp}/semselect-legacy-capture.XXXXXX")
WORKSPACE=$WORK/oshws
MILESTONES=$WORK/milestones.jsonl
: >"$MILESTONES"
STACK_STARTED=0
OUTCOME=failed
FAILURE=""

# compose runs from the SemSource checkout because its build.context is relative.
compose() {
	(cd "$SEMSOURCE_DIR" &&
		SEMSOURCE_TARGET="$WORKSPACE" SEMSOURCE_CONFIG="$SEMSOURCE_CONFIG_NAME" \
			docker compose -p "$PROJECT" -f docker-compose.yml "$@")
}

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
	jq -s --arg outcome "$OUTCOME" --arg failure "$FAILURE" \
		'{outcome: $outcome, failure: (if $failure == "" then null else $failure end), milestones: .}' \
		"$MILESTONES" >"$EVIDENCE/milestones.json"
	[[ -f $WORK/run.json ]] && cp "$WORK/run.json" "$EVIDENCE/run.json"
	[[ -f $WORK/mutualknn.log ]] && cp "$WORK/mutualknn.log" "$EVIDENCE/mutualknn.log"
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
	echo "run-osh-capture: FAILED: $FAILURE" >&2
	exit 1
}

# --- Corpus: shallow clone into a new empty directory; keep sensorhub-core + README.md.
mkdir "$WORK/clone"
git clone --depth 1 --quiet "$CORPUS_REPO" "$WORK/clone/osh-core"
CORPUS_COMMIT=$(git -C "$WORK/clone/osh-core" rev-parse HEAD)
CORPUS_COMMIT_DATE=$(git -C "$WORK/clone/osh-core" log -1 --format=%cI)
mkdir "$WORKSPACE"
cp -R "$WORK/clone/osh-core/sensorhub-core" "$WORK/clone/osh-core/README.md" "$WORKSPACE/"
JAVA_FILES=$(find "$WORKSPACE" -type f -name '*.java' | wc -l | tr -d ' ')
MD_FILES=$(find "$WORKSPACE" -type f -name '*.md' | wc -l | tr -d ' ')
ALL_FILES=$(find "$WORKSPACE" -type f | wc -l | tr -d ' ')
echo "corpus $CORPUS_REPO @ $CORPUS_COMMIT: $ALL_FILES files ($JAVA_FILES .java, $MD_FILES .md)"

# --- Provenance of the stack.
SEMSOURCE_COMMIT=$(git -C "$SEMSOURCE_DIR" rev-parse HEAD)
SEMSOURCE_DIRTY=$(git -C "$SEMSOURCE_DIR" status --porcelain | wc -l | tr -d ' ')
SEMSTREAMS_VERSION=$(awk '$1 == "github.com/c360studio/semstreams" { print $2 }' "$SEMSOURCE_DIR/go.mod")
[[ -n $SEMSTREAMS_VERSION ]] || die "could not read the semstreams version from $SEMSOURCE_DIR/go.mod"
SEMSELECT_COMMIT=$(git -C "$REPO_ROOT" rev-parse HEAD)
SEMSELECT_DIRTY=$(git -C "$REPO_ROOT" status --porcelain | wc -l | tr -d ' ')

# Build the analysis tool before the stack so a compile error costs nothing.
mkdir "$WORK/bin"
go build -C "$TOOL_DIR" -o "$WORK/bin/mutualknn" ./cmd/mutualknn

# --- Stack.
milestone build_start
STACK_STARTED=1
compose build semsource
milestone build_done
T0=$(now_s)
milestone compose_up_start
compose up -d --wait --wait-timeout 600 --build || fail "docker compose up --wait did not reach healthy"
milestone containers_healthy
HEALTHY_AT=$(now_s)

phase_ready=0 index_ready=0 embedding_ready=0 last_report=0 last_body=""
while :; do
	# Name the core services: a count would still pass with one of them down
	# whenever COMPOSE_PROFILES starts extra services (ui, caddy).
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
		if ((!embedding_ready)) && [[ $(jq -r '.embedding.ready' <<<"$body") == true ]]; then
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
		mkdir -p "$EVIDENCE" && cp "$WORK/last-status.json" "$EVIDENCE/last-status.json"
		fail "embedding.ready not true within ${EMBEDDING_CAP_SECONDS}s of containers healthy; last status: ${last_body:-<none>}"
	fi
	sleep "$POLL_SECONDS"
done
STATUS_AT_CAPTURE=$last_body

SEMEMBED_IMAGE=$(compose config --format json | jq -r '.services.semembed.image')
SEMEMBED_CID=$(compose ps -q semembed)
SEMEMBED_MODEL=$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$SEMEMBED_CID" |
	awk -F= '$1 == "SEMEMBED_MODEL" { print $2 }')
SEMEMBED_IMAGE_ID=$(docker inspect --format '{{.Image}}' "$SEMEMBED_CID")
SEMSOURCE_IMAGE_ID=$(docker inspect --format '{{.Image}}' "$(compose ps -q semsource)")
DOCKER_ARCH=$(docker info --format '{{.Architecture}}')

jq -n \
	--arg project "$PROJECT" --arg config_name "$SEMSOURCE_CONFIG_NAME" \
	--arg corpus_commit_date "$CORPUS_COMMIT_DATE" \
	--argjson java "$JAVA_FILES" --argjson md "$MD_FILES" --argjson all "$ALL_FILES" \
	--argjson semsource_dirty "$SEMSOURCE_DIRTY" \
	--arg semselect_commit "$SEMSELECT_COMMIT" --argjson semselect_dirty "$SEMSELECT_DIRTY" \
	--arg semembed_image_id "$SEMEMBED_IMAGE_ID" --arg semsource_image_id "$SEMSOURCE_IMAGE_ID" \
	--arg semembed_cpus "${SEMEMBED_CPUS:-2}" \
	--arg docker_os "$(docker info --format '{{.OperatingSystem}}')" \
	--arg docker_version "$(docker info --format '{{.ServerVersion}}')" \
	--argjson docker_ncpu "$(docker info --format '{{.NCPU}}')" \
	--argjson docker_mem "$(docker info --format '{{.MemTotal}}')" \
	--arg host_cpu "$(sysctl -n machdep.cpu.brand_string 2>/dev/null || uname -m)" \
	--argjson status "$STATUS_AT_CAPTURE" \
	--slurpfile milestones "$MILESTONES" \
	'{
		compose_project: $project,
		semsource_config: $config_name,
		semsource_dirty_paths: $semsource_dirty,
		semselect_commit: $semselect_commit,
		semselect_dirty_paths: $semselect_dirty,
		corpus: {commit_date: $corpus_commit_date, workspace: "sensorhub-core/ + README.md",
			files: $all, java_files: $java, markdown_files: $md},
		images: {semembed_image_id: $semembed_image_id, semsource_image_id: $semsource_image_id},
		semembed_cpus: $semembed_cpus,
		docker: {os: $docker_os, server_version: $docker_version, ncpu: $docker_ncpu, mem_total_bytes: $docker_mem},
		host_cpu: $host_cpu,
		status_at_capture: $status,
		milestones: $milestones
	}' >"$WORK/run.json"

milestone capture_start
"$WORK/bin/mutualknn" \
	-nats "$NATS_URL" -output "$EVIDENCE" \
	-corpus-repo "$CORPUS_REPO" -corpus-commit "$CORPUS_COMMIT" \
	-semsource-commit "$SEMSOURCE_COMMIT" -semstreams-version "$SEMSTREAMS_VERSION" \
	-semembed-image "$SEMEMBED_IMAGE" -semembed-model "$SEMEMBED_MODEL" \
	-config-path "$SEMSOURCE_DIR/configs/$SEMSOURCE_CONFIG_NAME" \
	-compose-file "$SEMSOURCE_DIR/docker-compose.yml" \
	-docker-arch "$DOCKER_ARCH" -run-metadata "$WORK/run.json" \
	2>&1 | tee "$WORK/mutualknn.log" || fail "mutualknn failed: $(tail -n 5 "$WORK/mutualknn.log")"
milestone capture_done

# The graph must not have moved underneath the capture: the entity count and
# embedding readiness/revision after the sweep must equal the status that gated
# it, and that count must equal the ENTITY_STATES count the tool listed.
curl -fsS --max-time 5 "$STATUS_URL" >"$EVIDENCE/status-after-capture.json" ||
	fail "could not read status after capture"
graph_check=$(jq -cn --argjson before "$STATUS_AT_CAPTURE" \
	--slurpfile after "$EVIDENCE/status-after-capture.json" \
	--slurpfile summary "$EVIDENCE/summary.json" '
	def sig: {total_entities, embedding_ready: .embedding.ready,
		embedding_indexed_revision: .embedding.indexed_revision,
		embedding_target_revision: .embedding.target_revision, embedding_revision: .embedding.revision};
	{before: ($before | sig), after: ($after[0] | sig), summary_total_entities: $summary[0].total_entities}
	| .unchanged = (.before == .after and .after.embedding_ready == true
		and .before.total_entities != null and .before.embedding_indexed_revision != null
		and .before.total_entities == .summary_total_entities)') ||
	fail "could not compare status before and after capture"
milestone graph_unchanged_check "$graph_check"
[[ $(jq -r .unchanged <<<"$graph_check") == true ]] || fail "graph moved during capture: $graph_check"

directed_bytes=$(wc -c <"$EVIDENCE/directed.jsonl" | tr -d ' ')
if ((directed_bytes >= DIRECTED_RAW_LIMIT_BYTES)); then
	gzip -9 "$EVIDENCE/directed.jsonl"
	echo "directed.jsonl is $directed_bytes bytes; kept gzip only"
fi

OUTCOME=ok
jq '{total_entities, embedded_entities, queried, failed, directed_pairs_at_threshold, mutual_pairs,
	mutual_pairs_explicit_dominated, mutual_pairs_review_candidates, mutual_pairs_cross_type,
	entities_with_mutual_edges, latency, wallclock_seconds}' "$EVIDENCE/summary.json"
