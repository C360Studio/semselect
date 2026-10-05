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

## What we know so far

| Question | Finding | Guidance today |
| --- | --- | --- |
| Does existing fusion need another classifier for loose wording? | Semsource's historical scorecard passed all seven tested loose-language queries with its existing fusion path | No demonstrated need on that set; fusion can include learned embeddings |
| Does Kev beat ordinary Qwen for simple ticket routing? | The matched 4B smoke comparison favored Qwen JSON | Keep Qwen as the practical baseline |
| Does direct scoring make Qwen more useful? | Metal preserved labels with 28% lower median inference HTTP latency; CPU saved 3.4% but abstained more | A conditional efficiency benefit, with no demonstrated accuracy gain |
| Can a model distinguish relevant text from sufficient evidence? | On 24 new source cases, Kev allowed 5/12 unsupported inputs and preserved all 12 supported cases; Qwen allowed 7/12 unsupported inputs and preserved 11/12 | Kev earns a downstream comparison; both retain consequential omissions |
| Does a gate improve the existing generator's answers? | On 12 usable captured summary inputs, both gates reduced unsupported assertions; with the 4B generator, 3 became 0 and one useful partial answer remained | No Kev advantage observed; all captures lacked a full answer, so improve evidence and test a balanced set |
| Do the probabilities support better decisions about when to defer? | Calibration and threshold benefits remain untested; Kev assigned about 0.795 to one wrong allow | Do not interpret scores as calibrated correctness |

These are small, workload-specific findings. Full conditions, failures and raw
results remain in the [results history](results.md). The
[algorithm audit](code-baselines-and-rag.md) is supporting detail.

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
deferrals. Confirm any useful result with CPU-only gates before seeking CUDA help.

## How each experiment should read

Every experiment gets one question, a few understandable examples, a small results
table, and a verdict: **keep the baseline**, **worth a larger test**, or
**inconclusive**. End with "use this when…" and "prefer the baseline when…".
Readers can follow links for reproduction details and raw evidence.

An added classifier earns further work by allowing more supported cases at the
same observed unsupported-allow count, or achieving comparable decisions at lower
total cost. Tradeoffs need an explicit caller error budget before recommendation.
Small samples guide the next experiment; they do not certify production safety.
