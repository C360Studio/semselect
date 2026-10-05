# When to use semselect

semselect exists to help a skeptical developer decide when a Jev-like decision
model is worth using, with evidence they can inspect and reproduce. We provide
a tested local service, comparisons against simpler alternatives, and guidance
that changes when the evidence changes. A recommendation to use ordinary code
or schema-constrained chat is a successful outcome of this project.

Here, "Jev-like" describes an interface for bounded semantic judgments: choose
among supplied options, assess an ordered scale, or evaluate a yes/no proposition.
Our current service runs Kev. Kev, Jev and ordinary Qwen with direct scoring are
different models and approaches; findings about one do not establish another's
accuracy or calibration. See the [research record](research-and-decision.md).

## Start with the decision you actually need

| Your situation | Starting point | What would justify another approach? |
| --- | --- | --- |
| The answer follows from authoritative fields and exact rules: permissions, balances, schema validity or allowed state transitions | Code | Missing semantic information may need interpretation; the rules still govern the action |
| Unstructured text needs a label or a few extracted fields | Schema-constrained chat as a baseline | A measured improvement in quality, latency, resource use or useful abstention |
| You need a bounded judgment and want scores to help decide when to defer | Evaluate a decision model or direct option scoring | More useful accepted decisions at the same tolerated error rate, measured on held-out examples |
| You need several independent judgments over the same input | Compare one multi-field JSON response with batched decisions | Better end-to-end quality or cost for the whole bundle; measure shared-input benefits |
| The decision requires missing records, external verification, a calculation or several reasoning steps | Obtain the evidence and perform the required computation | A bounded semantic judgment may fit within that workflow, but cannot supply missing authority |

Native probabilities are useful only if they improve a caller decision. A tidy
distribution and a valid response are not sufficient evidence. Likewise, code
that guesses intent from a few keywords remains a heuristic; being written as
an `if` statement does not make its premise authoritative.

The [existing-algorithm audit](code-baselines-and-rag.md) makes this concrete.
SemStreams already has keyword-first query routing, statistical example matching
and deterministic fusion/graph composition; learned embeddings may supply its NL
candidates. SemSource's historical scorecard found all seven tested loose-language
queries worked with the existing fusion path, so an extra classifier was not
justified there. Use that complete path as the baseline before adding a selector.
Keep query routing, retrieval relevance and evidence answerability as separate
tasks, and require an improvement on the task the caller actually needs.

## A concrete example: a refund request

Suppose a customer writes, "I was charged twice; can you sort this out?"

1. A model can interpret the message as a possible billing/refund request. That
   is a semantic judgment, and a schema-constrained label may be all you need.
2. Code obtains transaction records and checks eligibility, identity, permissions
   and policy. The message and the model's confidence cannot prove a duplicate
   charge or authorize a refund.
3. If a bounded classifier helps, evaluate `billing`, other relevant routes and
   `unknown`. The caller can defer uncertain cases and enforce policy on every
   accepted label before executing anything.

The reason to try the current semselect service here would be a demonstrated
improvement in routing or deferral compared with the simpler label baseline.
That benefit remains unproved for Kev. A separate ordinary-Qwen experiment below
shows a latency benefit from one-token scoring on Metal, with the same decisions.
The winning option always exists mathematically in a nonempty list; the caller
still needs a way to express that no offered option fits.

## What the evidence currently says

These are findings from 2026-10-05, not universal model rankings. The routing set
contains 24 manual cases in two option orders. The original model comparison
has 48 correlated observations per model; the later format comparison repeats
both orders twice. Both 4B models used the same pinned llama.cpp runtime and
Q4_K_M quantization class on an M3 Pro with Metal. Prompts, serving paths and model
training differ in the model comparison.

