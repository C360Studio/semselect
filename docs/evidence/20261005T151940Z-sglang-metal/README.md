# SGLang MLX compatibility probe — 2026-10-05

**Result: model loaded, server warmup failed, endpoints untested.** This is a
reproducible compatibility failure on one pinned configuration. It does not prove
that SGLang can never serve this model on Metal, and supplies no accuracy or
performance comparison.

Host: Apple M3 Pro, macOS 26.5.2 arm64. Isolated CPython 3.12.13; PyTorch 2.13.0,
MLX 0.32.3, mlx-lm 0.32.0, Transformers 5.17.0. Full dependency freeze is in
`dependencies.txt`. No optional native Metal kernels, custom inference kernels,
or runtime source patches were used. Installation plus source inspection and
three bounded starts took approximately 15 minutes; inference starts occupied
15:23:39–15:25:55 UTC.

Source: SGLang `efb62ce269b499123e2d1c89005ee4cea8c31098`. Source archive SHA-256,
host metadata and every downloaded model-file checksum are in `provenance.json`.
The source distribution falls back to package version `0.0.0.dev0` without Git
metadata; the source revision above, not that package version, identifies it.

Model: `mlx-community/Qwen3.5-4B-4bit` at
`0e7ffd5c629ef7719d4cbc04069232580bfa9d9c`, affine four-bit, group size 64.
The safetensors file is 3,034,300,695 bytes. This HF repository has no model card
or recorded original checkpoint/conversion revision; do not describe its
precision as equivalent to the existing GGUF Q4_K_M baseline. No remote model
code was enabled, and inference used only the pinned local files in offline mode.

## Outcomes

1. Installation of the serving-only `srt_mps` extra succeeded. The guide's
   `pyproject_other.toml` replacement was applied. `SGLANG_BUILD_RUST_EXTS=none`
   disabled optional Rust extensions; the optional native Metal build was omitted.
2. Sandboxed CLI preflight failed with missing `triton`. Source inspection showed
   Apple's Triton compatibility stubs are gated on MPS availability, which the
   sandbox did not expose. Host-visible CLI preflight succeeded. Both logs are
   retained; this initial failure is distinct from the real runtime failure.
3. `attempt1/`: automatic hybrid-model cache selection asserted
   `extra_buffer needs CUDA/MUSA/NPU/ROCm/XPU (FLA)` before startup. No endpoints ran.
4. `attempt2/`: explicit `--mamba-radix-cache-strategy no_buffer` and
   `--disable-overlap-schedule` passed that validation, then our optional
   `--language-model-only` choice was rejected for this architecture. This was a
   probe configuration error, not proof of an inference defect.
5. Final attempt (top-level `server.log` and `probe.json`): removed that optional
   flag. MLX loaded the model in 1.79 seconds according to its own log. The built-in
   warmup then crashed in unified cache matching:

   ```text
   AttributeError: 'MlxAuxiliaryStateComponent' object has no attribute 'mamba_checkpoint_grid'
   ```

   The server never became healthy. JSON Schema, `/v1/score`, `/v1/decisions`,
   complete candidate distributions and repeatability therefore remain **untested**.
   The server exited with signal 9 after its child-failure handler. The driver
   verified localhost port 30101 closed. No endpoint request latency is reported.

All launch arguments and selected environment variables are saved in each
`probe.json`. `run_probe.py` is the exact driver for that attempt. Its planned
fixtures are visible in the source, but were not executed. The driver's own shell
exit is zero because it records launch failures in JSON; use `failure` and
`ready`, not shell exit, to interpret these historical attempts.

## Reproduce setup

Use a fresh isolated workspace directory and resolve Python 3.12.13 with uv. Fetch
the source archive at the exact commit above, verify its SHA-256, and copy
`python/pyproject_other.toml` to `python/pyproject.toml`. Install with:

```text
SGLANG_BUILD_RUST_EXTS=none uv pip install --python <venv>/bin/python -e '<source>/python[srt_mps]'
```

Use `dependencies.txt` to constrain the recorded dependency versions. Download
the exact model revision above with `huggingface_hub.snapshot_download`, and
verify the checksums in `provenance.json`. The launch in `probe.json` enables
`SGLANG_USE_MLX=1`, `--mlx-enable-sampling`, one running request, a 4096-token
context and 8192-token pool. The copied driver has workspace-relative artifact
locations resolved from its original `.sglang/run_probe.py` location; adapt those
paths for another checkout. Reserve the GPU so another inference run does not
overlap it.

No further runtime changes or alternative model downloads were attempted after
the final failure. An upstream fix or a separately documented configuration trial
can follow; this snapshot must remain unchanged.

Sources: [Apple Metal guide](https://docs.sglang.io/docs/hardware-platforms/apple_metal),
[MLX scoring report](https://github.com/sgl-project/sglang/issues/41211),
[decision API](https://docs.sglang.io/docs/supported-models/decision_models).
