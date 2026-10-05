# Actual answer-synthesis replay

This is a diagnostic on 13 known source-pilot questions, captured once from an
isolated SemSource stack and then replayed at the actual SemStreams synthesis
boundary. It is not a production benchmark or a rerun of retrieval for each arm.
See the readable [experiment](../eval/synthesis/README.md) and
[answer-path audit](answering-path.md).

## Frozen inputs and baseline

The [query plan](../eval/synthesis/query-plan.json) chose all 13 distinct questions
before acquisition. Pinned SemSource `4093d3ce421371f4a99d7168e372552899bf6795`
uses SemStreams `v1.0.0-beta.160`. A six-document public corpus produced 92 indexed
entities: six parent documents and 86 chunks. Twelve one-shot requests returned
three community summaries each. S06 returned a temporal-strategy error because
its NATS request had no responder; it remains an acquisition failure in all arms.

The [captured inputs](../eval/synthesis/captured/input.jsonl) preserve actual
CommunitySummary values, ordering and entity counts. The
[actual prompt capture](../eval/synthesis/captured/prompts.jsonl) was obtained by
calling the imported production synthesizer with a recording client and no HTTP
inference. It exactly preserves selected clusters, keywords and representatives.
The [freeze](../eval/synthesis/captured/freeze.json) was written at
2026-10-05 18:41:51 UTC, before formal runtime launch and warmups.

Independent prompt-only review found **zero fully sufficient cases**, two with
limited partial facts (S09/S11), ten with no requested answer facts, and one
acquisition failure. The [rubric](../eval/synthesis/captured/evidence-review.json)
records literal support and missing details. Strict sufficiency requires defer on
all 12 usable cases. This set cannot estimate false-deferral rate or preservation
of fully answerable cases, or show superiority to always deferring. No source-pilot
labels were transferred to the retrieved summaries.

The driver imports the actual `NewLLMAnswerSynthesizer`, preserving its instruction
to state what is known and missing, temperature 0.3, 500-token limit, 15-second
HTTP/synthesis deadline and up to three retries within that deadline. A timeout
can return a degraded deterministic template; an empty summary list bypasses the
model. Neither behavior is automatically a semantic refusal. Typed requests,
responses, fallback outcomes and per-attempt trace events are recorded. Raw
per-attempt HTTP bodies/statuses are not captured by this driver.

## Models, limits and timing

Compare three arms within each generator: unchanged synthesis, Qwen JSON gate
then unchanged synthesis, and Kev native gate then unchanged synthesis. Gates
receive the exact actual user prompt as a single passage, with no gold or answer
hint. The original 710-byte sufficiency instructions and allow/defer categories
remain unchanged. The caller performs no generation after a gate defer.

- CPU generator: cached pinned `seminstruct:qwen3-0.6b`, Linux/ARM64 Docker,
  model SHA-256 `ac2d97712095a558e31573f62f466a3f9d93990898b0ec79d7c974c1780d524a`,
  two CPUs, 2 GiB container limit, four slots and **4096 effective tokens per slot**.
  This is the shipped wiring-smoke default, not the recommended quality model.
- Metal generator: pinned [Qwen3.5-4B Q4_K_M](../models.baseline.lock.json),
  same local runtime as prior Metal experiments, thinking disabled at the server.
  This is an explicit generator substitution.
- Both gates run on native Metal in both strata. The CPU-generator stratum is
  therefore a **mixed deployment**, not a CPU-only semselect gate measurement.
  Qwen JSON uses the pinned 4B model; Kev uses the pinned
  [Kev-4B Q4_K_M](../models.lock.json). Both native models remain loaded, but
  all requests are serialized. Four CPU threads, one slot, 4096 context,
  512 batch/microbatch and full Metal offload are required.

Native runtime revision: `6c59c40076c00eab49754dc955d7652d93f9e125`.
The CPU image's runtime is separately recorded; model size, runtime and hardware
change across generator strata, so their timing difference is not a GPU speedup.

Before any warmup, actual `/apply-template` and `/tokenize` calls checked chat
prompts; the exact pinned Kev choice template and native special-token escaping
were checked separately. Chat tokenization adds model special tokens; Kev does
not. Runtime `/props` confirmed 4096 effective context. Maximum prompt tokens:
1176 Metal synthesis, 1163 CPU synthesis, 1339 Qwen gate, 1310 Kev gate. All 12 fit,
including reserved output capacity. Maximum gate state was 4740 UTF-8 bytes,
below the 8192-byte limit. No inputs were truncated or excluded for size.

Two trials preserve the predeclared arm order and reverse case order on trial 2.
Trial 1 is primary; trial 2 describes variation on the same questions, not new
independent examples. One warmup per gate and generator is recorded separately.
Every allow performs a fresh generator call. Component time is gate preparation,
HTTP/validation plus actual synthesis duration; driver startup and I/O are
recorded separately. Existing prefix-cache behavior is retained. These are not
cold, matched-cache, model-head efficiency or production latency comparisons.

## Reproduction and checks

Build the [isolated Go driver](../eval/synthesis/driver/README.md) from cached pinned
dependencies. Recreate acquisition separately with the
[documented stack](../eval/synthesis/live/README.md), or replay the frozen captured
inputs. A new acquisition is a new evidence set; it does not replace this one.
The frozen driver hash identifies this exact local build. A different platform's
binary requires an explicitly documented new freeze, not bypassing the check.

