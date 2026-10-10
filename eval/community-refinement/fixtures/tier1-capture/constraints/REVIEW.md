# Review instructions for annotator B (a Codex session), step 4

Under ruling 7 (issue #5, 2026-10-10) an LLM writes the co-membership
constraints and a Codex session reviews them. A constraint survives only if
both agree; there is no `defer` for constraints, a disputed one is dropped and
the actual counts are recorded.

1. Read `PROMPT.md` in this directory: the two polarities, the containment
   rule, the evidence form. The same evidence rules bind you.
2. For each family under `../packets/` there is a sheet
   `constraints/<family>.json` by annotator A and a catalogue
   `constraints/<family>.catalogue.jsonl`. The family's full source is the
   exported workspace at `/tmp/semselect-families/<family>/`; if it is
   missing, run from the repository root
   `python3 -I eval/community-refinement/fixtures/legacy-count/families.py prepare --input eval/community-refinement/fixtures/legacy-count/families.input.json --ws-root /tmp/semselect-families --config-out /tmp/semselect-tier0.unused.json --manifest-out /tmp/semselect-prepare-manifest.json`
   to re-export every family at its pinned commit.
3. For each of A's twenty constraints, read the cited source and decide
   `agree` or `disagree` on the source alone: agree when the pair is clearly
   positive (or clearly negative) by `PROMPT.md`'s definitions and the cited
   lines show it; disagree when the polarity is wrong, the pair is a trivial
   containment, the evidence does not show the relationship, or you are not
   confident. Say why in one or two sentences.
4. Write `constraints/<family>.review.json`:

```json
{
  "provenance": "legacy SemStreams capture; not a SemEngine result",
  "step": "step 4 co-membership constraints (eval/community-refinement/README.md)",
  "family": "<family id>",
  "split": "<development|held-out>",
  "annotator": {"role": "B", "kind": "llm", "model": "<the Codex model>", "method": "full-source"},
  "sheet_sha256": "<sha256 of constraints/<family>.json as reviewed>",
  "verdicts": [
    {"id": "pos-01", "verdict": "agree", "rationale": "<one or two sentences>"},
    {"id": "neg-01", "verdict": "disagree", "rationale": "<why>"}
  ]
}
```

   Every one of the twenty IDs gets a verdict.
5. Run `python3 constraint_sheet.py validate <family>` for each family, then
   `python3 constraint_sheet.py index`. The index writes
   `constraints/<family>.final.json` (the agreed constraints) and
   `constraints/index.json` (counts per family and polarity, how many pairs
   lie outside the step-3 review set and outside the mutual-kNN candidate
   set, and how many share an explicit structural edge; these are computed
   after the fact from the frozen captures and were never shown to A).

Do not read the review packets, the step-3 labels, the selections or the
evidence directories while reviewing; the constraints are independent of
them by design. Do not edit A's sheets or the catalogues.
