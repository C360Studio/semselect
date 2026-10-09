# Throughput evidence: `20261009T143712.053883Z`

SGLang MLX serving Qwen3.5-4B as MLX affine 4-bit, group 64
(`mlx-community/Qwen3.5-4B-4bit` revision `0e7ffd5c…`), protocol version 3,
semselect commit `454405f`, empty working-tree diff (git status listed only the
three llama.cpp throughput evidence directories, then untracked). A W2
invocation limited to the two one-request cells. `w2-qwen_json-1x1` completed.
`w2-qwen_decisions-1x1` stopped after 13 measured requests when the scheduler
ran out of Metal memory: `RuntimeError: [METAL] Command buffer execution
failed: Insufficient Memory` (`w2-qwen_decisions-1x1/runtime.log`, lines 274
to 317). Native Metal through MLX on an Apple M3 Pro, pinned SGLang
`efb62ce269b499123e2d1c89005ee4cea8c31098`. Read the [throughput
record](../../validation-throughput.md#sglang-mlx) for method, results and
limits.

| File | Contents |
| --- | --- |
| `summary.json` | Invocation, protocol, provenance, request mapping and both cell summaries |
| `working-tree.diff` | Local diff at run start (empty) |
| `source/` | Harness and helper sources as executed |
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
