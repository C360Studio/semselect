# Retrieval query instructions (step 5)

You are writing known-answer evidence-retrieval queries for one family of
the community-refinement quality pilot (`eval/community-refinement/README.md`,
step 5). A query is a question a developer working in this family would
ask, together with its answer and its gold supporting sets: the entities
whose text, read together, establishes that answer. Later each comparison
arm retrieves evidence for the question under a fixed token budget, and the
query succeeds when the budgeted evidence contains every member of at least
one gold set. No answer generator is involved; only the retrieved evidence
is graded.

Six queries per family, of two kinds:

- **complementary** (`q-01` to `q-04`): the answer needs evidence from at
  least two entities in different files or documents, and no single entity
  establishes it. Shapes: a limit declared in one place and what happens
  when it is exceeded handled in another; a configuration field and the code
  that reads it; a passage that states a rule and the function that enforces
  it with its exact error; a request type and the handler that interprets it.
- **precise** (`q-05` and `q-06`): one entity's text establishes the whole
  answer, and the question is specific enough that a lookalike elsewhere (a
  sibling with the same name, a passage that uses the term loosely, an older
  variant, a similar helper for another subject) would be the wrong source.
  Name the lookalike in the rationale.

## What you may read

- The family's full source: the exported workspace directory you are given,
  the exact files at the pinned commit. Read as much of it as you need.
- The entity catalogue you are given (`<family>.catalogue.jsonl`): one line
  per entity with its ID, type, path, lines, title, section and whether it
  carries text (`has_body`). Gold sets name entities by these IDs.
- The passages file you are given when the family has documents
  (`<family>.passages.jsonl`): each passage entity's verbatim text, so you
  can see exactly which lines a passage covers before you put it in a gold
  set. A passage is only the text shown there, never the whole section.

Do not read, and do not use what you may remember of: the review packets,
the step-3 labels, the step-4 constraints, the selections, the evidence
directories (partitions, communities, neighbour lists, similarities) or
anything else under the repository. The queries must come from the source
alone, never from the current partition or from earlier labels.

## Rules

- Exactly six queries: `q-01` to `q-04` complementary, `q-05` and `q-06`
  precise.
- The question is one sentence a developer would ask, at most 300
  characters. It may name symbols, commands and concepts. It must not
  contain an entity ID or a file path: a question that only needs the file
  name is a lookup, not a retrieval question.
- The answer is the known answer, concrete and checkable against the
  source, at most 600 characters. Models never see it; the graders do.
- Gold set members are catalogue entity IDs whose rows have `has_body`
  true. Containers (repo, folder, file, document) carry no text and cannot
  be members.
- Record every supporting set you know of, the primary set first:
  alternatives such as a passage that restates what a function shows. A
  complementary set has two to six members from at least two different
  files or documents. A precise set has exactly one member. Sets are
  minimal: remove any member and the set no longer establishes the answer,
  so no set may contain another.
- The six primary sets differ from one another and together draw on at
  least three different files or documents.
- Prefer questions whose answer a careful reader of the source would be
  confident about. If you are not confident that a gold set establishes the
  answer in full, choose another question; there is no `defer` here.
- Spread the questions across the family's responsibilities rather than
  drawing all of them from one file.

## Evidence and rationale

For each query give one to eight `evidence` entries of the form
`path:start-end` or `path:line` (paths relative to the workspace root, as in
the catalogue) pointing at the lines that establish the answer; every file
of the primary gold set must be cited. The `rationale` (one to three
sentences, at most 600 characters) says, for a complementary query, what
each member contributes and why no single member suffices; for a precise
query, what makes this entity the only right source and what the lookalike
is.

## Output

One JSON file, `queries/<family>.json`:

```json
{
  "provenance": "legacy SemStreams capture; not a SemEngine result",
  "step": "step 5 known-answer retrieval queries (eval/community-refinement/README.md)",
  "family": "<family id>",
  "split": "<development|held-out>",
  "annotator": {"role": "A", "kind": "llm", "model": "<model name>", "method": "full-source"},
  "catalogue_sha256": "<sha256 of the catalogue file you were given>",
  "passages_sha256": "<sha256 of the passages file you were given, or null when the family has none>",
  "queries": [
    {"id": "q-01", "kind": "complementary",
     "question": "<one sentence>",
     "answer": "<the known answer>",
     "gold_sets": [["<entity id>", "<entity id>"], ["<entity id>", "<entity id>", "<entity id>"]],
     "evidence": ["internal/guard/guard.go:95-99", "README.md:40-44"],
     "rationale": "<one to three sentences>"},
    {"id": "q-05", "kind": "precise",
     "question": "<one sentence>",
     "answer": "<the known answer>",
     "gold_sets": [["<entity id>"]],
     "evidence": ["..."],
     "rationale": "<what makes this the only right source; the lookalike>"}
  ]
}
```

Validate with `python3 query_sheet.py validate <family>` before you finish;
it checks the counts and kinds, the catalogue membership and bodies, the
set rules, the evidence paths and lines, and it prints the catalogue and
passages hashes for you to compare.
