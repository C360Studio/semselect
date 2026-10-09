# Throughput evidence: `20261009T142410.714460Z`

SGLang MLX serving Qwen3.5-4B as MLX affine 4-bit, group 64
(`mlx-community/Qwen3.5-4B-4bit` revision `0e7ffd5c…`), protocol version 3,
semselect commit `454405f`, clean working tree. The W1 invocation, with both
re-probes first. `probe-overlap` passed. `probe-radix` crashed the server
during its own startup warmup, before it reported ready:
`AttributeError: 'MlxAuxiliaryStateComponent' object has no attribute
'mamba_checkpoint_grid'` (`probe-radix/runtime.log`, lines 78 to 123). The
runner then recorded a cleanup error and launched no cell, so this run has no
measured cell. Native Metal through MLX on an Apple M3 Pro, pinned SGLang
`efb62ce269b499123e2d1c89005ee4cea8c31098`. Read the [throughput
record](../../validation-throughput.md#sglang-mlx) for method, results and
limits.

| File | Contents |
| --- | --- |
| `summary.json` | Invocation, protocol, provenance, request mapping and both probe records; no cells |
| `working-tree.diff` | Local diff at run start (empty: the tree was clean) |
| `source/` | Harness and helper sources as executed |
| `probe-overlap/probe.json` | The fixture request, both raw responses, startup record, last 200 log lines and verdict |
| `probe-overlap/server-info.json`, `models.json` | `/get_server_info` and `/v1/models` at startup |
| `probe-radix/probe.json` | The same record for the probe that never became ready |
| `probe-*/runtime.log.gz` | The probe's SGLang server log |

Runtime logs are gzip-compressed (`gzip -9 -n`); `gzip -dc` restores the
original bytes, and the line numbers cited in the record refer to the
decompressed log. Stored records keep their original local paths.

Recompute the SGLang tables and readings from the four SGLang runs together:

```sh
python3 eval/throughput/report.py \
  docs/evidence/20261009T142410.714460Z-throughput-sglang-qwen \
  docs/evidence/20261009T142639.150858Z-throughput-sglang-qwen \
  docs/evidence/20261009T143126.613254Z-throughput-sglang-qwen \
  docs/evidence/20261009T143712.053883Z-throughput-sglang-qwen
```
