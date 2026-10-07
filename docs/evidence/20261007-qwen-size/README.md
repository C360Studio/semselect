# Smaller Qwen evaluation evidence

The [validation report](../../validation-qwen-size.md) explains the results.
Both models completed all 120 primary cases and failed quality and latency
targets. The 2B remap check is incomplete because its fixed inference budget
expired. Neither candidate advanced to load testing.

- [Original machine-readable summary](summary.json)
- [Independent original-result review](final-original-results-review.json)
- [Independent post-run harness-fix review](post-run-r3-review.json)
- [Evidence archive](evidence.tar.gz)
- [Archive and member hashes](manifest.json)

The archive contains **291 files**, 38,788,735 uncompressed bytes, compressed to
2,270,937 bytes. Its SHA-256 is
`e191912891f4b9fc557b8ac81d0327714cc870d62c52d3954c994ccdec8fed32`.
Every archived member was reread and verified against the manifest after packing.
The original summary SHA-256 is
`00c486690d885435b1aad8f09a98a1ca16bb31c399ed29b6c2bf0dfb75237d22`.

Included evidence:

- Both preparation snapshots, approved freeze and 912 exact payload envelopes.
- All 618 planned rows: 598 valid responses, one budget-deadline error and
  19 unattempted cases, including the excluded warmups and feasibility calls.
- Raw responses and hashes, full-input token manifests, frozen development
  selection, primary metrics, sensitivities and paired historical comparisons.
- All six lifecycle records, startup/final resources, complete runtime logs,
  shutdown verification, model/runtime locks, setup/build evidence and licenses.
- Independent plan, implementation and result reviews; failing and passing
  regression logs; the separate post-run eligibility fix and its hashes/diff.
- Exact graded sources under `run/source`, current corrected harness sources,
  validation prose and a verified offline replay record.

GGUF weights, Docker images and Python bytecode are excluded. Their model,
runtime, dependency and license identities remain recorded. No new inference,
CPU throughput, AMD64, CUDA or small-model Metal result is implied by packaging.

## Publication privacy redactions

This is a public derivative of the locally retained original archive. In twelve
lifecycle launch/result records, unrelated host process names/executable paths
were replaced with `<redacted>`; PID, CPU, RSS and ordering remain intact.
Prelaunch Docker snapshots were empty. The [redaction manifest](redactions.json)
records original/public archive hashes, changed-member hashes and exact field
scope. Original review records refer to the original evidence. No model response,
token, source freeze, selection, result metric or runtime-limit evidence changed.
The published derivative was checked separately and its offline report replay
still reproduces the original metrics. The original archive is retained locally;
its SHA-256 is recorded in the redaction manifest, not substituted for the public
archive checksum above.

## Reading and replaying the record

Archive paths are relative to the repository root. Extract into an empty
directory to inspect the record without overwriting current work. The run is
under `results/qwen-size/20261007/run`; the original report is
`reports/20261007T173251.585278+0000/summary.json` within it.

Current harness sources include the post-run eligibility correction, so they
intentionally fail the original source-freeze check. For offline replay, copy
the **contents** of `run/source` into a fresh temporary root, then copy the entire
run into `results/qwen-size/20261007/run` within that temporary root. Invoke that
root's `eval/qwen-size/evaluate.py report --run <copied-run>`. This reconstructs
the exact graded sources without modifying the current checkout. The recorded
offline replay reproduced every original report field except `created_at`.

New inference requires a new freeze and review, exact model/runtime artifacts
and the historical specialist record referenced by preparation. See the
[harness](../../../eval/qwen-size/README.md) and
[historical evidence](../20261007-specialist-intent/README.md). The already-used
authored cohort is a comparative screen, not fresh deployment qualification.
