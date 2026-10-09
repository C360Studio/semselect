# Metal throughput screen on llama.cpp, SGLang MLX and Kev MLX: 2026-10-09

On this Apple M3 Pro, more llama.cpp slots and more concurrent clients did not
make prompt processing faster for either model. Prompt processing ran at about
440 to 570 tokens per second in every cell, so with fresh evidence in each
request every path landed near **one decision per second**. Kev gained only
from grouping three questions over one shared state: **2.35× faster with
identical labels**, and still slower than Qwen JSON. Qwen JSON gained from
extra slots only where a warm prefix cache had already removed most of each
prompt. Neither pre-declared llama.cpp reading came out yes.

SGLang MLX served Qwen at one running request only: the one cell tried with
four running requests crashed its scheduler, and at one request its two
no-decode endpoints ran at 0.98 and 0.99 decisions per second against 0.78 for
JSON. Kev's own MLX server, at bf16, ran every planned cell: more clients added
nothing (1.18 to 1.19 decisions per second on W1), but once it had cached a
state, **each further question cost about 365 ms, against 1,970 ms for one
question on a new state**, and its pre-declared cached-state reading came out
yes. That cache exists only in Kev's server; llama.cpp reused no Kev state
across requests.

This is a small screen on one shared laptop, not a benchmark. It says nothing
about CUDA hardware, Jev or decision quality. The frozen protocol, both
amendments and the source citations are in
[`eval/throughput/README.md`](../eval/throughput/README.md). See the
[results history](results.md) for earlier runs.

Labels used below: **measured** means read from the saved run summaries and
runtime logs linked from each section; **probed** means a one-fixture check
that an endpoint answers; **from the pinned source** means taken from the
llama.cpp, SGLang or Kev source the protocols cite, not measured;
**inference** marks a reading the cells do not isolate; **author-reported**
figures are not ours.

## Method

- **Hardware.** Apple M3 Pro, 36 GiB unified memory, macOS 26.5.2 arm64,
  native Metal. A shared laptop with no thermal control or resource quotas. The
  harness holds `.native/operation.lock`, so one Metal operation ran at a time,
  and each cell started a fresh runtime.
- **Runtime.** llama.cpp `6c59c40076c00eab49754dc955d7652d93f9e125`, the
  pinned Metal build (`-DGGML_METAL=ON -DGGML_METAL_EMBED_LIBRARY=ON`, CMake
  4.1.2); `build.json` and its hashes are in every run summary. Every cell's
  log reports 33/33 layers offloaded to `MTL0 (Apple M3 Pro)`; every runtime
  exited 0 with no cleanup errors.
- **Models.** Kev-4B Q4_K_M (`ggml-org/Kev-4B-GGUF` revision `d924f2e2…`,
  SHA-256 `33ae6b18…`) and Qwen3.5-4B Q4_K_M (`unsloth/Qwen3.5-4B-GGUF`
  revision `e87f1764…`, SHA-256 `00fe7986…`), verified against
  `models.lock.json` and `models.baseline.lock.json` before each run.
