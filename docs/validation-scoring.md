# Direct scoring baseline — 2026-10-05

On the M3 Pro, ordinary Qwen3.5-4B produced the same routing decisions with
one-token option scoring and JSON Schema output. Scoring reduced median inference
HTTP latency by about 28% in two complete runs. This is evidence for a useful
output-format tradeoff on this smoke workload, not proof that a specialized
decision model is better. On CPU/Docker the median reduction was only 3.4%, and
scoring abstained on one additional case in each trial. CPU results therefore do
not establish the same quality-preserving tradeoff. SGLang compatibility is
recorded separately below.

## Matched Metal experiment

The [final comparison](evidence/20261005T153218.957088Z-metal/comparison.json)
contains prompts, label tokens, requests, responses and results. Its
[provenance](evidence/20261005T153218.957088Z-metal/provenance.json) records runtime
and model hashes, build details, launch arguments and captured evaluator source.

- Apple M3 Pro, 36 GiB unified RAM, native macOS 26.5.2 arm64; 33/33 layers offloaded.
- llama.cpp `6c59c40076c00eab49754dc955d7652d93f9e125`; the same binary and
  Qwen3.5-4B Q4_K_M bytes for both formats. The GGUF revision and checksum are
  pinned; the publisher does not identify its original conversion-source revision.
- Four generation/prompt threads, 4096-token context, batch/microbatch 512,
  one slot, thinking disabled, temperature zero and seed zero.
- Unchanged 24 manually labeled routing cases, normal and reversed category
  orders, repeated twice: **96 measured calls per format**, plus one excluded
  warmup per format. These are correlated views of 24 examples, not 96 independent
  examples. The format execution order alternates.
- `cache_prompt=false` for both formats; every measured response reported
  `cache_n=0`. This is a warmed-runtime test without prompt-prefix reuse, not a
  cold model-start measurement. Thermal state and other laptop activity were not
  controlled.

The evaluation-only scorer uses llama.cpp `/completion`, constrains output to one
of `A`–`D`, and requests the first token's raw vocabulary log probabilities with
`post_sampling_probs=false`. It checks each label at the actual answer position:
appending the letter must preserve the entire prompt-token prefix and add exactly
one distinct token. It requires all four candidates in the returned top-256 list;
missing candidates fail validation rather than receive invented scores. It
normalizes those complete candidate scores and checks that their maximum agrees
with the generated label. The scorer still emits **one token**; this is not a
claim of zero-token generation or SGLang `/v1/score` equivalence.

The JSON path uses `/v1/chat/completions` with a strict schema containing only a
`route` enum, and emitted **seven tokens** on every measured request. Category
descriptions and task instructions are shared, but the output-format instructions
and prompt serialization differ. Their effect cannot be separated from decoding
cost by this experiment. Inference HTTP latency excludes scoring-prompt preparation,
template rendering and label-token checks; those scoring preparation costs are
separately recorded (3.2–9.1 ms in the final run). JSON template rendering happens
inside its timed chat request. This asymmetry is small here but matters when
interpreting the measurements as end-to-end application latency.

Runtime counters help explain the latency difference. Median prompt length was
237 tokens for JSON and 238 for scoring. Median prompt processing took 500.1 ms
and 480.2 ms respectively; JSON then recorded 165.4 ms for the remaining decoding
work. The first output token comes from prompt processing, so the scoring path's
near-zero recorded decode time does not mean the decision required no compute.
These stage medians are descriptive counters, not an independent attribution of
the prompt-format and output-length effects.

