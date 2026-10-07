# semselect

**Help skeptical developers decide when a Jev-like decision model is worth using,
through a tested local service, reproducible comparisons, and plain-language guidance.**

semselect packages llama.cpp's native decision API for the c360studio sem* ecosystem.
It is also a completed CPU/Metal research baseline, with inspectable experiments
and a guide to choosing code, ordinary model output, or a specialized decision model.

**Query-classification decision — 2026-10-07: keep Qwen3.5-4B JSON and the
improved-code baseline.** Qwen remains our model quality reference; improve the
coded routing and binding path before adding a specialized backend or starting
custom training. The improved rules remain evaluation-local, and a combined app
router still needs validation. semselect remains an evaluation/reference service;
its packaged native System One runtime continues to use Kev.

The bounded specialist and smaller-Qwen follow-ups are complete. Their negative
results narrow the choice without establishing that 4B is the smallest possible
solution or that task-specific training is required. Use the measured small-team
requirements; no serious-scale requirement has been established.

## Answers we can give today

| Question | What we measured | Decision it supports |
| --- | --- | --- |
| What is our query-classification baseline? | On the same 120 operation cases, **Qwen3.5-4B: 111 correct, eight wrong, one defer**; **improved code: 95 correct, 25 wrong**. | Keep both as the model and code references. Neither certifies automatic routing. [Current decision](docs/when-to-use.md#current-query-classification-decision). |
| Can a smaller Qwen do the job? | **Qwen3.5-2B: 84/120**; **Qwen3-1.7B: 93/120**, with 36 and 27 wrong accepts. Most errors overrode ordinary search. | Neither fixed configuration qualifies. 1.7B outperformed 2B here, but both fell short of 4B and improved code. [Smaller-Qwen report](docs/validation-qwen-size.md). |
| Did a specialist earn its place? | The development-selected DeBERTa accepted **13 correct, one wrong, 106 deferred**; GLiClass got **37 raw correct**. | Keep the existing baselines; no specialist integration from this evidence. [Specialist report](docs/validation-specialist-intent.md). |
| Have we met the 250 ms median / 750 ms p95 budget? | 4B JSON on Metal: **867 / 1,044 ms**. Smaller Qwens on four-thread ARM64 CPU: **5.93 / 6.13 s** and **5.27 / 5.48 s**. | No tested Qwen configuration on this query contract meets that latency budget. These are different hardware operating points, not CPU speed ratios. [Conditions and limits](docs/results.md#2026-10-07--query-classification-decision). |
| Do we need to train our own model? | No task-trained small classifier has been evaluated. Pretrained 4B is the strongest measured operation classifier, with remaining errors. | Training is an unproved option, not a requirement. Keep labeled evaluation separate from a training project. [Decision guide](docs/when-to-use.md#current-query-classification-decision). |
| Are memory or serious scale driving the choice? | Small Qwen cgroup peaks were **1.54 / 1.62 GiB**. Load checks were skipped after quality/latency failures; no large-team throughput requirement was established. | Size for the current small team. These results do not justify a new serving or scaling framework. [Resource record](docs/validation-qwen-size.md#resources-sensitivity-and-the-bound). |
| Are the improved rules or a hybrid already shipped? | The improved rules are an evaluation comparator; full caller integration and a code-plus-Qwen policy remain untested. | Carry over measured fixes through the caller's tests, retaining explicit fallback and coded policy. [Proposed app work](docs/semstreams-integration.md#query-classification-baseline-and-next-app-work). |
| Can ordinary Qwen reduce output cost? | On the earlier Metal ticket task, one-token scoring preserved JSON labels at **28% lower median HTTP latency**. | A measured format saving without specialized training; it does not prove our query latency target. [Format comparison](docs/validation-scoring.md). |
| Can an evidence gate help answering? | On 12 captured inputs, either gate reduced unsupported assertions from **three to zero**, preserving one useful partial answer. None had a complete answer. | Improve the supplied evidence; false deferrals of fully answerable inputs remain unknown. [Answer replay](eval/synthesis/README.md). |

These are small task evaluations, not production benchmarks. Repeated orders and
trials reuse cases. Latency belongs to the measured runtime profile. The earlier
Kev query heads reprocessed shared state with one slot, and the ticket comparison
had unequal prefix-cache reuse; the later Qwen operation runs verified zero reuse.
These results do not establish an intrinsic model speed ranking. In the 4B
comparison, Qwen is the post-trained release and Kev was trained from its Base
sibling, so this is not an identical-backbone control. See the
[closeout audit](docs/research-and-decision.md#closeout-review-2026-10-06).

## When should a skeptic care?

A bounded semantic decision is useful when text requires interpretation and the
caller already knows the permissible outcomes: for example, judging whether
supplied evidence supports a claim. Caller-provided descriptions can change
without training a new classifier. That flexibility is not exclusive to Jev-like
models; Qwen JSON can supply it too.

Reach for a specialized decision implementation when it **measurably improves
quality or the cost of an existing semantic judgment**. A compact response or a
probability field alone is not that evidence. Use code for exact facts, permissions
and transitions; keep existing retrieval/ranking for finding evidence. With stable
labels and training data, include a small trained classifier in the comparison—we
have not tested one here. The [when-to-use guide](docs/when-to-use.md#choose-by-the-job)
makes these choices explicit.

A possible Tier 2 community-evidence use remains worth remembering: prioritize
which retrieved communities to inspect, or replace an existing expensive LLM
review. First supply the missing source evidence and compare with existing ranking.
We have not shown that an added classifier improves that path. A high event rate
alone does not require a model call per event.

## What is implemented and tested

The service packages **llama.cpp + Kev-4B Q4_K_M** behind a small Go request guard,
using `POST /v1/systemone`. Callers supply context and permissible answers, and own
authorization, thresholds, fallback and execution. No model decision authorizes
an action. Kev remains the pinned reference, not a proven best-of-breed choice;
Julia, the specialist classifiers and smaller Qwen models are isolated evaluations.
They do not change the supported service model.
See the [model selection review](docs/research-and-decision.md#is-kev-our-best-open-source-choice).

The **code baseline differs by task**. Query classification imports the actual
SemStreams regex and optional BM25 classifiers. Answerability uses a supplied
metadata/exact-fact precheck, not a general code-only text classifier.
[How the code works](docs/when-to-use.md#what-our-code-baseline-actually-does).

CPU results are **Linux/ARM64 Docker on an M3 Pro**; Metal results are native on
that laptop. They establish neither AMD64 nor CUDA performance. Qwen comparisons
use llama.cpp chat directly and do not upgrade or validate the existing seminstruct
release. SGLang/MLX has only a separate [one-fixture compatibility result](docs/sglang-investigation.md),
with caching disabled and earlier failures preserved. Calibration and the workload
benefit of native probabilities, Score and Noul remain unproved.

## Team review

Review the [selection guide and reopening conditions](docs/when-to-use.md#research-closeout-and-reopening-conditions),
then use the [latest query comparison](docs/results.md#2026-10-07--query-classification-decision)
and results history for full tables and raw proof.
The [closeout audit](docs/research-and-decision.md#closeout-review-2026-10-06)
records what we accepted and corrected from outside critique.

A useful review identifies a real caller, an error budget and a rate/latency need
that the current path cannot meet. Without that, more model shopping would not
answer the adoption question. Parser fixes and a possible typed decision contract
are [proposed SemStreams work](docs/semstreams-integration.md), not sibling changes
or a committed integration roadmap.

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
task specialist:test # offline specialist harness and pinned classifier driver
python3 -m unittest discover -s eval/qwen-size -p 'test_*.py' -v # smaller-Qwen harness
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

If a native decision backend earns adoption, SemStreams would need a small typed
`/v1/systemone` client to preserve its distributions. No sibling repository was
modified. A future seminstruct decision-model image
variant could share or absorb this packaging; a second inference implementation
is unnecessary.
