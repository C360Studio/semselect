# Evaluation results history

The [README](../README.md#latest-routing-results) shows the latest useful matched
comparison. This page preserves the complete published routing history; linked
JSON contains the per-request evidence and is the source for table values.
Detailed validation reports explain conditions, failures and interpretation.

All four runs below use the same [routing smoke dataset](../eval/routing-smoke.json),
SHA-256 `81590b0d2e0773f49b7b1c524729acb96d37a74b29ca134c710701a0037b5d2a`:
24 labeled cases, each in two candidate orders. The resulting 48 observations are
correlated. Each run excluded one warmup. Accuracy includes invalid/error calls
in its denominator. Latency covers measured calls; order flips compare valid
pairs. These are small smoke evaluations, not a production benchmark or calibration
study. Model probabilities are not established probabilities of correctness.

## 2026-10-05 — matched 4B models on native Metal

Apple M3 Pro, 36 GiB, native macOS/arm64. Both use llama.cpp
`6c59c40076c00eab49754dc955d7652d93f9e125`, Q4_K_M, four CPU threads, 4096-token
context, 512 batch/microbatch, one slot and 33/33 layers offloaded. Models ran
sequentially. See the [Metal validation report](validation-metal.md) for exact
pins, setup, failures, abstention thresholds and resource-accounting limits.

| Model / API | Correct | Valid | Median | p95 | Order flips | Recorded evidence |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Kev-4B / SystemOne through semselect | 43/48 (89.6%) | 48/48 | 449 ms | 474 ms | 2/24 | [Requests](evidence/metal-kev-4b.json), [resources](evidence/metal-kev-4b-resources.json), [runtime log](evidence/metal-kev-4b-runtime.log) |
| Qwen3.5-4B / chat directly through llama.cpp | 46/48 (95.8%) | 48/48 | 288 ms | 423 ms | 2/24 | [Requests](evidence/metal-qwen35-4b.json), [resources](evidence/metal-qwen35-4b-resources.json), [runtime log](evidence/metal-qwen35-4b-runtime.log) |

The Qwen chat comparison bypassed semselect's guard. It supports a
seminstruct-style label-routing choice; it does not establish Qwen as a replacement
for Kev behind `/v1/systemone`. Only Kev exercised native typed readouts in these
runs: [Choice/Score/Noul smoke](evidence/metal-primitives.json).
The native service also passed [health, context rejection and shutdown checks](evidence/metal-lifecycle.json).

Native launcher run IDs:

- Kev: `20261005T143608.505753Z`.
- Qwen: `20261005T143658.673689Z`.

Qwen returned more correct labels with lower median latency here. Kev's typed
distributions and Score/Noul remain the distinction to evaluate on appropriate
workloads. Neither model has established general injection robustness or domain
calibration from this set. The shared laptop's thermal state was not controlled.

## 2026-10-05 — original CPU deployment reference

Same physical M3 Pro, with inference inside Docker Desktop Linux/ARM64. Both
containers had four CPU quota/threads, 8 GiB memory limits, 4096-token context and
one slot. Models and runtime revisions differ; this is an existing-deployment
reference, not the matched 4B comparison above. See the
[CPU validation report](validation.md) for image/model hashes and measured limits.

| Model / API | Correct | Valid | Median | p95 | Order flips | Recorded evidence |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Kev-4B / SystemOne through semselect | 43/48 (89.6%) | 48/48 | 9,877 ms | 12,858 ms | 2/24 | [Requests](evidence/semselect.json), [resources](evidence/semselect-resources.json) |
| Qwen3-0.6B / existing seminstruct chat | 24/48 (50.0%) | 48/48 | 293 ms | 369 ms | 13/24 | [Requests](evidence/seminstruct.json), [resources](evidence/seminstruct-resources.json) |

Evaluator start times (UTC): Kev `2026-10-05T13:50:50.645047+00:00`; Qwen
`2026-10-05T13:59:55.961964+00:00`. The original bootstrap did not have native
launcher run IDs; retain these existing evidence filenames.

CPU cgroup memory and native process RSS measure different things. Cross-section
latency also mixes OS, VM, quota and build differences. Keep these sections
separate; do not rank all rows as an isolated model or GPU benchmark.

## Recording the next result

1. Run the existing evaluator and preserve the complete run, including errors,
   invalid responses and unfavorable results. Native evaluation already creates
   timestamped directories under `results/`; that directory is ignored by Git.
   A run is not preserved in the repository until its evidence is copied and committed.
2. Copy new publishable evidence into `docs/evidence/<unique-run-id>/`. Keep
   request/result JSON, resource/provenance JSON, native logs and primitive checks
   together. Existing flat evidence paths remain valid. Do not replace an earlier
   run's files when rerunning; record a new run. A startup failure with no measured
   requests gets a failed entry and its logs, with unavailable metrics left blank.
3. Record the evaluated semselect code commit (and any local diff), dataset hash
   and prompt/evaluator version, model revision/hash/quantization, runtime
   revision/build flags, hardware/OS/backend, launch limits, warmup, timestamps
   and commands. Record missing provenance as unknown. Frozen lockfiles or their
   exact contents must accompany a run; current lockfiles may change later.
4. Add the run to this history and link its validation notes. Copy numeric values
   from the saved JSON summary, retaining valid/total counts, correct/total,
   median/p95, candidate-order flips, and failed-check status. Keep abstention,
   conditional errors, known failure cases and resource metrics in the detailed
   report. Separate hardware, dataset, serving protocol and test-configuration
   changes rather than silently replacing a comparison. Future repeated trials
   should retain every run and report their spread, not just the fastest result.
5. Update the README when a new complete comparison changes the useful current
   reference. Link to its history/evidence and retain the older rows here. Commit
   the documentation and evidence together. Test artifacts containing private
   inputs belong in approved private storage; commit only their allowed summaries
   or access references.

At this scale, Markdown plus versioned JSON is sufficient. If the hardware/model
matrix grows, generate the tables and charts from those same saved summaries;
avoid maintaining a second spreadsheet of manually re-entered measurements.