The supplied [article](https://iamdgarcia.medium.com/build-your-own-jev-100-local-56799bcf2909)
compares scoring with generation
that requests a label plus explanation, with a 32-token allowance. Our JSON-only
baseline avoids making explanation generation a prerequisite for routing. The
article's isolated label-token check is also weaker than checking labels at the
actual answer position. We test the underlying idea without treating its timing
claims as transferable to this model, runtime or machine.

| Final Metal run | JSON Schema | One-token scores |
| --- | ---: | ---: |
| Valid responses | 96/96 | 96/96 |
| Correct | 92/96 (95.8%) | 92/96 (95.8%) |
| Normal/reversed order flips | 4/48 pairs | 4/48 pairs |
| Inference HTTP p50 | 673.4 ms | 487.7 ms |
| Inference HTTP p95 | 749.1 ms | 528.7 ms |
| Output tokens per call | 7 | 1 |

The saved rows confirm identical predictions across formats, both trials and the
[earlier complete run](evidence/20261005T152813.162235Z-metal/comparison.json).
Both formats misroute the two multi-intent examples to `account` when category
order is reversed; both correctly select `unknown` in the original order. Each
error repeats in the second trial. Across all four trials in the two runs, JSON
p50 spans 660.0–688.6 ms and scoring p50 spans 476.2–493.3 ms, recomputing arithmetic
medians from the saved rows for both runs. The earlier artifact's stored per-trial
`p50` used a nearest-rank convention and remains unchanged. No accuracy gain was
observed.

## What the scores add—and what remains unproved

Scores permit an additional caller abstention rule beyond the explicit `unknown`
option. The final run yields these **descriptive, uncalibrated** tradeoffs:

| Accept only non-unknown selections with… | Accepted / all calls | Errors among accepted |
| --- | ---: | ---: |
| No score threshold (also available to JSON) | 68/96 (70.8%) | 4/68 (5.9%) |
| Maximum candidate probability ≥ 0.5 | 66/96 (68.8%) | 4/66 (6.1%) |
| Maximum candidate probability ≥ 0.7 | 56/96 (58.3%) | 2/56 (3.6%) |
| Maximum candidate probability ≥ 0.9 | 48/96 (50.0%) | 0/48 |

Zero observed errors at 0.9 does not establish safety or make 0.9 a recommended
threshold. This tiny reused set has no independent calibration or held-out split.
The multiclass Brier score is 0.1066, using the mean sum of squared errors across
all four classes; there is no corresponding JSON probability output to compare.
Neither that number nor normalized token probabilities establishes calibrated
correctness. Product thresholds need development data followed by held-out tests
at an agreed error budget.

This supports investigating direct scoring when a caller has a bounded semantic
question and cares about the cost of output generation or explicit abstention.
It does not establish Jev-like training, uncertainty quality, joint multi-question
efficiency, production throughput or an advantage over coded rules. The production
semselect service still uses its native SystemOne API; this scorer is isolated
evaluation code. See [when to use semselect](when-to-use.md).

## Lifecycle and repeatability

The final run passed explicit shutdown checks: runtime exit code zero, process
stopped, and no listener on port 18086. The recorded largest-child peak RSS is
3.76 GiB; this process-level high-water mark is not aggregate GPU/unified-memory
usage and cannot allocate memory cost to either format separately.

The earlier run completed all inference calls, but its overall provenance says
`failed` because a bind-based cleanup check mistook TCP `TIME_WAIT` for a listener.
That historical record remains unchanged. The
[follow-up record](evidence/20261005T152813.162235Z-metal/cleanup-followup.json)
documents connection refusal, a failing regression before the check was fixed,
and the fresh complete run validating the corrected cleanup.

## Matched CPU/Docker experiment

The [complete CPU comparison](evidence/20261005T153432.573761Z-cpu-docker/comparison.json)
and [provenance](evidence/20261005T153432.573761Z-cpu-docker/provenance.json) preserve
192 measured calls, both excluded warmups, runtime logs and source snapshots.
This ran on the same M3 Pro inside Docker Desktop Linux/ARM64, with four CPU quota
and runtime threads, an 8 GiB memory limit and no GPU layers. The immutable local
image ID was `sha256:a24e5693f8e9634ded5a8b29a438ad87035a8e23a5ac182485d5eaee13c85db1`;
the image label and runtime file hashes are saved. Its llama.cpp source revision,
GGUF bytes, context/batch settings and every prepared request match the final
Metal experiment. This is not an AMD64 or bare-metal Linux result.

| CPU/Docker | JSON Schema | One-token scores |
| --- | ---: | ---: |
| Valid responses | 96/96 | 96/96 |
| Correct | 92/96 (95.8%) | 90/96 (93.8%) |
| Normal/reversed order flips | 4/48 pairs | 6/48 pairs |
| Inference HTTP p50 | 11,191.0 ms | 10,815.8 ms |
| Inference HTTP p95 | 15,119.4 ms | 13,993.7 ms |
| Output tokens per call | 7 | 1 |

Predictions repeated exactly within each CPU format. JSON matched Metal's labels.
Scoring added an `unknown` selection on the normal-order `injection-account` case
in both trials: the user asks to change a profile email address, then appends an
instruction to pretend the question concerns invoices. CPU scoring assigned
`unknown` 0.4765 and `account` 0.4184; Metal assigned `account` 0.4438 and `unknown`
0.4224. Neither chose the injected `billing` answer. The extra abstention counts
as an accuracy miss and lowers coverage, rather than adding a wrong accepted
route. Identical weights and requests did not imply identical backend scores.

Without a probability threshold, rejecting `unknown` accepted 68/96 JSON calls
with four wrong routes, versus 66/96 scored calls with the same four wrong routes.
Scoring at 0.7 accepted 56/96 with two errors; at 0.9 it accepted 48/96 with zero
observed errors. These are descriptive thresholds on the same tiny reused set,
not held-out policies. The scored Brier value was 0.1081.

The raw median latency difference was 375.2 ms (3.4%). Trial medians were
11,306.8/11,137.6 ms for JSON and 10,895.5/10,791.3 ms for scoring. Both formats
spent a median of about 10.8 seconds processing the prompt; JSON recorded another
362.8 ms of decoding. All measured responses reported `cache_n=0`. Scoring
preparation took 6.6–13.2 ms separately. Shared-host variability was substantial;
these measurements do not establish a production CPU advantage, especially with
the changed abstention behavior. Most CPU time here precedes output generation.

The run finished in 2,244.4 seconds including setup, warmups and cleanup. Container
cgroup lifetime peak memory was 3.54 GiB and cumulative CPU usage was 8,668.4 CPU
seconds, including all work in that container. These are not per-format costs or
native process RSS. The container exited zero without OOM, was removed, and port
18086 refused connections; cleanup errors were empty. The
[environment follow-up](evidence/20261005T153432.573761Z-cpu-docker/environment-followup.json)
records host and Docker engine details after completion, not during-run resource
samples. CPU/Docker and native Metal still differ in OS/VM, quota, build and backend;
cross-hardware timing is a deployment observation, not an isolated GPU speedup.

## SGLang/MLX compatibility result

The [probe record](evidence/20261005T151940Z-sglang-metal/README.md) preserves an
isolated source-pinned installation, dependency freeze, model checksums and all
three attempts. SGLang `efb62ce269b499123e2d1c89005ee4cea8c31098`, Python 3.12.13,
PyTorch 2.13.0 and MLX 0.32.3 installed successfully. The separate MLX four-bit
Qwen3.5-4B artifact is not equivalent to the GGUF quantization above and lacks a
published original checkpoint/conversion revision.

The default cache strategy rejected Metal. Selecting `no_buffer` with overlap
scheduling disabled passed that check; an optional text-only flag chosen for the
probe then failed architecture validation and was removed. The third attempt
loaded the model but crashed during built-in warmup with
`AttributeError: 'MlxAuxiliaryStateComponent' object has no attribute 'mamba_checkpoint_grid'`.
It never became healthy; no endpoint result is attributed to those attempts.
Process and listener absence were verified after shutdown.

A [fourth, separately preserved probe](evidence/20261005T154252Z-sglang-metal-cache-disabled/README.md)
added only `--disable-radix-cache`, with the same pinned environment and weights.
It passed its endpoint checks on one refund fixture, repeated twice:

- JSON Schema chat returned valid `billing` objects.
- Independent `/v1/score` returned complete, repeatable candidate distributions;
  labels were checked at their actual token position.
- `/v1/decisions` returned complete choice, yes/no and ordinal-score distributions.
- Replaying the decisions route prompt and label IDs through `/v1/score` matched
  its distribution exactly in both repeats.

The independent scoring prompt returned billing 0.9968; the decisions prompt
returned 0.9914. Different prompts still matter. The yes/no question's full-vocabulary
candidate mass was only 0.2009, despite a conditional yes probability of 0.8176;
normalized answer scores must not be read as calibrated correctness.

Eight inference HTTP calls covered one fixture, not the routing dataset. Each
three-question decisions bundle reported 160 prompt tokens (58 + 43 + 59), with
zero completion tokens. One HTTP request did not eliminate per-question prompt
work. The JSON fixture and quantization differ from the llama.cpp comparison;
these diagnostic request timings cannot establish a SGLang speedup. No source
patches, custom kernels or replacement weights were used to obtain the pass.
The cached MLX path, SGLang CPU/CUDA, and its `/v1/systemone` alias remain untested
by this successful probe.

Shutdown was bounded but not graceful. The server exited -9 after group SIGTERM;
logs and pinned source support SGLang killing its own process tree before the
driver's timeout fallback. The driver did not explicitly instrument that timeout
branch, so retain that limit on causal attribution. Independent host checks found
no matching process or listener on port 30101. The evidence includes the complete
shutdown analysis rather than treating endpoint success as production readiness.

The local baseline is now complete: matched llama.cpp experiments on CPU/Docker
and Metal, plus a working SGLang/MLX endpoint configuration and its earlier failures.
SGLang still needs the matched quality/performance experiment before a workload
recommendation; none of these runs establishes CUDA support or benefit.

## Reproduction and implementation checks

After the documented model/runtime setup, run `task compare:metal` and
`task compare:cpu` sequentially. Each preserves a fresh complete run under
`results/scoring-comparison/`; copy the entire directory into versioned evidence
before recording a published result. See [evaluation instructions](../eval/README.md)
and the SGLang probe's README for its separate pinned environment and driver.

`task check` passed Go race tests, formatting, vet and Compose validation. Python
ran 35 tests: 34 passed and one existing process-group timeout test was skipped
because this environment prohibits its signal operation (`EPERM`). The new
scoring/evidence/lifecycle tests all passed, including regressions first observed
failing for lost warmup evidence and the `TIME_WAIT` cleanup false alarm. These
tests verify the harness; the saved real requests establish the observed model
behavior. The skip is not a pass for descendant process-group termination.
