# Actual SemStreams query classification: validation

This experiment compares **classification hints**, including their arguments.
It does not execute graph queries or test answer synthesis. The short
[worked result](../eval/query-routing/README.md) explains the practical choice;
this record preserves the conditions and limitations.
The [evidence index](evidence/20261006-query-routing/README.md) links lossless
compressed runs, derived metrics, source snapshots and a hash verifier.

## Contract and baseline

The isolated Go driver imports `github.com/c360studio/semstreams/graph/query`
at `v1.0.0-beta.160`. The pinned classifier files match the inspected local
SemStreams checkout. Sibling repositories were not changed. The three arms are
the actual keyword chain, that chain with configured BM25 at the upstream default
threshold `0.7`, and the same BM25 examples at development-selected `0.9`.
BM25 only runs when the keyword tier produces no explicit hints; it copies an
example's options instead of extracting new arguments.

Twenty training examples and 18 development cases preceded the 32 authored
held-out cases. Nine BM25 thresholds were tried on development only. Keyword
and the best BM25 configurations tied at 12/18; the frozen tie rule selected
keyword as the designated comparator and `0.9` as the BM25 threshold. Primary
code cases each use a fresh process; two persistent orders separately check
BM25's mutable query statistics. All 224 planned code records are preserved.

The models receive the same query, examples, task instructions and bounded
vocabularies. Qwen returns one schema-constrained JSON object. Kev answers three
native Choice questions in one HTTP request: operation, node and field. A correct
operation with the wrong argument is wrong. Incompatible combinations, errors
and invalid responses stay in the denominator. Empty options mean no specialized
hint, not an abstention. R32's path intent without a named node is a correct
partial classification; a caller must resolve that node before execution.

The authors inspected the implementation, and related phrase templates cross the
development/test split. These are 32 inspectable examples, not production traffic
or a broad generalization test. Reversing case and candidate order provides a
sensitivity check, not 32 additional independent examples. Literal rules,
statistical example matching, and models remain separate categories; this is
not a fusion-retrieval comparison.

## Execution and preservation

Design commit: `d098bb4`. Design freeze SHA-256:
`daaf3b6924df1644e019e59e4dcf5fb9bce40dad26ccecec1d942df7c0e15e13`.
The design's historical `heldout_executed=false` records its state at freezing;
it is deliberately unchanged. A separately reviewed execution manifest pins the
runner, runtime adapter, tests and reused helpers. Exact request bytes are checked
against the frozen manifest before every call. Responses retain their original
bytes, including invalid output. No response-dependent retries or prompt changes
were made.

The first Metal attempt passed Qwen's 65 context preflights, then failed before
starting Kev because a bind-based check mistook TCP TIME_WAIT for a listener.
It made **zero inference requests**: all 128 formal rows remain `not_run`, and
Qwen exited cleanly. Its original execution manifest and all nine source files
are retained. Three failing regressions preceded a strict connection check;
only connection refusal permits launch. Independent read-only review approved
the amendment. Prompts, labels, grading and the resource profile did not change.

The amended execution SHA-256 is
`8451ff1e13bb4962c291d56cec82f5373efb2ef5ef98b5b112e03914d92edcaf`;
the original is
`0f59a0fbbc1368e2d7278effc123fcc9a94806d76ca8618e2233d12a09e65b7a`.
The code run used the original manifest; its driver and grading are identical
under the amendment, which changes only model-runtime startup and its tests.

## Runtime conditions

Both model families must pass all exact-request preflights on a hardware path
before either generates. Each then receives one excluded D01 warmup, 32 primary
normal-order calls and 32 calls with reversed cases/candidates. The same pinned
Kev-4B and Qwen3.5-4B Q4_K_M GGUF bytes and llama.cpp revision
`6c59c40076c00eab49754dc955d7652d93f9e125` are used on both hardware paths.
Lockfiles, actual artifact hashes, commands, properties and logs accompany the run.

- Apple M3 Pro / 36 GiB, native macOS/ARM64 Metal; 33/33 layers offloaded.
- CPU inference: the same laptop's Docker Desktop Linux/ARM64, immutable image
  `sha256:a24e5693f8e9634ded5a8b29a438ad87035a8e23a5ac182485d5eaee13c85db1`.
  Four CPU quota/threads, 8 GiB memory and memory-plus-swap limit, no GPU requests,
  read-only root/model mount and loopback-only published port.