- **Launch profile.** `scripts/metal.py`'s flags (`-ngl 99 -t 4 -tb 4 -b 512
  -ub 512 --no-context-shift --metrics -lv 4`) with `-np N` and `-c 4096×N`,
  so every slot holds 4,096 tokens. One cell adds `-kvu`; the two Amendment 2
  cells use `-b 4096` with `-ub 512` unchanged.
- **Workloads.** W1: the 22 source-evidence cases the code precheck leaves
  unresolved, both candidate orders, 44 requests per pass, one warmup pass and
  two measured passes (88 measured requests). W2: the 32-case query-routing
  set, three questions per request (Kev: three Choice heads; Qwen: one compound
  JSON object), one warmup and one measured pass (32 requests, 96 questions).
  Kev and Qwen JSON request bodies are byte-identical to the serial runs
  (`task throughput:validate`).
- **Arms.** Kev through the native `/v1/systemone` API; Qwen JSON through
  `/v1/chat/completions`; Qwen one-token scoring through `/completion` (W1
  only).
- **Load and stops.** C client threads keep up to C requests in flight. 30 s per
  request including time queued at the server; no retries; three consecutive
  runtime errors in the measured passes stop a cell (from protocol version 2;
  version 1 also counted warmup); 45 minutes per runtime and model.
- **Yardstick.** Valid questions per measured second. Request time p50 (median)
  and p95 (nearest rank), including queueing. Label agreement with the serial
  runs' labels, counting errors and unattempted requests as non-matches. Prompt
  tokens processed and cached, decode calls and busy slots per decode from the
  runtime's `/metrics` delta over the measured passes.

| Run | Model | Protocol version | semselect commit | Cells | Time (UTC) | Run status |
| --- | --- | ---: | --- | ---: | --- | --- |
| `20261009T132345.224681Z` | Kev | 1 | `ded19f1` | 8 | 13:23:45 to 13:47:28 | stopped: two cells stopped |
| `20261009T140002.987772Z` | Qwen | 3 | `454405f` | 10 | 14:00:03 to 14:18:52 | stopped: one cell stopped |
| `20261009T141930.309707Z` | Kev | 3 | `454405f` | 3 | 14:19:30 to 14:23:42 | stopped: one stopped, one refused |

A run is `stopped` when any of its cells did not complete. All three ran from a
clean working tree (empty `working-tree.diff`), and none reached the 45-minute
budget.

**The guard is bypassed, deliberately.** semselect's Go guard admits one
inference and answers a second with HTTP 429, so with the guard in place every
overlapping request would be refused. Clients called llama-server directly, as
every Qwen baseline already did, with the same Kev bodies the guard admitted in
the serial runs. These numbers describe the runtime, not semselect's public
service. Serving this throughput through semselect would need a reviewed guard
change; this screen does not make or justify one.

## Results

Generated by `report.py` from the three runs together; numbers are copied
unchanged. Values a cell did not produce are shown as n/a (the script prints a
dash). Questions/s counts valid answers over measured passes; a W2 request
counts three questions. A p95 of 30,003 ms is the 30 s timeout, not a service
time.

| Cell | Slots × clients | Unified KV | Valid / planned questions | Questions/s | Requests/s | p50 ms | p95 ms | Label agreement | Prompt tokens processed / cached | Busy slots per decode | Warmup ok / errors / attempted / planned | Status |
| --- | ---: | :---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `w1-kev-1x1` | 1 × 1 | no | 88 / 88 | 0.94 | 0.94 | 939 | 1,458 | 88 / 88 | 47,908 / 0 | 1.00 | 44 / 0 / 44 / 44 | complete |
| `w1-kev-4x4` | 4 × 4 | no | 86 / 88 | 0.90 | 0.90 | 3,035 | 12,168 | 86 / 88 | 47,511 / 0 | 3.24 | 43 / 1 / 44 / 44 | complete |
| `w1-kev-8x8` | 8 × 8 | no | 33 / 88 | 0.88 | 0.88 | 3,037 | 30,003 | 33 / 88 | 18,749 / 0 | 6.65 | 41 / 3 / 44 / 44 | stopped (three consecutive runtime errors) |
| `w2-kev-1x1` | 1 × 1 | no | 96 / 96 | 0.43 | 0.14 | 6,998 | 7,005 | 96 / 96 | 116,057 / 0 | 1.00 | 32 / 0 / 32 / 32 | complete |
| `w2-kev-4x1` | 4 × 1 | no | 96 / 96 | 1.01 | 0.34 | 2,972 | 2,974 | 96 / 96 | 47,731 / 68,326 | 2.80 | 32 / 0 / 32 / 32 | complete |
| `w2-kev-4x4` | 4 × 4 | no | 84 / 96 | 1.01 | 0.34 | 5,935 | 30,003 | 84 / 96 | 41,768 / 59,792 | 2.80 | 28 / 4 / 32 / 32 | complete |
| `w2-kev-4x4-kvu` | 4 × 4 | yes | 84 / 96 | 1.01 | 0.34 | 5,922 | 30,003 | 84 / 96 | 41,770 / 59,796 | 2.80 | 28 / 4 / 32 / 32 | complete |
| `w2-kev-8x8` | 8 × 8 | no | 75 / 96 | 0.89 | 0.30 | 19,678 | 30,003 | 75 / 96 | 41,831 / 59,794 | 6.48 | 26 / 6 / 32 / 32 | complete |
| `w1-qwen_json-1x1` | 1 × 1 | no | 88 / 88 | 1.06 | 1.06 | 883 | 1,363 | 88 / 88 | 30,265 / 20,195 | 1.00 | 44 / 0 / 44 / 44 | complete |
| `w1-qwen_json-4x4` | 4 × 4 | no | 88 / 88 | 2.42 | 2.42 | 1,036 | 3,557 | 88 / 88 | 10,387 / 40,073 | 3.71 | 44 / 0 / 44 / 44 | complete |
| `w1-qwen_json-8x8` | 8 × 8 | no | 88 / 88 | 2.88 | 2.88 | 2,164 | 6,527 | 88 / 88 | 10,680 / 39,780 | 7.48 | 44 / 0 / 44 / 44 | complete |
| `w1-qwen_json-8x8-b4096` | 8 × 8 | no | 88 / 88 | 4.43 | 4.43 | 917 | 4,581 | 88 / 88 | 6,170 / 44,290 | 7.93 | 44 / 0 / 44 / 44 | complete |
| `w1-qwen_score-1x1` | 1 × 1 | no | 88 / 88 | 0.97 | 0.97 | 930 | 1,390 | 60 / 88 | 50,460 / 0 | 1.00 | 44 / 0 / 44 / 44 | complete |
| `w1-qwen_score-4x4` | 4 × 4 | no | 88 / 88 | 0.96 | 0.96 | 3,515 | 8,400 | 60 / 88 | 50,460 / 0 | 3.46 | 44 / 0 / 44 / 44 | complete |
| `w1-qwen_score-8x8` | 8 × 8 | no | 32 / 88 | 0.92 | 0.92 | 3,849 | 30,003 | 22 / 88 | 18,884 / 0 | 7.08 | 40 / 4 / 44 / 44 | stopped (three consecutive runtime errors) |
| `w2-qwen_json-1x1` | 1 × 1 | no | 96 / 96 | 1.30 | 0.43 | 2,297 | 2,401 | 96 / 96 | 34,899 / 0 | 1.00 | 32 / 0 / 32 / 32 | complete |
| `w2-qwen_json-4x4` | 4 × 4 | no | 96 / 96 | 1.39 | 0.46 | 8,593 | 12,611 | 96 / 96 | 34,899 / 0 | 3.81 | 32 / 0 / 32 / 32 | complete |
| `w2-qwen_json-8x8` | 8 × 8 | no | 96 / 96 | 1.38 | 0.46 | 17,079 | 27,443 | 96 / 96 | 34,899 / 0 | 7.13 | 32 / 0 / 32 / 32 | complete |
| `w1-kev-8x8-b4096` | 8 × 8 | no | 0 / 88 | n/a | n/a | n/a | n/a | 0 / 88 | n/a / n/a | n/a | 0 / 0 / 0 / 44 | failed (RuntimeError: runtime slot/context/batch layout differs from the cell (observed, expected): {'n_batch': (512, 4096)}) |

### Cell sources

`report.py` takes each cell ID from the highest protocol version, then the
latest start. Two Kev cells were rerun under protocol version 3. Their version 1
results are kept below as recorded and are not used in the readings.

| Cell | Run | Protocol version | Started | Superseded |
| --- | --- | ---: | --- | --- |
| `w1-kev-1x1` | 20261009T132345.224681Z | 1 | 2026-10-09T13:23:45.300848+00:00 | n/a |
| `w1-kev-4x4` | 20261009T132345.224681Z | 1 | 2026-10-09T13:26:07.253586+00:00 | n/a |
| `w1-kev-8x8` | 20261009T141930.309707Z | 3 | 2026-10-09T14:19:30.366435+00:00 | 20261009T132345.224681Z (protocol version 1, stopped) |
| `w2-kev-1x1` | 20261009T132345.224681Z | 1 | 2026-10-09T13:29:06.506526+00:00 | n/a |
| `w2-kev-4x1` | 20261009T132345.224681Z | 1 | 2026-10-09T13:36:35.359260+00:00 | n/a |
| `w2-kev-4x4` | 20261009T132345.224681Z | 1 | 2026-10-09T13:39:46.700555+00:00 | n/a |
| `w2-kev-4x4-kvu` | 20261009T132345.224681Z | 1 | 2026-10-09T13:42:33.966846+00:00 | n/a |
| `w2-kev-8x8` | 20261009T141930.309707Z | 3 | 2026-10-09T14:20:55.885708+00:00 | 20261009T132345.224681Z (protocol version 1, stopped) |
| `w1-qwen_json-1x1` | 20261009T140002.987772Z | 3 | 2026-10-09T14:00:03.044660+00:00 | n/a |
| `w1-qwen_json-4x4` | 20261009T140002.987772Z | 3 | 2026-10-09T14:02:14.977762+00:00 | n/a |
| `w1-qwen_json-8x8` | 20261009T140002.987772Z | 3 | 2026-10-09T14:03:32.541849+00:00 | n/a |
| `w1-qwen_json-8x8-b4096` | 20261009T140002.987772Z | 3 | 2026-10-09T14:04:45.368422+00:00 | n/a |
| `w1-qwen_score-1x1` | 20261009T140002.987772Z | 3 | 2026-10-09T14:05:44.812133+00:00 | n/a |
| `w1-qwen_score-4x4` | 20261009T140002.987772Z | 3 | 2026-10-09T14:08:01.723493+00:00 | n/a |
| `w1-qwen_score-8x8` | 20261009T140002.987772Z | 3 | 2026-10-09T14:10:21.196464+00:00 | n/a |
| `w2-qwen_json-1x1` | 20261009T140002.987772Z | 3 | 2026-10-09T14:11:43.148745+00:00 | n/a |
| `w2-qwen_json-4x4` | 20261009T140002.987772Z | 3 | 2026-10-09T14:14:12.539916+00:00 | n/a |
| `w2-qwen_json-8x8` | 20261009T140002.987772Z | 3 | 2026-10-09T14:16:32.063641+00:00 | n/a |
| `w1-kev-8x8-b4096` | 20261009T141930.309707Z | 3 | 2026-10-09T14:20:54.742119+00:00 | n/a |

Superseded cells, as recorded:

| Cell | Slots × clients | Unified KV | Valid / planned questions | Questions/s | Requests/s | p50 ms | p95 ms | Label agreement | Prompt tokens processed / cached | Busy slots per decode | Warmup ok / errors / attempted / planned | Status |
| --- | ---: | :---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `w1-kev-8x8` | 8 × 8 | no | 0 / 88 | n/a | n/a | n/a | n/a | 0 / 88 | 0 / 0 | n/a | 30 / 5 / 35 / 44 | stopped (three consecutive runtime errors) |
| `w2-kev-8x8` | 8 × 8 | no | 39 / 96 | 0.90 | 0.30 | 14,634 | 30,003 | 39 / 96 | 21,436 / 29,884 | 6.30 | 25 / 7 / 32 / 32 | stopped (three consecutive runtime errors) |

### Pre-declared readings

Fixed before any inference; screening readings, not adoption gates. As printed
by `report.py`:

8×8 means the `-b 512` cells; the `-b4096` cells (amendment 2) are a diagnostic,
shown in the table only.

| Reading | Runs (1×1 / 8×8) | Speedup (8×8 / 1×1) | p95 ratio | Agreement drop | Result |
| --- | --- | ---: | ---: | ---: | --- |
| W1 kev batches usefully | 20261009T132345.224681Z / 20261009T141930.309707Z | n/a | n/a | n/a | not evaluable |
| W1 qwen_json batches usefully | 20261009T140002.987772Z / 20261009T140002.987772Z | 2.71 | 4.79 | 0 | no |
| W1 qwen_score batches usefully | 20261009T140002.987772Z / 20261009T140002.987772Z | n/a | n/a | n/a | not evaluable |
| W2 kev batches usefully | 20261009T132345.224681Z / 20261009T141930.309707Z | 2.07 | 4.28 | 21 | no |
| W2 qwen_json batches usefully | 20261009T140002.987772Z / 20261009T140002.987772Z | 1.07 | 11.43 | 0 | no |

Kev shared-state advantage: **no** (Kev best `w2-kev-4x4-kvu` run
20261009T132345.224681Z 1.01 questions/s vs Qwen JSON best `w2-qwen_json-4x4`
run 20261009T140002.987772Z 1.39)

- **W1 Kev and W1 one-token scoring: not evaluable.** Their 8×8 cells stopped,
  and the protocol forbids inferring a reading from a stopped cell.
- **W1 Qwen JSON: no.** 2.71× the 1×1 rate clears the 2× bar, but p95 grew
  4.79×, past the 3× bound.
- **W2 Kev: no.** 2.07× clears the throughput bar, but p95 grew 4.28× and 21
  fewer questions matched (the 7 timeouts). The gain over 1×1 is head grouping,
  which `w2-kev-4x1` reaches with one client (finding 4), not batching.
- **W2 Qwen JSON: no.** 1.07× the 1×1 rate, with p95 11.43× longer.
- **Kev shared-state advantage: no.** Kev's best W2 cell reached 1.01
  questions/s; Qwen JSON's reached 1.39.

## What each finding establishes

### 1. The harness reproduces the serial runs (measured)

| Cell | This screen, p50 / p95 | Serial record, p50 / p95 | Serial source |
| --- | ---: | ---: | --- |
| `w1-kev-1x1` | 939 / 1,458 ms | 965 / 1,484 ms | [source-evidence pilot](validation-answerability-source.md), all 88 model calls |
| `w1-qwen_json-1x1` | 883 / 1,363 ms | 918 / 1,562 ms | same |
| `w2-kev-1x1` | 6,998 / 7,005 ms | 7,111 / 7,121 ms | [query-routing Metal run](../eval/query-routing/README.md) |
| `w2-qwen_json-1x1` | 2,297 / 2,401 ms | 2,381 / 2,487 ms | same |

Every 1×1 cell of Kev and Qwen JSON matched all of its serial labels (88/88 or
96/96). The serial Kev calls went through the guard and the serial W2 run used
`-b 1024` with caching off, so small timing differences are expected. The
one-slot cells are a fair baseline for the multi-slot cells.

Across every cell, every valid Kev and Qwen JSON answer matched its serial
label; each non-match in those arms is a timeout or an unattempted request.

### 2. Prompt processing sets the rate, and nothing made it faster (measured)

Prompt tokens processed per second of prompt processing, from each cell's
`/metrics` delta (`prompt_tokens_total / prompt_seconds_total`, the same
figure Amendment 2 used; `report.py` does not print it):

| Arm | 1 slot | 4 slots | 8 slots |
| --- | ---: | ---: | ---: |
| Kev W1 | 512 | 500 | 500 (stopped) |
| Kev W2 | 519 | 503 (4×1), 503 (4×4), 504 (unified KV) | 496 |
| Qwen JSON W1 | 543 | 442 | 453; 524 at `-b 4096` |
| Qwen one-token scoring W1 | 563 | 553 | 546 (stopped) |
| Qwen JSON W2 | 567 | 533 | 515 |

The cells with no cache reuse tell the same story in wall-clock terms: Kev,
one-token scoring and W2 Qwen JSON at 4 and 8 slots processed 496 to 558 prompt
tokens per measured second. Slots were in use, not idle: Kev W1 averaged 3.24
busy slots per decode at 4 slots and 6.65 at 8. Extra slots added queueing,
not prompt throughput.

Prompt processing is most of the cost. It is all of Kev's busy time (Kev
generates nothing) and nearly all of one-token scoring's. At one slot it took
84% of W2 Qwen JSON's busy time and 68% of W1 Qwen JSON's, where a warm cache
had already removed 40% of the prompt tokens (prompt seconds against
generation seconds in `/metrics`).

### 3. Kev gains nothing from more slots (measured; cause in the pinned source)

- W1: 0.94 decisions/s at 1×1, 0.90 at 4×4 (2 timeouts) and starved at 8×8.
  The version 1 cell stopped in warmup. The version 3 rerun stopped when four
  requests of its first measured wave timed out; 33 requests completed validly
  (0.88/s).
- W2: 1.01 questions/s at 4×1, 4×4 (4 timeouts) and 4×4 with unified KV (4
  timeouts); 0.89 at 8×8 with 7 timeouts and a 19.7 s median.

**From the pinned source**, confirmed by the build's own log: a decision model
loads in embedding mode, and with embeddings on, llama.cpp sets `n_batch` to
`n_ubatch` ([`common/common.cpp:1246-1266`][kev-embd]). Embedding mode makes
every token an output ([`src/llama-context.cpp:1736-1737`][output-all]), so the
hybrid memory cuts every compute step by sequence
([`src/llama-memory-hybrid.cpp:74-90`][hybrid-split]), and a sequence-split step
holds one slot's tokens only ([`src/llama-batch.cpp:774-813`][split-seq]). On
this build Kev evaluates one slot's prompt per compute step at any slot count.
The refused `w1-kev-8x8-b4096` cell's log shows the clamp directly:

```text
I decision model reads the embeddings output, enabling embedding mode
W embeddings enabled: setting n_batch = n_ubatch = 512
```

Every Kev cell log shows the embedding-mode line. The protocol's
[Amendment 2](../eval/throughput/README.md#amendment-2-2026-10-09-batch-size-cells)
gives the full chain of citations.

### 4. Kev's one gain is head grouping (measured)

`w2-kev-4x1` gives one client four slots. llama.cpp grouped each request's three
heads into a parent and two children and evaluated the shared prefix once:

| | `w2-kev-1x1` | `w2-kev-4x1` |
| --- | ---: | ---: |
| Questions/s | 0.43 | 1.01 (2.35×) |
| Median / p95 request | 6,998 / 7,005 ms | 2,972 / 2,974 ms |
| Labels matched | 96/96 | 96/96 |
| Prompt tokens processed / cached | 116,057 / 0 | 47,731 / 68,326 |

The runtime log records 64 grouped launches
(`launching slots for parent task … with 2 child tasks`) and 128 lines
`copying shared prompt (N tokens) to child`, with N from 1,065 to 1,073. The
copied tokens total 136,652 over the identical warmup and measured passes; half
of that is exactly the 68,326 cached tokens in the measured delta.

This resolves the closeout's untested `-np 3 --kv-unified` item. Grouping
needs three free slots for one request (from the pinned source); `-np 4` is
what was measured, and `-np 3` itself was not run. Unified KV added nothing
measurable: 1.011 against 1.013 questions/s, medians 5,935 against 5,922 ms.

What grouping does not do: Kev reused no prompt tokens across requests in any
cell (0 cached in every W1 cell and in `w2-kev-1x1`; the grouped cells' cached
counts are the in-request prefix copies), and the logs show `forcing full prompt
re-processing due to lack of cache data` on repeated prompts. Each request
re-reads its state. Even grouped, Kev's 1.01 questions/s and 2,972 ms median
are slower than Qwen JSON's one-slot 1.30 questions/s and 2,297 ms on the same
task.

### 5. Qwen JSON batches only with a warm prefix cache (measured)

| `w1-qwen_json-` | `1x1` | `4x4` | `8x8` | `8x8-b4096` |
| --- | ---: | ---: | ---: | ---: |
| Decisions/s | 1.06 | 2.42 | 2.88 | 4.43 |
| p50 / p95 ms | 883 / 1,363 | 1,036 / 3,557 | 2,164 / 6,527 | 917 / 4,581 |
| Labels matched | 88/88 | 88/88 | 88/88 | 88/88 |
| Prompt tokens processed | 30,265 | 10,387 | 10,680 | 6,170 |
| Prompt tokens cached | 20,195 | 40,073 | 39,780 | 44,290 |
| Decode calls (1,056 tokens generated) | 1,149 | 297 | 151 | 136 |

Zero errors in every cell. The frozen W1 Qwen JSON request sets
`cache_prompt=true`, and the measured passes repeat the warmup's prompts. As
slots increased, more of each prompt came from the cache, leaving on average a
70-token suffix plus 12 generated tokens per request in the b4096 cell. That
small remaining work did share compute steps: the same 1,056 generated tokens
took 1,149 decode calls at one slot and 136 at eight. Why more slots produced
more cache hits is not established here (inference: more slots keep more
recent prompts resident). Tail latency grew with slots.

The b4096 diagnostic is mixed. Decisions/s rose 1.54× over `w1-qwen_json-8x8`
and the median fell from 2,164 to 917 ms, which fits slots no longer waiting
behind a 512-token batch. But its prompt rate stayed at 524 tokens per second,
and it also processed 42% fewer prompt tokens because more came from the cache,
so it does not separate the two causes. Its compute buffer matched the 8×8
cell's (321.09 MiB), as Amendment 2 predicted.

### 6. Without a cache, Qwen does not batch either (measured)

- One-token scoring, W1 (`cache_prompt=false` in its frozen request): 0.97
  decisions/s at 1×1, 0.96 at 4×4, starved at 8×8 (stopped in the first
  measured wave, 32 valid at 0.92/s). Both completed cells processed 50,460
  prompt tokens with 0 cached.
- Qwen JSON, W2 (`cache_prompt=false`): 1.30, 1.39 and 1.38 questions/s at 1,
  4 and 8 slots, with medians of 2.3, 8.6 and 17.1 s. Every cell processed
  34,899 prompt tokens with 0 cached. Its 497 generated tokens took 593 decode
  calls at one slot and 100 at eight, but generation was only 12 of 74 seconds
  at one slot, so sharing it barely moved the rate.

One-token scoring matched the Qwen JSON reference on 60 of 88 requests in both
completed cells, with all 88 labels identical between `1x1` and `4x4`. This is
a cross-format difference between scoring and JSON, not an effect of slots.

### What these findings do not establish

- Not a benchmark: 22 and 32 authored cases, repeated passes and reversed
  orders are correlated, one run per cell, one shared laptop.
- Not decision quality. Label agreement shows whether labels moved against the
  serial runs, not whether they are correct.
- Not semselect's service: the guard was bypassed.
- Not novel traffic: measured passes repeat warmup prompts, which favours any
  prompt cache.
- Not other hardware or runtimes: Metal on ARM64 only. No CUDA, AMD64 or CPU
  Docker inference; SGLang MLX and Kev MLX have their own sections below;
  nothing about Jev.

## The decision-model pitch, checked on this laptop

The [README](../README.md#what-the-decision-model-pitch-claims-in-plain-language)
lists the three claims behind the pitch's speed figures.

1. **"No decode step."** True, but not where the time goes here. Prompt
   processing at about 500 tokens per second is most of the cost (finding 2).
   On the same Qwen, one-token scoring writes one token and JSON about 12, yet
   scoring was slightly slower (0.97 against 1.06 decisions/s at one slot)
   because its frozen request turns the prompt cache off and it processed 50,460
   prompt tokens to JSON's 30,265. The earlier uncached comparison, where
   scoring cut the median by 28% ([record](validation-scoring.md)), still
   stands as an output-format effect.
2. **"One state, many cheap questions."** Real, and now measured within one
   request: grouping three heads over one state made Kev 2.35× faster with
   identical labels (finding 4). It needs at least three free slots in
   llama.cpp, so the one-slot service profile cannot use it, and the state is
   still re-read on every request because Kev had zero cross-request cache hits
   on this build. Keeping the state between requests needs Kev's own server,
   where it is measured: see [Kev MLX server](#kev-mlx-server).
3. **"Batching."** It did not speed up prompt processing on this GPU at all
   (finding 2). The only cells that gained were Qwen JSON with a warm prefix
   cache (finding 5). llama.cpp's decision-model path cannot batch prompts
   across slots by construction (finding 3). Datacenter GPUs are untested.

**Consequence.** With fresh evidence in every request, every path on this M3 Pro
lands near one decision per second for a packet of 540 to 570 prompt tokens,
whichever model and whatever the slot count: Kev 0.94, one-token scoring 0.97.
Three-question bundles over a shared state of about 1.1K tokens reached 1.01
(Kev, grouped) to 1.39 (Qwen JSON) questions per second. The lever is fewer
processed tokens per decision, by bundling questions per shared state or reusing
a cached prefix, not concurrency. On this build the specialized path is slower
at throughput than ordinary Qwen. This says nothing about CUDA hardware, Jev or
decision quality.

## Stops, refusals and timeouts, as recorded

- `w1-kev-8x8`, version 1: stopped in warmup. Five of the first eight cold
  requests timed out together; 30 of 35 attempted warmup requests completed. No
  measured request was sent (0/88 valid, 88 `not_run`). This stop led to
  Amendment 1.
- `w2-kev-8x8`, version 1: stopped in measurement after three consecutive
  timeouts; 13 of 32 requests valid (39/96 questions), 16 `not_run`.
- `w1-kev-8x8`, version 3 rerun: warmup 41 ok and 3 errors, no longer a stop.
  Four requests of the first measured wave timed out at 30 s; the cell stopped
  with 33 valid, 4 timeouts and 51 `not_run`.
- `w2-kev-8x8`, version 3 rerun: complete, 25 of 32 requests valid, 7
  timeouts.
- `w1-qwen_score-8x8`: three requests of the first measured wave timed out; the
  cell stopped with 32 valid, 3 timeouts and 53 `not_run`.
- `w1-kev-8x8-b4096`: refused at startup, as Amendment 2 expected. The startup
  check found `n_batch` 512 where the cell planned 4096 and recorded a `failed`
  cell with all 88 measured requests `not_run`.
- Timeouts inside complete cells: `w1-kev-4x4` 2, `w2-kev-4x4` 4,
  `w2-kev-4x4-kvu` 4. Every error in all three runs, 27 measured and 34 in
  warmup, was a 30 s timeout.

## Amendments and why

Both amendments were made on 2026-10-09 after the version 1 Kev run and before
any Qwen cell ran; the pre-declared readings did not change.

- [Amendment 1](../eval/throughput/README.md#amendment-1-2026-10-09-warmup-errors-no-longer-trigger-the-stop):
  warmup errors no longer count toward the three-error stop. The version 1 rule
  stopped `w1-kev-8x8` on a cold warmup burst, excluded from every metric,
  before any measured request. The amendment only removes stops.
- [Amendment 2](../eval/throughput/README.md#amendment-2-2026-10-09-batch-size-cells):
  adds `w1-kev-8x8-b4096` and `w1-qwen_json-8x8-b4096` as diagnostics outside
  the readings, to ask whether a 512-token logical batch starved high-numbered
  slots. Kev's cell was kept although the startup check was expected to refuse
  it, so that the clamp is documented from the build's own log.

The two version 1 Kev cells that stopped were rerun under version 3 in their own
run; `report.py` uses the reruns and lists the originals as superseded.

## Memory, from startup logs (measured)

| Slots | KV cache | Recurrent state | Kev compute buffer | Qwen compute buffer |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 128 MiB | 50.25 MiB | 509.02 MiB | 89.21 MiB |
| 4 | 512 MiB | 201 MiB | 509.02 MiB (526.02 unified KV) | 188.59 MiB |
| 8 | 1,024 MiB | 402 MiB | 509.02 MiB | 321.09 MiB |

The largest child lifetime peak RSS was 7.09 GiB in the version 1 Kev run, 14.21
GiB in the Qwen run and 7.06 GiB in the Kev rerun. That figure is cumulative
across cells and misses some Metal allocations; the buffer sizes are the better
per-cell signal.

## Confounds

- **Prefix cache on W1 Qwen JSON only.** The frozen requests keep their cache
  fields: W1 Qwen JSON `cache_prompt=true`; one-token scoring and W2 Qwen JSON
  `cache_prompt=false`; Kev the server default, which reused nothing across
  requests. Comparisons across W1 arms are therefore not like for like.
- **Shared host.** No thermal control; background load unknown.
- **Single passes.** W2 has one measured pass per cell, and every cell ran
  once. No spread across repeated runs exists.
- **Quantization.** GGUF Q4_K_M on both models. The SGLang MLX (4-bit) and Kev
  MLX (bf16) runs use different weights and kernels; compare them by throughput
  shape only.
- **References.** The W2 reference ran `-b 1024` with caching off, and
  one-token scoring is compared with a JSON reference across output formats.
- **Mixed protocol versions.** Six Kev cells come from version 1 and three from
  version 3. Amendment 1 only removes stops and Amendment 2 only adds cells, but
  they are different runtime sessions.

## Reproduction

```sh
task metal:build
task model:fetch
task metal:baseline:fetch
task throughput:validate
task throughput:metal -- --model kev
task throughput:metal -- --model qwen
task throughput:metal -- --model kev \
  --cells w1-kev-8x8,w2-kev-8x8,w1-kev-8x8-b4096
