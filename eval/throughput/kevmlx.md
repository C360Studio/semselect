# Kev MLX arm: does Kev's own server batch, and does its state cache pay off?

**Design 2026-10-09; no inference results yet.** This file fixes the protocol
for one more arm of the [throughput screen](README.md): Kev's own server,
`kev.serve`, running Kev-4B on MLX. Nothing below is a measurement. Source
citations are to [jaredpalmer/kev at `5e42a7a0`][kev] unless they name a
semselect file.

## Why this arm exists

The llama.cpp arm serves `Kev-4B-Q4_K_M.gguf`, a 4-bit conversion of the same
adapter revision (`models.lock.json` `conversion_source_revision`
`6cfce5c2…`), through llama.cpp's port of the decision API. Two questions
cannot be answered there:

- **The reference implementation at full precision.** `kev.serve` on Apple
  Silicon is the only Metal path here that runs Kev's own Python encoder and
  pointer head over unquantized weights: the LoRA adapter merged into the bf16
  base, the backbone run by mlx-lm, and the head in fp32
  ([`mlx_model.py:1-17`][mlx-doc], [`97-125`][merge], [`158-163`][head]).
- **The author's cached-state claim.** Kev's README says that on an Apple M5,
  "Kev-4B takes 721 ms for five questions, or 136 ms when the text repeats and
  comes from the cache" ([`README.md:218`][claim], [`398-403`][claim-table]).
  That cache exists only in `kev.serve`, so only this arm can test whether the
  claim holds for these fixtures on this laptop.

No result from this arm is evidence about the llama.cpp path, or the reverse.
Weights, precision, kernels and request encoding all differ.

## Pins

| Item | Pin |
| --- | --- |
| Kev source | `jaredpalmer/kev` commit `5e42a7a03f28134853dd3ff77461457e921e5ec1` (2026-10-05), Apache-2.0. The archive's SHA-256 is recorded on first fetch; the 37 extracted files (package, `pyproject.toml`, `README.md`, `LICENSE`, `.python-version`) and the installed package are checked against the commit's git blob SHA-1s. |
| Adapter | `jaredpalmer/kev-4b` revision `6cfce5c2fa4b4bd64026336ab649c5ca78857d52`, Apache-2.0; 14 files, 159,727,524 bytes. `head.pt` must name the pinned base and revision. |
| Base | `Qwen/Qwen3.5-4B-Base` revision `1001bb4d826a52d1f399e183466143f4da7b741b`, Apache-2.0; 12 files, 9,342,823,181 bytes (safetensors 9,319,828,056). |
| Python | uv venv, CPython 3.13; `kev[serve]` (mlx-lm `>=0.31.3,<0.32` on Apple Silicon) resolved with `--exclude-newer 2026-10-09T00:00:00Z`; the full `uv pip freeze` is recorded. |
| Precision | bf16 backbone as stored (`KEV_DTYPE=bf16`, `KEV_BACKEND=mlx`); fp32 pointer head. Each cell records `/v1/models` `backend` and `dtype` and refuses to measure unless they are `mlx` and `bfloat16`. |
| Server | `.kev/venv/bin/python -I -m kev.serve --run <local adapter snapshot> --host 127.0.0.1 --port 18096`, from `.kev/`, with `HF_HUB_OFFLINE=1`. Every `KEV_*` variable is set explicitly; inherited ones are dropped. |

Every Hub file is checked against the Hub's own digest at the pinned revision
(SHA-256 for LFS files, git blob SHA-1 otherwise), and `.kev/provenance.json`
records every file's SHA-256 and size, the license strings and each command
run. `kevmlx_setup.py --verify` repeats every check offline, and a run repeats
it before the first launch.

## What the server does

Read from the pinned source; none of it is measured yet.

- **Schema.** `POST /v1/systemone` takes `{"state", "model", "questions":
  {id: {"type", "instructions", "criteria"}}}` ([`api.py:17-46`][schema]) and
  returns `{"model", "answers", "usage": {"input_tokens", "output_tokens"},
  "latency_ms"}` ([`serve.py:211-220`][body]). A Choice answer is
  `{"type", "choice", "confidence", "probabilities"}`, with probabilities and
  confidence rounded to 4 decimals ([`api.py:143-160`][answers]).
