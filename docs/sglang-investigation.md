# Bounded SGLang investigation

Status: local compatibility checkpoint complete, 2026-10-05. Matched llama.cpp
CPU/Docker and Metal [format experiments](validation-scoring.md) are preserved.
SGLang/MLX failed warmup with its initial cache settings, then passed schema JSON,
scores and decision-bundle checks after adding `--disable-radix-cache`.
The [initial failures](evidence/20261005T151940Z-sglang-metal/README.md) and
[successful one-fixture probe](evidence/20261005T154252Z-sglang-metal-cache-disabled/README.md)
remain separate. Shutdown was bounded but not graceful. This is endpoint
compatibility, not a SGLang quality, calibration or performance result. The matched
SGLang workload comparison below remains future work.

The [real-source answerability pilot](../eval/answerability/heldout/README.md) shows
a limited aggregate quality advantage for Kev, with omissions still missed by both
models. The immediate priority is downstream benefit using the
[existing answer path](answering-path.md). Return to this serving comparison when a
workload result makes runtime cost or scoring mechanics the next useful question.

## Question and architecture boundary

Can direct option scoring improve useful semantic decisions or operating cost
over schema-constrained Qwen, enough to justify additional integration work?
The educational deliverable is a worked example for the
[when-to-use guide](when-to-use.md), whether direct scoring wins, loses or leaves
the question unresolved. It should let a skeptical reader inspect the evidence
and reproduce the comparison.
Use the [existing-algorithm audit](code-baselines-and-rag.md) to choose the workload
and its strongest applicable code/retrieval baseline. A same-model format test is
useful, but does not establish a reason to add inference to the existing pipeline.
Jev, Kev and ordinary Qwen served by SGLang are different models; API compatibility
does not establish equivalent training, calibration or accuracy.

The project owner's skeptical prior is recorded in the
[research decision](research-and-decision.md). Treat it as a reason to demand
evidence, not as an expected experimental outcome. Known exact routes, permissions,
validation and state transitions stay in code. Model judgments address remaining
semantic ambiguity; caller policy governs abstention, fallback and execution.

## What SGLang supplies

