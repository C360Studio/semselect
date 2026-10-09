# Community-refinement family candidates

**Measured candidate list, 2026-10-09, before capture and labeling.** These
are entity counts for the 12 owner-chosen graph families of the community
refinement pilot (issue #5, "Fixtures frozen"). Each family was ingested as its
own `system` segment and trimmed to 50–250 entities.

Every count here is a **legacy SemStreams capture (SemSource
`4093d3ce421371f4a99d7168e372552899bf6795`, SemStreams `v1.0.0-beta.160`); not
a SemEngine result.** No labels, embedding-service vectors, neighbour results,
mutual pairs, partitions, review packets, constraints or retrieval questions
exist yet. The freeze steps in
[`../README.md`](../README.md#corpus-labels-and-evidence-freeze) are still
ahead.

[`families.json`](families.json) is the manifest. For each family it records
the split, role, repository, commit, licence, include and exclude rules, the
copied files, a workspace hash, entities by type and domain, the edge proxy,
and the history of every iteration. The run logs, status payloads and KV
counts are in
[`docs/evidence/20261009T163623Z-legacy-family-counts/`](../../../docs/evidence/20261009T163623Z-legacy-family-counts/).

| Family | Split | Entities | Iteration 1 | Non-containment edges | Dangling edges | Licence |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| nats-go-micro | development | 217 | 217 | 174 | 70 | Apache-2.0 |
| commons-csv | development | 223 | 414 | 352 | 73 | Apache-2.0 |
| flask-core | development | 173 | 495 | 200 | 52 | BSD-3-Clause |
| express-lib | development | 235 | 235 | 0 | 0 | MIT |
| osh-module | held-out | 202 | 266 | 318 | 105 | MPL-2.0 |
| ogcapi-cs-sections | held-out | 187 | 274 | 0 | 0 | LicenseRef-OGC-Software-License |
| mavlink-minimal | held-out | 129 | 47 | 15 | 0 | NOASSERTION |
| sveltekit-server-runtime | held-out | 192 | 789 | 18 | 0 | MIT |
| homebrew-docs | held-out | 152 | 327 | 0 | 0 | BSD-2-Clause |
| zlib-core | held-out | 186 | 637 | 27 | 0 | Zlib |
| requests-core | held-out | 206 | 364 | 325 | 69 | Apache-2.0 |
| cobra-root | held-out | 218 | 336 | 275 | 83 | Apache-2.0 |

## What the numbers mean

- **Entities** counts the `ENTITY_STATES` keys whose fourth ID segment is the
  family. The count includes the repo, folder, file, doc and chunk structure
  entities as well as symbols. Doc chunk counts follow SemSource's passage
  bounds (2,000/400/6,000 bytes) at this commit.
- **Edge proxy** reads `OUTGOING_INDEX`. "Non-containment" excludes
  `code.structure.belongs/contains`, so it covers calls, receivers, parameters,
  returns, references, imports, extends and embeds. "Dangling" targets are
  absent from `ENTITY_STATES`: external symbols or excluded files. These are
  candidates for recorded boundary edges, not frozen boundary edges.
  Cross-family edges were 0 for every family.
- Statistical retrieval, explicit structure and learned embeddings remain
  separate. Tier 0 builds SemSource's in-process BM25 index (`embedding.ready`
  in the status payload refers to it). No semembed, HTTP embedder or LLM ran.

## How it was produced

1. `legacy-count/families.input.json` names each family, its pinned commit and
   the include/exclude rules. `families.py prepare` shallow-clones each repo
   under `/tmp/semselect-families-src/` and copies only the chosen files to
   `/tmp/semselect-families/<family>/`. It also writes a per-file SHA-256
   manifest and generates `family-count.tier0.json`, which contains one `ast`
   source per code family and one `docs` source per family with Markdown or
   AsciiDoc.
2. `run.sh` starts the stock SemSource compose stack with the
   `compose.family-count.yml` override. It uses project
   `semselect-family-count` on ports 14222, 18222 and 18080, with semembed
   disabled, and waits for status `phase=ready` and `index.ready=true`. The
   cap is 15 minutes after the container is healthy. The script then runs the
   counter (`main.go`) twice, 15 seconds apart, and always runs
   `docker compose down -v`.
3. `families.py assemble` combines the iteration outputs into `families.json`.

The AST languages are `go`, `typescript`, `javascript`, `java`, `python`,
`svelte` and `c`. `cpp` is omitted deliberately. Whenever `cpp` is declared,
SemSource reads `.h` files as C++ (`processor/ast-source/routing.go`), and none
of these families is C++.

Iteration 1 used the owner subtrees and placed only nats-go-micro and
express-lib in range. A read-only planning probe produced per-file counts that
reproduced iteration 1 exactly. The probe imported SemSource's own parsers and
doc handler; its source and output are under `planning-probe/` in the evidence
directory. The trims were chosen from those per-file counts and entity totals
only, never from a baseline or model outcome. Iteration 2 matched the probe's
predictions for all 12 families, so the third permitted iteration was not used.
The rationale for each trim is in each family's `notes` field.

## Caveats for the freeze

- **mavlink-minimal** has no README.md or LICENSE in `c_library_v2` at this
  commit. Its licence needs an owner ruling before redistribution. It was
  widened beyond `minimal/` to the dialect's own include closure in the same
  repository.
- **osh-module** is a subset of the code behind the recorded 6,685-entity
  baseline, not a recapture of that baseline.
- **express-lib**, **ogcapi-cs-sections** and **homebrew-docs** have no
  explicit relations beyond containment in this legacy capture. This is
  intended for express-lib (sparse explicit topology) and the docs-only
  families.
- The counts are specific to this legacy parser and doc splitter. A SemEngine
  capture may count differently and must be re-measured, not assumed equal.

## Grouping objective and what the freeze still needs

Each family carries one fixed grouping objective, set here so labels are not
derived from a partition or from edge scores:

- **Code families** (nats-go-micro, commons-csv, flask-core, express-lib,
  osh-module, mavlink-minimal, sveltekit-server-runtime, zlib-core,
  requests-core, cobra-root): two entities belong together when a maintainer
  changing the responsibility one implements would need to inspect the other.
  In the two cross-type families (nats-go-micro, osh-module) a doc passage
  joins the responsibility it describes.
- **Docs families** (ogcapi-cs-sections, homebrew-docs): two passages belong
  together when a reader answering one question about the topic would need
  both.

The protocol's freeze steps 1 to 5 remain open for every family, and all of
them need the embedding service that this count run did not start:

| Step | Needs | Produced by |
| --- | --- | --- |
| Directed neighbours, mutual pairs, effective weights, structural-only partition | semembed `arctic-embed-s` plus the legacy clustering with semantic influence off | a tier-1 capture run per family using the [legacy capture tool](../legacy-capture/README.md) |
| At most 32 effective candidates per family, selected by the protocol's priority rule | the mutual pairs and the structural-only partition | deterministic selection code, offline |
| Bounded reviewer packets (8,192-byte state, bundle contract) | entity bodies and local neighbourhoods | serializer, offline, verified against both tokenizers |
| Edge labels `keep`/`suppress`/`defer` | two named annotators | people, up to 384 packets, both annotators each |
| 20 co-membership constraints and 6 retrieval queries per family | full source evidence, not the packets | people, 240 constraints and 72 queries |

The annotators must be named before packets are generated. Reviewed packets,
constraints and queries are the long pole in people time, not in compute: the
inference for a 32-review family cycle is about half a minute on this laptop.

## Rerun

Docker, Go and a sibling `../semsource` checkout at the commit above are
required, and ports 14222, 18222 and 18080 must be free.

```bash
ev=docs/evidence/$(date -u +%Y%m%dT%H%M%SZ)-legacy-family-counts
eval/community-refinement/fixtures/legacy-count/run.sh iter1 "$ev"
python3 -I eval/community-refinement/fixtures/legacy-count/families.py assemble \
  --input eval/community-refinement/fixtures/legacy-count/families.input.json \
  --iteration "$ev/iter1" --semsource <semsource commit> \
  --semstreams v1.0.0-beta.160 --image <semsource image ID from run.log> \
  --evidence "$ev" --out eval/community-refinement/fixtures/families.json
```

`run.sh` refuses to start if any of its ports is already listening, so it does
not touch another capture stack.
