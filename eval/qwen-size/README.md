# Smaller Qwen query-classification screen

This isolated CPU evaluation reuses the frozen specialist contract and scorer.
Read [plan.md](plan.md) for the fixed gates and execution limits. Model weights
and runtime provenance are pinned in `locks/`. It does not change the service.

**Completed result:** 2B obtained 84/120 correct operations and 1.7B 93/120;
both failed the quality and uncached CPU latency targets. The 2B sensitivity
check was budget-limited; both primary cohorts completed. Read the
[validation report](../../docs/validation-qwen-size.md),
[public evidence and replay notes](../../docs/evidence/20261007-qwen-size/README.md)
and [current baseline decision](../../docs/when-to-use.md#current-query-classification-decision).

Run offline checks with:

```sh
python3 -m unittest discover -s eval/qwen-size -p 'test_*.py' -v
```

For real inference, acquire the exact locked GGUF files at their recorded local
paths, and build the root Dockerfile `runtime` target at the pinned revision.
Verify the resulting image and binary against `locks/runtime.json`; another
architecture or changed build needs a separate validation record. Preserve the
historical specialist run referenced by `evaluate.py` (available in its evidence
archive). Start with a new, empty results directory:

```sh
python3 eval/qwen-size/evaluate.py prepare --run results/qwen-size/20261007/run
```

An independent reviewer must audit the frozen sources, payloads and lifecycle,
then place `review.json` in the run directory with the exact `freeze_sha256` and
true `independent`, `payloads_verified`, `scoring_verified`, `lifecycle_verified`
fields. The author must not self-approve.

Run `lifecycle.py --run <directory> --arm <arm> --stage <stage>` in the foreground.
Register its printed OS PID and log if using Enjoy. Run `development` for
`qwen35_2b`, then `qwen3_17b`; execute `evaluate.py select --run <directory>`;
run both arms in that same order for `primary`, then for `sensitivity`.
`evaluate.py report --run <directory>` writes a timestamped report. Run `load`
only for an arm whose report says it is required, then report again. Every
lifecycle verifies and records shutdown before the next can launch.

Source edits invalidate the freeze. Stage destinations are immutable; failures
retain attempted and unattempted denominators. Do not resume or replace scored
rows, retune prompts or change models after seeing results. This cohort was
already used: even a passing result requires fresh confirmation before adoption.
