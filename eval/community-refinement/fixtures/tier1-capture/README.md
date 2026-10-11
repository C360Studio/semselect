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
| 3. Reviewer packets | one packet per candidate, 8,192-byte state bound, per-entity bundle variant, both tokenizers verified | [`make_packets.py`](make_packets.py) over the hydration captures, [`verify_packets.py`](verify_packets.py) on both pinned runtimes | frozen for the twelve families in [`packets/`](packets/) (below) |
| 4. Co-membership constraints | 20 per family, ten positive and ten negative | [`constraint_sheet.py`](constraint_sheet.py) catalogue, validator and index; annotators as in ruling 7, from full source | done for the twelve families in [`constraints/`](constraints/) (below): 237 of 240 agreed, 119 positive and 118 negative stand |
| 5. Retrieval queries | 6 per family with gold evidence sets, four complementary and two precise | [`query_sheet.py`](query_sheet.py) validator and index; annotators as in ruling 7, from full source and passage text | done for the twelve families in [`queries/`](queries/) (below): 43 of 72 agreed, 28 complementary and 15 precise stand, 28 of them held-out |

Labels (`keep`, `suppress`, `defer`) follow step 3. Ruling 7 (owner,
2026-10-10) replaces the annotator arrangement of ruling 3: no person labels.
Annotator A is an LLM (Claude Code subagents of this session, packet-only,
one per family), annotator B is a Codex session reviewing blind by the same
instructions, and a pair the two do not agree on stays `defer`. The record
discloses that every label is model-written.

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

## Step 3: frozen reviewer packets (2026-10-10)

The step-1 captures held entity IDs, topology and vectors but not the
passages a reviewer must read, so the runner gained a hydration step
(`legacy-capture/cmd/hydrate`): every entity state verbatim plus the bodies the
SemSource producers offloaded to the `CONTENT` object store (code symbols and
document passages; repo, folder, file and document entities carry no body).
The twelve families were re-captured with it under the same explicit-only
floor. Every re-capture reproduced its frozen step-1 inputs byte for byte:
`entities.jsonl`, `mutual_pairs.jsonl` and the partition hash all equal the
values recorded in `selections/<family>.json`, which is also what
`make_packets.py` refuses to proceed without. The partition is therefore
deterministic across stack rebuilds, as the mutual pairs were already known
to be.

`make_packets.py --selection selections/<id>.json --evidence <re-capture dir>
--out packets/<id>` writes, per family:

- `pairs.jsonl`, one request per selected pair, the per-pair baseline
  contract: a state of at most 8,192 UTF-8 bytes holding the community
  objective, the candidate's similarity, both neighbour ranks, any explicit
  link and whether the pair crosses the explicit-structure partition, then one
  card per entity (type, title, path and lines, package or section, content
  or file hash, signature, doc comment, up to six explicit links per direction
  with the omitted count, contained children for a container) and its verbatim
  source. When both bodies do not fit, the remaining budget is split between
  them, longest-first for leftovers, and each is cut on a line end; the state
  says how many bytes were omitted after which line, and the packet record
  carries the full reference (entity ID, path, lines, hash, body key, body
  sha256, body bytes, included and omitted bytes). The single Choice question
  is the protocol's, with the three labels as its criteria.
- `bundles.jsonl`, the per-entity variant: one entity's card and source as the
  state, each selected neighbour as its own question carrying that neighbour's
  head line and an excerpt of at most 512 bytes within the 1,024-byte
  instruction limit, split into requests of at most four questions with the
  split recorded. The questions in a bundle share one state, which is the
  shape where a runtime's state reuse can show.
- `index.json`: input hashes, the prompt text, state byte statistics,
  truncation counts and the sha256 of both files.

Packets quote the frozen evidence only; no label, community ID beyond the
partition-crossing flag, or query answer enters them. The Qwen JSON message
shape (system: instructions plus categories plus the JSON-only clause; user:
the state; constrained `choice` schema) mirrors `scripts/evaluate.py`'s JSON
baseline and is the pilot's prompt version 1; development labels may still
tune it, held-out labels never.

