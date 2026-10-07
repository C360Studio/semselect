# Independent specialist implementation review

Date: 2026-10-07. Reviewer: `/root/label_review`. Read-only review of code not authored by this reviewer, against `/tmp/semselect-specialist-plan.md`. No candidate/model outputs inspected, no deliverable edits, and no heldout query text or per-case gold disclosed. This is a static implementation review plus offline checks, not operational approval.

## Status

**Focused code re-review: the four original implementation blockers have targeted corrections, detailed in the final re-review below. No further blocker identified in that focused static scope. Operational execution is still unapproved and untested.** The original findings below are retained as review history, with original snapshot hashes. A previously reported primary-only resource peak bug was fixed during review.

## Original blockers (superseded by focused re-review)

1. **Container listener is unreachable through ordinary Docker port publishing.** `server.py:153` binds the specialist HTTP server to `127.0.0.1` inside the container. The Dockerfile entrypoint starts that server and there is no alternate bind argument. A host runner using a loopback-published Docker port is forwarded to the container interface, not its internal loopback. Provide an appropriate container-interface listener with host-loopback-only publishing, or demonstrate another supported network path. This conclusion is from the actual code path; Docker networking was not exercised in this sandbox.

2. **Actual resource limits are checked after inference, rather than before it.** `runner.py:287` only validates self-reported environment metadata; `resources.capture` actually checks the Docker CPU/memory/device/image constraints, but normal successful execution first invokes it in the stage finalizer. A mislabeled container can therefore execute the entire stage outside the four-CPU/4-GiB CPU-only bounds before receiving a failed report. The load path has the same initial gap. Capture and require verified running-container limits before the first learned request/index call, and retain that observation. An existing mismatch should close the arm instead of allowing dependent stages to keep issuing calls.

3. **Runtime identity and telemetry target are not frozen across stages or bound to the endpoint.** `run_stage` and `load_check` accept a fresh environment record and independent URL each time. Model artifact pins are checked, but neither the preparation freeze nor selection freeze constrains runtime SHA/image/dependency closure or endpoint/container relationship. `resources.capture` checks the supplied container in isolation and never verifies that the URL points to that container. Consequently, a stage can use another runtime or collect memory from a different idle container while retaining the same model pin. Freeze stable runtime identity before measurements, check it across development/primary/sensitivity/load, allow lifecycle-specific IDs only with explicit provenance, and bind the observed container's published endpoint to the called URL.

4. **Model process lifecycle bounds remain an unenforced external obligation.** `transport.py` bounds the HTTP client and runner bounds dispatch, but neither terminates a wedged model kernel. `server.py` explicitly acknowledges this. The currently reviewed path has no actual owned supervisor enforcing the frozen outer per-arm process deadline, setup/readiness limits, or one-loaded-model-at-a-time requirement. An inference timeout can leave compute running after the client has stopped, distorting subsequent contention/resource evidence and exceeding the stated process bound. A concrete supervised launch/stop path and cleanup evidence are needed before operational execution can be approved. Do not equate the bounded HTTP client with model cancellation.

## Resolved or positively checked

- Required IDs and opaque sensitivity mappings are decoded before grading; raw scores retained.
- Explicit error, deferred, unattempted and no_override outcomes remain distinct, and missing rows stay in all-case denominators.
- Persisted fatal/budget/runtime stop reasons prevent a later stage from silently restarting the arm.
- Development selection and source/payload hashes precede heldout execution; review metadata must bind the preparation freeze.
- Model/token artifact identity checks bind tokenization to pinned artifact manifests rather than just hash-shaped strings.
- Family-cluster intervals and native code options are reported separately.
- During review, `report` was changed to require and aggregate resource finalizations across development, primary, sensitivity and load; load now captures final resources. This closes the previously reported primary-only peak/OOM gap in the reviewed snapshot.
- Failed embedding-index records now include elapsed setup time; completed setup/index work enters the separate embedding budget.

## Tests and untested constraints

Executed `python3 -m unittest discover -s eval/specialist-intent -p 'test_*.py'` on the then-current shared tree: 66 tests, two errors, one skip. The two errors were `test_transport` socket listeners rejected by this sandbox (`PermissionError: [Errno 1] Operation not permitted`) before exercising transport behavior. Other tests passed. The skipped native-driver integration check was not established as passing. The implementation owner is adding and running tests independently; this record does not claim those later results.

No Docker model launch, native inference, image/dependency closure build, actual tokenizer payload run, cgroup observation, OOM behavior, server cancellation, concurrency load, Metal request, or network path was validated by this reviewer. Offline tests cannot establish CPU feasibility, latency, memory, model quality or the reliability of measured provenance.

