# Upstream software and model artifacts

- llama.cpp: Copyright (c) 2023-2024 The ggml authors, MIT. The exact source
  revision is recorded in `models.lock.json` and the Dockerfile. The original
  license is preserved in `licenses/llama.cpp-MIT.txt` and in the runtime image.
- Kev model and training implementation: Jared Palmer / Kev contributors,
  Apache-2.0. The model is derived from Qwen3.5-4B-Base (Qwen Team, Alibaba Cloud),
  also Apache-2.0. `ggml-org/Kev-4B-GGUF` is the converted distribution used here;
  `.src_sha` records conversion input revision `6cfce5c2fa4b4bd64026336ab649c5ca78857d52`.
  The downloaded GGUF is not modified by semselect. Preserve the license and
  attribution when redistributing weights. See `licenses/Apache-2.0.txt`.
- Ubuntu and Go images contain software under their respective package licenses;
  image digests and the Ubuntu package snapshot are pinned in the Dockerfile.
- Optional Julia evaluation candidate: Julia-1, Supersonic Labs, based on
  JHU CLSP's mmBERT-small; the source card and ggml-org distribution declare
  Apache-2.0. The unmodified Q8_0 artifact and conversion source revision are
  pinned in `eval/julia/models.lock.json`. Preserve attribution and
  `licenses/Apache-2.0.txt` with redistributed weights. The private training
  pipeline is not part of this repository.
- Optional matched-size chat baseline: Qwen3.5-4B, Qwen Team / Alibaba Cloud,
  Apache-2.0, distributed as a Q4_K_M GGUF by Unsloth. The exact artifact revision,
  size and SHA-256 are recorded in `models.baseline.lock.json`. Its model card
  identifies `Qwen/Qwen3.5-4B` as the source, but does not give the conversion input
  revision. semselect does not modify the downloaded GGUF. This text-only evaluation
  does not acquire its separate vision projection file. Preserve Apache-2.0 license
  and attribution when redistributing weights; see `licenses/Apache-2.0.txt`.

Primary license declarations: [llama.cpp](https://github.com/ggml-org/llama.cpp/blob/6c59c40076c00eab49754dc955d7652d93f9e125/LICENSE),
[Kev](https://github.com/jaredpalmer/kev/blob/main/LICENSE),
[selected GGUF](https://huggingface.co/ggml-org/Kev-4B-GGUF/blob/d924f2e2c3872da8b8aaf3eb4453b4126deceb79/README.md),
[base model](https://huggingface.co/Qwen/Qwen3.5-4B-Base).

Baseline sources: [pinned Unsloth model card](https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/blob/e87f176479d0855a907a41277aca2f8ee7a09523/README.md),
[pinned GGUF metadata](https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/blob/e87f176479d0855a907a41277aca2f8ee7a09523/Qwen3.5-4B-Q4_K_M.gguf),
[Qwen source model and license declaration](https://huggingface.co/Qwen/Qwen3.5-4B).

Jev and System One identify TypeSafe's product and API. semselect is independent
and does not contain proprietary Jev code or weights. API compatibility is not
a claim of equivalent quality, calibration, latency, or endorsement.

Julia sources: [pinned source model card](https://huggingface.co/SupersonicLabs/Julia-1/blob/a85b127321d580d65176c89ced8273f305745d85/README.md),
[pinned GGUF card](https://huggingface.co/ggml-org/Julia-1-GGUF/blob/16fee17949206fbf58da9347daea44d792a81211/README.md),
[base encoder](https://huggingface.co/jhu-clsp/mmBERT-small).
