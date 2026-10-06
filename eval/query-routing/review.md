# Preparation review — 2026-10-05 America/Chicago

The parent agent owns the design, cases and Python preparation adapter.
The independent `routing_driver` worker owns the isolated Go driver.
The read-only `routing_contract` reviewer audited upstream behavior, the design,
all labels, driver changes and Python adapters. It edited no deliverable and ran
no classifier or model on development or held-out cases.

## Review findings resolved before the design freeze

- Scoped the comparison to actual classifier hints. Graph-query dispatch has a
  different resolver; neither `InferStrategy` nor correct hints prove execution.
- Identified configured BM25 as an unchanged upstream algorithm with authored
  examples. The `0.7` threshold is upstream; the examples are not shipped defaults.
- R32 originally required empty options for a missing path node. Review changed
  it to the native partial path intent. Missing binding is a caller readiness
  issue, not a model win over code.
- Clarified that solely negated/quoted operations differ from affirmative ones.
- Incompatible model selections remain invalid. Wrong and extra arguments are
  retained as errors, even when the operation matches.
- Fixed an empty freeze manifest passing verification, typed gold conflating
  `true` and `1`, and unchecked runtime revision disagreement. All three first
  failed in regression tests and then passed after repair.
- Recorded constructor time separately. Driver tests cover cancellation during
  setup and during the final classification using explicit synchronization.
- The driver's FIFO-input regression first blocked beyond its deadline; a
  nonblocking regular-file open fixed it. No production sibling was modified.
- Direct task verification exposed command-level `dir` not selecting the driver
  module. A task-level working directory now runs the intended isolated module.

The reviewer reported no remaining blockers for development-only selection after
the fixes, independently passing all eight Python tests and static validation.
The worker's ten driver tests passed with race detection; the parent confirmed
the task runs that module. The six imported implementation files match both the
cached pinned module and the inspected sibling source. Offline `go mod tidy`
could not resolve uncached dependency-test packages; test/build succeeded with
the existing module graph, `GOPROXY=off`, and `-mod=readonly`.

## Development selection, not held-out results

The parent ran 180 fresh-process development jobs: keyword once per 18 cases and
BM25 at nine thresholds per case. All completed successfully. The recorded rule
chooses the highest threshold among equal best exact-options counts. Threshold
`0.9` was selected; `0.6`–`0.9` all scored 12/18. Keyword also scored 12/18 and wins
the predefined simplicity tie, becoming the designated code comparator. Lower
BM25 thresholds scored 11/18. Both fixed-`0.7` and selected-`0.9` arms remain in
the held-out plan; no per-case code winner will be selected.

[Selection](development-selection.json) binds these counts to the driver and
source hashes. [Evidence](development-evidence.json) retains every typed driver
input, complete parsed JSONL output and process result. [Request hashes](request-manifest.json)
cover normal/reversed choices for both models over all development/test cases.
The validator recomputes the counts, selection, request hashes and artifact hashes.

No held-out classifier call or model inference occurred during preparation.
Byte-size checks are not token-budget checks. Actual runtime context fit, cache
behavior, model tuple validity and CPU/Metal performance remain unproved. The
execution runner and its preflight need separate review before formal calls;
retain this design freeze when adding their execution manifest.
