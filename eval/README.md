# Routing smoke evaluation

`routing-smoke.json` contains 24 manually labeled examples. Categories, descriptions,
unknown selection, and multi-intent fallback are caller policy. The service owns
none of this taxonomy. Each example runs twice, with original and reversed candidate
order: 48 measured requests plus warmup. Reversed pairs are correlated observations;
this tiny set is a smoke test, not a production benchmark or calibration study.

On Apple Silicon, `task metal:evaluate` and `task metal:baseline:evaluate` start
and stop their own native GPU servers and save separate timestamped evidence.
The second uses Qwen3.5-4B Q4_K_M on the same runtime, not the old 0.6B container.
See [setup and measurement limits](../docs/laptop-and-gpu.md) and the
[measured Metal comparison](../docs/validation-metal.md).

Run against a real semselect service:

```sh
python3 scripts/evaluate.py --backend semselect --url http://localhost:8084 \
  --model semselect-kev-4b --output results/semselect.json \
  --hardware 'CPU-only Linux container; record CPU, RAM and container/runtime limits here'
```

Compare with a running seminstruct OpenAI-compatible llama.cpp service on the same
hardware. Use the exact served model ID from that deployment:

```sh
python3 scripts/evaluate.py --backend seminstruct --url http://localhost:8083 \
  --model qwen3-0.6b --output results/seminstruct.json \
  --hardware 'Same CPU/container limits as semselect'
```

Both receive the same instructions, category descriptions, examples, and candidate
orders. The chat baseline uses temperature zero and a structured enum JSON response;
it disables Qwen3 thinking through the supported chat-template option. It does not
invent confidence values or request generated confidence. Record any model/template
incompatibility as a failed baseline run, not an inferred result. Different model
sizes and chat templates remain confounders even on identical hardware.

`--timeout` defaults to 120 seconds per HTTP operation. `--warmup` defaults to one
request and accepts zero through ten. Warmup results are recorded and excluded from
metrics. There are no retries. Errors and malformed responses count against total
accuracy and coverage; the command exits nonzero if any measured call fails. Model
misclassifications alone do not fail the command; inspect reported accuracy. Results
store every response, duration, expected label, candidate order, full dataset, and
run metadata. They contain the input text: keep private datasets within appropriate
storage. Client platform metadata is detected; supply actual inference hardware,
model revision, limits, and resource measurements with `--hardware` and `--notes`.
The evaluator does not measure server CPU or memory. The optional resource wrapper
below records read-only cgroup observations around the same command.

Reported metrics:

- Accuracy and unknown-selection rate use all measured requests as denominator.
- Unknown-policy coverage is the fraction receiving a valid non-unknown selection;
  conditional error is the wrong-label fraction within that accepted subset.
- Native `pmax` thresholds 0.0, 0.5, 0.7, and 0.9 additionally reject low-score
  decisions. These are candidate-distribution scores, not calibrated probabilities
  of correctness. Returned native confidence is preserved, never synthesized.
- The baseline has no candidate probability distribution. Its report explicitly
  marks native confidence unavailable and provides only unknown-policy coverage.
- Order flip rate includes only pairs with two valid responses; missing/invalid
  pairs are counted separately. A stable but wrong label still reduces accuracy.
- Latency includes failed measured calls and excludes warmup; p50 is the median and
  p95 uses nearest rank. Separate cold-start, throughput, and load testing are deferred.

## Optional container resource observations

Use the actual running inference container name from `docker compose ps` (the
example name depends on the Compose project name):

```sh
python3 scripts/measure.py --container semselect-runtime-1 \
  --output results/semselect-resources.json -- \
  python3 scripts/evaluate.py --backend semselect --url http://localhost:8084 \
  --model semselect-kev-4b --output results/semselect.json \
  --hardware 'Record physical hardware and runtime limits here'
```

Repeat around the seminstruct command with its inference container name. Use the
same hardware and runtime limits, one service at a time, without other requests.
The wrapper records selected Docker image/platform and limit fields, Docker engine
CPU/RAM information, timestamps, command duration, and before/after cgroup v2
`memory.current`, `memory.peak`, and `cpu.stat`. Docker Desktop values describe its
Linux VM; record the physical host separately. Environment variables and full
container config are not collected; wrapped command arguments are not persisted.

`memory.current` includes cgroup-accounted file cache. `memory.peak` is the existing
cgroup peak, normally covering the container's entire lifetime including startup,
cache, and previous work; it is **not** process RSS or the measured interval's peak.
The wrapper never resets counters. CPU usage delta includes all activity between
snapshots, including warmup, background work, other requests, and small measurement
overhead. A changed container identity/start time or decreased counter makes the
delta unavailable. Snapshots are sequential, not atomic. No GPU metrics are collected.

The wrapped command defaults to a 1,800-second wall-time limit (`--timeout` changes
it), and each Docker probe has a 10-second limit (`--probe-timeout`). Probes occur
outside evaluator request timings. Unsupported cgroups or unavailable Docker reads
are recorded as unavailable; the evaluation command still runs. Its nonzero exit
status is propagated, with 124 for wrapper timeout, 130 for interruption, and 127
when the command cannot start. On timeout the wrapper terminates its command process
group on POSIX systems, then saves available observations.

Offline evaluator tests check response validation, request construction, and metric
denominators. They do **not** provide evidence that either model ran or was correct:

```sh
python3 -m unittest discover -s tests -p 'test_evaluate.py' -v
```
