# Community-refinement family candidates

**Measured candidate list, 2026-10-09, before capture and labeling.** These
are entity counts for the 12 owner-chosen graph families of the community
refinement pilot (issue #5, "Fixtures frozen"), drawn from the C360 `sem*`
product repositories. Each family was ingested as its own `system` segment and
trimmed to 50–250 entities.

Every count here is a **legacy SemStreams capture (SemSource
`4093d3ce421371f4a99d7168e372552899bf6795`, SemStreams `v1.0.0-beta.160`); not
a SemEngine result.** `v1.0.0-beta.160` is the SemStreams library that SemSource
links at runtime. It is not the SemStreams source commit the held-out families
were taken from, which is listed below. No labels, embedding-service vectors,
neighbour results, mutual pairs, partitions, review packets, constraints or
retrieval questions exist yet. The freeze steps in
[`../README.md`](../README.md#corpus-labels-and-evidence-freeze) are still
ahead.

[`families.json`](families.json) is the manifest. For each family it records
the split, role, repository, commit, licence, include and exclude rules, the
copied files, a workspace hash, entities by type and domain, the edge proxy,
and the history of every iteration. The run logs, status payloads, KV counts
and duplicate checks are in
[`docs/evidence/20261009T172300Z-legacy-family-counts-semrepos/`](../../../docs/evidence/20261009T172300Z-legacy-family-counts-semrepos/).

Each repository is assigned whole to one split, so near-duplicate code never
straddles development and held-out:

| Repository | Commit | Split | Licence |
| --- | --- | --- | --- |
| [semselect](https://github.com/C360Studio/semselect) | `5ee6c9bb214254393728ef5b588273cf3de5339c` | development | MIT |
| [semengine](https://github.com/C360Studio/semengine) | `00b4a81871d0dbfbf60382ac86a13c6a9908f116` | development | MIT |
| [semstreams](https://github.com/C360Studio/semstreams) | `1b1accf4ea4ea878c26236b5a9e6cb83d2d89d7a` | held-out | MIT |
| [semsource](https://github.com/C360Studio/semsource) | `4093d3ce421371f4a99d7168e372552899bf6795` | held-out | MIT |

semembed (Rust) and seminstruct (docs only) are not used because the legacy
parser reads neither. SemStreams `natsclient/`, `message/` and
`payloadregistry/` are deliberately left out of the held-out families because
the SemEngine development families are extractions of them.

| Family | Split | Repository | Entities | Iteration 1 | Non-containment edges | Dangling edges | Licence |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| semselect-service | development | semselect | 56 | 56 | 26 | 14 | MIT |
| semselect-docs | development | semselect | 242 | 303 | 0 | 0 | MIT |
| semengine-natsclient | development | semengine | 226 | 653 | 377 | 191 | MIT |
| semengine-message | development | semengine | 111 | 111 | 123 | 36 | MIT |
| semstreams-graph-clustering | held-out | semstreams | 175 | 175 | 291 | 143 | MIT |
| semstreams-rule | held-out | semstreams | 162 | 747 | 276 | 152 | MIT |
| semstreams-service | held-out | semstreams | 202 | 541 | 277 | 143 | MIT |
| semstreams-component | held-out | semstreams | 226 | 452 | 370 | 94 | MIT |
| semstreams-agentic-loop | held-out | semstreams | 207 | 805 | 660 | 437 | MIT |
| semsource-source-manifest | held-out | semsource | 223 | 255 | 276 | 106 | MIT |
| semsource-cli | held-out | semsource | 143 | 143 | 151 | 5 | MIT |
| semsource-ui | held-out | semsource | 206 | 432 | 69 | 31 | MIT |

### Generalization set

Owner ruling 4 on issue #5 keeps two of the earlier public-repo families in
`families.json` under a third split, `generalization`. They check whether a
result on the `sem*` families carries over to external code. Only repositories
with an unambiguous licence qualify. They sit outside the four development and
eight held-out families and do not count toward the promotion rule. Their
entries are copied verbatim from the earlier run and were not re-measured: same
commits, counts, files and workspace hashes. Each entry records `copied_from`
(the earlier manifest at commit `f10aff6`) and `evidence`
([`docs/evidence/20261009T163623Z-legacy-family-counts/`](../../../docs/evidence/20261009T163623Z-legacy-family-counts/)).
That run used the same SemSource commit and image with an AST language list
that also declared `java` and `c`.

| Family | Repository and commit | Entities | Non-containment edges | Dangling edges | Licence |
| --- | --- | ---: | ---: | ---: | --- |
| nats-go-micro | [nats-io/nats.go](https://github.com/nats-io/nats.go) `31a3ee4` | 217 | 174 | 70 | Apache-2.0 |
| commons-csv | [apache/commons-csv](https://github.com/apache/commons-csv) `26f2e86` | 223 | 352 | 73 | Apache-2.0 |

### Superseded

The first candidate list of 2026-10-09 drew all twelve families from public
repositories. It had four development families (nats-go-micro, commons-csv,
flask-core and express-lib) and eight held-out families (osh-module,
ogcapi-cs-sections, mavlink-minimal, sveltekit-server-runtime, homebrew-docs,
zlib-core, requests-core and cobra-root). Its measurement is kept unchanged in
[`docs/evidence/20261009T163623Z-legacy-family-counts/`](../../../docs/evidence/20261009T163623Z-legacy-family-counts/),
and its manifest is `families.json` at commit `f10aff6`. The owner ruling of
2026-10-09 on issue #5 replaced it: families come only from the C360 `sem*`
product repositories, and each repository goes whole to one split. Apart from
nats-go-micro and commons-csv, which form the generalization set above, those
families are superseded.

### Annotators

Owner ruling, 2026-10-09 (issue #5): annotator A is the owner. Annotator B may
be a Codex session. The owner resolves disagreements. Who annotated, including
any Codex session, is disclosed in the evidence record.

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

1. `legacy-count/families.input.json` names each repository (URL, sibling
   checkout, pinned commit, licence, export exclusions) and each family (its
   repository, role and include/exclude rules). `families.py prepare` exports
   each repository's committed tree with `git archive` into
   `/tmp/semselect-families-src/<repo>@<commit>/`. Uncommitted or untracked
   files in the sibling checkouts are never read. It then copies only the
   chosen files to `/tmp/semselect-families/<family>/` and writes a per-file
   SHA-256 manifest. It also generates `family-count.tier0.json`, which
   contains one `ast` source per code family and one `docs` source per family
   with Markdown.
2. `families.py dupcheck` hashes every captured file. It compares each
   development family file with each held-out family file and records both
   identical hashes and same-basename pairs, with the share of the longer
   file's lines that `diff` leaves unchanged. Results go to `dupcheck.json` in
   each iteration directory.
3. `run.sh` starts the stock SemSource compose stack with the
   `compose.family-count.yml` override. It uses project
   `semselect-family-count` on ports 14222, 18222 and 18080, with semembed
   disabled, and waits for status `phase=ready` and `index.ready=true`. The
   cap is 15 minutes after the container is healthy. The script then runs the
   counter (`main.go`) twice, 15 seconds apart, and always runs
   `docker compose down -v`.
4. `families.py assemble` combines the iteration outputs into `families.json`.
   It copies the generalization entries from the earlier manifest.

The AST languages are `go`, `typescript`, `javascript`, `svelte` and `python`.
`c` and `cpp` are omitted because none of these families carries C or C++.
Where the owner named a README for a family it is included. Elsewhere,
"non-test" is read as the package's non-test Go files. Test support files that
are not `*_test.go` (`component/lifecycle_test_suite.go`,
`component/test_helpers.go`) are excluded as tests.

Iteration 1 used the owner subtrees and placed four families in range:
semselect-service, semengine-message, semstreams-graph-clustering and
semsource-cli. A read-only planning probe produced per-file counts that
reproduced iteration 1 exactly for all 12 families. The probe imports
SemSource's own parsers (now including Svelte) and doc handler. Its source and
output are under `planning-probe/` in the evidence directory. The trims were
chosen from those per-file counts, file sizes and the files' own package
comments only, never from a baseline or model outcome. Iteration 2 matched the
probe's predictions for all 12 families, so the third permitted iteration was
not used. The rationale for each trim is in each family's `notes` field.

A first attempt at iteration 1 reached readiness, but the counter failed on a
relative evidence path because `run.sh` runs it from its own directory. That
attempt produced no counts. Its logs are kept in
`iter1-attempt-counter-path-failure/`, and `run.sh` now resolves the path.

## Duplicate check

Nothing was flagged, so no file was moved out of a development family. Each
iteration hashed every captured file: 290 files in iteration 1 and 166 in
iteration 2. Development and held-out families shared no identical file. The
check also compared every development/held-out pair with the same basename (23
pairs in iteration 1, 18 in iteration 2). The highest share of unchanged lines
was 0.2081, for SemEngine `message/doc.go` against SemStreams
`processor/agentic-loop/doc.go`, far below the 0.8 threshold. Outside the
captured files, `eval/answerability/heldout/source/` in semselect holds copies
of SemStreams and SemSource files. No family includes `eval/`, but any
widening of semselect-service must keep it that way.

## Caveats for the freeze

- **semselect-service** (56) sits just above the floor. 32 of its entities are
  the root README's doc and chunks. Its code side is only three Go files
  (`cmd/semselect/main.go`, `internal/guard/guard.go` and
  `internal/guard/json.go`).
- **semselect-docs** (242) is close to the ceiling and has no explicit
  relations beyond containment. That is intended for a docs family with
  isolated entities.
- Families trimmed by file have many dangling edges into the excluded files,
  most of all **semstreams-agentic-loop** (437 of 1,072 edges). That is the
  boundary the freeze has to record.
- **semsource-ui** has the fewest non-containment edges per entity of the code
  families (69 for 206 entities), as its sparse-topology role intends.
- semsource is both the capture tool and the source of three held-out
  families. The tool reads its families as plain source like any other
  repository.
- The counts are specific to this legacy parser and doc splitter. A SemEngine
  capture may count differently and must be re-measured, not assumed equal.

## Grouping objective and what the freeze still needs

Each family carries one fixed grouping objective, set here so labels are not
derived from a partition or from edge scores:

- **Code families** (semselect-service, semengine-natsclient,
  semengine-message, semstreams-graph-clustering, semstreams-rule,
  semstreams-service, semstreams-component, semstreams-agentic-loop,
  semsource-source-manifest, semsource-cli, semsource-ui, and the
  generalization pair nats-go-micro and commons-csv): two entities belong
  together when a maintainer changing the responsibility one implements would
  need to inspect the other. In the cross-type families (semselect-service,
  semengine-natsclient, semstreams-graph-clustering,
  semsource-source-manifest, nats-go-micro) a doc passage joins the
  responsibility it describes.
- **Docs family** (semselect-docs): two passages belong together when a
  reader answering one question about the topic would need both.

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

Docker, Go, Python 3.12 or later, a SemSource checkout at the commit above and
sibling checkouts of semselect, semengine, semstreams and semsource that
contain the pinned commits are required. Uncommitted work in those checkouts
does not affect the export. Ports 14222, 18222 and 18080 must be free.

```bash
ev=docs/evidence/$(date -u +%Y%m%dT%H%M%SZ)-legacy-family-counts-semrepos
eval/community-refinement/fixtures/legacy-count/run.sh iter1 "$ev"
python3 -I eval/community-refinement/fixtures/legacy-count/families.py assemble \
  --input eval/community-refinement/fixtures/legacy-count/families.input.json \
  --iteration "$ev/iter1" --semsource <semsource commit> \
  --semstreams v1.0.0-beta.160 --image <semsource image ID from run.log> \
  --evidence "$ev" --out eval/community-refinement/fixtures/families.json
```

`run.sh` refuses to start if any of its ports is already listening, so it does
not touch another capture stack.
