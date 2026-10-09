# semselect

**Help skeptical developers decide when a Jev-like decision model is worth using,
through a tested local service, reproducible comparisons, and plain-language guidance.**

semselect packages llama.cpp's native decision API with a pinned Kev model. A
caller supplies evidence and bounded choices; the model evaluates them, and the
caller owns thresholds, fallback and actions. The experiments ask whether this
approach improves useful decisions or their cost compared with existing algorithms
and ordinary schema-constrained models. A result favoring code or Qwen is useful
research too. “Jev-like” describes the interface; we have not evaluated Jev itself.

Routing was an initial test workload. Evidence sufficiency and community/graph
refinement ask different questions and need their own measures of success.
**The service works on CPU and Metal. We have not yet demonstrated a workload
where adding this specialized service earns production adoption.** That is the
current evidence, not a conclusion that bounded decisions have no graph use.

## How to read the latency numbers

Every latency **we measured** before 2026-10-09 has the same shape: one request
at a time, on one slot (`-np 1`, llama.cpp's working memory for one
conversation). Kev requests pass through semselect's guard, which admits one
inference and answers a second with HTTP 429. The Qwen runs called llama.cpp
directly, but an evaluator sent them one at a time too. That is the shape of an
interactive router for a small team: one person asks and one answer comes back.

