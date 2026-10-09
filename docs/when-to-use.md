# When to use a bounded decision model

**Evidence update, 2026-10-07: keep semselect as an evaluation/reference
service. No production sem* integration is recommended from these results.** We have measured useful
model behavior, but have not yet identified a production task where this service
earns its additional cost over the applicable alternatives.

**Scope update, 2026-10-08:** this recommendation describes the workloads tested.
Community formation/refinement and graph maintenance remain unmeasured. The
[new evaluation design](../eval/community-refinement/README.md) targets SemEngine,
which will replace SemStreams when ready. It does not turn routing failures into
a conclusion about graph decisions, or a promising graph hypothesis into adoption
evidence. The required clustering/semantic-edge port is not present in the audited
SemEngine main or ingest PR yet.

"Jev-like" describes the bounded decision interface. Our service runs Kev; our
comparisons also use Qwen. Those results do not establish Jev's quality.
Kev was selected for integration fit and reproducibility; we have not established
that it is the best open-source decision model. See the bounded
[selection review](research-and-decision.md#is-kev-our-best-open-source-choice)
before generalizing these results to other decision models.

## Current query-classification decision

**Keep Qwen3.5-4B JSON as the model baseline alongside the improved-code baseline.**
This is our starting point for the app's routing work, not a claim that either
already satisfies the full acceptance gates. On the same 120 operation cases,
4B obtained 111 correct, eight wrong and one defer; improved rules obtained
95 correct and 25 wrong. The rules remain evaluation-local. The combined caller
policy and its end-to-end quality/latency are still unmeasured.

| Question we need to answer | Current answer |
| --- | --- |
| Should we replace 4B with a smaller pretrained Qwen? | No, on these fixed configurations: 2B got 84/120 and 1.7B 93/120. Both failed quality and uncached CPU latency. [Evidence](validation-qwen-size.md). |
| Should we add a specialist backend? | No measured benefit here. The selected DeBERTa accepted only 13 correct cases and deferred 106; GLiClass was also weak. [Evidence](validation-specialist-intent.md). |
| Must we do our own training? | That has not been established. A supervised small classifier remains unmeasured. Evaluation labels are necessary regardless of whether we train. |
| Is 4B already fast enough? | Its 867/1,044 ms Metal median/p95 misses the accepted 250/750 ms classifier budget. Retaining it as a quality baseline does not waive the latency target. Small-model Metal and shared-prefix optimizations were not tested. |
| Do we need serious-scale infrastructure? | No such requirement has been established beyond a small team. Concurrency/load checks did not run because prerequisite gates failed. Avoid a new service or provider framework on this evidence. |
| What should app work focus on? | Bring measured rule/binding improvements into caller tests, retain Qwen4B for semantic comparison, and measure any proposed fallback policy on representative fresh queries. [Proposed integration work](semstreams-integration.md#query-classification-baseline-and-next-app-work). |

The common mistake is treating a selected operation as proof that a specialized
operation applies. The smaller Qwens recognized most explicit operations but
frequently overrode ordinary search. Code still owns binding, validation,
authorization and actions. No measured result establishes that 4B is the minimum
possible size, that all small models need training, or that a code-plus-model
router meets the app's targets without its own evaluation.

## Choose by the job

| Caller needs to… | Start with… | When a decision model merits a test |
| --- | --- | --- |
| Check an exact fact, permission or allowed transition | Caller code and authoritative data | Text interpretation may supply a hint; it never replaces the check. |
| Retrieve relevant documents or communities | Existing lexical/statistical ranking, embeddings and applicable reranking | A remaining semantic judgment improves downstream answers, rather than merely producing another relevance score. |
| Improve community boundaries or semantic graph hints | Existing weighted clustering, identity/explicit edges and tuned semantic-neighbor selection | Reviewing bounded proposals improves the resulting communities and retrieved evidence within a background-processing budget. This workload is **unmeasured here**. |
| Assign stable labels with representative training data | A small supervised classifier, such as embeddings plus a trained linear head | It beats that trained baseline after including labeling, training and serving costs. This baseline is **unmeasured here**. |
| Interpret text with changing caller-defined choices and little labeled data | Schema-constrained Qwen as the first model baseline | A specialized model improves the actual caller's errors, abstention or total cost. |
| Reduce the cost of an existing bounded LLM judgment | Measure output overhead, then compare ordinary-model one-token scoring and native decision heads | The cheaper implementation preserves acceptable decisions at the required input size and concurrency. |

A decision interface offers a useful contract: bounded options, typed answers and
an explicit way to defer. It does not itself establish the need for a new model or
service. Variable instructions/options can be useful without per-task training;
ordinary instruction-following models also provide that flexibility. Native
readouts avoid generating a confidence claim, but their scores still require
workload validation before they control a threshold.

**Zero-shot can avoid task-specific training; it does not avoid labeled
evaluation.** Every route needs representative examples with known answers to
measure error rates and choose deferral thresholds. Fit a calibration or threshold
on development data and confirm it separately. Training labels and evaluation
labels serve different purposes; a fitted temperature alone does not establish
robustness to new wording or domains.

The tradeoff is not a hard split by parameter count. A hosted model adds external
data handling, vendor dependence and usage cost. An open model adds local serving
cost, whether CPU or GPU. A small supervised model also needs training data and a
training/update process. Reusing Qwen can reduce integration work but still costs
inference and validation. A 27B model is not categorically impossible on CPU;
its acceptable latency and capacity would need measurement. Conversely, Julia's
fast but inaccurate diagnostic does not prove every small zero-shot CPU model
needs fine-tuning.

System One is an API contract, not a quality guarantee. Compatible models can
share transport code, but changing the backend still requires checking input
limits, decision quality, ordering effects and score/threshold behavior. Ordinary
Qwen's chat/scoring path is not automatically a native System One replacement.

## Decisions the evidence supports today

**For our routing tasks, use Qwen JSON as the first model comparison baseline,
not as a latency-qualified deployment.** On Metal, the ticket test favored Qwen
in both quality (46/48 versus Kev's 43/48) and median latency (288 versus 449 ms).
The full query-classifier task tied at 23/32, but Qwen's median was 2.38 seconds
and Kev's 7.11 seconds. Kev was not the faster routing alternative in those runs.
Cache reuse and Kev's three-head execution limit architectural comparisons; the
observed serving cost still matters. See the [paired quality and latency table](../README.md#qwen-versus-kev-quality-and-latency).

The later **250 ms median / 750 ms p95 CPU target** belongs to the separate
120-case operation-only contract. Its Qwen3.5-4B quality reference ran on Metal at
867/1,044 ms median/p95, with eight wrong accepts; it was not a CPU qualification.
**Kev was not tested on that contract.** The earlier three-head task is neither
a passing result nor a direct operation-only latency measurement for Kev. No
model in the later CPU comparison qualified on both quality and latency. A
recommendation to compare against Qwen does not waive either requirement.

**Improve the existing query rules and binding logic before replacing them.**
The earlier full query-plan comparison showed real language failures: code got 18/32 exact, compared with
23/32 for either model. But Qwen lost seven existing successes and Kev lost six;
Kev also invented a missing node. We found concrete parser defects such as
extracting `of` instead of `pressure`. See the
[worked cases](../eval/query-routing/README.md).

The new [bounded specialist pilot](validation-specialist-intent.md) separates
operation selection from shared coded binding. On 120 fresh held-out cases,
existing keyword/BM25 obtained 61 correct operations, evaluation-local improved
rules 95, and Qwen JSON 111. The preselected DeBERTa specialist accepted only
13 correct operations, made one wrong specialized selection and deferred 106;
its CPU median/p95 were 1.13/2.05 seconds. GLiClass achieved 37 raw correct and
changed 15 of 24 decisions when label order reversed. **Keep the baseline; these
specialists did not earn an integration prototype.** The improved rules and
Qwen still accepted 25 and eight wrong operations respectively. These authored
operation-only results do not certify automatic actions, and the experimental
rules have not been shipped to the caller.

The [smaller-Qwen follow-up](validation-qwen-size.md) tested Qwen3.5-2B and
Qwen3-1.7B on that same 120-case cohort. They obtained 84 and 93 correct operations,
with 36 and 27 wrong accepts, compared with the historical 4B's 111 correct.
Their uncached four-thread ARM64 CPU median/p95 latencies were 5.93/6.13 seconds
and 5.27/5.48 seconds, exceeding the accepted 250/750 ms target. **Neither smaller
pretrained model qualifies on this contract.** Most errors selected a specialized
operation when ordinary search should remain in control. These results do not
prove that 4B is the smallest model that could work, or that custom training is
required; they reject these two fixed configurations. The cohort was reused,
and no small-model Metal or shared-prefix-cache result is established.

**For answering, improve the evidence before adopting an added gate.** Either
gate reduced the 4B generator's unsupported assertions from three to zero on
12 captured inputs, preserving one useful partial answer. The benefit is real
on that capture. None of the inputs contained a complete answer, so the experiment
cannot tell us how often gating would block good answers. Kev's 19/24 versus
Qwen's 16/24 on the earlier source-passage pilot did not yield a Kev advantage on
the captured-summary task. See the [answer replay](../eval/synthesis/README.md).

**If output cost matters, one-token scoring deserves consideration with ordinary
Qwen.** On the Metal ticket test it preserved every JSON label while reducing
median inference HTTP latency by 28%. This is a measured efficiency benefit that
does not require Kev. This method still generates one token; it does not reproduce
a native zero-output decision head. It establishes neither better accuracy nor reliable
confidence thresholds. CPU's smaller saving came with more abstention.
See the [same-model comparison](validation-scoring.md).

**Use Metal for further 4B quality experiments on this laptop.** CPU operation is
verified, but the richer query task took a 49.25-second median for Qwen and
185–196 seconds on Kev's three completed CPU calls. CPU is still useful for bounded
compatibility and latency checks. See the [hardware observations](../eval/query-routing/README.md#the-result).

**CPU-only cost can be practical for a smaller model; qualify its decisions
separately.** Julia-1 Q8_0 completed the existing ticket diagnostic on Linux/ARM64
CPU at 159 ms median and a 530 MiB cgroup memory peak. Its 24/48 correct selections,
23 unnecessary fallbacks and seven order-sensitive cases do not justify it as a
general router. This is short-input feasibility, not a community-evidence result
or proof that all CPU-only decision models will behave similarly.
See the [Julia diagnostic](../eval/julia/README.md).

## Research closeout and reopening conditions

**The initial CPU/Metal phase is complete for its routing and evidence-judgment
workloads. Keep the reference service and evidence; those results do not justify
production integration.** That closeout did not evaluate graph refinement.
The owner requested a separate SemEngine community-refinement design on
2026-10-08. Execution depends on the port and fixture readiness described in its
[protocol](../eval/community-refinement/README.md), not another query classifier.
SGLang performance, CUDA and more model candidates remain conditional
investigations, not unfinished requirements of the initial phase.

Several gaps limit broader conclusions. The fresh specialist pilot now compares
improved rules, reused embeddings and operation-only Qwen with shared binding,
but full caller integration and production traffic remain untested. A small
trained classifier is still missing for stable-label tasks, and specialist
confidence thresholds did not provide useful coverage within the frozen error
and latency budgets.
The [closeout audit](research-and-decision.md#closeout-review-2026-10-06) also records
serving confounds: one-slot repeated state processing, unequal cache reuse, and
unvalidated calibration after quantization. Those gaps prevent a universal model
ranking. They do not create a reason to deploy a service without a measured benefit.

Before executing a new workload comparison, name:

1. **A caller and a shortfall:** a necessary semantic judgment whose error rate,
   latency or required decisions per second exceeds the current path's budget.
2. **A fair comparison:** representative fresh cases and identical evidence,
   including the strongest applicable code, retrieval, trained classifier or Qwen
   baseline. Fix observed defects on development cases; judge on separate data.
3. **An acceptance rule:** allowed errors and deferrals, input/context limits,
   target hardware, and total resource cost. Measure throughput and tail latency
   under that workload before claiming high-rate capacity.

Then test one candidate or serving change that addresses the shortfall. Neither
CPU, Metal, CUDA nor a particular parameter count is automatically the right
answer. Calibration/thresholds need development data and separate confirmation;
a normalized distribution is not a correctness probability.

The [SemEngine pilot](../eval/community-refinement/README.md) starts earlier in
the graph lifecycle: review semantic co-location hints, rerun the existing
clustering algorithm, and measure harmful grouping, useful evidence retained and
total pilot-cycle cost. It compares the structural and tuned embedding paths,
a trained edge reviewer, Qwen and Kev. It preserves explicit facts and identity
edges, and leaves the baseline unchanged on deferral or failure. Its background
budget is distinct from the query router's subsecond target.

Missing-edge proposals, membership moves, merge/split choices, factual anomaly
review and summary validation are separate untested graph tasks. Selecting which
retrieved communities to expand is another retrieval task, requiring applicable
ranking/reranking baselines and a measure of losing the only answer-bearing
community. The historical [retrieval audit](code-baselines-and-rag.md) and
[answer-path audit](answering-path.md) do not establish SemEngine integration or
community-refinement quality. New proposals belong in
[SemEngine integration](semengine-integration.md).

## What our code baseline actually does

The answerability experiments use a **code precheck**, implemented in
[`common_gate`](../scripts/answerability.py). It checks supplied fields; it does
not interpret the question or read facts out of prose. This is evaluation code
illustrating caller policy, separate from semselect's Go API guard.

The caller supplies the audience, passage status/audience, an optional required
fact key, and fact records tied to passages. First, code keeps only current
passages for that audience (or everyone), and facts tied to those passages. Then:

| Condition after filtering | Code decision |
| --- | --- |
| No passages remain | Defer |
| A required fact key has exactly one distinct supplied value | Allow |
| A required fact key has no supplied value or conflicting values | Defer |
| No required fact key was supplied | Unresolved |

For example, [A01](../eval/answerability/examples.md#a01) supplies
`required_fact = seminstruct.port` and a fact record with value `8083`. Code allows
it without calling a model. It compares supplied strings; it neither discovers
the key from the question nor extracts `8083` from the document. The fixture
author supplied those fields. They are trusted experiment inputs, not an existing
SemSource extraction contract or an implemented authorization system.

**Unresolved does not mean answerable.** The “no added semantic gate” control
allows unresolved cases through. Qwen and Kev instead judge the remaining text.
All three approaches share the same precheck: it resolves 4/12 teaching cases and
2/24 source cases before any model call. In the later synthesis replay, the 12
usable captures supply no structured facts or required keys, so all remain
unresolved; the control uses the existing generator, which can itself refuse.

SemStreams also has real code-based query classifiers: regex rules recognize
query patterns, and an optional BM25 example matcher compares word-weighted
vectors with labeled examples. Fusion retrieves and assembles evidence. Those
are different jobs from deciding whether evidence fully answers a question; the
[algorithm audit](code-baselines-and-rag.md) explains their wiring and limits.

**We have not yet compared a strong code-only text classifier with these models
on the same answerability task.** The current control measures the effect of
adding a semantic check. It cannot establish that models beat regex, BM25 or
fusion. A fair additional comparison needs an applicable algorithm, development
examples to set its rules/thresholds, and fresh held-out cases.

The [query-classifier experiment](../eval/query-routing/README.md) now tests the
actual rules and configured BM25 on their own intent/argument task, with a reviewed
32-case authored cohort. On its primary Metal view, all three code arms get
18/32 exact; Qwen JSON and Kev each get 23/32. Qwen fixes 12 code errors but loses
seven code successes; Kev fixes 11 and loses six. Their extra mistakes include
incompatible arguments, and Kev invents a node for an unresolved reference.
Neither is a drop-in improvement on every case. This remains a different task
from answerability and does not establish superiority over code on that task.

Semsource's historical fusion scorecard passed all seven tested loose-language
queries using its existing path, which can include learned embeddings. That
provides no demonstrated need for an added classifier on that set. The
[algorithm audit](code-baselines-and-rag.md) explains this separate retrieval
evidence; it is not a head-to-head answerability result.

## A measured example: does the evidence answer the question?

Question: **"Which title property replaces `source.doc.summary`, and what happens
when the body store is unavailable?"**

| Supplied text | Expected action |
| --- | --- |
| Replacement property and startup-failure behavior are both supplied | Allow an answer |
| Startup-failure behavior is supplied, but the replacement property is absent | Defer: one requested detail is missing |

These are cases H15/H16 in the [real-source pilot](../eval/answerability/heldout/README.md).
In the primary normal-order view, Kev correctly distinguishes them; Qwen allows
both. But Kev also allows other
incomplete evidence, including a question asking for an unspecified waiting
period. It fixes four Qwen errors and introduces one, rather than winning every
case. Code resolves two exact-fact cases without a model. Median decision time on
the other 22 was 940 ms for Qwen and 985 ms for Kev on Metal; differing cache reuse
and serving paths limit that timing comparison.

The control applied shared coded checks, then allowed unresolved cases. We did
not rerun retrieval or test whether the existing answer generator would already
refuse. These manually selected excerpts and deliberate ablations are a pilot,
not production traffic. The [answer-path audit](answering-path.md) shows that the
existing generator is already instructed to acknowledge missing information and
consumes community summaries. The [subsequent synthesis replay](../eval/synthesis/README.md)
preserved both. Qwen and Kev made identical gate decisions and reduced unsupported
assertions, but neither rescued the 0.6B generator's remaining disk-permission
invention. The 4B generator gave a useful qualified answer to that same allowed
question. A gate decision is not permission to execute an action.

That capture contained no fully answerable questions: broad community descriptions
and headings had lost details available in the source documents. If this caller
justifies reopening, improve evidence and include fully answerable questions so
unnecessary deferrals are measurable. More hardware cannot restore facts omitted
from the input.

## How each experiment should read

Every experiment gets one question, a few understandable examples, a small results
table, and a verdict: **keep the baseline**, **worth a larger test**, or
**inconclusive**. End with "use this when…" and "prefer the baseline when…".
Readers can follow links for reproduction details and raw evidence.

An added classifier earns further work by allowing more supported cases at the
same observed unsupported-allow count, or achieving comparable decisions at lower
total cost. Tradeoffs need an explicit caller error budget before recommendation.
Small samples guide the next experiment; they do not certify production safety.
