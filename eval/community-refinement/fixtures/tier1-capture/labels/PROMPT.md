# Edge-review labelling instructions (step 3 labels)

You are labelling reviewer packets for the community-refinement quality pilot
(`eval/community-refinement/README.md`). Each packet is one candidate pair of
entities from a mixed code/document graph. The same instructions apply to
annotator A (an LLM) and annotator B (a reviewing LLM session). Labels are
ground truth for the pilot, so follow the rules exactly.

## What you may read

Only the packet: the `request.state` field of one record in
`packets/<family>/pairs.jsonl`. Do not open the family's source files, the
hydration captures, other packets or anything else, and do not use what you
may already know about these repositories. If the packet does not show the
decisive evidence, the answer is `defer`, even when the full source would
settle it. This is the point of the exercise: the reviewer arms see exactly
this packet and nothing more.

## The question

Should this pair retain its semantic co-location hint for the stated
community objective?

The objective, printed at the top of every packet: put evidence about a shared
responsibility together without letting boilerplate, overloaded terms or
generic utility nodes join unrelated subjects. The hint is an edge that will
pull A and B into the same community at the granularity of one package, one
document or one responsibility within this family.

## Labels

- `keep`: the evidence in the packet shows that A and B concern one shared
  subject or responsibility at that granularity. Someone inspecting one would
  want the other alongside it.
- `suppress`: the evidence shows an incidental match that would mislead the
  grouping: boilerplate (license or file headers, imports, generic error
  handling, test scaffolding), an overloaded term (the same word in different
  senses), or a generic utility node (a logger, a config loader, a helper used
  everywhere) that would join unrelated subjects.
- `defer`: the packet's evidence is insufficient or conflicting. Use it when a
  side has no verbatim source and its header does not settle the question,
  when the shown part of a cut source does not reach the relevant content, or
  when the two readings are genuinely balanced.

Rules that override intuition:

- A similarity score, a neighbour rank, "same explicit-structure group" or an
  explicit link is context, never evidence. Do not label `keep` because the
  score is high or `suppress` because the sides are in different groups.
- Judge the pair, not the entities' importance. A central function paired with
  a passage about something else is `suppress`, not `keep`.
- A passage and the code it documents is the paradigm `keep`; a passage and
  code that merely shares vocabulary with it is the paradigm `suppress`.
- Two code symbols that implement one behaviour together (a type and the
  function that constructs it, a handler and the request it parses) are
  `keep`; two symbols that only share a pattern (both validate input, both
  return errors) are `suppress`.
- When in doubt between `keep` and `suppress`, and the doubt comes from what
  the packet does not show, label `defer`. When the doubt comes from a real
  judgement call on fully visible evidence, decide, and say why.

## Evidence and rationale

For every label give one to three `evidence` quotes copied verbatim from the
packet state (exact substrings, at most 300 characters each; whitespace may
differ) and a `rationale` of one or two sentences that says what the quotes
show. A `defer` must name what is missing. The validator rejects a quote that
is not found in the packet.

## Output

One JSON file per family, `labels/<family>.json` for annotator A and
`labels/<family>.review.json` for annotator B:

```json
{
  "provenance": "legacy SemStreams capture; not a SemEngine result",
  "step": "step 3 labels (eval/community-refinement/README.md)",
  "family": "<family id>",
  "split": "<development|held-out>",
  "annotator": {"role": "A", "kind": "llm", "model": "<model name>", "method": "packet-only"},
  "packets_sha256": "<the pairs.sha256 value from packets/<family>/index.json>",
  "labels": [
    {"pair_hash": "<64 hex>", "rank": 1, "label": "keep",
     "evidence": ["<verbatim quote>", "<verbatim quote>"],
     "rationale": "<one or two sentences>"}
  ]
}
```

Every pair in `pairs.jsonl` appears exactly once, in rank order. Validate with
`python3 label_sheet.py validate <family>` before you finish.
