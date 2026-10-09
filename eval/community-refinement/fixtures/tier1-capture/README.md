# Tier-1 family capture (freeze steps 1 to 5)

**Legacy SemStreams capture; not a SemEngine result.** This directory turns the
measured family list in [`../families.json`](../families.json) into frozen
pilot fixtures, following the protocol's
[corpus, labels and evidence freeze](../../README.md#corpus-labels-and-evidence-freeze)
and the owner rulings recorded on
[issue #5](https://github.com/C360Studio/semselect/issues/5). Every output is
a legacy SemStreams capture until the SemEngine port exists and confirms the
provider chain.

## What each family gets

| Step | Output | How | Status |
| --- | --- | --- | --- |
| 1. Neighbours and partition | directed neighbour results (`k=8`, threshold 0.75 recorded), mutual pairs, explicit and identity memberships, effective weights, the structural-only partition, embedding identity (semembed image digest and model), source and config hashes | one legacy tier-1 stack **per family** ([`run-family.sh`](run-family.sh)), so the similarity index holds that family alone; [`cmd/structural`](../../legacy-capture/README.md#structural-freeze-cmdstructural) freezes the structural side, [`cmd/mutualknn`](../../legacy-capture/README.md) the candidates | runner built; `semselect-service` captured (below) |
| 2. Candidate selection | at most 32 effective candidates, with the fraction of all candidates they cover | deterministic selection, offline; the "distance from 0.8" key is reconsidered after the first family's distribution is seen (ruling 2) | not started; first-family distribution is below |
| 3. Reviewer packets | one packet per candidate, 8,192-byte state bound, per-entity bundle variant, both tokenizers verified | serializer, offline | not started |
| 4. Co-membership constraints | 20 per family, ten positive and ten negative | people, from full source evidence | not started |
| 5. Retrieval queries | 6 per family with gold evidence sets | people | not started |

Labels (`keep`, `suppress`, `defer`) follow step 3: annotator A is the owner;
annotator B may be a Codex session; the owner resolves disagreements before
inference, and the record discloses which labels are model-written (ruling 3).

## Step 1 runner

```bash
eval/community-refinement/fixtures/tier1-capture/run-family.sh <family-id>
```

One family, one stack, always torn down with `down -v`:

1. [`../legacy-count/families.py`](../legacy-count/families.py) `prepare`
   exports every repository at its pinned commit from the sibling checkouts
   (`git archive`, committed files only) into `/tmp/semselect-families/`.
   [`family_config.py`](family_config.py) refuses to continue unless the
   family's commit, file list and `workspace_sha256` equal `families.json`,
   then writes the SemSource config for that family alone: `namespace`
   `semselect`, the AST and docs sources at `/workspace/<family-id>` (same
   languages as the tier-0 count, so entity IDs match), the HTTP embedder and
   model registry of SemSource's shipped `configs/mvp.json`,
   `graph.enable_clustering` on, and `graph.entity_id_edges` set to the
   semantic profile's structural baseline (sibling 0.7 capped at 5, system
   peer 0.2 capped at 8). SemSource passes no `semantic_edges` block, so the
   live partition is structural-only by construction: the semantic profile's
   structural weights retained, semantic influence off, as freeze step 1 asks.
2. SemSource's own `docker-compose.yml` plus [`compose.tier1.yml`](compose.tier1.yml)
   (mounts only that family at `/workspace/<family-id>`, keeps semembed) runs
   as Compose project `semselect-tier1-capture` on host ports 18080, 14222 and
   18222. The SemSource checkout must sit at the commit `families.json` pins
   and be clean, and SemSource's `go.mod` must link the pinned SemStreams
   version, or the runner refuses (`ALLOW_SEMSOURCE_DRIFT=1` overrides).
3. It polls `/source-manifest/status` until `phase`, `index.ready` and
   `embedding.ready` are all ready, records the status, then runs
   `cmd/structural`, which waits until `COMMUNITY_INDEX` level 0 covers every
   `ENTITY_STATES` entity and its memberships and back-pointers are unchanged
   across a 35 s settle interval (longer than the 30 s detection interval),
   then reads the explicit topology, embedding identity and identity edges.
4. `cmd/mutualknn` replays the legacy mutual-kNN candidate generation, the
   status is read again, and the run fails unless entity count and embedding
   revisions equal the status that gated the capture and both tools saw the
   same entity count.
5. Evidence lands in `docs/evidence/<UTC>-legacy-tier1-capture-<family-id>/`:
   `summary.json` (roll-up), `structural/`, `mutualknn/`, the config, the
   family entry and file hashes, `run.json` (images, model, machine, status),
   `milestones.json`, SemSource and Compose logs (gzip) and `SHA256SUMS`.

Generalization families (`nats-go-micro`, `commons-csv`) are not in
`families.input.json`, so `prepare` does not export them and `family_config.py`
refuses them; they need their own export step before capture.

## Captured: `semselect-service` (development)

Evidence: `docs/evidence/20261009T183243Z-legacy-tier1-capture-semselect-service/`
(SemSource `4093d3c`, SemStreams `v1.0.0-beta.160`, semembed
`ghcr.io/c360studio/semembed:latest@sha256:7972174f…` running
`Snowflake/snowflake-arctic-embed-s`, Docker 29.8.2 linux/aarch64 on Apple
Silicon; 73 s from `compose up` to capture done).

| Measure | Value |
| --- | --- |
| Entities (`ENTITY_STATES`) | 56, equal to the tier-0 count; 56 embedded |
| Explicit edges (`OUTGOING_INDEX`) | 103 (80 undirected pairs), `INCOMING_INDEX` agrees exactly |
| LPA voting edges, per voter | 817: 146 explicit, 223 sibling, 448 system peer |
| Structural-only partition, level 0 | 2 communities: one seeded by `README.md` (32 members: the doc and its 31 chunks) and one by `internal/guard/guard.go` (24 members: the repo, folders, files and Go symbols); no singletons |
| Directed pairs at threshold 0.75 (`k=8`) | 351 over 56 queries, 0 failures |
| Mutual pairs | 136; 11 explicit-dominated; 125 review candidates; 32 cross-type |
| Candidates crossing the level-0 partition | 12 of 125 (mostly doc chunks against the repo and folder hubs) |
| Candidate similarity (min of the two directions) | min 0.751, median 0.814, max 1.000; 78 at or above 0.8, 24 at or above 0.85, 5 at or above 0.9 |

Two findings for the protocol:

- **The 0.8 anchor is not degenerate on this family (ruling 2 input).** On
  the osh corpus nearly every mutual pair scored above 0.85
  ([scale.md](../../scale.md)); here only 24 of 125 candidates do, and the
  histogram peaks in `[0.80, 0.85)`. Ordering by partition crossing first and
  then distance from 0.8 fills the 32-candidate ceiling with all 12
  cross-partition pairs plus the 20 same-community pairs closest to 0.8
  (similarities 0.791 to 0.809). The owner's ruling on the key is still open;
  this is the distribution it asked to see.
- **The stored community values are not byte-stable between cycles.** On an
  unchanged graph the legacy statistical summarizer rewrites each community's
  `keywords` in a different order every 30 s cycle, so a raw-bytes comparison
  never settles. `cmd/structural` therefore settles on memberships and
  back-pointers and reports `community_values_byte_stable: false`; the frozen
  `partition.json` keeps the raw values as read. Memberships were identical
  across the two covered reads 35 s apart; a probe during the aborted first
  attempt (whose stack ran for 7 minutes with the raw-bytes check) also found
  level-0 memberships identical across cycles while only `keywords` moved.

Clustering ran with `min_community_size` 3 and `max_iterations` 100 (the
graph-clustering defaults SemSource does not override); LPA uses a fixed seed
and sorted inputs, so the partition is reproducible for a fixed graph.

## Order

Development families first (`semselect-service` done, then
`semselect-docs`, `semengine-natsclient`, `semengine-message`), then the
eight held-out families, then the two generalization families. Held-out
captures are frozen before any held-out label is read.

## Stop-point

Step 1 is runnable and has produced one development family. Next: run the
three remaining development families with the same runner, then write the
step-2 candidate selector against `structural/` and `mutualknn/` (reading
`voting_edges.jsonl` for partition crossing and `mutual_pairs.jsonl` for
similarity and explicit dominance) once the owner rules on the priority key.
