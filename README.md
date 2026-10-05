# semselect

Small, local decision service for the c360studio sem* ecosystem. Callers supply
context and permissible answers; semselect returns typed model readouts. Callers
own taxonomies, routing, authorization, acceptance thresholds, fallback and execution.

The bootstrap packages **llama.cpp + Kev-4B Q4_K_M** behind a small Go request guard.
It uses the existing `POST /v1/systemone` API. It adds no model, scoring formula,
agent loop, NATS dependency or durable application state. See the
[research and decision](docs/research-and-decision.md),
[validation record](docs/validation.md) and
[proposed SemStreams integration](docs/semstreams-integration.md).

## Capabilities and target

| Primitive | Input | Native result |
| --- | --- | --- |
| Choice | Named candidates with descriptions | Selected candidate, full distribution, native confidence |
| Score | Ordered level descriptions | Expected zero-based level index, legend, distribution, native confidence |
| Noul | Yes/no proposition, optional descriptions | Probability assigned to yes |

These are actual model scores, not generated confidence text. Kev includes fitted
temperatures; that does **not** establish calibration on your data or parity with
Jev. Score need not be an integer. All probabilities are conditional on the supplied
question and candidates. Explicitly include `unknown` when the categories may not fit.

Initial supported deployment: **CPU-only Linux containers**. This bootstrap was
run on Linux/ARM64 in Docker Desktop on an Apple Silicon Mac, using CPU inference
only. It does not validate Metal, CUDA, native macOS, or Linux/AMD64 performance.
The Dockerfile can target AMD64, but that build and older CPU instruction support
remain unverified. Start with 4 CPU cores, an 8 GiB runtime memory budget, and
10 GiB free disk for model, images and build cache. The GGUF is 3.03 GB; RAM use
is higher. See measured resource limits in the validation record.

On the 48-request routing smoke, Kev returned 43 correct labels versus the
seminstruct 0.6B baseline's 24, at median latencies of **9.88 s versus 0.293 s**.
These results are too small for production conclusions. In particular, this
configuration is unsuitable for a subsecond CPU latency budget without further work.

## Quick start

Requires Docker with Compose v2, Task v3, Python 3.11+ and curl. Go 1.26.4+ is only
needed for host contract tests; Docker supplies its pinned build toolchain.

```sh
cp .env.example .env
task model:fetch
task build
task up
task test:health
task smoke
```

`model:fetch` downloads exactly the revision and SHA-256 in `models.lock.json`,
verifies size and checksum, and atomically installs it in `models/`. Reruns reuse
the verified file. Startup verifies the cache again. Download is separate from
startup: the running service needs no model download or hosted inference access.
An interrupted download can leave a `.lock` file; remove that file only after
checking no model download is running, then retry. `task down` keeps the cache.

The runtime's internal port is not published. The guarded API binds to
`127.0.0.1:8084` by default. Both containers run nonroot with read-only filesystems
and memory limits. It is a local/trusted-network service; authentication and remote
exposure belong at the caller's gateway. The guard deliberately exposes only
decisions, model identity, health and metrics.

```sh
curl --fail --max-time 125 http://127.0.0.1:8084/v1/systemone \
  -H 'Content-Type: application/json' --data-binary @examples/decision.json
```

That example exercises all three primitives. An actual smoke response included:

```json
{
  "type": "choice",
  "choice": "billing",
  "probabilities": {
    "billing": 0.796965, "technical": 0.049404,
    "account": 0.016571, "unknown": 0.137060
  },
  "confidence": 0.729286
}
```

The full response is `{model, answers: {question_id: answer}, usage}`. Numbers
above are rounded for readability, not a guaranteed prediction. Query candidate
names are examples owned by the request. See `examples/decision.json` for Score
and Noul request forms. `task smoke` checks native distributions and ordered-score
semantics against real inference; it does not certify model quality.

An illustrative **caller** abstention policy (choose thresholds on held-out data):

```python
answer = response['answers']['route']
label = answer['choice']
if label == 'unknown' or answer['probabilities'][label] < 0.7:
    fallback()  # clarify, use another classifier, or ask a person
else:
    apply_caller_policy(label)
```

This threshold is not a safety guarantee. A confident wrong label can still pass.
Do not compare this probability with SemStreams' existing heterogeneous `Confidence`
field; it currently mixes heuristics, similarity and generated confidence.

## Configuration and operational bounds

`.env` configures Compose and Task commands:

