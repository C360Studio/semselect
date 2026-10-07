# Bounded specialist evaluation — 2026-10-07

**Verdict: keep the baseline. Do not add a specialist classification backend
from this evidence.** DeBERTa was nominated using development data before any
held-out execution. It accepted only 13 correct operations out of 120, accepted
one wrong specialized operation, and deferred 106. Its CPU latency also exceeded
the frozen budget. GLiClass did not qualify on development and remained a
diagnostic arm. This is a completed negative result, not a compatibility failure.

The evaluation-local improved rules obtained 95 correct operations; Qwen JSON
obtained 111. Their 25 and eight wrong accepted operations respectively still
require caller checks and fallback. “Keep the baseline” does not approve either
for autonomous actions or ship the experimental rules into SemStreams.

## Held-out results

All rows below use the same 120 authored queries and shared literal argument
binder. Raw correct ignores confidence thresholds; accepted correct applies the
configuration frozen on the separate 60-case development set. `no_override` is
an ordinary label and is distinct from defer or error.

| Arm | Raw correct | Accepted correct | Wrong accepted | Deferred | Errors | HTTP median / p95 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Existing SemStreams keyword | 61 | 61 | 53 | 0 | 6 | — |
| Existing keyword + BM25, default | 61 | 61 | 53 | 0 | 6 | — |
| Existing keyword + BM25, development-tuned | 61 | 61 | 53 | 0 | 6 | — |
| Evaluation-local improved rules | 95 | 95 | 25 | 0 | 0 | — |
| Existing semembed, nearest example | 34 | 0 | 0 | 120 | 0 | 23 / 29 ms |
| GLiClass, diagnostic configuration | 37 | 37 | 83 | 0 | 0 | 253 / 483 ms |
| **DeBERTa, preselected nominee** | **22** | **13** | **1** | **106** | **0** | **1,134 / 2,049 ms** |
| Qwen3.5-4B Q4_K_M JSON, Metal | 111 | 111 | 8 | 1 | 0 | 867 / 1,044 ms |

Every learned primary arm completed all 120 requests. Historical code's six
native-output errors per arm (four conflicting operations and two unsupported
SearchOptions fields) remain explicit errors in the denominator.
The chosen improved-rule comparator completed all 120. Code runs locally rather
than over HTTP: improved-rule wrapper p95 was 0.031 ms; historical code includes
a Go subprocess and had 42–49 ms timeout-inclusive wrapper p95. These are not
matched serving paths. Learned-arm timeout-inclusive outer p95 values were
79 ms for embeddings, 553 ms for GLiClass, 2,118 ms for DeBERTa and 1,098 ms for
Qwen; the table reports the common loopback HTTP boundary.

DeBERTa's frozen configuration was prompt variant 1, score threshold 0 and margin
0.2. Embeddings selected cosine 0.9, margin 0; that conservative development
choice accepted nothing on held-out data. GLiClass used unfiltered variant 0;
Qwen used variant 0. No configuration or nominee was changed after selection.
The raw embedding and specialist results show that deferral alone does not
explain their poor task fit. These observations apply to these checkpoints,
payloads and examples, not all embedding or zero-shot classifiers.

## Why the nominee failed

| Frozen gate | Required | Observed |
| --- | --- | --- |
| Raw operation quality | At least 108/120 | 22/120 |
| Useful acceptance | At least 96 correct; at most 3 wrong | 13 correct; 1 wrong |
| Wrong specialized routing on `no_override` | 0 | 1 |
| Added value over both code and embeddings | At least 12 additional correct accepts; no extra wrong accepts | −82 versus improved rules; +13 versus embeddings, with one extra wrong |
| Generalist comparison | Within 3 correct accepts of Qwen; no extra wrong accepts | 98 fewer correct accepts |
| Binding and executable plans | No invented binding; at least 6 additional correct executable plans over both baselines | 11 correct executable plans versus rules' 45; one protocol binding error |
| CPU latency | Median ≤250 ms; p95 ≤750 ms | 1,134 / 2,049 ms |
| Peak memory / cold readiness | ≤2 GiB / ≤60 s | 1.874 GiB / 4.19 s: pass |
| Sensitivity | At most one change per 24-case view; no new wrong specialized route | Zero changes in both views: pass |

