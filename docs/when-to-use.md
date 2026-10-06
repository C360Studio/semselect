# When to use a decision classifier

**Use one when it improves a specific semantic judgment over the simplest adequate
existing approach.** semselect exists to demonstrate where that happens—and where
code, retrieval or ordinary JSON output is enough.

"Jev-like" describes the bounded decision interface. Our service runs Kev; our
comparisons also use Qwen. Those results do not establish Jev's quality.

## Three questions before adding a classifier

1. **Can code determine the answer from known facts?** Use code for permissions,
   exact metadata, valid transitions and calculations.
2. **Does existing retrieval or a simple JSON response already meet the need?**
   Keep it unless an added classifier demonstrates a useful improvement.
3. **Is there an unresolved judgment about meaning?** A decision classifier may
   help—for example, distinguishing a relevant passage from one that actually
   contains the requested answer. The caller still owns actions and fallback.

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

## What we know so far

| Question | Finding | Guidance today |
| --- | --- | --- |
| Does existing fusion need another classifier for loose wording? | Semsource's historical scorecard passed all seven tested loose-language queries with its existing fusion path | No demonstrated need on that set; fusion can include learned embeddings |
| Does Kev beat ordinary Qwen for simple ticket routing? | The matched 4B smoke comparison favored Qwen JSON | Keep Qwen as the practical baseline |
| Does a model improve actual query-classifier hints? | On 32 authored Metal cases, code gets 18 exact and both models get 23, with different regressions; Kev takes about three times the request time | Fix ordinary rule/parser gaps first; test any model fallback on fresh residual cases |
| Does direct scoring make Qwen more useful? | Metal preserved labels with 28% lower median inference HTTP latency; CPU saved 3.4% but abstained more | A conditional efficiency benefit, with no demonstrated accuracy gain |
| Can a model distinguish relevant text from sufficient evidence? | On 24 new source cases, Kev allowed 5/12 unsupported inputs and preserved all 12 supported cases; Qwen allowed 7/12 unsupported inputs and preserved 11/12 | Kev earns a downstream comparison; both retain consequential omissions |
| Does a gate improve the existing generator's answers? | On 12 usable captured summary inputs, both gates reduced unsupported assertions; with the 4B generator, 3 became 0 and one useful partial answer remained | No Kev advantage observed; all captures lacked a full answer, so improve evidence and test a balanced set |
| Do the probabilities support better decisions about when to defer? | Calibration and threshold benefits remain untested; Kev assigned about 0.795 to one wrong allow | Do not interpret scores as calibrated correctness |

These are small, workload-specific findings. Full conditions, failures and raw
results remain in the [results history](results.md). The
[algorithm audit](code-baselines-and-rag.md) is supporting detail.

For the query-classifier task, “arithmetic mean of pressure” exposes a code parser
that captures `of`; both models return the right field. That is a measured model
correction, but also a concrete candidate for a small code fix. On “Show connections
from that device,” code and Qwen preserve the missing binding while Kev supplies
`sensor-17`. Recognizing meaning and returning a safe-to-use argument are separate
requirements. The next useful comparison should strengthen code and JSON's
cross-field constraints, then use new cases. Do not tune on these failures and
reuse them as independent proof.
Unlike a check of supplied authoritative facts, a regex interpretation can be
wrong. A model fallback used only when no rule matches will not correct false
rule matches on negation, quoted text or ambiguity; that policy needs its own test.

CPU Qwen reproduces the 23/32 primary result at a 49.25-second median. CPU Kev's
three completed calls take 185–196 seconds each; the owner stopped the remaining
run because another roughly 3½ hours would add little to this decision. Its
partial record establishes no cohort accuracy. Future CPU work should start with
bounded compatibility/latency probes and expand only for a concrete CPU deployment
question. Use the faster verified Metal path for the next quality comparison.

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
and headings had lost details available in the source documents. The next priority
is better evidence and a balanced downstream set that can reveal unnecessary
deferrals. Start CPU confirmation with bounded compatibility/latency probes;
expand to a full CPU gate comparison only for a concrete deployment question.
CUDA work remains deferred.

## How each experiment should read

Every experiment gets one question, a few understandable examples, a small results
table, and a verdict: **keep the baseline**, **worth a larger test**, or
**inconclusive**. End with "use this when…" and "prefer the baseline when…".
Readers can follow links for reproduction details and raw evidence.

An added classifier earns further work by allowing more supported cases at the
same observed unsupported-allow count, or achieving comparable decisions at lower
total cost. Tradeoffs need an explicit caller error budget before recommendation.
Small samples guide the next experiment; they do not certify production safety.
