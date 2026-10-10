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
| 2. Candidate selection | at most 32 effective candidates, with the fraction of all candidates they cover | [`select_candidates.py`](select_candidates.py), deterministic and offline; order fixed by ruling 6 | frozen for the twelve families in [`selections/`](selections/) (below) |
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
   `graph.enable_clustering` on, and `graph.entity_id_edges` set from the
   identity profile (below). SemSource passes no `semantic_edges` block, so
   the live partition is structural-only by construction.
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

`IDENTITY_PROFILE` selects the identity profile. The default, `explicit-only`,
turns both identity tiers off (the switches the legacy component already
exposes) so detection runs on explicit topology alone; it is the floor the
owner ruled on 2026-10-10 and its evidence directories carry the
`-explicit-only` suffix. `semantic-baseline` is the profile the protocol first
named; it collapses every family and is kept for the artifact record.

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

- **The identity-edge synthesis is a star, and it decides the partition.** The
  legacy provider sorts each entity's sibling and system-peer candidates
  lexically and keeps the first few, so every entity in a system votes for the
  same lexically-first entities. On all four families the top eight system-peer
  targets receive 92 to 95 percent of the system-peer edges, and on the docs
  family the top eight sibling targets receive 96 percent of the sibling
  edges. Identity edges carry 54 to 83 percent of the vote mass. The one-or-two
  community partitions above are that star, not the families' structure.
  SemStreams documents that system-peer synthesis "collapses a single-system
  graph" and exposes a switch (gh#461); the sibling tier does the same on a
  homogeneous family. See the explicit-only check below.
- **The 0.8 anchor is not degenerate on the code families (ruling 2 input).**
  On the osh corpus nearly every mutual pair scored above 0.85
  ([scale.md](../../scale.md)); on `semselect-service` only 24 of 125
  candidates do, and the three code-bearing families have medians 0.814 to
  0.829. The all-docs family is closer to osh (median 0.852, 300 of 547 at or
  above 0.85).
- **Against the semantic-baseline floor the priority order's first key is
  empty or saturated.** On `semselect-docs` there is nothing to cross; on
  `semengine-natsclient` and `semengine-message` the cross-partition
  candidates (43 and 40) already exceed the 32 ceiling; only
  `semselect-service` mixes the two. Against the explicit-only floor (below)
  the first key is never empty and exceeds the ceiling on three families.
- **Most candidates pair entities inside one file or doc.** 56 to 79 percent
  of review candidates share their immediate container, and most are
  same-type pairs; those duplicate containment and need no model. The pairs
  that can change a partition are the cross-file and cross-doc ones.
- **The stored community values are not byte-stable between cycles.** On an
  unchanged graph the legacy statistical summarizer rewrites each community's
  `keywords` in a different order between 30 s cycles (seven of the eight
  captures; `semengine-message` happened to be byte-stable once), so a
  raw-bytes comparison never settles. `cmd/structural` therefore settles on
  memberships and back-pointers and reports `community_values_byte_stable`;
  the frozen `partition.json` keeps the raw values as read.
- **The mutual-kNN replay is reproducible.** The explicit-only re-captures
  rebuilt every stack from scratch and produced byte-identical
  `mutual_pairs.jsonl` on all four families (same pairs, same similarities).

### Identity-synthesis check: explicit-only re-capture (2026-10-10)

The same four families, same runner, `IDENTITY_PROFILE=explicit-only`
(`include_siblings` and `include_system_peers` false, everything else equal).
Every check passed; 73 to 75 s each. Evidence directories carry the
`-explicit-only` suffix.

