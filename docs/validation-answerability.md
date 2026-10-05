# Answerability teaching run: validation record

Run `20261005T172542.446287Z`, native Metal, 2026-10-05 17:25:42–17:26:20 UTC.
**Passed execution and cleanup; teaching evidence only.** Start with the
[short result and worked examples](../eval/answerability/README.md).
The full [comparison record](evidence/20261005T172542.446287Z-answerability-metal/comparison.json)
is authoritative for requests, raw bodies, predictions, timing and summaries.

## Frozen task and fair scope

Twelve public development cases: five allow, seven defer. Exact facts and audience
metadata let code settle four; eight remain unresolved. One case reproduces a
pinned SemSource excerpt; the other cases are clearly constructed or mutated.
The [source record](../eval/answerability/sources/provenance.json) preserves the
upstream commit and full-file/excerpt hashes. These facts are teaching sidecars,
not a claim about current SemSource output contracts.

Dataset SHA-256:
`7660ef6a314af9f3c09748189b104060c0f728736462924aff00cf6ceca5734e`.
Independent pre-run review checked labels, support quotes, source provenance and
runner behavior. A08's question was clarified from sending a claim to Finance
receiving it before the fixture was frozen and before any model call.

Every arm filters current passages for the supplied audience. A required exact
fact allows only one distinct eligible value; missing/conflicting values defer.
No eligible evidence defers. Models cannot override these code decisions. The
control allows remaining cases without an additional semantic check. This is
not a fusion retrieval rerun or a measurement of downstream generated answers.

Model requests contain only filtered input plus the common task rule and category
descriptions. Case titles, families, gold labels/reasons/support and provenance
are excluded. Qwen returns schema-constrained `route` JSON with thinking disabled,
temperature zero, seed zero and a 128-token output bound. Kev uses native Choice
with `allow`/`defer`, retaining its distribution and native selected argmax. No
threshold tuning or confidence calibration was performed.

Two sequential model arms, Qwen then Kev. Each gets one excluded A05 warmup,
then two trials of every case in normal/reversed order. Reversal changes passage,
fact and candidate order together; it is a composite perturbation. Trial 2 also
reverses case traversal. These variants are correlated. The predefined primary
result is trial 1/normal, not the most favorable observed view.

## Counts and timing

Decision time includes common filtering and request preparation plus HTTP and
response validation. Disk writes, model startup and warmups are excluded. Prefix
caching is enabled in both configurations: Qwen explicitly sets `cache_prompt=true`; pinned native
SystemOne defaults to true, without an override through the guard. Observed behavior
differs: Qwen reused prefixes in 31/32 measured replies; Kev reported zero cached
tokens on all 33 warmup/measured prompts and reprocessed each full prompt. Different
model tokenization, prompts, serving paths and actual cache reuse prevent a cold or
matched-decoding interpretation. The shared laptop had no thermal controls.

| Primary result | Correct / total | Model calls | Code decisions | Median, all cases | p95, all cases | Median, unresolved | p95, unresolved |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| No added gate | 8/12 | 0 | 4 | 0.003 ms | 0.023 ms | 0.002 ms | 0.005 ms |
| Qwen JSON | 12/12 | 8 | 4 | 532 ms | 620 ms | 553 ms | 620 ms |
| Kev | 12/12 | 8 | 4 | 494 ms | 642 ms | 530 ms | 642 ms |

The control permits unsupported A06/A07/A09/A12. Both models defer all four and
allow all supported cases. There are no unnecessary deferrals, invalid responses,
transport errors or unattempted cases. The primary code decisions plus model
responses are valid on 12/12 for each arm. Operational failure would defer for
action, but would not count as a correct semantic prediction.

| All correlated views | Correct / observations | Actual model calls, excluding warmup | Evidence/candidate-order flips | Median / p95 on unresolved cases |
| --- | ---: | ---: | ---: | ---: |
| No added gate | 32/48 | 0 | 0/24 | 0.003 / 0.006 ms |
| Qwen JSON | 48/48 | 32 | 0/24 | 555 / 620 ms |
| Kev | 48/48 | 32 | 0/24 | 499 / 619 ms |

Of each model arm's 24 order pairs, 16 actually called the model and eight were
code-only. The four views produce identical labels; repeats do not increase the
number of independent examples. Neither model's success on one injected passage
establishes injection robustness. No evidence here establishes an advantage over
ordinary Qwen, calibrated probabilities, fewer generated hallucinations or saved
end-to-end cost.

## Machine, pins and resources

Apple M3 Pro, 36 GiB unified memory, macOS 26.5.2 arm64. Native llama.cpp
`6c59c40076c00eab49754dc955d7652d93f9e125`, four generation/batch CPU threads,
4096-token context, batch/microbatch 512, one slot, context shifting disabled.
Both logs verify 33/33 layers offloaded to GPU. There are no container resource
limits in this run. Runtime and guard hashes were verified against the native
build record; model size/SHA-256 verified before launch. No models were downloaded.

Exact model revisions, filenames, quantization and SHA-256 are frozen in
[Kev's lock](evidence/20261005T172542.446287Z-answerability-metal/source/models.lock.json)
and [Qwen's lock](evidence/20261005T172542.446287Z-answerability-metal/source/models.baseline.lock.json).
Qwen's original GGUF conversion commit remains unknown. Qwen calls llama.cpp
chat directly; Kev calls the semselect guard and native SystemOne. This compares
practical model/service paths, not an isolated model-head change or a running
seminstruct release.

| Arm | Startup | Excluded warmup | Wall time, including lifecycle | Child CPU |
| --- | ---: | ---: | ---: | ---: |
| Qwen JSON | 1.045 s | 866 ms | 19.350 s | 8.715 s |
| Kev | 1.303 s | 510 ms | 18.950 s | 1.700 s |

CPU time is child-process accounting, not GPU time or energy. Recorded peak RSS
is the process-wide cumulative largest completed child lifetime peak:
3,801,497,600 bytes in both arm records. It is not separate per-model peak memory,
a sum of processes or a measurement of Metal allocation. Do not rank memory
usage from this value.

## Reproduction and checks

Run `task answerability:validate`, then `task answerability:metal` with the existing
pinned native build and model cache. The runner serializes access through
`.native/operation.lock` and refuses occupied experiment ports 18087/18088.
Each new run gets a unique directory. It performs no fetch or build.

This run used code commit `2182196` plus local changes. The evidence directory
contains a tracked working-tree diff and exact source snapshots/hashes for the
runner and imported helpers, plus both lockfiles and the complete dataset. The
new runner was untracked at launch, so its source snapshot, not that diff, records
its implementation. All 13 original run files were copied byte-for-byte into
[the preserved evidence directory](evidence/20261005T172542.446287Z-answerability-metal/).

All owned runtime/guard processes exited with code zero. Both loopback ports were
independently checked closed after each arm. There were no cleanup errors. Raw
requests, normal/error response fields, warmups, timestamps and native/guard logs
remain alongside the summary; no failed or unfavorable run was discarded.

`task check` passed Go race tests, formatting, vet and Compose validation. Python
ran 52 tests: 51 passed, one existing lifecycle test was skipped because this
environment rejects owned process-group signals with `EPERM`. The skip concerns
build-timeout descendant cleanup; it is not a passing check. All 17 new
answerability tests passed. Independent review also checked the fixture and
runner before launch.

The next quality step is a separately frozen test set from real source families,
followed by full-pipeline benefit measurement. CPU/Docker and CUDA answerability
results have not been measured.
