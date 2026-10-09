# semselect working agreements

- The mission is to help skeptical developers decide when a Jev-like decision
  model is worth using, through a tested service, reproducible comparisons and
  plain-language guidance. Evidence favoring code or schema-constrained chat is
  a successful result. Keep `docs/when-to-use.md` aligned with measured evidence;
  distinguish a functioning API from demonstrated workload benefit.
- Keep this a small packaging/integration service. Use the native llama.cpp
  `/v1/systemone` API; do not implement a second scoring engine or provider framework.
- Callers own categories, routing, authorization, thresholds, fallback and actions.
  Example taxonomies belong in evaluation fixtures, never the service.
- Prefer coded control structures for authoritative facts, validation, permissions
  and state transitions. Use models for the remaining semantic judgments, with
  explicit abstention/fallback and caller-enforced action policy.
- Include the strongest applicable existing algorithms in comparisons, including
  SemSource/SemEngine graph, community and retrieval paths. Preserve pinned
  SemStreams comparisons as historical evidence during the migration. Separate
  deterministic rules, statistical retrieval, learned embeddings and generative/decision models.
  Use the same task and evidence; a taxonomy mismatch is not a model advantage.
- SGLang/direct-scoring investigations belong in isolated evaluation work. They
  do not change the default runtime or justify a new provider framework. Follow
  the scope and evidence criteria in `docs/sglang-investigation.md`.
- Preserve the pinned runtime, model revision, SHA-256 and license attribution.
  Model/runtime changes require real smoke evaluation and a revised validation record.
- Support CPU-only Linux containers and the native Apple Silicon Metal path.
  Record the architecture actually tested; do not infer AMD64 or CUDA results
  from ARM64 CPU or Metal execution.
- Establish reproducible CPU/Docker and Metal experiments before asking for CUDA
  help. Preserve SGLang compatibility failures separately from successful
  llama.cpp experiments; neither establishes support for the other runtime.
- Go code must honor contexts and bound bodies, concurrency and response size.
  Preserve original candidate order and native response values.
- Run `task check` for service changes. Use a failing regression before bug fixes.
  `task smoke` and `task evaluate` require real inference; offline tests do not
  establish model accuracy. Record failures, coverage, latency and resource limits.
- Keep sibling repositories unchanged; propose new integration work in
  `docs/semengine-integration.md`. `docs/semstreams-integration.md` is historical.
  Distinguish planned SemEngine ports from implemented and tested capabilities.
  Preserve other work in the shared workspace.
- Do not label small smoke results as benchmarks or model probabilities as
  calibrated correctness. See `docs/validation.md` for the current evidence.
