# Actual SemStreams query classifier driver

This isolated evaluation CLI imports `github.com/c360studio/semstreams/graph/query`
at `v1.0.0-beta.160`. It does not reproduce its rules in Python, change semselect's
service, or run a model. [Source pins](source-pins.json) record the cached module
hashes; these classifier sources match the inspected local SemStreams files.

Two arms use the same production chain:

| Arm | What it does |
| --- | --- |
| `keyword` | Run the actual regular expressions, extracting options such as path start node, count, time range, or ranking limit. If no explicit option matches, return the chain's empty default. |
| `keyword_bm25` | Run those same rules first. Only if they find no explicit option, compare the query against supplied development examples using BM25-weighted vectors, feature hashing into 384 dimensions, and cosine similarity. Above the supplied threshold, copy the best example's intent and options. |

For example, `devices connected to sensor-007` sets `path_intent=true` and
`path_start_node="sensor-007"`. In contrast, an example matcher taught
`equipment roster` with `path_start_node="pump-17"` can copy **pump-17** when asked
`equipment roster pump-99`. The matcher does not extract a replacement entity
from the new question. These are synthetic development probes in the tests,
not held-out accuracy measurements.

The optional BM25 tier is not enabled by the audited graph-query call site,
which constructs the chain with no example classifier. It is a separately
available existing algorithm worth testing. No learned embeddings or LLM fallback
are installed by this driver.

## Input and execution

Input is one JSON object in a regular file. There are no gold labels or scoring
fields in the input schema. `examples` uses the upstream `DomainExamples` format;
its `intent` values are demonstration labels, not held-out gold.

```json
{
  "arm": "keyword_bm25",
  "threshold": 0.2,
  "examples": {
    "domain": "synthetic-development",
    "version": "1",
    "examples": [
      {"query": "equipment roster", "intent": "path", "options": {"path_intent": true, "path_start_node": "pump-17"}}
    ]
  },
  "queries": [{"id": "development-1", "text": "devices connected to sensor-007"}]
}
```

The illustrative threshold above is not an empirically selected setting.
Freeze the examples, threshold, query order, task contract, and scoring policy
before collecting held-out results. Each process constructs a fresh classifier,
then classifies every query exactly once in the supplied order.

From this directory, using already cached dependencies:

```sh
GOTOOLCHAIN=local GOPROXY=off GOSUMDB=off GOCACHE=/tmp/semselect-query-routing-gocache go test -mod=readonly -race ./...
GOTOOLCHAIN=local GOPROXY=off GOSUMDB=off GOCACHE=/tmp/semselect-query-routing-gocache go build -mod=readonly -o /tmp/semselect-query-routing-driver .
TZ=UTC /tmp/semselect-query-routing-driver --input /tmp/routing-development.json --output /tmp/routing-development-results.jsonl --timeout 30s
```

The module graph and sums start from the existing synthesis driver, without any
change to that driver. Offline `go mod tidy` currently reaches uncached
transitive dependency tests (`grpc-gateway/v2`); test/build use the existing graph
with `-mod=readonly`, requiring no dependency download.

## Results and limits

Output begins with a `kind="provenance"` JSONL record containing the exact input
SHA-256, arm, threshold, counts, start time, timezone, timeout, module version,
Go build information, and `setup_duration_ms` for the actual classifier
constructor. Subsequent `kind="classification"` records preserve
the native `ClassificationResult` fields (`Tier`, `Intent`, `Options`,
`Confidence`) plus query ID/text, status, duration, input SHA, and the same batch
setup duration. The driver
does not call `InferStrategy`, infer a route from options, normalize scores,
or reinterpret empty results as an explicit upstream abstention.

The driver targets Darwin/Linux. Its input open uses nonblocking Unix flags so
named pipes are rejected without waiting for a writer; Windows has not been
implemented or validated here.

- Keyword matches and the chain's empty fallback both report confidence `1`.
  That number is not calibrated correctness. Empty options mean no match, not
  successful semantic understanding.
- The BM25 query adapter calls `Generate`, which updates corpus statistics.
  Earlier queries can affect later scores, while stored example vectors retain
  their original weights. A fresh process and recorded order are necessary;
  repeated queries within a batch are not independent repeats.
- Relative time rules use the upstream wall clock and local timezone. Use
  `TZ=UTC`, preserve timestamps, and score time windows against execution time;
  byte-identical time-range results across runs are not promised.
- Per-query duration covers the single classifier call. It excludes example
  vector construction, process startup, and disk persistence. Constructor time,
  including example vector construction, is recorded once as `setup_duration_ms`
  and repeated unchanged on each row for reference; do not sum it across rows.
  The experiment wrapper separately measures cold process wall time. These
  timings do not by themselves establish service-level latency.
- Unknown struct fields, malformed or trailing JSON, invalid thresholds,
  duplicate IDs, and empty text are rejected before constructing a classifier.
  Options remain the upstream free-form map. Bounds: 1 MiB batch, 1–1024 queries,
  at most 256 examples, 8192-byte query/example text and example options,
  128-byte IDs/intents/domain/version, and 32 MiB output.
- Output is created with `O_EXCL` and each record is synced. Existing evidence
  is never overwritten. Cancellation/deadline errors produce a nonzero exit;
  completed rows and an in-flight row's returned result remain on disk. A row
  interrupted during classification is marked `canceled`, even if it returned
  a native value. Unstarted rows are absent. The upstream keyword and BM25
  adapter internals are not fully context-aware; cancellation is enforced at
  driver boundaries, with bounded input limiting intervening work.
  Cancellation during construction preserves the provenance header with setup
  duration once the constructor returns, exits nonzero, and runs no queries.

Tests exercise real rules, native fallback, BM25 option copying, keyword
precedence, strict batch validation, evidence preservation, and explicit
synchronization of cancellation during the final row. They do not establish
held-out accuracy or comparative model benefit.
