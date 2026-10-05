# Frozen synthesis replay evidence

Formal run: 2026-10-05 18:42:18–18:48:54 UTC. Exit 0, all 156 planned records
accounted for. The 13 distinct questions are repeated across two generator strata,
three arms and two trials; they are not 156 independent examples.

- [Summary](summary.json), recomputed by `python3 summarize.py`.
- [Full replay](replay.json), including gates, typed synthesis results, timings,
  warmups, failures and Metal cleanup; individual driver calls also remain under
  `calls/` with inputs, logs, outputs and provenance.
- [Actual token budgets](context-preflight/summary.json); no inference in preflight.
- [Blind answers](grading/blind-answers.json), [independent grades](grading/grades.json),
  [adjudication](grading/adjudication.json) and [identity map](grading/private-map.json).
  “Private” meant withheld during grading; the map is now published for inspection.
- [CPU shutdown](cpu-cleanup/summary.json): owned generator stopped, exit 0,
  no OOM, loopback port closed. Its cumulative cgroup counters include acquisition
  and replay; they are not per-arm resource measurements.
- [Acquisition evidence](../20261005-synthesis-acquisition/README.md), including
  the failed initial setup and the successful captured input set.

The run data were copied byte for byte from the ignored result directory. Grading
and summary artifacts were added after inference, with original blind grades
preserved. `manifest.json` records every published file's hash. Absolute local
paths in original provenance identify this execution; no weights or binaries are
included. Source snapshots identify the replay code that actually ran.

The [validation record](../../validation-synthesis.md) explains limits and
reproduction. The [experiment](../../../eval/synthesis/README.md) provides the
readable verdict. No result establishes production accuracy, calibration, CUDA
performance or advantage over always deferring on this all-insufficient capture.
