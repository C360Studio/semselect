# Synthesis acquisition evidence — 2026-10-05

This package preserves the setup and acquisition boundary for a downstream
synthesis experiment. It contains no formal gate comparison or answer grading.
SemSource `4093d3ce421371f4a99d7168e372552899bf6795` was built offline for
Linux/ARM64 against SemStreams `v1.0.0-beta.160`. The six public documents were
already pinned in `eval/answerability/heldout/source`; their hashes and licenses
are retained here. The corpus excludes authored questions and gold labels.

Attempt 1 stopped before any query: semembed lacked its model cache and could
not download on the internal Docker network; that network also left declared
host ports unbound. All 13 planned cases are recorded as blocked before capture.

Attempt 2 used a separate owned project and fresh NATS state, a dedicated bridge
with actual loopback-only port bindings, and a verified read-only Snowflake
cache. Its five model/tokenizer files total 133,807,972 bytes at revision
`e596f507467533e48a2e17c007f0e1dacc837b33`; `HF_ENDPOINT=http://127.0.0.1:9`
makes cache misses fail locally. No model weight is included in this package.
Readiness reported six documents plus 86 chunks (92 entities), with structural
and embedding indexes caught up. Actual community records were captured before
and after the queries.

All 13 distinct planned queries ran once, in order. Twelve responses contained
three full community summaries each. S06 returned a temporal-strategy
no-responder error; it remains a pipeline failure in the 13-case denominator.
The placeholder S06 row in `attempt2/inputs.jsonl` must not be scored as a
successful empty-evidence query. `attempt2/status.jsonl` and the raw response are
part of the input contract. The other 12 rows preserve the exact response
`community_summaries` and `count` as `summaries` and `total_entities`.

Acquisition includes real upstream generation. Its answers and timings are
preserved as setup evidence, not formal model-comparison outcomes. At acquisition
cleanup, source/NATS exited 0; semembed reached the bounded stop's final kill
(137, not OOM). Only the healthy owned seminstruct generator remained for replay.
These files are snapshots, not a claim about the current runtime state.

## Byte preservation and scope

`MANIFEST.json` distinguishes original byte copies, explicit projections and
authored explanations. Every artifact has a SHA-256 and size. Copied originals
are also byte-compared against their ignored local source during verification.
The manifest itself is pinned by `MANIFEST.sha256`; do not edit frozen files in
place. Create a separate record for corrections or later attempts.

Files named `provenance-project.json`, `compose-project.json`,
`capture-project.json` or `metadata-project.json` are **projections**, never raw
original records. Their manifest entries preserve original local hashes and
state each selection/transformation. Compose host paths use `${SEMSELECT_ROOT}`
in place of the original absolute semselect checkout. Raw requests/responses,
owned runtime inspection, KV snapshots, logs and readiness records are copied
without modification. Runtime/image environment values were audited against the
public configuration allowlist; the only credential-shaped variable is an
empty `MODEL_API_KEY`.

`omissions.json` documents exclusions. Unrelated Docker volume/container
inventory and the broad preflight inventory stay ignored locally. Binaries,
model weights, source archive and NATS database are excluded; their applicable
hashes/configuration remain in provenance. The model card's published benchmark
claims are attribution, not results measured by this experiment.

```sh
python3 docs/evidence/20261005-synthesis-acquisition/verify.py
# Also verify source equality when the ignored acquisition workspace is present:
python3 docs/evidence/20261005-synthesis-acquisition/verify.py --local
```
