# Research and initial backend decision

Checked 2026-10-05. Initial deployment target: CPU-only Linux container. This document separates source review from the [local validation record](validation.md); numbers attributed to upstream are not local measurements.

Follow-up: [native Metal validation](validation-metal.md) on the M3 Pro now compares
both 4B models at Q4_K_M on the same runtime. Qwen3.5-4B returned 46/48 correct
labels at 288 ms median versus Kev's 43/48 at 449 ms. This strengthens the case
for retaining seminstruct as the routing reference and positioning semselect
around typed model readouts. The earlier CPU/0.6B comparison below remains a
bootstrap record, not a fair model-size comparison.

## Project stance and next investigation — 2026-10-05

The mission is to teach a skeptical developer when a Jev-like decision model is
worth using, with inspectable evidence. A tested service, reproducible comparison
matrix and [plain-language selection guide](when-to-use.md) are all deliverables.
The project can provide value by showing where code or schema-constrained chat
is sufficient, even if decision-model packaging eventually joins seminstruct.

The project owner explicitly reports a strong skeptical prior toward Jev's hype.
The concern is that a convenient semantic selection API may encourage developers
to replace coded control structures with uncertain model judgments. This records
a starting bias and an architectural concern, not a finding that Jev lacks value.
The owner supports further evidence-driven investigation, including SGLang.
Preserve favorable and unfavorable results and define acceptance criteria before
evaluating, so the experiment can challenge that prior as well as vendor claims.

Prefer coded rules where authoritative facts and exact predicates are available.
Use models to interpret ambiguous unstructured evidence when a semantic judgment
is actually needed. The caller owns eligibility, permissions, valid transitions,
abstention, fallback and execution. Selecting the highest-scoring supplied option
does not itself provide a fallthrough case, prove the option applies, or authorize
an action. Type-safe output does not change that boundary.

The [completed same-Qwen experiment](validation-scoring.md) compares schema JSON
with one-token option scoring on CPU/Docker and Metal. Metal preserved all labels
while lowering median inference HTTP latency by about 28%; CPU lowered it by only
3.4% and added an abstention in each trial. Neither result demonstrates a Jev
training advantage or a need to replace an adequate coded pipeline.

SGLang/MLX also passed a one-fixture endpoint probe after disabling radix caching;
its earlier startup failures and bounded, non-graceful shutdown remain documented.
A matched SGLang workload comparison is still future work. SGLang supplies serving
mechanisms, not Jev's training. See the [bounded investigation](sglang-investigation.md).
This supersedes the initial shortlist's decision to defer SGLang research while
leaving the production llama.cpp deployment and historical results unchanged.

## Decision

Package **llama.cpp's native `/v1/systemone` endpoint with Kev-4B Q4_K_M**. Keep semselect's own service code limited to operational bounds, health and request forwarding. The upstream runtime already implements Choice, Score and Noul, so semselect does not need its own probability extraction, tokenizer logic or decision head.