SGLang documents `/v1/decisions` for reading single-token option scores from chat
models, plus a compatible `/v1/systemone` shape. It batches questions but performs
a prefill per question. Scores are normalized over candidate labels; `label_mass`
also reports their full-vocabulary mass. These are not calibrated correctness
probabilities. Prompt versions, label-token checks and cache state matter.
See [SGLang decision documentation](https://docs.sglang.io/docs/supported-models/decision_models).

This supplies a direct-scoring mechanism and a distribution API. Whether it gives
better abstention, lower latency or greater throughput is the experiment. Retain
raw label mass for diagnostics, without treating it as a validated confidence
threshold; token spelling and template effects can also influence it.

## Comparison design

Use one pinned Qwen3.5-4B checkpoint, tokenizer, numerical precision and SGLang
build on one machine. Its [official model card](https://huggingface.co/Qwen/Qwen3.5-4B)
documents SGLang serving, but that does not prove our decision endpoint/profile
works. Verify label placement and endpoint compatibility before collecting results.

| Path | Purpose |
| --- | --- |
| Existing code and retrieval paths | Preserve exact predicates, keyword routing, statistical retrieval and full fusion where applicable; distinguish learned embeddings from deterministic stages |
| Qwen with JSON Schema | Practical existing approach, including a fair one-response/multiple-fields baseline |
| Same Qwen with `/v1/decisions` | Test direct distributions and decision-request batching |
| Same prompt with one-token constrained output/logprobs or `/v1/score` replay | Diagnostic control for label mapping and direct-score agreement |

Changing from JSON prompts to the server's decision prompt also changes wording
and token positions. Save rendered prompt/label token IDs where supported; use the
diagnostic control to distinguish that effect from scoring mechanics. Require
explicitly disabled thinking for the chat baseline as well as decisions.

The old Metal Q4_K_M results remain a deployment reference. Comparing them with
NVIDIA/BF16 would not isolate the benefit of SGLang or direct scoring. Do not swap
to a much larger Qwen model and attribute any gain solely to the serving engine.
Kev can be a later third-model comparison once the same-Qwen question is answered.

## Small, staged workload

1. Reuse the unchanged 24-case routing smoke and both candidate orders. Confirm
   valid outputs, label agreement, errors and latency before broadening the task.
2. Freeze a modest labeled set of operational examples before inference. Separate
   code-resolvable conditions from semantic ones. For each text, ask one question
   and then a bundle of four: route, explicit incident evidence, requested action
   and an ordinal urgency judgment. Include missing evidence, conflicting intents,
   negation, instruction injection and explicit unknown cases. Score both per
   question and per complete bundle; a transport success is not a correct decision.
3. Use short and longer inputs within a fixed context budget. Measure fresh and
   warmed caches separately, repeat runs, and alternate path order. Keep one
   caller first; only add a modest fixed concurrency trial if correctness passes.
   For bundles, let the JSON baseline return all fields in one response.
4. Select any abstention/calibration settings on a separate development split,
   then freeze them for held-out evaluation. Keep original/reversed examples and
   examples from the same source in the same split. Use the unchanged smoke for
   regression, not as a newly independent calibration test set.

Report valid and correct counts, order flips, latency p50/p95, completed decisions
per second, peak memory with its measurement definition, and total model calls
avoided by rules. For scored outputs, report Brier score and accepted-case error
versus coverage. Compare paths at the same caller error budget where data supports
that comparison; the chat path can abstain with an explicit unknown label. Do not
invent missing chat probabilities. Any generated confidence fields belong in a
separate optional baseline, never as native model-score observations.

## Hardware and bounded effort

The owner's chosen sequence is to establish verified CPU/Docker and native Metal
experiments before requesting CUDA assistance. CPU/Docker uses the existing
llama.cpp ARM64 runtime; the SGLang MLX path gets its own compatibility record.
NVIDIA targets below are later work, not prerequisites for the baseline.

The owner reports access to a Spark and RTX 3090s. Interpret Spark as NVIDIA DGX
Spark pending confirmation; host OS, driver versions and remote access are not yet
established. Hardware availability is distinct from a tested deployment.

| Target | Initial role and constraints |
| --- | --- |
| Existing M3 Pro | First bounded compatibility probe using SGLang's documented MLX/Metal backend. If both scoring and schema output work for the pinned Qwen model, compare them here. Retain the measured llama.cpp baseline. |
| One RTX 3090 | Later NVIDIA comparison after CPU/Metal evidence is recorded. Its 24 GB VRAM makes a 4B BF16 model a plausible fit (roughly 8 GB for weights alone); measure total runtime/cache memory. Prefer Linux; validate the environment separately if using WSL2. No multi-GPU work initially. |
| DGX Spark | Second deployment target if the first comparison is useful. NVIDIA publishes an SGLang playbook, but the selected build must also contain the decisions endpoint and support Qwen3.5-4B. Repeat the same-model comparison before trying larger models. |
| CPU-only SGLang | Upstream documents an Intel AMX/Xeon server path. This is not evidence of a supported Apple ARM CPU deployment. Keep llama.cpp as our already-tested CPU option. |

Sources: [RTX 3090 specifications](https://www.nvidia.com/en-us/geforce/graphics-cards/30-series/rtx-3090-3090ti/),
[NVIDIA SGLang on Spark](https://build.nvidia.com/spark/sglang),
[Spark launch instructions](https://build.nvidia.com/spark/sglang/instructions),
[SGLang Apple Silicon](https://docs.sglang.io/docs/hardware-platforms/apple_metal),
[SGLang CPU servers](https://docs.sglang.io/docs/hardware-platforms/cpu_server),
[SGLang installation](https://docs.sglang.io/docs/get-started/install).

Follow-up source review on 2026-10-05 corrects the earlier NVIDIA-first plan:
SGLang does not require CUDA. Its Apple serving path uses MLX/Metal, with
`SGLANG_USE_MLX=1` and `--disable-cuda-graph`. The source-install guide specifies
macOS 14+, stable PyTorch 2.13.x and MLX 0.32+. Full Xcode is required only for the
optional native Metal kernels. It documents HF/MLX weight loading; our existing
GGUF files do not establish a ready-to-use MLX artifact.

Scoring needs its own compatibility check. An
[upstream MLX report](https://github.com/sgl-project/sglang/issues/41211) reproduces
empty logprobs and a failed `/v1/score` call without `--mlx-enable-sampling`, then
reports success with that flag on Qwen2.5-0.5B. This does not verify Qwen3.5-4B or
`/v1/decisions` on our laptop. A chat-only smoke is insufficient.

The Mac probe should establish startup, constrained JSON, complete candidate
scores, prompt/label correctness and repeatability before running the comparison.
Use an isolated environment and a pinned source revision. A smaller documented
model may diagnose setup, but must not replace the 4B quality baseline silently.
Keep this to the initial half-day compatibility budget; record any failure and
complete the llama.cpp CPU/Metal baseline before considering a NVIDIA follow-up.

Separately, the pinned llama.cpp server documents token probabilities through
`n_probs` and `post_sampling_probs`. A one-token label/logprob experiment can
test the same scoring idea on our existing CPU or Metal runtime, provided every
candidate score is present and the sampling/normalization semantics are verified.
That experiment would validate the mechanism in llama.cpp, not SGLang compatibility.
See the [pinned server reference](https://github.com/ggml-org/llama.cpp/blob/6c59c40076c00eab49754dc955d7652d93f9e125/tools/server/README.md).

Record host architecture, GPU, driver, image digest, model revision, precision and
launch arguments for each run. The NVIDIA installation docs describe CUDA 13;
that is not a requirement for CPU or Metal. Pin a compatible build containing the
decisions endpoint, which may require a dated nightly. Avoid assuming an x86 image
runs on Spark's ARM host, or that a Spark-specific configuration works on a 3090.
The pinned Apple-Silicon configuration above now has one-fixture endpoint evidence;
the cached path and a matched workload comparison remain unverified.

Planning estimate: one to two engineer-days for the first comparison once access
is working, with a compatibility checkpoint in the first half-day. Allow another
half to one day for a Spark repeat if its documented image works for this model and
endpoint. Dataset labeling effort is additional and depends on domain complexity.
If setup requires backend porting or custom kernels, record the blocker and
reconsider scope instead of silently expanding the project.

## Decision after the experiment

Agree the caller's acceptable error rate, latency target and a worthwhile gain
before interpreting the expanded evaluation. These product requirements are not
yet specified; do not invent a pass threshold or tune it after seeing results.

Expand production integration only if direct scoring produces a repeatable benefit:
more accepted correct decisions at the same error budget, or lower latency/resource cost at
comparable decision quality. Include integration and maintenance cost in that
judgment. Faster responses with more wrong accepted actions are not an improvement.

If schema-constrained Qwen remains adequate and direct scores do not improve
abstention or efficiency, retain the simpler deployment. Publishing a negative
result and explaining that choice fulfills the educational mission. Packaging may
eventually join seminstruct while the comparison evidence and guide retain their
value. Preserve every run under the [results recording procedure](results.md#recording-the-next-result).
