# Real-source answerability pilot: validation record

Run `20261005T174941.125801Z`, native Metal, 2026-10-05 17:49:41–17:52:49 UTC.
**Execution and cleanup passed. Quality is mixed.** The
[short result](../eval/answerability/heldout/README.md) explains the practical finding;
[comparison.json](evidence/20261005T174941.125801Z-answerability-source-metal/comparison.json)
contains every model request, response and decision, plus summaries and provenance.

## What was frozen

The [pre-run record](../eval/answerability/heldout/freeze.json) was written at
17:49:18 UTC, before any candidate-model inference. It specifies the unchanged
710-byte task instruction, categories, argmax/no-threshold policy, primary view,
two trials and failure accounting. Dataset SHA-256:
`74066d02a4fcff48b8363478dcbc22f9e08bc6cef5be668c859a7a50ee536594`.

Twenty-four cases span six new source families, four cases per family, with two
allow and two defer labels each. The [source manifest](../eval/answerability/heldout/sources.json)
maps all 41 passages to exact inclusive source ranges. Seven complete source files
and two repository licenses were preserved and independently checked byte-for-byte
against their pinned Git blobs. Gold labels/support and topic/source-path separation
from the teaching set were reviewed before inference. One support quote was expanded
to include its DON'T heading before the final freeze; no input or label changed.

Sources are SemStreams `1b1accf4ea4ea878c26236b5a9e6cb83d2d89d7a` and SemSource
`4093d3ce421371f4a99d7168e372552899bf6795`. These are manually selected documentation
and code excerpts with authored questions and deliberate ablations, not recorded
retrieval or traffic. “Held out” means new source families after the teaching
prompt was fixed, not a blind research test or a claim about pretraining exposure.
The six families share two related codebases. ADR questions concern the supplied
design, not certification of deployed behavior or permission to act.

H01 uses the exact `MaxAttempts: 3` literal from the supplied `DefaultConfig`
function; H02 contains only the declaration and no default fact. Code resolves
one allow and one defer. The other 22 have no supplied authoritative fact sidecars
for their requested prose/procedural details. All arms retain the same applicability
and fact checks. This sidecar is an experimental input, not a SemSource output
contract; no weak keyword classifier stands in for existing retrieval.

## Outcomes and repeatability

| Primary: trial 1 / normal | Correct / total | Unsupported allowed / 12 | Supported allowed / 12 | Supported deferred / 12 | Whole-set median / p95 | Unresolved median / p95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| No added gate | 13/24 | 11 | 12 | 0 | 0.005 / 0.008 ms | 0.005 / 0.008 ms |
| Qwen JSON | 16/24 | 7 | 11 | 1 | 928 / 1562 ms | 940 / 1562 ms |
| Kev | 19/24 | 5 | 12 | 0 | 935 / 1469 ms | 985 / 1469 ms |

Primary residual correctness is Qwen 14/22, Kev 17/22 and the control 11/22.
No invalid/error responses, unattempted cases or failure fallback deferrals occurred.
An operational failure would defer for action but would not count as a correct
semantic decision. All requested details must be supported under the shared rule;
a useful partial answer is a separate downstream outcome, not a positive gate label.

| Source family (four primary cases each) | No gate correct | Qwen correct | Kev correct |
| --- | ---: | ---: | ---: |
| Retry behavior | 3 | 3 | 3 |
| Cache eviction | 2 | 4 | 4 |
| Buffer overflow | 2 | 3 | 3 |
| Document-passage migration | 2 | 2 | 3 |
| Repository registration | 2 | 2 | 4 |
| Source retention | 2 | 2 | 2 |

Kev fixes Qwen's primary errors on H11/H16/H18/H20, but adds an error on H12.
Both wrongly allow H04/H14/H22/H24. Qwen additionally wrongly allows H16/H18/H20
and unnecessarily defers H11; Kev additionally wrongly allows H12. These errors
are stable across trials. Kev's selected-label probability is about 0.795 on the
wrong H22 allow; it is not a calibrated guarantee of correctness. No post-hoc
threshold recommendation was derived from these test outcomes.