- **One model thread.** Request handlers tokenize on the event loop and queue
  the record. One thread takes up to 64 queued requests as a batch and answers
  all of them when the batch ends ([`serve.py:34`][max-batch],
  [`132-170`][work]). On MLX a batch runs **one request at a time**
  ([`mlx_model.py:226-229`][one-at-a-time]). Client concurrency can overlap
  HTTP, parsing and tokenization with model time; it cannot batch kernels. A
  request waits for its whole batch, so at C clients a request's time is
  roughly C times one request's model time. `latency_ms` is the batch's model
  time, not the request's ([`serve.py:186-190`][latency]).
- **Questions share one state pass.** The state runs once into a prompt cache;
  each question runs as a row on a copy of it ([`mlx_model.py:176-215`][prefix]).
- **The state cache.** `PrefixCache` keys on the state's token IDs (the tokens
  before the first question) and `option_isolation`; instructions and options
  are not part of the key ([`serve.py:38-61`][cache]). It is least recently
  used, at most `KEV_PREFIX_CACHE` states (default 4) and
  `KEV_PREFIX_MAX_TOKENS` tokens (default 65,536) ([`serve.py:27-29`][cache-env]),
  and on MLX it caches states of every length ([`mlx_model.py:131`][min-tokens]).
  Hits are decided for a whole batch before it runs, so two requests with the
  same new state in one batch both miss ([`serve.py:172-180`][run]).
  `/v1/models` reports hits and misses ([`serve.py:309-310`][models]);
  responses do not say whether they hit.
- **No reset route.** The routes are `/v1/systemone`, `/permute`, `/separate`
  and `/v1/models` ([`serve.py:249-312`][routes]). The only `clear()` call is
  the out-of-memory retry ([`serve.py:177-185`][run]).

## Cache reset: the method

- **New-state cells** run with `KEV_PREFIX_CACHE=0`. No state gets a key, so
  nothing is stored or counted ([`serve.py:55`][cache], [`80-87`][store]).
  Warmup therefore cannot serve a measured request, and neither can W1's other
  candidate order (the normal and reversed requests share a state in 6 of the
  22 cases). The model work is the same as a cache miss with caching on: a miss
  runs the state pass and the question rows and keeps the state, caching off
  runs the same passes and drops it ([`mlx_model.py:210-224`][paths],
  [`model.py:236-240`][probs-one]). Each cell checks that `/v1/models` reports
  cache size 0 at startup and no hits or misses over the measured passes.
- **The cached-state cell** runs with `KEV_PREFIX_CACHE=4`, the server default.
  Its warmup is the same A-then-B sequence over all 32 cases. With 32 distinct
  states and four cache entries, every state has been evicted by later ones
  before the measured pass reaches it, so each measured A misses and each
  measured B hits. The cell checks that the measured-pass deltas equal the
  plan: misses = A requests attempted, hits = B requests attempted. Any other
  count stops the cell with `prefix-cache accounting differs from the plan`.
- **A fresh server per cell**, as in the llama.cpp harness. The server is not
  restarted between warmup and measurement, because that would discard the
  process state warmup exists to build (for example MLX's buffer cache,
  [`mlx_model.py:32`][cache-limit]).

## Cells

"1 × C" means one model thread and C clients. Clients are the shared
`ThreadPoolExecutor` runner.

| Cell | Clients | `KEV_PREFIX_CACHE` | Requests per pass | What it isolates |
| --- | ---: | ---: | ---: | --- |
| `w1-kevmlx-1x1` | 1 | 0 | 44 | Serial baseline; every state new. |
| `w1-kevmlx-1x4`, `w1-kevmlx-1x8` | 4, 8 | 0 | 44 | Client concurrency against one model thread. |
| `w2-kevmlx-1x1` | 1 | 0 | 32 | Three questions on one new state in one request, one state pass. |
| `w2-kevmlx-1x4`, `w2-kevmlx-1x8` | 4, 8 | 0 | 32 | The same with concurrent clients. |
| `w2-kevmlx-cached-1x1` | 1 | 4 | 64 | Per case, request A (`operation`, new state), then request B (`node`, `field`, cached state). |

Passes are the shared ones: W1 one warmup and two measured passes (the second
in reversed case order), W2 one warmup and one measured pass. `--validate`
plans 7 cells, 716 requests (292 warmup, 424 measured) and 648 measured
questions.

## New state versus cached state

A request has a **new state** when the server holds no cache for its state: it
pays for the state pass and every question row. It has a **cached state** when
an earlier request left that state in the cache: it pays only for its question
rows, run on copies of the cached state.

In the cached-state cell, A and B for one case carry the frozen request's
`model` and `state` bytes and its question objects, in order, spliced from the
frozen bytes. A has one question and pays for the state; B has two and should
not. The cell runs at one client so that B starts only after A has answered;
if A fails or times out, B may meet an uncached state, and the accounting
check catches it. The summary reports A and B separately (request time p50 and
p95, valid answers) and B's p50 divided by two as the cached cost per question.

