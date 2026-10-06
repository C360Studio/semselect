# When to use a decision classifier

**Research closeout, 2026-10-06: keep semselect as an evaluation/reference
service. No production sem* integration is recommended from these results.** We have measured useful
model behavior, but have not yet identified a production task where this service
earns its additional cost over the applicable alternatives.

"Jev-like" describes the bounded decision interface. Our service runs Kev; our
comparisons also use Qwen. Those results do not establish Jev's quality.
Kev was selected for integration fit and reproducibility; we have not established
that it is the best open-source decision model. See the bounded
[selection review](research-and-decision.md#is-kev-our-best-open-source-choice)
before generalizing these results to other decision models.

## Choose by the job

| Caller needs to… | Start with… | When a decision model merits a test |
| --- | --- | --- |
| Check an exact fact, permission or allowed transition | Caller code and authoritative data | Text interpretation may supply a hint; it never replaces the check. |
| Retrieve relevant documents or communities | Existing lexical/statistical ranking, embeddings and applicable reranking | A remaining semantic judgment improves downstream answers, rather than merely producing another relevance score. |
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

**For our routing tasks, use Qwen JSON as the first model baseline.** The matched
4B ticket test favored Qwen, and the primary query-classifier test tied Qwen and
Kev at 23/32. Kev's native distribution API has not demonstrated a routing benefit
that changes this recommendation. See the [routing results](results.md).

**Improve the existing query rules and binding logic before replacing them.**
Models corrected real language failures: code got 18/32 exact, compared with
23/32 for either model. But Qwen lost seven existing successes and Kev lost six;
Kev also invented a missing node. We found concrete parser defects such as
extracting `of` instead of `pressure`. Fixing them is a justified next step, not
proof that improved code will beat a model—we have not run that comparison.
See the [worked cases](../eval/query-routing/README.md).

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

**The CPU/Metal research phase is complete. Keep the working reference service
and its evidence; do not add a specialized decision service to sem* on this basis.**
We have answered enough to make that decision even though we have not found a
positive adoption case. SGLang performance, CUDA and more model candidates are
conditional investigations, not unfinished requirements of this phase.

Three gaps limit broader conclusions. Improved code and conditional JSON have
not been compared on fresh cases; a small trained classifier is missing for
stable-label tasks; and useful confidence-based deferral has not been demonstrated.
The [closeout audit](research-and-decision.md#closeout-review-2026-10-06) also records
serving confounds: one-slot repeated state processing, unequal cache reuse, and
unvalidated calibration after quantization. Those gaps prevent a universal model
ranking. They do not create a reason to deploy a service without a measured benefit.

Reopen only when a team can name:

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

A credible future Tier 2 case is selecting which retrieved communities to expand,
or replacing an existing LLM review of uncertain graph candidates. First hydrate
the evidence and compare with existing embedding/ranking paths; for a ranking
question, include an applicable reranker. Measure supported answers retained per
context budget and the cost of discarding the only answer-bearing community.
Prefer prioritization over irreversible pruning until that cost is understood.
This remains a hypothesis, not a demonstrated need for semselect. The relevant
[retrieval audit](code-baselines-and-rag.md) and
[answer-path audit](answering-path.md) preserve the source context.

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
