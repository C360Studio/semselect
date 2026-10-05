# Isolated live SemSource preparation

This prepares a real indexed documentation corpus at SemSource commit
`4093d3ce421371f4a99d7168e372552899bf6795` / SemStreams `v1.0.0-beta.160`.
The sibling checkout is read only. All generated files, containers and state are
owned by this experiment. The six source files (63,787 bytes) come from the
already pinned public snapshots in `eval/answerability/heldout/source`; questions,
gold labels and code are excluded. Existing passage-level labels do **not**
automatically apply to the resulting community summaries.

Run from the semselect root:

```sh
python3 eval/synthesis/live/prepare.py
python3 eval/synthesis/live/build.py
```

Preparation resolves the sibling checkout as `ROOT.parent / "semsource"`.
The builder resolves its module cache using `go env GOMODCACHE`, with an optional
`SEMSELECT_GOMODCACHE` override. These portability edits were validated without
rerunning the experiment; the published evidence preserves the earlier launcher
snapshots and distinguishes them from the current source.

Both commands refuse to overwrite their evidence. Preparation writes an archive,
source/corpus hashes, licenses, configuration and a Compose plan to `.synthesis/`.
The compiler uses a cached digest-pinned Linux/ARM64 Go 1.26.6 image with CGO,
network disabled, read-only source/module cache, 4 CPU and 8 GiB limits, and
temporary compiler caches. The output binary, command, elapsed time, exit status
and SHA-256 are retained. No download or service launch occurs in preparation;
the build starts only its temporary compiler container.

The generated `.synthesis/config/compose.json` is the service launch plan. It
uses only cached immutable images, a separate internal Docker network, bounded
resources and experiment-owned NATS data. SemSource runs in the same Bookworm
image used to compile it. Host ports are loopback only: HTTP `48080`, NATS
`44222`, monitoring `48222`, and seminstruct `48083`. WebSocket and GraphQL stay
inside the SemSource container. Check these ports and the project name for
collisions immediately before launch. Nothing refers to existing qfl services.

## Launch and readiness contract

Launch is a separate, explicit step after review. Use this Compose file and its
project name exclusively; never run a sibling stack's `up` or `down` commands.
Start NATS, semembed and seminstruct first, wait for actual readiness, then start
SemSource. Images have `pull_policy: never` and restart is disabled so failures
remain visible. The seminstruct configuration matches the shipped tier-2
0.6B smoke defaults: alias `seminstruct`, reasoning off, context 16,384, four
parallel slots, two threads and two CPU. This proves wiring, not production
answer quality; the shipped overlay recommends 8B for quality work.

Retain the rendered Compose configuration and `docker inspect` identity/resource
state at launch. Save health responses and container logs with UTC timestamps.
Bound service health waits to 120 seconds and indexing/community waits to 300
seconds; a timeout is a recorded failure, not a reason to continue scoring.

Reuse the source scorecard's readiness predicate at
`GET http://127.0.0.1:48080/source-manifest/status`: `phase == "ready"`,
`index.ready == true`, and `embedding.ready == true`. Also require positive
entity counts. These signals do not establish community readiness. Record
the `COMMUNITY_SUMMARIES` KV records and their `status` values
(`llm-enhanced`, `llm-failed`, or absence). Capture every planned query once,
including legitimate empty `community_summaries`, errors and timeouts; never
select cases by whether they reach synthesis. An answer with `answer_model` empty
is a template, not an LLM answer; retain `degraded` and `degraded_reason`.

## Exact collection and comparison boundary

The production MCP `graph_search` handler sends
`{"query":"...","summarize_threshold":1}` to `graph.query.searchGraph`.
Collect the raw NATS response with that same request, including `count`, complete
`community_summaries`, representative entity IDs/types/labels/tags/relevance,
answer/model and degradation fields. MCP projects the response, so its human
result alone is insufficient provenance for exact synthesis replay. Snapshot
the relevant community records too; summarization is asynchronous.

