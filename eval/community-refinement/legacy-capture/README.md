# Legacy semantic-edge capture (SemStreams, not SemEngine)

**Provenance: legacy SemStreams capture; not a SemEngine result.** Every artifact
this directory produces carries that string. SemEngine has not ported graph
clustering or embeddings (see [../README.md](../README.md)), so nothing here
measures SemEngine behavior.

## What it measures

How many semantic virtual-edge candidates the legacy SemStreams
`SemanticEdgeProvider` would generate on one full build over a real SemSource
graph. That number sizes the review workload for the community-refinement pilot
([issue #5](https://github.com/C360Studio/semselect/issues/5), "Scale number").

It does not measure community quality, clustering output, review accuracy or
any model judgment. No LLM or decision model runs.

## How it reproduces the legacy provider

`cmd/mutualknn` replays the provider's candidate generation from outside the
stack, read-only, against a live legacy tier-1 deployment:

| Legacy behavior (SemStreams `v1.0.0-beta.160`) | Reproduction |
| --- | --- |
| `refreshCache` asks `graph.embedding.query.similar` with `{"entity_id", "limit": k}` per entity | Same request per `EMBEDDING_INDEX` key, `k=8`, bounded concurrency |
| `parseSimilarResponse` keeps `similarity >= 0.75` | Same inclusive client-side threshold |
| `refreshCache` drops self-edges; directed sets are sets | Self and duplicate IDs dropped, reply order kept, capped at `k` |
| `computeMutual` intersects directed sets | Verbatim copy of `computeMutual` |
| An `embedding_unavailable` reply is a definitive empty | Recorded as `embedding_unavailable`, not retried, counts as answered |
| Explicit edges dominate (`kvProvider` "both" = outgoing ∪ incoming) | `explicit_dominated` if either endpoint's `OUTGOING_INDEX` row names the other |

Querying only embedded entities is equivalent to the provider's
all-`ENTITY_STATES` sweep: the handler only ranks entities with generated
embeddings, so an entity without one cannot appear in any directed set.

Differences from the in-process provider, stated so the numbers are not
over-read:

- Transient NATS failures are retried at most twice; the sweep aborts once
  more than 10% of entities fail, mirroring the provider's coverage threshold.
  The provider instead re-queries failed entities next cycle.
- A reply naming a different `entity_id`, an oversized reply or a parse error
  is a failure, never an empty set.
- The explicit-edge check reads `OUTGOING_INDEX` for both endpoints instead of
  `OUTGOING_INDEX ∪ INCOMING_INDEX` for one. These agree whenever the two
  indexes are consistent; the capture does not verify that.
- It records candidates only. The provider's edge weight (0.9), structural
  tiers and label propagation are not run.

## No deployment has produced these edges

The semantic tier is off unless the graph-clustering component receives
`enable_semantic_edges: true`. SemSource's `cmd/semsource/run.go` passes only
`detection_interval`, `enable_llm` and `entity_id_edges` to graph-clustering,
and no SemSource commit, in any branch, contains `enable_semantic_edges`
(`git log --all -S enable_semantic_edges` is empty at SemSource `4093d3c`).
Clustering itself is off in the shipped `mvp.json`. These candidates are
therefore a counterfactual: what the legacy tier would have proposed, not edges
any SemSource deployment has used.

## Running it

Requirements: Docker with Compose, Go, `git`, `jq`, `curl`, a SemSource checkout
next to this repository (or `SEMSOURCE_DIR`), and free host ports 8080, 4222
and 8222 (override with `SEMSOURCE_HTTP_PORT`, `NATS_HOST_PORT`,
`NATS_MONITOR_HOST_PORT`).

```bash
eval/community-refinement/legacy-capture/scripts/run-osh-capture.sh
```

The script:

1. Shallow-clones `opensensorhub/osh-core` into a new temporary directory and
   builds a workspace with only `sensorhub-core/` and `README.md`. This is the
   SemSource tier-baseline corpus (6,685 entities at SemSource `d21752c`).
2. Runs SemSource's own `docker-compose.yml` and shipped `configs/mvp.json`
   (HTTP embedder at `http://semembed:8081/v1`, Java AST and Markdown docs
   sources) unchanged, as Compose project `semselect-legacy-capture`. No
   override file is needed. It refuses to start if that project already has
   containers or volumes.
3. Polls `GET /source-manifest/status` and records `containers_healthy`,
   `phase_ready`, `index_ready` and `embedding_ready`. If `embedding.ready` is
   not true within 30 minutes of healthy containers, it stops and records the
   failure.
4. Runs `cmd/mutualknn` into
   `docs/evidence/<UTC timestamp>-legacy-capture-osh-mutualknn/`.
5. Saves the SemSource log (gzip), its graph-embedding/graph-clustering lines,
   all Compose logs (gzip), `SHA256SUMS`, and always runs `down -v`.

The tool can also run against any live legacy stack:

```bash
go build -o /tmp/mutualknn ./cmd/mutualknn
/tmp/mutualknn -nats nats://localhost:4222 -output /tmp/capture \
  -corpus-repo ... -corpus-commit ... -semsource-commit ... \
  -semstreams-version ... -semembed-image ... -semembed-model ... \
  -config-path .../mvp.json
```

Provenance flags are required. Offline checks: `gofmt -l .`, `go vet ./...`
and `go test -race ./...` from this directory. The tests cover the pure
functions and the retry and classification policy with a fake requester; no
NATS server is involved.

## Outputs

| File | Contents |
| --- | --- |
| `summary.json` | Counts, mutual-pair breakdowns, degree and similarity histograms, latency and wall-clock time |
| `manifest.json` | Corpus, SemSource, SemStreams and semembed provenance, file hashes, machine, timestamps and run metadata |
| `directed.jsonl` | One line per embedded entity: status, attempts, latency, the raw `similar` reply in order and the thresholded directed set (gzip if 20 MB or larger) |
| `mutual_pairs.jsonl` | One line per mutual pair: both similarities and ranks, system/type of each endpoint and `explicit_dominated` |
| `milestones.json`, `run.json` | Readiness milestones, outcome and the status payload at capture |
| `status-after-capture.json` | Status after the sweep, to show the graph did not change during it |

`directed.jsonl` keeps every returned neighbour, including those below the
threshold, so any other threshold, or any k up to 8, can be evaluated offline
without rebuilding the stack.

This is one corpus, one run on one machine. It is not a benchmark, and the
similarity values are not calibrated probabilities.
