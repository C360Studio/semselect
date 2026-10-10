# Co-membership constraint instructions (step 4)

You are writing co-membership constraints for one family of the
community-refinement quality pilot (`eval/community-refinement/README.md`,
step 4). A constraint is a pair of entities with a polarity:

- **positive**: useful to inspect together. Someone working on one of them
  needs the other alongside it, because the two carry evidence about one
  shared responsibility: a passage and the behaviour it documents, two
  symbols that split one workflow across files, a configuration type and the
  code that validates or consumes it, a request and the handler that parses
  it.
- **negative**: harmful to conflate under the objective. The two look alike
  to a shallow reader (shared words, the same shape, the same boilerplate,
  both using a common utility) but belong to different responsibilities, so a
  tool that grouped them would mislead: two unrelated `Config` types, two
  handlers of different subjects sharing scaffolding, a passage that uses a
  term in a different sense from the code that defines it.

The objective these serve: put evidence about a shared responsibility
together without letting boilerplate, overloaded terms or generic utility
nodes join unrelated subjects. Later, each comparison arm's communities are
scored by how often positives land in one community and negatives do not.

## What you may read

- The family's full source: the exported workspace directory you are given,
  the exact files at the pinned commit. Read as much of it as you need.
- The entity catalogue you are given (`<family>.catalogue.jsonl`): one line
  per entity with its ID, type, path, lines, title and section. Constraints
  name entities by these IDs.

Do not read, and do not use what you may remember of: the review packets,
the step-3 labels, the selections, the evidence directories (partitions,
communities, neighbour lists, similarities) or anything else under the
repository. The constraints must be independent graph labels derived from
the source alone, never from the current partition or the edge labels.

## Rules

- Exactly ten positive and ten negative constraints.
- Both sides are catalogue entity IDs; a pair appears once; the two sides
  differ.
- No trivial containment pairs: not a file with a symbol inside it, not a
  folder with its file, not a document with its own passage, not the repo
  with anything. Those hold in every arm and measure nothing.
- At least six of the ten positives join entities in different files or
  different documents.
- Every negative names the superficial likeness that makes it a trap (the
  shared term, shape or scaffold) and why the responsibilities differ.
- Prefer pairs a careful reader of the source would be confident about. If
  you are not confident, pick another pair; there is no `defer` here.
- Spread the pairs across the family's responsibilities rather than
  drawing all of them from one file.

## Evidence and rationale

For each constraint give one to three `evidence` entries of the form
`path:start-end` or `path:line` (paths relative to the workspace root, as in
the catalogue) pointing at the lines that show the relationship, and a
`rationale` of one to three sentences. For a positive say what shared
responsibility the two carry and what one side needs from the other. For a
negative say what makes them look alike and what separates them.

## Output

One JSON file, `constraints/<family>.json`:

```json
{
  "provenance": "legacy SemStreams capture; not a SemEngine result",
  "step": "step 4 co-membership constraints (eval/community-refinement/README.md)",
  "family": "<family id>",
  "split": "<development|held-out>",
  "annotator": {"role": "A", "kind": "llm", "model": "<model name>", "method": "full-source"},
  "catalogue_sha256": "<sha256 of the catalogue file you were given>",
  "constraints": [
    {"id": "pos-01", "polarity": "positive", "a": "<entity id>", "b": "<entity id>",
     "evidence": ["internal/guard/guard.go:95-99", "README.md:40-44"],
     "rationale": "<one to three sentences>"},
    {"id": "neg-01", "polarity": "negative", "a": "<entity id>", "b": "<entity id>",
     "evidence": ["..."], "rationale": "<one to three sentences>"}
  ]
}
```

IDs run `pos-01` to `pos-10` and `neg-01` to `neg-10`. Validate with
`python3 constraint_sheet.py validate <family>` before you finish; it checks
the counts, the IDs, the catalogue membership, the evidence paths and the
containment rule, and it computes the sha256 of the catalogue for you to
compare.