The binding error is the protocol's shared-binder outcome metric; the specialist
never generates argument strings. Caller code still owns binding, authorization
and actions. The paired correct-accept difference versus improved rules is
−82/120; its seeded family-cluster 95% interval is −78.4 to −57.9 percentage
points. Raw rows, per-stratum metrics, confusion matrices, tradeoff grids and
paired case IDs remain in the evidence.

All three models completed the planned 24 reversed-order and 24 remapped-ID
checks. Operation changes were GLiClass 15/0, DeBERTa 0/0 and Qwen 2/4
(reverse/remap). Specialists do not encode opaque IDs, so their ID-remap check
tests adapter mapping. The 200-request concurrency-two load check was **not run**:
the nominee failed seven prerequisite gates, so the frozen plan did not permit
promotion through a load result. No throughput claim is made.

## Execution and limits

Specialists and semembed ran in CPU-only Linux ARM64 Docker with four CPUs,
affinity `0-3`, four inference threads and a 4 GiB hard memory limit. Specialists
used FP32, query batch one, and DeBERTa nine hypothesis pairs per query. Peaks
from container creation, including startup and warmup, were 1.176 GiB for
GLiClass, 1.874 GiB for DeBERTa and 326 MiB for embeddings. No OOM, truncation,
timeout, learned-arm runtime error or inference-budget stop occurred.

Each decision model completed 309 requests across warmups, feasibility,
development, primary and sensitivity stages. Accounted inference time was
99.34 s GLiClass, 460.46 s DeBERTa and 300.72 s Qwen, each below its 1,800 s cap.
Embedding requests plus its cold example index consumed 14.86 s of 600 s;
code consumed 30.05 s of 300 s. Independent review approved fixture labels,
all 1,568 frozen payloads, scoring and the preparation hash. Full-input tokenizer
checks covered every arm before its inference. Selection at 15:28:21 UTC
preceded the first held-out stage at 15:28:31 UTC.

The initial PyPI torch closure contained CUDA dependencies and was rejected.
The single corrective dependency attempt used official `torch==2.14.1+cpu`.
A wheel-lock parser regression exposed nested vendored metadata; its fix and
failing setup logs are preserved. The corrected hash-locked CPU image and exact
model artifacts loaded successfully within setup bounds. No further checkpoint
or dependency search was performed. GLiClass revision
`4f6a108b08a5537f395521d19b5073e197923dd3` retains Apache-2.0 attribution;
DeBERTa revision `bddf8c5411c34ac3565e16e04384fd68b2618dda` retains MIT attribution.
The complete locks include artifact SHA-256 values and model-card sources.

Qwen used the previously pinned native Apple Silicon Metal runtime and GGUF.
CPU specialists versus Metal Qwen is an operating-point comparison. The optional
maximum-12-case Qwen CPU probe was unattempted because its historical pinned
runtime image was absent locally; this run establishes no Qwen CPU result.
Native RSS sampling does not measure total GPU/unified memory. Initial
environment records conservatively marked cache verification false; subsequent
independent raw-log audits verified zero initial cached tokens and full-prompt
evaluation for all 309 requests across all three stages, without rewriting
those frozen records.

The first readiness runs and embedding/GLiClass development runs did not measure
background contention; their empty arrays are not proof of an idle host. Later
launches preserve process/container snapshots. Lifecycle review added explicit
serialization and guarded cleanup evidence; all actual model runs were serial
and their shutdown was verified. These limitations do not turn the large
quality shortfall into evidence for adoption.

`task specialist:test` passed all **76 Python tests** and the pinned Go driver
race checks, with no skipped Python tests. Five additional independent mock
fault tests passed for lifecycle cleanup and lock refusal. Those tests validate
the harness; the measurements above come from real inference. This stratified,
family-separated authored pilot is not production traffic, a calibration claim,
or a universal model ranking. No AMD64 or CUDA execution is inferred.

Use these results when deciding whether these particular CPU specialists earn
an integration prototype on this operation contract. Prefer continued code and
Qwen baseline work here; a different workload or trained classifier needs its
own reviewed comparison.

The [evidence index](evidence/20261007-specialist-intent/README.md) contains the
frozen run, raw responses, resource records, reviews, setup failures, logs and
reproduction hashes. The [harness](../eval/specialist-intent/README.md) documents
the bounded execution commands.