Freeze the response before evaluating gates. The unchanged exported beta.160
`NewLLMAnswerSynthesizer` receives the original query, these exact
`CommunitySummary` objects and response `count`, using the same endpoint/model.
It preserves the existing system/user prompts, 500-token cap, temperature 0.3,
15-second deadline, retries and template fallback. Root's evaluation harness
owns this invocation and paired no-gate / Qwen-gate / Kev-gate measurements.
Gate prompts must not replace the generator prompt. Include all gate and
generation wall time actually incurred; record skipped generation explicitly.

This is **live retrieval plus an exact synthesis-seam experiment**. The upstream
stack has no deployed gate, so it cannot establish an integrated end-to-end
gated pipeline. Collection already invokes production synthesis; keep that
acquisition cost separate from scored replay latency. Identical evidence and
generator settings allow an honest comparison of gate utility at this seam.

## First acquisition attempt

The first launch failed before SemSource or any query ran: the cached semembed
image lacked its model files and attempted a download on the internal network.
NATS and seminstruct started, but this Docker environment left their declared
loopback port mappings inactive on that network. All three owned dependencies
were stopped; no unrelated services were changed. The unmodified attempt is
retained under `.synthesis/attempts/01-offline-no-cache/`, including all 13
planned cases marked `blocked_before_capture`, not scored as empty evidence.
Changing model provisioning or network configuration requires a separately
recorded attempt, preserving this failure.

## Pinned-cache remediation

`fetch_embed.py` provisions exactly five runtime files at Snowflake revision
`e596f507467533e48a2e17c007f0e1dacc837b33` (133,807,972 bytes) plus its model card.
It validates declared sizes, the ONNX LFS SHA-256 and the small files' Git blob
IDs, computes SHA-256 for every file, and fixes the Rust cache's `refs/main` to
that revision. The resulting read-only cache is experiment-owned. The actual
cached semembed binary identifies fastembed 5.13.4 / hf-hub 0.5.0; these use
`FASTEMBED_CACHE_DIR`/`HF_HOME` and `HF_ENDPOINT`. The retry sets both cache
variables to `/model-cache` and the endpoint to `http://127.0.0.1:9`, so a missing
file fails locally instead of fetching remotely. Python Transformers offline
environment variables are not relied upon for this Rust service.

Attempt 2 uses a distinct owned project and fresh NATS state at
`.synthesis/attempts/02-pinned-embedding-cache/`. Its dedicated bridge permits
the requested loopback bindings; actual `NetworkSettings.Ports` must verify
them. The original stopped containers and first-attempt evidence remain intact.

`acquire.py` accepts `SEMSELECT_ACQUISITION_DIR` and
`SEMSELECT_ACQUISITION_COMPOSE` paths inside `.synthesis/` for separate attempts.
Its subcommands are `preflight`, `wait-deps`, `wait-source`, `snapshot`, `capture`,
`state` and `logs`; it never starts the four-service stack. The standalone
`capture.go` is compiled explicitly against the archived source module and
cached nats.go; its build-ignore tag keeps it outside the semselect service's
package/dependency graph. Every query has a 60-second client bound, one attempt,
a full raw body when available, and a status record. Failed transport/server
responses cannot be treated as legitimate empty-evidence synthesis inputs.

Attempt 2 indexed six documents plus 86 chunks (92 entities) and acquired all
13 planned IDs once: 12 full responses carried three community summaries each;
S06 returned a temporal-strategy/no-responder error.
The 70,055-byte `inputs.jsonl` has SHA-256
`9939d6776c1df7ce071145cf43d9ada3f422fef092f0b74d31b3be859d36e1b3`.
Its matching status manifest is mandatory: S06's placeholder must remain a
pipeline failure in the 13-case denominator, never an empty-evidence success.
Source and NATS stopped with exit 0. Semembed required the bounded stop's final
kill (exit 137, `OOMKilled=false`); the healthy owned seminstruct remained
available at `http://127.0.0.1:48083/v1` for formal replay. These are acquisition
and cleanup observations, not gate-quality results.

The [published acquisition evidence](../../../docs/evidence/20261005-synthesis-acquisition/README.md)
includes raw requests/responses, KV/readiness records, scoped runtime/model
provenance, logs, licenses and the original setup failure. Its manifest separates
77 byte-preserved originals from six explicit projections and source snapshots;
unrelated Docker inventory, binaries, model weights and NATS databases remain
ignored. Run its `verify.py --local` to check copied bytes against local originals.
