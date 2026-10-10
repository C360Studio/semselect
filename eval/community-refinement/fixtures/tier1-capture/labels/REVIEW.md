# Review instructions for annotator B (a Codex session)

The owner has ruled (issue #5, 2026-10-10) that no person labels these pairs:
annotator A is an LLM, annotator B is a Codex session, and a disagreement
that the two do not resolve stays `defer`. This file tells the Codex session
what to do.

1. Read `PROMPT.md` in this directory. It is the whole contract: packet-only
   evidence, the three labels, verbatim quotes.
2. For each family directory under `../packets/`, open `pairs.jsonl`. For each
   record, read `request.state` and nothing else, and decide your own label
   before looking at annotator A's sheet. Blind labelling is what makes the
   disagreement count meaningful.
3. Write `labels/<family>.review.json` in the format given in `PROMPT.md`, with
   `"annotator": {"role": "B", "kind": "llm", "model": "<the Codex model>",
   "method": "packet-only"}` and the family's `pairs.sha256` from
   `../packets/<family>/index.json` as `packets_sha256`.
4. Run `python3 label_sheet.py validate <family>` for each family, then
   `python3 label_sheet.py index`. The index computes agreement, the confusion
   matrix between A and B, and the final labels: A's label where A and B
   agree, `defer` where they do not. It writes `labels/<family>.final.json`
   and `labels/index.json`.
5. Only after your sheets are written, read A's sheet. If you find a label of
   A's that you think is wrong on the packet's evidence and your own label
   already differs, nothing more is needed: the pair becomes `defer`. If you
   want to change one of your own labels after reading A's reasoning, do it
   only on packet evidence, say so in a `review_note` field on that label, and
   re-run the index. Record the number of changed labels in your report.

Do not edit A's sheets, the packets or the selections. Held-out labels never
tune prompts, graph parameters or thresholds; they are only ever compared.