python3 eval/throughput/report.py \
  docs/evidence/20261009T132345.224681Z-throughput-llamacpp-kev \
  docs/evidence/20261009T140002.987772Z-throughput-llamacpp-qwen \
  docs/evidence/20261009T141930.309707Z-throughput-llamacpp-kev
```

The last command reproduces every table and reading above from the published
evidence. A new run writes its own directory under `results/throughput/`.

## Evidence

- [Kev, protocol version 1](evidence/20261009T132345.224681Z-throughput-llamacpp-kev/README.md)
- [Qwen, protocol version 3](evidence/20261009T140002.987772Z-throughput-llamacpp-qwen/README.md)
- [Kev rerun, protocol version 3](evidence/20261009T141930.309707Z-throughput-llamacpp-kev/README.md)

Each holds the run summary, the executed sources, the working-tree diff and,
per cell, the summary, the request journal and the runtime log. Journals and
logs are gzip-compressed; `requests.json` is omitted because it duplicates the
frozen fixtures that `task throughput:validate` rebuilds. The SGLang MLX and
Kev MLX evidence is linked from their own sections below.

## SGLang MLX

Protocol: [`eval/throughput/sglang.md`](../eval/throughput/sglang.md). SGLang
does not serve Kev here, so every SGLang cell is Qwen. Four invocations ran
between 14:24 and 14:46 UTC, and three of them ended on a server crash.

### SGLang method

- **Runtime.** SGLang source `efb62ce269b499123e2d1c89005ee4cea8c31098` on
  its MLX backend (`SGLANG_USE_MLX=1`), from the pinned venv of the 2026-10-05
  probe. Every run verified 5,310 source files against the checksummed archive
  before launching.
- **Model.** `mlx-community/Qwen3.5-4B-4bit` revision `0e7ffd5c…`, MLX affine
  4-bit, group 64. These are not the GGUF Q4_K_M weights of the llama.cpp
  cells.
- **Launch profile.** The probe's command, flag for flag, including the flags
  added because the default path failed on 2026-10-05
  (`--disable-radix-cache`, `--disable-overlap-schedule`,
  `--mamba-radix-cache-strategy no_buffer`), plus `--mlx-enable-sampling`,
  `--grammar-backend llguidance` and `--mem-fraction-static 0.5`, at a
  4,096-token context. A cell sets `--max-running-requests N` and a token pool
  of max(8,192, 4,096 × N). Every launched cell's `/get_server_info` shows the
  planned flags, running requests and pool.
- **Re-probes.** `probe-overlap` and `probe-radix` each drop one of the first
  two flags at one running request and send the first W1 decisions request
  twice. A pass adds one 4 × 4 W1 decisions cell with that flag dropped.
- **Arms.** `qwen_json` through `/v1/chat/completions` with the llama.cpp Qwen
  JSON bodies (served model name changed, `cache_prompt` dropped);
  `qwen_decisions` through `/v1/decisions`, built from the Kev bodies by
  SGLang's own System One mapping; `qwen_score` through `/v1/score` with the
  one-token arm's text and labels, W1 only.
- **Workloads, load and stops.** As for llama.cpp, except that W1 and W2 run
  as separate invocations, each with its own 45-minute budget.
- **Yardstick.** As for llama.cpp. This profile serves no `/metrics`, so
  prompt tokens come from the scheduler's `Prefill batch` log lines and the
  busy-slot column stays empty; every call's server-reported `usage` is saved.
  Label agreement is within the runtime, against measured pass 1 of the same
  arm's 1 × 1 cell. Agreement with the serial llama.cpp Qwen JSON labels is
  kept as a cross-runtime diagnostic.

| Run | Invocation | Time (UTC) | Run status | Ended by |
| --- | --- | --- | --- | --- |
| `20261009T142410.714460Z` | W1, both re-probes first | 14:24:10 to 14:24:59 | failed | `probe-radix` crash; no cell launched |
| `20261009T142639.150858Z` | W1, `probe-overlap` only | 14:26:39 to 14:30:44 | failed | `w1-qwen_json-4x4` crash |
| `20261009T143126.613254Z` | W1, `w1-qwen_decisions-1x1` and `w1-qwen_score-1x1` | 14:31:26 to 14:36:43 | complete | n/a |
| `20261009T143712.053883Z` | W2, the two 1 × 1 cells | 14:37:12 to 14:45:39 | failed | `w2-qwen_decisions-1x1` out of memory |

All four ran semselect `454405f` with an empty `working-tree.diff`. The later
runs selected their probes and cells, as each summary's `planned_probes` and
`planned_cells` record; the command lines themselves were not saved.

### SGLang results

Generated by `report.py` from the four runs together; numbers are copied
unchanged, with n/a where the script prints a dash. Each cell ID comes from one
run: `w1-qwen_json-*` from `20261009T142639.150858Z`, the other two W1 cells
from `20261009T143126.613254Z` and both W2 cells from
`20261009T143712.053883Z`. The 0 ms request times of `w1-qwen_json-4x4` are six
refused connections, not service times.

| Cell | Slots × clients | Unified KV | Valid / planned questions | Questions/s | Requests/s | p50 ms | p95 ms | Label agreement | Prompt tokens processed / cached | Busy slots per decode | Warmup ok / errors / attempted / planned | Status |
| --- | ---: | :---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `w1-qwen_json-1x1` | 1 × 1 | no | 88 / 88 | 0.78 | 0.78 | 1,182 | 1,655 | 88 / 88 | 50,460 / 0 | n/a | 44 / 0 / 44 / 44 | complete |
| `w1-qwen_json-4x4` | 4 × 4 | no | 0 / 88 | 0.00 | 0.00 | 0 | 0 | 0 / 88 | 0 / 0 | n/a | 0 / 44 / 44 / 44 | stopped (three consecutive runtime errors) |
| `w1-qwen_decisions-1x1` | 1 × 1 | no | 88 / 88 | 0.99 | 0.99 | 926 | 1,401 | 88 / 88 | 49,932 / 0 | n/a | 44 / 0 / 44 / 44 | complete |
| `w1-qwen_score-1x1` | 1 × 1 | no | 88 / 88 | 0.98 | 0.98 | 923 | 1,387 | 88 / 88 | 50,460 / 0 | n/a | 44 / 0 / 44 / 44 | complete |
| `w2-qwen_json-1x1` | 1 × 1 | no | 96 / 96 | 1.21 | 0.40 | 2,563 | 2,669 | 96 / 96 | 34,899 / 0 | n/a | 32 / 0 / 32 / 32 | complete |
| `w2-qwen_decisions-1x1` | 1 × 1 | no | 39 / 96 | 0.40 | 0.13 | 6,564 | 11,362 | 39 / 96 | 50,826 / 0 | n/a | 32 / 0 / 32 / 32 | stopped (three consecutive runtime errors) |

Pre-declared readings, as printed by `report.py`:

| Reading | Runs (1×1 / 8×8) | Speedup (8×8 / 1×1) | p95 ratio | Agreement drop | Result |
| --- | --- | ---: | ---: | ---: | --- |
| W1 qwen_decisions batches usefully | 20261009T143126.613254Z / n/a | n/a | n/a | n/a | not evaluable |
| W1 qwen_json batches usefully | 20261009T142639.150858Z / n/a | n/a | n/a | n/a | not evaluable |
| W1 qwen_score batches usefully | 20261009T143126.613254Z / n/a | n/a | n/a | n/a | not evaluable |
| W2 qwen_decisions batches usefully | 20261009T143712.053883Z / n/a | n/a | n/a | n/a | not evaluable |
| W2 qwen_json batches usefully | 20261009T143712.053883Z / n/a | n/a | n/a | n/a | not evaluable |

Kev shared-state advantage: **not evaluable** (not evaluable: every planned W2
kev cell must complete)

No 8 × 8 cell ran, so no batching reading can be evaluated. The shared-state
reading never applied: SGLang does not serve Kev here, as the protocol says.

Server-reported tokens and both label comparisons, from each cell summary
(measured, totals over the measured passes):

| Cell | Prompt tokens | Generated tokens | Within-runtime labels | Versus serial llama.cpp Qwen JSON (diagnostic) |
| --- | ---: | ---: | ---: | ---: |
| `w1-qwen_json-1x1` | 50,460 | 1,056 | 88 / 88 | 66 / 88 |
| `w1-qwen_decisions-1x1` | 49,932 | 0 | 88 / 88 | 46 / 88 |
| `w1-qwen_score-1x1` | 50,460 | 0 | 88 / 88 | 54 / 88 |
| `w2-qwen_json-1x1` | 34,899 | 785 | 96 / 96 | 85 / 96 |
| `w2-qwen_decisions-1x1` | 48,259 (13 requests) | 0 | 39 / 96 | 32 / 96 |
| `w1-qwen_json-4x4` | none | none | 0 / 88 | 0 / 88 |

A decisions request counts its shared input once per question. The diagnostic
column crosses quantization and, for decisions and score, a different prompt
wrapper and readout. It is not an error rate and not a batching effect.

### Crashes, as recorded

Three crashes, each in SGLang's scheduler process. Each time SGLang's own
handler then killed its process tree (`kill_process_tree`, the last line of each
log). The runner's stop got `PermissionError: [Errno 1] Operation not permitted`
when it signalled the server's process group, recorded that as a cleanup error
and, by its rule, launched nothing further: `RuntimeError: owned runtime
cleanup failed; no further cell was launched`. Line numbers refer to the
decompressed runtime logs in the evidence.

1. **`probe-radix`** (run `20261009T142410.714460Z`), the one-request profile
   with `--disable-radix-cache` dropped. The server started listening, then
   crashed on its own startup warmup, before it reported ready:
   `AttributeError: 'MlxAuxiliaryStateComponent' object has no attribute
   'mamba_checkpoint_grid'`, raised in
   `mem_cache/unified_cache/components/mamba.py:176`
   (`probe-radix/runtime.log`, lines 78 to 123). It is the error that stopped
   the first probe on 2026-10-05. The probe recorded `ConnectionResetError:
   [Errno 54] Connection reset by peer` and sent neither of its requests.
   `w1-qwen_decisions-4x4-radix` was not added, and no W1 cell ran in that
   invocation.
2. **`w1-qwen_json-4x4`** (run `20261009T142639.150858Z`): four running
   requests and a 16,384-token pool, with overlap scheduling still disabled
   (its `server-info.json` shows `disable_overlap_schedule: true`). Startup
   and the server's own warmup succeeded. After the first 392-token prefill of
   the cell's warmup, the scheduler raised `RuntimeError: Expected all tensors
   to be on the same device, but found at least two devices, mps:0 and cpu!`
   in `managers/overlap_utils.py:608` (`stash`, called from
   `scheduler.py:4700`) (`w1-qwen_json-4x4/runtime.log`, lines 91 to 120). The
   four requests in flight ended `RemoteDisconnected` after about 10.8 s and
   every later request was refused: all 44 warmup requests and the 6 measured
   requests attempted failed, 82 were `not_run`, and the cell stopped on three
   consecutive errors. The same profile at one running request had just
   answered all 132 requests of `w1-qwen_json-1x1`; the only differences were
   the running-request limit and the pool.
3. **`w2-qwen_decisions-1x1`** (run `20261009T143712.053883Z`), one running
   request. The server answered 32 warmup and 13 measured three-question
   requests, the measured ones in 6.56 to 6.60 s each, then crashed in a
   prefill of measured request R14: `RuntimeError: [METAL] Command buffer
   execution failed: Insufficient Memory
   (00000008:kIOGPUCommandBufferCallbackErrorOutOfMemory)`, raised by `mx.eval`
   in `hardware_backend/mlx/kv_cache/auxiliary_state.py:77`
   (`_snapshot_cache`) (`w2-qwen_decisions-1x1/runtime.log`, lines 274 to
   317). R14 ended `RemoteDisconnected` at 11.4 s, R15 and R16 were refused,
   and the cell stopped with 39 of 96 questions valid and 16 requests
   `not_run`. Startup had reported 22.77 GB of available GPU memory; what else
   held memory on the shared laptop at 14:45 is not recorded. It was the last
   cell of its invocation.

**What was left unrun.** After the 4 × 4 crash the later invocations selected
1 × 1 cells only, so no other multi-request cell was attempted. Ten planned
cells are unrun, not failed on their own: `w1-qwen_json-8x8`;
`w1-qwen_decisions-4x4`, `-8x8` and `-4x4-overlap`; `w1-qwen_score-4x4` and
`-8x8`; `w2-qwen_json-4x4` and `-8x8`; `w2-qwen_decisions-4x4` and `-8x8`.
**At this pin, SGLang MLX serves Qwen at one running request only**: four
crashed on the first requests, and eight was never tried.

### What the SGLang cells establish

1. **Overlap scheduling answered at one running request (probed).**
   `probe-overlap` passed in both W1 invocations: both repeats answered `defer`
   for case H03 in 717 to 735 ms, with identical probabilities. That is one
   fixture. Its gated 4 × 4 cell never ran, so nothing here says the overlap
   scheduler is faster, or correct under load.
2. **The radix cache still crashes at this pin (measured).** Prefix reuse on
   MLX stays unavailable, so every SGLang cell computed every prompt token.
3. **No decode step was faster on the same server (measured).** With the cache
   off in all three arms, `/v1/score` and `/v1/decisions` reached 0.98 and 0.99
   decisions per second against 0.78 for JSON, with medians of 923 and 926 ms
   against 1,182 ms, 22% lower. JSON and scoring processed the same number of
   prompt tokens (50,460); JSON also generated 1,056 tokens, 12 per request.
   This is an output-format comparison within one runtime, and it agrees in
   direction with the earlier uncached llama.cpp comparison (28% lower median,
   [record](validation-scoring.md)).
4. **Prompt processing ran at llama.cpp's rate (measured; different
   artifacts).** The two no-decode arms processed 559 prompt tokens per
   measured second (49,932 tokens in 89.2 s; 50,460 in 90.2 s). llama.cpp's
   one-token scoring processed the same 50,460 tokens at 558 per second
   (90.4 s).
5. **Three questions re-read the input three times (measured).** Each W2
   decisions request ran one prefill per question with nothing reused, about
   3,712 prompt tokens per request, and took 6.56 to 6.60 s. That is close to
   llama.cpp Kev at one slot (6,998 ms median), which also re-read the state
   for each head. JSON on the same server answered the same three questions in
   one compound object in 2,563 ms.
6. **Labels were stable within the runtime (measured).** In each W1 1 × 1
   cell, the second measured pass matched the first on all 44 requests.

### What the SGLang cells do not establish

- Not an SGLang batching result. Every reading is not evaluable: four running
  requests crashed and eight never ran. This is a compatibility finding about
  this pin on this laptop, not a measured throughput limit of SGLang.
- Not quality. The cross-runtime counts mix quantization, kernels and prompt
  wrappers.
- Not later SGLang revisions, other hardware, or Kev on SGLang, which was not
  attempted.
- Not a memory limit: one out-of-memory event on a shared host.

### SGLang reproduction and evidence

```sh
python3 eval/throughput/run_sglang.py --validate
python3 eval/throughput/run_sglang.py --model qwen --workload w1
python3 eval/throughput/run_sglang.py --model qwen --workload w2
python3 eval/throughput/report.py \
  docs/evidence/20261009T142410.714460Z-throughput-sglang-qwen \
  docs/evidence/20261009T142639.150858Z-throughput-sglang-qwen \
  docs/evidence/20261009T143126.613254Z-throughput-sglang-qwen \
  docs/evidence/20261009T143712.053883Z-throughput-sglang-qwen