- Four threads, one slot, 4096-token context, no context shifting; requested
  batch 1024 / microbatch 512. Kev's embedding mode clamps its effective batch
  to 512. The actual maximum decision tail is 255 tokens, so it still fits.
- Prompt caching disabled with `--no-cache-prompt --cache-reuse 0 --cache-ram 0`.
  Actual response/log observations must support any cache claim.
- Request bounds: 240 seconds in the Kev guard, 245 seconds in the HTTP client,
  1 MiB response limit. Context preflight reserves 128 generated tokens for Qwen.
- Qwen calls llama.cpp directly. Kev passes through the native semselect guard;
  that guard runs on the host even for CPU inference, outside the Docker quota.

Latency covers each complete HTTP call plus local response validation. Kev's
three native questions are included. Startup, warmup and preflight are excluded.
Code classification, constructor and cold-process times are recorded separately;
the cold-process time includes disk persistence. Code ran on Darwin, not in the
Linux CPU container. These timings do not isolate model architecture or establish
a matched service-level speedup. The shared laptop's thermal state is uncontrolled.
Configured memory limits are not measurements of peak memory use.

## What the Metal totals contain

| Primary case family | Cases | Actual keyword / either BM25 | Qwen JSON | Kev |
| --- | ---: | ---: | ---: | ---: |
| Literal | 10 | 10 | 7 | 9 |
| Ordinary text | 3 | 3 | 1 | 1 |
| Paraphrase | 14 | 4 | 11 | 12 |
| Negation | 2 | 0 | 1 | 1 |
| Quoted mention | 1 | 0 | 1 | 0 |
| Ambiguity | 1 | 0 | 1 | 0 |
| Missing binding | 1 | 1 | 1 | 0 |

Qwen corrects R08, R14, R17, R20, R21, R23, R24, R26, R27, R28, R30 and
R31, while regressing R01, R03, R04, R05, R06, R13 and R22. Kev corrects
R08, R14, R17, R18, R20, R21, R23, R24, R26, R27 and R28, while regressing
R01, R03, R04, R05, R06 and R32. Pairing is identical against each code arm,
not a per-case selection of the weakest baseline.

Qwen's eight primary invalid tuples are all parseable JSON satisfying the frozen
key/type/enum schema. It selects an inappropriate node on R01, R03, R04, R05,
R06, R13, R22 and R29; on R13/R22 that specific node is absent from the query.
Its remaining valid error, R18, chooses sum instead of arithmetic mean. Kev's
six invalid tuples are R03, R04, R05, R06, R29 and R30. Its valid errors are an
unrequested path on R01, premature average on R31, and invented node on R32.
The protocol explicitly keeps similarity references in the original query;
`path_start_node` is not an argument for similarity.

Reverse order preserves Qwen's 23/32 but makes R18 invalid instead of a valid
wrong sum. Kev becomes 22/32: R18 becomes invalid, R29 changes between two invalid
selections, and R30 becomes a valid but incorrect average. Raw selections change
on one Qwen and three Kev cases. Among pairs valid in both orders, options do not
change (23 Qwen / 25 Kev pairs); that restricted observation is not order invariance.

The stored `operation_correct` secondary metric requires a valid tuple first.
It therefore counts operation-correct **valid tuples**, not correct raw operation
heads regardless of argument errors. Exact complete hints remain the primary
measure; original selections are available for inspecting argument failures.

## Reproduce

Use the pinned caches and native build described in the
[driver](../eval/query-routing/driver/README.md) and
[laptop guide](laptop-and-gpu.md). The historical driver binary hash is specific
to the recorded Darwin/ARM64 build; another build needs a new, reviewed execution
record rather than silently substituting its hash in the old evidence.

```sh
task routing:validate
task routing:test
python3 eval/query-routing/runner.py --stage validate
python3 eval/query-routing/runner.py --stage code --output results/query-routing/new-code
python3 eval/query-routing/runner.py --stage models --hardware metal --output results/query-routing/new-metal
python3 eval/query-routing/runner.py --stage models --hardware cpu-docker --output results/query-routing/new-cpu
python3 eval/query-routing/report.py --code results/query-routing/new-code/result.json --models results/query-routing/new-metal/result.json results/query-routing/new-cpu/result.json --output results/query-routing/new-summary.json
```

Output directories must be new. Runs are sequential and take the repository's
native-operation lock. The runner owns only its new processes and UUID-labeled
containers, records their shutdown, and retains stopped containers and logs.
Existing unrelated containers are left alone.