| Question | Evidence | Practical conclusion |
| --- | --- | --- |
| Does the local service work? | Real native Choice, Score and Noul smoke checks, health, request bounds and lifecycle checks | The documented service is usable for evaluation; these checks do not certify decision quality |
| Does Kev improve simple routing over the matched Qwen baseline? | Kev: 43/48 correct, 449 ms median. Qwen: 46/48, 288 ms median | This comparison favors Qwen's schema-constrained labels |
| Do Kev scores establish reliable abstention? | Rejecting `unknown` and requiring a top-option probability of at least 0.7 accepted 29/48 calls, including two wrong labels | A probability threshold alone does not establish reliable decisions; production calibration remains unproved |
| Can ordinary Qwen benefit from direct option scoring? | On Metal, the same Qwen3.5-4B returned identical labels with JSON and one-token scoring: 92/96 correct for each, with 673 ms versus 488 ms median inference HTTP latency | A measured format-efficiency benefit for this workload; no accuracy gain or evidence of Jev-specific training benefits |
| Does that Metal result transfer unchanged to CPU/Docker? | JSON: 92/96 correct, 11,191 ms median. Scoring: 90/96, 10,816 ms; one additional `unknown` per trial | A 3.4% raw median reduction with lower coverage does not justify the same recommendation; validate the actual deployment |
| Are ordered scores, yes/no judgments or bundles better than chat? | API smoke checks exist; matched workload-quality comparisons do not | Worth investigating when a caller needs them; no recommendation based on measured superiority yet |
| Does SGLang direct scoring improve ordinary Qwen? | After preserved warmup failures, a [cache-disabled MLX probe](evidence/20261005T154252Z-sglang-metal-cache-disabled/README.md) passed JSON, scoring and decision-bundle checks on one fixture | The mechanism works in that configuration; workload quality, useful abstention and speedup remain unproved |

Inspect the [comparison history](results.md), [Metal validation](validation-metal.md)
and raw [Kev](evidence/metal-kev-4b.json) / [Qwen](evidence/metal-qwen35-4b.json)
results. Reproduction commands and exact conditions are in the validation report.
The Qwen run used the chat API directly, bypassing semselect; it represents the
seminstruct-style approach, not a validated upgrade of the seminstruct release.
The displayed threshold was inspected on this small set, not independently
validated as a production policy.

The [direct-scoring experiment](validation-scoring.md) holds the Qwen model and
runtime fixed, repeats both candidate orders twice and disables prompt-prefix
reuse. Its 96 calls per format are still only 24 distinct examples. It uses
evaluation-only llama.cpp code, separate from the production semselect API.
Both formats make the same two reversed-order mistakes in each trial. Scoring
returns one token versus JSON's seven; it exposes candidate probabilities, but
their value for production deferral still needs held-out evidence. Output-format
instructions differ, and scoring preparation is recorded outside inference HTTP
latency. Keep those conditions attached to the timing claim.

A practical reason to investigate this path is repeated, bounded interpretation
where labels alone are sufficient and decoding time is a meaningful part of the
latency budget. If JSON already meets the budget, the observed speedup may not
justify another integration. A need for explanations, extracted fields or
multi-step reasoning calls for a different comparison.

## What would earn a positive recommendation?

We will recommend a decision-model path for a specific workload when evidence
shows a useful gain over its simplest adequate alternative. Define acceptable
errors, latency and resource limits before evaluating. Keep the model and hardware
fixed when testing a serving mechanism, and label model comparisons separately.

A worked example should let a reader answer:

- What interpretation is needed, and which parts remain ordinary code?
- What does the simpler baseline get right or wrong?
- Does the alternative accept more correct cases at the same error budget, or
  reduce latency/resource cost at comparable quality? How often does it defer?
- What fails under ambiguity, missing evidence, changed option order or injected
  instructions? What happens after an error, timeout or unknown response?
- Which exact model, runtime, hardware, prompts and data produced the result,
  and how can someone reproduce it?

For scores, choose thresholds or calibration settings on development examples
and evaluate them on a separate held-out set. Report errors together with coverage
and uncertainty; a zero-error result on a tiny accepted subset proves little.
Retain negative and inconclusive outcomes alongside successful ones.

Until such a benefit is demonstrated, treat a new use case as an experiment.
"Proof" here means reproducible evidence for a stated workload and operating
conditions. The service, comparison matrix and explanation should make that
boundary easy for the next skeptic to understand.