`verify_packets.py --packets packets/* --out <roll-up> --logs <dir>` launches
the two pinned llama.cpp runtimes on Metal (4,096-token slot, thinking off for
Qwen), renders each packet the way its arm would send it, counts tokens with
the runtime's own `/tokenize` and writes `packets/<id>/tokens.json`. No
completion is requested. The Kev render uses the GGUF's SystemOne choice
template, checked against the pinned renderer first; a bundle is rendered once
per question over the shared state. Reserve is 128 output tokens for the JSON
reviewer and 1 for the decision head. A packet that does not fit would be
recorded as out of profile, not truncated.

Two findings from the re-captures. First, every family's entity set and
partition reproduced exactly, but on two families the embedding replay did
not: `semstreams-graph-clustering` (mutual pairs 436 in the frozen capture,
438 in the re-capture; one `var` entity's similarity to its neighbours moved by
up to 0.045) and `semsource-ui` (472 against 467; one `const` entity moved by
up to 0.101, dragging twelve entities' lists). In each case a single entity's
vector changed while its state, body and neighbours' vectors did not. That is
consistent with the legacy embedder racing the concurrent body offload
(`processor/ast-source/bodystore.go` puts bodies with bounded concurrency;
`graph-embedding` fetches an offloaded body through its `StorageRef` at embed
time and counts a failed fetch as content excluded), which would embed the
identity text alone for the entity whose body was not yet stored. The
embedder's content metrics that would confirm it were not captured, so the
mechanism is a hypothesis; the measurement is not. The selections stay frozen
on the first capture: packets take similarity and ranks from that capture of
record, the serializer refuses a hydration capture whose entities or partition
differ, and it records the drift in `index.json` (`mutual_pairs_drift`). The
affected pair is rank 9 of `semstreams-graph-clustering`, which keeps its
frozen similarity. For the port this is a second reproducibility requirement
alongside the identity synthesis: an embedding must not depend on the timing
of the body offload.

Second, the hydration itself: 2,179 entities across the twelve families, of
which 2,009 carry a body handle (code symbols and passages) resolved to 1,999
distinct bodies, every handle resolved (`semsource-ui` has ten entities whose
bodies are byte-identical to another's). The 170 entities without a body are
containers, which is what a packet quoting one of them says.

<!-- step3-table -->
| Family | Split | Pair packets | State bytes (min, median, max) | Sources truncated | Sources without body | Qwen JSON prompt tokens (min, median, max) | Kev prompt tokens (min, median, max) | Bundle requests / entities / split entities | Bundle Kev prompt tokens (min, median, max) | All fit |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `semselect-service` | development | 32 | 989, 2,238, 5,849 | 0 | 16 | 413, 785, 1958 | 382, 754, 1927 | 28 / 25 / 3 | 293, 492, 1308 | yes |
| `semselect-docs` | development | 32 | 1,222, 3,457, 4,768 | 0 | 2 | 483, 982, 1833 | 452, 951, 1802 | 53 / 52 / 1 | 277, 678, 1255 | yes |
| `semengine-natsclient` | development | 32 | 1,170, 2,170, 7,674 | 0 | 6 | 502, 674, 2027 | 471, 643, 1996 | 47 / 47 / 0 | 320, 479, 1873 | yes |
| `semengine-message` | development | 32 | 1,528, 2,656, 5,412 | 0 | 8 | 569, 813, 1561 | 538, 782, 1530 | 41 / 41 / 0 | 338, 508, 1165 | yes |
| `semstreams-graph-clustering` | held-out | 32 | 1,362, 2,960, 8,021 | 2 | 2 | 511, 911, 2205 | 480, 880, 2174 | 50 / 50 / 0 | 339, 594, 2135 | yes |
| `semstreams-rule` | held-out | 32 | 1,414, 3,472, 5,982 | 0 | 13 | 527, 982, 1745 | 496, 951, 1714 | 47 / 47 / 0 | 360, 627, 1446 | yes |
| `semstreams-service` | held-out | 32 | 1,427, 2,431, 5,096 | 0 | 4 | 539, 793, 1501 | 508, 762, 1470 | 47 / 47 / 0 | 363, 530, 995 | yes |
| `semstreams-component` | held-out | 32 | 1,056, 1,931, 5,814 | 0 | 8 | 430, 651, 1588 | 399, 620, 1557 | 37 / 37 / 0 | 271, 455, 1361 | yes |
| `semstreams-agentic-loop` | held-out | 32 | 1,897, 4,790, 8,028 | 8 | 0 | 681, 1487, 2303 | 650, 1456, 2272 | 52 / 52 / 0 | 450, 758, 2423 | yes |
| `semsource-source-manifest` | held-out | 32 | 1,367, 2,649, 5,220 | 0 | 4 | 513, 839, 1403 | 482, 808, 1372 | 54 / 54 / 0 | 315, 546, 1170 | yes |
| `semsource-cli` | held-out | 32 | 980, 2,144, 4,874 | 0 | 3 | 430, 765, 1627 | 399, 734, 1596 | 35 / 33 / 2 | 281, 568, 1189 | yes |
| `semsource-ui` | held-out | 32 | 1,225, 2,214, 8,028 | 2 | 1 | 477, 728, 2292 | 446, 697, 2261 | 41 / 41 / 0 | 311, 481, 2316 | yes |

Totals: 384 pair packets, 12 truncated sources, 67 sources without a body (container entities), 532 bundle requests carrying 768 questions, 6 entities split across more than one request. Largest prompt: 2,303 tokens on the Qwen JSON arm and 2,272 on Kev for a pair, 2,423 on Kev for a bundle question, against a 4,096-token slot; every packet fits on both runtimes (`packets/tokens-rollup.json`, all_fit true).
<!-- /step3-table -->

## Step 3 labels: annotators A and B (2026-10-10)

[`labels/PROMPT.md`](labels/PROMPT.md) is the labelling contract for both
annotators: the packet state is the only admissible evidence, the three
labels carry the protocol's definitions with worked distinctions (a passage
and the code it documents is `keep`; shared vocabulary alone is `suppress`;
a missing or cut source that hides the decisive part is `defer`), scores,
ranks and partition flags are context and never evidence, and every label
cites one to three verbatim quotes from the packet plus a rationale.
[`label_sheet.py`](label_sheet.py) validates a sheet (every packet labelled
once in rank order, labels in the set, the packets' sha256, every quote found
verbatim in its packet with whitespace collapsed) and builds
[`labels/index.json`](labels/index.json): per-family counts for A, and once
annotator B's `labels/<family>.review.json` exists, agreement, the A/B
confusion and the final labels (A's where they agree, `defer` otherwise) in
`labels/<family>.final.json`. [`labels/REVIEW.md`](labels/REVIEW.md) tells
the Codex session what to do; it labels blind before reading A's sheets.

Annotator A ran as twelve Claude Code subagents, one per family, each given
only `PROMPT.md`, the family's `index.json` and `pairs.jsonl`, and told to
read nothing else and to use no prior knowledge of the repositories. The
model is the session's default for subagents (Claude Fable 5.1 session, owner
ruling of 2026-10-09 that this track may run on Fable). Each sheet passed the
validator before it was kept. Annotator B was a Codex session run by the
owner following `labels/REVIEW.md`: it labelled every family blind from the
packets by the same contract, wrote `labels/<family>.review.json`, validated
and built the index; its sheets cite two quotes per label on average and
carry no `review_note`, so no label of B's changed after reading A. Where the
two disagree the final label is `defer` (ruling 7).

<!-- labels-table -->
| Family | Split | A keep/suppress/defer | B keep/suppress/defer | Agreed | Disagreed | Final keep/suppress/defer |
| --- | --- | --- | --- | --- | --- | --- |
| `semselect-service` | development | 7/12/13 | 8/10/14 | 30 | 2 | 7/10/15 |
| `semselect-docs` | development | 16/14/2 | 18/13/1 | 30 | 2 | 16/13/3 |
| `semengine-natsclient` | development | 16/12/4 | 16/12/4 | 32 | 0 | 16/12/4 |
| `semengine-message` | development | 23/6/3 | 22/6/4 | 31 | 1 | 22/6/4 |
| `semstreams-graph-clustering` | held-out | 11/16/5 | 18/13/1 | 23 | 9 | 11/12/9 |
| `semstreams-rule` | held-out | 23/9/0 | 13/16/3 | 19 | 13 | 13/6/13 |
| `semstreams-service` | held-out | 6/25/1 | 7/24/1 | 31 | 1 | 6/24/2 |
| `semstreams-component` | held-out | 18/13/1 | 11/17/4 | 18 | 14 | 9/8/15 |
| `semstreams-agentic-loop` | held-out | 22/10/0 | 26/6/0 | 28 | 4 | 22/6/4 |
| `semsource-source-manifest` | held-out | 18/11/3 | 20/9/3 | 24 | 8 | 16/8/8 |
| `semsource-cli` | held-out | 10/21/1 | 15/15/2 | 26 | 6 | 10/15/7 |
| `semsource-ui` | held-out | 12/15/5 | 13/15/4 | 29 | 3 | 12/14/6 |
| **Total** | | **182/164/38** | **187/156/41** | **321** | **63** | **160/134/90** |

Annotator B (Codex, model string `GPT-6`, blind, no label changed after reading A's sheets) agreed with A on 321 of 384 pairs (123 of 128 development, 198 of 256 held-out). The 63 disagreements by A/B label: keep/suppress 20, suppress/keep 18, suppress/defer 12, defer/keep 9, keep/defer 2, defer/suppress 2. Final labels over 384 pairs: 160 keep, 134 suppress, 90 defer (64 of the defers held-out). Per-pair final labels are in `labels/<family>.final.json`; `labels/index.json` carries the counts and the per-family confusion.
<!-- /labels-table -->

What the annotators reported, across families: the single-package families
(`semstreams-rule`, `semstreams-agentic-loop`, `semsource-source-manifest`,
`semstreams-graph-clustering`) make "one responsibility" rather than "one
package" the operative grain, and most keep/suppress calls there turn on
where a responsibility ends; sibling-shape pairs (port-kind structs,
request/response structs against wire structs, lifecycle and getter
boilerplate across services) were labelled `suppress` as shared pattern; a
container side without a body went `defer` unless its header (doc comment,
contained-symbol list) settled the subject, which drives the thirteen defers
on `semselect-service`; cut sources were rarely decisive (one `defer` on
`semstreams-graph-clustering` where the shown part of `Start` stops before
the relevant code). Two packet defects surfaced that the serializer did not
cause: the legacy TypeScript producer stores a one-line body for arrow
function constants and some props destructures, so a "complete" source can
fail to show the named entity (three defers on `semsource-ui`), and a
multi-line Go comment cannot be quoted as one span because the `//` markers
break the whitespace rule, so evidence came from doc fields or single lines.
Each annotator flagged its judgement calls in the rationales. A check of the
twelve annotators' tool calls shows reads of `PROMPT.md`, the family's
`index.json` and `pairs.jsonl`, and the files they wrote, and no command
naming a source file, an evidence directory or another family.

## Step 4: co-membership constraints (2026-10-10)

Constraints are independent graph labels: pairs of entities that are useful
to inspect together (positive) or harmful to conflate under the objective
(negative), twenty per family, drawn from the full source and never from the
partition, the neighbour lists or the edge labels. Each arm's communities are
later scored by how often positives share a community and negatives do not.
[`constraints/PROMPT.md`](constraints/PROMPT.md) is the contract for both
annotators: definitions with worked shapes, the containment rule (no file
with its own symbol, folder with its file, document with its passage, or
the repo entity, since those hold in every arm), at least six positives
across different files, every negative naming the superficial likeness that
makes it a trap, and evidence as `path:lines` into the workspace.
[`constraint_sheet.py`](constraint_sheet.py) writes each family's entity
catalogue from the hydration capture (ID, type, path, lines, title, section,
signature; no community, neighbour or label field), recomputes the exported
workspace's `workspace_sha256` before a sheet is validated, checks a sheet
(exact IDs and counts, catalogue membership, no duplicate pair, the
containment rule against the frozen explicit edges, evidence paths in the
family's file list with line ranges inside the file), and builds
[`constraints/index.json`](constraints/index.json) with after-the-fact flags
computed from the frozen captures and never shown to annotator A: whether a
pair is in the step-3 review set, a mutual-kNN candidate, joined by an
explicit edge, or in one file. [`constraints/REVIEW.md`](constraints/REVIEW.md)
tells the Codex session what to do: a constraint survives only when B agrees
on the source; disputed ones are dropped and the actual counts recorded.

Annotator A ran as twelve Claude Code subagents, one per family, each given
`PROMPT.md`, the family's catalogue and its exported workspace at
`/tmp/semselect-families/<family>/` (hash verified against the capture), and
told to read nothing else.

<!-- constraints-table -->
| Family | Split | Proposed pos/neg | Outside review set | Outside mutual-kNN | With explicit edge | Same file | Agreed pos/neg | Dropped |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `semselect-service` | development | 10/10 | 15 | 12 | 1 | 4 | 10/10 | 0 |
| `semselect-docs` | development | 10/10 | 18 | 14 | 0 | 0 | 9/10 | 1 |
| `semengine-natsclient` | development | 10/10 | 19 | 12 | 1 | 4 | 10/10 | 0 |
| `semengine-message` | development | 10/10 | 18 | 11 | 3 | 3 | 10/9 | 1 |
| `semstreams-graph-clustering` | held-out | 10/10 | 19 | 14 | 0 | 4 | 10/9 | 1 |
| `semstreams-rule` | held-out | 10/10 | 19 | 11 | 3 | 0 | 10/10 | 0 |
| `semstreams-service` | held-out | 10/10 | 19 | 14 | 2 | 2 | 10/10 | 0 |
| `semstreams-component` | held-out | 10/10 | 20 | 15 | 3 | 0 | 10/10 | 0 |
| `semstreams-agentic-loop` | held-out | 10/10 | 19 | 15 | 1 | 9 | 10/10 | 0 |
| `semsource-source-manifest` | held-out | 10/10 | 18 | 12 | 2 | 0 | 10/10 | 0 |
| `semsource-cli` | held-out | 10/10 | 18 | 15 | 6 | 0 | 10/10 | 0 |
| `semsource-ui` | held-out | 10/10 | 20 | 10 | 2 | 1 | 10/10 | 0 |
| **Total** | | **120/120** | **222** | **155** | **24** | **27** | **119/118** | **3** |

Of the 240 proposed pairs, 222 lie outside the step-3 review set and 155 outside the mutual-kNN candidate set, 24 are joined by some explicit edge (never a containment edge, which the validator forbids) and 27 sit in one file. After annotator B, 119 positives and 118 negatives stand and 3 were dropped; per-pair constraints with their flags are in `constraints/<family>.final.json`.
<!-- /constraints-table -->

What the annotators reported: positives were almost all cross-file by
construction (the rule asks for six; most families gave nine or ten) and
spread over each family's responsibilities (ingest, status, lifecycle,
validation, wiring, and the document passages that describe them); negatives
lean on overloaded terms (`port`, `index`, `level`, `schema`, `register`,
`timeout`, `config`), copied scaffolds (`Validate`, `Stop`, GET handlers,
ticker loops, print-and-marshal commands) and sibling types that the source
itself contrasts. The closest calls, flagged in the rationales, are pairs
joined only outside the family (a generator or a fact table that is not in
the workspace) and sibling helpers with identical shape. Two limitations of
the inputs: the split is not derivable from the allowed files, so several
annotators guessed it and the validator corrected them; and the catalogue
gives a document passage its index and section but no line range, so
annotators mapped passages to README lines by section size. For annotator
B, `constraint_sheet.py passages` now writes each passage's verbatim text
beside the catalogue (`<family>.passages.jsonl`, five families have
passages). A check of the twelve annotators' tool calls shows reads of
`PROMPT.md`, the catalogue, the exported workspace and the sheet they
wrote, and no command naming the packets, labels, selections or evidence
directories.

## Order

Development families first (all four done), then the eight held-out
families (all eight done under the explicit-only floor), then the two
generalization families. Held-out captures were frozen before any held-out
label exists.

Annotator B ran as one Codex session (model string `GPT-6`) following
[`constraints/REVIEW.md`](constraints/REVIEW.md): the same contract, the
same workspaces (hashes recomputed by the validator), A's sheets, and
`constraints/<family>.passages.jsonl` with every document passage's verbatim
text, which A did not have. B agreed with 237 of the 240 constraints and
disagreed with three, each dropped without adjudication (ruling 7):
`semengine-message` neg-01, where `BaseMessage.Validate` calls
`m.payload.Validate()` on the other side, so the pair splits one validation
workflow rather than conflating two; `semselect-docs` pos-03, where the
cited laptop-guide passage entity is a 47-byte command block and the text A
quoted lies in a neighbouring passage; and `semstreams-graph-clustering`
neg-06, where the cited README passage goes on to say the algorithm reads
the OUTGOING/INCOMING indexes, the very responsibility of the method A set
against it. All three verdicts were checked against the source and the
passage text; two of the three trace to A inferring passage boundaries
from section sizes, the input limitation the passages file now removes. B's
rationales name the symbols and behaviours involved and cite no line numbers.
Every review sheet validated with its `sheet_sha256` matching A's file as
committed; A's sheets and the catalogues are byte-identical before and after
the review, and re-running `constraint_sheet.py index` reproduces B's
`<family>.final.json` and `index.json` byte for byte. Whether the Codex
session read anything it was told not to is not verifiable from this
repository; the independence rests on its instructions and on the flags
above, which were computed afterwards.

## Step 5: retrieval queries (2026-10-11)

A query is a question a developer working in the family would ask, with its
known answer and its gold supporting sets: the entities whose text, read
together, establishes the answer. Later each comparison arm retrieves
evidence for the question under the protocol's fixed 4,096-token budget,
and the query succeeds when the budgeted evidence contains every member of
at least one gold set; no answer generator is involved. Six per family,
four complementary (the answer needs at least two entities from different
files or documents, so community expansion has something to add) and two
precise (one entity establishes the whole answer and a named lookalike
elsewhere would be the wrong source). [`queries/PROMPT.md`](queries/PROMPT.md)
is the contract for both annotators: every known supporting set recorded,
primary first and minimal; gold members restricted to entities that carry
text (never the repo, a folder, a file or a document); questions free of
entity IDs and file paths; `path:lines` evidence citing every file of the
primary set; the six primary sets distinct and drawn from at least three
files or documents. [`query_sheet.py`](query_sheet.py) reuses the step-4
family reader to validate both annotators' sheets and builds
[`queries/index.json`](queries/index.json) with after-the-fact flags
computed from the frozen captures and never shown to annotator A: how many
level-0 communities of the frozen structural partition each gold set
spans, and whether its members are joined by an explicit edge, a mutual-kNN
pair or a step-4 constraint. [`queries/REVIEW.md`](queries/REVIEW.md) tells
the Codex session what to do: agree or disagree on the source, record any
supporting set A missed (kept beside the gold sets, never gold, since
nobody adjudicates); a query survives only when both agree.

Annotator A ran as twelve Claude Code subagents, one per family, each given
`PROMPT.md`, the family's catalogue, its passages file where it has
documents (the step-4 lesson) and its exported workspace at
`/tmp/semselect-families/<family>/` (hash verified against the capture), and
told to read nothing else; the split was given in the instruction since it
is not derivable from those inputs.

<!-- queries-table -->
| Family | Split | Proposed compl./precise | Alternative sets | Primary set spans communities | Some set inside one community | Primary set has explicit edge | Primary set has mutual pair | Primary set has constraint pair | Agreed compl./precise | Dropped | B added sets |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `semselect-service` | development | 4/2 | 1 | 4 | 0 | 0 | 2 | 2 | 2/2 | 2 | 0 |
| `semselect-docs` | development | 4/2 | 2 | 4 | 0 | 0 | 1 | 0 | 2/1 | 3 | 0 |
| `semengine-natsclient` | development | 4/2 | 1 | 4 | 1 | 1 | 0 | 0 | 3/0 | 3 | 0 |
| `semengine-message` | development | 4/2 | 0 | 4 | 0 | 1 | 4 | 3 | 3/2 | 1 | 0 |
| `semstreams-graph-clustering` | held-out | 4/2 | 0 | 3 | 1 | 0 | 2 | 1 | 2/0 | 4 | 0 |
| `semstreams-rule` | held-out | 4/2 | 0 | 3 | 1 | 0 | 1 | 2 | 2/1 | 3 | 0 |
| `semstreams-service` | held-out | 4/2 | 0 | 4 | 0 | 2 | 2 | 1 | 2/2 | 2 | 0 |
| `semstreams-component` | held-out | 4/2 | 1 | 3 | 1 | 3 | 1 | 1 | 2/2 | 2 | 0 |
| `semstreams-agentic-loop` | held-out | 4/2 | 0 | 4 | 0 | 0 | 2 | 1 | 3/2 | 1 | 0 |
| `semsource-source-manifest` | held-out | 4/2 | 2 | 4 | 0 | 1 | 0 | 0 | 2/1 | 3 | 0 |
| `semsource-cli` | held-out | 4/2 | 0 | 3 | 1 | 2 | 0 | 1 | 2/1 | 3 | 0 |
| `semsource-ui` | held-out | 4/2 | 5 | 4 | 0 | 2 | 2 | 1 | 3/1 | 2 | 0 |
| **Total** | | **48/24** | **12** | **44** | **5** | **12** | **17** | **13** | **28/15** | **29** | **0** |

Of the 72 proposed queries (48 complementary, 24 precise) with 12 alternative supporting sets, the primary gold set of 44 of the 48 complementary queries spans more than one level-0 community of the frozen structural partition, 5 have some gold set inside one community, 12 have an explicit edge among the primary members, 17 a mutual-kNN pair and 13 a step-4 constraint pair. After annotator B, 28 complementary and 15 precise queries stand, 29 were dropped and B recorded 0 additional supporting sets on agreed queries (kept beside the gold sets, not gold; sets B added on dropped queries stay in the review sheets); per-query gold sets with their flags are in `queries/<family>.final.json`.
<!-- /queries-table -->

What the annotators reported, and two rule changes made while they ran.
Every family's six questions validated; the closest calls were
complementary questions whose first entity nearly answers alone (a doc
comment or inline comment restating the rule), which the annotators handled
by asking for the part only the second entity shows. Two of my rules were
wrong for the data and were relaxed before any sheet was frozen: the
`semstreams-agentic-loop` family has text-bearing entities in only two
files, so the "three files" spread rule now bends to every text-bearing
file when a family has fewer; and the two-file requirement had applied to
every supporting set, which stopped `semengine-natsclient` and
`semselect-service` from recording same-file sets that genuinely establish
an answer, so it now binds the primary set only and every annotator was
asked afterwards to add the same-file alternatives they knew of (an arm
that retrieves true evidence must not be scored as a miss). Findings for
the port, all from the legacy producer: catalogue line ranges for Go
symbols exclude the doc comment above the declaration, so a fact stated
only there is not in the entity's text and the annotators grounded gold
sets in code and in-range comments; Svelte catalogue lines sit one below
the file's lines; eleven of `semselect-docs`'s passages split mid-table or
mid-list; the Go `doc.go` files yield a file entity without text and no
passages, so package documentation is unreachable as evidence.

Annotator B ran as one Codex session (model string `GPT-6`) following
[`queries/REVIEW.md`](queries/REVIEW.md) with the same inputs as A plus A's
sheets. Every review sheet validated with its `sheet_sha256` matching A's
committed file; A's sheets, the catalogues and the passages are
byte-identical before and after the review; re-running
`query_sheet.py index` reproduces B's `<family>.final.json` and
`index.json` byte for byte. B agreed with 43 of the 72 queries and
disagreed with 29 (20 complementary, 9 precise; 9 development, 20
held-out), each dropped without adjudication (ruling 7). The agreement
rate is far below steps 3 and 4, and the reason is visible in the
rationales: B applied the contract's "every gold set establishes the
answer in full" against the catalogue bodies as captured, while A's
answers (mean 453 characters against a 600 cap) routinely carried claims
beyond what those bodies literally say. In my reading of the 29: about
seven rest on a fact stated in a Go doc comment above the declaration,
which the legacy producer leaves outside the entity's body; about
fourteen on a claim that needs one more entity (a helper, a constant, the
caller that supplies a literal) the set omits; four fault an alternative
set that establishes less than the primary; three or four call part of
the answer wrong (a 503 that is a 502 on one timing; a running guard the
answer skipped; an overstated fallback); one cites a passage that ends
before the definition it is asked for, with the passage text available,
so that one is A's error. B recorded seven additional sets, all on
disagreed queries and all supersets of A's primary set; they stay in the
review sheets and nowhere else, since a dropped query has no final entry.
Three of B's claims were checked against the source and the passage text
and hold. Whether the Codex session read anything it was told not to is
not verifiable from this repository. The lesson for the contract, not
applied retroactively: ask A for the answer the gold text literally
establishes, nothing more, and tell both annotators that bodies exclude
doc comments. The held-out count that stands, 28, is below the protocol's
48-question target; the protocol records actual counts and treats the
targets as ceilings, so this is recorded, not padded. A second round for
the 29 dropped slots would need the owner's ruling and fresh questions,
written and reviewed blind as before, never revisions of the dropped ones.

## Stop-point

Steps 1 to 5 are done for the twelve sem* families: captures under the ruled
explicit-only floor (the development families also under the artifact profile
for the record), frozen 32-pair selections, hydration re-captures and frozen
reviewer packets verified on both pinned runtimes, final edge-review labels
(ruling 7: model-written, packet-only, validated, disagreement `defer`): 160
keep, 134 suppress, 90 defer, 321 of 384 agreed; final co-membership
constraints (full-source, validated, disputed ones dropped): 119 positive and
118 negative, 237 of 240 agreed, in
[`constraints/<family>.final.json`](constraints/); and final retrieval
queries (full-source and passage text, validated, disputed ones dropped): 28
complementary and 15 precise, 43 of 72 agreed, 28 held-out against the
48-question target, in [`queries/<family>.final.json`](queries/). Open for
the owner: accept the 43 as the frozen query set, or rule a second blind
round of fresh questions for the 29 dropped slots under a tightened answer
contract. The generalization families still need an export step before
the runner accepts them.