| All four correlated views | Correct / observations | Unsupported allowed / 48 | Supported deferred / 48 | Order flips / pairs | Unresolved median / p95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| No gate | 52/96 | 44 | 0 | 0/48 | 0.004 / 0.008 ms |
| Qwen JSON | 62/96 | 30 | 4 | 6/48 | 918 / 1562 ms |
| Kev | 74/96 | 22 | 0 | 2/48 | 965 / 1484 ms |

Each model has 88 measured model calls and eight code decisions, plus one excluded
warmup. Of 48 order pairs, 44 involve model calls. Qwen flips H06/H10/H16 in each
trial; Kev flips H06. The second trial preserves every corresponding first-trial
label. Normal-order correctness is 16/24 vs 19/24; reversed is 15/24 vs 18/24.
Reversing passages, facts and candidates together is a composite perturbation;
these data cannot identify which change caused a flip. Repeats are not independent
examples and should not narrow a confidence interval as if they were.

## Runtime and resource conditions

Same cached Qwen3.5-4B and Kev-4B Q4_K_M bytes as the teaching experiment, same
llama.cpp `6c59c40076c00eab49754dc955d7652d93f9e125`. Exact model locks, native build
and binary/library hashes accompany the run. Apple M3 Pro, 36 GiB, macOS 26.5.2
arm64; four CPU threads, 4096-token context, 512 batch/microbatch, one slot,
33/33 layers offloaded, context shifting disabled. No model download or build.
Qwen chat bypasses the guard; Kev uses the guard and native SystemOne. This is a
practical service/model comparison, not isolation of training or head architecture.

Qwen explicitly enables prefix caching; native SystemOne defaults to enabled in
this runtime. Actual behavior differs: Qwen reports positive `cache_n` in 82/88
measured responses (six report zero). Kev starts all 89 warmup/measured requests
with zero cached tokens and logs 88 forced full-prompt reprocessing messages.
Later cached-token counts within a request reflect batching, not cross-request
prefix reuse. Do not
claim matched cache reuse or cold timings. The shared laptop was thermally
uncontrolled. Decision time includes preparation and HTTP/validation, excluding
disk evidence writes, startup and warmup.

| Arm | Startup | Excluded warmup | Wall including lifecycle | Child CPU |
| --- | ---: | ---: | ---: | ---: |
| Qwen JSON | 1.050 s | 1060 ms | 90.280 s | 23.993 s |
| Kev | 1.550 s | 752 ms | 97.779 s | 4.893 s |

Child CPU excludes GPU time and is not an energy measure. Both arm records contain
5,294,866,432 bytes cumulative largest-child peak RSS, not separate per-model
peaks, total memory or Metal allocation. No memory-efficiency ranking follows.

## Reproduction and checks

`task answerability:source:validate` validates the frozen dataset offline.
`task answerability:source:metal` requires that exact reviewed SHA before model
verification/loading. The run uses the same bounded lifecycle and operation lock
as the teaching runner. Model/runtime/guard hashes are verified before launch;
no fetch occurs. A changed fixture requires new review and a new frozen revision.

The run records code commit `0cd9ad0` plus its local diff and exact script/lock/dataset
snapshots. Its `fixture/` also preserves freeze/source manifests, complete source
files and licenses, with independent hashes. All 24 original files were copied
byte-for-byte into the preserved evidence directory. The original frozen record
and run are unchanged by later explanatory documentation.

Both runtime processes and the guard exited zero; both loopback ports were checked
closed after each arm. No cleanup errors occurred. `task check` passed Go race tests,
formatting, vet and Compose validation. Python ran 54 tests: 53 passed, one existing
build-descendant lifecycle check skipped due to environment `EPERM` on process-group
signals. All 19 answerability tests passed, including the new source-scope and
freeze-mismatch checks that first failed before implementation.

## What this earns

This small pilot provides an observed aggregate quality advantage for Kev over
Qwen on these inputs, with remaining errors and a case-level regression. It does
not show lower latency, calibration or a production-ready safety gate. Both models
improve on allowing every unresolved case, but that control neither reruns fusion
nor includes a generator's own refusal behavior.

The [existing-answer-path audit](answering-path.md) identifies the next comparison:
preserve the current generator's missing-information instruction and community-summary
representation, and score final unsupported assertions, useful partial answers and
refusals. A faithful prompt replay and a full current stack are distinct experiments.
CPU/Docker confirmation follows a useful downstream result before any CUDA expansion.
