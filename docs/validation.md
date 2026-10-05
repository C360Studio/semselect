# Bootstrap validation — 2026-10-05

**The local service works; these are small smoke results, not production benchmarks.**
The final CPU image loads the pinned model, serves native Choice/Score/Noul, and
passes the offline contract checks. The routing evaluation favors Kev over the
available small seminstruct baseline on these examples, at a substantial CPU cost.
It does not establish calibration, Jev parity, or robustness to arbitrary input.

## Hardware, versions and method

- Host: Apple M3 Pro, 36 GiB physical memory. Inference ran inside Docker Desktop
  **Linux/ARM64**, CPU only. Docker engine: 12 vCPUs, 24,895,582,208 bytes memory
  (~23.2 GiB). No Metal or CUDA inference was used.
- Both inference containers: 4-CPU quota, 4 runtime threads, one slot, 4096-token
  context and 8 GiB memory limit. They were evaluated sequentially; the other
  server was idle. The host was not reserved, so background/thermal effects remain.
- semselect: `Kev-4B-Q4_K_M.gguf`, repository revision
  `d924f2e2c3872da8b8aaf3eb4453b4126deceb79`, SHA-256
  `33ae6b18926502b2209a1bf7d3b61a350d65938441515c686bb87d226a19eff9`.
  llama.cpp source `6c59c40076c00eab49754dc955d7652d93f9e125`, with the Dockerfile's
  pinned base images and Ubuntu snapshot `20261005T000000Z`. The archive build's
  `--version` prints `0.5.0-dev (build 0, commit unknown)` because the tarball lacks
  Git metadata; the checked tarball hash, source pin and image label identify it.
- Baseline: cached `ghcr.io/c360studio/seminstruct` ARM64 image digest
  `sha256:297507bee8396438fe284f9ed285165c47a565b3f65be177030c473a4b90bdb9`.
  Served Qwen3-0.6B Q4_K_M, actual GGUF SHA-256
  `ac2d97712095a558e31573f62f466a3f9d93990898b0ec79d7c974c1780d524a`.
  The sibling source specifies `b8994`, but the **measured binary** reports
  `1 (aab6821)`. The immutable image and actual model hash identify the evaluated
  artifact; no claim is made that the current source reproduces that cached image.
- Dataset fixed before inference: [24 manual cases](../eval/routing-smoke.json),
  SHA-256 `81590b0d2e0773f49b7b1c524729acb96d37a74b29ca134c710701a0037b5d2a`.
  Each ran in original and reversed candidate order: **48 correlated observations**.
  One warmup was excluded from latency/quality metrics. No retries or post-result
  prompt tuning. Both models received identical category descriptions and policy.
  The baseline used constrained JSON enum generation, temperature zero and thinking
  disabled. Different model sizes, templates and runtimes remain confounders.

## Observations

| Measure | semselect Kev-4B | seminstruct Qwen3-0.6B |
| --- | ---: | ---: |
| Valid responses | 48/48 | 48/48 |
| Correct labels | **43/48 (89.6%)** | **24/48 (50.0%)** |
| Median latency | 9,877 ms | 293 ms |
| p95 latency | 12,858 ms | 369 ms |
| Unknown selections | 11/48 | 20/48 |
| Coverage after rejecting unknown | 77.1% | 58.3% |
| Error among accepted non-unknown labels | 5/37 (13.5%) | 15/28 (53.6%) |
| Candidate-order label flips | 2/24 pairs | 13/24 pairs |
| Cgroup lifetime peak memory | 3.54 GiB | 1.04 GiB |
| CPU counter delta, including warmup | 1,973.8 CPU seconds | 61.3 CPU seconds |
| Wrapped evaluation wall time, including warmup | 508.5 s | 16.1 s |

Memory is cgroup-accounted memory, including file cache and startup; it is **not
process RSS or an interval-only peak**. CPU counters include background activity
and measurement commands. The guard was separately observed around 8.45 MiB via
`docker compose stats`; that was a snapshot, not peak memory. Full Docker image
IDs, limits, engine metadata, raw counters and their caveats are preserved below.

Kev was about 34× slower by median latency. This supports retaining it as a useful
distribution-capable bootstrap, **not** treating it as an economical drop-in for
subsecond CPU routing. If subsecond response is required, evaluate a smaller
decision model or a different hardware target before adopting it. This run does
not prove that the scoring architecture alone caused the quality difference.

### Caller abstention on Kev probabilities