## Requests and responses

The frozen W1 and W2 Kev bodies already have Kev's schema, so every new-state
request is sent byte for byte as the serial runs sent it; `--validate` checks
all 44 W1 and 32 W2 bodies.

| Field | Frozen request | How `kev.serve` reads it |
| --- | --- | --- |
| `model` | `"semselect-kev-4b"`, the llama.cpp alias | Any string; never checked, echoed back ([`api.py:45`][schema], [`serve.py:216`][body]). No effect on inference. |
| `state` | A JSON-encoded string | Rendered unchanged ([`api.py:53`][render], [`117`][record]); `<\|name\|>` patterns are escaped before tokenizing ([`model.py:75-81`][escape]). |
| `questions.<id>` | Choice: `instructions` string, `criteria` {candidate: description} | Each option becomes `name: description` in candidate order ([`api.py:58-59`][option], [`112`][record]). |
| A / B split | One request with operation, node and field | Two spliced requests per case (cached-state cell only). |
| `probabilities` | llama.cpp returns full doubles | Rounded to 4 decimals. A K-option distribution sums to 1 within K × 5e-5, not the shared validator's 1e-5. The parser widens only that test, renormalizes, and then applies `scripts/evaluate.py` `validate_native` unchanged. |
| `usage`, `latency_ms` | `usage.input_tokens` | `input_tokens` counts the whole packed record ([`serve.py:189`][latency]); `output_tokens` counts serialized answer tokens; `latency_ms` is the batch's model time. Recorded, not compared across runtimes. |

Kev's encoder and llama.cpp's port tokenize the same characters independently.
This arm does not show that they produce the same tokens.

## Agreement and the precision caveat

- **Primary, within-runtime.** Each measured question is compared with the
  label from measured pass 1 of this run's 1×1 new-state cell for the same
  workload. That cell's pass 1 matches itself by construction; its W1 pass 2
  is the repeat check. The cached-state cell is compared with `w2-kevmlx-1x1`.
  Errors, invalid answers and unattempted requests count as non-matches. If
  the 1×1 cell did not run in the same invocation, agreement is "not evaluable".
- **Diagnostic, cross-runtime.** The serial llama.cpp Kev labels that
  `fixtures.py` loads (W1 trial 1, W2 `metal/result.json`) are reported as
  `label_agreement_cross_runtime`. They come from Q4_K_M weights through a
  different implementation. Kev's own README puts served bf16 within about 0.05
  of fp32 on a Mac, with the top answer changing on about one question in 300
  ([`README.md:409`][bf16]); 4-bit weights add their own error. A disagreement
  there is not evidence that either runtime is wrong.
- `KEV_TEMPERATURE` is unset, so probabilities carry the checkpoint's fitted
  temperature. Temperature does not change the argmax, so labels are unaffected.

## Budgets and stop rules

The shared rules apply unchanged:

- 30 s per request, including queueing at the server.
- No retries.
- Three consecutive runtime errors in the measured passes stop the cell.
  Warmup errors are recorded but do not count (amendment 1 in the
  [README](README.md)).
- 45 minutes of wall clock per invocation, covering every cell's server start,
  warmup and shutdown. When the budget runs out, the remaining requests are
  recorded as `not_run` with `stop_reason: budget`.
- Every planned measured request is journaled exactly once.

Rules specific to this arm:

- **Startup.** The readiness wait is at most 600 s and never past the
  deadline: the adapter merge and 9.3 GB of base weights load inside it.
- **Profile checks.** A cell is refused unless `/v1/models` reports the pinned
  adapter path, base, LoRA rank, temperature, `mlx`, `bfloat16`, `mps`, the
  planned cache size, and both model names. The port must be held by the owned
  process.
- **Shutdown.** SIGTERM, then SIGKILL after 10 s. The port must be closed
  afterwards; a cleanup failure stops the invocation.
- **Cache accounting.** In the cached-state cell, accounting that differs from
  the plan stops the cell.

At 1 × 8, a W2 request waits for about eight requests' model time. If one W2
request takes more than about 3.7 s, 1 × 8 requests will pass the timeout.
That is a measured outcome, not a harness fault.

## Disk and downloads

