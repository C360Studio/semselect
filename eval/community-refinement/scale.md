# Scale: candidates per cycle and refresh cadence

**Legacy SemStreams evidence, measured 2026-10-09. Not a SemEngine result.**
Issue [#5](https://github.com/C360Studio/semselect/issues/5) asks for the real
scale before any pilot inference: candidates per cycle and refresh cadence on a
representative SemSource graph, so that required decisions per second can be
compared with the laptop's measured rate. This note records what a cycle is in
the legacy runtime, what the recorded real graphs bound, what the legacy
capture measured, and the resulting verdict. Every number below was taken from
the pinned SemStreams source or from the legacy stack; the SemEngine port must
confirm the provider chain before any of it is quoted as SemEngine behavior.

## What a cycle is in the legacy runtime

Read from the local SemStreams checkout at `1b1accf4`:

| Fact | Source |
| --- | --- |
| Community detection runs on a ticker, `detection_interval`, default 30 s | `processor/graph-clustering/component.go:412-413`, ticker at `:1452` |
| Detection also fires when `batch_size` graph events accumulate, default 100 | `processor/graph-clustering/component.go:416-417` |
| Semantic candidates are mutual-kNN pairs: per-direction top `k=8`, similarity threshold `0.75`, edge weight `0.9` | `graph/clustering/semantic_edge_provider.go:58,61,66`, `computeMutual` at `:890` |
| The directed query is `graph.embedding.query.similar` with `{entity_id, limit}`; the threshold is applied client-side | `processor/graph-clustering/similarity.go:21,77-105` |
| An explicit edge dominates a pair; semantic weight only applies to pairs with no explicit edge | `graph/clustering/semantic_edge_provider.go:182-197` |
| The mutual-kNN cache is keyed by the embedding index watermark: an unchanged watermark reuses every directed set and issues zero queries; an advance triggers an O(N) re-query rebuild | `processor/graph-clustering/component.go:1557-1576`, `metrics.go:137-147` |

Two consequences for the reviewer workload:

- **An unchanged graph produces zero new candidates per cycle.** The 30 s tick
  is not the review cadence. Review load is driven by embedding churn.
- **Any watermark advance recomputes the whole candidate set.** Pairs that were
  already decided must be recognized by content, or every churn cycle re-asks
  everything. The protocol's decision cache keyed by endpoint content,
  neighborhood and revision is therefore required, not optional.

### SemSource has never exposed the semantic tier

SemSource's graph configuration carries `enable_clustering`, `clustering_llm`
and `entity_id_edges` only (`semsource/config/config.go:53-65`), and the
clustering component config it builds passes only those
(`semsource/cmd/semsource/run.go:898-917`). SemStreams reads the tier from a
separate `semantic_edges` block (`processor/graph-clustering/component.go:718`)
that SemSource never writes, and SemStreams' own ADR-086 ships the tier
default-off with a single ~74-entity e2e corpus as its only measurement. No
SemSource deployment has produced semantic virtual edges. Every recorded real
graph below ran with the tier off, so the candidate count had to be measured by
reproducing the provider's query externally (the legacy capture tool in
[`legacy-capture/`](legacy-capture/README.md)), or it waits for the SemEngine port.

## Recorded real SemSource graphs

All legacy SemStreams runs, semantic tier off. Sources are in the SemSource
repository.

| Corpus | Entities | Embedded | Record |
| --- | --- | --- | --- |
| osh-core `sensorhub-core` + README (Java), SemSource `d21752c`, SemStreams `beta.159` | 6,685 | not recorded | `docs/testing/tier-baselines.md` |
| Three-repo corpus (osh-core, ogcapi-connected-systems, meshtastic) | 12,798 | not recorded | `docs/testing/tier-baselines.md` |
| SemSource dogfood (Go), tier 1 wired to semembed `arctic-embed-s` | 21,648 | 8,096 (`embeddings_generated_total`) | `docs/upstream/semstreams-asks.md:342` |
| OSH scale run, `beta.160`, 2-CPU semembed | 32,157 | not recorded | `docs/upstream/semstreams-asks.md:1019` |

**Upper bound.** With `k=8` each entity contributes at most eight directed
neighbors, and a mutual pair consumes one directed slot at both ends, so mutual
pairs are at most `4 × embedded entities`. The bound ignores the threshold and
mutuality, so the real count is lower; the dogfood run shows the embedded
fraction can be well under half of the entity count.

| Corpus | Embedded entities (bound basis) | Mutual pairs, upper bound |
| --- | --- | --- |
| osh-core `sensorhub-core` | ≤ 6,685 | ≤ 26,740 |
| three-repo corpus | ≤ 12,798 | ≤ 51,192 |
| SemSource dogfood | 8,096 | ≤ 32,384 |
| OSH scale | ≤ 32,157 | ≤ 128,628 |

## Measured on the legacy stack

