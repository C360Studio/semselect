# Julia CPU feasibility diagnostic

Question: **can a much smaller native decision model make CPU-only semantic
classification practical without losing the quality the caller needs?**

This is the first compatibility and cost check for Julia-1. It uses the existing
ticket-routing fixture; it is not the fresh community-evidence comparison needed
to select a model for a SemEngine application. Kev remains the service default.

## Result: CPU speed is promising; routing quality is not

The 2026-10-06 Linux/ARM64 Docker run completed on the M3 Pro laptop. All inputs
survived preflight unchanged, all three primitive response shapes passed, and
all 48 routing responses were valid.

| Measured result | Julia-1 Q8_0 on CPU |
| --- | ---: |
| Correct selections | 24/48 (50%) |
| Median / p95 request latency | 159 / 170 ms |
| Cgroup memory peak / configured cap | 530 MiB / 2 GiB |
| `unknown` selections | 39/48 |
| Unnecessary fallbacks / wrong nonfallback routes | 23 / 1 |
| Cases changing selection under option reversal | 7/24 |

All 16 expected fallback observations were correct. Only nine calls selected a
concrete route; eight of those were correct. For example, both orders returned
`unknown` for “I would like a refund for the payment I made yesterday.” Normal
order scored 11/24 and reverse order 13/24. This is excessive deferral, not just
one or two obscure errors.

**CPU-only operation is practical for these short requests. This Julia
configuration is not an adequate general ticket router.** The result separates
compute cost from useful decision quality; it establishes neither a production
use case nor a reason to replace Kev or Qwen. The simple three-head smoke correctly
selected billing for its refund message, but that does not settle why the longer
routing policy fails. Prompt sensitivity, quantization and conversion effects
have not been isolated.

Formal prompts were 195–215 tokens. The whole inference/preflight phase took
11.32 seconds; no retries or prompt changes were made. Sixteen other containers
were running, so this is a local feasibility observation. The owned runtime exited
zero with no OOM or cleanup errors. See [summary and original evidence](../../docs/evidence/20261006-julia-cpu/README.md).

## Registered scope

The [protocol](protocol.json) fixes the spending limits and diagnostic before
inference. The [model lock](models.lock.json) pins Julia-1 Q8_0, its SHA-256 and
conversion source revision. The 168 MB download is not a memory measurement.
Both the source and converted model cards declare Apache-2.0; attribution is in
[the project notices](../../THIRD_PARTY_NOTICES.md).

Use the existing pinned llama.cpp CPU image in an isolated container: four CPU
threads, four-CPU quota, 2 GiB memory limit, no GPU layers, one slot, and 1,024-token
context and physical batch. This calls the native SystemOne endpoint directly;
it does not test Julia through the Go guard or change deployment configuration.
The image's actual architecture and immutable ID are saved with each run.

The sequence is fixed:

1. Verify model, runtime and fixtures; freeze all request bodies.
2. Check every planned input for truncation before semantic inference. Check all
   three response types for valid native shapes, then one excluded routing warmup.
3. Time the first three normal-order routing cases. Continue only if every
   response is valid and their median is at most two seconds. Correctness is
   recorded but does not determine whether to continue.
4. If the gate passes, run the 24 existing ticket cases in normal and reversed
   candidate order: 48 correlated observations, excluding the gate and warmup.

The post-readiness budget is **180 seconds including token preflight**, with at
most ten seconds per HTTP call and a separate 90-second readiness limit. Stop
on the first error, invalid response, input truncation or exhausted budget.
Preserve unattempted rows and omit cohort accuracy/latency statistics when the
diagnostic is incomplete. The two-second gate limits experiment spending; it is
not a production service-level objective.

## Why input checks matter

Julia uses the pinned runtime's LAYA decision path. Its encoder needs the whole
prompt in one physical batch. The runtime also silently shortens option text
beyond 48 tokens and shortens questions/options to the model's 256-token head
budget. A successful HTTP response alone would not prove it read the full input.

[Preflight](preflight.py) checks the actual template and token boundaries from
[verified GGUF metadata](gguf-metadata.json). Before inference, the runner also
compares reconstructed token IDs with the runtime's tokenization of the complete
rendered prompt. It rejects truncation instead of shortening the fixture. The
original routing instructions, evidence and candidate descriptions stay intact.

Julia's upstream 1,024-token quality evaluation does not validate longer inputs.
Its 8,192-token architectural limit and upstream long-input smoke are separate
claims. This experiment stays at 1,024 tokens.

## Reproduce

The CPU image must already be built with the repository's pinned Dockerfile;
the runner never pulls or builds an image. This registered probe requires the
exact Linux/ARM64 image ID in `protocol.json`; a different build or architecture
needs its own reviewed protocol. Each output directory must be new.

```sh
task julia:fetch
task julia:test
task julia:cpu -- --output results/julia/cpu-attempt-1
```

Evidence includes original requests/responses, token preflights, source and
artifact hashes, runtime logs, cgroup memory peak, run limits and verified
shutdown. Other active Docker workloads are recorded as possible contention.
Historical Kev/Qwen timing used different profiles and precision; this is not
an intrinsic speedup measurement against those runs.

## What this can decide

A complete, fast run establishes a promising CPU operating point for these short
requests. Correct labels on this public, repeatedly inspected fixture are a
diagnostic, not a held-out quality qualification. A fast but inaccurate result
does not make the model useful. A failed or slow result does not establish that
all CPU-only classifiers are impractical.

This diagnostic completes the small-model CPU investigation in the current
research phase. A later adoption test is conditional on a real caller need and
would require fresh evidence, explicit error limits and the strongest applicable
baselines. See the [closeout](../../docs/when-to-use.md#research-closeout-and-reopening-conditions). Production throughput, calibration, longer passages, Metal, AMD64 and
CUDA are outside this CPU probe.
