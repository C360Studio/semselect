# semselect working agreements

- Keep this a small packaging/integration service. Use the native llama.cpp
  `/v1/systemone` API; do not implement a second scoring engine or provider framework.
- Callers own categories, routing, authorization, thresholds, fallback and actions.
  Example taxonomies belong in evaluation fixtures, never the service.
- Preserve the pinned runtime, model revision, SHA-256 and license attribution.
  Model/runtime changes require real smoke evaluation and a revised validation record.
- Support CPU-only Linux containers and the native Apple Silicon Metal path.
  Record the architecture actually tested; do not infer AMD64 or CUDA results
  from ARM64 CPU or Metal execution.
- Go code must honor contexts and bound bodies, concurrency and response size.
  Preserve original candidate order and native response values.
- Run `task check` for service changes. Use a failing regression before bug fixes.
  `task smoke` and `task evaluate` require real inference; offline tests do not
  establish model accuracy. Record failures, coverage, latency and resource limits.
- Keep sibling repositories unchanged; propose SemStreams work in
  `docs/semstreams-integration.md`. Preserve other work in the shared workspace.
- Do not label small smoke results as benchmarks or model probabilities as
  calibrated correctness. See `docs/validation.md` for the current evidence.
