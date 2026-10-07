# Smaller Qwen query classification — 2026-10-07

**Neither Qwen3.5-2B nor Qwen3-1.7B meets our query-classification quality and
250 ms median / 750 ms p95 latency targets in the tested CPU configuration.**
Both completed all 120 primary queries. Qwen3-1.7B was the stronger small model,
but it still made 27 wrong selections and took about 5.3 seconds per query.
These pretrained models do not replace the 4B quality reference on this contract.
This result does not establish that custom training is necessary.

## Primary results

All candidates use the same nine operations, two development prompt variants,
literal argument binder and 120 authored evaluation queries. This is the
previously used specialist cohort, not fresh confirmation or production traffic.
Only the model alias changes in the frozen Qwen wire payloads.

| Configuration | Correct operations / 120 | Wrong accepted | Deferred | HTTP median / p95 |
| --- | ---: | ---: | ---: | --- |
| Qwen3.5-2B Q4_K_M, CPU | 84 (70.0%) | 36 | 0 | 5,928 / 6,127 ms |
| Qwen3-1.7B Q4_K_M, CPU | 93 (77.5%) | 27 | 0 | 5,267 / 5,480 ms |
| Historical improved rules | 95 (79.2%) | 25 | 0 | Different execution boundary |
| Historical Qwen3.5-4B Q4_K_M, Metal | 111 (92.5%) | 8 | 1 | Different hardware |

Both small models returned valid responses for all 120 primary requests, with
zero primary errors or unattempted cases. Correct raw and accepted counts are
equal because these configurations deferred nothing. The historical rows are
frozen quality references from the [specialist evaluation](validation-specialist-intent.md);
they were not rerun. Its 4B Metal timing must not be used for a CPU speed ratio.

| Quality gate | Required | 2B | 1.7B |
| --- | --- | ---: | ---: |
| Raw correct operations | At least 108 | 84 | 93 |
| Correct accepted operations | At least 96 | 84 | 93 |
| Wrong accepted operations | At most 3 | 36 | 27 |
| Wrong specialized routes on `no_override` | 0 | 35 | 24 |
| Gold-argument mismatches after shared binding | 0 | 5 | 4 |
| Retain historical 4B quality | At least 108 correct; at most 8 wrong | Fail | Fail |

The main quality problem is knowing when to leave ordinary search alone.
The 2B model recognized 79 of 80 specialized-operation cases, but only five of
40 `no_override` cases. The 1.7B model recognized 77 of 80 specialized cases and
16 of 40 `no_override` cases. Recognizing explicit counts, paths or aggregations
does not establish safe routing on negated, quoted, unsupported or ambiguous
requests. The binding metric counts gold-argument mismatches after the shared
coded binder, often downstream of a wrong route; it does not independently prove
a binder implementation bug. Neither model generated argument strings.
Correct executable-plan counts were 60 for
2B and 58 for 1.7B, distinct from operation-classification accuracy.

## Serving configuration and limits

Both models ran serially in CPU-only Linux ARM64 Docker, with four CPU threads,
a four-CPU quota, affinity `0-3`, a 4 GiB hard memory limit and one inference
slot. Requests used thinking disabled, temperature and seed zero, schema-bound
operation JSON, a 64-token output cap and a 4,096-token context. This evaluation
does not establish AMD64, CUDA or native Metal performance for these models.

Prompt caches were disabled. Full-input preflight used each loaded model's own
chat template and tokenizer: 329–398 input tokens for 2B and 327–396 for 1.7B,
plus the reserved output allowance. The comparison measures complete uncached
requests, not a warmed shared-prefix optimization. Smaller parameter count did
not make this serving path meet the latency target. GPU execution or caching
could change latency; neither was measured here, and neither fixes the observed
classification errors by itself.

Both models completed 135 development requests before selection. Development
included three excluded warmups, twelve feasibility calls and both prompt
variants on 60 queries. Neither variant qualified under the development error
rule for either model. Variant zero was therefore frozen as a **diagnostic**
choice for each model at 16:56:21.976 UTC, before the first primary stage at
16:56:28.129 UTC. No prompts, labels, thresholds or model artifacts changed after
results appeared. Each primary stage added three excluded warmups and 120
measured calls.

## Resources, sensitivity and the bound

