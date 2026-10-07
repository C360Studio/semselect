# Implementation validation — 2026-10-07

Implementation checks ran on native Darwin ARM64. No Linux ARM64 specialist
model, ONNX embedding runtime, Metal Qwen, real tokenizer cohort, cgroup or
concurrency performance result is claimed here.

`task specialist:test` passed the pinned SemStreams Go driver race checks and
75 Python tests, with no skipped Python checks in the completed run. The native
keyword/BM25 bridge used `/tmp/semselect-specialist-query-driver`. HTTP tests
exercised a real short-lived loopback listener; process deadline tests started
and reaped an owned local child. Model and Docker observations are test doubles.

The initial sandboxed task run failed two HTTP tests at listener creation with
`PermissionError: [Errno 1] Operation not permitted`, and Go reported a module
stat-cache write restriction. The same task passed after the provider approved
local socket/cache access. This was an environment restriction, not a model
compatibility result. No model server or long-lived process was started.

Meaningful failures were observed before the corresponding corrections:

- Runner tests reproduced `KeyError: sensitivity_ids` and the missing
  `adapters.decode_embedding` integration. The manifest and actual semembed
  adapter interfaces now match.
- Scoring tests reproduced missing native-output and family-cluster fields;
  native SearchOptions now grade separately from the shared binder, and repeated
  families receive clustered intervals.
- A runner regression reproduced missing persistent arm-stop handling.
- Cancellation regression reproduced an in-flight request labeled unattempted;
  it now remains an attempted error while untouched jobs stay unattempted.
- The supervisor regression rejected its second allowed setup attempt because
  launch metadata was counted as an attempt; completed attempt filtering fixes it.
- Adapter tests reproduced acceptance of duplicate JSON keys before strict
  decoding. [rules-review.md](rules-review.md) records development-language and
  partial-timeout regressions for the bounded baseline.

The no-inference end-to-end test prepares the real 180-case manifests, closes
all arms as unavailable, freezes development selection, advances stage barriers
with a synthetic test review, and reports `inconclusive` with all 120 primary
cases still in each denominator. It never supplies or examines a held-out
candidate prediction. Synthetic review metadata is confined to a temporary
test directory and is not an execution approval.

Independent reviews are preserved in [reviews/fixtures.md](reviews/fixtures.md)
and [reviews/implementation.md](reviews/implementation.md). Fixture approval
covers exact final hashes. Implementation review covers its recorded snapshots
and targeted fixes, not subsequent runtime evidence or blanket approval of
later edits. The implementation owner cannot supply independent approval of
their own final execution freeze.

Remaining execution prerequisites are explicit: complete and hash the ARM64
wheel/image closure, acquire/verify weights, audit actual semembed precision/
threads/tokenizer behavior, run full-input token preflight, obtain run-specific
payload/scoring review, and collect supervised real runtime/resource/cleanup
evidence. These prerequisites are not simulated by offline tests. No guidance
claim or promotion decision is changed by this implementation.
