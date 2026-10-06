# semselect

**Help skeptical developers decide when a Jev-like decision model is worth using,
through a tested local service, reproducible comparisons, and plain-language guidance.**

semselect is a small, local decision service and evaluation project for the
c360studio sem* ecosystem. Its deliverables are a working service, a comparison
matrix with inspectable evidence, and an explanation of when each approach earns
its place. A result recommending ordinary code or schema-constrained Qwen is a
useful outcome. **Start with code and the existing retrieval/generation path;
add a classifier when a fair task comparison shows that it earns its cost.**
Our service runs Kev. These experiments do not establish the quality of Jev itself.

The bootstrap packages **llama.cpp + Kev-4B Q4_K_M** behind a small Go request guard.
It uses the existing `POST /v1/systemone` API. It adds no model, scoring formula,
agent loop, NATS dependency or durable application state. Callers supply context
and permissible answers, and own routing, authorization, thresholds, fallback and
execution. A model decision is not permission to act.

## What the evidence says

These are small, workload-specific experiments. Each link explains the task with
worked examples and points to the complete evidence; rows are not one shared
benchmark. Repeated orders/trials reuse the same cases.

| Task | Measured finding | Decision today |
| --- | --- | --- |
| [Actual SemStreams query classification](eval/query-routing/README.md) | On the primary 32-case Metal view, keyword rules and both configured BM25 arms get 18 exact; Qwen JSON and Kev each get 23. Qwen fixes 12 code errors but loses seven successes; Kev fixes 11 and loses six. | Improve ordinary rules/extraction and argument constraints first. No primary Kev accuracy advantage. |
| [Evidence sufficiency on source passages](eval/answerability/heldout/README.md) | On 24 primary cases, Kev gets 19 correct and Qwen 16. Kev still allows 5/12 unsupported inputs. Both share the same code precheck. | A useful Kev lead on this pilot, sufficient to test downstream effects; neither is a reliable production gate from this evidence. |
| [Actual captured answer-generation inputs](eval/synthesis/README.md) | On 12 usable captures, either gate reduces the 4B generator's unsupported assertions from three to zero, preserving one useful partial answer. No capture fully answers its question. | No Kev advantage observed. Improve evidence and test a balanced set before recommending a gate. |
| [Simple ticket routing](docs/results.md#2026-10-05--matched-4b-models-on-native-metal) | Matched 4B Metal comparison: Qwen JSON gets 46/48 correct; Kev gets 43/48, over 24 cases in two orders. | Keep Qwen JSON as the practical model baseline. |
| [Qwen JSON versus one-token scores](docs/validation-scoring.md) | Same Qwen model on Metal: identical labels, both 92/96 correct, with 28% lower median inference HTTP latency for scores. CPU saves 3.4% but abstains more. | A conditional format-efficiency benefit, with no demonstrated accuracy gain. |

The **code baseline differs by task**. Query classification imports the real
SemStreams regex and optional BM25 classifiers. Answerability uses a small
metadata/exact-fact precheck, then lets unresolved text through in the control;
it is not a general code-only text classifier. See [how both work](docs/when-to-use.md#what-our-code-baseline-actually-does).

Qwen JSON comparisons use llama.cpp's chat API directly. They evaluate a
seminstruct-style approach; the existing seminstruct release has not been
upgraded or validated by these runs. Semselect continues to serve Kev's native
Choice/Score/Noul readouts.

The latest query experiment also completed CPU Qwen: 23/32 primary at a
49.25-second median, matching all Metal primary selections. **CPU Kev was
intentionally stopped** after three completed calls at 185–196 seconds each:
one call was interrupted and 60 were unattempted. That partial run supplies
compatibility/timing observations, not cohort accuracy. Its original interruption
record and the completed runs are in the [verified archive](docs/evidence/20261006-query-routing/README.md).

SGLang/MLX has a separate [one-fixture compatibility result](docs/sglang-investigation.md)
with caching disabled, alongside preserved startup failures. It has no matched
workload or performance result here. Calibration and the workload value of
probabilities and Score/Noul remain unproved despite successful API operation.

## Team review

Start with the [when-to-use guide](docs/when-to-use.md) and the query experiment's
[four worked examples](eval/query-routing/README.md#four-examples-explain-the-tradeoff).
Use the [results history](docs/results.md) for conditions and raw proof. The main
review questions are:

- Do the authored queries and expected arguments represent our actual callers?
- Which failures deserve ordinary parser/binding fixes, and which require a
  semantic judgment? Review the [proposed SemStreams work](docs/semstreams-integration.md#classifier-hints-and-production-dispatch).
- What error and latency budget would justify an added model call, and what
  caller decision would benefit from a distribution rather than a JSON label?

The proposed next experiment strengthens code and JSON constraints, then freezes
fresh cases for Metal evaluation. Observed failures become development material.
For answerability, improve source evidence and include fully answerable inputs.
Use bounded CPU compatibility/latency probes before committing to a full CPU run;
CUDA work remains deferred. These are proposals, not production or sibling-repo
changes. The [research decision](docs/research-and-decision.md) records the original scope.

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

Deployment paths: **CPU-only Linux containers** and **native Apple Silicon Metal**.
The CPU bootstrap ran on Linux/ARM64 in Docker Desktop; Metal was subsequently
validated on an M3 Pro laptop. CUDA and Linux/AMD64 remain unvalidated.
The Dockerfile can target AMD64, but that build and older CPU instruction support
remain unverified. Start with 4 CPU cores, an 8 GiB runtime memory budget, and
10 GiB free disk for model, images and build cache. The GGUF is 3.03 GB; RAM use
is higher. See measured resource limits in the validation record.

For this laptop, after fetching the model, run `task metal:build`, `task down`
(if Docker is running), then `task metal:serve`. The API is on the same loopback
port 8084. This serves Kev. Ctrl-C stops the native runtime and guard.
See the [laptop/GPU workflow](docs/laptop-and-gpu.md).

## Quick start

The CPU quick start requires Docker with Compose v2, Task v3, Python 3.11+ and curl.
Go 1.26.4+ is needed for host contract tests and native Metal builds; Docker supplies
its own pinned build toolchain for the container path.

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
runtime. `SEMSELECT_ADDR` controls the standalone listener (default `:8084`);
the Metal launcher sets `127.0.0.1:8084` and the health check follows that address.
Changing the model is a reviewed deployment change, not a per-request
download or routing operation.

The supported API profile is intentionally bounded: UTF-8 JSON body ≤32 KiB,
string `state` of 1–8192 bytes, 1–4 questions, question IDs ≤64 bytes, nonempty
string instructions ≤1024 bytes. Choice has 2–16 candidates (names ≤128 bytes,
string descriptions ≤512 bytes or null). Score has 2–10 nonempty string levels
≤512 bytes. Noul permits optional `true`/`false` string descriptions ≤512 bytes.
Unknown fields, duplicate keys, wrong casing and non-string structured state are
rejected. This is a subset of the upstream API, not blanket SDK conformance.
The original candidate order and native response bytes are preserved by the guard.

Both paths use one slot, 4096 context tokens and equal 512-token batch/microbatch
sizes. Docker uses CPU layers; the Metal launcher requires full GPU offload.
Byte limits do not guarantee fitting the tokenizer budget:
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
task answerability:validate # offline checks for the 12 teaching cases
task answerability:metal    # same fixed evidence; cached native build/models required
task answerability:source:validate # frozen 24-case source pilot, offline
task answerability:source:metal    # same prompt on six new source families
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
