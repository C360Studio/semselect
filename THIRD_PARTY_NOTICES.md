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

Primary license declarations: [llama.cpp](https://github.com/ggml-org/llama.cpp/blob/6c59c40076c00eab49754dc955d7652d93f9e125/LICENSE),
[Kev](https://github.com/jaredpalmer/kev/blob/main/LICENSE),
[selected GGUF](https://huggingface.co/ggml-org/Kev-4B-GGUF/blob/d924f2e2c3872da8b8aaf3eb4453b4126deceb79/README.md),
[base model](https://huggingface.co/Qwen/Qwen3.5-4B-Base).

Jev and System One identify TypeSafe's product and API. semselect is independent
and does not contain proprietary Jev code or weights. API compatibility is not
a claim of equivalent quality, calibration, latency, or endorsement.
