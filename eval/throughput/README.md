# Does serving several decisions at once pay off on this laptop?

**Design 2026-10-09; no inference results yet.** This directory holds a frozen
protocol and an offline-tested harness. Nothing below is a measurement.

Every model latency in this repository today is one request at a time: one
llama.cpp slot (`-np 1`), one client, and for Kev a Go guard that admits one
inference and answers `429` to the next. That is the right shape for a person
waiting on one answer. It says nothing about background work, such as working
through a queue of answerability checks or routing decisions, where what matters
is how many decisions finish per second.

This screen asks two questions on native Apple Silicon Metal with the pinned
llama.cpp build:

1. Does any Metal serving path deliver **materially more decisions per second**
   than the one-slot serial profile, **at unchanged labels**?
2. Does the decision-model path (Kev through the native `/v1/systemone` API) gain
   **more from parallel slots or shared state** than ordinary Qwen does?

"No" is a useful answer to either. If schema-constrained Qwen batches as well as
Kev, or nothing batches, that belongs in [when to use](../../docs/when-to-use.md)
as plainly as a win would.

## Why decisions per second, not request latency

For a person waiting, the time of one request is what they feel. For a backlog,
the queue drains at the rate decisions complete. A server that takes 1.5 times
as long per request but runs four at once finishes the backlog about 2.7 times
sooner. So the yardstick is **valid decisions per measured second**. Latency is
kept as the cost side, because background work still has deadlines: p50 and p95
request time are recorded, and they include time spent queued at the server.

Kev answers several questions about one state in one request; Qwen JSON answers
them in one compound object. Counting **questions**, not requests, puts both on
the same footing: a W2 request counts three questions for either model.

## Workloads: frozen fixtures, no new prompts or labels

| | W1: independent decisions | W2: bundled questions over shared state |
| --- | --- | --- |
| Source | [`eval/answerability/heldout/cases.json`](../answerability/heldout/cases.json), SHA-256 `74066d02…6594` (as in `answerability:source:validate`) | [`eval/query-routing`](../query-routing/README.md) held-out set, verified through its `freeze.json` and `request-manifest.json` |
| Cases | The 22 of 24 cases the shared code precheck (`common_gate`) leaves unresolved; both candidate orders | 32 cases; primary normal order only |
| Requests per pass | 44 | 32 |
| Passes per cell | 1 warmup + 2 measured (the second reverses case order, as the serial trial did) | 1 warmup + 1 measured (serial Kev took a median 7.1 s per request) |
| Arms | Kev `/v1/systemone`; Qwen JSON `/v1/chat/completions`; Qwen one-token scoring `/completion` | Kev, one request with three Choice heads (operation, node, field); Qwen JSON, one compound object |
| Request builders | `answerability.prepare` → `evaluate.build_request` (Qwen keeps `cache_prompt=true, seed=0`); `compare_scoring.prepare_score` | `query-routing/runner.request_for` → `experiment.build_request` |

Request bodies are rebuilt by the original builders and checked before any run:
`task throughput:validate` confirms that all 44 W1 Kev and Qwen JSON bodies are
byte-identical to the serial run's saved requests, and that all 32 W2 bodies of
each model match both the frozen request manifest and the executed Metal run.
One-token scoring needs the runtime's own `/apply-template` and `/tokenize`, so
its token IDs are built after startup, before warmup and outside the timed
window; offline, the harness checks that its evidence text is the same text the
JSON and Kev arms receive.

### Reference labels and agreement

| Workload | Reference | Notes |
| --- | --- | --- |
| W1 | Trial-1 rows (`arm`, `case_id`, `order`, `action`) of [`docs/evidence/20261005T174941.125801Z-answerability-source-metal/comparison.json`](../../docs/evidence/20261005T174941.125801Z-answerability-source-metal/comparison.json), SHA-256 `22c40587…b606` | Trial 2 agreed with trial 1 on all 88 model rows. One-token scoring never ran serially on this pilot, so its reference is the same Qwen model's JSON label. That comparison crosses output formats. |
| W2 | `metal/result.json` inside [`docs/evidence/20261006-query-routing/metal.tar.gz`](../../docs/evidence/20261006-query-routing/README.md), member SHA-256 `e46d230e…1b8c`, archive digest from its `MANIFEST.json` | Neither `execution.json` (the frozen profile) nor `heldout.json` (gold labels) holds model selections. Raw selections are used, including the 6 Kev and 8 Qwen tuples the application rejected, because the question is whether labels move, not whether they are right. |

