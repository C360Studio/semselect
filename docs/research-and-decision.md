# Research scope and backend decision

Initial decision checked 2026-10-05; initial routing/evidence phase closed out
2026-10-06; community/graph scope added 2026-10-08; throughput scope added
2026-10-09. Initial deployment target:
CPU-only Linux container. This document separates source review from the
[local validation record](validation.md); numbers attributed to upstream are not
local measurements.

## Closeout 2026-10-09: serving question closed on this hardware

**The serving and throughput question is closed for this laptop. The one open
question is graph-refinement quality, which waits on the SemEngine port.**
semselect stays a reference service with its evidence and plain-language
guide; no production integration is proposed. The merged evidence snapshot is
tagged `research-2026-10-09`.

| Question | Status after five experiments | What could still change it |
| --- | --- | --- |
| Is the bounded decision interface useful? | Yes, measured and stable; both models deliver it. | Settled. |
| Does the trained decision head decide better than Qwen JSON at 4B? | No measured advantage on four workloads: one small win, one loss, two ties. | A workload with ambiguous, varied evidence and no training data. Graph refinement is the only candidate left. |
| Is it faster per request? | No; Qwen was equal or faster every time. | Nothing on this hardware. |
| Does it win at throughput? | No; prompt processing is the cost and does not batch here, and llama.cpp's decision path is worse at it than ordinary Qwen. [Record](validation-throughput.md). | Datacenter GPUs, which do not change the quality question. |
| Is "one state, many cheap questions" real? | Yes, measured: 2.35× from head grouping on llama.cpp; follow-up questions at about a fifth of a new-state read on Kev's own server. | It is a caching property that ordinary models share; Qwen's prefix cache gave the largest gain measured. |

Next steps live in the quality-pilot prerequisites issue: the real
candidates-per-cycle number, a SemEngine commit with clustering and semantic
edges, frozen labeled fixtures, and the serving profile fixed in the
[protocol](../eval/community-refinement/README.md). Do not build an
evaluation-only graph engine here to get ahead of the port.

## Throughput and serving scope 2026-10-09

