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
| 1. Neighbours and partition | directed neighbour results (`k=8`, threshold 0.75 recorded), mutual pairs, explicit and identity memberships, effective weights, the structural-only partition, embedding identity (semembed image digest and model), source and config hashes | one legacy tier-1 stack **per family** ([`run-family.sh`](run-family.sh)), so the similarity index holds that family alone; [`cmd/structural`](../../legacy-capture/README.md#structural-freeze-cmdstructural) freezes the structural side, [`cmd/mutualknn`](../../legacy-capture/README.md) the candidates | runner built; four development families captured (below) |
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

## Captured: the four development families

All four ran through the runner on 2026-10-09 (SemSource `4093d3c`,
SemStreams `v1.0.0-beta.160`, semembed
`ghcr.io/c360studio/semembed:latest@sha256:7972174f…` running
`Snowflake/snowflake-arctic-embed-s`, Docker 29.8.2 linux/aarch64 on Apple
Silicon; 73 to 75 s from `compose up` to capture done each). Every structural
check passed, both explicit indexes agreed, and no similar query failed.
Evidence directories under `docs/evidence/`:

| Family | Evidence | Entities | Explicit edges | Voting edges (explicit / sibling / peer) | Level-0 partition | Mutual pairs | Candidates | Cross-partition | Candidate similarity (min of both directions) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `semselect-service` | `20261009T183243Z-legacy-tier1-capture-semselect-service` | 56 | 103 | 817 (146 / 223 / 448) | 2: `README.md` 32, `guard.go` 24 | 136 (11 explicit-dominated) | 125 | 12 | min 0.751, median 0.814; 24 at or above 0.85 |
| `semselect-docs` | `20261009T233233Z-legacy-tier1-capture-semselect-docs` | 242 | 224 (all `belongs`) | 3,594 (448 / 3,002 / 144) | **1 community of 242** | 605 (58) | 547 | 0 | min 0.770, median 0.852; 300 at or above 0.85 |
| `semengine-natsclient` | `20261009T233354Z-legacy-tier1-capture-semengine-natsclient` | 226 | 805 | 3,918 (1,009 / 1,245 / 1,664) | 2: `client.go` 205, `README.md` 21 | 577 (50) | 527 | 43 | min 0.754, median 0.829; 160 at or above 0.85 |
| `semengine-message` | `20261009T233514Z-legacy-tier1-capture-semengine-message` | 111 | 341 | 1,852 (422 / 548 / 882) | 2: `base_message.go` 74, folder `message` 37 | 256 (43) | 213 | 40 | min 0.751, median 0.822; 51 at or above 0.85 |

Entity counts equal the tier-0 counts in `families.json` and every entity was
embedded. "Candidates" are mutual pairs that are not explicit-dominated;
"cross-partition" counts candidates whose endpoints sit in different level-0
communities.

Findings for the protocol:

- **The 0.8 anchor is not degenerate on the code families (ruling 2 input).**
  On the osh corpus nearly every mutual pair scored above 0.85
  ([scale.md](../../scale.md)); on `semselect-service` only 24 of 125
  candidates do, and the three code-bearing families have medians 0.814 to
  0.829. The all-docs family is closer to osh (median 0.852, 300 of 547 at or
  above 0.85).
- **The first key of the priority order is empty or saturated.** Ordering by
  partition crossing first, then distance from 0.8: on `semselect-docs` there
  is nothing to cross, so the 32 selected are the same-community pairs nearest
  0.8 (0.785 to 0.817); on `semengine-natsclient` and `semengine-message` the
  cross-partition candidates (43 and 40) already exceed the ceiling, so the
  second key only orders within them (selected similarities 0.774 to 0.849
  and 0.762 to 0.834); only `semselect-service` mixes the two (12 cross plus
  20 same-community pairs at 0.791 to 0.809). The 32-candidate ceiling covers
  6 to 26 percent of each family's candidates. The owner's ruling on the key
  is still open; this is the distribution it asked to see.
- **An all-docs family has no structural partition to cross.** `semselect-docs`
  carries only `belongs` containment edges, so with the semantic profile's
  structural weights every chunk ends up in one community seeded by the
  lexically first doc. Review candidates there can only be ordered by
  similarity; co-membership constraints (step 4) will have to carry the
  structural signal for that family.
- **The stored community values are not byte-stable between cycles.** On an
  unchanged graph the legacy statistical summarizer rewrites each community's
  `keywords` in a different order between 30 s cycles on three of the four
  families (`semengine-message` happened to be byte-stable), so a raw-bytes
  comparison never settles. `cmd/structural` therefore settles on memberships
  and back-pointers and reports `community_values_byte_stable`; the frozen
  `partition.json` keeps the raw values as read. Memberships were identical
  across the two covered reads 35 s apart on every family; a probe during the
  aborted first `semselect-service` attempt (whose stack ran for 7 minutes with
  the raw-bytes check) also found level-0 memberships identical across cycles
  while only `keywords` moved.

Clustering ran with `min_community_size` 3 and `max_iterations` 100 (the
graph-clustering defaults SemSource does not override); LPA uses a fixed seed
and sorted inputs, so the partition is reproducible for a fixed graph.

## Order

Development families first (all four done), then the eight held-out
families, then the two generalization families. Held-out captures are frozen
before any held-out label is read.

## Stop-point

Step 1 is runnable and has produced the four development families. Next:
the eight held-out families with the same runner (frozen before any held-out
label is read), then the step-2 candidate selector against `structural/` and
`mutualknn/` (reading `voting_edges.jsonl` for partition crossing and
`mutual_pairs.jsonl` for similarity and explicit dominance) once the owner
rules on the priority key. The generalization families still need an export
step before the runner accepts them.
