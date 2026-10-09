# Throughput evidence: `20261009T144559.263704Z`

Kev's own server (`kev.serve`) on MLX: Kev commit `5e42a7a0…`, adapter
`jaredpalmer/kev-4b` revision `6cfce5c2…`, base `Qwen/Qwen3.5-4B-Base` revision
`1001bb4d…`, bf16 backbone and fp32 pointer head. Protocol version 3,
semselect commit `2c5aad4`, clean working tree. All seven planned cells
completed with no errors, and the cached-state cell's prefix-cache accounting
matched the plan (32 hits, 32 misses). Native Metal through MLX on an Apple M3
Pro. Read the [throughput record](../../validation-throughput.md#kev-mlx-server)
for method, results and limits.

| File | Contents |
| --- | --- |
| `summary.json` | Invocation, protocol, provenance, the arm's own readings and all cell summaries |
| `working-tree.diff` | Local diff at run start (empty: the tree was clean) |
| `source/` | Harness and helper sources as executed |
| `kev-provenance.json` | The pinned serving environment: Kev commit and archive digest, adapter and base files with sizes and SHA-256, `head.pt` metadata, Python and package versions, disk use and the setup commands |
| `requirements.freeze.txt` | `uv pip freeze` of the Kev venv (78 lines) |
| `<cell>/summary.json` | Profile, kev.serve counters, cache check, A/B split (cached cell), labels |
| `<cell>/models.json` | `/v1/models` at startup: backend, dtype, device, LoRA rank, temperature, cache size |
| `<cell>/journal.jsonl.gz` | Every planned request with raw responses |
| `<cell>/runtime.log.gz` | The cell's kev.serve log |

`kev-provenance.json` is the run's copy of `.kev/provenance.json`, byte for
byte. It holds local paths, digests, versions and commands, and no credentials.
Journals and runtime logs are gzip-compressed (`gzip -9 -n`); `gzip -dc`
restores the original bytes. Each cell's `requests.json` is omitted: it
duplicates the frozen Kev bodies, which `run_kevmlx.py --validate` rebuilds and
checks. Every journal row keeps its `request_sha256`. Stored records keep their
original local paths.

`report.py` renders the cell table, but its generic readings pair a 1 × 1 cell
with an 8 × 8 llama.cpp cell and find none here. The arm's pre-declared
readings, including the cached-state reading, come from `run_kevmlx.py`:

```sh
python3 eval/throughput/report.py \
  docs/evidence/20261009T144559.263704Z-throughput-kevmlx-kev
python3 -c 'import json, sys; sys.path.insert(0, "eval/throughput"); import run_kevmlx; print(run_kevmlx.render(json.load(open(sys.argv[1]))), end="")' \
  docs/evidence/20261009T144559.263704Z-throughput-kevmlx-kev/summary.json
```