Native support merged on October 2 in [llama.cpp PR #29818](https://github.com/ggml-org/llama.cpp/pull/29818). It is very new even though the underlying runtime is established. A version predating that change cannot serve this endpoint. Pin the actual runtime commit and model artifact; do not use a floating latest image. The PR includes decision-model tests and a reference comparison, but these do not establish correctness for semselect workloads. Kev's Python `date_facts` preprocessing was intentionally omitted from the native implementation; do not assume complete parity with the Python server.

**Local outcome:** the implemented CPU bootstrap produced 43/48 correct routing labels versus 24/48 for the available seminstruct 0.6B baseline, but median latency was 9.88 seconds versus 0.293 seconds. This supports the model as an initial distribution-capable service, with a material latency limit; it is not a subsecond CPU routing recommendation. The tiny smoke set and different model sizes do not isolate architectural effects. Two injection-only errors survived a 0.7 probability threshold. See the validation record for complete results and abstention tradeoffs.

The [official Kev-4B GGUF card](https://huggingface.co/ggml-org/Kev-4B-GGUF) identifies the original Kev adapter and Qwen3.5-4B-Base, explicitly targets this endpoint, and publishes a 3.03 GB Q4_K_M artifact. That is download size, not peak RAM. [Kev-0.8B Q8_0](https://huggingface.co/ggml-org/Kev-0.8B-GGUF) is an 812 MB alternative for constrained machines, but is not an interchangeable quality baseline. Both cards declare Apache-2.0; [llama.cpp code is MIT](https://github.com/ggml-org/llama.cpp/blob/master/LICENSE).

**Selection rationale (engineering judgment):** this keeps the initial dependency surface close to the ecosystem's existing llama.cpp packaging, uses an actual trained decision model, and leaves smaller models available without inventing another inference protocol. Prefer 4B initially because the [Kev project's held-out evaluation](https://github.com/jaredpalmer/kev) reports better new-source test accuracy for 4B than 0.8B (0.838 versus 0.697). These are upstream model-suite results, not measurements of these GGUF quantizations or semselect data. The model family is versioned as Kev 1.0, ships fitted temperatures and reproducible evaluation suites, and remains a young project.

The [llama.cpp announcement](https://huggingface.co/blog/ggml-org/decision-models-in-llamacpp) reports GPU timings on an RTX PRO 6000. Its Kev-4B 12 ms figure is **not a Linux CPU latency claim**. CPU latency, throughput, peak RSS, cold load, quantization drift and useful operating limits must be measured with the pinned build. Apple Silicon and CUDA are separate validation targets.

## Existing seminstruct baseline and service economics

The inspected [seminstruct Dockerfile](https://github.com/c360studio/seminstruct/blob/7f9135a99cd27a6c63a2a60db5daeee9f5622be4/Dockerfile#L44) packages llama.cpp `b8994` with Qwen3-0.6B Q4_K_M. It already supplies the runtime, health checks and deployment conventions needed for a small local classifier. The [comparison configuration](../docker-compose.baseline.yml) pins the available Linux ARM64 image to `sha256:297507bee8396438fe284f9ed285165c47a565b3f65be177030c473a4b90bdb9`, uses its existing baked model and fixes four CPU threads, one slot and 4,096 context tokens. That architecture-specific artifact does not establish an AMD64 baseline.

The [shared evaluation](../eval/README.md) asks seminstruct to generate a constrained enum label with reasoning disabled. It compares label quality and latency, while explicitly recording that this baseline supplies no native candidate distribution. A label-only baseline remains useful: if its quality and latency meet a caller's needs, a separately resident 4B service may not justify its extra memory and operating cost. Current results must decide that tradeoff; model size and API shape alone cannot establish a speed or accuracy advantage.

`b8994` predates the native decision endpoint. A modest seminstruct runtime upgrade plus a decision-model variant could absorb this capability, subject to regression checks for its current chat models. semselect is an independent packaging and operational boundary for a new model/API, not an inference invention. Keeping it separate initially isolates the newer runtime, artifact pins and request bounds; consolidation is reasonable if maintaining two services costs more than that separation saves. No sibling runtime upgrade is made here.

## Shortlist

| Candidate | Local mechanism and API | CPU/dependency fit | License and maturity | Disposition |
| --- | --- | --- | --- | --- |
| **llama.cpp + Kev** | Trained decision head; native Choice/Score/Noul over runtime-defined options, without generated answer text | Native GGUF runtime; 4B Q4 download 3.03 GB, 0.8B Q8 812 MB; total RAM unmeasured here | MIT runtime, Apache-2.0 weights; new native endpoint, upstream tests and model evaluations | Initial backend; validate pinned 4B |
| **Laya** | ModernBERT-large plus decision head; option-marker scores, native Choice/Score/Noul | Python/PyTorch CPU supported; also now a native [Laya GGUF](https://huggingface.co/ggml-org/Laya-GGUF) option | Apache-2.0 code/weights; Python 0.3.27 declares beta, tests and Docker support | Smaller alternative; domain-quality concerns below |
| **SemIf-OpenJev** | Direct conditional option-token logits from ordinary open causal models; typed option distributions | Current upstream supports llama.cpp/GGUF CPU, PyTorch CPU, CUDA and Apple backends | MIT engine; model licenses separate; committed fixtures, runners and failures, small project | Useful reference/baseline; redundant inference layer for current scope |
| **SGLang decisions / openjev-sglang** | Selected label-token probabilities; native decisions and System One interfaces | Reviewed openjev deployment is Qwen3.6-35B-A3B on B200; that does not establish laptop or 4B results | SGLang is an existing serving project; openjev calls itself an early experiment and recommends native SGLang | Next bounded evaluation candidate; see [investigation plan](sglang-investigation.md). No production backend change |
| **NotJev** | One-token logprobs from an OpenAI-compatible backend; normalize option mass; System One adapter | Node/Bun adapter plus a separate model runtime; backend may be local or hosted | Apache-2.0 adapter; test and benchmark directories; backend/model licenses separate | Adds a layer already supplied by native llama.cpp |
| **GLiClass** | Encoder classifier for dynamic label sets; softmax single-label or sigmoid multilabel scores | Python/PyTorch CPU path; small model is about 0.1B parameters | Apache-2.0 code and reviewed small-model weights; tests, paper and pretrained families | Relevant classifier baseline, not native ordered Score/Noul semantics |

Table sources: [Laya source](https://github.com/NandhaKishorM/laya), [package metadata](https://github.com/NandhaKishorM/laya/blob/main/pyproject.toml), [SemIf](https://github.com/TheoLeeCJ/SemIf-OpenJev), [SGLang decisions](https://docs.sglang.io/docs/supported-models/decision_models), [openjev-sglang](https://github.com/ekzhang/openjev-sglang), [NotJev](https://github.com/9pings/notjev), [GLiClass](https://github.com/Knowledgator/GLiClass), [GLiClass small card](https://huggingface.co/knowledgator/gliclass-small-v1.0), [GLiClass score implementation](https://github.com/Knowledgator/GLiClass/blob/main/gliclass/pipeline.py).

## Limits that affect the choice

Laya has particularly useful published counterevidence. Its [benchmark record](https://github.com/NandhaKishorM/laya/blob/main/BENCHMARKS.md) reports near-chance typed-decisions accuracy for the base checkpoints; the 0.766 result belongs to a checkpoint fine-tuned on that benchmark's training split. It also identifies ordinal Score as weak and warns about overconfidence and crowded option sets. Its own CPU study on four AMD EPYC cores reports one-question p50 of 580 ms for English and 193 ms for multilingual; batching ten questions took 6.244 s and 1.842 s respectively. These are specific PyTorch measurements, not native-GGUF results. The [CPU Docker guide](https://github.com/NandhaKishorM/laya/blob/main/docs/docker.md) advises allowing 8 GB RAM and 10 GB disk. These facts make Laya a plausible smaller deployment experiment, not a demonstrated replacement for the selected quality baseline.

SemIf publishes direct-logit and generation comparisons using the same model and evidence. It also discloses decision disagreement and shared-prefix numerical changes. Its native BF16 accuracy tables should not be relabeled as quantized CPU results. [Meanblock/JEV-CPU](https://huggingface.co/Meanblock/JEV-CPU) is an older thin CPU loader/UI adaptation of SemIf using Qwen3-0.6B, not newly trained Jev weights. Its model card estimates about 2.4 GB float32 weights and about 3 GB RAM; neither is a semselect measurement. Current upstream SemIf already has a CPU path, so selecting the derivative brings little benefit.

The similarly named [OpenJev 27B GGUF](https://huggingface.co/ggml-org/OpenJev-GGUF) is a distinct artifact. Its declared **CC BY-NC 4.0** license excludes it from this project's default commercial-use candidate set. Do not confuse its terms with the MIT SemIf engine or with openjev-sglang. Source availability alone does not establish permission to redistribute a particular model. Preserve the selected runtime and model notices and record source repository, revision, filename and SHA-256 for every artifact; training datasets retain their own terms.

## What the returned numbers mean

Choice selects the maximum-probability supplied option. Score is the probability-weighted mean of zero-based ordered level indices, not automatically a 0–1 score. Noul returns P(yes). The native route supplies structured numerical readouts rather than asking a language model to write a confidence number. The adapter must preserve those semantics and must not manufacture confidence.

A normalized distribution is not proof that reported confidence matches observed correctness. Kev's fitted temperature is checkpoint-specific evidence, not calibration on semselect traffic. Quantization, option wording and order, label count, missing information and domain shift can change results. Entropy-based confidence and a chosen option's probability also differ; thresholds cannot be transferred between providers merely because a field has the same name. SGLang's [probability documentation](https://docs.sglang.io/docs/supported-models/decision_models) explicitly makes this distinction for token-score adapters.

Before using automatic actions, evaluate representative labeled examples separately for routing Choice, binary Noul and ordinal Score; include negation, uncertain evidence, option-order permutations and long-input boundaries. Report errors and calibration separately from latency. A successful three-type smoke test proves that the integration runs, not that decisions are reliable.

## Starting reference

The supplied [Daniel García article](https://iamdgarcia.medium.com/build-your-own-jev-100-local-56799bcf2909), published September 21, was initially accessible only as a member-only introduction. The owner supplied the article text on 2026-10-05, making its example code and comparison description available for review. The copyrighted article is not copied into this repository.

The article distinguishes the inference mechanism from Jev's training and
calibration, explains restricted option probabilities, and requires an escape
option when the candidates are not exhaustive. Its example uses SGLang's
`/v1/score` with single-token labels. Our investigation tests this same idea.

Its described demo generation comparison asks for a label and a short explanation
with a 32-token limit. That does not establish a speedup over our existing
label-only JSON baseline. We keep that stronger baseline. The prose also calls
for checking tokenization at the answer position; the shown example tokenizes
labels in isolation. Our probe checks each label appended to the rendered prompt
and rejects a changed prefix or a multi-token continuation.

The llama.cpp experiment emits one constrained token and reads its pre-sampling
log-probabilities; it is not SGLang's zero-output-token scoring endpoint. Every
candidate must be present before normalizing, and both the raw candidate mass
and conditional distribution are retained. Preserve that distinction in results.
Upstream references and the local evidence remain the basis for compatibility
and performance claims.

### Additional RAG article supplied by the owner

On 2026-10-05 the owner supplied another Medium excerpt, beginning "Where to place
Jev in RAG"; its author, URL and result images were not included. It proposes
post-retrieval relevance/answerability judgments and inferred metadata filters
before retrieval. The latter is explicitly an untested proposal in the excerpt.
Its hosted Jev timings, prices, question limits and score thresholds are not local
semselect measurements or transferable deployment settings.

The useful workload distinction is **topical relevance versus sufficient evidence**.
A passage may discuss expense reimbursement while omitting the requested deadline.
The author acknowledges that one reranking comparison already had Recall@5 of
1.0 without reranking, so that result does not establish a need for a reranker.
We should apply the same skepticism to our existing algorithmic retrieval paths.

Inferred filters can exclude the correct document before retrieval has a chance
to find it. Per-chunk answerability also differs from answerability across several
chunks: an answer can require evidence spread across documents. Both need explicit
failure cases and a broad-search or abstention fallback. A single HTTP request
with many questions does not establish constant inference cost. Our current guard
allows four questions, not the excerpt's 20/40-question examples, and our native
Score/Noul smoke checks do not validate a RAG workload.

The next comparison must include existing rules and retrieval algorithms, not
only model alternatives. Keep corpus, retrieved candidates and task definitions
fixed when evaluating an added gate, and include a no-gate baseline. Compare
single-response JSON with bundled decisions fairly. Preserve exact metadata and
authorization predicates in code; measure any benefit of semantic filtering,
reranking or answerability separately.
The [source and scorecard audit](code-baselines-and-rag.md) records the existing
semsource/SemStreams capabilities, historical no-gap finding and fair test design.
