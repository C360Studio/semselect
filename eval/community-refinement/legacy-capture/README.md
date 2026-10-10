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
| `refreshCache` asks `graph.embedding.query.similar` with `{"entity_id", "limit": k}` per `ENTITY_STATES` entity, 30 s timeout | Same request per entity in both `ENTITY_STATES` and `EMBEDDING_INDEX`, `k=8`, 30 s timeout, bounded concurrency |
| `parseSimilarResponse` keeps `similarity >= 0.75` | Same inclusive client-side threshold |
| `refreshCache` drops self-edges; directed sets are sets | Self and duplicate IDs dropped, reply order kept, capped at `k` |
| `computeMutual` intersects directed sets | Verbatim copy of `computeMutual` |
| An `embedding_unavailable` reply is a definitive empty | Recorded as `embedding_unavailable`, not retried, counts as answered |
| Explicit edges dominate (`kvProvider` "both" = outgoing ∪ incoming) | `explicit_dominated` if either endpoint's `OUTGOING_INDEX` row names the other |

Skipping `ENTITY_STATES` entities without an embedding is equivalent to the
provider's sweep: the handler only ranks entities with generated embeddings,
so such an entity cannot appear in any directed set. An `EMBEDDING_INDEX` key
absent from `ENTITY_STATES` is an orphan the provider never queries. The tool
reports orphans as `embedded_not_in_entity_states` and exits non-zero before
the sweep when there are any, unless `-allow-orphan-embeddings` is set; they
are then reported and left unqueried.

Differences from the in-process provider, stated so the numbers are not
over-read:

- Transient NATS failures are retried at most twice; the sweep aborts once
  failures exceed 10% of the `ENTITY_STATES` count, mirroring the provider's
  coverage threshold and its denominator. The provider instead re-queries
  failed entities next cycle.
- A failed or unqueried entity has no directed set, so any mutual pair
  through it is missing: `mutual_pairs_is_lower_bound` is then true.
  `directed_edges_to_unanswered` counts directed edges from answered entities
  to unanswered ones, an upper bound on pairs lost with one answered endpoint.
  A pair between two unanswered entities cannot be observed.
- A reply naming a different `entity_id`, an oversized reply or a parse error
  is a failure, never an empty set.
- The explicit-edge check reads `OUTGOING_INDEX` for both endpoints instead of
  `OUTGOING_INDEX ∪ INCOMING_INDEX` for one. These agree whenever the two
  indexes are consistent; the capture does not verify that. A row with an
  entry lacking `to_entity_id` is rejected whole, as the provider's
  `getNeighborsFromBucket` does; its predicate and entity-ID validation are
  not replicated.
- If any mutual pair stays unresolved, or the run is interrupted after the
  sweep, the outputs are still written with the unknown counts null and the
  tool exits non-zero.
- It records candidates only. The provider's edge weight (0.9), structural
  tiers and label propagation are not run.

The published osh capture
(`docs/evidence/20261009T163957Z-legacy-capture-osh-mutualknn/`) predates the
30 s default and the `ENTITY_STATES` denominator: it ran with a 10 s
per-request timeout (`parameters.timeout_ms: 10000` in its `summary.json`) and
a failure budget over the embedded count. Neither changes its numbers. It had
zero failures and `embedded_not_in_entity_states: 0`, so the overlap sweep
covers the same 6,689 entities, and the fields added since
(`embedded_in_entity_states`, `directed_edges_to_unanswered`,
`mutual_pairs_is_lower_bound`) would read 6,689, 0 and false. Its
`status-after-capture.json` matches its status at capture.

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
   not true within 30 minutes of healthy containers, or `nats`, `semembed` or
   `semsource` stops running, it stops and records the failure.
4. Runs `cmd/mutualknn` into
   `docs/evidence/<UTC timestamp>-legacy-capture-osh-mutualknn/`, then reads
   the status again. Unless the entity count, `embedding.ready` and the
   embedding revisions equal the status that gated the capture, and that count
   equals the tool's `total_entities`, the run fails.
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

Provenance flags are required. `-output` must not exist and its parent must;
a run that fails before writing anything removes it. `-timeout` (30 s) bounds
one similar request and `-list-timeout` (120 s) bounds listing one bucket.

Offline checks: `gofmt -l .`, `go vet ./...` and `go test -race ./...` from
this directory. Unit tests cover the pure functions and the retry and
classification policy with a fake requester. End-to-end tests run the tool
against an embedded `nats-server` with seeded KV buckets and a scripted
similar responder. Neither involves Docker, SemSource or a model.

## Outputs

| File | Contents |
| --- | --- |
| `summary.json` | Counts, mutual-pair breakdowns, degree and similarity histograms, latency and wall-clock time |
| `manifest.json` | Corpus, SemSource, SemStreams and semembed provenance, file hashes, machine, timestamps and run metadata |
| `directed.jsonl` | One line per embedded entity: status, attempts, latency, the raw `similar` reply in order and the thresholded directed set (gzip if 20 MB or larger) |
| `mutual_pairs.jsonl` | One line per mutual pair: both similarities and ranks, system/type of each endpoint and `explicit_dominated` |
| `milestones.json`, `run.json` | Readiness milestones, outcome and the status payload at capture |
| `status-after-capture.json` | Status after the sweep; the `graph_unchanged_check` milestone records its comparison with the status at capture |