```sh
python3 -m unittest discover -s eval/synthesis -p 'test_*.py' -v
python3 eval/synthesis/replay.py \
  --input eval/synthesis/captured/input.jsonl \
  --prompts eval/synthesis/captured/prompts.jsonl \
  --acquiredstatuses eval/synthesis/captured/acquiredstatuses.json \
  --freeze eval/synthesis/captured/freeze.json \
  --driver /tmp/semselect-synthesis-driver \
  --output results/synthesis/NEW-RUN --validate
```

Validation does not launch models or prove runtime context fits. Removing
`--validate` launches the two owned Metal servers and guard, checks actual token
budgets, uses an already-running owned CPU generator on loopback port 48083,
records fresh outputs and stops the Metal processes. Existing evidence directories
are never overwritten. Generator/container lifecycle is separately recorded.

Eight Go driver race tests pass. Thirteen Python synthesis tests pass. Independent
review found and corrected two defects before formal inference: final-record
cancellation had incorrectly reported CLI success, and chat preflight had omitted
model-added special tokens. Both fixes followed failing regressions. The driver
still persists a cancelled final result before returning failure.

## Recorded outcomes and interpretation

The [complete run](evidence/20261005T184218Z-synthesis-replay/README.md) completed
18:42:18–18:48:54 UTC with exit 0. All 156 planned records are present: 55
nondegraded generated answers, 88 gate deferrals, one degraded synthesis timeout,
and 12 repeated records for the same S06 acquisition failure. All 55 successful
generations ended with `finish_reason=stop`; none exhausted the 500-token limit.
All four excluded warmups succeeded. No gate request was invalid or failed.

Both gates allowed only S09 in each of the four generator/trial views. This is
11/12 correct strict deferrals and one unsupported allow on an all-insufficient
set. It is not a balanced accuracy claim. The 4B generator's S09 answer was a
useful partial answer anyway; the 0.6B generator's S09 answer invented disk rules.
This illustrates why strict gate labels and final-answer utility are distinct.

The [readable table](../eval/synthesis/README.md) uses primary trial 1. Trial2 kept
all gate decisions and 4B category totals unchanged, but the particular 4B errors
varied. The CPU baseline's trial 1 timeout became an unsupported answer in trial 2.
The [summary](evidence/20261005T184218Z-synthesis-replay/summary.json) and its
`summarize.py` retain both views, all denominators and undefined false-deferral
rate (zero fully sufficient inputs).

Independent grading saw opaque answer IDs, queries and frozen evidence, without
model, arm, trial, latency or token usage. Root audited every grade reason/quoted
span and all eight flagged full answers before opening the identity map. All
original grades were upheld; original grading and adjudication are both preserved.
There were 31 unsupported assertions, 18 refusals and six supported partial
answers across the 55 correlated outputs. The rubric is grounding in visible
text, not truth in unseen source files. Eight boundary cases include hypothetical
language, vague advice and overconfident document pointers. One primary 4B error
fabricates a `DefaultConfig` section while correctly withholding the requested
number. Some refusals also contain ancillary pointer or topic-conflation cautions;
a refusal label does not certify every sentence. Removing that fabricated-section
case from a narrower substantive-answer-error count would make primary 4B 2/12,
rather than 3/12; both gated 4B arms remain 0/12. This small judgment-based sample
cannot establish precise error rates.

Observed primary component medians over 12 usable captures:

| Generator | No gate | Qwen JSON gate | Kev gate |
| --- | ---: | ---: | ---: |
| CPU 0.6B | 1817 ms | 1826 ms | 1974 ms |
| Metal 4B | 6819 ms | 326 ms | 1971 ms |

These timings include an actual fresh generator call only when an arm allows.
They exclude startup/I/O and are strongly dependent on repeated-input caching.
All 48 measured Qwen gate responses report positive `cache_n`: the initial CPU
pass mostly reused 183 tokens, while later passes reused 789–1335 tokens, often
almost the entire prompt. All 49 Kev requests including warmup started with
zero cached tokens; later 512-token lines are within-request batching, not reuse.
The lower Qwen repeated-pass times cannot be read as a cold or matched-cache
architectural speed advantage. Each gated arm made 12 gate calls plus 1 synthesis;
the baseline made 12 synthesis calls. Actual synthesis trace events record one
written request per attempted synthesis, including the timeout; no additional
transport retry was observed.

Metal guard and both native runtimes exited 0, with ports 18087/18088/18089 closed.
The owned CPU generator then exited 0 with no OOM and port 48083 closed. Acquisition
source/NATS had already stopped; semembed required Docker's bounded final kill,
exit 137 with OOMKilled=false. Unrelated user containers were not changed.
`task check` passed; its existing process-group-signal test remains skipped due
to environment EPERM (53 passed, 1 skipped), separate from the 8 Go-driver race and 13
synthesis Python tests. Hash/byte and raw-summary mapping verification passed for
the published acquisition package.

Independent post-run review recomputed the summary, verified all 336 published
replay-file hashes, checked frozen execution order, and matched all 56 synthesis
attempts and 96 gate payloads to their frozen inputs. Worked examples, grading
caveats, context limits and cleanup claims passed that review. Formal inference
counts were 96 gate calls plus 56 synthesis attempts, separate from four warmups;
156 planned records include deferrals and the repeated upstream failure.
