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
| Can a model distinguish relevant text from sufficient evidence? | Code resolved four teaching cases; both Qwen JSON and Kev correctly judged the remaining eight, catching four unsupported cases that a no-added-gate control allowed | Worth a held-out test; no decision-quality advantage for Kev here |
| Do the probabilities support better decisions about when to defer? | No independent held-out evidence yet | Do not interpret scores as calibrated correctness |

These are small, workload-specific findings. Full conditions, failures and raw
results remain in the [results history](results.md). The
[algorithm audit](code-baselines-and-rag.md) is supporting detail.

## A measured example: does the evidence answer the question?

Question: **"When must I submit an expense claim?"**

| Retrieved text | Expected action |
| --- | --- |
| "Expense claims must be submitted by the 25th." | Allow an answer |
| "Use the expense portal to submit a claim." | Defer: the deadline is missing |
| Two equally applicable passages give conflicting deadlines | Defer: the conflict is unresolved |

These summarize cases in the [completed teaching experiment](../eval/answerability/README.md).
Both models made the expected decisions, including allowing a supported answer
assembled from two passages. Median decision time on the eight cases needing a
model was 553 ms for Qwen JSON and 530 ms for Kev on Metal. Different serving paths
and observed cache reuse limit that timing comparison: Qwen reused prefixes while
Kev reprocessed each full prompt.

The control applied shared coded checks, then allowed unresolved cases. We did
not rerun retrieval or test whether the existing answer generator would already
refuse. These constructed examples explain the task; they do not prove the benefit
of inserting a gate into SemSource. Next, freeze unseen real-source families and
test the complete answering pipeline before recommending an integration. Repeat
a useful finding on CPU/Docker before seeking CUDA help.

## How each experiment should read

Every experiment gets one question, a few understandable examples, a small results
table, and a verdict: **keep the baseline**, **worth a larger test**, or
**inconclusive**. End with "use this when…" and "prefer the baseline when…".
Readers can follow links for reproduction details and raw evidence.

An added classifier earns further work by allowing more supported cases at the
same observed unsupported-allow count, or achieving comparable decisions at lower
total cost. Tradeoffs need an explicit caller error budget before recommendation.
Small samples guide the next experiment; they do not certify production safety.
