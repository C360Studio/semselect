# Actual-code query classification evidence

The code and Metal runs are complete and independently reviewed. CPU formal
inference is still running; it is not included in this checkpoint's results.
See the [worked examples](../../../eval/query-routing/README.md),
[validation](../../validation-query-routing.md) and
[execution review](../../../eval/query-routing/execution-review.md).

| Artifact | Contents |
| --- | --- |
| [summary-metal.json](summary-metal.json) | Primary scores, every paired correction/regression, case families, failures and raw-order changes |
| [code.tar.gz](code.tar.gz) | All 224 classifier rows and 100 raw Go jobs, including input, output and process receipts |
| [metal.tar.gz](metal.tar.gz) | All 128 formal responses, two excluded warmups, exact token preflights, runtime properties, artifact hashes, commands and complete logs |
| [metal-failed-attempt1.tar.gz](metal-failed-attempt1.tar.gz) | Original failed handover: zero inference calls, 128 `not_run` rows, clean Qwen exit, original execution manifest and all nine original source files |
| [execution-source.tar.gz](execution-source.tar.gz) | Exact frozen design/execution sources and source hashes at execution, including lockfiles and driver pins |
| [analysis-source.tar.gz](analysis-source.tar.gz) | Derived-report and runtime-audit helpers with their tests, as used for this checkpoint |
| [metal-runtime-audit.json](metal-runtime-audit.json) | Observed cache, token usage, effective batch/context, offload and successful cleanup checks |
| [offline-checks.log](offline-checks.log), [postprocess-checks.log](postprocess-checks.log) | Fixture/freeze validation, 44 Python tests across both logs, and Go driver race tests |
| [MANIFEST.json](MANIFEST.json), [verify.py](verify.py) | SHA-256 of compressed archives, every original uncompressed member and published derived files |

Archive prefixes are `code/`, `metal/`, `metal-failed-attempt1/`, `execution-source/`
and `analysis-source/`. Stored records keep their original local execution paths;
the archives preserve their bytes under these shorter prefixes. The source
archives retain repository-relative paths beneath their prefix. No model weights,
native executables or third-party dependency trees are included.

```sh
python3 docs/evidence/20261006-query-routing/verify.py
mkdir -p /tmp/semselect-routing-proof
tar -xzf docs/evidence/20261006-query-routing/code.tar.gz -C /tmp/semselect-routing-proof
tar -xzf docs/evidence/20261006-query-routing/metal.tar.gz -C /tmp/semselect-routing-proof
python3 eval/query-routing/report.py --code /tmp/semselect-routing-proof/code/result.json --models /tmp/semselect-routing-proof/metal/result.json --output /tmp/semselect-routing-proof/recomputed.json
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
memory was not measured. Hardware support established here is native Apple Silicon
Metal; CPU preflight is verified, but CPU formal results remain pending.
