# Actual SemStreams answer-synthesis driver

This isolated evaluation module imports SemStreams **v1.0.0-beta.160** and calls
`graphquery.NewLLMAnswerSynthesizer` with the actual `graph/llm.NewOpenAIClient`.
It does not reproduce the production prompt or change production packages.
[source-pins.json](source-pins.json) identifies the tag commit and implementation hashes.

Input is JSONL, one captured query result per line:

```json
{"id":"capture-id","query":"user question","summaries":[],"total_entities":0}
```

`summaries` must contain the actual exported `CommunitySummary` JSON values from
the captured retrieval path, including summary, keywords, representative entities,
member count and relevance when present. The driver does not retrieve, reconstruct,
rank or label summaries. Authored values under `testdata/` are **offline tests only**.
Extra top-level fields, including gold labels, are rejected. Empty summaries retain
the production behavior: no model call and an empty outcome.

Build and verify using cached dependencies only, from the repository root:

```sh
GOCACHE=/tmp/semselect-synthesis-go-cache GOPROXY=off GOSUMDB=off GOTOOLCHAIN=local \
  go -C eval/synthesis/driver test -race -mod=readonly ./...
GOCACHE=/tmp/semselect-synthesis-go-cache GOPROXY=off GOSUMDB=off GOTOOLCHAIN=local \
  go -C eval/synthesis/driver build -trimpath -mod=readonly \
  -o /tmp/semselect-synthesis-driver .
```

The SDK tests start only local stub HTTP servers; they do not contact an inference
backend. `go mod tidy` also considers dependency test packages and encounters an
uncached grpc-gateway module in this environment. The commands above compile/test
the actual driver import graph offline; no dependency download is needed.

Capture the **actual selected/truncated model-visible prompt** before classifying it:

```sh
/tmp/semselect-synthesis-driver --input captured-summaries.jsonl \
  --model ACTUAL-SERVED-ALIAS --capture-only --output prompt-capture.jsonl
```

Capture mode runs the actual synthesizer through a recording `llm.Client`, makes no
HTTP call, and omits the dummy synthesis outcome. Give a gate the exact captured
messages/evidence; raw retrieved passages are not interchangeable with production
community summaries. Source IDs, unused clusters and keywords may remain in the
input record even when the actual prompt builder excludes them.

Replay through an already-running local backend:

```sh
/tmp/semselect-synthesis-driver --input captured-summaries.jsonl \
  --model ACTUAL-SERVED-ALIAS --base-url http://127.0.0.1:PORT/v1 \
  --output answers.jsonl
```

The backend launcher and controlled input/gold freeze belong to the caller. The
CLI only accepts HTTP loopback endpoints. It starts no model, container or service.
It preserves the default production **15-second synthesis and HTTP deadlines**,
**temperature 0.3**, **500-token maximum**, and actual client retry policy:
three retries, at most four attempts under the same deadline. It adds no seed,
JSON schema, cache, thinking or reasoning override. Provider defaults still matter.

Each flushed output row preserves the original typed input, actual `ChatRequest`,
message view, actual `ChatResponse` (when available), client errors,
`SynthesisOutcome`, duration and HTTP trace events. A model failure may return a
nonempty **degraded template fallback** with no top-level synthesis error; grade that
outcome as returned rather than treating every string as a successful model answer.
The CLI's success means the batch was recorded, not that synthesis was nondegraded
or factually correct. On cancellation, completed rows remain and the process exits
nonzero; compare completed IDs with the input before reporting completeness.

`requests_written` counts `httptrace.WroteRequest` callbacks. Trace events record
connection reuse, write errors and first response bytes; they do **not** capture raw
HTTP bodies, individual HTTP statuses, or prove backend acceptance. The wrapper
records only the final typed response/error. The offline SDK test verifies the exact
serialized request body and four-attempt retry behavior against a local recorder.
The caller may add a separate raw capture proxy if per-attempt bodies are necessary.

A companion `.provenance.json` contains the complete input hash, model/endpoint,
protocol and Go build/module information. Output and provenance paths must be new;
existing evidence is never overwritten. Build artifacts do not belong in Git.