`directed.jsonl` keeps every returned neighbour, including those below the
threshold, so any other threshold, or any k up to 8, can be evaluated offline
without rebuilding the stack.

This is one corpus, one run on one machine. It is not a benchmark, and the
similarity values are not calibrated probabilities.

## Structural freeze (`cmd/structural`)

`cmd/structural` freezes the structural side of one family capture from the
same live legacy stack, read-only (protocol freeze step 1, used per family by
`eval/community-refinement/fixtures/tier1-capture/run-family.sh`). It reads
five KV buckets and reproduces, outside the stack, what the legacy clustering
provider chain (`kvProvider -> EntityIDProvider`, SemStreams `v1.0.0-beta.160`)
hands the LPA vote:

| Legacy behavior | Reproduction |
| --- | --- |
| `kvProvider.GetAllEntityIDs` lists `ENTITY_STATES` | Same; sorted |
| `bothNeighborSet(A)` = `OUTGOING_INDEX(A)` targets ∪ sources of `INCOMING_INDEX` keys `A.<source>.<hex predicate>`; a row with an entry lacking `to_entity_id` is rejected whole | Same; both indexes are read in full and compared, and every disagreement is reported (`outgoing_only`, `incoming_only`) |
| `EntityIDProvider.GetNeighbors`: explicit ∪ siblings (same five-part prefix, sorted, self and explicit excluded, capped) ∪ system peers (same system, sorted, self, explicit and the *listed* siblings excluded, capped) | Same, per voter (`voting_edges.jsonl`, `listed_as`) |
| `GetEdgeWeight` cascade: explicit 1.0, else sibling weight if same prefix, else system-peer weight if same system | Same, evaluated on identity, so a sibling cut by the sibling cap and listed as a system peer votes at the sibling weight (`weight_tier`) |
| `COMMUNITY_INDEX`: `{level}.{id}` holds the community, `entity.{level}.{id}` its members' back-pointers; LPA with a fixed seed is reproducible for a fixed graph | Read in full; level 0 must cover `ENTITY_STATES` exactly, be disjoint and agree with the back-pointers, and the whole bucket must be byte-identical across one settle interval (default 35 s, longer than the 30 s detection interval) before it is frozen |

The identity weights and caps default to the semantic profile's structural
baseline (`processor/graph-clustering` `semanticEnabledEntityIDBaseline`:
sibling 0.7 capped at 5, system peer 0.2 capped at 8), which the runner also
passes to SemSource as `graph.entity_id_edges`. That is the chain the protocol
names: the semantic profile's structural weights retained, semantic influence
off. SemSource cannot enable the semantic tier, so the live partition is
structural-only by construction. The structural default profile (0.7/10,
0.3/15) would give a different partition; it is not what is frozen.

Outputs: `entities.jsonl` (ID parts, embedded or not, explicit degrees, listed
identity neighbours, community per level), `explicit_edges.jsonl`,
`voting_edges.jsonl` (one line per voter and listed neighbour with tier,
weight and whether the pair crosses the level-0 partition), `partition.json`
(every stored community with its raw value, back-pointers, per-level size
histograms and the level-0 checks) and `structural.json` (counts, index
consistency, settle reads, checks, embedded run metadata). It exits non-zero
when the partition did not settle, does not cover the entities, is not disjoint
or disagrees with its back-pointers, or the graph moved while the topology was
read; outputs are still written. Index disagreement and malformed entity IDs
are reported but do not fail the run, because the capture records what the
stack served. Offline tests run the tool against an embedded `nats-server`
with seeded buckets, including a partition that appears late and one that
never settles.

## Hydration (`cmd/hydrate`)

`cmd/hydrate` dumps every `ENTITY_STATES` value verbatim and the bodies those
states reference: SemSource's producers offload each code symbol's and each
document passage's verbatim body to the `CONTENT` object store and stamp a
`code.body.store`/`code.body.key` or `source.doc.body-store`/`source.doc.body-key`
handle on the entity (ADR-062 at the pinned commit). Containers (repo, folder,
file, document) carry no body. The quality pilot's reviewer packets
(`fixtures/tier1-capture/make_packets.py`) are serialized from this dump, so a
reviewer sees the bytes the system embedded, not a re-parse of the workspace.

```
hydrate -nats nats://127.0.0.1:14222 -output <dir>/hydration [-run-metadata run.json]
```

Outputs: `entity_states.jsonl` (entity ID, byte count, sha256, triple count, the
lifted handle and the state JSON verbatim; a non-JSON value is kept as `raw`),
`bodies.jsonl` (one row per distinct body key with the entities that name it,
bytes, sha256 and the text, or base64 when the bytes are not UTF-8) and
`hydration.json` (counts, handles by predicate and store, missing or oversize
bodies, checks). It exits 1 after writing when a state does not parse, a
handle does not resolve, or a handle names a store other than `objectstore`.
`run-family.sh` runs it after the mutual-kNN replay and rolls its counts into
`summary.json`. Offline tests run against an embedded nats-server with a
`CONTENT` object store.