```

- [Re-probes, W1](evidence/20261009T142410.714460Z-throughput-sglang-qwen/README.md)
- [W1 JSON and the 4 × 4 crash](evidence/20261009T142639.150858Z-throughput-sglang-qwen/README.md)
- [W1 decisions and score](evidence/20261009T143126.613254Z-throughput-sglang-qwen/README.md)
- [W2 JSON and decisions](evidence/20261009T143712.053883Z-throughput-sglang-qwen/README.md)

Each holds the run summary, the executed sources and the working-tree diff;
per cell or probe, the summary or probe record, `server-info.json`,
`models.json`, `usage.json` and, gzip-compressed, the request journal and the
server log. `requests.json` is omitted, as for llama.cpp.

## Kev MLX server

Protocol: [`eval/throughput/kevmlx.md`](../eval/throughput/kevmlx.md).
`kev.serve` is Kev's reference implementation: its own encoder and pointer head
over unquantized weights, and the only runtime here that keeps a state between
requests.

### Kev MLX method

- **Pins.** Kev `jaredpalmer/kev` commit `5e42a7a0…` (Apache-2.0; 37 pinned
  files checked against their git blob SHA-1s); adapter `jaredpalmer/kev-4b`
  revision `6cfce5c2…` (14 files, 159,727,524 bytes); base
  `Qwen/Qwen3.5-4B-Base` revision `1001bb4d…` (12 files, 9,342,823,181 bytes).
  The adapter's `head.pt` records LoRA rank 16, an fp32 head and temperature
  2.406.
- **Environment.** CPython 3.13.7 in a uv venv resolved with
  `--exclude-newer 2026-10-09T00:00:00Z`: MLX 0.32.3, mlx-lm 0.31.3, torch
  2.8.0, transformers 5.19.0. The full freeze and the environment record
  (`.kev/provenance.json`, copied as `kev-provenance.json`) are in the evidence.
- **Server.** `python -I -m kev.serve --run <adapter snapshot> --fallback <same
  snapshot> --host 127.0.0.1 --port 18096`, offline, with `KEV_BACKEND=mlx`,
  `KEV_DTYPE=bf16` and `KEV_PREFIX_CACHE` set per cell. Every cell's
  `/v1/models` reported `mlx`, `bfloat16`, `mps`, LoRA 16, temperature 2.41
  and the planned cache size. A fresh server ran per cell; the first took
  30.7 s to become ready and the others about 4.9 s. Every server exited on
  SIGTERM with no cleanup error.
- **Requests.** The frozen Kev `/v1/systemone` bodies, byte for byte. The
  cached-state cell splits each W2 request into A (`operation`, on a new state)
  and B (`node` and `field`, on the state A left in the cache), spliced from the
  frozen bytes and sent in that order by one client.
- **Cells.** "1 × C" is one model thread and C clients. Six new-state cells
  with the cache off (`KEV_PREFIX_CACHE=0`) at 1, 4 and 8 clients per workload,
  and `w2-kevmlx-cached-1x1` with the default four-state cache. With 32 states
  and four cache entries each measured A should miss and each B hit; the cell
  checks the server's counters against that plan and would stop otherwise.
- **Agreement.** Within the runtime, against measured pass 1 of the workload's
  1 × 1 cell; the cached cell against `w2-kevmlx-1x1`. The serial llama.cpp Kev
  labels (Q4_K_M) are a cross-runtime diagnostic.
- **Run.** `20261009T144559.263704Z`, 14:45:59 to 15:05:34 UTC, semselect
  `2c5aad4`, clean working tree, status complete. Budgets and stop rules as
  for llama.cpp; none triggered.

### Kev MLX results

Generated by `report.py` from this run; numbers are copied unchanged, with n/a
where the script prints a dash. kev.serve serves no `/metrics`, so the token
and busy-slot columns are empty.

| Cell | Slots × clients | Unified KV | Valid / planned questions | Questions/s | Requests/s | p50 ms | p95 ms | Label agreement | Prompt tokens processed / cached | Busy slots per decode | Warmup ok / errors / attempted / planned | Status |
| --- | ---: | :---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `w1-kevmlx-1x1` | 1 × 1 | no | 88 / 88 | 1.18 | 1.18 | 773 | 1,158 | 88 / 88 | n/a / n/a | n/a | 44 / 0 / 44 / 44 | complete |
| `w1-kevmlx-1x4` | 1 × 4 | no | 88 / 88 | 1.19 | 1.19 | 2,739 | 4,807 | 88 / 88 | n/a / n/a | n/a | 44 / 0 / 44 / 44 | complete |
| `w1-kevmlx-1x8` | 1 × 8 | no | 88 / 88 | 1.19 | 1.19 | 6,486 | 8,902 | 88 / 88 | n/a / n/a | n/a | 44 / 0 / 44 / 44 | complete |
| `w2-kevmlx-1x1` | 1 × 1 | no | 96 / 96 | 0.95 | 0.32 | 3,153 | 3,164 | 96 / 96 | n/a / n/a | n/a | 32 / 0 / 32 / 32 | complete |
| `w2-kevmlx-1x4` | 1 × 4 | no | 96 / 96 | 0.95 | 0.32 | 12,604 | 12,612 | 96 / 96 | n/a / n/a | n/a | 32 / 0 / 32 / 32 | complete |
| `w2-kevmlx-1x8` | 1 × 8 | no | 96 / 96 | 0.95 | 0.32 | 25,232 | 25,239 | 96 / 96 | n/a / n/a | n/a | 32 / 0 / 32 / 32 | complete |
| `w2-kevmlx-cached-1x1` | 1 × 1 | no | 96 / 96 | 1.11 | 0.74 | 1,349 | 1,971 | 96 / 96 | n/a / n/a | n/a | 64 / 0 / 64 / 64 | complete |

`report.py`'s own readings over this run, as printed:

| Reading | Runs (1×1 / 8×8) | Speedup (8×8 / 1×1) | p95 ratio | Agreement drop | Result |
| --- | --- | ---: | ---: | ---: | --- |
| W1 kevmlx batches usefully | n/a / n/a | n/a | n/a | n/a | not evaluable |
| W2 kevmlx batches usefully | n/a / n/a | n/a | n/a | n/a | not evaluable |
| W2 kevmlx_cached batches usefully | n/a / n/a | n/a | n/a | n/a | not evaluable |

Kev shared-state advantage: **not evaluable** (not evaluable: every planned W2
kev cell must complete)

These are not evaluable by construction. `report.py` looks for a 1 × 1 and an
eight-slot, eight-client cell at llama.cpp's batch size of 512. These cells
record no batch size, so it finds neither (its runs column is empty), and this
arm has 1 × 8 rather than 8 × 8 cells anyway. For the same reason the script
prints its note on the `-b 4096` diagnostics, though this run has none. The arm's
pre-declared readings were fixed in `kevmlx.md` before inference and are
computed by `run_kevmlx.py`, which saved them in the run summary and
`report.md`:

| Reading | Speedup (1×8 / 1×1) | p95 ratio | Agreement drop | Result |
| --- | ---: | ---: | ---: | --- |
| W1 Kev MLX batches usefully | 1.00 | 7.69 | 0 | no |
| W2 Kev MLX batches usefully | 1.00 | 7.98 | 0 | no |

| Reading | A p50 ms (operation, new state) | B p50 ms (node + field, cached) | B / A | Cached ms per question | Agreement drop | Result |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Cached state is materially cheaper | 1,970 | 730 | 0.37 | 365 | 0 | yes |

kev.serve's own counters over the measured passes, from the same output:

| Cell | KEV_PREFIX_CACHE | Requests attempted / batched | Requests per server batch | Prefix hits / misses | Cache accounting | Server batch latency_ms p50 |
| --- | ---: | ---: | ---: | ---: | --- | ---: |
| `w1-kevmlx-1x1` | 0 | 88 / 88 | 1.00 | 0 / 0 | as planned | 771 |
| `w1-kevmlx-1x4` | 0 | 88 / 88 | 2.00 | 0 / 0 | as planned | 2,094 |
| `w1-kevmlx-1x8` | 0 | 88 / 88 | 4.00 | 0 / 0 | as planned | 4,834 |
| `w2-kevmlx-1x1` | 0 | 32 / 32 | 1.00 | 0 / 0 | as planned | 3,149 |
| `w2-kevmlx-1x4` | 0 | 32 / 32 | 2.00 | 0 / 0 | as planned | 9,449 |
| `w2-kevmlx-1x8` | 0 | 32 / 32 | 4.00 | 0 / 0 | as planned | 22,074 |
| `w2-kevmlx-cached-1x1` | 4 | 64 / 64 | 1.00 | 32 / 32 | as planned | 1,346 |

### What the Kev MLX cells establish

1. **More clients add no throughput (measured; cause in the pinned source).**
   W1 ran at 1.18, 1.19 and 1.19 decisions per second at 1, 4 and 8 clients,
   and W2 at 0.95 questions per second at each. The server gathered 2 and then
   4 queued requests per batch, but on MLX it runs a batch one request at a
   time ([`mlx_model.py:226-229`][kev-one-at-a-time]), and every request waits
   for its whole batch. Medians therefore grew with the client count: 773,
   2,739 and 6,486 ms on W1; 3,153, 12,604 and 25,232 ms on W2. No request
   failed and every label matched. Both batching readings: no.
2. **A cached state made further questions cheap (measured).** This tests the
   author's cached-state claim on this laptop:

   | `w2-kevmlx-cached-1x1` | A: one question, new state | B: two questions, cached state |
   | --- | ---: | ---: |
   | Requests | 32 | 32 |
   | Median / p95 request | 1,970 / 1,973 ms | 730 / 732 ms |
   | Median per question | 1,970 ms | 365 ms |
   | Valid questions | 32 / 32 | 64 / 64 |
   | Prefix cache, measured pass | 32 misses | 32 hits |

   In plain words: once Kev's server held a query's state, each further
   question cost about 365 ms, roughly a fifth of the 1,970 ms that one
   question cost when the state was new. B asked twice as many questions as A
   in 37% of A's time, and all 96 labels matched the new-state cell. Each
   request still sent the whole state, about 1,350 input tokens either way, so
   the saving is work the server skipped, not a shorter request. Over the whole
   cell, alternating A and B gave 1.11 questions per second, against 0.95 when
   every request carried a new state.

   It needed Kev's own server. On llama.cpp, Kev reused no prompt tokens across
   requests in any cell (finding 4); the cache B used exists only in kev.serve
   ([`serve.py:38-61`][kev-cache], from the pinned source). The author's M5
   figures, 721 ms for five questions on a new state of about 270 tokens and
   136 ms cached, remain author-reported. Our split asks a different number of
   questions per request over longer states on a different chip, so the two
   ratios are not comparable; the direction is the same.
3. **Kev's bf16 server answered faster than llama.cpp's Q4_K_M Kev at one
   request (measured; cause not isolated).** W1: 1.18 against 0.94 decisions
   per second, medians 773 against 939 ms; both runtimes counted 47,908 input
   tokens over the 88 measured requests, though equal counts do not show
   identical tokens. W2: 0.95 against 0.43 questions per second. kev.serve runs
   a request's state once for all its questions
   ([`mlx_model.py:176-215`][kev-prefix], from the pinned source) and counted
   about 1,770 input tokens per W2 request, where llama.cpp at one slot
   processed about 3,627 prompt tokens per request because each head re-read
   the state. llama.cpp's head grouping at four slots reached 1.01. Weights,
   precision, kernels and request encoding all differ, so none of this says
   which difference mattered.
4. **Labels (measured).** Within the runtime every cell matched its 1 × 1
   reference, and W1's two passes agreed. Against the serial llama.cpp Kev
   labels (diagnostic): 84/88 on W1, where H14 in normal order and H24 reversed
   went from `allow` to `defer` in both passes, and 95/96 on W2, where R31's
   `operation` went from `avg` to `no_override`. Precision and implementation
   differ; neither runtime is shown wrong.
5. **Memory (measured).** The largest child lifetime peak RSS, cumulative
   across cells, was 16,849,764,352 bytes (15.7 GiB) and already at that value
   after the first cell, with the caveats of the
   [memory section](#memory-from-startup-logs-measured).

### What the Kev MLX cells do not establish

- Not batching: on MLX this server runs one request at a time by construction.
- Not the cache under real traffic. Four cache entries and the A-then-B order
  guarantee a hit for every B. A caller gains only when it asks again about a
  state still among the four most recent, and it still sends the whole state.
- Not the author's longer states (8,192 and 65,000 tokens) or M5 timings.
- Not semselect's service: semselect has no Kev MLX path, and the guard was not
  involved.
- Not decision quality.

### Kev MLX reproduction and evidence

```sh
python3 eval/throughput/kevmlx_setup.py            # network, once
python3 eval/throughput/kevmlx_setup.py --verify
python3 eval/throughput/run_kevmlx.py
python3 eval/throughput/report.py \
  docs/evidence/20261009T144559.263704Z-throughput-kevmlx-kev