The [legacy capture tool](legacy-capture/README.md) reproduced the provider's
query against a live legacy tier-1 stack (SemSource `4093d3c`, SemStreams
`v1.0.0-beta.160`, semembed `Snowflake/snowflake-arctic-embed-s`, shipped
`mvp.json`) over the osh-core `sensorhub-core` corpus at commit `9a43f9e`.
Evidence: [`docs/evidence/20261009T163957Z-legacy-capture-osh-mutualknn/`](../../docs/evidence/20261009T163957Z-legacy-capture-osh-mutualknn/).
One corpus, one run, one machine (Apple M3 Pro, Docker Desktop, aarch64); it is
a measurement, not a benchmark.

| Quantity | Value |
| --- | --- |
| Entities / embedded | 6,689 / 6,689 (every entity answered; 0 failures, 0 retries) |
| Directed neighbours returned / at or above 0.75 | 53,512 / 53,356 |
| Entities filling all 8 slots | 6,655 of 6,689 |
| Mutual pairs | 15,578 |
| Explicit-dominated mutual pairs | 1,086 |
| **Review candidates (mutual, not explicit-dominated)** | **14,492** (2.17 per entity) |
| Entities with at least one mutual edge / mean mutual degree | 6,443 / 4.66 |
| Mutual pairs by lower similarity: [0.75,0.80) / [0.80,0.85) / [0.85,0.90) / [0.90,0.95) / [0.95,1.00] | 36 / 835 / 4,513 / 5,946 / 4,248 |
| Cross-type mutual pairs | 1,825 (method–method 8,327 and var–var 3,251 dominate) |
| Stack: containers healthy / `index.ready` / `embedding.ready` | 12 s / 22 s / 201 s after `compose up` |
| Query sweep | 27 s; p50 31 ms, p95 40 ms per request |

Two readings:

- **The 0.75 threshold is inert on this embedder and corpus.** It removed 156
  of 53,512 directed neighbours. The candidate count is set by `k=8`. A
  threshold that bites sits at 0.90 (removes 35% of mutual pairs) or 0.95
  (removes 73%); that is a configuration lever with its own quality cost, not a
  model property, and the protocol's "distance from 0.8" priority key selects
  the thin low-similarity tail of this distribution.
- **Cross-system is 0 by construction**: one source root, one `system`.


## Required rate against the measured ceiling

The [throughput record](../../docs/validation-throughput.md) fixes the laptop
rate: about one decision per second with fresh evidence per request (0.94 to
1.06 at one slot), 1.01 to 1.39 questions per second for three-question bundles,
and on Kev's own MLX server 1,970 ms for a new state plus about 365 ms per
further question on that state. A one-state, four-question bundle is therefore
about 3.1 s, or 1.3 decisions per second. The ceiling is roughly **3,600 to
4,700 decisions per hour**, whichever model.

Three regimes follow from the cycle facts above:

| Regime | Candidates needing a decision | Rate required |
| --- | --- | --- |
| First build over a module | every non-dominated mutual pair (measured above) | pairs ÷ seed window; the seed of 6,685 entities reached semantic-ready in about 5 minutes on a Mac mini |
| Unchanged graph | 0 | 0 |
| Churn (commit, re-embed) | new pairs only, about re-embedded entities × mean mutual degree, if decisions are cached by content | new pairs ÷ 30 s if review must land within one detection tick |

### Verdict

| Scenario | Decisions | Required rate | Against the ceiling (about 1 to 1.3 per second) |
| --- | --- | --- | --- |
| Keep up with the seed: 14,492 candidates inside the 201 s embedding window | 14,492 | 72 per second | about 55 to 70 times too slow |
| Full build as a batch, Qwen JSON or Kev fresh packets | 14,492 | at 1 per second | about 4.0 hours |
| Full build as Kev per-entity bundles, four questions per state (3,623 states at 1.97 s plus 10,869 follow-ups at 0.365 s) | 14,492 | at 1.3 per second | about 3.1 hours |
| Hourly full refresh of this one module (the protocol's framing) | 14,492 per hour | 4.0 per second | about 4 times too slow |
| Churn: one saved file, about 12.6 entities × 4.66 mutual pairs | about 59 | within one 30 s tick | about 1 minute at the ceiling, two ticks |
| Unchanged graph | 0 | 0 | free, if decisions are cached by pair content and revision |
| Pilot: 12 families × at most 32 reviews | 384 per model | none | about 6.5 minutes per model; a family cycle about 32 s |

**The pilot runs on this laptop.** Its cycles are capped at 32 reviews and the
whole development plus held-out sweep is minutes of inference per model.

**The workload the pilot would justify does not run synchronously on this
laptop at a real module's scale.** A single 6,689-entity module produces 14,492
review candidates on first build, three to four hours of decisions at the
measured ceiling, against an embedding seed of 201 s. Steady state is feasible
only if review is asynchronous to the 30 s detection tick, decisions are cached
by pair content and revision, and the partition is allowed to run on unreviewed
semantic edges (or without them) while review lags by about a minute per
changed file. At the pilot's family size the same density gives roughly 430
candidates per 200-entity family, so a 32-review cycle covers about 7% of them;
the protocol's priority rule, not the model, decides which 7%.

This is the laptop's number. Datacenter hardware changes the rate, not the
quality question, and the quality question is what the pilot measures.

