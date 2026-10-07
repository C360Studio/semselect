# Bounded specialist evidence — 2026-10-07

Verdict: **keep the baseline**. The
[validation report](../../validation-specialist-intent.md) explains the completed
negative result, selected configurations, resource limits and caveats.

- [Evidence archive](evidence.tar.gz): frozen source and fixtures, 1,568 prepared
  payloads, development selection, raw primary/sensitivity responses, scoring,
  resource and shutdown records, setup failures, exact dependency closure,
  model/tokenizer provenance, independent reviews and test/runtime logs.
- [Manifest](manifest.json): SHA-256 and byte count for every archived file,
  archive SHA-256, and explicit binary/cache omissions.

The archive preserves repository-relative paths. Its run root is
`results/specialist-intent/20261007-execution/`. The immutable generated report
is `run/reports/20261007T153847.325600Z/summary.json`; its SHA-256 is
`b5da8a43d8d76b1e9352e5db387e7382c7eb9620ab80faac342f6c3a2c815d6c`.
Preparation freeze SHA-256 is
`5374ff31adda4938c4d02ceb1dc09633d6518bedd6911173fa2c9d211166177a`.
`setup/final-review.md` and `.json` contain the independent result audit;
`setup/execution-summary.json` includes all-arm sensitivities and load omission.
`setup/final-shutdown-verification.json` verifies all 13 owned runtime lifecycles
stopped after the evaluation.

Large weights, runtime binaries and wheel caches remain excluded from Git.
Their immutable identities and verification records are retained. The specialist
CPU image was
`sha256:7db4b05ba0eaf9d677935ccc93f6396c1693c94971bc8405d5fdc9ebee6f44b5`;
semembed used
`sha256:7972174f8e3462fd38bf3ad95d94870f406bb3d1350e0ea3280ce002cb2e09b2`.
The [harness instructions](../../../eval/specialist-intent/README.md), frozen
source snapshot and run-owned lifecycle helpers preserve reproduction details.
Use a new output directory for another run; do not overwrite this evidence.

Earlier `eval/specialist-intent/validation.md` records implementation-only
checks before this execution and remains historical. Actual model quality is
established only by the completed inference evidence here. Code, labels,
thresholds or runtime changes require a new explicit freeze and review.

## Publication privacy redactions

The public archive is a derivative of the locally retained original. Eighteen
lifecycle launch/result records replace unrelated host process names/executable
paths with `<redacted>`, preserving PID, CPU, RSS and ordering. Twelve of those
records also replace unrelated prelaunch container details with the running
container count. Own runtime inspect records, original reviews, frozen sources,
requests, model responses, tokens, selection and quality/resource results remain
unchanged. Original review records refer to the original evidence; the public
derivative has separate integrity checks.

The [redaction manifest](redactions.json) records exact fields and original/public
archive and member hashes. The public archive contains 536 files and is
6,603,726 bytes; its SHA-256 is
`01cb07b4cb7d714a9bd0f94452d28af0d65afb4898652c685f8587b01fd5f4e7`.
The original archive remains local, with its original checksum recorded in that
manifest. These edits remove unrelated workstation details, not measurement rows.