| Operating check | Target | 2B | 1.7B |
| --- | --- | --- | --- |
| HTTP median / p95 | ≤250 / ≤750 ms | 5,928 / 6,127 ms: fail | 5,267 / 5,480 ms: fail |
| Startup-inclusive cgroup peak | ≤2 GiB | 1.544 GiB: pass | 1.623 GiB: pass |
| Maximum process readiness | ≤60 s | 2.85 s: pass | 0.90 s: pass |
| Reversed-label decisions | ≤1 change / 24; no new wrong specialized route | 7 changes; 1 new wrong: fail | 6 changes; 2 new wrong: fail |
| Remapped-ID decisions | Same gate on 24 cases | Incomplete | 13 changes; 1 new wrong: fail |

Memory is the Docker cgroup lifetime peak, including model loading and warmup,
not total host/VM memory. Readiness starts a fresh process but does not flush OS
file caches. Contention snapshots are preserved; they are not proof of an idle
host. Both models remained below the memory limit, with no OOM or observed input
truncation.

The 2B model reached its cumulative 1,800-second inference cap during sensitivity.
It completed all 24 reverse cases and four remap cases; the next remap request
hit the remaining absolute deadline, and 19 cases were unattempted. All 48
planned sensitivity cases remain in the denominator. Across development,
primary, sensitivity and excluded warmups, it recorded 289 valid responses, one
deadline error and 19 unattempted calls out of 309 planned. The elapsed ledger
was 1,800.044874 seconds: the final timeout's outer cancellation accounting added
about 45 ms, without an extra completed request or budget extension.

The 1.7B model completed all 309 planned calls in 1,676.80 accounted seconds,
including both full sensitivity views. The 200-request concurrency-two load
checks were **skipped for both models** because the prerequisite quality and
latency gates failed. This is not a throughput or scale result.

The frozen report labels 2B's overall protocol status “inconclusive” because its
remap evidence is incomplete. Its **primary quality and latency failures are
conclusive**, as is its failed complete reverse-order check. Do not interpret
the report's 22 remap outcome changes as 22 changed model decisions: that number
includes the error and unattempted outcomes. The 1.7B protocol verdict is
“does not meet targets.” Neither diagnostic configuration qualifies.

All six runtime lifecycles recorded verified container shutdown and CLI reaping.
A final OS/container inventory found no owned controllers or model
containers still running.

## Provenance and reproduction

The experiment uses llama.cpp revision
`6c59c40076c00eab49754dc955d7652d93f9e125`. Its CPU image is
`sha256:4e61c2714fd97b14eb645a4b9b284f0eba8fefcfb332d077f975d91c78670bc4`,
with runtime binary SHA-256
`fb85943749fd82842334ff104753cfe9f6dddd7dcf266d110956f218961b29fd`.
The archive build reports “commit unknown”; the pinned source archive, build
record and image label supply revision evidence, not binary self-report.

The Apache-2.0 GGUF distributions are pinned as follows:

| Model | Publisher revision | GGUF SHA-256 |
| --- | --- | --- |
| Unsloth Qwen3.5-2B Q4_K_M | `f6d5376be1edb4d416d56da11e5397a961aca8ae` | `aaf42c8b7c3cab2bf3d69c355048d4a0ee9973d48f16c731c0520ee914699223` |
| Unsloth Qwen3-1.7B Q4_K_M | `d7f544eead698dbd1f15126ef60b45a1e1933222` | `b139949c5bd74937ad8ed8c8cf3d9ffb1e99c866c823204dc42c0d91fa181897` |

Publisher and upstream model cards and license attribution are preserved. The
publisher does not pin the conversion's upstream source revision; the locks
distinguish inspected upstream provenance from the exact distributed GGUF bytes.
One setup attempt, without corrective retry, completed in 106.51 seconds.

Independent review verified all 912 alias-only payloads and 63 frozen sources
before inference. The approved freeze SHA-256 is
`06ea8a033d987b3cbfb79097c57b26df6f6f04ba9b34b2b0b4804b3f7060b027`.
The 22 new offline tests and 11 reused scoring tests passed independent review.
The initial review found incomplete-response auditing and final-cleanup reporting
gaps; failing regressions and fixes were preserved before the approved freeze.

After all inference closed and independent review approved the original report,
a separate regression corrected a latent promotion guard: a development-ineligible
diagnostic must not advance even if later gates pass. The original run already
failed quality and latency for both candidates, so this correction changes no
observed decision. All 23 current harness tests pass. The evidence preserves the
original graded sources, original report, narrow post-run diff and separate review.
Current sources intentionally reject the old freeze; use its source snapshot for
replay. An offline replay from that snapshot reproduced every report field except
the generation timestamp, without rerunning inference.

The [harness](../eval/qwen-size/README.md) documents execution. The
[evidence index](evidence/20261007-qwen-size/README.md) contains raw responses,
token manifests, resources, lifecycle proofs, setup logs, reviews and hashes.