| Variable | Default | Meaning |
| --- | --- | --- |
| `SEMSELECT_PORT` | 8084 | Host loopback port; container port stays 8084 |
| `SEMSELECT_THREADS` | 4 | Runtime generation and batch threads; container CPU quota stays 4 |
| `SEMSELECT_TIMEOUT` | 120s | Upstream request deadline, positive and at most 5m |

The standalone guard also reads `SEMSELECT_UPSTREAM` (HTTP origin) and
`SEMSELECT_MODEL` (expected model alias); Compose fixes these to the packaged
runtime. Changing the model is a reviewed deployment change, not a per-request
download or routing operation.

The supported API profile is intentionally bounded: UTF-8 JSON body ≤32 KiB,
string `state` of 1–8192 bytes, 1–4 questions, question IDs ≤64 bytes, nonempty
string instructions ≤1024 bytes. Choice has 2–16 candidates (names ≤128 bytes,
string descriptions ≤512 bytes or null). Score has 2–10 nonempty string levels
≤512 bytes. Noul permits optional `true`/`false` string descriptions ≤512 bytes.
Unknown fields, duplicate keys, wrong casing and non-string structured state are
rejected. This is a subset of the upstream API, not blanket SDK conformance.
The original candidate order and native response bytes are preserved by the guard.

The runtime has one slot, 4096 context tokens, CPU layers only, and equal 512-token
batch/microbatch sizes. Byte limits do not guarantee fitting the tokenizer budget:
native over-context errors are returned, without context shifting. One admitted
inference request runs at a time; another receives 429 with `Retry-After: 1`.
HTTP read timeouts also bound slow uploads. Upstream responses are limited to 1 MiB.
Client cancellation cancels the upstream HTTP call; immediate interruption of
native model computation has not been established.

| Status | Meaning |
| --- | --- |
| 400 | Invalid profile, malformed JSON, or native context/input error |
| 413 / 415 | Body too large / unsupported content type |
| 429 | Runtime busy; callers own bounded retry/backoff |
| 502 | Invalid, oversized or redirected runtime response |
| 503 / 504 | Runtime unavailable / deadline exceeded |

Native error status/body is preserved; local errors use
`{"error":{"code":400,"message":"..."}}`.

- `GET /health`: guard liveness. `GET /ready`: loaded runtime readiness; also the
  container health check. Readiness is not a quality assessment.
- `GET /v1/models`: loaded model metadata.
- `GET /metrics`: guard request/error counts, cumulative duration and in-flight
  gauge. `GET /metrics/runtime`: native llama.cpp Prometheus metrics.
- `task logs`: JSON guard logs with status/duration, plus native runtime diagnostics.
  The guard logs no input text. Avoid enabling native verbose prompt logging for
  private input. `task stats` shows current container resource use.

## Checks and evaluation

```sh
task check          # offline contract/evaluator tests, Go race detector/vet, Compose
task smoke          # requires running model; all three native primitives
task evaluate       # 24 fixed cases × two candidate orders; results/semselect.json
task baseline:up    # optional pinned ARM64 seminstruct image, port 8083
task baseline:evaluate
task down
```

The dataset covers paraphrases, ambiguity, multiple intents, negation, out-of-scope
inputs and instruction injection. Reports include accuracy, abstention coverage
versus conditional error, label-order flips and p50/p95 latency. The seminstruct
baseline uses constrained JSON labels and has no candidate probabilities; the
evaluator does not invent them. See [evaluation instructions](eval/README.md) for
hardware/resource metadata and [measured results](docs/validation.md).

## Pins and limitations

`VERSION` identifies the semselect packaging version. Source/model revisions and
SHA-256 live in `models.lock.json`; the Dockerfile pins the llama.cpp source tarball,
Go/Ubuntu image digests and Ubuntu package snapshot `20261005T000000Z`. Changing
model, tokenizer, quantization, runtime or prompt semantics requires rerunning the
evaluation and recording a new compatibility result. Pins are reproducible inputs,
not a claim of bit-identical compiler output. Snapshots and public artifacts still
depend on upstream retention.

The native decision endpoint is new (October 2026), despite llama.cpp's broader
maturity. CPU throughput, longer contexts, larger candidate sets, multilingual
quality and domain calibration need separate evaluation. Native Kev omits Python
`date_facts` preprocessing. Multimodal and structured-state requests are deferred.
Score/Noul run but have only contract/smoke validation, not workload accuracy studies.
See [upstream licenses and attribution](THIRD_PARTY_NOTICES.md).

SemStreams needs a small typed `/v1/systemone` client to preserve distributions.
No sibling repository was modified. A future seminstruct decision-model image
variant could share or absorb this packaging; a second inference implementation
is unnecessary.