A question matches when its valid label equals the reference label. Errors,
invalid answers and unattempted requests count as non-matches. The total is every
planned measured question.

The W2 reference ran a different profile: `-b 1024` with prompt caching disabled
(`--no-cache-prompt --cache-reuse 0 --cache-ram 0`). This screen uses
`scripts/metal.py`'s profile (`-b 512`, server cache defaults) everywhere. The
largest Kev decision tail in that run's preflight was 233 tokens, below the
512-token limit the server enforces for one decision's options
([`server-context.cpp:3429-3437`][decision-batch]), so the requests fit. Labels
may still shift with batch size. That is why the readings compare 8×8 against
this protocol's own 1×1 cell, never against 100% agreement.

## Cells

| Cell IDs | Slots × clients | What it isolates |
| --- | --- | --- |
| `w1-kev-1x1`, `w1-qwen_json-1x1`, `w1-qwen_score-1x1` | 1 × 1 | The serial baseline in this harness; the existing profile without the guard. |
| `w1-*-4x4`, `w1-*-8x8` | 4 × 4, 8 × 8 | Parallel slots fed by as many clients: does Metal batch independent decisions usefully? |
| `w2-kev-1x1` | 1 × 1 | Three heads evaluated one after another on one slot; no prefix sharing is possible. |
| `w2-kev-4x1` | 4 × 1 | **Head grouping alone.** One client, but enough slots for a parent head and two child heads over one shared prefix. |
| `w2-kev-4x4` | 4 × 4 | Adds request concurrency. A W2 Kev request needs three free slots at once, so only one fits in four slots; this mostly adds queueing. |
| `w2-kev-4x4-kvu` | 4 × 4, `-kvu` | The same with a unified KV cache: copying the shared prefix becomes bookkeeping instead of a buffer copy. |
| `w2-kev-8x8` | 8 × 8 | Two Kev requests fit (six of eight slots); the rest wait. |
| `w2-qwen_json-1x1`, `-4x4`, `-8x8` | as named | Qwen's compound answer has no head grouping; slots can only add batching across requests. |

Clients are a `ThreadPoolExecutor` with C workers that keep up to C requests in
flight. Warmup is drained before the measured clock starts; measured passes run
as one queue. Each cell gets a fresh runtime, so no cell inherits another's caches.

## Runtime profile and slot/context semantics

The launch command is `scripts/metal.py`'s, flag for flag, except `-np N`,
`-c 4096×N`, and `-kvu` in the unified-KV cell only. A test parses `metal.py` and
fails if the shared flags drift. The runtime listens on `compare_scoring.PORT`
(18086) rather than 8085 so that `prepare_score` tokenizes against the cell's
runtime; the port does not affect inference. Before launch the harness verifies
the model lock and model bytes (`models.lock.json`, `models.baseline.lock.json`),
`.native/build.json` against `runtime_hashes()`, and holds `.native/operation.lock`.
`LLAMA_ARG_*` variables are stripped, as in `metal.py`.

Semantics in the pinned source (revision `6c59c40076c00eab49754dc955d7652d93f9e125`;
every cited file was checked byte-for-byte against the checksummed archive
`metal.py` builds from):

- `-np` sets the slot count and becomes `n_seq_max`
  ([`common/common.cpp:1656`][common-seq]). The server's own default is "auto",
  which means four slots **with** unified KV ([`common/arg.cpp:1403`][arg-auto],
  [`tools/server/server.cpp:168-173`][server-auto]). An explicit `-np` leaves
  `kv_unified` at its default `false` ([`common/common.h:578`][common-kvu]), so
  the serial profile and every cell except `-kvu` use separate per-slot caches.
