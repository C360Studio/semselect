# Existing algorithms are part of the baseline

The question is whether an added semantic judgment improves the existing system.
Comparing two models alone cannot answer that. Code already handles exact facts,
syntax, graph relationships and substantial retrieval work in the sem* ecosystem.
Keep those capabilities in the comparison; do not make them artificially weak by
replacing the real implementation with a few ticket-routing keywords.

This is a read-only source/evidence audit on 2026-10-05, not a new sibling benchmark.
SemSource was clean at `4093d3ce421371f4a99d7168e372552899bf6795`. No sibling repository
was changed and no historical scorecard was rerun. The current semselect routing
smoke set measures a different task and cannot rank these retrieval algorithms.

## Where SemEngine and fusion fit

SemEngine at clean commit `2ec3bcf08bc1d2794d6cb1081ebd1982e08cf2c5` has a documented
extraction/design, not product Go packages yet. Its contract makes lexical search
tier 0, neural search tier 1 and LLM classification an optional future tier-2
capability. See its [repository map](https://github.com/c360studio/semengine/blob/2ec3bcf08bc1d2794d6cb1081ebd1982e08cf2c5/docs/repository-map.md#L9)
and [contract](https://github.com/c360studio/semengine/blob/2ec3bcf08bc1d2794d6cb1081ebd1982e08cf2c5/docs/contract.md#L31).

The implemented algorithms live in SemStreams. The principal files below were
byte-identical at SemEngine's extraction pin
`8b99efe9c66a4faa4fa509f9f62cc6bad8392128` and the inspected clean SemStreams HEAD
`1b1accf4ea4ea878c26236b5a9e6cb83d2d89d7a`.

| Implemented path | What it already handles | Limit relevant to this comparison |
| --- | --- | --- |
| Regex query classifier | Time ranges, zones/coordinates, path/similarity intent, aggregation and ranking | Pattern matching is useful evidence, not general semantic correctness |
| Optional nearest-example classifier | BM25-weighted, feature-hashed vectors and cosine matching to labeled examples | Optional configuration; its initial vectors are non-neural |
| Classifier chain | Keyword short circuit, optional example matching, then optional LLM fallback | Actual call sites differ: graph-query passes no example classifier |
| Lens fusion | Candidate resolution, hydration, lexical/ontology/predicate ranking, graph traversal and context budgeting | Deterministic retrieval assembly, not textual answerability judgment |
| Operational failure reporting | Readiness, backend errors, missing seeds and failed body retrieval, with distinct errors, deferrals and partial-result reasons | Code already owns these outcomes; a model adds no authority |

Sources: [query patterns](https://github.com/C360Studio/semstreams/blob/8b99efe9c66a4faa4fa509f9f62cc6bad8392128/graph/query/classifier.go#L27),
[example classifier](https://github.com/C360Studio/semstreams/blob/8b99efe9c66a4faa4fa509f9f62cc6bad8392128/graph/query/classifier_embedding.go#L62),
[chain](https://github.com/C360Studio/semstreams/blob/8b99efe9c66a4faa4fa509f9f62cc6bad8392128/graph/query/classifier_chain.go#L65),
[graph-query wiring](https://github.com/C360Studio/semstreams/blob/8b99efe9c66a4faa4fa509f9f62cc6bad8392128/processor/graph-query/component.go#L419),
[fusion execution](https://github.com/C360Studio/semstreams/blob/8b99efe9c66a4faa4fa509f9f62cc6bad8392128/pkg/fusion/engine_lens.go#L123)
and [ranking formula](https://github.com/C360Studio/semstreams/blob/8b99efe9c66a4faa4fa509f9f62cc6bad8392128/pkg/fusion/engine_lens.go#L558).

The fusion NL resolver dispatches to semantic retrieval; the configured embedder
determines whether that means statistical vectors or a learned model. The two
`Fuse` entry points also differ: one assembles already-built subqueries, while the
lens engine resolves a raw query. Neither should be casually relabeled a general
NL classifier or standard reciprocal-rank fusion.

An unchanged example-classifier baseline needs careful provenance. Its BM25 vectors
have process-local, order-dependent statistics, and its query adapter uses mutating
`Generate` rather than retrieval's read-only `GenerateQuery`. Record example/query
order and fresh-process repeats; do not silently change that behavior and call it
the existing baseline. Its tokenizer also removes `no` and `not`: a reason to test
negation, not a measured failure. See [BM25 implementation](https://github.com/C360Studio/semstreams/blob/8b99efe9c66a4faa4fa509f9f62cc6bad8392128/graph/embedding/bm25_embedder.go#L34)
and [classifier adapter](https://github.com/C360Studio/semstreams/blob/8b99efe9c66a4faa4fa509f9f62cc6bad8392128/graph/query/classifier_embedding.go#L43).

For query routing, compare supported strategy and extracted fields using existing
domain examples and genuinely held-out paraphrases. Freeze time/timezone for
temporal queries. Include the unchanged keyword path and whichever optional stages
the deployed configuration actually enables. Do not score an IoT query classifier
on billing-ticket labels and present that taxonomy mismatch as a model victory.

## What semsource already does

| Existing capability | Mechanism | What a decision model would have to add |
| --- | --- | --- |
| Choose symbol, path or natural-language retrieval | Syntax heuristics: whitespace, path separators and extensions | Improve actual misrouted queries, without damaging exact lookups |
| Preserve exact symbol identity and compose impact results | Byte-exact seed policy and declared graph edges | A separately demonstrated semantic gap; re-guessing known identity is not a benefit |
| Produce ontology tags | Table mappings from parsed entity domain/type | Latent prose labels that structured facts cannot already supply |
| Statistical text retrieval | Pure-Go BM25 configuration, no external model service | Better retrieval on a held-out workload at an acceptable extra cost |
| Semantic retrieval plus fusion | Learned embeddings followed by deterministic ranking, filtering and structural composition | Useful incremental results over this complete existing path |

Source: [code lens](https://github.com/c360studio/semsource/blob/4093d3ce421371f4a99d7168e372552899bf6795/source/fusion/lens/code/code.go#L31),
[exact seed policy](https://github.com/c360studio/semsource/blob/4093d3ce421371f4a99d7168e372552899bf6795/processor/code-context/exact_seed.go#L12),
[ontology mappings](https://github.com/c360studio/semsource/blob/4093d3ce421371f4a99d7168e372552899bf6795/source/ontology/ontology.go#L30),
[statistical tier](https://github.com/c360studio/semsource/blob/4093d3ce421371f4a99d7168e372552899bf6795/configs/tiers/tier0-statistical.json#L7),
[semantic tier](https://github.com/c360studio/semsource/blob/4093d3ce421371f4a99d7168e372552899bf6795/configs/tiers/tier1-semantic.json#L21).

Calling all fusion results "pure code" would hide the learned embedding stage.
Conversely, calling the whole system an LLM classifier would hide how much its
deterministic policies and graph composition accomplish. The fusion MCP tools
do not traverse query classification or answer synthesis; the separate Tier 2
configuration explicitly binds those optional capabilities to seminstruct.
See [tool routing](https://github.com/c360studio/semsource/blob/4093d3ce421371f4a99d7168e372552899bf6795/processor/mcp-gateway/query_tools.go#L50)
and [Tier 2 bindings](https://github.com/c360studio/semsource/blob/4093d3ce421371f4a99d7168e372552899bf6795/configs/tiers/tier2-semantic-instruct.json#L21).

## Existing evidence already argues against an unnecessary classifier

The [August 13 loose-language scorecard](https://github.com/c360studio/semsource/blob/4093d3ce421371f4a99d7168e372552899bf6795/scripts/scorecard/results/SUMMARY-v5-loose.md)
used dogfood corpus `076ac39`, 6,495 vectors and an M3 Pro shared host. Each product
question was called three times, with no verdict instability; grep and raw cosine
were each called once per question. Counts below are unique questions, not repeat
calls. Its archived results report:

| Retrieval arm | Fact-presence passes | Loose-language subset |
| --- | ---: | ---: |
| Deterministic grep/read | 26/33 | 7/7 |
| Fusion product surface | 33/33 | 7/7 |
| Raw embedding cosine, top 20 | 28/33 | 6/7 |

Fusion already handled both the precise and loose wording in the tested pairs.
The scorecard therefore left the proposed embedding-classifier tier unfed:
there was no demonstrated gap for it to fix. This is a concrete reason to retain
the existing path, not an assumption that another model must help. See the
[recorded decision](https://github.com/c360studio/semsource/blob/4093d3ce421371f4a99d7168e372552899bf6795/scripts/scorecard/README.md#L402).

These are retrieval fact-presence checks, not arbitrary classification accuracy or
answerability certification. Much grading checks expected substrings somewhere
in returned evidence; a broad match can be weaker than a correct answer. The grep
arm charges whole files to context size, which disadvantages it compared with a
bounded match-window implementation. Learned embeddings participate in the other
two arms. Do not mix these historical timings or success rates with today's
semselect routing results.

Two existing discrimination cases are particularly useful: X01 separates the NATS
monitor default `8222` from a workaround `28222`; X02 separates seminstruct `8083`
from semembed `8081`. They grade the top passage and distinguish a precise answer,
a confusable answer, and both together. See
[questions](https://github.com/c360studio/semsource/blob/4093d3ce421371f4a99d7168e372552899bf6795/scripts/scorecard/questions.json#L272)
and [grader](https://github.com/c360studio/semsource/blob/4093d3ce421371f4a99d7168e372552899bf6795/scripts/scorecard/grade.sh#L50).
Two cases do not establish general semantic entailment, abstention or calibration.
The archived answers are truncated to 6,000 bytes, so they cannot serve as a complete
frozen candidate pool for a new reranking experiment; capture full passages again.

## What the RAG article suggests testing

The owner-supplied Medium excerpt separates relevance from answerability. A
passage can be about reimbursement and still omit the deadline being requested.
That is a more promising semantic test than asking a model to rediscover an exact
tag or graph relationship. The excerpt's own reranking example already retrieved
the answer in the top five without a reranker; a no-reranker baseline is essential.
Its performance claims and proposed pre-search tagging are not local validation.

| Decision point | Existing baseline to preserve | Relevant failure measure |
| --- | --- | --- |
| Exact metadata restriction | Coded predicates on authoritative fields | Wrong exclusion or policy violation |
| Inferred pre-search tags | Existing retrieval with no inferred filter | Relevant documents removed before retrieval; fallback recovery |
| Post-search ordering | Existing fusion order; bounded lexical/BM25 and cosine controls | Relevant/supporting passages in the allowed context budget |
| Whether retrieved evidence answers the question | No added gate; exact fact checks where applicable; development-tuned retrieval-score threshold | Accepted insufficient or contradictory evidence, with coverage |

For the added semantic stages, compare Qwen JSON and option scoring on **the same
full candidate passages**, query, metadata and context budget. Retrieval quality
and classifier quality are different experiments: first freeze retrieval output
to isolate the gate, then measure the full pipeline if the gate earns that work.
Existing fusion is the main product baseline; raw cosine alone would omit its
structural and ranking advantages.

Use precise/loose query pairs and gold support spans, including sufficient,
partial, contradictory and irrelevant evidence. Include missing requested values,
wrong audience/version, negation, misleading instructions within passages and
answers that require multiple chunks. Per-chunk "can answer" scores are not
equivalent to assessing the whole evidence set. Preserve answer-bearing passages
and false exclusions, not only the final winning label.

Tune score thresholds on development examples, then freeze them for held-out
tests. Count false acceptance of insufficient/contradictory evidence separately
from false rejection, and report coverage, retrieval recall, total pipeline
latency, bytes and extra model calls. Do not let a small zero-error accepted subset
stand in for a safety claim. Code-derived flags, similarity and model probabilities
retain their distinct meanings; a shared `confidence` field does not unify them.

For bundled comparisons, let JSON return all requested fields in one response.
Count every request and inference operation on both sides. Our current semselect
guard allows four questions, so the article's 20/40-question examples do not fit
one supported request. A single HTTP call alone proves no shared-compute benefit.

Measure the complete pipeline as well as the difficult residual cases, including
how often code avoids a model call. An always-model test alone hides the value of
the existing code-first chain. Exact tenant, security and authorization filters
remain mandatory even when broader semantic search is used as a fallback.

The acceptance rule is incremental value: an added selector must fix an observed
semantic failure or reduce total cost at comparable quality while preserving the
existing successes. If the current algorithmic path meets the requirement, the
documented recommendation should be to keep it.