| Item | Size | Notes |
| --- | ---: | --- |
| Kev source archive | not known until fetched | The tree at the commit is 852,470,904 bytes in 3,049 files; the archive is kept for `--verify`, and only 37 files are extracted. |
| Adapter snapshot | 159,727,524 bytes | 14 files. |
| Base snapshot | 9,342,823,181 bytes | 12 files. Setup refuses to fetch it with less than 12 GiB free. |
| Python venv and uv cache | not measured yet | Recorded in `.kev/provenance.json`. |

Everything lives under the git-ignored `.kev/`. Setup and `--verify` refuse to
run while a Metal build or measurement holds `.native/operation.lock`; a run
holds it.

## Pre-declared readings

These readings are fixed before any inference. They are screening readings,
not adoption gates, and `run_kevmlx.py` computes them.

1. **Kev MLX batches usefully** (per workload): the 1 × 8 cell reaches at least
   **2×** the 1 × 1 cell's questions per second, with p95 request time at most
   **3×** the 1 × 1 p95, and within-runtime agreement **no more than 2 below**
   the 1 × 1 cell's. These are the README thresholds (`report.py` constants),
   with 1 × 8 in place of 8 × 8. Since MLX runs a batch one request at a time,
   the source suggests little gain beyond overlapping non-model work. The
   screen measures it.
2. **Cached state is materially cheaper**: in `w2-kevmlx-cached-1x1`, B's p50
   request time is at most **half** of A's. In addition, within-runtime
   agreement against `w2-kevmlx-1x1` must be no more than 2 below that cell's
   own, and cache accounting must be as planned. B asks twice as many
   questions as A but skips the state. If the state dominates cost, as the
   author's M5 example implies, B lands well under half of A. A "no" means the
   state is not the dominant cost for these W2 fixtures on this laptop.

A reading is evaluated only when every cell it names completed with no stop
reason and no unattempted requests; otherwise it is "not evaluable". The
README's shared-state reading (Kev versus Qwen) is not computed here, because
this arm serves no Qwen. A cross-runtime comparison is of throughput shape
only. A "yes" does not establish correctness, calibration, a service-level
gain or behavior on other hardware.

## Output

`results/throughput/<timestamp>-kevmlx-kev/` has the llama.cpp runner's layout
and `summary.json` shape, with `runtime: kevmlx`, plus these files:

- `kev-provenance.json` and `requirements.freeze.txt`.
- In each cell, `models.json`, the `/v1/models` card at startup.

Each cell summary also differs from the llama.cpp runner's:

- `metrics.measured_delta` holds kev.serve's counters (batches, batched
  requests, prefix hits and misses) instead of llama.cpp `/metrics`.
- `kev_server` holds the batch `latency_ms` and requests per batch.
- `cache_check` holds the planned and observed cache accounting.
- `label_agreement` is within-runtime; `label_agreement_cross_runtime` is the
  diagnostic.
- `split` holds the A/B figures (cached-state cell only).

Cell arms are `kevmlx` and `kevmlx_cached`, so `report.py` never pairs these
cells with llama.cpp cells.

## Running

```sh
python3 eval/throughput/kevmlx_setup.py            # network, once: source, venv, ~9.5 GB of weights
python3 eval/throughput/kevmlx_setup.py --verify   # offline re-check of every recorded byte
python3 eval/throughput/run_kevmlx.py --validate   # offline: frozen inputs, request mapping, plan
python3 eval/throughput/run_kevmlx.py              # every cell; --cells / --workload / --output as in run.py
python3 -m unittest discover -s eval/throughput -p 'test_kevmlx*.py'
```

## Limits

- Nothing here has loaded the model yet. Several things are known only from
  the source until a run shows them:
  - that the offline load resolves the base tokenizer, config and weights from
    `.kev/hf`;
  - the load time and memory peak;
  - the dtype actually loaded.
- GitHub does not promise byte-stable archives. The archive's SHA-256 is
  trusted on first fetch; the pinned blob SHA-1s are what bind the code to the
  commit.
- Kev's dependency ranges are open. `--exclude-newer` makes the resolution
  repeatable, and the freeze records it, but it is not Kev's own lockfile.
- Peak RSS is the largest reaped child's lifetime peak, cumulative across
  cells, and does not count all Metal allocations in unified memory.
- The general limits in the [README](README.md) apply: small authored sets on
  one shared laptop with no thermal control, loopback latency only, and Metal
  on ARM64 only.

