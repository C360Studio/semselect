# Proposed SemStreams integration

**Closeout status, 2026-10-06: proposal only.** The CPU/Metal research did not
establish a production adoption case. No adapter or new provider framework is
required to complete it. Revisit this design only for a caller that meets the
[reopening conditions](when-to-use.md#research-closeout-and-reopening-conditions).

semselect packages llama.cpp's native `POST /v1/systemone` endpoint for a pinned
decision model. The initial local profile uses textual state and bounded requests.
It keeps the upstream Choice, Score, and Noul wire shapes; a new semselect selection
protocol is unnecessary. This document proposes future SemStreams work. No sibling
repository is changed by this bootstrap.

## Query-classification baseline and next app work

**The agreed starting point is Qwen3.5-4B JSON plus improved code.** The latest
[120-case operation comparison](results.md#2026-10-07--query-classification-decision)
returned 111 correct operations for 4B and 95 for evaluation-local improved rules.
The tested specialists and smaller Qwens did not qualify as replacements. This
does not ship the rules, enable a code-plus-model policy, or change the packaged
Kev runtime. No sibling repository has been changed.

Proposed caller work should stay narrow:

1. Bring the demonstrated rule and binding fixes into SemStreams with failing
   caller regressions and representative fresh cases. Preserve ordinary search
   when a specialized operation does not apply; weak keyword guesses need checks.
2. Retain schema-constrained Qwen4B as the semantic reference. Any policy deciding
   when to invoke it or fall back needs its own quality and end-to-end latency
   measurement. Individual component results do not establish a hybrid's result.
3. Keep binding, validation, authorization, timeouts and actions in caller code.
   Evaluate errors and deferrals separately under the 250 ms median / 750 ms p95
   budget. The measured 4B Metal configuration currently misses that budget.

There is no established need for custom training, a specialist service or
serious-scale infrastructure. A small team is the current scope; load checks in
the bounded follow-ups were skipped after prerequisite failures. If actual
caller traffic exposes a remaining gap, choose the smallest targeted improvement
and validate it rather than treating this proposal as a provider-framework roadmap.

## Typed decisions can use an existing model

A narrow caller contract could separate a permitted label, explicit abstention,
model/method identity, and optional score evidence from transport failures. Start
with an existing caller that needs those distinctions. Qwen JSON can return a
label without a distribution; the measured one-token Qwen path can additionally
supply raw option scores. Neither needs a specialized model to make the result
typed. Keep absent evidence absent, and keep raw scores distinct from calibrated
correctness estimates.

The hardcoded `0.9` and generated confidence values in the inventory below deserve
clearer semantics. They do not justify replacing them with unvalidated native
probabilities or building a general provider layer. Compare a proposed Qwen-backed
contract at one real call site before committing to any integration. The native
client design below applies only if a specialized backend later earns its place.

## Responsibility boundary

semselect owns model acquisition, runtime packaging, request limits, readiness,
and operational visibility. It returns model evaluations. SemStreams owns caller
contexts, endpoint selection, timeout/fallback policy, and integration with its
classifier chain. Products supply their domain categories and decide what actions
are permitted. Neither a selected label nor a high probability authorizes an action.
This follows the framework/product boundary in
[SemStreams `openspec/project.md:39`](https://github.com/c360studio/semstreams/blob/1b1accf4ea4ea878c26236b5a9e6cb83d2d89d7a/openspec/project.md#L39).

Resolve authoritative facts and exact predicates in code first. For example,
transaction records and policy determine refund eligibility; a model may interpret
whether a free-text message requests a refund. The model cannot waive eligibility
checks. A semantic selector also needs an explicit unknown/abstention path:
returning the largest option score does not prove that any offered option applies.
Weak keyword guesses are not authoritative facts merely because they are coded.

The service needs no graph, NATS dependency, agent loop, or durable workflow state.
SemTeams remains a product-shell consumer of framework primitives; new inference
transport belongs in SemStreams, while product-specific routing stays in SemTeams
configuration and policy. See
[SemTeams `AGENTS.md:268`](https://github.com/c360studio/semteams/blob/ce22c961d30014c463a09f8f8a2a90044ee1a1cf/AGENTS.md#L268).

## Native operation and probability semantics

Use the pinned upstream
[`/v1/systemone` specification](https://github.com/ggml-org/llama.cpp/blob/6c59c40076c00eab49754dc955d7652d93f9e125/tools/server/README.md#post-v1systemone-typesafe-compatible-system-one-api).
Requests contain `state` and a `questions` object. Each question has a `type`,
`instructions`, and type-specific `criteria`. Responses preserve question IDs in
`answers` and report usage.

| Primitive | Native criteria | Native result to preserve |
|---|---|---|
| Choice | Option name to description or null | `choice`, all `probabilities`, `confidence` |
| Score | Ordered descriptions, lowest first | Expected level `score`, `legend`, all `probabilities`, `confidence` |
| Noul | Optional true/false descriptions | `noul`, the model probability of true |

Choice/Score probabilities sum to one over the supplied options. Model-file
temperatures affect these values; upstream does not guarantee calibration on the
caller's data. A Score may lie between levels. Preserve the upstream `confidence`
separately from the selected option's probability. It is not a measured correctness
rate. This is model output, not a generated natural-language claim of confidence.
Native usage reports prompt tokens and zero output tokens for these evaluations.

The adapter must retain the full distribution, including an explicit caller-supplied
`unknown` option. It must not manufacture missing probabilities from a winning
label, convert embedding similarity to probability, or replace missing model output
with arbitrary confidence values.

## Gaps in the current framework

The inventory below describes local SemStreams commit
`1b1accf4ea4ea878c26236b5a9e6cb83d2d89d7a`, inspected on 2026-10-05.

| Existing surface | What it supports | Integration gap |
|---|---|---|
| [`graph/query/classifier.go:13`](https://github.com/c360studio/semstreams/blob/1b1accf4ea4ea878c26236b5a9e6cb83d2d89d7a/graph/query/classifier.go#L13) | Query text to `SearchOptions` | No caller-supplied choices or probability result |
| [`graph/query/classifier_chain.go:8`](https://github.com/c360studio/semstreams/blob/1b1accf4ea4ea878c26236b5a9e6cb83d2d89d7a/graph/query/classifier_chain.go#L8) | Tier, intent, search hints, scalar `Confidence` | Scalar mixes keyword certainty and embedding similarity; no distribution or abstention outcome |
| [`graph/query/classifier_llm.go:15`](https://github.com/c360studio/semstreams/blob/1b1accf4ea4ea878c26236b5a9e6cb83d2d89d7a/graph/query/classifier_llm.go#L15) | Prompt to generated JSON search options | Returns hardcoded confidence 0.9 at line 91; cannot represent native decision output faithfully |
| [`graph/llm/client.go:31`](https://github.com/c360studio/semstreams/blob/1b1accf4ea4ea878c26236b5a9e6cb83d2d89d7a/graph/llm/client.go#L31) | Chat prompts, temperature, text and token usage | No System One request, typed selection, or probability distribution |
| [`processor/agentic-dispatch/intent_classifier.go:33`](https://github.com/c360studio/semstreams/blob/1b1accf4ea4ea878c26236b5a9e6cb83d2d89d7a/processor/agentic-dispatch/intent_classifier.go#L33) | `IntentClassifier` interface; intent and scalar confidence | Current implementation asks the model to generate confidence; fallback is `new_task` with 0.5, rather than an explicit abstention |
| [`model/wire/types.go:14`](https://github.com/c360studio/semstreams/blob/1b1accf4ea4ea878c26236b5a9e6cb83d2d89d7a/model/wire/types.go#L14) | Provider extras, response `Choice.Logprobs` at line 78 | Useful lower-level preservation, but no typed System One operation |

Changing an OpenAI endpoint URL is insufficient. The query component explicitly
constructs a chat client and `LLMClassifier` in
[`processor/graph-query/component.go:422`](https://github.com/c360studio/semstreams/blob/1b1accf4ea4ea878c26236b5a9e6cb83d2d89d7a/processor/graph-query/component.go#L422),
and the chain retains concrete classifier types. If native decision integration
is justified, it needs an explicit client branch or a narrow injectable classifier
seam. A label-only compatibility adapter cannot preserve a native distribution.

## Smallest proposed adapter

If native integration earns adoption, add one System One HTTP client, with an injected `http.Client`, base URL, and
per-call `context.Context`. Use Go request/response types mirroring the native
endpoint and typed methods for its three question kinds. The following signatures
are a proposal, not an existing SemStreams package or a new HTTP API:

```go
type SystemOneClient interface {
    Choice(context.Context, ChoiceQuestion) (ChoiceAnswer, error)
    Score(context.Context, ScoreQuestion) (ScoreAnswer, error)
    Noul(context.Context, NoulQuestion) (NoulAnswer, error)
}
```

Each question type carries textual state, instructions, and its native criteria.
Each answer retains native fields, returned model identity, and usage. Methods wrap
a single question into `questions` and post to `/v1/systemone`; batching can wait for
an actual caller need. Use explicit configuration to select this transport. Existing
[`query_classification` and `intent_classification` capability names](https://github.com/c360studio/semstreams/blob/1b1accf4ea4ea878c26236b5a9e6cb83d2d89d7a/model/registry.go#L22)
describe workloads; they do not currently select a System One transport. An operator
must not bind semselect into the present chat path and expect it to work.

Validate response question IDs/types, exact candidate membership, finite bounded
probabilities, normalization within numerical tolerance, and consistency between
the selected choice and a maximum probability. Reject malformed responses and retain
upstream error status where useful. Context cancellation and deadlines must bound
queueing, HTTP exchange, and decoding; do not store caller contexts in client structs.
Malformed input, unsupported models, readiness failures, and transport failures
remain errors for the caller to handle.

Before implementing this adapter, demonstrate a gap in the existing coded and
embedding-based retrieval paths. A working probability API does not justify adding
a classifier to a path that already meets its requirements. Existing scorecard
results and dormant classifier branches are relevant baselines; distinguish
query-strategy classification from retrieval ranking and answerability.
See the [code and RAG comparison audit](code-baselines-and-rag.md) for inspected
implementations, source pins, historical results and the proposed workload split.

If that comparison justifies integration, start with one measured call site. Map
permitted labels to existing search strategies in caller code and retain a separate decision
evidence field containing the distribution and upstream confidence. Do not overwrite
the meaning of the existing heterogeneous `Confidence` field. Follow the repository's
change process to decide whether to extend `ClassificationResult` or introduce a
parallel result; either requires explicit downstream handling and tests.

Callers may abstain when `unknown` wins, the highest probability is below a configured
threshold, or the first/second margin is too small. These are application policy,
not calibration claims. Fit thresholds on development data, confirm on separate
held-out data, and report coverage and error together. The caller chooses keyword fallback, another model, clarification,
or no action; semselect does not execute the routing decision.

## Integration acceptance checks

- Exercise the real HTTP client against malformed envelopes, omitted/extra candidates,
  mismatched question types, deadline expiry, unavailable upstreams, and native errors.
- Check that labels, full distributions, upstream confidence, model identity, and
  usage survive the adapter unchanged; use contract fixtures without calling them
  model-quality tests.
- With the pinned model, run the shared routing evaluation: clear/paraphrased inputs,
  ambiguous and multi-intent inputs, negation, unknowns, candidate reordering, and
  input attempting to influence classification. Reordering need not produce identical
  probabilities; measure sensitivity rather than asserting invariance.
- Verify caller abstention/fallback and that no selected label bypasses authorization.
  Report inference quality separately from transport correctness and model readiness.

## Sibling operating conventions

Follow the small inference siblings' Docker/Compose plus Task v3 packaging, env-based
configuration, health checks, and metrics. semembed uses nonroot runtime execution,
`SEMEMBED_*` configuration, a model-cache volume, request/error counters, and duration
histograms; see
[`semembed/Dockerfile:48`](https://github.com/c360studio/semembed/blob/7ceb5281c96b3664321f3f28c9d7f96acbb41843/Dockerfile#L48)
and [`semembed/src/main.rs:93`](https://github.com/c360studio/semembed/blob/7ceb5281c96b3664321f3f28c9d7f96acbb41843/src/main.rs#L93).
Use bounded metric labels and avoid logging request state by default.

seminstruct packages llama-server with a baked GGUF, `/health`, native `/metrics`,
runtime knobs, and `build`, `up`, `down`, `integration`, and smoke tasks. Its existing
baseline is llama.cpp `b8994` plus Qwen3-0.6B Q4_K_M, reasoning off, four slots, and
16384 total context tokens; see
[`seminstruct/Dockerfile:44`](https://github.com/c360studio/seminstruct/blob/7f9135a99cd27a6c63a2a60db5daeee9f5622be4/Dockerfile#L44),
[`Dockerfile:69`](https://github.com/c360studio/seminstruct/blob/7f9135a99cd27a6c63a2a60db5daeee9f5622be4/Dockerfile#L69),
and [`Taskfile.yml:46`](https://github.com/c360studio/seminstruct/blob/7f9135a99cd27a6c63a2a60db5daeee9f5622be4/Taskfile.yml#L46).
Its model download lacks an immutable revision/checksum, so a comparison must record
the actual artifact used. semselect can reuse the llama.cpp operational knowledge
while pinning the newer native-decision runtime and decision model independently.

## Classifier hints and production dispatch

The [query-classifier experiment](../eval/query-routing/README.md) evaluates the
actual library's options, including node bindings and aggregation fields. It
does not prove that a deployed request executes the corresponding search.
At inspected SemStreams `1b1accf4ea4ea878c26236b5a9e6cb83d2d89d7a`,
[`resolveStrategy`](https://github.com/C360Studio/semstreams/blob/1b1accf4ea4ea878c26236b5a9e6cb83d2d89d7a/processor/graph-query/graphrag.go#L633)
uses explicit strategy values and path/time/geography signals, but does not infer
aggregation or similarity dispatch from `aggregation_type` or `use_embeddings`.
The library's `SearchOptions.InferStrategy` has different precedence and no
non-test caller in that inspected checkout.

Before integrating a new classifier, independently test how its options travel
through gateway requests, graph-query reclassification, strategy resolution and
fallback. Preserve exact node bindings and test unsupported combinations.
The optional gateway BM25 path and graph-query's keyword/optional-LLM path are
different configurations. A correct library hint is not evidence of correct
end-to-end dispatch. This is proposed follow-up work; siblings remain unchanged.

The [measured classifier pilot](../eval/query-routing/README.md) identifies
concrete improvements for SemStreams owners to consider. They are not remaining
requirements of the semselect research phase or changes to SemStreams:

1. Add regressions for extracting `of` instead of a metric (R17/R21), then improve
   ordinary missing phrases such as “number of,” “smallest” and “highest.” Use
   these observed failures as development cases; reserve new cases for comparison.
2. Test negation, quoted operator words and ambiguity even when a keyword fires.
   Calling a model only after an empty code result cannot repair those false
   matches. A regex interpretation is not an authoritative fact.
3. Keep literal argument binding and cross-field validation in caller code.
   Preserve an unresolved path node instead of inventing one. Compare a stronger
   conditional JSON schema or explicit binding step under a new frozen protocol;
   do not silently repair this pilot's recorded invalid tuples.
4. Verify the actual gateway-to-dispatch path before claiming a user-visible
   search improvement. Better classifier hints alone do not demonstrate that
   aggregation, similarity or fallback executes correctly.

Only then test whether a model earns its additional calls on the remaining
semantic cases. The current always-model comparison does not measure a hybrid
policy's total accuracy, coverage or latency, and selects no production threshold.
