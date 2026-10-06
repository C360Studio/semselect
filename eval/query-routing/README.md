# Can a model improve on our existing query classifier?

**Both models fix code misses and introduce new mistakes.** On the primary
32-case Metal view, the actual SemStreams code scores 18/32; Qwen JSON and Kev
both score 23/32. This is evidence of useful paraphrase interpretation, with no
observed primary accuracy advantage for Kev. It is not a drop-in replacement
recommendation. Metal's reversed order scores Qwen 23/32 and Kev 22/32. CPU
confirmation and its order-sensitivity check are still being collected under
the same frozen contract.

This task asks what a query requests and which arguments belong to it. The driver
imports the actual SemStreams classifier library at `v1.0.0-beta.160`. It does not
execute searches or judge whether retrieved passages answer a question.

## A few results you can inspect

| Question | Actual code | Qwen JSON, Metal | Kev, Metal |
| --- | --- | --- | --- |
| “Compute the arithmetic mean of pressure” | Average of **`of`**: wrong field | Average of pressure ✓ | Average of pressure ✓ |
| “Show maintenance records for pump-42” | No specialized hints ✓ | Unneeded node makes tuple invalid | Unneeded node makes tuple invalid |
| “Maybe count devices or average pressure; I have not decided” | Count: premature choice | No specialized hints ✓ | Average pressure: premature choice |
| “Show connections from that device” | Path with unresolved node ✓ | Path with unresolved node ✓ | Invents `sensor-17` as the node |

These are R17, R03, R31 and R32 in the [frozen cases](heldout.json).
R32 is a correct **partial classification**, not permission to execute: the caller
must resolve the missing node. Empty options mean no specialized hint; native
code has no abstain label, and its empty fallback does not prove understanding.

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

## The comparison

| Primary approach | Exact / 32 | Invalid tuples | Median time |
| --- | ---: | ---: | ---: |
| Actual keyword rules, native host | 18 | 0 | 0.032 ms |
| Rules + BM25 at `0.7`, native host | 18 | 0 | 0.032 ms |
| Rules + BM25 at development-selected `0.9`, native host | 18 | 0 | 0.034 ms |
| Qwen3.5-4B JSON, Metal | 23 | 8 | 2,381 ms |
| Kev-4B native Choice, Metal | 23 | 6 | 7,111 ms |

Code time covers the classifier call on Darwin; model time covers the complete
HTTP request and response validation. Constructor/cold-process times are recorded
separately. These are not matched service-level latencies. The models receive the
same query, full 20 training examples, choices and instructions; Qwen emits one
JSON object, while Kev answers operation, node and field as three Choice heads.
The [preserved evidence](../../docs/evidence/20261006-query-routing/README.md)
contains the complete rows and timing conditions.

Against the designated keyword comparator, Qwen fixes 12 errors and loses seven
successes; Kev fixes 11 and loses six. Both configured BM25 arms produce the same
held-out options as keyword rules, with no example matches accepted at these
thresholds. BM25's `0.9` threshold and keyword's comparator status were selected
on 18 development cases, before these outcomes were known.

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

## What to do with this result

**Improve the inexpensive baseline before adding inference.** Several code misses
are ordinary missing phrases (“number of,” “smallest,” “highest”) or extraction
of `of` instead of a metric. Fixing those may be cheaper and easier to validate
than operating a model. Test any fixes on new cases; these observed failures are
now development material, not a fresh test set.

Models can help interpret the remaining varied wording, but they must preserve
existing successes and return coherent arguments. The frozen Qwen schema constrains
keys and individual enums; cross-field rules are checked afterward. Its invalid
results are schema-valid JSON with incompatible search hints. A conditional schema
or caller-owned binding step is a stronger follow-up baseline, not a repair we
silently apply to these scores. Kev's three heads can also disagree.

**Use a model when** a recurring semantic residual survives maintainable code and
a fresh comparison shows useful corrections at acceptable errors and total cost.
**Prefer code when** known patterns and authoritative fields suffice. This run
does not establish a reason to prefer Kev over schema-constrained Qwen.

## Reproduction and limits

The [preparation review](review.md) and [design freeze](freeze.json) predate
held-out execution. The [execution manifest](execution.json) separately pins the
runner and runtime. The [detailed validation](../../docs/validation-query-routing.md)
records the startup failure, amendment, actual token budgets, cache observations,
resource bounds, raw evidence and reproduction commands.

Run `task routing:validate` for fixture/request/freeze checks, `task routing:test`
for offline tests, and `task routing:execution:validate` for the frozen runner and
driver binary. Real runs use `task routing:code`, `task routing:metal` or
`task routing:cpu`, each followed by `-- --output NEW_DIRECTORY`.

Relative time, geography, ranking and composed operations are outside this slice.
Actual graph-query dispatch is separate: its resolver differs from the library's
`InferStrategy`. See the [integration follow-up](../../docs/semstreams-integration.md#classifier-hints-and-production-dispatch).
No sibling repository or production classifier was changed.