Background graph work asks a different question: how many decisions per second
can the hardware finish, at acceptable quality? A model that is slow one
request at a time could still be the cheaper choice at volume, or the reverse.
The high-throughput case is the main pitch for decision models. The
[pitch itself](#what-the-decision-model-pitch-claims-in-plain-language)
is explained further down.

A [bounded Metal throughput screen](eval/throughput/README.md) has now
**measured** that on llama.cpp ([record](docs/validation-throughput.md),
tracking issue [#3](https://github.com/C360Studio/semselect/issues/3)). On this
M3 Pro, prompt processing stayed at about 440 to 570 tokens per second at 1, 4
and 8 slots for both models, so with fresh evidence in each request every path
landed near one decision per second; extra slots mostly added queueing. Kev
gained only from grouping three questions over one shared state (2.35×,
identical labels) and stayed slower than Qwen JSON, which gained from slots
only with a warm prefix cache. SGLang MLX served Qwen at one running request
only; four crashed at its pinned revision. Kev's own MLX server tested the
cached-state claim: once it held a state, each further question cost about
365 ms, against 1,970 ms for one question on a new state. Nothing here
describes CUDA hardware.

Headline cells on Metal, guard bypassed: llama.cpp with Q4_K_M files, and two
rows from Kev's own MLX server with bf16 weights (marked Kev MLX), a
different artifact. W1 is the 22-case source-evidence pilot in both orders; W2
is the 32-case query task, three questions per request. Latency includes
queueing at the server.

| Cell (slots × clients) | Decisions/s | p50 ms | p95 ms | Labels matched |
| --- | ---: | ---: | ---: | ---: |
| W1 Kev, 1 × 1 | 0.94 | 939 | 1,458 | 88/88 |
| W2 Kev, 1 × 1 | 0.43 | 6,998 | 7,005 | 96/96 |
| W2 Kev, 4 × 1 (head grouping) | 1.01 | 2,972 | 2,974 | 96/96 |
| W1 Qwen JSON, 1 × 1 | 1.06 | 883 | 1,363 | 88/88 |
| W1 Qwen JSON, 8 × 8 (warm prefix cache) | 2.88 | 2,164 | 6,527 | 88/88 |
| W1 Qwen JSON, 8 × 8, `-b 4096` (diagnostic) | 4.43 | 917 | 4,581 | 88/88 |
| W2 Qwen JSON, 1 × 1 | 1.30 | 2,297 | 2,401 | 96/96 |
| W2 Qwen JSON, 8 × 8 | 1.38 | 17,079 | 27,443 | 96/96 |
| W1 Kev MLX, 1 × 1 | 1.18 | 773 | 1,158 | 88/88 |
| W2 Kev MLX, cached state, 1 × 1 | 1.11 | 1,349 | 1,971 | 96/96 |

"Labels matched" compares each answer with the same model's earlier serial run,
or for Kev MLX with the same server's one-client, new-state cell; it shows
whether labels moved, not whether they are correct. The cached-state row
alternates one question on a new state (median 1,970 ms) with two questions on
that state once cached (730 ms).

## Qwen versus Kev: quality and latency

**Kev was slower in our routing comparisons.** Recommending Qwen as a comparison
baseline means it had stronger measured quality/cost; it does **not** mean Qwen
meets the application's latency or error budget.

Each row below compares the same workload on the same hardware. Both models are
4B Q4_K_M; Qwen returns constrained JSON and Kev uses native Choice. Times are
**median / p95 measured decision latency**, excluding startup and warmup.

| Workload and hardware | Qwen3.5-4B: quality; latency | Kev-4B: quality; latency | What it establishes |
| --- | --- | --- | --- |
| [Ticket routing, Metal](docs/validation-metal.md): 24 cases in two option orders | **46/48** correct; **288 / 423 ms** | **43/48** correct; **449 / 474 ms** | Kev was slower and less accurate on this set. |
| [Query intent + arguments, Metal](eval/query-routing/README.md): 32 cases | **23/32** exact; **2,381 / 2,487 ms** | **23/32** exact; **7,111 / 7,121 ms** | Equal primary accuracy; Kev took about **3×** as long at the median. |
| [Source-evidence sufficiency, Metal](docs/validation-answerability-source.md): 24 cases | **16/24** correct; **940 / 1,562 ms** | **19/24** correct; **985 / 1,469 ms** | Kev improved quality on this pilot; its median was slightly slower and p95 lower. |

The source-evidence row times the 22 cases needing a model; two other cases were
resolved by shared code. These are small authored evaluations, not production
benchmarks. Reordered or repeated cases are not new independent examples.

On the **CPU version of the 32-case query task**, Qwen's median/p95 were
**49.25 / 52.56 seconds**. Kev's three completed requests took **185–196 seconds
each**, after which the run was intentionally stopped. That partial Kev run has
no cohort accuracy, median or p95. It supplies no evidence of a faster CPU
alternative. [CPU record](docs/validation-query-routing.md#intentional-cpu-kev-stop).

The measured serving configurations matter. Ticket Qwen reused prompt prefixes
while Kev reprocessed them. The query task used one Qwen JSON response versus
three Kev heads that reprocessed shared state in one slot. These results describe
those actual deployments, not an intrinsic speed ranking of model architectures.
Head grouping with four slots later narrowed but did not close the query gap:
Kev's median fell from 6,998 to 2,972 ms against Qwen's 2,297 ms in the
[throughput screen](docs/validation-throughput.md). Keeping a state cached
between requests was measured only on Kev's own MLX server, a different
artifact, where two follow-up questions took 730 ms. See the
[runtime audit](docs/research-and-decision.md#closeout-review-2026-10-06).

Existing rules and BM25 got **18/32** on the full query task. Both models corrected
some misses, but Qwen also lost seven code successes and Kev lost six. Better
aggregate accuracy did not make either a reliable replacement for the existing
path; the [worked cases](eval/query-routing/README.md#four-examples-explain-the-tradeoff)
show why the follow-up separated operation selection from coded argument binding.

## Did a model meet the query latency budget?

**No model qualified on the later CPU operation-selection contract.** Its target
was **250 ms median / 750 ms p95**, with quality and wrong-selection limits as
well. This is a different, 120-case task: choose an operation while shared code
binds arguments. Do not transfer its results to the 32-case task above.

| Candidate and tested hardware | Correct operations / 120 | HTTP median / p95 | Outcome on this contract |
| --- | ---: | ---: | --- |
| Qwen3.5-4B, **Metal** | 111 | 867 / 1,044 ms | Quality reference, not a qualified CPU deployment. Eight wrong accepts and one defer; even its measured Metal latency exceeds the numeric target. |
| Qwen3.5-2B, **CPU** | 84 | 5,928 / 6,127 ms | Failed quality and latency. |
| Qwen3-1.7B, **CPU** | 93 | 5,267 / 5,480 ms | Failed quality and latency. |
| Kev-4B | **Not tested** | **Not measured** | No result on this operation-only contract; the earlier three-head timings cannot substitute for one. |

The 4B row is the historical reference from the
[specialist pilot](docs/validation-specialist-intent.md); the smaller models
reused that cohort in the [size comparison](docs/validation-qwen-size.md). This
is not a CPU speed comparison between 4B and the smaller models. The CPU
specialists also failed qualification; the fast embedding arm deferred every
case. Evaluation-local improved rules reached 95/120, but their 25 wrong
selections also prevent treating them as an approved automatic router.

## What the other experiments add

| Question | Finding | What remains unproved |
| --- | --- | --- |
| Does avoiding generated output reduce decision cost? | In a separate **uncached Metal** comparison, the same Qwen went from **673 ms JSON to 488 ms one-token scoring**, with identical labels: 28% lower median inference HTTP latency. [Record](docs/validation-scoring.md). | This is an output-format benefit, not a Kev speedup or proof of calibrated scores. It uses a different cache profile from the 288 ms ticket run above. |
| Can a small decision model be fast on CPU? | Julia took **159 ms median**, but got only **24/48** ticket selections correct, with 23 unnecessary fallbacks. [Record](eval/julia/README.md). | Fast short-input inference alone does not establish useful routing quality or performance on graph evidence. |
| Does an evidence gate improve generated answers? | On 12 captured inputs, either Qwen or Kev reduced the 4B generator's unsupported assertions from **3 to 0**, retaining one useful partial answer. [Record](eval/synthesis/README.md). | No captured input fully answered its question, so the test cannot measure how often the gate would block good answers. It showed no Kev advantage over Qwen. |

These experiments separate a functioning API, useful semantic judgment, and a
reason to choose a specialized backend. Native distributions and typed outputs
are implemented capabilities; their presence alone does not establish better
accuracy, calibrated confidence, or lower application cost.

## What the decision-model pitch claims, in plain language

A chat model answers by writing text one token at a time. A decision model
writes no text. It reads the input once and reads out a probability for each
option the caller supplied, such as `billing`, `technical` or `unknown`. The
answer is a list of numbers over your options, which caller code can threshold
or send to a fallback. "Jev-like" in this repository means that interface:
bounded options, typed answers and an explicit way to abstain.

Jev itself is a hosted model, so we cannot test it locally. The open
[OpenJev 27B GGUF](https://huggingface.co/ggml-org/OpenJev-GGUF) is licensed
CC BY-NC 4.0, which puts it outside this project's default candidate set.
Kev is the open decision model we run. Our results say nothing about Jev's
quality.

The pitch's speed claims rest on three things:

1. **No decode step.** Nothing is generated, so the cost is one pass over the
   input.
2. **One state, many cheap questions.** Read a document once, keep that state,
   and each new question pays only for itself.
3. **Batching on a datacenter GPU.** Many requests share each pass over the
   weights.

**Author-reported** speed figures for Kev-4B follow. None is measured here, and
the Apple rows are an M5, not our M3 Pro.

| Hardware | Setup | New state | Cached state |
| --- | --- | --- | --- |
| Apple M5, MLX bf16 | 5 questions, ~270-token state | 721 ms | 136 ms |
| Apple M5, MLX bf16 | 3 questions, 8,192-token state | 6.6 s | 354 ms |
| Apple M5, MLX bf16 | 3 questions, 65,000-token state | 84.5 s | 716 ms |
| RTX PRO 6000 | one decision | 12 ms | not stated |
| H100, bf16 | 64 concurrent clients | about 101 requests/s | not stated |

Sources: the [Kev repository](https://github.com/jaredpalmer/kev) and
[model card](https://huggingface.co/jaredpalmer/kev-4b) for the M5 and H100
rows; the [llama.cpp announcement](https://huggingface.co/blog/ggml-org/decision-models-in-llamacpp)
for the RTX PRO 6000 row.

**Cached** means the state was already sent, so only the new questions are paid
for. Those figures apply only when you ask more questions about a document you
already sent. Most of our measurements carry their own evidence in every
request. One cell on Kev's own MLX server sent each query's state with one
question and then asked two more against the cached state; it is our only
cached-state measurement (below). The author's figures are still a different
machine, and their state sizes and question counts differ from ours.

What we have tested of each claim on our M3 Pro, on llama.cpp Metal, SGLang MLX
and Kev's own MLX server ([throughput record](docs/validation-throughput.md)):

- **No decode step:** true, but not where the time goes on this laptop.
  Prompt processing at about 500 tokens per second is most of the cost: all of
  Kev's, and 68 to 84% of Qwen JSON's at one slot (measured). On the same Qwen,
  one-token scoring was slightly slower than JSON (0.97 against 1.06 decisions
  per second) because its request leaves the prompt cache off and it processed
  more prompt tokens. An earlier uncached comparison, where scoring cut median
  latency by 28% ([record](docs/validation-scoring.md)), is an output-format
  effect on an ordinary model, not Kev's native head. SGLang MLX showed the
  same direction with the cache off in every arm: no decode step raised Qwen
  from 0.78 decisions per second (JSON) to 0.98 (`/v1/score`, the same 50,460
  prompt tokens) and 0.99 (`/v1/decisions`), and cut the median by 22%.
- **One state, many questions:** real within one request, now measured. With
  four slots and one client, llama.cpp grouped Kev's three query heads and
  evaluated their shared prefix of about 1,070 tokens once: 2.35× more questions
  per second than one slot, all 96 labels identical. It needs at least three
  free slots, so the one-slot service profile cannot use it, and grouped Kev
  (1.01 questions/s) was still slower than Qwen JSON (1.30 to 1.39). Kev reused
  no prompt tokens across requests on this build, so each request re-reads its
  state. Unified KV (`--kv-unified`) added nothing measurable. Kev's own MLX
  server keeps a state between requests, and there a cached state made further
  questions cheap: two more questions on it took 730 ms, about 365 ms each,
  against 1,970 ms for one question on a new state, with all labels unchanged.
- **Batching:** measured on Metal, and it does not speed up prompt processing.
  The only cells that gained were Qwen JSON with a warm prefix cache, where
  little prompt was left and the short answers shared decode steps.
  llama.cpp's decision-model path evaluates one slot's prompt per compute step
  by construction (from the pinned source). SGLang MLX crashed at four running
  requests at its pinned revision, and Kev's own MLX server runs one request at
  a time: 1.18 to 1.19 decisions per second at 1, 4 and 8 clients. Datacenter
  GPUs are untested.

With fresh evidence in every request, every path on this laptop lands near one
decision per second, whichever model. The lever is bundling questions per
shared state, or keeping that state cached between requests, not concurrency.
This says nothing about CUDA hardware, Jev or decision quality.

Whether a decision model is worth using for your job is the question for the
[selection guide](docs/when-to-use.md). This section only says what the pitch
claims and which parts we have and have not tested.

## Community and graph refinement

The next [designed evaluation](eval/community-refinement/README.md) asks whether
reviewing semantic virtual edges improves the resulting communities and retrieved
evidence. It compares existing structural/tuned semantic clustering, a trained
edge reviewer, Qwen and Kev. Background refinement gets its own cost budget;
the query router's 250 ms target is not a universal semselect requirement.
The pilot's serving profile and cost budget now follow the
[throughput record](docs/validation-throughput.md): four slots and one client
for Kev, so its questions share one state, and one slot for Qwen JSON unless
its state is cached. Its review contract also gains a
[per-entity bundle variant](eval/community-refinement/README.md#per-entity-bundle-variant),
which asks several questions about one entity in a single request.

SemEngine is the integration target, replacing SemStreams when ready. The
**2026-10-08 source audit** found that its clustering and semantic-edge path was
not ported yet; the protocol records the exact revisions and execution
prerequisites. This track is **designed, not executed**. Routing results neither
establish nor rule out its value. See the [SemEngine proposal](docs/semengine-integration.md).

## What is implemented and tested

The service packages **llama.cpp + Kev-4B Q4_K_M** behind a small Go request guard
at `POST /v1/systemone`. Kev remains the pinned reference, not a proven
best-of-breed choice. Qwen comparisons use a separate chat/scoring path; they do
not replace Kev behind that endpoint. Julia and other candidates are evaluation
arms, not additional supported service backends.

CPU inference results are **Linux/ARM64 Docker on an M3 Pro**; Metal results are
native on that laptop. Neither establishes AMD64 or CUDA performance. Native
Choice, Score and Noul work, but Score/Noul have contract and smoke validation
rather than workload-quality evidence. The serving-path table at the end of this
section shows what has and has not been run on Apple Silicon.

The [selection guide](docs/when-to-use.md) explains when to use code, a general
model or a decision model. The linked validation reports preserve conditions and
raw evidence; [results history](docs/results.md) and the
[model-selection review](docs/research-and-decision.md#is-kev-our-best-open-source-choice)
provide the earlier experiment and backend rationale.

### Serving paths on Apple Silicon

| Path | Model artifact | Status here |
| --- | --- | --- |
| llama.cpp native | Kev, Qwen GGUF Q4_K_M | **Measured**, serial and 1/4/8 slots |
| SGLang MLX | Qwen MLX 4-bit | **Measured** at one running request only; more than one crashes this pin; Kev not documented |
| Kev's own MLX server | Kev bf16 | **Measured**, including cached state |

- **llama.cpp native** produced every latency above except the two Kev MLX rows:
  the comparison tables one request at a time, and the
  [throughput screen](docs/validation-throughput.md) at 1, 4 and 8 slots for
  both models.
- **SGLang MLX** served Qwen3.5-4B (mlx-community 4-bit) for JSON chat,
  `/v1/score` and `/v1/decisions` on this M3 Pro on 2026-10-05, after adding
  `--disable-radix-cache`. That was one fixture and one running request, with
  no throughput. See the [compatibility record](docs/sglang-investigation.md).
  The 2026-10-09 throughput run measured the three paths at one running
  request; four running requests crashed the scheduler and the radix cache
  still crashed ([record](docs/validation-throughput.md#sglang-mlx)).
  SGLang's [documentation](https://docs.sglang.io/docs/supported-models/decision_models)
  names trained decision checkpoints (PPLX-Decider, Clef) that answer through
  `/v1/systemone`, and mentions a `decision_config.json` for them. It does not
  name Kev, and Kev ships no such file, so on the documentation SGLang does not
  serve Kev. We have not tried loading it. SGLang can run Qwen direct scoring
  through `/v1/decisions`.
- **Kev's own MLX server** (`python -m kev.serve`) exposes `/v1/systemone` and
  picks MLX in bf16 on Apple Silicon automatically, per the
  [Kev model card](https://huggingface.co/jaredpalmer/kev-4b). On this M3 Pro
  it ran W1 at 1.18 decisions per second whatever the client count, and a
  cached state cut the cost of a further question to about 365 ms
  ([record](docs/validation-throughput.md#kev-mlx-server)).

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
Do not compare this probability with the historical SemStreams `Confidence`
field audited here, which mixes heuristics, similarity and generated confidence.

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

If a native decision backend earns adoption, SemEngine would need a small typed
`/v1/systemone` client at its actual admitted caller to preserve distributions.
See the [conditional integration proposal](docs/semengine-integration.md). No sibling repository was
modified. A future seminstruct decision-model image
variant could share or absorb this packaging; a second inference implementation
is unnecessary.
