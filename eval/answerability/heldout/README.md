# Can the classifier spot a missing part of a real answer?

**Verdict: Kev earns further quality testing; neither model is a reliable
answerability gate yet.** With the teaching prompt unchanged, Kev made better
aggregate decisions than Qwen JSON on this small source pilot. Both still allowed
passages that answered only part of the question.

## Three cases explain the result

These examples use the predefined primary view: trial 1, normal ordering.

- **Code resolves an explicit fact — H01.** The caller asks for
  `retry.DefaultConfig().MaxAttempts`. Its supplied Go function sets `3`, and
  an explicit extracted fact lets code answer without either model.
- **Kev catches an omission — H16.** The question asks which title property
  replaces `source.doc.summary` and what happens when the body store is unavailable.
  The passages explain the startup failure but omit the replacement property.
  Qwen allows the incomplete evidence; Kev correctly defers.
- **Both miss an omission — H24.** A source-retention ADR describes two cleanup
  obligations. The question also asks for an exact waiting period, which the
  passage never specifies. Both models allow it. Knowing part of an answer is
  not enough under this test's rule.

There is a counterexample to Kev's advantage: **H12** omits `DropNewest` behavior.
Qwen correctly defers; Kev allows it. The improvement is aggregate, not universal.
See the [24 frozen cases and label explanations](cases.md) and
[complete questions/passages](cases.json).

## The measured comparison

Apple M3 Pro, native Metal, 2026-10-05. **24 cases: 12 answerable, 12 unsupported.**
The predefined primary result is trial 1 with normal ordering. Shared code resolves
one allow and one defer; each model receives the other 22 cases.

| Approach after shared code checks | Unsupported allowed / 12 | Answerable allowed / 12 | Answerable deferred / 12 | Median decision time on 22 unresolved cases |
| --- | ---: | ---: | ---: | ---: |
| No added semantic gate | 11 | 12 | 0 | <1 ms |
| Qwen3.5-4B JSON | 7 | 11 | 1 | 940 ms |
| Kev-4B through semselect | 5 | 12 | 0 | 985 ms |

All responses were valid; no transport errors or cleanup failures occurred.
The second trial retained every label. Reversing evidence and candidate order
changed three Qwen decisions and one Kev decision in each trial. Qwen's primary
16/24 correct became 15/24 with reversal; Kev's 19/24 became 18/24. These remain
24 cases grouped into six related source families, not 96 independent examples.

Caching was enabled in both configurations, but Qwen reused prefixes in 82/88
measured model calls and Kev reprocessed every prompt. Model training, prompts
and serving paths also differ. Timings include preparation and HTTP/validation,
exclude startup/warmup, and do not isolate a decision-head efficiency advantage.

## What we learned—and what remains open

These are authored questions over intact pinned SemStreams/SemSource excerpts,
with deliberate omissions and evidence ablations. They cover new source families
relative to the teaching set. Labels were independently reviewed and
[hash-frozen before inference](freeze.json); prompts and thresholds were not tuned
against the results. This is not live retrieval, production traffic, or proof that
the material was absent from model training.

The control applies explicit fact checks, then allows unresolved cases. Its 11
unsupported allows do not mean fusion or an existing generator produced 11 bad
answers. Likewise, a gate's unsupported allow is not itself a generated falsehood.

The [answer-path audit](../../../docs/answering-path.md) found that the existing
SemStreams generator already asks for missing information to be acknowledged.
It consumes community summaries, a different representation from these passages.
The next experiment must preserve that prompt and representation, including useful
partial answers. A current full-stack test also needs a verified SemSource build
and isolated indexed data; a prompt replay alone cannot establish that result.

**Use these findings to justify testing an evidence check where partial answers
are costly. Keep exact facts in code. Do not treat either model's `allow` as proof
that the evidence is complete.**

Reproduce with `task answerability:source:validate` and
`task answerability:source:metal` (cached pinned models/native build required).
[Detailed checks and evidence](../../../docs/validation-answerability-source.md)
preserve every outcome. CPU/Docker and downstream answer-quality results for this
workload remain unmeasured; CUDA work stays behind those steps.
