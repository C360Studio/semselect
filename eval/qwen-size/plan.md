# Bounded smaller-Qwen comparison — 2026-10-07

The user requests Qwen3.5-2B and Qwen3-1.7B against the app's accepted 250 ms
median / 750 ms p95 query-classification target. Test both pretrained models
without training, on CPU-only Linux ARM64, using pinned Q4_K_M artifacts and the
existing llama.cpp revision. This evaluation adds no service backend.

Reuse the specialist experiment's exact nine-operation definitions, literal
binder, 60 development queries, 120 evaluation queries, two prompt variants and
24 sensitivity IDs. Only the requested model alias changes. This is a fixed
comparison on a previously used evaluation cohort, not newly untouched data.
Do not inspect case texts to tune prompts or repair labels. A passing screen
would still need fresh confirmation before adoption.

Freeze source, fixture, model, runtime and payload hashes before inference.
Independent review must cover the plan, payload mapping, tokenizer preflight,
scoring, budgets and lifecycle before executing candidate requests. Use the
existing tested transport, strict decoder, binder and scoring functions.

Run arm order Qwen3.5-2B, then Qwen3-1.7B within each stage. Complete both
development stages before selecting each variant by the existing development
rule: at most one wrong accept and zero wrong specialized routes, then maximize
correct accepts; if no eligible variant exists, retain the best diagnostic
variant without claiming qualification. Freeze selection before evaluation.
Run three excluded warmups per stage; development also includes the twelve
designated feasibility queries. Evaluate each variant on all60 development
queries; primary has120 requests, sensitivity24 reversed labels and24 remapped
IDs. This totals309 planned requests per model before conditional load.

Use thinking disabled, temperature/seed0, constrained operation JSON, 64 output
tokens, context4096, batch1024/microbatch512, one slot, four CPU threads, CPU
affinity0-3, four-CPU quota and4GiB hard memory limit. Disable prompt caches and
audit actual logs for reuse. Verify full templated tokens with the loaded
runtime before calls; context plus output reserve must fit, never truncate.
Collect startup, cgroup lifetime peak memory, idle memory, CPU time, raw response
bytes/hashes and both HTTP and outer elapsed latency. Run one model at a time;
record contention snapshots and verify shutdown before the next stage.

Bound setup to30minutes per model, including a shared runtime build; at most one
corrective setup attempt. Readiness120seconds, request30seconds, inference work
1800seconds per model including warmups, development, sensitivity and load.
Each lifecycle controller has a1920second OS signal timer outside the Docker
inference process, with bounded subprocess waits. Stop on OOM, truncation,
invalid mapping or three consecutive runtime failures; retain errors and all
unattempted cases in denominators. No checkpoint or runtime shopping after
results. Missing execution evidence cannot pass.

Report separately:

- Absolute quality screen inherited from the specialist plan: raw correct>=108,
  correct accepted>=96, wrong accepted<=3, zero wrong specialized selections on
  no_override, zero invented bindings, all120 valid requests.
- CPU operating target: median<=250ms, p95<=750ms, peak<=2GiB and readiness<=60s.
- Historical Qwen4B retention: at least108 correct accepts (within3 of111) and
  at most8 wrong accepts. Its native Metal timing is a separate operating point,
  never a CPU speed ratio. Compare the frozen improved-rule and embedding rows
  as task-quality references without rerunning them.
- Stability: at most one outcome change in each24-case sensitivity view and no
  new wrong specialized routes. Report both candidates; do not choose a winner
  retrospectively and present it as a preselected validation result.

Only a candidate passing absolute quality, CPU cost and stability advances to a
200-request concurrency-two check, max300seconds and p95<=1500ms. This limited
queueing check describes small-team use, not production throughput. Failure of
prerequisites means load is skipped explicitly. Passing all screens means
"candidate for fresh confirmation", never automatic deployment.

Risks include CPU setup failure, runtime/tokenizer differences between Qwen
generations, instruction-following regressions, overfitting to an already used
cohort and insufficient CPU speed despite fewer parameters. Preserve failures
and historical evidence. Offline adapter/budget/cleanup tests and independent
result review validate the experiment; only real inference establishes quality.