[kev]: https://github.com/jaredpalmer/kev/tree/5e42a7a03f28134853dd3ff77461457e921e5ec1
[mlx-doc]: https://github.com/jaredpalmer/kev/blob/5e42a7a03f28134853dd3ff77461457e921e5ec1/kev/mlx_model.py#L1-L17
[merge]: https://github.com/jaredpalmer/kev/blob/5e42a7a03f28134853dd3ff77461457e921e5ec1/kev/mlx_model.py#L97-L125
[head]: https://github.com/jaredpalmer/kev/blob/5e42a7a03f28134853dd3ff77461457e921e5ec1/kev/mlx_model.py#L158-L163
[cache-limit]: https://github.com/jaredpalmer/kev/blob/5e42a7a03f28134853dd3ff77461457e921e5ec1/kev/mlx_model.py#L32
[min-tokens]: https://github.com/jaredpalmer/kev/blob/5e42a7a03f28134853dd3ff77461457e921e5ec1/kev/mlx_model.py#L131
[prefix]: https://github.com/jaredpalmer/kev/blob/5e42a7a03f28134853dd3ff77461457e921e5ec1/kev/mlx_model.py#L176-L215
[paths]: https://github.com/jaredpalmer/kev/blob/5e42a7a03f28134853dd3ff77461457e921e5ec1/kev/mlx_model.py#L210-L224
[one-at-a-time]: https://github.com/jaredpalmer/kev/blob/5e42a7a03f28134853dd3ff77461457e921e5ec1/kev/mlx_model.py#L226-L229
[cache-env]: https://github.com/jaredpalmer/kev/blob/5e42a7a03f28134853dd3ff77461457e921e5ec1/kev/serve.py#L27-L29
[max-batch]: https://github.com/jaredpalmer/kev/blob/5e42a7a03f28134853dd3ff77461457e921e5ec1/kev/serve.py#L34
[cache]: https://github.com/jaredpalmer/kev/blob/5e42a7a03f28134853dd3ff77461457e921e5ec1/kev/serve.py#L38-L61
[store]: https://github.com/jaredpalmer/kev/blob/5e42a7a03f28134853dd3ff77461457e921e5ec1/kev/serve.py#L80-L87
[work]: https://github.com/jaredpalmer/kev/blob/5e42a7a03f28134853dd3ff77461457e921e5ec1/kev/serve.py#L132-L170
[run]: https://github.com/jaredpalmer/kev/blob/5e42a7a03f28134853dd3ff77461457e921e5ec1/kev/serve.py#L172-L191
[latency]: https://github.com/jaredpalmer/kev/blob/5e42a7a03f28134853dd3ff77461457e921e5ec1/kev/serve.py#L186-L190
[body]: https://github.com/jaredpalmer/kev/blob/5e42a7a03f28134853dd3ff77461457e921e5ec1/kev/serve.py#L211-L220
[routes]: https://github.com/jaredpalmer/kev/blob/5e42a7a03f28134853dd3ff77461457e921e5ec1/kev/serve.py#L249-L312
[models]: https://github.com/jaredpalmer/kev/blob/5e42a7a03f28134853dd3ff77461457e921e5ec1/kev/serve.py#L298-L312
[schema]: https://github.com/jaredpalmer/kev/blob/5e42a7a03f28134853dd3ff77461457e921e5ec1/kev/api.py#L17-L46
[render]: https://github.com/jaredpalmer/kev/blob/5e42a7a03f28134853dd3ff77461457e921e5ec1/kev/api.py#L49-L55
[option]: https://github.com/jaredpalmer/kev/blob/5e42a7a03f28134853dd3ff77461457e921e5ec1/kev/api.py#L58-L59
[record]: https://github.com/jaredpalmer/kev/blob/5e42a7a03f28134853dd3ff77461457e921e5ec1/kev/api.py#L102-L117
[answers]: https://github.com/jaredpalmer/kev/blob/5e42a7a03f28134853dd3ff77461457e921e5ec1/kev/api.py#L143-L160
[escape]: https://github.com/jaredpalmer/kev/blob/5e42a7a03f28134853dd3ff77461457e921e5ec1/kev/model.py#L75-L81
[probs-one]: https://github.com/jaredpalmer/kev/blob/5e42a7a03f28134853dd3ff77461457e921e5ec1/kev/model.py#L236-L240
[claim]: https://github.com/jaredpalmer/kev/blob/5e42a7a03f28134853dd3ff77461457e921e5ec1/README.md?plain=1#L218
[claim-table]: https://github.com/jaredpalmer/kev/blob/5e42a7a03f28134853dd3ff77461457e921e5ec1/README.md?plain=1#L398-L403
[bf16]: https://github.com/jaredpalmer/kev/blob/5e42a7a03f28134853dd3ff77461457e921e5ec1/README.md?plain=1#L409
