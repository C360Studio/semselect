# Throughput evidence: `20261009T141930.309707Z`

Kev-4B Q4_K_M, protocol version 3, semselect commit `454405f`, clean working
tree. Rerun of `w1-kev-8x8` (stopped again) and `w2-kev-8x8` (complete), plus
`w1-kev-8x8-b4096`, which the startup check refused because the build set
`n_batch` to 512; its `runtime.log.gz` shows the clamp. Native Metal on an
Apple M3 Pro, pinned llama.cpp `6c59c40076c00eab49754dc955d7652d93f9e125`.
Read the [throughput record](../../validation-throughput.md) for method,
results and limits.

| File | Contents |
| --- | --- |
| `summary.json` | Invocation, protocol, provenance and all cell summaries |
| `working-tree.diff` | Local diff at run start (empty: the tree was clean) |
| `source/` | Harness and helper sources as executed |
| `<cell>/summary.json` | Profile, startup layout, metrics, errors, labels |
| `<cell>/journal.jsonl.gz` | Every planned request with raw responses |
| `<cell>/runtime.log.gz` | The cell's llama-server log at `-lv 4` |

Journals and runtime logs are gzip-compressed (`gzip -9 -n`) to keep the
published evidence small; `gzip -dc` restores the original bytes. Each cell's
`requests.json` is omitted: it duplicates the frozen fixtures, which
`task throughput:validate` rebuilds and checks. Every journal row keeps its
`request_sha256`. Stored records keep their original local paths.

Recompute the tables and readings from the three llama.cpp runs together:

```sh
python3 eval/throughput/report.py \
  docs/evidence/20261009T132345.224681Z-throughput-llamacpp-kev \
  docs/evidence/20261009T140002.987772Z-throughput-llamacpp-qwen \
  docs/evidence/20261009T141930.309707Z-throughput-llamacpp-kev
```