`unknown` always abstains. Additional thresholds apply to the selected candidate's
probability, not the native `confidence` field. Thresholds were declared before
the run, and are examples rather than recommended production settings.

| Minimum p(max) | Accepted / total | Coverage | Errors among accepted |
| --- | ---: | ---: | ---: |
| 0.0 | 37/48 | 77.1% | 5/37 (13.5%) |
| 0.5 | 34/48 | 70.8% | 2/34 (5.9%) |
| 0.7 | 29/48 | 60.4% | 2/29 (6.9%) |
| 0.9 | 2/48 | 4.2% | 0/2 |

Zero errors on two accepted examples is not evidence of production reliability.
Higher thresholds need not improve empirical conditional error on tiny samples.
The baseline has no native candidate distribution, so no corresponding probability
threshold results are fabricated.

Kev's five failures were the reversed billing/account multi-intent case, both
orders of the technical/account multi-intent case, and both orders of the
instruction-only injection. The last two selected `billing` with probabilities
**0.783 and 0.851**, even though the expected category was `unknown`. A 0.7
threshold did not stop them. Caller authorization and routing safeguards remain
necessary. Correct labels on the other injection examples do not establish general
injection resistance.

## What ran and passed

- `docker compose build`: final service and CPU runtime images built successfully
  on Linux/ARM64 using the frozen inputs. `task up`'s equivalent verification and
  bounded Compose startup succeeded; both services became healthy.
- Model download and a separate offline verification matched the exact size and
  SHA-256. Subsequent startup used the cache, without downloading a model.
- `GOCACHE=/tmp/semselect-go-cache task check`: Go contract tests with race detector,
  13 Python evaluator tests, formatting, `go vet`, and Compose validation passed.
  Contract tests cover admission/429, context cancellation and deadlines,
  unavailable readiness, malformed/oversized requests, upstream response bounds,
  redirects, and exact response/candidate-order passthrough. They use a stub HTTP
  transport and provide no model-quality evidence.
- An independent review found case-insensitive JSON validation could disagree with
  native JSON parsing. A failing regression demonstrated the casing/duplicate-key
  bypass and null Noul descriptions before the strict parser fix; all now pass.
  Review also corrected graceful shutdown and write-deadline handling.
- `python3 scripts/smoke.py`: real Choice, Score and Noul inference passed on the
  final image. [Recorded response](evidence/primitives.json). Only Choice received
  the routing quality evaluation; Score/Noul task accuracy remains unmeasured.
- A native over-context probe with 16 candidates containing 512-byte descriptions
  returned HTTP 400: `request (4189 tokens) exceeds the available context size
  (4096 tokens)`. The input fit the guard's byte/candidate bounds; it was rejected
  by the runtime instead of silently shifted or truncated.
- Both 48-request real evaluations completed with no HTTP/shape errors and exit
  zero. **That exit status does not mean every classification was correct.**
- Resource wrapper validation exercised unavailable metrics, nonzero command exits,
  timeout reporting, counter parsing and descendant cleanup. Real cgroup counters
  were successfully collected for these runs.

## Evidence and reproduction

- [Kev full evaluation rows and summary](evidence/semselect.json)
- [Seminstruct full evaluation rows and summary](evidence/seminstruct.json)
- [Kev Docker/cgroup record](evidence/semselect-resources.json)
- [Seminstruct Docker/cgroup record](evidence/seminstruct-resources.json)

Follow the README quick start, then:

```sh
python3 scripts/measure.py --container semselect-runtime-1 \
  --output results/semselect-resources.json -- \
  python3 scripts/evaluate.py --backend semselect --url http://127.0.0.1:8084 \
  --output results/semselect.json --hardware 'Record your actual hardware and limits'
task baseline:up
python3 scripts/measure.py --container semselect-baseline-1 \
  --output results/seminstruct-resources.json -- \
  python3 scripts/evaluate.py --backend seminstruct --url http://127.0.0.1:8083 \
  --model qwen3-0.6b --output results/seminstruct.json \
  --hardware 'Same host and limits; run after semselect evaluation finishes'
```

Native macOS/Metal, CUDA, Linux/AMD64 builds/performance, sustained concurrency,
production traffic, broad calibration, multilingual inputs, larger models and
Score/Noul accuracy studies were **not run**. Guard cancellation reaches the
upstream HTTP request in contract tests; immediate interruption of native compute
was not proved. No changes were made to SemStreams, SemTeams, semembed or seminstruct.
