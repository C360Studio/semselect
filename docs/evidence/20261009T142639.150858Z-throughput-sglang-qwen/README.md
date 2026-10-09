# Throughput evidence: `20261009T142639.150858Z`

SGLang MLX serving Qwen3.5-4B as MLX affine 4-bit, group 64
(`mlx-community/Qwen3.5-4B-4bit` revision `0e7ffd5c…`), protocol version 3,
semselect commit `454405f`, clean working tree. The W1 invocation again, with
`probe-overlap` only. The probe passed and `w1-qwen_json-1x1` completed.
`w1-qwen_json-4x4`, the first cell with four running requests, crashed the
scheduler on its first requests: `RuntimeError: Expected all tensors to be on
the same device, but found at least two devices, mps:0 and cpu!`
(`w1-qwen_json-4x4/runtime.log`, lines 91 to 120). The runner then recorded a
cleanup error and launched no further cell. Native Metal through MLX on an
Apple M3 Pro, pinned SGLang `efb62ce269b499123e2d1c89005ee4cea8c31098`. Read
the [throughput record](../../validation-throughput.md#sglang-mlx) for method,
results and limits.

| File | Contents |
| --- | --- |
| `summary.json` | Invocation, protocol, provenance, request mapping, the probe record and both cell summaries |
| `working-tree.diff` | Local diff at run start (empty: the tree was clean) |
| `source/` | Harness and helper sources as executed |
| `probe-overlap/` | `probe.json`, `server-info.json`, `models.json` and `runtime.log.gz`, as in the first run |
| `<cell>/summary.json` | Profile, startup layout, prefill counters, errors, labels |
| `<cell>/server-info.json`, `models.json` | `/get_server_info` and `/v1/models` at startup |
| `<cell>/usage.json` | Server-reported `prompt_tokens` and `completion_tokens` per call |
| `<cell>/journal.jsonl.gz` | Every planned request with raw responses |
| `<cell>/runtime.log.gz` | The cell's SGLang server log |

Journals and runtime logs are gzip-compressed (`gzip -9 -n`); `gzip -dc`
restores the original bytes, and the line numbers cited in the record refer to
the decompressed log. Each cell's `requests.json` is omitted: it duplicates
bodies that `run_sglang.py --validate` rebuilds from the frozen fixtures. Every
journal row keeps its `request_sha256`. Stored records keep their original
local paths.

Recompute the SGLang tables and readings from the four SGLang runs together:

```sh
python3 eval/throughput/report.py \
  docs/evidence/20261009T142410.714460Z-throughput-sglang-qwen \
  docs/evidence/20261009T142639.150858Z-throughput-sglang-qwen \
  docs/evidence/20261009T143126.613254Z-throughput-sglang-qwen \
  docs/evidence/20261009T143712.053883Z-throughput-sglang-qwen
```
