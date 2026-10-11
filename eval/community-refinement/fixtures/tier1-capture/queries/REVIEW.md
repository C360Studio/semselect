# Review instructions for annotator B (a Codex session), step 5

Under ruling 7 (issue #5, 2026-10-10) an LLM writes the retrieval queries
and a Codex session reviews them. A query survives only if both agree;
there is no `defer`, a disputed query is dropped and the actual counts are
recorded. Nobody adjudicates.

1. Read `PROMPT.md` in this directory: the two kinds, the gold-set rules,
   the evidence form. The same rules bind you.
2. For each family under `../packets/` there is a sheet
   `queries/<family>.json` by annotator A, the catalogue
   `../constraints/<family>.catalogue.jsonl` and, for the five families
   with documents, `../constraints/<family>.passages.jsonl` with every
   passage entity's verbatim text (A had this file too). The family's full
   source is the exported workspace at `/tmp/semselect-families/<family>/`;
   if it is missing, run from the repository root
   `python3 -I eval/community-refinement/fixtures/legacy-count/families.py prepare --input eval/community-refinement/fixtures/legacy-count/families.input.json --ws-root /tmp/semselect-families --config-out /tmp/semselect-tier0.unused.json --manifest-out /tmp/semselect-prepare-manifest.json`
   to re-export every family at its pinned commit.
3. For each of A's six queries, read the cited source and the text of every
   gold member and decide `agree` or `disagree` on the source alone. Agree
   when all of these hold: the question is one a developer would ask and is
   answerable from this family's source; the answer is correct; every gold
   set establishes the answer in full; a complementary set genuinely needs
   at least two of its members (no single entity establishes the answer by
   itself); a precise set's one entity establishes the whole answer and the
   lookalike A names is real. Disagree when any of these fails, when a set
   is not minimal, or when you are not confident. Say why in one or two
   sentences.
   If you know another supporting set that A did not list, put it under
   `additional_sets` (same rules as A's sets: entity IDs with bodies, two to
   six members from at least two files for a complementary query, exactly
   one for a precise query). It is recorded, not adjudicated, and does not
   become gold.
4. Write `queries/<family>.review.json`:

```json
{
  "provenance": "legacy SemStreams capture; not a SemEngine result",
  "step": "step 5 known-answer retrieval queries (eval/community-refinement/README.md)",
  "family": "<family id>",
  "split": "<development|held-out>",
  "annotator": {"role": "B", "kind": "llm", "model": "<the Codex model>", "method": "full-source"},
  "sheet_sha256": "<sha256 of queries/<family>.json as reviewed>",
  "verdicts": [
    {"id": "q-01", "verdict": "agree", "rationale": "<one or two sentences>",
     "additional_sets": [["<entity id>", "<entity id>"]]},
    {"id": "q-05", "verdict": "disagree", "rationale": "<why>"}
  ]
}
```

   Every one of the six IDs gets a verdict; `additional_sets` is optional.
5. Run `python3 query_sheet.py validate <family>` for each family, then
   `python3 query_sheet.py index`. The index writes
   `queries/<family>.final.json` (the agreed queries with A's gold sets;
   B's additional sets are kept beside them, marked not gold) and
   `queries/index.json` (counts per family and kind, and after-the-fact
   flags computed from the frozen captures: how many level-0 communities
   each gold set spans, explicit edges and mutual-kNN pairs among its
   members, whether a gold pair is a step-4 constraint; these were never
   shown to A).

Do not read the review packets, the step-3 labels, the step-4 constraint
sheets, the selections or the evidence directories while reviewing; the
queries are independent of them by design. Do not edit A's sheets, the
catalogues or the passages.