- Without unified KV, the padded context is split evenly:
  `n_ctx_seq = n_ctx / n_seq_max`, padded to 256, and `n_ctx` is rounded down to
  a multiple ([`src/llama-context.cpp:292-308`][ctx-split]). Hence `-c 4096×N`
  gives every slot exactly 4,096 tokens.
- With `-kvu`, `n_ctx_seq = n_ctx` ([`src/llama-context.cpp:294-295`][ctx-split]):
  one pool that any slot may fill. A slot's limit is the whole 16,384 tokens,
  capped only by `--kv-unified-per-slot` (not set here) and the training context
  ([`server-context.cpp:4376-4384`][slot-ctx]). Total KV memory is the same as the
  4×4 cell; only the per-slot cap differs. W2 prompts are at most about 1.4K
  tokens per head, so fit is unaffected.
- Unified KV keeps one KV stream instead of one per sequence
  ([`src/llama-kv-cache.cpp:84`][kv-stream]); copying a sequence within one stream
  updates cell metadata, while a cross-stream copy moves buffer data
  ([`src/llama-kv-cache.cpp:481-512`][kv-copy]). Upstream notes unified KV can be
  slower when sequences share no long prefix ([`include/llama.h:410-413`][llama-h]).
  With the RAM prompt cache on, unified KV also clears idle slots when a new task
  starts ([`server-context.cpp:1800-1806`][idle-clear], [`2651-2654`][idle-save]).
- The runtime prints its actual layout:
  `llama_context: n_seq_max / n_ctx / n_ctx_seq / kv_unified`
  ([`src/llama-context.cpp:310-317`][ctx-log]) and
  `srv load_model: initializing, n_slots = N, n_ctx_slot = M, kv_unified = '…'`
  ([`server-context.cpp:1383-1384`][slot-log]). The harness parses these at
  startup, refuses to measure a cell whose layout differs from its plan, and
  records them (`profile.n_ctx_per_slot_observed`, `runtime.startup`, plus the
  KV, recurrent-state and compute buffer sizes). The committed serial Kev log
  shows `n_slots = 1, n_ctx_slot = 4096, kv_unified = 'false'`.

### Head grouping and prefix copy: what to look for in `runtime.log`

`-lv 4` is the trace threshold ([`common/log.h:24-26`][log-levels]): trace and
info lines appear; debug lines do not. Kev's decision type may share a prompt
([`server-decision.h:39-49`][share]). With more than one slot, `/v1/systemone`
groups a request's per-question tasks into one parent and children that share
the longest common token prefix ([`server-context.cpp:5614-5615`][group-call],
[`server-decision.cpp:810-839`][group]). With `-np 1` every group has one task.

| Log line | Meaning |
| --- | --- |
| `launching slots for parent task id_task = X with 2 child tasks` ([`2475`][parent]) | A W2 request's three heads were grouped. |
| `processing task, is_child = 1` ([`1962`][child]) | A head was placed on a child slot. |
| `copying shared prompt (N tokens) to child K` ([`3685`][copy]) | The shared prefix was evaluated once and copied; expect N near 1.1K tokens for W2. |
| `new prompt, n_ctx_slot = …, task.n_tokens = …` ([`3358`][new-prompt]) and `cached n_tokens = …, memory_seq_rm [p, end)` ([`3672`][cached]) | Per slot: prompt length and how much was reused. |
| `forcing full prompt re-processing due to lack of cache data (likely due to SWA or hybrid/recurrent memory …)` ([`3610`][forcing]) | Both models are `qwen35` hybrids with recurrent state. The committed serial Kev log shows this line on repeated W1 prompts: cross-request reuse mostly did not happen for Kev there. |
| Not visible at `-lv 4`: `not enough free slots for child tasks … defer task` ([`2628`][defer], debug) | Waiting for three free slots shows only as queueing latency. The `/metrics` `requests_deferred` value is an instantaneous gauge, not a count. |

## The guard is bypassed, deliberately

