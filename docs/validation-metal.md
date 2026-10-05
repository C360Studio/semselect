# Native Metal validation — 2026-10-05

The M3 Pro Metal path serves real Choice, Score and Noul. The matched 4B chat
baseline was faster and more accurate on this small routing set. The results
support native GPU packaging, but do not establish a routing advantage for Kev.

## Method

- Apple M3 Pro, 36 GiB unified physical memory, native macOS/arm64. The host was
  shared, without controlled thermal state or container resource quotas.
- llama.cpp `6c59c40076c00eab49754dc955d7652d93f9e125`, built with Metal and embedded
  shader source, CMake 4.1.2 and Apple clang 21.0.0. Both runs used the identical
  runtime binary/libraries. Verified source/tool archives and all runtime files
  are hashed in the accompanying resource records. Source archives have no Git
  metadata; the binary's build commit is `unknown`, not the semselect commit.
- Kev-4B and Qwen3.5-4B, both Q4_K_M, with exact artifact hashes in
  `models.lock.json` and `models.baseline.lock.json`. The baseline is Unsloth's
  fixed GGUF conversion of Qwen/Qwen3.5-4B; its publisher does not pin the original
  conversion input revision. Distributed GGUF bytes are pinned and verified.
- Both runtime logs confirm `MTL0 (Apple M3 Pro)` and **33/33 layers offloaded**.
  Four CPU threads, 4096 context tokens, 512 batch/microbatch, one slot, no
  context shifting. Runs were sequential, with only one model server loaded.
- Unchanged 24-case dataset, both candidate orders, **48 correlated observations**.
  One warmup was excluded. No retries or prompt tuning. Kev received the native
  SystemOne request; the baseline used the existing structured chat evaluator,
  temperature zero and thinking disabled. Both received the same caller policy
  and descriptions. The chat protocol has no native candidate probabilities.
- This baseline exercises the seminstruct-style chat protocol using our pinned
  runtime; it is not the existing seminstruct release image. Templates, output
  generation, per-tensor quantization and cache behavior remain confounders.

## Observations

| Measure | Kev-4B Metal | Qwen3.5-4B Metal |
| --- | ---: | ---: |
| Valid responses | 48/48 | 48/48 |
| Correct labels | **43/48 (89.6%)** | **46/48 (95.8%)** |
| Median latency | **449 ms** | **288 ms** |
| p95 latency | 474 ms | 423 ms |
| Unknown selections | 11/48 | 14/48 |
| Coverage after rejecting unknown | 77.1% | 70.8% |
| Errors among accepted non-unknown labels | 5/37 (13.5%) | 2/34 (5.9%) |
| Candidate-order label flips | 2/24 pairs | 2/24 pairs |
| Largest child lifetime peak RSS | 3.39 GiB | 3.57 GiB |
| Child CPU seconds, including startup and warmup | 2.72 s | 9.62 s |
| Whole-run wall time, including startup and warmup | 24.93 s | 17.01 s |

Whole-run resource totals are **not equivalent workloads**: Kev also ran the
three-primitives smoke. Peak RSS is the largest child process peak, not summed
memory or GPU allocation. CPU seconds exclude GPU execution time. Neither metric
can be directly compared with the CPU record's container cgroup counters.
Use measured request latency for the timing comparison.

Kev's median latency was about **22× lower than the earlier CPU container run**
(9877 → 449 ms). This is an observed deployment difference, not an isolated
GPU speedup: native macOS versus the Linux VM, build flags, quotas and background
load also differ. All 48 Kev labels matched that earlier run.

Kev retained the same five mistakes: reversed billing/account ambiguity, both
technical/account orders, and both instruction-only injection orders. Its example
0.7 p(max) threshold accepted 29/48 requests and still admitted two wrong labels.
Qwen's two mistakes were the reversed multi-intent cases; it correctly returned
unknown for the instruction-only injection. This is too little evidence to claim
general injection robustness, calibration or production accuracy for either model.

For ordinary routing, Qwen3.5-4B is now the stronger measured reference. Keep
semselect's separate value proposition centered on native candidate distributions
and typed Score/Noul responses. GPU build variants alone do not establish a need
for separate product repositories; share runtime conventions where practical.

## Checks and reproducibility

```sh
task model:fetch
task metal:baseline:fetch
task metal:build
task down
task metal:evaluate
task metal:baseline:evaluate
task check
```

Both GGUF checksums, the clean native build, full-layer GPU offload, Kev's real
primitive smoke, and both complete routing evaluations passed. Go race/contract
checks and Python evaluator/acquisition/lifecycle checks validate packaging rather
than inference quality. One process-group timeout test is explicitly skipped in
this agent environment: `killpg` returned `EPERM` even on an owned group after a
reviewed escalation. Direct-child service cleanup is tested; build-descendant
cancellation is not proved here. The launcher reports group-signal denial rather
than claiming all build descendants stopped.

A separate real native service run passed the guard's configured-address health
check, returned HTTP 400 for 4209 input tokens against the 4096-token context, and
closed both listener ports after SIGTERM to the launcher (exit 130). See the
[lifecycle evidence](evidence/metal-lifecycle.json). The final `task check` completed
with both Go packages passing, 24 Python tests passing and the one explicit skip,
plus formatting, vet and Compose validation.

The initial launcher checks stopped two runs before inference because the default
runtime log level hid allocation details and this revision names its Metal device
`MTL0`. The final check uses trace-level allocation logs, requires a positive full
offload count on an Apple MTL device, and has a passing regression against the
observed device-name format. Those startup failures were not scored as model calls.

Preserved evidence:

- [Kev measured requests](evidence/metal-kev-4b.json),
  [resources/provenance](evidence/metal-kev-4b-resources.json),
  [native primitive response](evidence/metal-primitives.json),
  [runtime log](evidence/metal-kev-4b-runtime.log).
- [Qwen measured requests](evidence/metal-qwen35-4b.json),
  [resources/provenance](evidence/metal-qwen35-4b-resources.json),
  [runtime log](evidence/metal-qwen35-4b-runtime.log).

CUDA, WSL2, native Windows, Linux/AMD64 and other Apple chips remain unvalidated.
No GPU throughput, sustained load, power or thermal benchmark has been performed.