**The README read as a speed verdict it had not earned.** The owner asked
(tracking issue [#3](https://github.com/C360Studio/semselect/issues/3)) for the
hype around Jev-like decision models to be demystified for the team and the
wider community, with a plain-language account of why we did what we did. The
gap was in our own framing. Every latency we measured is one request at a time,
on one slot (`-np 1`), and Kev requests pass through a guard that admits one
inference and returns 429 to a second. That is the right yardstick for an
interactive router and the wrong one for background graph work, where the
question is decisions per second. Throughput, batching and multi-slot serving
were then unmeasured for both models on every runtime. The
[README](../README.md#how-to-read-the-latency-numbers) and the
[selection guide](when-to-use.md#why-per-request-latency-is-the-wrong-yardstick-for-background-work)
now say so, and the README explains
[what the decision-model pitch claims](../README.md#what-the-decision-model-pitch-claims-in-plain-language).

What is measured, probed, designed and only reported by others:

- **Measured here:** serial latency and quality on the ticket, query and
  source-evidence workloads, as recorded in the closeout below. Qwen reused
  prompt prefixes in 47/48 ticket rows while Kev reprocessed them; Kev's query
  heads ran with zero cached starts in one slot. `-np 3 --kv-unified` is named
  in the closeout as an untested optimization; the outcome below resolves it.
- **Probed here, 2026-10-05:** SGLang's MLX backend served Qwen3.5-4B
  (mlx-community 4-bit) for JSON chat, `/v1/score` and `/v1/decisions` on this
  M3 Pro after `--disable-radix-cache`. One fixture, one running request, no
  throughput. The [compatibility record](sglang-investigation.md) keeps it
  apart from any performance claim. The venv and model are still on disk.
- **Designed and run 2026-10-09:** the
  [throughput experiment](../eval/throughput/README.md).
  It compares three serving paths on this laptop, reusing the 24-case
  source-evidence pilot (independent decisions) and the 32-case query-routing
  task (three questions over one shared state), at 1, 4 and 8 slots, with Kev
  head grouping and unified KV cells. SGLang MLX cells cover Qwen JSON,
  `/v1/decisions` and `/v1/score`; Kev's own MLX server includes a cached-state
  cell. All three paths ran on 2026-10-09 (outcomes below).
- **Author-reported, not ours:** the figures in the README's
  [pitch table](../README.md#what-the-decision-model-pitch-claims-in-plain-language),
  and the SGLang and openjev-sglang statements below.

**Outcome on llama.cpp Metal, 2026-10-09 (measured).** The [throughput
record](validation-throughput.md) ran 19 cells across both models on this M3
Pro, rerunning two Kev cells. Prompt processing stayed at about 440 to 570
tokens per second at 1, 4 and 8 slots, so with fresh evidence in each request
every path landed near one decision per second, and extra slots mostly added
queueing. Kev gained nothing from slots: a decision model loads in embedding
mode (every Kev log shows it), and on the pinned build that evaluates one slot's
prompt per compute step (from the pinned source). Its one gain was head
grouping: with four slots and one client the three query heads shared one
prefix, 2.35× faster than one slot with identical labels, and still slower than
Qwen JSON (1.01 against 1.30 to 1.39 questions per second). Qwen JSON gained
from slots only with a warm prefix cache. Neither pre-declared reading came out
yes: no arm batched usefully, and Kev showed no shared-state advantage. The
lever on this hardware is fewer processed tokens per decision, not concurrency.
This says nothing about CUDA hardware, Jev or decision quality.

**Outcome on SGLang MLX and Kev's own MLX server, 2026-10-09 (measured).** The
same workloads ran on two more serving paths, each with a different artifact
([record](validation-throughput.md#three-runtimes-at-one-request)). SGLang MLX
(Qwen, MLX 4-bit) served one running request only at its pinned revision: four
running requests crashed the scheduler, the radix cache still crashed and the
three-question decisions cell ran out of Metal memory, so no SGLang batching
reading could be evaluated. At one request its no-decode endpoints ran at 0.98
and 0.99 decisions per second against 0.78 for JSON. Kev's own server (bf16)
gained nothing from more clients, because on MLX it runs one request at a time,
but it is the only runtime here that keeps a state between requests: once a
state was cached, each further question cost about 365 ms, against 1,970 ms for
one question on a new state, with labels unchanged, and its pre-declared
cached-state reading came out yes. At one request the three runtimes landed
between 0.78 and 1.18 decisions per second on the source-evidence pilot. The
lever is unchanged, fewer processed tokens per decision, and on Kev's own server
that now includes a cached state. This says nothing about CUDA hardware, Jev or
decision quality.

**SGLang does not document Kev support.** SGLang's
[decision-model documentation](https://docs.sglang.io/docs/supported-models/decision_models),
reviewed 2026-10-09, describes two routes. `/v1/decisions` scores ordinary chat
models by answer-token probabilities, with one prefill per question and the
questions scored together in one batch. `/v1/systemone` accepts the System One
request shape for those models and for trained decision checkpoints, which
answer only through it; the page names PPLX-Decider-v1-27B, PPLX-Decider-v1.1-27B
and Clef, and mentions a `decision_config.json` for the decision checkpoints.
Kev is not named, and its [model card](https://huggingface.co/jaredpalmer/kev-4b)
file list (adapter, `head.pt`, tokenizer and training records) has no such file.
On the documentation, SGLang can serve Qwen direct scoring but not Kev. This is
a reading of the documentation; we have not tried to load Kev in SGLang. The
[openjev-sglang](https://github.com/ekzhang/openjev-sglang) experiment scored
options on an ordinary model (prefill plus a first-token readout, Qwen3.6-35B-A3B
on a B200 through Modal). It is archived, and its notice recommends SGLang's
native decision endpoint; it does not mention Apple Silicon. SGLang's
[Apple Metal page](https://docs.sglang.io/docs/hardware-platforms/apple_metal)
documents overlap scheduling through MLX async evaluation, and does not
describe batching limits or prefix caching on MLX.

**Kev's own server is the MLX path for Kev.** The
[Kev repository](https://github.com/jaredpalmer/kev) ships `python -m kev.serve`,
which exposes `/v1/systemone` and selects MLX automatically on Apple Silicon
(bf16). The artifact is a LoRA adapter plus a pointer head with fitted
temperature 2.41, under Apache-2.0. Its authors report Apple M5 and H100 numbers
(see the README table). We ran it on 2026-10-09 (outcome above); an M5 is still
a different machine from our M3 Pro.

**A "neither runtime batches usefully on Metal" result is a valid outcome.** The
experiment is isolated evaluation work. It changes neither the default runtime
profile nor the guard, adds no provider framework and no second scoring engine,
and compares throughput within a runtime first, because the artifacts differ
(GGUF Q4_K_M, MLX 4-bit, bf16). Such a result would be written up as a finding
about this hardware, in the same way as a favorable one.

## Community and graph scope 2026-10-08

The owner requested an evaluation of community refinement against **SemEngine**,
which will replace SemStreams when ready. The earlier routing, answerability and
synthesis work did not evaluate graph construction, membership quality or graph
maintenance. Its no-adoption conclusion applies to the measured workloads; it
does not close those broader questions.

The [community-refinement design](../eval/community-refinement/README.md) proposes
reviewing existing semantic virtual edges, then measuring actual communities and
evidence retrieval. Structural/identity grouping, stock and tuned mutual-kNN
edges, a small trained reviewer, Qwen and Kev are separate comparison arms.
Factual-edge review and community co-location remain distinct contracts. A useful
result must improve graph/retrieval outcomes within a background-processing budget,
not merely win another label-classification test.

The [source audit](../eval/community-refinement/README.md#source-audit-and-execution-readiness)
checked remote SemEngine main and the open ingest-kernel PR. Foundation and ingest
work exists, but clustering and semantic edges have not landed at those revisions.
The design uses SemEngine's pinned SemStreams algorithms as source context and
requires re-auditing the real port before execution. It adds no sibling changes,
new runtime, or production admission. New caller work is described in
[SemEngine integration](semengine-integration.md); the
[SemStreams integration audit](semstreams-integration.md) is historical.

This is a designed, unexecuted research track. Previous measurements remain
unchanged. Missing-edge discovery, membership moves, merge/split selection and
summary validation are visible follow-ups, not silently counted as tested or
made requirements of the first pilot.

## Query-classification follow-up 2026-10-07

**Retain Qwen3.5-4B JSON and improved code as the query-classification baselines.**
The [bounded specialist evaluation](validation-specialist-intent.md) found no
specialist adoption case. On its 120 primary operation cases, improved code got
95 correct and Qwen4B 111. The [smaller-Qwen follow-up](validation-qwen-size.md)
reused that cohort: 2B got 84 correct and 1.7B 93. Both missed the accepted
250/750 ms latency budget on four-thread ARM64 CPU and overrode ordinary search
too often. 4B's 867/1,044 ms Metal result also misses that budget.

The decision concerns our comparison and app-design starting point. Improved
rules remain evaluation-local; a combined caller policy still needs end-to-end
validation. Our native System One packaging remains pinned to Kev. A working
native API, the best tested semantic baseline and a qualified app router are
separate claims.

Custom training is not established as necessary, and no serious-scale requirement
has emerged beyond a small team. Keep code responsible for binding and policy;
use representative caller errors to justify any later model, training or serving
work. The [current decision table](when-to-use.md#current-query-classification-decision)
and [full comparison](results.md#2026-10-07--query-classification-decision) supersede
older selection proposals for this workload. Historical evidence below remains
useful without creating a new research backlog.

## Closeout review 2026-10-06

**Keep semselect as a working evaluation/reference service. No measured production
sem* use case currently justifies adopting it.** This historical closeout covers
the initial CPU/Metal workloads. The [selection guide](when-to-use.md) is the
current recommendation; earlier experiment proposals below are history or
conditional designs. The separately requested community track is described above.

The owner supplied Fable's outside critique. We checked its claims against the
pinned source, preserved runs and cited primary material. Its useful contribution
is separating three questions: **is a semantic judgment needed, does a typed
interface help, and does a specialized model earn its cost?** Our evidence supports
some semantic judgments and a Qwen output-format saving; it does not yet support
adding this specialized service to production. The critique itself is not evidence
that every untested alternative would fail.

| Point raised | What the closeout accepts or corrects |
| --- | --- |
| Kev's three query heads repeat shared context | **Confirmed for our one-slot profile.** The saved run has 195 heads with zero cached starts. Native grouping uses available slots and can copy a shared prefix to child slots. `-np 3 --kv-unified` is an untested optimization, not a guaranteed removal of the measured 7.11 s versus 2.38 s gap. [Grouping source](https://github.com/ggml-org/llama.cpp/blob/6c59c40076c00eab49754dc955d7652d93f9e125/tools/server/server-decision.cpp#L810-L839), [prefix copy](https://github.com/ggml-org/llama.cpp/blob/6c59c40076c00eab49754dc955d7652d93f9e125/tools/server/server-context.cpp#L3676-L3691). **Resolved 2026-10-09:** with `-np 4` and one client, grouping copied a shared prefix of about 1,070 tokens to two child heads and cut Kev's query median from 6,998 to 2,972 ms (0.43 to 1.01 questions/s), all 96 labels identical; `--kv-unified` added nothing measurable, and `-np 3` itself was not run. Kev stayed slower than Qwen JSON's 2,297 ms. [Throughput record](validation-throughput.md). |
| Ticket latency measures inherent model speed | **It does not.** Qwen reused prefixes in 47/48 measured rows; Kev reprocessed them. The observed hybrid path lacks the completion-only rollback checkpoints. This confounds 449 versus 288 ms without establishing how much of the gap it explains. Decision tasks do enter prefix-reuse logic; “decision models cannot cache” is too broad. [Reuse](https://github.com/ggml-org/llama.cpp/blob/6c59c40076c00eab49754dc955d7652d93f9e125/tools/server/server-context.cpp#L3440-L3448), [checkpoint restriction](https://github.com/ggml-org/llama.cpp/blob/6c59c40076c00eab49754dc955d7652d93f9e125/tools/server/server-context.cpp#L3708-L3721). **Resolved 2026-10-09:** at one slot the two models processed prompt tokens at similar rates (Kev 512 to 519, Qwen 543 to 567 tokens per second); Kev reused no prompt tokens across requests in any throughput cell, while W1 Qwen JSON reused 40% of its prompt tokens. A rate difference under a tenth cannot explain 449 versus 288 ms, so cache reuse is the likelier main cause (inference; the ticket run itself was not repeated). [Throughput record](validation-throughput.md). |
| Calibration was omitted or explicitly required a Q4 refit | **Shipped calibration was applied; workload calibration is unvalidated.** Conversion preserves the learned temperature and the runtime applies it (logged as 2.406050). The reviewed card recommends workload-specific validation/refitting, but does not substantiate the claimed BF16-fit/Q4-specific instruction. Positive scalar temperature cannot repair a single-variant Choice argmax error. [Conversion](https://github.com/ggml-org/llama.cpp/blob/6c59c40076c00eab49754dc955d7652d93f9e125/conversion/lev.py#L196-L201), [runtime](https://github.com/ggml-org/llama.cpp/blob/6c59c40076c00eab49754dc955d7652d93f9e125/tools/server/server-decision.cpp#L737-L758), [model guidance](https://huggingface.co/jaredpalmer/kev-4b#bias-risks-and-ethical-considerations). |
| Qwen was Kev's identical backbone control | **Incorrect.** Kev uses Qwen3.5-4B-Base plus its trained adapter/head; our baseline is the post-trained Qwen3.5-4B release. Same family and size do not isolate training, weights or readout effects. [Kev details](https://huggingface.co/jaredpalmer/kev-4b#model-details), [Qwen card](https://huggingface.co/Qwen/Qwen3.5-4B). |
| Argument selection makes the query task invalid | **Too strong.** The [frozen task](../eval/query-routing/protocol.json) uses finite operation/node/field choices and excludes open-vocabulary extraction. Kev's [intended uses](https://huggingface.co/jaredpalmer/kev-4b#intended-uses) include extraction choices. Independent heads can create inconsistent tuples; selecting a supplied node when `none` is correct remains an application error. This tests a compound caller task, not isolated architecture. |
| A small trained classifier is missing | **Agreed for stable-label workloads with training data.** Embeddings plus a trained linear head or a fine-tuned encoder is a relevant additional baseline. Neither our rules/BM25 nor zero-shot Julia substitutes for that comparison. Its local quality, training cost and serving cost remain unmeasured; no new training experiment is needed to close this phase. |
| The small quality leads settle adoption | **They do not.** Source-passage Kev fixed four Qwen errors and introduced one on 24 cases. That is a pilot observation, not a reliable population advantage. Julia's 50% on our ticket fixture is also not a replication of a different published banking task. No universal sample count substitutes for representative cases and an explicit error budget. |

### What the outside comparisons add

[LangWatch's measurement report](https://langwatch.ai/compare/jev-vs-all), reviewed
2026-10-06, reports SemIf/Qwen3.5-4B at 72.7% versus Jev at **81.4% on the same
nine tasks**; Jev's 81.9% headline covers eleven. Eikos-27B's 80.0% compares with
Jev's 79.9% on the same eight tasks. These averages mix different metrics and are
not a universal ranking. LangWatch discloses that its Instant Evals product uses
Jev, that the runs included uncommitted harness changes, and that the full open-model
harness/framings are not public. Treat the report as external evidence, not our
replication or a matched CPU/Metal cost comparison. It supports keeping the
question workload-specific; it does not establish our local best model.

The [MindStudio article](https://www.mindstudio.ai/blog/jev-vs-classic-classifiers-benchmark)
reviewed for this closeout does not identify or link the underlying independent
benchmark it describes. We therefore do not import its encoder accuracy, CPU
latency or calibration numbers as verified evidence. Its suggested trained-small-model
comparison remains a sensible hypothesis without those numbers.

We also do not adopt the critique's claims that Jev's advantage is caused by an
undisclosed large backbone, that only 27B-class alternatives merit testing, or that
runtime fixes cannot change the conclusion. Those causal and exclusion claims are
not established. A possible future typed contract should first serve an existing
caller and may use ordinary Qwen; it is not a reason to build a provider framework.
See the [conditional SemStreams proposal](semstreams-integration.md).

The critique's later “training tax versus labeled-data tax” distinction is useful
when stated narrowly: zero-shot use avoids task-specific training, not validation.
All deployment routes need representative labeled evaluation, including larger
open models and reused Qwen. Neither “27B requires a GPU” nor “CPU-only means train
and tune” follows from these experiments. System One compatibility permits common
transport; it does not make input limits, model quality or calibration interchangeable.
The [selection guide](when-to-use.md#choose-by-the-job) records those tradeoffs.

## Is Kev our best open-source choice?

**Kev is our locally validated starting choice; we have not established that it
is best of breed.** We selected it for a trained decision head, permissively
licensed artifacts, native llama.cpp integration and reproducible upstream work.
That was a packaging decision, not the result of a comparison among decision
models. Our Qwen comparisons cannot establish a conclusion about the whole
decision-model category.

Follow-up: the owner approved Julia as the single evaluation challenger. Its
[first CPU diagnostic](../eval/julia/README.md) completed at 159 ms median and
24/48 correct, with excessive deferral. That supports the CPU cost hypothesis
for short requests but does not establish sufficient routing quality. The source
screen below records why we selected that experiment; it is not its result.

For semselect, the useful selection question is: **which model meets the target
workload's error budget at the lowest acceptable CPU/Metal operating cost?**
Restricting every challenger to 4B parameters would miss smaller models that
might solve the hardware problem. A larger model's published quality lead would
not by itself establish acceptable cost. This source review considered native
integration, artifact terms, quality evidence, input limits and resource class:

| Candidate | What the source review establishes | Selection consequence |
| --- | --- | --- |
| **Kev-4B** | Working, pinned CPU/Metal service here; upstream publishes checkpoints, evaluations and limitations. Our routing results do not favor it over Qwen JSON. | Retain as the measured reference, without a best-of-breed claim. |
| **Julia-1, 144M** | Apache-2.0; [native SystemOne GGUF](https://huggingface.co/ggml-org/Julia-1-GGUF), 168 MB Q8 / 303 MB BF16 downloads. Its [author reports](https://huggingface.co/SupersonicLabs/Julia-1) 73.15% on 2,000 typed decisions, using H200 BF16 and 1,024-token inputs; CPU reproduction differs. The training pipeline is private and 8k task accuracy is unestablished. | **Completed single challenger diagnostic.** Short-input CPU cost was low; local routing quality did not justify adoption. |
| **Lev, 4B** | [Native SystemOne GGUF](https://huggingface.co/ggml-org/lev-GGUF). Its [author reports](https://huggingface.co/interfaze-ai/lev) summary-faithfulness accuracy of 27.1% versus its untuned backbone's 82.6%, and flags noncommercial terms in some training data despite Apache adapter metadata. | Closest same-size technical alternative, but weak evidence for our community-evidence task and unresolved provenance questions. Do not select it merely for API compatibility. |
| **Eval Engine Decision-4B** | [Author comparison](https://huggingface.co/evalengine/decision-4b): 79.1% versus Kev's 64.8%, but familiar training-source distributions and differing interfaces/precisions; the reviewed card/CSV does not identify the installed Kev revision. Its [GGUF recipe](https://huggingface.co/evalengine/decision-4b-gguf) uses one-token chat/logprobs. | Interesting quality claim, not a matched native-SystemOne replacement result. |
| **Laya** | Its [published limitations](https://github.com/NandhaKishorM/laya/blob/main/BENCHMARKS.md) distinguish weak base typed-decision results from a checkpoint trained on that benchmark's training split. | No stronger reason than Julia to spend our one small-model experiment here. |
| **Clef-Flash 9B / Clef 27B; larger Kev** | Cloudflare's [internal comparison](https://huggingface.co/Cloudflare/clef-flash) favors Clef variants on many tasks, but Flash trails Kev-9B on RAGTruth (35.6 versus 46.2 F1). [Flash's native GGUF](https://huggingface.co/ggml-org/Clef-Flash-GGUF) is 6.49 GB Q4. Kev also publishes [larger checkpoints](https://github.com/jaredpalmer/kev/blob/main/PLAN.md). | A broader resource budget needs a new selection decision. Neither vendor tables nor parameter counts settle our workload. |

Download sizes are not resident memory or latency. These are author/package
sources, not a common independent leaderboard. In particular, Kev's research
record identifies its Decision Index 0.2 entry as an older checkpoint; do not use
that ranking to grade our pinned artifact. Source inspection finds Julia and Lev
conversion support in our pinned runtime. Julia has since been executed in the
linked diagnostic; Lev has not.
OpenJev's noncommercial artifact restriction and ordinary-model scoring adapters
remain covered in the original shortlist below.

**Native-System-One closeout selection: retain Kev as the packaging reference.**
Julia tested the distinct small-model CPU hypothesis; it did not qualify as a
service replacement. No best-of-breed claim follows. Reopen model selection only
for a named workload under the [reopening conditions](when-to-use.md#research-closeout-and-reopening-conditions).
Native input fit, error/deferral limits, fresh evidence and measured resource cost
remain necessary for any later candidate. Larger inputs, concurrency and a
production rate requirement cannot be inferred from this short-input diagnostic.

The candidate table began as a source-only screen; Julia's linked run is the
subsequent local evidence. The material below preserves the original integration
rationale and earlier plans. It does not add obligations to the completed phase.

## Original bootstrap evidence

Follow-up: [native Metal validation](validation-metal.md) on the M3 Pro now compares
both 4B models at Q4_K_M on the same runtime. Qwen3.5-4B returned 46/48 correct
labels at 288 ms median versus Kev's 43/48 at 449 ms. This strengthens the case
for retaining seminstruct as the routing reference and positioning semselect
around typed model readouts. The earlier CPU/0.6B comparison below remains a
bootstrap record, not a fair model-size comparison.

## Historical project stance and investigation — 2026-10-05

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
A matched SGLang workload comparison remains unperformed and conditional on
a new caller need; it is not required for this closeout. SGLang supplies serving
mechanisms, not Jev's training. See the [bounded investigation](sglang-investigation.md).
This supersedes the initial shortlist's decision to defer SGLang research while
leaving the reference llama.cpp deployment and historical results unchanged.

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