The serial Kev numbers went through `bin/semselect`, which validates a request,
admits one inference (a channel of capacity one, [`internal/guard/guard.go:69`](../../internal/guard/guard.go))
and returns `429` when busy ([`guard.go:216-224`](../../internal/guard/guard.go)),
then forwards the body bytes unchanged ([`guard.go:225`](../../internal/guard/guard.go)).
With the guard in place every overlapping request would be refused, so the
experiment could only measure the refusal. Clients therefore call llama-server
directly, exactly as every Qwen baseline already did. The Kev bodies are the ones
the guard admitted in the serial runs, and `--validate` checks they stay within
its 32 KiB body limit. Consequence: these numbers describe the runtime, not
semselect's public service. A service that wanted this throughput would need a
reviewed guard change; this screen does not make or justify one.

## Caching, recorded rather than controlled

- Requests keep their frozen cache fields: W1 Qwen JSON `cache_prompt=true`;
  one-token scoring `cache_prompt=false` (the builder's own setting); W2 Qwen JSON
  `cache_prompt=false`; Kev carries no field, so the server default applies.
- The server's RAM prompt cache is on by default (8,192 MiB, `common/common.h:638`)
  and measured passes repeat the warmup prompts. In the committed serial Qwen run,
  the first measured request reused 388 of 392 prompt tokens because warmup had
  sent the same prompt. Repeats can be friendlier than novel traffic. The 1×1
  cells have the same opportunity, so cells compare like for like, but absolute
  rates are not novel-traffic rates.
- Recorded per request where the endpoint reports it: `timings.prompt_n`
  (processed) and `timings.cache_n` (reused) for chat and completion
  ([`tools/server/README.md:1405-1421`][timings]); `/v1/systemone` reports only
  `usage.input_tokens` ([`server-context.cpp:5652-5658`][usage]). Per cell, the
  `/metrics` deltas over measured passes give processed and cached prompt tokens
  for every endpoint (`prompt_tokens_total`, `prompt_tokens_cached_total`,
  [`server-task.cpp:1537-1543`][metrics]), decode calls, and busy slots per decode.
  The last is a lifetime average upstream ([`1597-1599`][busy]), so the harness
  rebuilds the window value from the totals.

## Budgets and stop rules

- One warmup pass per cell, excluded from every metric.
- **30 s per request**, including time queued at the server. In `w2-kev-8x8` most
  clients wait for slots; a timeout there is a measured outcome, not a harness
  fault. **No retries.**
- **Three consecutive runtime errors** (non-200, timeout, connection failure)
  stop the cell. The count runs across warmup and measurement.
- **45 minutes wall clock per runtime × model**, covering startup, warmup and
  shutdown. Run both workloads in one invocation (the default) so the budget
  covers them together. The deadline also bounds the readiness wait and each
  request's timeout. When it passes, unstarted requests and cells are recorded
  `not_run` with `stop_reason: budget`.
- Every planned measured request appears exactly once in `journal.jsonl` as
  `ok`, `invalid`, `error` or `not_run`. Denominators never shrink.
- Planned load from `--validate`: Kev 8 cells, 716 requests (424 measured);
  Qwen 9 cells, 984 requests (624 measured).

## Pre-declared readings

Fixed before any inference so the results cannot be reframed afterwards. They
are **screening readings, not adoption gates**; `report.py` computes them.

1. **Batches usefully on Metal** (per workload and arm): the 8×8 cell reaches at
   least **2×** the 1×1 cell's decisions per second, with p95 request time at most
   **3×** the 1×1 p95, and label agreement (matched questions) **no more than 2
   below** the 1×1 cell's.
2. **Kev shared-state advantage**: W2 Kev questions per second at its best cell
   exceeds W2 Qwen JSON questions per second at its best cell.

A reading is evaluated only when every cell it names completed with no stop
reason and no unattempted requests; otherwise it is reported as "not
evaluable", never inferred. A "yes" says the runtime can deliver more decisions
per second on this hardware for these fixtures. It does not establish correctness,
calibration, a service-level gain or behavior on other hardware.

## Output

`results/throughput/<timestamp>-llamacpp-<model>/` holds `summary.json`
(invocation, cells and provenance), `report.md`, `working-tree.diff`, copies of
the harness and helper sources, and one directory per cell with `runtime.log`,
`requests.json`, `journal.jsonl` (raw response bytes, base64, bounded to 1 MiB)
and `summary.json`. A cell summary records planned and valid requests and
questions, errors by kind (`http_429`, `timeout`, …), measured `elapsed_s`,
`decisions_per_s`, `requests_per_s`, `request_ms` p50/p95 (nearest rank, as in
`scripts/evaluate.py`), `label_agreement` with the reference path and digest,
per-request token counts, `/metrics` snapshots and deltas, the startup layout,
the profile and command, and provenance (commit, working-tree diff digest,
dataset digests, model lock, `build.json`, hardware, timestamps). Peak RSS is
`metal.py`'s measure, the largest child lifetime peak, which is cumulative across
cells and does not show all Metal allocations; per-cell buffer sizes from the
startup log are the better memory signal.

## Comparing runtimes

Quantization differs across runtimes: this runner serves GGUF **Q4_K_M**; the
sibling runners that other work will add use **MLX 4-bit and bf16**. Weights,
kernels and numerics differ. Cross-runtime comparisons are of **throughput
shape only**: how the rate scales with slots and clients and where it saturates,
not absolute speed or label quality.

## SGLang MLX runner

**Not implemented yet.** Placeholder for a sibling runner using the same cells,
fixtures, budgets and readings. It must stay within the isolated scope and
evidence rules of [`docs/sglang-investigation.md`](../../docs/sglang-investigation.md),
record compatibility failures separately, and does not change the default runtime.

## Kev MLX runner

**Not implemented yet.** Placeholder for a sibling runner serving Kev under MLX
with the same cells, fixtures, budgets and readings. No result from it may be
read as evidence about the llama.cpp path, or the reverse.

## Running

```sh
task throughput:validate   # offline: verify inputs, rebuild requests, print plan
task throughput:test       # offline harness tests with loopback stub servers
task throughput:metal -- --model kev
task throughput:metal -- --model qwen
python3 eval/throughput/report.py results/throughput/<kev-run> results/throughput/<qwen-run>
```

A run needs `task metal:build`, `task model:fetch`, `task metal:baseline:fetch`
and a free loopback port 18086. `--cells` selects cell IDs and `--output` names
a new directory. Only one Metal operation may run at a time.

## Limits

- Small authored sets (22 and 32 cases) on one shared laptop with no thermal
  control. Repeated passes and reversed orders are correlated, not new examples.
  This is a screen, not a benchmark.
- Latency is loopback HTTP plus server queueing, not service-level latency.
- Metal on ARM64 only. Do not infer CPU Docker, AMD64 or CUDA behavior.
- One-token scoring is compared with a cross-format reference, and the W2
  reference ran a different batch and cache profile.

[decision-batch]: https://github.com/ggml-org/llama.cpp/blob/6c59c40076c00eab49754dc955d7652d93f9e125/tools/server/server-context.cpp#L3429-L3437
[common-seq]: https://github.com/ggml-org/llama.cpp/blob/6c59c40076c00eab49754dc955d7652d93f9e125/common/common.cpp#L1656
[arg-auto]: https://github.com/ggml-org/llama.cpp/blob/6c59c40076c00eab49754dc955d7652d93f9e125/common/arg.cpp#L1403
[server-auto]: https://github.com/ggml-org/llama.cpp/blob/6c59c40076c00eab49754dc955d7652d93f9e125/tools/server/server.cpp#L168-L173
[common-kvu]: https://github.com/ggml-org/llama.cpp/blob/6c59c40076c00eab49754dc955d7652d93f9e125/common/common.h#L578
[ctx-split]: https://github.com/ggml-org/llama.cpp/blob/6c59c40076c00eab49754dc955d7652d93f9e125/src/llama-context.cpp#L292-L308
[slot-ctx]: https://github.com/ggml-org/llama.cpp/blob/6c59c40076c00eab49754dc955d7652d93f9e125/tools/server/server-context.cpp#L4376-L4384
[kv-stream]: https://github.com/ggml-org/llama.cpp/blob/6c59c40076c00eab49754dc955d7652d93f9e125/src/llama-kv-cache.cpp#L84
[kv-copy]: https://github.com/ggml-org/llama.cpp/blob/6c59c40076c00eab49754dc955d7652d93f9e125/src/llama-kv-cache.cpp#L481-L512
[llama-h]: https://github.com/ggml-org/llama.cpp/blob/6c59c40076c00eab49754dc955d7652d93f9e125/include/llama.h#L410-L413
[idle-clear]: https://github.com/ggml-org/llama.cpp/blob/6c59c40076c00eab49754dc955d7652d93f9e125/tools/server/server-context.cpp#L1800-L1806
[idle-save]: https://github.com/ggml-org/llama.cpp/blob/6c59c40076c00eab49754dc955d7652d93f9e125/tools/server/server-context.cpp#L2651-L2654
[ctx-log]: https://github.com/ggml-org/llama.cpp/blob/6c59c40076c00eab49754dc955d7652d93f9e125/src/llama-context.cpp#L310-L317
[slot-log]: https://github.com/ggml-org/llama.cpp/blob/6c59c40076c00eab49754dc955d7652d93f9e125/tools/server/server-context.cpp#L1383-L1384
[log-levels]: https://github.com/ggml-org/llama.cpp/blob/6c59c40076c00eab49754dc955d7652d93f9e125/common/log.h#L24-L26
[share]: https://github.com/ggml-org/llama.cpp/blob/6c59c40076c00eab49754dc955d7652d93f9e125/tools/server/server-decision.h#L39-L49
[group-call]: https://github.com/ggml-org/llama.cpp/blob/6c59c40076c00eab49754dc955d7652d93f9e125/tools/server/server-context.cpp#L5614-L5615
[group]: https://github.com/ggml-org/llama.cpp/blob/6c59c40076c00eab49754dc955d7652d93f9e125/tools/server/server-decision.cpp#L810-L839
[parent]: https://github.com/ggml-org/llama.cpp/blob/6c59c40076c00eab49754dc955d7652d93f9e125/tools/server/server-context.cpp#L2475
[child]: https://github.com/ggml-org/llama.cpp/blob/6c59c40076c00eab49754dc955d7652d93f9e125/tools/server/server-context.cpp#L1962
[copy]: https://github.com/ggml-org/llama.cpp/blob/6c59c40076c00eab49754dc955d7652d93f9e125/tools/server/server-context.cpp#L3676-L3690
[new-prompt]: https://github.com/ggml-org/llama.cpp/blob/6c59c40076c00eab49754dc955d7652d93f9e125/tools/server/server-context.cpp#L3358
[cached]: https://github.com/ggml-org/llama.cpp/blob/6c59c40076c00eab49754dc955d7652d93f9e125/tools/server/server-context.cpp#L3672
[forcing]: https://github.com/ggml-org/llama.cpp/blob/6c59c40076c00eab49754dc955d7652d93f9e125/tools/server/server-context.cpp#L3610
[defer]: https://github.com/ggml-org/llama.cpp/blob/6c59c40076c00eab49754dc955d7652d93f9e125/tools/server/server-context.cpp#L2623-L2631
[timings]: https://github.com/ggml-org/llama.cpp/blob/6c59c40076c00eab49754dc955d7652d93f9e125/tools/server/README.md#L1405-L1421
[usage]: https://github.com/ggml-org/llama.cpp/blob/6c59c40076c00eab49754dc955d7652d93f9e125/tools/server/server-context.cpp#L5652-L5658
[metrics]: https://github.com/ggml-org/llama.cpp/blob/6c59c40076c00eab49754dc955d7652d93f9e125/tools/server/server-task.cpp#L1537-L1543
[busy]: https://github.com/ggml-org/llama.cpp/blob/6c59c40076c00eab49754dc955d7652d93f9e125/tools/server/server-task.cpp#L1597-L1599