| Family | Level-0 communities, semantic baseline → explicit-only | Explicit-only community sizes | Cross-partition candidates, semantic baseline → explicit-only | Cross-partition similarity (explicit-only) |
| --- | --- | --- | --- | --- |
| `semselect-service` | 2 → 4 | 32 (README), 13 (`guard.go`), 7 (`cmd/` folder, repo), 4 (`cmd/semselect`) | 12 → 29 | median 0.788; 3 at or above 0.85 |
| `semselect-docs` | 1 → 18 | one community per doc, 9 to 36 chunks | 0 → 242 | median 0.853; 140 at or above 0.85 |
| `semengine-natsclient` | 2 → 8 | one per file plus the README: 116 (`client.go`), 37, 21, 16, 15, 12, 6, 3 | 43 → 140 | median 0.821; 28 at or above 0.85 |
| `semengine-message` | 2 → 7 | 34 (`message/` folder with its small files), 21, 18, 15, 9, 7, 7 | 40 → 83 | median 0.814; 7 at or above 0.85 |

With identity synthesis off, the legacy detector over explicit topology gives
a containment-shaped floor: one community per doc or per file, with small
files absorbed into their folder. That floor is sane and interpretable, and it
is also largely what the file tree already says; a comparison arm that reads
containment directly should be the stated structural comparator, not LPA.
Against it the semantic candidates have something to decide: 29 to 242
cross-container pairs per family, mostly same-type pairs across files or docs
(`function`/`method` across Go files, `chunk`/`chunk` across docs), which is
the co-location question the pilot was written to ask. The identity tiers
should not be part of any floor the pilot refines, and a SemEngine port should
not reproduce sorted-then-capped identity synthesis (see
[`docs/semengine-integration.md`](../../../../docs/semengine-integration.md)).
The owner ruled on 2026-10-10 (issue #5) that the fixtures freeze the
explicit-only floor, with containment named as the structural comparator, and
that the step-2 priority order stays as written; the semantic-baseline
captures are kept so the artifact stays on record.

Clustering ran with `min_community_size` 3 and `max_iterations` 100 (the
graph-clustering defaults SemSource does not override); LPA uses a fixed seed
and sorted inputs, so the partition is reproducible for a fixed graph.

## Captured: the eight held-out families (explicit-only floor, 2026-10-10)

Captured under the ruled floor, one after another, 74 to 75 s each, every
check passing, both explicit indexes agreeing, no similar query failing, and
entity counts equal to the tier-0 counts. Frozen before any held-out label
exists; nothing here is a label. Evidence directories carry the
`-explicit-only` suffix.

| Family | Entities | Explicit edges | Level-0 communities (largest) | Mutual pairs (explicit-dominated) | Candidates | Cross-partition | Candidate similarity median; at or above 0.85 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `semstreams-graph-clustering` | 175 | 624 | 6 (104) | 436 (46) | 390 | 111 | 0.834; 134 |
| `semstreams-rule` | 162 | 598 | 12 (34) | 401 (83) | 318 | 142 | 0.821; 87 |
| `semstreams-service` | 202 | 675 | 14 (58) | 506 (85) | 421 | 170 | 0.829; 126 |
| `semstreams-component` | 226 | 807 | 13 (65) | 541 (58) | 483 | 226 | 0.893; 337 |
| `semstreams-agentic-loop` | 207 | 1,072 | 3 (105, 98, 4) | 432 (22) | 410 | 71 | 0.836; 136 |
| `semsource-source-manifest` | 223 | 694 | 11 (45) | 564 (40) | 524 | 252 | 0.849; 261 |
| `semsource-cli` | 143 | 435 | 14 (27) | 373 (14) | 359 | 242 | 0.922; 308 |
| `semsource-ui` | 206 | 479 | 16 (30) | 472 (33) | 439 | 315 | 0.867; 305 |

Every held-out family has more cross-partition candidates than the 32
ceiling, so the step-2 order's second key (distance from 0.8) does the
ordering on all of them, and eight families times 32 reaches the protocol's
256 held-out review pairs. The similarity medians run higher than on the
development families on three of the eight (`semsource-cli` 0.922,
`semstreams-component` 0.893, `semsource-ui` 0.867), closer to the osh
picture; the development-tuned anchor is applied to them unchanged, as the
protocol requires. `semstreams-agentic-loop` is the densest explicit graph
(1,072 edges) and the coarsest partition (two large communities); its 71
cross-partition candidates still exceed the ceiling.

## Step 2: frozen candidate selections (2026-10-10)

`select_candidates.py --evidence <step-1 dir> --family <id> --out selections/<id>.json`
reads one family's `summary.json`, `structural/entities.jsonl` and
`mutualknn/mutual_pairs.jsonl`, drops explicit-dominated pairs and any pair
with an endpoint outside the frozen partition (the protocol's
semantic-no-effect class, empty under the explicit-only floor because every
remaining candidate adds a 0.9 edge where the vote had none), orders the rest
by crossing the structural-only partition, then distance of similarity from
0.8 (the weaker of the two directed similarities; they were equal on every
pair), then the sha256 of the sorted entity IDs, and freezes the first 32 with
the input hashes. It refuses evidence captured under another identity
profile. Offline tests: `tests/test_select_candidates.py`.
[`selections/index.json`](selections/index.json) rolls the twelve up: 384
pairs selected (128 development, 256 held-out) of 4,756 candidates.

| Family | Split | Candidates | Selected | Coverage | Cross-partition selected / available | Selected similarity (min, median, max) | Same-type among selected |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `semselect-service` | development | 125 | 32 | 25.6% | 29 / 29 | 0.751, 0.793, 1.000 | 12 |
| `semselect-docs` | development | 547 | 32 | 5.9% | 32 / 242 | 0.776, 0.819, 0.828 | 30 |
| `semengine-natsclient` | development | 527 | 32 | 6.1% | 32 / 140 | 0.792, 0.802, 0.810 | 12 |
| `semengine-message` | development | 213 | 32 | 15.0% | 32 / 83 | 0.784, 0.808, 0.818 | 17 |
| `semstreams-graph-clustering` | held-out | 390 | 32 | 8.2% | 32 / 111 | 0.782, 0.806, 0.817 | 23 |
| `semstreams-rule` | held-out | 318 | 32 | 10.1% | 32 / 142 | 0.791, 0.803, 0.808 | 15 |
| `semstreams-service` | held-out | 421 | 32 | 7.6% | 32 / 170 | 0.790, 0.802, 0.810 | 24 |
| `semstreams-component` | held-out | 483 | 32 | 6.6% | 32 / 226 | 0.783, 0.799, 0.817 | 17 |
| `semstreams-agentic-loop` | held-out | 410 | 32 | 7.8% | 32 / 71 | 0.771, 0.816, 0.831 | 31 |
| `semsource-source-manifest` | held-out | 524 | 32 | 6.1% | 32 / 252 | 0.794, 0.799, 0.806 | 17 |
| `semsource-cli` | held-out | 359 | 32 | 8.9% | 32 / 242 | 0.760, 0.812, 0.869 | 27 |
| `semsource-ui` | held-out | 439 | 32 | 7.3% | 32 / 315 | 0.792, 0.813, 0.822 | 14 |

Only `semselect-service` has fewer cross-partition candidates than the
ceiling, so its 32 span the whole similarity range and include three
same-community pairs; everywhere else the 32 are cross-partition pairs within
about 0.03 of the anchor. The selections are frozen: they are not refilled
from labels or outcomes, and the 32-pair ceiling covers 6 to 26 percent of
each family's candidates, as the protocol anticipated.

## Order

Development families first (all four done), then the eight held-out
families (all eight done under the explicit-only floor), then the two
generalization families. Held-out captures were frozen before any held-out
label exists.

## Stop-point

Steps 1 and 2 are done for the twelve sem* families: captures under the
ruled explicit-only floor (the development families also under the artifact
profile for the record) and frozen 32-pair selections. Next: the step-3
reviewer packet serializer (one packet per selected pair, 8,192-byte state
bound, per-entity bundle variant, both tokenizers verified), then the label,
constraint and query sheets. The generalization families still need an export
step before the runner accepts them.
