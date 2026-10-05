# The existing answer path and a faithful gate comparison

This read-only audit on 2026-10-05 identifies the generator that an added gate
would precede. It does not establish a downstream quality or cost improvement.
SemSource was clean at `4093d3ce421371f4a99d7168e372552899bf6795`; its
[go.mod](https://github.com/C360Studio/semsource/blob/4093d3ce421371f4a99d7168e372552899bf6795/go.mod#L8)
pins SemStreams `v1.0.0-beta.160`. The implementation links below use that
dependency version, inspected from the local module cache. The synthesis file
`answer.go` is also byte-identical at inspected SemStreams HEAD
`1b1accf4ea4ea878c26236b5a9e6cb83d2d89d7a`; other query files have changed, so the
two versions are not interchangeable.

## Retrieval and synthesis are separate paths

The fusion MCP tools (`code_context`, `code_search`, `code_impact`, `doc_context`)
return retrieved source evidence. They do not themselves invoke an answer
generator. An external agent can reason over those results, but its prompt and
model are not specified by SemSource's fusion contract.

SemSource's `graph_search` is the concrete in-repository answering entry point:

1. The [MCP handler](https://github.com/C360Studio/semsource/blob/4093d3ce421371f4a99d7168e372552899bf6795/processor/mcp-gateway/query_tools.go#L99)
   sends the query with `summarize_threshold: 1` to `graph.query.searchGraph`.
2. [SearchGraph](https://github.com/C360Studio/semstreams/blob/v1.0.0-beta.160/processor/graph-query/searchgraph.go#L68)
   first calls global search, which classifies the query and dispatches among
   entity, path, semantic, temporal, spatial and GraphRAG strategies. Empty
   results can trigger semantic fallback; hard errors are not model judgments.
3. The [GraphRAG path](https://github.com/C360Studio/semstreams/blob/v1.0.0-beta.160/processor/graph-query/graphrag.go#L814)
   retrieves semantic hits, applies inferred type filters and finds matching
   communities. A type filter that would empty a nonempty hit set falls back to
   the unfiltered set. That heuristic fallback is not an authorization rule.
4. [Enrichment](https://github.com/C360Studio/semstreams/blob/v1.0.0-beta.160/processor/graph-query/graphrag.go#L1978)
   adds representative entity labels/types/tags, then
   [synthesis](https://github.com/C360Studio/semstreams/blob/v1.0.0-beta.160/processor/graph-query/graphrag.go#L2144)
   consumes `CommunitySummary` objects. No matching communities means no
   synthesized answer on this enrichment path.
5. The gateway [discloses](https://github.com/C360Studio/semsource/blob/4093d3ce421371f4a99d7168e372552899bf6795/processor/mcp-gateway/disclosure.go#L76)
   entity-only, community/template or LLM output. `answer_model`, rather than
   `degraded` alone, distinguishes an LLM answer from the template floor.

Community summaries are a different evidence representation from the verbatim
passages returned by fusion. A test that puts raw passages into a new RAG prompt
can be useful, but it cannot claim to test this unchanged answer path.

## What the existing generator already does

The [answer synthesizer](https://github.com/C360Studio/semstreams/blob/v1.0.0-beta.160/processor/graph-query/answer.go#L112)
already instructs the model to say what is known and missing when the clusters
do not contain enough information, and not to speculate beyond the supplied
data. Preserve that instruction in every comparison arm; removing it would
artificially weaken the no-added-gate baseline.

Its [user prompt builder](https://github.com/C360Studio/semstreams/blob/v1.0.0-beta.160/processor/graph-query/answer.go#L255)
includes the query, total matching entity/cluster counts and up to five clusters.
Each cluster carries its summary, member count and rounded relevance when
present, representative `Label [Type] {tags: ...}` strings and at most five
keywords. It ends by asking for a concise answer based on those clusters.

Generation uses temperature `0.3`, `max_tokens: 500`, and a default 15-second
synthesis deadline. There is no response JSON schema or explicit semantic
answerability validator: successful response content is returned as the answer.
The [client](https://github.com/C360Studio/semstreams/blob/v1.0.0-beta.160/graph/llm/openai_client.go#L209)
bounds prompt lengths, requires a response choice and allows up to three retries
within the context deadline. Preserve or explicitly disclose these transport
semantics in any replay; do not quietly grant unlimited retries or more time.

An empty summary list bypasses the LLM. Transport errors/timeouts fall back to
the [deterministic template](https://github.com/C360Studio/semstreams/blob/v1.0.0-beta.160/processor/graph-query/graphrag.go#L2161),
with degraded status and a reason. That template lists existing community
material; it is not a semantic refusal. Readiness and authoritative graph-state
checks occur before synthesis: unavailable community enrichment can be stripped,
and fatal state-contract errors stop enrichment. Those checks establish usable
graph state, not that prose logically answers the question. No inspected
synthesis function delegates tenant, security or action authorization to an LLM.

The [model binding](https://github.com/C360Studio/semstreams/blob/v1.0.0-beta.160/processor/graph-query/component.go#L421)
is the `answer_synthesis` capability. SemSource's
[Tier 2 development config](https://github.com/C360Studio/semsource/blob/4093d3ce421371f4a99d7168e372552899bf6795/configs/tiers/tier2-compose-dev.json#L22)
enables clustering and binds community summarization, query classification and
answer synthesis to `seminstruct`. Its
[Compose overlay](https://github.com/C360Studio/semsource/blob/4093d3ce421371f4a99d7168e372552899bf6795/docker-compose.tier2-dev.yml#L21)
defaults to Qwen3-0.6B, explicitly described there as a wiring smoke model.
Server reasoning is off. The size-free alias `seminstruct` is not proof of the
loaded model; record the actual image/model bytes.

## Local feasibility without changing siblings

Read-only Docker inspection found only the unrelated `qfl-lifecycle-a` PostgreSQL
and servicesim containers running. They were left untouched. There was no running
SemSource/NATS/semembed/seminstruct Docker stack to query.

The exact cached ARM64 Linux images include:

- Tier 2 default generator:
  `ghcr.io/c360studio/seminstruct@sha256:297507bee8396438fe284f9ed285165c47a565b3f65be177030c473a4b90bdb9`
  (Qwen3-0.6B, model included in image).
- Default embedder:
  `ghcr.io/c360studio/semembed@sha256:7972174f8e3462fd38bf3ad95d94870f406bb3d1350e0ea3280ce002cb2e09b2`.

No SemSource image was cached. The only discovered native `semsource` executable
was an old dirty build at `34ead7d5c1f483f19139cc74e624f0714fedb6ec`, linked to
SemStreams `v1.0.0-alpha.29`; `go version -m` established that mismatch. Current
Compose builds SemSource from source. A verified current full deployment therefore
needs a rebuild and an isolated stack with indexed test data. This audit used only
cached artifacts; it did not attempt that deployment or establish a build failure.

The cached generator image can support a future isolated CPU prompt replay with
`--pull=never`, a unique owned container and a free loopback port. The cached
[Qwen3.5-4B model](../models.baseline.lock.json) and pinned native runtime can
support a Metal replay. **Qwen3.5-4B is a proposed generator substitution**, not
the Tier 2 default. Keep its results in a separate generator stratum. The cached
[Kev model](../models.lock.json) can supply the optional decision gate. No server
was launched for this audit; cached assets establish feasibility, not execution.

## Smallest faithful experiment

Freeze explicit `CommunitySummary` fixtures, including representative entities,
keywords, ordering and total-entity counts. Record whether each fixture was
captured from a real graph or authored for the experiment. Do not relabel a raw
passage as a production-generated community summary. Preserve gold supporting
facts separately from the model-visible representation.

Reproduce the pinned system prompt and `buildAnswerPrompt` formatting exactly.
Validate the formatted request against source-derived golden examples, including
the five-cluster/five-keyword limits. A replay reproduces the synthesis seam; it
does not rerun classification, retrieval, clustering or community summarization.

For each generator independently, compare:

| Arm | Behavior |
| --- | --- |
| Existing synthesis | Same generator, existing prompt and frozen summaries; its own missing-information instruction remains active |
| Qwen gate then synthesis | The gate sees the same query/evidence; allow invokes the unchanged generator request, defer avoids it |
| Kev gate then synthesis | Same policy and evidence, with Kev producing the gate decision |

Start with the cached Tier 2 Qwen3-0.6B image when testing fidelity to that
deployment. A second Qwen3.5-4B Metal stratum tests the stronger cached generator.
Compare gates **within** each stratum; differences between the two generators
also change model and serving environment. Gating must not rewrite the
generator's prompt or pass its predicted answer into generation.

The gate must see exactly the evidence visible to synthesis, including the same
truncation/selection. An answer hidden beyond the first five clusters cannot
justify allowing a generator that never receives it. Common authoritative checks
must run before every arm, and any experiment-only policy checks must be named
as such rather than attributed to the existing synthesizer.

Score the final outcome: supported answers, unsupported assertions, correct
refusals, unnecessary refusals and partial answers that correctly identify what
is missing. A gate that blocks a generator which would already refuse has not
improved factual correctness; it may only save generation cost. A gate that
blocks a useful qualified answer can reduce utility. Record every gate/generator
call, deadline, fallback, latency, token count and valid-answer coverage. At
temperature `0.3`, repeat the unchanged baseline to measure variation; do not
count repeats as new questions or substitute replayed outputs for measured
end-to-end latency.

This experiment can establish whether the selector improves the existing
synthesis decision on those frozen summaries. Full-pipeline value still requires
the real retrieval/summary distribution, indexing cost and upstream failures.

Source-file SHA-256 values from the inspected `v1.0.0-beta.160` module cache:

| File | SHA-256 |
| --- | --- |
| `processor/graph-query/answer.go` | `052adde66aa48d05e86c7141c25c44e86d3e6deec321a0635d83abe6962d2c19` |
| `processor/graph-query/graphrag.go` | `21ff00840aecd3f414af0bed5c1531986de4bf347d6014cb9f41412f7514ba15` |
| `graph/llm/openai_client.go` | `b251e87f71bbcc053b136172f275440a8b7d3f5446816e63654bfec7ea95149b` |