## Reviewed snapshot hashes

| File | SHA-256 |
|---|---|
| runner.py | e1c4f90e959d7a9cc4326fa3f6ce3fb3b72c16b1b8da98617c85d4018cc31a70 |
| scoring.py | 75476e63a523a874d30ed389c69e8450e743659e5d2f1fa153bad688547aea68 |
| evidence.py | 20b79efb284759727058e3a41eb1d9c2154ad0098ea0b910797324fc7895683a |
| resources.py | d9cf3a3b4e4cda347352e20138fe55c54a4a4d0e4a6731d2f52705c079e073ae |
| transport.py | 1528fe194dbe9d60d5e35e6c7958089d7315784ff866e6697571a16cf05d71b2 |
| server.py | adc98d3ba5b8c1aee6c1a820d34dfae06bb7625af0f564cb87da190bd71fcee0 |
| protocol.json | 05a1b095aa39f15219815b7fce7f153e4c7a22b3376737ee8f73248b945dc545 |


## Final focused re-review

Read the current targeted changes without editing the deliverable. The four original blockers are closed at the implementation level:

1. Docker `ENTRYPOINT` now includes `--host 0.0.0.0`, so appending the required arm/model arguments cannot discard the container-interface bind. Native server default remains loopback. Actual Docker reachability still needs a real smoke check.
2. Every learned CPU stage and load attempt now requires a successful live container audit before calls. CPU/memory/image/device bounds and running state are checked before warmups or embedding-index construction.
3. Runner requires URL equality with the recorded endpoint, and resource capture verifies the endpoint's explicit host-loopback port belongs to the inspected container. Stable runtime/artifact/dependency/precision metadata must match development. Selection freeze now includes development environment hashes and rejects edits to the identity authority before heldout stages.
4. Specialist server has startup (120s), per-request (10s) and total-lifetime (at most 1920s) watchdogs. The new external supervisor bounds owned commands, allows at most two setup attempts under a cumulative 1800s budget, and terminates/reaps the owned native process group. Direct Docker runs require a new matching cidfile; cleanup explicitly stops/inspects/kills the owned container and records container shutdown separately from local CLI-process absence. Failed shutdown is not called verified. The initial `.launch.json` double-counting defect was corrected before this final snapshot.

Later-stage peak/OOM observations remain included in advancement gating. Independent execution review remains required by the runner; this code review does not populate or authorize that run-specific review.

Reviewer checks after relevant changes: nine runner tests passed; two supervisor tests passed, including a real short-lived local child deadline/reap and exactly two allowed setup attempts. These tests do not involve model inference or Docker. The fixture-independent Docker-cleanup regression mentioned by the implementation owner was still being added when this snapshot was inspected and is not claimed as a reviewer-observed pass.

Remaining operational constraints: use the supervisor for actual external runtimes; record and verify the one-loaded-model discipline and startup/readiness measurements, obtain actual Docker cgroup and shutdown evidence, qualify native-kernel watchdog behavior, and perform model/token/runtime smoke checks before any measured evaluation claim. Python thread watchdog behavior against a genuinely wedged native kernel was not demonstrated; the external process supervisor supplies a separate termination mechanism. Self-reported provenance requires actual evidence, and this static review is not that evidence. No real inference, Linux ARM64 container, Metal endpoint, full input tokenization, or load check was run by this reviewer.

### Final focused snapshot

| File | SHA-256 |
|---|---|
| runner.py | 8b72ee3856a1d6fa7ee11fce5721ec9a30042d90de6c246c78c9ac99675b1ee5 |
| scoring.py | 75476e63a523a874d30ed389c69e8450e743659e5d2f1fa153bad688547aea68 |
| evidence.py | d61ef122b10dd2d27b707525587b9f82ac81929f353f347735353dd2dcfb1eb3 |
| resources.py | b90fde64bc9f0576530d122970c7a2e4f522015a82136fd225dbe9d92e2b8ac3 |
| transport.py | 1528fe194dbe9d60d5e35e6c7958089d7315784ff866e6697571a16cf05d71b2 |
| server.py | 87cc34554ca54394720d09133cd0f1f5c0bcac5636a30af193200d3ed0bd8cd7 |
| supervise.py | 54ffe97847499710b0e4a8e49049348dd786bf4a2c7f8dcce20a18029bf0487f |
| Dockerfile | 9b424e84c6b9f8df391b8d3925d4d47c3efe4a1e174df678caee32d67c82d3e3 |
| protocol.json | 05a1b095aa39f15219815b7fce7f153e4c7a22b3376737ee8f73248b945dc545 |
