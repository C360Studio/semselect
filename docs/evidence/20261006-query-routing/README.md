# Actual-code query classification evidence

The code, Metal and CPU Qwen runs are complete and independently reviewed. CPU
Kev was intentionally stopped at the user's request after three completed formal
calls, one interrupted call and 60 unattempted cases. It supplies partial
compatibility/latency observations, not cohort accuracy. All owned runtimes are
stopped; the original interrupted result is preserved.
See the [worked examples](../../../eval/query-routing/README.md),
[validation](../../validation-query-routing.md) and
[execution review](../../../eval/query-routing/execution-review.md).

| Artifact | Contents |
| --- | --- |
| [summary.json](summary.json) | Combined results, assessed/unassessed cases, paired corrections/regressions, case families and hardware/order differences; incomplete CPU Kev has no reported accuracy |
| [summary-metal.json](summary-metal.json) | Original code/Metal checkpoint, preserved unchanged |
| [code.tar.gz](code.tar.gz) | All 224 classifier rows and 100 raw Go jobs, including input, output and process receipts |
| [metal.tar.gz](metal.tar.gz) | All 128 formal responses, two excluded warmups, exact token preflights, runtime properties, artifact hashes, commands and complete logs |
| [cpu-partial.tar.gz](cpu-partial.tar.gz) | Complete CPU Qwen, partial CPU Kev, all planned rows, warmups, preflights, exact response bytes, runtime logs and shutdown records |
| [operator-stop.json](operator-stop.json) | Explicit stop authorization, original result hash, observed counts and shutdown outcome; also preserved in the CPU archive |
| [metal-failed-attempt1.tar.gz](metal-failed-attempt1.tar.gz) | Original failed handover: zero inference calls, 128 `not_run` rows, clean Qwen exit, original execution manifest and all nine original source files |
| [execution-source.tar.gz](execution-source.tar.gz) | Exact frozen design/execution sources and source hashes at execution, including lockfiles and driver pins |
| [analysis-source.tar.gz](analysis-source.tar.gz) | Original code/Metal reporting helpers and tests, preserved unchanged |
| [analysis-source-final.tar.gz](analysis-source-final.tar.gz) | Final combined-report and runtime-audit helpers with tests, including incomplete-run reporting |
| [metal-runtime-audit.json](metal-runtime-audit.json) | Observed cache, token usage, effective batch/context, offload and successful cleanup checks |
| [cpu-runtime-audit.json](cpu-runtime-audit.json) | CPU token/cache observations and shutdown outcomes, including Kev exit 137 during bounded stop, without OOM |
| [offline-checks.log](offline-checks.log), [postprocess-checks.log](postprocess-checks.log) | Fixture/freeze validation, 44 Python tests across both logs, and Go driver race tests |
| [partial-report-checks.log](partial-report-checks.log) | Five passing report tests, including one new regression for incomplete runs; 45 distinct Python tests across the checkpoints |
| [partial-latency-regression-before.log](partial-latency-regression-before.log) | Preserved failing regression before suppressing incomplete-cohort median/percentile latency |
| [MANIFEST.json](MANIFEST.json), [verify.py](verify.py) | SHA-256 of compressed archives, every original uncompressed member and published derived files |

Archive prefixes are `code/`, `metal/`, `cpu-partial/`, `metal-failed-attempt1/`,
`execution-source/`, `analysis-source/` and `analysis-source-final/`.
Stored records keep their original local execution paths;
the archives preserve their bytes under these shorter prefixes. The source
archives retain repository-relative paths beneath their prefix. No model weights,
native executables or third-party dependency trees are included.

```sh
python3 docs/evidence/20261006-query-routing/verify.py
mkdir -p /tmp/semselect-routing-proof
tar -xzf docs/evidence/20261006-query-routing/code.tar.gz -C /tmp/semselect-routing-proof
tar -xzf docs/evidence/20261006-query-routing/metal.tar.gz -C /tmp/semselect-routing-proof
tar -xzf docs/evidence/20261006-query-routing/cpu-partial.tar.gz -C /tmp/semselect-routing-proof
python3 eval/query-routing/report.py --code /tmp/semselect-routing-proof/code/result.json --models /tmp/semselect-routing-proof/metal/result.json /tmp/semselect-routing-proof/cpu-partial/result.json --output /tmp/semselect-routing-proof/recomputed.json
```

The derived report's source path strings depend on extraction location, but its
source hashes and outcome metrics must match. Verify the archived source hashes
before changing a future protocol or model lock. Source commit is `d098bb4` plus
the archived execution supplement. The original and amended execution manifests
retain the immutable design freeze. The historical `heldout_executed=false` in
that design records its pre-execution state; it is not today's run status.

These are public synthetic queries and pinned public training examples. They do
not include user traffic or captured private retrieval content. Invalid outputs
are retained without repairing their selections. Memory quotas are known; peak
memory was not measured. Hardware actually tested is native Apple Silicon Metal
and Linux/ARM64 CPU Docker. CPU Qwen completed its matrix; CPU Kev completed only
three formal calls. These observations do not establish AMD64 or CUDA support.
