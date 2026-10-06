# Can a model improve on our existing query classifier?

**Preparation only: no held-out accuracy results yet.** This experiment tests
what a query asks for and which arguments belong to it. The driver imports the
actual SemStreams classifier library at `v1.0.0-beta.160`. It does not execute
searches or judge whether retrieved passages answer a question.

The [preparation review](review.md) records independent review and development
selection. BM25's selected threshold is `0.9`; keyword rules remain the designated
comparator after a 12/18 development tie. These are tuning results, not test-set
accuracy. All 32 test cases remain unexecuted.

## What the code does

**Rules:** regular expressions recognize phrases such as “how many,” “similar,”
or “connected to.” They produce search hints: count entities, use similarity,
or follow graph links from a literal node. Rules can also extract a metric name.
They do not read the corpus or call an LLM.

**Rules plus BM25 examples:** try those same rules first. If none matches, turn
the query into a word-weighted vector and compare it with labeled examples.
Above a similarity threshold, copy the closest example's stored options. This
uses word statistics and feature hashing, with no learned embedding model.
It **copies that example's arguments; it does not extract fresh arguments**.
A keyword match bypasses BM25 even when the keyword result is wrong.

The [driver explanation and source pins](driver/README.md) show the actual
functions and a synthetic example of an old node ID being copied. BM25 is
available upstream, but the audited graph-query component does not enable it.
Our configured BM25 arms supply new training examples; “default” refers only
to the upstream threshold of `0.7`, not a shipped example configuration.

## Three examples of what will count

| Question | Required classification | Why a label alone is insufficient |
| --- | --- | --- |
| “What is connected to sensor-17?” | Path intent, start node `sensor-17` | Copying `pump-42` would bind the wrong node |
| “Compute the arithmetic mean of pressure” | Average, field `pressure` | Extracting `of` as the field is wrong |
| “Do not count sensors; list their names” | No specialized hints | Seeing the word “count” is insufficient |

These are expected answers, not observed outcomes. An explicit path request with
no named node is a valid **partial classification**: retain path intent and report
the unresolved binding. The caller must resolve it before execution. Native code
has no abstain label; empty options mean no override, not proven understanding.

## The comparison

| Approach | Configuration |
| --- | --- |
| Actual keyword rules | Pinned upstream implementation |
| Rules + configured BM25 at `0.7` | Same rules and 20 training examples |
| Rules + configured BM25 at a development-selected threshold | Same examples; tune only on 18 development questions |
| Qwen3.5-4B JSON | Same query, full training examples, choices and instructions |
| Kev-4B native Choice | Same information; choose operation, node and field in three questions |

The **32 held-out authored questions** cover ordinary text, similarity, paths,
zones and five numeric aggregations. They include paraphrases, negation, quoted
operator words, ambiguity and missing bindings. Fixed node/metric vocabularies
are identical for every arm. This is bounded selection, not open-vocabulary
extraction. Authors inspected the implementation and related phrases cross the
development/test split; this pilot does not establish broad generalization.

Score exact search hints, including arguments and unwanted flags. Invalid model
combinations remain failures. Report corrections and regressions against a code
baseline selected on development, plus each other code arm. Correct partial
intent remains distinct from readiness to execute.

BM25 updates its statistics as it sees queries. Primary cases use fresh
classifiers; separate persistent forward/reverse runs test order sensitivity.
Record construction, classification and process time separately. Qwen emits one
JSON object; Kev makes three decisions in one HTTP request. Count the full work.

## Preparation and execution

Review the contract and labels, select the threshold using development only,
then freeze fixtures, driver, grading code and model request hashes. Complete
and independently review the execution runner next; verify actual token budgets
and cache settings before held-out calls. Add an execution manifest that retains
this design freeze. Run on Metal, then confirm useful findings on CPU-only Docker.

Relative time, geography, ranking and composed operations are outside this slice.
Actual graph-query dispatch is also separate: its resolver differs from the
library's `InferStrategy`. See the [integration follow-up](../../docs/semstreams-integration.md#classifier-hints-and-production-dispatch).

Run `task routing:validate` for fixture/request/freeze checks and `task routing:test`
for offline adapter and driver tests. The [protocol](protocol.json),
[training](training.json), [development](development.json) and
[held-out cases](heldout.json) keep the rules and questions inspectable.

**Keep code if it handles the task adequately.** A model needs a demonstrated
residual benefit. If a few maintainable rule fixes explain the gap, improve code
before adding inference.
