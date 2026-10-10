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
| 4. Co-membership constraints | 20 per family, ten positive and ten negative | from full source evidence; annotators as in ruling 7 | not started |
| 5. Retrieval queries | 6 per family with gold evidence sets | annotators as in ruling 7 | not started |

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

## Order

Development families first (all four done), then the eight held-out
families (all eight done under the explicit-only floor), then the two
generalization families. Held-out captures were frozen before any held-out
label exists.

## Stop-point

Steps 1 to 3 are done for the twelve sem* families: captures under the ruled
explicit-only floor (the development families also under the artifact profile
for the record), frozen 32-pair selections, hydration re-captures and frozen
reviewer packets verified on both pinned runtimes, and annotator A's labels
for all 384 pairs, annotator B's blind review, and the merged final labels
(ruling 7: model-written, packet-only, validated, disagreement `defer`): 160
keep, 134 suppress, 90 defer, 321 of 384 agreed. Next: the co-membership
constraints (step 4, from full source evidence) and the retrieval queries
(step 5) under the same annotator arrangement. The generalization families
still need an export step before the runner accepts them.
