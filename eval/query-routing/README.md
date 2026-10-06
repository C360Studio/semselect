# Can a model improve on our existing query classifier?

**Both models fix code misses and add new mistakes.** On 32 authored Metal cases,
actual SemStreams code gets 18 exact classifications; Qwen JSON and Kev each get
23. That shows useful interpretation of varied wording, with no primary accuracy
advantage for Kev. It does not justify replacing working code indiscriminately.

This task classifies **intent and arguments**. It does not execute graph searches
or judge whether retrieved evidence answers a question. CPU Qwen is complete;
CPU Kev was intentionally stopped after three completed calls because finishing
the matrix offered little value for another roughly 3½ hours of inference.

## The result

| Primary approach | Exact / 32 | Invalid tuples | Median time |
| --- | ---: | ---: | ---: |
| Actual keyword rules, native host | 18 | 0 | 0.032 ms |
| Rules + BM25 at `0.7`, native host | 18 | 0 | 0.032 ms |
| Rules + BM25 at development-selected `0.9`, native host | 18 | 0 | 0.034 ms |
| Qwen3.5-4B JSON, Metal | 23 | 8 | 2,381 ms |
| Kev-4B native Choice, Metal | 23 | 6 | 7,111 ms |
| Qwen3.5-4B JSON, Docker Linux/ARM64 CPU | 23 | 8 | 49,255 ms |

CPU Kev's three completed calls took **185–196 seconds each**. One further call
was interrupted and 60 remained unattempted. This is partial compatibility/latency
evidence, **not a CPU Kev accuracy result**; the original full plan and stop record
remain preserved.

Code time covers a classifier call on Darwin; model time covers the full HTTP
request and response validation. These are not matched service-level latencies.
Qwen emits one JSON object; Kev answers three Choice heads in one request.

Qwen fixes **12 code errors but loses seven code successes**. Kev fixes **11 but
loses six**. On Metal, reversed order scores Qwen 23/32 and Kev 22/32; raw
selections change on one and three cases respectively. CPU Qwen matches every
Metal primary selection but scores 22/32 reversed: it adds an invalid node on
R16. These are repeated views of the same cases.

## Four examples explain the tradeoff

| Question | Actual code | Qwen, Metal | Kev, Metal |
| --- | --- | --- | --- |
| “Compute the arithmetic mean of pressure” | Average of **`of`**: wrong field | Average of pressure ✓ | Average of pressure ✓ |
| “Show maintenance records for pump-42” | No specialized hints ✓ | Unneeded node makes tuple invalid | Unneeded node makes tuple invalid |
| “Maybe count devices or average pressure; I have not decided” | Count: premature choice | No specialized hints ✓ | Average pressure: premature choice |
| “Show connections from that device” | Path with unresolved node ✓ | Path with unresolved node ✓ | Invents `sensor-17` |

These are [R17, R03, R31 and R32](heldout.json). R32 is a correct partial
classification: the caller must resolve the node before execution. Empty code
output means no specialized hint, not proven understanding or a native abstention.

## How the existing code works

The driver imports the actual SemStreams `v1.0.0-beta.160` library:

- **Keyword rules:** regular expressions recognize phrases such as “how many,”
  “similar” and “connected to,” then extract search hints and literal arguments.
- **Optional BM25 examples:** only when rules produce no hints, compare
  word-weighted vectors against 20 labeled examples. Above a threshold, copy
  the closest example's options. This uses statistics, not learned embeddings,
  and **copies arguments rather than extracting new ones**. A wrong keyword match
  still bypasses BM25.

Neither BM25 threshold adds an accepted match on this set. The `0.9` threshold
and keyword comparator were selected on 18 development cases before testing.
The audited graph-query component does not enable BM25; these are explicitly
configured comparison arms. See the [driver and source pins](driver/README.md).

## What to do next

**Improve inexpensive rules/extraction first.** Several misses are ordinary
phrases (“number of,” “smallest,” “highest”) or the `of` extraction bug. Test fixes
on new cases; the failures shown here are now development material.
Rule matches are not authoritative facts: a fallback used only when rules miss
will retain their false matches on negation, quoted text and ambiguity.

Also strengthen the JSON baseline. Its frozen schema constrains keys/types/enums,
but application validation rejects incompatible combinations afterward. Thus
“invalid” here includes **schema-valid JSON with unusable arguments**. Conditional
schema constraints or caller-owned binding logic need a new comparison; no output
was silently repaired. Kev's separate heads can also disagree.

**Use a model when** varied semantic wording survives maintainable code and a
fresh test demonstrates useful corrections at acceptable errors and cost.
**Prefer code when** known patterns and authoritative fields suffice. This run
does not establish a reason to choose Kev over schema-constrained Qwen.

The authors inspected the source, and related phrases cross the development/test
split. Fixed node/metric choices, one trial and 32 authored cases do not establish
broad generalization. Runtime details, failures, tests and reproduction commands
are in [validation](../../docs/validation-query-routing.md); complete requests,
responses and source snapshots are in the [evidence archive](../../docs/evidence/20261006-query-routing/README.md).
No sibling or production classifier was changed.
