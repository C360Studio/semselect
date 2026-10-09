# Proposed SemEngine integration

**Target update, 2026-10-08: SemEngine replaces SemStreams when ready.** New
community/graph research targets SemEngine. The [SemStreams proposal](semstreams-integration.md)
remains a historical source audit, not the implementation destination. No sibling
repository changes or production integration are part of this proposal.

## Current readiness

The [community-refinement design](../eval/community-refinement/README.md#source-audit-and-execution-readiness)
records the source audit. Remote SemEngine main at
`b34ab3a8ad4fa6d5c644b08370252899e53a95ef` has foundation packages but no graph
runtime. The open ingest-kernel PR has graph types, ingest, readiness, KV catalog
and hierarchy inference; it does not yet contain community clustering, semantic
edges or anomaly review. SemEngine's plan reserves LLM-backed features for
separate admission. A source package in the extraction plan is not an available
SemEngine caller.

The immediate deliverable is therefore an evaluation contract. Execution needs
the actual ported algorithm/provider chain and a pinned SemEngine revision. Do
not build another graph implementation in semselect to fill the port gap, or
present a run against SemStreams as SemEngine evidence.

## Proposed first caller

Use a background community computation over a frozen SemSource graph. Code and
the embedding provider generate bounded semantic virtual-edge candidates. A
reviewer decides `keep`, `suppress` or `defer` from the source content and local
context, then the caller recomputes communities with the existing algorithm.
Compare the resulting graph and retrieved evidence, not only the chosen labels.

The [pilot protocol](../eval/community-refinement/README.md) specifies structural,
stock/tuned semantic, trained-classifier, Qwen and Kev comparisons. It distinguishes
three adoption questions: whether refinement helps, whether model review earns
its cost, and whether a specialized decision backend improves on ordinary Qwen.
None currently has a measured answer for this workload.

## Responsibilities and integration seam

SemEngine owns graph identity, explicit facts, revisions, provider readiness,
candidate generation, deterministic graph computation and lifecycle. The caller
owns the grouping objective, permitted outcomes, source selection, budgets and
fallback. semselect keeps its existing native llama.cpp API, pinned model,
request limits and raw native responses; it acquires no graph/NATS dependency.

Use an evaluation-only immutable graph overlay for the pilot. A suppressed
semantic contribution must preserve explicit and identity-based contributions,
the maximum-weight rule and symmetric semantic membership. Deferral or review
failure leaves the baseline unchanged. Review decisions never create factual
triples, bypass authority boundaries or assign calibrated correctness to scores.

If the pilot qualifies, propose one narrow injectable reviewer at the actual
ported caller. Preserve semantic deferral separately from transport errors and
preserve all native decision fields when using System One. Qwen JSON may satisfy
the same caller contract without a native distribution; do not manufacture one.
The existing chat client cannot become a System One client merely by changing
its URL. No general provider framework is justified by this proposal.

Keep refinement optional. Loss of its provider must leave the qualified lower
tier usable. Cached decisions need content, neighborhood, graph revision, model,
objective and policy identity; a stale decision must not survive a changed
source or graph. Qualify cold start, incremental updates, cancellation, bounded
concurrency, invalidation and restart in SemEngine's real composition before
claiming an integration result. A materialized offline graph cannot prove them.

## Scope and next steps

1. Recheck the port when the clustering and semantic-edge implementations land;
   map this proposal to the actual admitted API rather than assuming SemStreams
   package paths or lifecycle contracts survive unchanged.
2. Prepare and independently review fresh graph families, source evidence,
   co-membership labels and retrieval queries; freeze the pilot before inference.
3. Compare existing algorithms first, then the bounded reviewers if a shortfall
   remains. Preserve a no-benefit result as a valid outcome.
4. Propose a SemEngine integration only after the graph, retrieval, harm and cost
   gates pass. SemEngine's own admission and release process remains separate.

Candidate expansion, membership moves, merge/split selection, factual anomaly
review and summary validation stay visible as distinct graph workloads. They
are not production promises or additional requirements of the first pilot.
