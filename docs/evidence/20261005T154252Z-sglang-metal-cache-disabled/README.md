# SGLang MLX cache-disabled compatibility probe

**Result: the prepared endpoint checks passed.** Adding the standard
`--disable-radix-cache` flag bypassed the previous warmup failure without changing
runtime source, dependencies or model weights. This verifies one fixture on one
pinned configuration, not routing accuracy, calibration or a speed advantage.

The directory timestamp identifies preparation at 2026-10-05 15:42:52 UTC. Actual
execution was **16:13:24–16:14:02 UTC**, after the separate CPU experiment released
the machine. `probe.json` preserves actual times, launch arguments, environment,
all requests and raw responses. `summary.json` records direct checks against those
responses. `server.log` preserves runtime startup and request logs.

## Configuration

- Apple M3 Pro, macOS 26.5.2 arm64.
- SGLang source `efb62ce269b499123e2d1c89005ee4cea8c31098`.
- Isolated Python 3.12.13, PyTorch 2.13.0, MLX 0.32.3 and mlx-lm 0.32.0;
  full dependency freeze in `dependencies.txt`.
- `mlx-community/Qwen3.5-4B-4bit` revision
  `0e7ffd5c629ef7719d4cbc04069232580bfa9d9c`, affine four-bit/group size 64.
  Every downloaded file's checksum and the source archive checksum are recorded in
  `provenance.json`. This artifact differs from the GGUF Q4_K_M baseline. The
  publisher does not supply an original checkpoint/conversion revision or model
  card; that provenance limitation remains.
- Local files only, offline mode, no remote model code. No dependency changes,
  model downloads, optional native Metal kernels or source patches in this retry.
- `SGLANG_USE_MLX=1`, `--mlx-enable-sampling`, `--disable-cuda-graph`,
  `--mamba-radix-cache-strategy no_buffer`, `--disable-overlap-schedule`, and
  **`--disable-radix-cache`**. One running request, 4096-token context, 8192-token
  pool, localhost port 30101. The full command is in `probe.json`.

## Observed endpoints

All inference used the single message “Please refund the duplicate charge on my
invoice.” Thinking was explicitly disabled for chat and tokenized score prompts;
the decisions endpoint owns its own template and reasoning toggle.

| Check | Outcome |
| --- | --- |
| Two JSON Schema chat calls | HTTP 200, valid `route` enum objects, both select `billing` |
| Tokenize/detokenize | HTTP 200; rendered prompt round-trips, A/B each add one distinct token at the actual answer position |
| Two independent `/v1/score` calls | HTTP 200, complete finite A/B distributions summing to one; identical returned scores and raw label logprobs |
| Two `/v1/decisions` calls | HTTP 200, complete choice/yes-no/ordinal-score distributions with label IDs and full-vocabulary label mass; identical answers across repeats |
| Two route-score replays | HTTP 200; replaying the decisions prompt/label IDs through `/v1/score` gives exactly matching route probabilities |

The independent scoring prompt yields billing 0.996827 and unknown 0.003173. The
server-owned decisions prompt yields billing 0.991423 and unknown 0.008577.
Different prompts produce different distributions; neither number is calibrated
probability of being correct. The yes/no question's candidate mass is only
0.200930, another reason to keep raw label mass distinct from the normalized
answer distribution. The urgency output was checked for structural validity,
not accepted as a validated semantic judgment.

This is eight bounded inference endpoint calls, with each decisions call containing
three questions. It is not the 24-case routing evaluation. Request times are
retained for diagnosis but must not be compared with the llama.cpp latency matrix:
prompts, quantization, output formatting, warmup and work performed differ. The
first JSON request alone took 5.89 seconds and the second 0.38 seconds, illustrating
why this tiny startup probe does not establish steady-state performance.

Server-reported token usage per call was 42 prompt + 12 completion tokens for
JSON, 57 + 0 for independent scoring, 160 + 0 for each three-question decisions
bundle, and 58 + 0 for route replay. Across the eight calls this totals 634 prompt
tokens and 24 completion tokens, excluding internal startup/warmup work. The
three decisions prompts account for 58 + 43 + 59 tokens per bundle. Twelve HTTP
requests were recorded overall, including metadata and tokenization requests.

## Cleanup and reproduction

The driver sends SIGTERM to its owned process group, waits up to 15 seconds, then
has a five-second SIGKILL fallback. The log shows SIGTERM at 16:13:57 UTC, the
scheduler exiting with -15, and SGLang's subprocess watchdog triggering SIGQUIT.
At 16:14:02 UTC SGLang logs `kill_process_tree` with its own PID and
`include_parent=True`. Pinned source confirms that this handler calls
`itself.kill()`. This supports a **SGLang self-kill after the driver's group
SIGTERM**, before the driver's 15-second fallback, rather than an inference
failure or demonstrated graceful exit. The driver did not separately instrument
whether its timeout branch ran; the five-second log sequence and self-kill call
are the evidence for this interpretation. See `shutdown-analysis.json` for exact
log lines and pinned source references. The observed server exit was **-9**.
The driver observed a closed port, and
separate host `pgrep` and `lsof` checks found no matching server/scheduler/
detokenizer processes or listener on 30101. Exact commands, statuses and outputs
are preserved in `cleanup-verification.json`.

`run_probe.py` is the exact driver. From the original checkout it ran as:

```sh
.sglang/venv/bin/python results/sglang-probe/20261005T154252Z/run_probe.py
```

The driver refuses to overwrite `probe.json`; reproduce by copying the prepared
driver into a new timestamped directory at the same directory depth. It limits
readiness to 150 seconds, each request to 30 seconds and total request time to
330 seconds, and exits nonzero on failed validation. Use the pinned setup and
checksums from the earlier probe when rebuilding the environment. Earlier failure
artifacts remain unchanged; disabling caching is a working configuration for this
probe, not a fix or validation of SGLang's cached MLX path.
