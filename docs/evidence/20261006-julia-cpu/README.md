# Julia CPU diagnostic evidence

**Fast CPU operation, weak routing quality:** 24/48 correct at 158.64 ms median
and 169.73 ms p95, with a 530.15 MiB cgroup memory peak. This is the existing
24-case ticket fixture evaluated in two correlated option orders, not a fresh
held-out benchmark or community-evidence test.

- [Readable experiment and reproduction](../../../eval/julia/README.md)
- [Compact derived summary](summary.json)
- [All requests, responses, raw HTTP bytes and token preflights](results.json)
- [Request manifest frozen before launch](requests.json)
- [Artifact and source hashes](provenance.json)
- [Runtime identity, limits, memory and shutdown](runtime.json)
- [Original runtime log](runtime.log)
- [Host identity checked after the run](host.json)
- [Stopped-container removal and closed port](cleanup-confirmation.json)
- [Per-file SHA-256 manifest](sha256.json)

All 53 HTTP inference calls completed: one three-primitive smoke, one excluded
warmup, three excluded latency-gate calls, and 48 formal routing observations.
All planned prompts passed exact token parity and truncation checks before the
first inference call. Native usage matched those counts; output token counts
were zero. The log records zero cached tokens for all 55 native question tasks.
Formal prompts contain 195–215 tokens.

The 48 observations include 39 `unknown` selections: 16 expected fallbacks and
23 unnecessary deferrals. The remaining nine selections contain eight correct
routes and one wrong route. Seven of the 24 cases change label under reversal.
The two-second median gate was a spending limit, not a quality threshold.

Julia Q8_0 ran on the pinned Linux/ARM64 CPU image with four threads, a four-CPU
quota, a 2 GiB memory cap, one slot and 1,024-token context/batch/physical batch.
The run took 11.32 seconds after readiness including token preflight and
checkpointing. The cgroup peak was 555,900,928 bytes; this is not process RSS.
The container stopped with exit 0, no OOM and no cleanup errors. After preserving
the evidence, its ownership was checked again, the stopped container removed and
its loopback port verified closed.

`results.json`, `requests.json`, `provenance.json`, `runtime.log` and `source/`
preserve the original run bytes. `runtime.json` is a disclosed derivative:
unrelated Docker inventory was omitted while retaining its count of 16 running
containers and the original local file's SHA-256. All experiment runtime fields
are unchanged. The original remains in the ignored local results directory.
`summary.json` is recomputed from the rows; the manifest covers the recorded
files except itself. The snapshot in `source/` is the implementation actually run.

No result here establishes Metal, AMD64, CUDA, long-context quality, calibrated
confidence, maximum throughput or a fair speedup over earlier differently
configured Kev/Qwen runs. Thirty-five Julia offline tests and thirteen shared
evaluator tests passed; independent review approved the exact experiment before
launch and verified the resulting evidence afterward.
