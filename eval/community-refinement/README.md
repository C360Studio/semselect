# Community refinement in SemEngine

**Design, 2026-10-08; no fixtures, driver or inference results yet.** The question
is whether reviewing semantic virtual edges improves communities enough to earn
the review cost. SemEngine is the integration target; SemStreams is the frozen
source reference during its replacement. Routing results do not answer this
question. This is an isolated evaluation proposal, not admission of a new
SemEngine capability or a change to semselect's runtime.

The first pilot tests **removing misleading semantic influence while retaining
useful groupings**. It does not ask a model to generate a graph, invent factual
relationships, or replace the clustering algorithm. Missing-edge discovery,
direct membership moves, merge/split selection and summary validation remain
separate follow-ups.

## Source audit and execution readiness

The graph port is less complete than the initial assumption. Read-only inspection
on 2026-10-08 found:

| Inspected source | What is present | Consequence |
| --- | --- | --- |
| Local SemEngine main `00b4a81871d0dbfbf60382ac86a13c6a9908f116` | Foundation packages; no `graph/` or `processor/` tree | The local checkout cannot run this experiment. |
| GitHub SemEngine main `b34ab3a8ad4fa6d5c644b08370252899e53a95ef` | The complete recursive tree also has no `graph/` or `processor/`; the repository map says no runtime or consumer yet | This is not just a stale local checkout. |
| Open [SemEngine PR #93](https://github.com/C360Studio/semengine/pull/93), head `27922bda966c94e371a62a6ef82336cb88898943` | `graph`, readiness, KV catalog, hierarchy inference and `processor/graph-ingest`; no clustering, embeddings, query package or anomaly review worker | Ingest work is present, but the community path is not ported at this head. |
| SemEngine's declared SemStreams source pin `8b99efe9c66a4faa4fa509f9f62cc6bad8392128` | Weighted label propagation, identity and semantic virtual-edge providers, anomaly review | These implementations inform the design; they are not measured SemEngine behavior. |

Sources: the [remote main tree][engine-tree], [current repository map][engine-map],
[PR #93 tree][ingest-tree] and its [hierarchy-only inference cut][ingest-design].
The accepted extraction design places the clustering library with graph roots
and the clustering processor later in the substrate work; PR #93 also moves
structural analysis and the remainder of inference to that later work. These are
plans, not completed ports. SemEngine's [tier plan][engine-tiers] distinguishes
provider-free clustering, neural retrieval, and separately admitted LLM features;
the Tier 2 slot is empty at the MVP.

The pinned source gives us concrete baseline behavior:

| Surface at the SemStreams source pin | Observed behavior to preserve or compare |
| --- | --- |
| [Weighted LPA][lpa] | Sorts entity and neighbor order, uses a fixed shuffle seed and weighted neighbor votes; computes hierarchical levels. Repeated provider ordering is a determinism check, not a new accuracy sample. |
| [EntityIDProvider][identity] | Adds ephemeral sibling/system-peer hints over explicit edges. Identity grouping is already a baseline, not a new model feature. |
| [SemanticEdgeProvider][semantic] | Intersects directed nearest-neighbor sets into symmetric mutual-kNN edges. Source defaults are `k=8`, similarity threshold `0.75`, semantic weight `0.9`. These are starting values, not proven optimal settings. |
| [Weight resolution][weights] | An explicit edge dominates. Otherwise use the maximum of qualifying sibling, system-peer and semantic weights, never their sum. |
| [Readiness/failure behavior][semantic-fallback] | An unavailable semantic tier degrades to structural behavior; readiness, partial refresh and previous-cache behavior must be recorded separately from a healthy empty candidate set. |
| [Anomaly review][review] | Threshold decisions followed by optional LLM approve/reject review. This evaluates suggested factual relationships, a different contract from virtual community co-location. |

Before execution, select a SemEngine commit with the required implementation and
its tests. Re-audit the ported provider chain, actual configuration, tier admission,
public seams, lifecycle and retrieval path. Freeze that commit and record every
relevant departure from the source pin. Do not transplant a second graph engine
into semselect or silently substitute SemStreams. A separately requested legacy
replay would be labeled SemStreams evidence and would need SemEngine confirmation.

Plan boundaries: the current deliverables are this protocol and the research
scope update. The main risks are an unfinished port, sparse or biased source
evidence, circular community labels, and crediting a configuration change to a
model. Acceptance for this design is a traceable source audit, explicit comparison
and grading rules, bounded execution, and honest separation of design from results.

## Caller and decision contract

The proposed caller is SemEngine's background community computation for a
SemSource mixed code/document graph. Its product objective is to put evidence
about a shared responsibility together without letting boilerplate, overloaded
terms or generic utility nodes join unrelated subjects. This is an objective for
this fixture, not a universal definition of a correct community.

The caller proposes an unordered pair from its existing semantic virtual edges.
The reviewer receives both entities' source passages, types, provenance and
revisions, plus bounded local structural context and the candidate's similarity
and neighbor ranks. Every arm sees the same frozen evidence; models receive no
gold labels, ideal community IDs or query answers. Use source passages rather
than generated summaries as the initial evidence representation.

The single Choice question is: **Should this pair retain its semantic co-location
hint for the stated community objective?**

| Label | Meaning | Evaluation action |
| --- | --- | --- |
| `keep` | Evidence supports shared subject/responsibility at the chosen granularity | Retain the existing semantic contribution. |
| `suppress` | Evidence shows an incidental match that would mislead this grouping | Remove only the semantic contribution. |
| `defer` | Available evidence is insufficient or conflicting | Preserve the baseline and count the unresolved review. |

Timeouts, malformed responses, input overflow and unavailable evidence are
operational failures, recorded separately from semantic `defer`; their fallback
also preserves the baseline. The caller applies all scope, freshness and identity
checks before review. A deferred pair is not a rejected edge or a missing fact.

Suppressing a semantic contribution must retain any explicit, sibling or
system-peer contribution and recompute the effective weight under the same
resolution rule. It must update neighbor membership as well as weights: a pair
with no remaining contribution is absent in both directions. Never delete an
explicit triple, alter factual provenance, create a persisted relationship, or
turn a model probability into an edge weight. Pairs with a dominating explicit
edge need no model call. Record no-effect decisions where another virtual tier
already dominates.

Apply the entire review overlay to an immutable graph view, then invoke the
**same ported LPA implementation** for every arm. No sequential model feedback
within a cycle. Keep all non-reviewed pairs unchanged. Candidate generation is
frozen before labels or model results; this first experiment cannot discover a
missing pair outside that candidate set.

### Per-entity bundle variant

The per-pair request above stays the baseline contract. The variant makes one
entity's source passage the shared state. Each candidate neighbour of that
entity becomes its own Choice question in the same request. The question names
the neighbour and carries its bounded excerpt, kept within the existing
512-byte candidate-description limit, and it uses the same `keep`, `suppress`
and `defer` labels as the per-pair contract.

The service profile allows 1 to 4 questions per request, so a bundle of more
than four neighbours is split into several requests, and the split is recorded
with the results. The pilot does not change the guard.

The trade-off is packet size: a bundle packs one entity's passage and several
neighbour excerpts into one request, which pushes against the 8,192-byte state
limit where a per-pair request carries a single pair. A packet that does not fit
follows the existing out-of-profile rule below. Because the questions in a
bundle share one state, this is the shape where state reuse can show up on a
runtime that offers it; a per-pair request leaves nothing to reuse. Whether it
does is a question for the [throughput experiment](../throughput/README.md),
which has not run.

## Corpus, labels and evidence freeze

Start with 12 disjoint graph families: four development and eight held-out. Each
has one primary snapshot of 50–250 entities, complete induced edges plus recorded
boundary edges, source revisions, embedding identity and a fixed grouping
objective. Use real SemSource captures once its SemEngine path is available.
Until then fixture preparation may use source material, but must identify it as
authored or captured from a legacy system. Never call either a SemEngine capture.

Assign repositories or genuinely independent subject families to splits before
annotation. Related modules, duplicate passages, graph versions, paraphrases and
all perturbations stay in the same family. Do not split edges randomly: many share
entities and are strongly dependent. The eight held-out families are a pilot,
not eight hundred independent examples or production traffic.

Include cross-type evidence about the same responsibility, lexically similar
unrelated subjects, generic hubs/boilerplate, multiple legitimate topics, isolated
entities and sparse explicit topology. Version/scope conflicts, missing bodies
and unavailable embeddings belong in a separately reported fault suite. Do not
select held-out graphs because a particular baseline or model fails on them.

For each primary snapshot:

1. Freeze the stock directed neighbor results, mutual pairs, explicit/identity
   memberships, effective weights and structural-only partition: the same
   explicit/identity provider chain with the semantic profile's structural
   weights retained and semantic influence disabled. Use the actual
   qualified embedding service and preserve its model/artifact/preprocessing
   identity. Replayed neighbor results isolate review cost; a live refresh later
   measures total embedding and clustering cost.
2. Review at most 32 effective semantic candidates. Prioritize pairs crossing
   the structural-only partition, then distance of cosine similarity from `0.8`,
   then a stable hash of the unordered IDs. Exclude explicit-dominated and
   semantic-no-effect pairs. Freeze this selection; do not refill it according
   to labels or outcomes. Report the fraction of all candidates it covers.
3. Prepare the exact bounded reviewer packet as specified below, then independently
   label it `keep`, `suppress` or `defer`, citing evidence visible in that packet.
   Missing decisive evidence means `defer`, even when the full source would
   answer the question. Two annotators resolve disagreements before inference;
   unresolved cases remain `defer`. Show disagreement counts. Structural or
   embedding scores alone are not label evidence.
4. Separately annotate 20 co-membership constraints, ten positive and ten
   negative, including pairs outside the review set. Positives mean useful to
   inspect together; negatives mean harmful to conflate under this objective.
   Use full source evidence, not the bounded review packet, for these independent
   graph labels. Do not derive them from the current partition or the edge labels.
5. Write six known-answer evidence-retrieval queries with gold source/entity
   sets: four require complementary evidence and two require a precise source.
   Record all known alternative supporting sets. Grade retrieved evidence,
   without introducing an answer generator as another changing variable.

The target is 128 development and 256 held-out edge reviews, 160 held-out
co-membership constraints and 48 held-out retrieval questions. These are ceilings
for review counts, not permission to fabricate extra candidates. Record actual
counts. Fewer than eight qualifying held-out families or fewer than 128 eligible
held-out review pairs makes the result exploratory and unable to pass the pilot
promotion rule below. Smaller valid fixtures still produce useful diagnostics.

Serialize source packets deterministically, with a maximum 8,192-byte state and
the service's existing request limits. Bound each entity excerpt and local
neighborhood before freezing; preserve full source references and omitted-content
metadata. Verify both tokenizers, templates, native decision tails and Qwen output
reserve against the 4,096-token runtime context. A packet that cannot fit is an
explicit out-of-profile case for all paired model comparisons, not silently
truncated input or evidence of inability to understand a complete graph.

Freeze graph and source hashes, family assignments, annotations, queries,
candidate sets, prompts, configuration grids, random seeds, grading and budgets
before held-out calls. Development labels may tune policies; held-out labels
never select prompts, graph parameters or thresholds.

## Comparison arms and selection

| Arm | Purpose |
| --- | --- |
| Explicit-only LPA | Diagnostic lower tier: how much do virtual hints add? |
| Structural/identity LPA | Existing coded grouping without semantic edges. Preserve the configured identity weights. |
| Stock semantic LPA | Actual ported semantic provider, no review; records whether any improvement is needed. |
| Tuned semantic LPA | Strong algorithm baseline: development-only search over `k={4,8}`, cosine threshold `{0.75,0.80,0.85}`, semantic weight `{0.3,0.6,0.9}`; retain the same explicit/identity configuration. |
| Small trained edge reviewer | Regularized logistic regression over frozen cosine, reciprocal ranks, shared-neighbor statistics, identity-tier flags and symmetric endpoint embedding features. Trained only on development families; suppress or preserve. |
| Qwen JSON reviewer | Pinned Qwen3.5-4B, constrained `keep/suppress/defer`, no generated confidence. |
| Native decision reviewer | Pinned Kev-4B through semselect's native Choice API, preserving the full distribution and confidence separately. |

The model and trained-reviewer arms overlay the **same stock graph and selected
review set**. The tuned algorithm may change more of the stock graph; it is a
whole-pipeline comparator, not a matched per-edge accuracy arm. Its grid only
narrows the stock neighbor envelope; preserve exact tie/order semantics. If the
ported stock parameters differ from the source defaults, amend and freeze the
grid before collecting outcomes. Also run a stock arm with semantic influence
disabled but the semantic profile's structural weights retained, so a changed
system-peer weight cannot be credited to embeddings.

The trained reviewer is required because this is a stable binary intervention
with development labels, even though the source text varies. Treat gold `defer`
as a preserve target, not permission to train suppression on uncertain evidence.
Use leave-one-family-out development validation, `C={0.1,1,10}` and suppression
thresholds `{0.7,0.8,0.9}`; fit preprocessing within each fold. Record label,
training and inference costs. Out-of-fold decisions choose the configuration,
then refit on all development families. Report its intervention metrics rather
than inventing three-way labels for this binary scorer.

Use one shared wording of the task, temperature zero/thinking disabled for Qwen,
and one Choice head for Kev. Kev may suppress only when `suppress` wins and its
score passes a development-selected threshold `{0,0.7,0.8,0.9}`; otherwise preserve
the graph. Qwen has its explicit `defer`, without fabricated scores. These scores
are not calibrated correctness probabilities. No prompt/model search after the
freeze; the model/runtime revisions, quantization, SHA-256 and licenses stay at
the existing locks.

Runtimes use different artifacts: GGUF Q4_K_M on llama.cpp, MLX affine 4-bit on
SGLang and bf16 on Kev's own MLX server. Compare throughput within a runtime
first. Cross-runtime label agreement is a diagnostic, not a quality ranking,
because the quantization differs along with the runtime.

Select configurations using development graph outcomes: relative to **stock
semantic LPA**, first require retaining every previously successful retrieval
question and no decrease in macro positive co-membership;
then minimize negative-pair co-location; break ties by fewer changes and lower
measured total cost, then configuration order. If no configuration is eligible,
retain the no-change baseline. Publish every arm; preselect the strongest eligible
non-generative comparator before opening held-out outcomes.

This compares the actual existing algorithm family and a trained alternative.
It does not establish that LPA is the best possible clustering algorithm. If
development shows the failure comes from LPA or identity weights, record that
shortfall and test an applicable algorithm/configuration change before presenting
model review as the necessary repair.

## Outcomes and pilot promotion rule

Report three separate layers; label accuracy alone cannot promote an integration.

| Layer | Measurements |
| --- | --- |
| Review decisions | Full confusion matrix for three-way arms; suppress precision/coverage for every reviewer; wrongly suppressed `keep` and `defer` cases; retained bad hints; transport failures, deferrals, no-effect decisions and unattempted cases. |
| Resulting communities | Positive-pair co-location rate; negative-pair co-location rate; per-family and macro averages; community-size distribution, singleton share, lost entities and membership change. Primary level is 0; other hierarchy levels are diagnostics. |
| Retrieval and cost | Evidence-set completion at a fixed context budget, indispensable-source losses, source precision, cold cycle and incremental cycle latency, total CPU/memory, embedding/hydration/review/LPA costs and actual cache hit rates. |

For the initial materialized-graph pilot, a timed cycle covers snapshot loading,
packet hydration, review calls, overlay assembly, LPA and evidence retrieval.
Model startup is separate. Embedding creation/refresh is shared frozen input;
report its capture cost separately and do not count it as zero or include it in
a claim of live pipeline speed. The later integration confirmation measures the
whole live path and incremental operation. Measure each held-out cycle from an
empty decision cache; measure unchanged replays separately with a populated cache.

For retrieval, freeze SemEngine's existing retrieval strategy and direct-evidence
path, query embeddings, ranking and tie rules before held-out runs. Vary only the
partition used for community expansion. Use a 4,096-token evidence budget under
one fixed tokenizer, identical source formatting and duplicate removal. A query
succeeds when the budgeted evidence contains every member of at least one gold
supporting set. Report community expansion separately from the complete path: if
direct retrieval masks every partition difference, graph improvement has not
shown a downstream retrieval benefit. Also compare the existing retrieval path
with community expansion disabled; a new community mechanism must earn its cost
against that option too.

Proposed **pilot screening thresholds**, to freeze before execution:

- No explicit-edge, authority/scope, entity-retention or freshness violations.
  A failed pair review preserves that pair's baseline contribution; successful
  reviews of other pairs may still change the partition. With every review
  unavailable, the partition must equal the no-review result. A provider or
  whole-cycle failure must follow the actual provider's qualified fallback/cache
  behavior, using no incompatible review overlay. Never score failure as a good
  suppression.
- At most two wrongly suppressed gold `keep` pairs and zero suppressions of gold
  `defer` pairs on held-out data; report numerators, denominators and uncertainty.
- Against the frozen strongest non-generative comparator, reduce macro negative
  co-location by at least 10 percentage points, with no macro loss of positive
  co-location and no family losing more than one of its ten positive pairs.
- Lose zero previously successful retrieval questions against that comparator,
  and gain at least four of the 48. If graph metrics improve but retrieval does
  not, record a graph-quality signal without claiming the SemSource use case
  earns integration. If the comparator is already at the ceiling, choose a new
  prospective workload in a new protocol; do not weaken this gate after results.
- Complete every held-out pilot cycle (up to 250 entities and 32 reviews) in at
  most five minutes on the selected Metal profile; an unchanged-input cycle
  makes zero new review calls. Name the largest size actually tested; smaller
  snapshots do not establish 250-entity capacity. These are pilot budgets, not
  production SLOs or the query router's subsecond latency target.
- Name the real scale before the pilot: candidates per cycle and refresh cadence
  on a representative SemSource graph. Required decisions per second equals
  candidates divided by the cadence in seconds. Compare it with the selected
  profile's measured decisions per second from the
  [throughput experiment](../throughput/README.md). If the requirement exceeds
  the measurement, the track is infeasible on this hardware, regardless of Kev
  versus Qwen. For example (arithmetic only), 10,000 candidates refreshed hourly
  need about 2.8 decisions per second; refreshed every five minutes, about 33.

Passing those gates establishes a reason for a larger confirmation, not production
adoption. Kev earns preference over Qwen only if it also passes the graph-value
gates and either retains all of Qwen's successful held-out retrieval questions
and adds at least two successes with no extra harmful suppressions, or preserves
Qwen's successful queries and harm
counts while reducing matched pilot-cycle median elapsed time by at least 20%
with no worse p95. Report resource use separately; elapsed time is not a dollar
or energy estimate. Eight cycle observations give only a preliminary tail
measurement. Report individual families and failures alongside aggregates.

Use paired family-level resampling (10,000 bootstrap draws, seed `20261008`) for
95% intervals on differences. Eight families give limited precision: intervals
crossing zero leave a superiority claim inconclusive even when a point estimate
passes a screening threshold. Edges, query paraphrases and reorderings are not
independent samples. Never use increased modularity or the same embedding cosine
that generated the edges as the sole evidence of semantic improvement.

## Robustness, resources and execution sequence

Before model calls, meaningful harness tests must catch suppression that deletes
an explicit/identity edge, asymmetric overlays, mismatched evidence versions,
stale decision reuse, graph-order dependence, incorrect context-budget grading,
and failures disappearing from denominators. Use injected faults and exact
expected partitions; offline passes establish harness behavior only.

Reserve 24 development-derived review cases for option reversal, endpoint-order
swaps and source text containing misleading instructions (eight each). Change
neither labels nor authoritative IDs. Require zero additional harmful
suppressions and report all decision changes; these variants do not increase the
primary sample size. Test healthy empty neighbors, missing bodies, embedding
unavailability, mid-refresh failure, runtime timeout and recovery separately.

Cache decisions by graph/evidence revision, endpoint content, neighborhood,
embedding/provider identity, objective, model and prompt/policy version. Reuse
only while every dependency matches. Freeze one update sequence per development
family: unchanged replay, a source revision, an explicit-edge change and a
provider outage/recovery. First run these as offline contract checks with a
counting test reviewer, outside model-quality denominators and inference budgets.
Measure actual invalidations and requested reviews; do not assume a one-node
change affects only that node when ranks/neighborhoods change. Live incremental
cost remains unmeasured until the integration confirmation.

Execution is staged:

1. **Port readiness and fixture freeze:** verify the actual SemEngine seam and
   isolated driver, record exact source/configuration hashes, independently
   review labels and grading, and pin the actual served embedding artifacts.
2. **Code and trained baselines:** finish development selection and a readiness
   check. If representative development graphs show no shortfall, publish that
   result and do not start model shopping.
3. **Primary Metal quality pilot:** Qwen and Kev runs on the same M3 Pro, one
   model server at a time, using the serving profile selected by the
   [throughput experiment](../throughput/README.md). Until that result exists,
   the existing one-slot service profile (see the
   [README](../../README.md#configuration-and-operational-bounds)) is a
   placeholder and the cost gate is provisional.
   Account for Qwen's direct chat path and Kev's guard. Verify actual cache/token
   behavior in logs. Cold start and warmup remain separate from request timing.
4. **CPU/Docker feasibility:** at most six fixed development packets per model,
   spanning short/long evidence, on Linux/ARM64 with four CPUs/threads and an
   8 GiB hard memory limit. This bounded check is not full CPU graph quality.
   Only a useful Metal result and an acceptable probe justify a separate CPU
   cohort. Do not infer AMD64 or CUDA behavior.
5. **Conditional integration confirmation:** once the pilot qualifies, replay
   live embedding refresh, hydration, invalidation and retrieval on SemEngine's
   real composition. Passing a materialized-graph driver is not proof of its
   background scheduling, persistent state or provider recovery.

Bound primary Metal work to 45 minutes of inference per model, 450 requests per
model including development, primary, warmups, sensitivity and CPU probes, and
30 seconds per Metal request. Allow at most three warmups per hardware profile.
The planned ceiling is 384 development/primary calls + 24 robustness variants +
six CPU probes + six warmups = 420 per model; the cap is not an invitation to add
adaptive cases. The CPU probe has a 240-second request limit and a 20-minute total per model.
Setup is capped at 20 minutes per model with one corrective attempt. Stop on
OOM, input truncation, invalid candidate mapping or three consecutive runtime
failures; retain failed and `not_run` rows, and mark incomplete cohorts unable to
pass. No response-dependent retries. Record request/outer latency, startup,
cgroup peaks or native RSS limitations, hardware contention and verified cleanup.
Model servers run serially under the existing inference lock and owned-process
conventions. No new model download, server or experiment is started by this design.

The eventual driver belongs under this directory in an isolated Go module using
the ported SemEngine algorithm and an evaluation-only graph overlay. Reuse the
repository's model transports and validation helpers; add neither a production
provider framework nor a second scoring engine. Persist source snapshots,
configuration selection, full request/response bytes, graph partitions, graded
query evidence, resource logs and reproduction hashes under a new evidence
directory before updating the selection guide. No runnable task is claimed yet.

## What follows this pilot

If suppression helps, test proposals for adding missing semantic candidates,
then bounded membership moves or merge/split alternatives generated by code.
Each needs a new candidate-recall control and its own graph-level outcomes.
Factual relationship review needs source-backed predicate labels; community
co-location is not evidence for `depends_on`, causality or identity. Summary
faithfulness and prioritizing expensive reviews are separate cost/quality
questions. None is ruled out by the routing studies or this first suppression
pilot.

[engine-tree]: https://github.com/C360Studio/semengine/tree/b34ab3a8ad4fa6d5c644b08370252899e53a95ef
[engine-map]: https://github.com/C360Studio/semengine/blob/b34ab3a8ad4fa6d5c644b08370252899e53a95ef/docs/repository-map.md#not-yet-present
[engine-tiers]: https://github.com/C360Studio/semengine/blob/b34ab3a8ad4fa6d5c644b08370252899e53a95ef/docs/setup-plan.md#L74
[ingest-tree]: https://github.com/C360Studio/semengine/tree/27922bda966c94e371a62a6ef82336cb88898943/graph
[ingest-design]: https://github.com/C360Studio/semengine/blob/27922bda966c94e371a62a6ef82336cb88898943/openspec/changes/setup-04a-02-ingest-kernel/design.md#d1a-the-graphinference-slice-97-and-what-it-leaves-out
[lpa]: https://github.com/C360Studio/semstreams/blob/8b99efe9c66a4faa4fa509f9f62cc6bad8392128/graph/clustering/lpa.go#L154
[identity]: https://github.com/C360Studio/semstreams/blob/8b99efe9c66a4faa4fa509f9f62cc6bad8392128/graph/clustering/entityid_provider.go#L15
[semantic]: https://github.com/C360Studio/semstreams/blob/8b99efe9c66a4faa4fa509f9f62cc6bad8392128/graph/clustering/semantic_edge_provider.go#L53
[weights]: https://github.com/C360Studio/semstreams/blob/8b99efe9c66a4faa4fa509f9f62cc6bad8392128/graph/clustering/semantic_edge_provider.go#L171
[semantic-fallback]: https://github.com/C360Studio/semstreams/blob/8b99efe9c66a4faa4fa509f9f62cc6bad8392128/graph/clustering/semantic_edge_provider.go#L477
[review]: https://github.com/C360Studio/semstreams/blob/8b99efe9c66a4faa4fa509f9f62cc6bad8392128/graph/inference/review_worker.go#L372