```

The [evidence README](evidence/20261009T144559.263704Z-throughput-kevmlx-kev/README.md)
also gives the command that re-renders the arm's own readings from the saved
summary. It holds the run summary, the executed sources, the working-tree diff,
the environment provenance and freeze and, per cell, the summary, `models.json`
and the gzip-compressed journal and server log.

## Three runtimes at one request

Each column is a different artifact: GGUF Q4_K_M on llama.cpp, MLX affine 4-bit
on SGLang and bf16 on Kev's own server, each with its own kernels and request
wrapper. Read a row as "this runtime with this artifact on this laptop", not as
a ranking of models or runtimes. Questions per measured second in the 1 × 1
cells (measured):

| Arm | llama.cpp, Q4_K_M | SGLang MLX, 4-bit | Kev MLX server, bf16 |
| --- | ---: | ---: | ---: |
| W1 Qwen JSON | 1.06 (warm prefix cache) | 0.78 (no cache) | not served |
| W1 Qwen, no decode | 0.97 (one-token scoring) | 0.98 (`/v1/score`), 0.99 (`/v1/decisions`) | not served |
| W1 Kev | 0.94 | not served | 1.18 |
| W2 Qwen JSON | 1.30 | 1.21 | not served |
| W2 Qwen decisions | no counterpart | 0.40 (stopped, 39/96) | not served |
| W2 Kev, new state | 0.43 | not served | 0.95 |
| W2 Kev, cached state (A then B) | no cross-request cache | not served | 1.11 |

llama.cpp served 20,195 of W1 Qwen JSON's 50,460 prompt tokens from its cache;
SGLang computed all of them. W2 Qwen JSON processed the same 34,899 prompt
tokens on both runtimes, and SGLang generated 785 tokens to llama.cpp's 497.

With fresh evidence in each request, every runtime landed between 0.78 and 1.18
decisions per second on W1. The larger differences follow how often a W2
request re-reads its shared state: once per question for Kev on llama.cpp at
one slot and for SGLang decisions; once per request for Kev's own server and
for Qwen JSON on either runtime; and not at all for a B request on a cached
state. On llama.cpp, sharing Kev's state within a request needs four slots
(1.01 questions per second, finding 4).

[kev-embd]: https://github.com/ggml-org/llama.cpp/blob/6c59c40076c00eab49754dc955d7652d93f9e125/common/common.cpp#L1246-L1266
[output-all]: https://github.com/ggml-org/llama.cpp/blob/6c59c40076c00eab49754dc955d7652d93f9e125/src/llama-context.cpp#L1736-L1737
[hybrid-split]: https://github.com/ggml-org/llama.cpp/blob/6c59c40076c00eab49754dc955d7652d93f9e125/src/llama-memory-hybrid.cpp#L74-L90
[split-seq]: https://github.com/ggml-org/llama.cpp/blob/6c59c40076c00eab49754dc955d7652d93f9e125/src/llama-batch.cpp#L774-L813
[kev-one-at-a-time]: https://github.com/jaredpalmer/kev/blob/5e42a7a03f28134853dd3ff77461457e921e5ec1/kev/mlx_model.py#L226-L229
[kev-prefix]: https://github.com/jaredpalmer/kev/blob/5e42a7a03f28134853dd3ff77461457e921e5ec1/kev/mlx_model.py#L176-L215
[kev-cache]: https://github.com/jaredpalmer/kev/blob/5e42a7a03f28134853dd3ff77461457e921e5ec1/kev/serve.py#L38-L61
