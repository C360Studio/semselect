# Does SGLang on MLX serve several Qwen decisions at once usefully?

**Design 2026-10-09; results recorded 2026-10-09** in the [throughput
record](../../docs/validation-throughput.md#sglang-mlx): one running request
only, because four crashed at this pin. The protocol text below is unchanged
from the design, including its statements that nothing had run yet. This is
the protocol for `run_sglang.py`, a sibling of the llama.cpp runner in
[README.md](README.md). It reuses that runner's fixtures, cell runner, stop
rules, summary shape and report.
Nothing below is a measurement. It stays inside the scope and evidence rules of
the [bounded SGLang investigation](../../docs/sglang-investigation.md): isolated
evaluation work that changes neither the default runtime nor the service.

The question, asked separately for each way of getting a Qwen decision out of
SGLang: does raising SGLang's `--max-running-requests` from 1 to 8, with as many
clients, deliver **materially more decisions per second at unchanged labels**
than SGLang's own one-request profile? "No" is a useful answer. So is "SGLang
cannot run this profile". A "yes" here says nothing about llama.cpp, and the
reverse.

## What SGLang can and cannot serve here

| | Status | Evidence |
| --- | --- | --- |
| Qwen3.5-4B, MLX 4-bit, JSON Schema chat | Served in a one-fixture probe | [Cache-disabled probe](../../docs/evidence/20261005T154252Z-sglang-metal-cache-disabled/README.md): two valid `route` objects |
| Same model, `/v1/score` (direct label scoring) | Served in the same probe | Complete A/B distributions, identical across repeats |
| Same model, `/v1/decisions` (SGLang's bundled decision path) | Served in the same probe | Three-question bundles with complete distributions; replay through `/v1/score` matched exactly |
| Kev | **Not attempted** | SGLang's [decision-model page](https://docs.sglang.io/docs/supported-models/decision_models) names other trained decision checkpoints, each with a `decision_config.json`. Kev ships none and is not named. Kev's MLX path is its own server, a separate runner. |
| Cached (radix) or overlap-scheduled MLX path | Unverified | The probe passed only with both disabled; see the re-probe cells below |

The probe is one fixture with two repeats. It establishes that the endpoints
answered, not that they answer well or fast. Everything in this protocol is the
next step: the same frozen workloads the llama.cpp runner uses.

## Arms and the request mapping

Every SGLang body is derived from a body `fixtures.py` rebuilt and byte-verified
against the serial llama.cpp runs. No prompt text, label or candidate order
changes. `--validate` re-checks every mapped body, and each cell summary carries
the mapping (`sglang_requests.MAPPING`). Source references are to the pinned
SGLang revision `efb62ce269b499123e2d1c89005ee4cea8c31098`.

| Arm | Endpoint | Built from | Workloads |
| --- | --- | --- | --- |
| `qwen_json` | `/v1/chat/completions` | the llama.cpp Qwen JSON bodies | W1, W2 |
| `qwen_decisions` | `/v1/decisions` | the Kev `/v1/systemone` bodies (state and Choice heads), served by Qwen | W1, W2 |
| `qwen_score` | `/v1/score` | the evidence text and labels of the llama.cpp one-token arm | W1 |

**`qwen_json`: two differences.** The `model` value changes from the llama.cpp
alias `qwen3.5-4b` to the served name `qwen35-4b-mlx`; SGLang echoes it and reads
it only for a `base:adapter` LoRA suffix (`entrypoints/openai/serving_base.py:39-52`).
`cache_prompt` is removed: it is a llama.cpp field, SGLang's chat request has no
such field and ignores unknown ones (`entrypoints/openai/protocol.py:858`), and
prefix reuse is a server setting here. Messages, `temperature` 0, `max_tokens`
128, `seed` 0, `chat_template_kwargs` and the strict JSON schema are unchanged,
in the same key order and encoding. Semantics to keep in mind: `seed` is accepted
but ignored on MLX outside deterministic mode (`hardware_backend/mlx/sampling.py:76-86`);
`max_tokens` is deprecated in SGLang but honoured, as in the probe; the schema is
enforced by `--grammar-backend llguidance`, not llama.cpp's grammar sampler.

**`qwen_decisions`: the same mapping SGLang's own System One route applies.**

| Kev `/v1/systemone` field | SGLang `/v1/decisions` field |
| --- | --- |
| `state` (a JSON string) | `input`, the same string; strings render verbatim (`serving_decisions.py:449-454`) |
| `questions` map, in head order | `questions` list, same order and ids |
| `type: choice` | `type: choice` |
| `instructions` | `question` |
| `criteria` name → description, in candidate order | `options: [{name, description}]`, same order |
| `model` (Kev alias) | `model` (served name; echoed only) |
| — | `prompt_format_version: 1`, which pins the server-owned wording (another version returns 400) |
| — | `return_prompt_token_ids: true`, as in the probe; every answer carries its rendered prompt and label ids |

This is how `/v1/systemone` maps a System One body for a chat model
(`entrypoints/systemone/serving.py:80-110`, `214-221`), so the mapping is SGLang's,
not ours. `temperature` is left at its default; it scales probabilities, never
the chosen option. W2 sends **one request carrying the three heads**, SGLang's
bundled path. What does change is the wrapper around the unchanged text: SGLang
renders each question as one user message with thinking off, the input, a blank
line, `Question: <instructions>`, one `A: <name> - <description>` line per option
and `Answer with the letter of one option only.` (`serving_decisions.py:514-547`),
then reads the letter probabilities from one prefill per question. Kev's runtime
wraps the same text in its own prompt. `usage.prompt_tokens` counts every
question's prompt, so a W2 request counts the shared state three times
(`serving_decisions.py:441-444`).

**`qwen_score`: the probe's independent-score call.** The messages are
`compare_scoring.score_messages`, the text the llama.cpp one-token arm scores.
After startup and before warmup, outside the timed window, the runner renders and
tokenizes them exactly as the probe did: `/v1/tokenize` with the messages and
`enable_thinking: false`, `/v1/detokenize`, then `/v1/tokenize` of the prompt
and the prompt plus each letter, requiring one distinct token per label at the
answer position. The request is `{model, query: [], items: [prompt_ids],
label_token_ids, apply_softmax: true, return_token_logprobs: true}`, key for key
the probe's. No token is generated: the choice is the argmax of the label
softmax, where llama.cpp's is the greedy grammar-constrained token. Same labels,
different readout. `cache_prompt: false` and `seed: 0` have no counterpart.

**Parsing.** `qwen_json` goes through the shared parsers
(`evaluate.validate_baseline` for W1, the fixtures W2 JSON parser); SGLang returns
the same OpenAI chat shape. Decision answers have no `confidence` and add
`label_mass` and token ids, so `evaluate.validate_native` does not apply;
`sglang_requests.validate_decision` checks the same things it does (type,
allowed choice, a complete distribution summing to one, the choice maximal) plus
a full-vocabulary label mass in (0, 1] and one distinct label token per option.
W2 heads are valid or invalid independently, as for Kev. `validate_score`
requires one finite score per label, the scores to be the softmax of the returned
label log-probabilities, and a label mass in (0, 1]. The probe's saved raw
responses are the test fixtures for all three.

## Cells

Fresh runtime per cell; `N` running requests with `N` clients, as in the
llama.cpp runner's slot cells.

| Cell IDs | Running × clients | Pool tokens | Notes |
| --- | --- | ---: | --- |
| `w1-{qwen_json,qwen_decisions,qwen_score}-1x1` | 1 × 1 | 8,192 | Each arm's own baseline and within-runtime label reference |
| `w1-*-4x4`, `w1-*-8x8` | 4 × 4, 8 × 8 | 16,384, 32,768 | Does SGLang on MLX batch independent decisions usefully? |
| `w2-{qwen_json,qwen_decisions}-1x1`, `-4x4`, `-8x8` | as named | as above | A decisions request is three questions; with one running request they run one after another |
| `probe-overlap`, `probe-radix` | 1 × 1, one fixture | 8,192 | Run first in the W1 invocation; see below |
| `w1-qwen_decisions-4x4-overlap` | 4 × 4 | 16,384 | Added only if `probe-overlap` passes |
| `w1-qwen_decisions-4x4-radix` | 4 × 4 | 16,384 | Added only if `probe-radix` passes |

The workloads, fixtures, warmup and measured passes, case orders and question
counting are the llama.cpp runner's (see [Workloads](README.md#workloads-frozen-fixtures-no-new-prompts-or-labels)).

## Runtime profile and the degraded flags

The launch is the probe's command, flag for flag: the pinned venv
`.sglang/venv` (SGLang `efb62ce2…`, Python 3.12.13, MLX 0.32.3, mlx-lm 0.32.0,
freeze in the probe's `dependencies.txt`), the model `.sglang/models/Qwen3.5-4B-4bit`,
the same environment (`SGLANG_USE_MLX=1`, offline Hugging Face settings,
`SGLANG_MLX_CACHE_LIMIT_GB=1`; inherited `SGLANG_*` variables are stripped), a
4,096-token context and SGLang's default chunked prefill (4,096, as the probe's
server reported; not passed, but checked at startup). Only `--max-running-requests N`,
the token pool (below), the port (30111) and the served name differ. Before any launch the runner refuses
to start unless the venv's editable install points at the extracted pinned tree,
every `python/sglang/` file in that tree equals the checksummed source archive,
and every model file matches the byte count and SHA-256 in the probe's
`provenance.json` (itself pinned by digest).

Several flags are there because the default path failed on this laptop, not as
tuning. They are carried over unchanged:

| Flag | Why it is there |
| --- | --- |
| `SGLANG_USE_MLX=1`, `--disable-cuda-graph` | SGLang's [Apple Metal guide](https://docs.sglang.io/docs/hardware-platforms/apple_metal): MLX backend, no CUDA |
| `--mlx-enable-sampling` | Without it an [upstream MLX report](https://github.com/sgl-project/sglang/issues/41211) shows empty logprobs and a failed `/v1/score` |
| `--mamba-radix-cache-strategy no_buffer` | Automatic hybrid-cache selection asserted `extra_buffer needs CUDA/MUSA/NPU/ROCm/XPU (FLA)` before startup ([first probe](../../docs/evidence/20261005T151940Z-sglang-metal/README.md), attempt 1) |
| `--disable-overlap-schedule` | Added together with `no_buffer` in attempt 2. **Never tested on its own**; it may not be needed |
| `--disable-radix-cache` | With the radix cache on, the server's warmup crashed: `'MlxAuxiliaryStateComponent' object has no attribute 'mamba_checkpoint_grid'`. Disabling it was the [one change](../../docs/evidence/20261005T154252Z-sglang-metal-cache-disabled/README.md) that passed |
| `--grammar-backend llguidance`, `--mem-fraction-static 0.5` | The probe's choices, kept so the profile is the one that passed |

Two consequences matter for reading throughput:

- **No prefix reuse.** With the radix cache off, every request computes its whole
  prompt, including the repeats that warmup sent. The llama.cpp runner's RAM
  prompt cache can serve such repeats. W2 decisions also recompute the shared
  state for each of the three questions.
- **The pool scales with the cell: `--max-total-tokens` = max(8,192, 4,096 × N)**,
  so 8,192 at one running request (the probe's value, unchanged), 16,384 at four
  and 32,768 at eight. The reason is parity with llama.cpp, whose `-c 4096×N`
  gives every slot its own 4,096 tokens. On the MLX path the scheduler's token
  allocator is sized to this pool (`hardware_backend/mlx/model_runner_stub.py:279-280`,
  `308-319`) and admits a request only when the pool has room
  (`managers/schedule_policy.py:866-873`). A W2 question was roughly 1.1–1.4K
  tokens in the llama.cpp runs, so the probe's fixed 8,192 would admit only about
  five at once and W2 at 8 running requests would measure the pool, not the
  running-request limit. With 4,096 per running request, admission is bounded by
  `N` for every planned prompt. The value is passed verbatim to the MLX runner
  as its pool size (`hardware_backend/mlx/tp_worker.py:103-104`,
  `model_runner.py:670-671`), but with the radix cache off the runner allocates
  no shared KV pool (`model_runner.py:726-727`): in every cell except
  `4x4-radix` the pool governs admission only, and each request keeps its own
  MLX cache. In `4x4-radix` it allocates 16,385 slots × 8 full-attention layers ×
  4 KV heads × 256 dimensions × K and V × 2 bytes (bfloat16, the model config's
  dtype), about 512 MiB. The pool is set
  by this rule, not tuned; the `#running-req` and `#queue-req` fields the
  scheduler logs show which limit applied.

One reading of the pinned source sets expectations without settling anything:
on MLX, an extend batch launches one prefill forward per request and evaluates
them together (`hardware_backend/mlx/tp_worker.py:455-554`), while decode is a
batched forward (`hardware_backend/mlx/model_runner.py:1554`). `/v1/decisions`
and `/v1/score` are prefill-only, so extra running requests can overlap GPU work
but are not merged into one larger prefill. Whether that pays off is the
measurement.

At startup the runner records the log, `/get_server_info` (server arguments) and
`/v1/models`, and refuses to measure a cell unless the scheduler reports the MLX
runner, `max_running_requests = N`, `context_len = 4096` and
`max_total_num_tokens` = max(8192, 4096 × N), and the server arguments show the
same running requests, pool, context and chunked-prefill size and the planned flags.
A variant whose dropped flag SGLang silently re-enables therefore fails instead
of being measured under the wrong name.

## Re-probe cells, run first

Each re-probe launches the probe's one-running-request profile with exactly one
degraded flag dropped, then sends one fixture, the first W1 `qwen_decisions`
request (case `H03`, normal order), twice in sequence. The second request is a
repeat, so with the radix cache on it exercises the cache path that crashed.

- `probe-overlap`: drops `--disable-overlap-schedule`.
- `probe-radix`: drops `--disable-radix-cache`.

**A probe passes** when the server becomes ready, its reported arguments show the
flag really dropped and the layout planned, both requests return valid answers,
and shutdown leaves no process and a closed port. Anything else fails. Whether
the two repeats agree is recorded as a diagnostic, not a pass condition: SGLang
documents small differences between cold and prefix-cached requests on these
hybrid models. Each probe saves `probe.json` with the request, both raw responses,
the startup record, the last 200 log lines and the verdict, plus the full
`runtime.log`. A failure is a compatibility record, kept as the earlier ones were.

A passing probe adds one 4 × 4 W1 `qwen_decisions` cell with that flag dropped.
These cells are reported beside `w1-qwen_decisions-4x4`; no reading is declared
for them. A probe pass is one fixture, not evidence that the cached or overlapped
path is correct or faster.

## Labels and agreement

- **Primary, within-runtime.** Each cell is compared with **measured pass 1 of
  the same arm's 1×1 cell** in the same invocation, keyed by case and order. The
  1×1 cell compares with itself, so its second W1 pass measures repeatability.
  This is what `label_agreement` holds and what the reading uses. The total is
  every planned measured question; errors, invalid answers and unattempted
  requests are non-matches, as in the README. If the 1×1 cell did not run in the
  invocation, `matched` is null and the reading is not evaluable.
- **Diagnostic, cross-runtime.** The same rows are compared with the serial
  llama.cpp **Qwen JSON** labels that `fixtures.py` already loads (W1 trial 1,
  W2 normal order), for all three arms. This is `label_agreement_cross_runtime`,
  flagged as diagnostic. Quantization, kernels and, for decisions and score, the
  prompt wrapper differ; disagreement there is not a batching effect and not an
  error rate. Journal rows carry this reference in `reference` and `matched`.

## Quantization

This runner serves `mlx-community/Qwen3.5-4B-4bit` at revision
`0e7ffd5c629ef7719d4cbc04069232580bfa9d9c`: **MLX affine 4-bit, group 64**. The
llama.cpp runner serves GGUF **Q4_K_M**. The weights, kernels and numerics
differ, and the MLX repository records no original checkpoint revision or
conversion command. Comparisons across runtimes are of throughput shape only:
how the rate scales with running requests and where it saturates, not absolute
speed or label quality.

## Budgets and stop rules

The llama.cpp runner's, unchanged, including [Amendment 1](README.md#amendment-1-2026-10-09-warmup-errors-no-longer-trigger-the-stop).
Summaries carry the shared harness protocol version; Amendment 2 adds llama.cpp-only
`-b 4096` cells and changes nothing here. The rules:
one warmup pass per cell excluded from every metric; 30 s per request including
queueing; no retries; three consecutive runtime errors in the measured passes
stop a cell (warmup errors are recorded but do not count); 45 minutes wall clock
per invocation covering every cell's startup, warmup and shutdown; unstarted
requests and cells are `not_run` with `stop_reason: budget`; every planned
measured request appears exactly once in `journal.jsonl`.

**One difference: W1 and W2 run as two invocations** (`--workload w1`,
`--workload w2`), each with its own 45-minute budget, where the README runs both
in one. Either order works and each invocation is complete on its own: its 1×1
cells are its within-runtime references. The two re-probes run in the W1
invocation, first, because the cells they gate are W1 cells; a W2 invocation
neither runs nor needs them. Planned load from `--validate`:

| Invocation | Cells | Requests (warmup + measured) | Measured questions | Also |
| --- | ---: | ---: | ---: | --- |
| `--workload w1` | 9 | 1,188 (396 + 792) | 792 | 2 probes × 2 requests; up to 2 probe-gated cells, 264 requests |
| `--workload w2` | 6 | 384 (192 + 192) | 576 | — |

If a budget runs out, that invocation's remaining cells are `not_run` and their
readings are "not evaluable", never negative. Shutdown takes about five seconds: SGLang's own
handler kills its process tree on SIGTERM ([shutdown analysis](../../docs/evidence/20261005T154252Z-sglang-metal-cache-disabled/shutdown-analysis.json));
the runner waits 15 s, then sends SIGKILL to the process group, and records
which happened.

## Pre-declared reading

Fixed before any inference, computed by `report.py` without change.

**Batches usefully on SGLang MLX** (per workload and arm: `qwen_json`,
`qwen_decisions`, `qwen_score`): the 8×8 cell reaches at least **2×** the 1×1
cell's decisions per second, with p95 request time at most **3×** the 1×1 p95,
and within-runtime label agreement **no more than 2 below** the 1×1 cell's.

The reading is evaluated only when both cells completed with no stop reason and
no unattempted requests; otherwise it is "not evaluable". The README's Kev
shared-state reading does not apply: SGLang does not serve Kev here, and
`report.py` will print it as not evaluable for this run. A "yes" says this
runtime, with these flags, can deliver more decisions per second on this laptop
for these fixtures. It says nothing about correctness, calibration, the cached
path, a service-level gain or other hardware.

## What is recorded

`results/throughput/<timestamp>-sglang-qwen/` holds the llama.cpp runner's files
(`summary.json`, `report.md`, `working-tree.diff`, copied sources, and per cell
`runtime.log`, `requests.json`, `journal.jsonl` with raw response bytes and
`summary.json`), plus:

- per cell `server-info.json` and `models.json`, `usage.json` (server-reported
  `prompt_tokens` and `completion_tokens` per call), and for `qwen_score`
  `score-preparation.json` (rendered prompt and label token ids per case);
- per probe `probe-*/probe.json`;
- in each cell summary: `profile` (running requests, context, pool, variant),
  `request_mapping`, `label_agreement` (within-runtime) and
  `label_agreement_cross_runtime`, `tokens_per_request.prompt_tokens`, and
  `metrics.measured_delta` built from the scheduler's `Prefill batch` log lines
  over the measured window: prompt tokens computed and reused, prefill batches,
  sequences per prefill batch, and the largest queue. SGLang serves no `/metrics`
  without `--enable-metrics`, which the probe did not pass, so the report's
  "busy slots per decode" column is empty for this runtime;
- provenance: commit and working-tree digest, the SGLang source revision and
  archive digest, the count of source files verified, the dependency freeze path
  and digest, the model revision, file checksums and `quantization: mlx affine
  4-bit, group 64`, and hardware.

Peak RSS is not a usable memory signal here: the scheduler that holds the MLX
weights is killed by the server itself and is never reaped by the runner. The
startup log's `available_gpu_mem`, wired-memory and MLX buffer-cache lines are
recorded instead.

## Running

```sh
python3 eval/throughput/run_sglang.py --validate               # offline: map requests, print the plan
python3 eval/throughput/run_sglang.py --validate --checksums   # also verify source tree and model bytes (~3 GB read)
python3 -m unittest discover -s eval/throughput -p 'test_sglang*.py' -v
python3 eval/throughput/run_sglang.py --model qwen --workload w1   # re-probes first, then W1 cells
python3 eval/throughput/run_sglang.py --model qwen --workload w2
python3 eval/throughput/report.py results/throughput/<sglang-w1-run> results/throughput/<sglang-w2-run>
```

A run needs the probe's `.sglang/` workspace, a free loopback port 30111 and the
Metal operation lock (`.native/operation.lock`), which it shares with the
llama.cpp runner so only one Metal run happens at a time. `--cells` takes cell
and probe IDs; a probe-gated cell also needs its probe. `--output` names a new
directory.

## Limits

- Everything in the README's limits applies: small authored sets, one shared
  laptop with no thermal control, correlated repeats, loopback latency, ARM64
  Metal only. Do not infer CPU, AMD64 or CUDA behaviour, or SGLang behaviour on
  other hardware.
- The profile is degraded on purpose: no prefix reuse and no overlap scheduling
  unless a re-probe passes. The pool is sized by rule for 4,096 tokens per
  running request, not tuned.
- The installed packages are not compared with the dependency freeze; the freeze
  path and digest are recorded.
- Throughput comparisons with llama.cpp are shape-only; label comparisons across
  runtimes are diagnostic.
