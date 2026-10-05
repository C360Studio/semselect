# Laptop and GPU validation

The first GPU target is native macOS on Apple Silicon. Docker Desktop's Linux
CPU path remains available; the Metal launcher runs llama.cpp and the same Go
guard as host processes. The public API and caller-owned decision policy stay
the same. Establish the CPU/Docker and Metal comparison first; Windows/NVIDIA
validation is a separate follow-up.

## Native Apple Silicon

Requires macOS/arm64, Apple's Command Line Tools (`xcode-select --install`),
Go 1.26.4+, Python 3.11+, curl and Task. The build downloads a SHA-256-pinned
CMake 4.1.2 wheel into `.native/`; it does not install global packages. The pinned
llama.cpp uses embedded Metal shader source, compiled by Metal at runtime, so this
path does not require the separate `xcrun metal` offline compiler.

```sh
task model:fetch
task metal:build
task down             # free port 8084 if the Docker service is running
task metal:serve      # foreground; Ctrl-C stops its runtime and guard
```

From another terminal, use the ordinary `task smoke`, `task evaluate` or HTTP
client at `http://127.0.0.1:8084`. Native ports are fixed: guard 8084, runtime 8085.
`SEMSELECT_PORT` configures Docker and ordinary client tasks, so leave it at 8084
for this native workflow. The runtime is also loopback-bound; local programs can
access its full API directly. The guard's admission/input bounds apply on 8084.
Neither process has a container memory or CPU quota.

The launcher checks both ports before startup, verifies the GGUF and built
runtime files, waits at most 180 seconds per service for readiness, and requires log evidence
that all model layers were offloaded. It runs one model at a time and refuses a
second build/run from the same checkout. Stop it before rebuilding. Shutdown
terminates only owned processes, with a bounded wait and forced cleanup if needed.
Source/tools/build outputs in `.native/` are managed, disposable files: builds
replace extracted source/tools and clean prior objects. Model files stay cached.

## Matched 4B comparison

```sh
task metal:baseline:fetch
task metal:evaluate
task metal:baseline:evaluate
```

Run these with `metal:serve` stopped. Each evaluation starts the model, runs one
excluded warmup plus the same 48 routing requests, then shuts down. Kev additionally
runs the three-primitives smoke. There are no retries or prompt changes based on
results. Runs have separate timestamped directories beneath
`results/metal-kev-4b/` and `results/metal-qwen35-4b/`, containing evaluation JSON,
resource/provenance JSON, runtime diagnostics and guard logs. Preserve each run's
files together. A failed run cannot inherit an earlier run's evaluation JSON.

Both use the same pinned llama.cpp commit, M3 Pro GPU, Q4_K_M quantization class,
four CPU threads, 4096-token context, 512-token batch/microbatch, and one slot.
The baseline uses Qwen3.5-4B chat with constrained JSON and thinking disabled;
it is a comparison of the seminstruct-style protocol, **not** a rebuilt release
of the existing seminstruct container. Model architecture, quantization details,
prompt/template format, output generation and cache behavior still differ.
Parameter parity makes this more informative than the old 4B-versus-0.6B run,
but it does not isolate the contribution of decision-head training.

Resource JSON records runtime/model hashes, commands, hardware and OS, confirmed
layer offload, total child CPU seconds and the largest child lifetime peak RSS.
Those OS counters include startup, warmup, guard and evaluator. Peak RSS is not
summed memory, an interval-only measurement, or GPU/unified-memory allocation;
do not compare it directly with the CPU run's cgroup memory peak. Native logs
also report Metal buffer allocations. Timing excludes model startup and warmup.
The laptop is shared, with no controlled thermal state; these remain small smoke
observations rather than a hardware benchmark.

## Same-model output-format comparison

```sh
task compare:metal
task compare:cpu
```

Run these sequentially, with other owned inference stopped. Both use the pinned
Qwen3.5-4B GGUF and llama.cpp revision, compare schema-constrained JSON with
one-token option scores, and disable prompt-prefix reuse. Each runs two trials
of the 24 cases in both candidate orders: 96 measured calls per format. The CPU
command requires the existing `semselect-runtime:dev` image built from this
checkout. Both use an isolated loopback port and clean up their owned runtime.

The [scoring validation report](validation-scoring.md) records the method,
complete evidence, timing scope and compatibility failures. Its latencies are
not interchangeable with the earlier cache-enabled model comparison above.
The scorer is evaluation code; the production service still serves Kev's native
SystemOne readouts.

## Windows/NVIDIA handoff

After the local baselines, use a teammate's real NVIDIA machine for the next target. Start with Windows
plus WSL2/Linux CUDA to share the Linux packaging; native Windows is a separate
support decision. Before claiming support, record the exact Windows/WSL/kernel,
GPU and VRAM, driver, CUDA toolchain, runtime commit, model hashes and launch flags.
Build llama.cpp's CUDA backend at our same pin, verify actual GPU offload, and run
the same primitives and 48-request evaluation for both 4B models sequentially.
Return complete logs and evaluation/resource files, including failed runs.

Acceptance includes clean startup/shutdown, checksum rejection, bounded readiness,
over-context rejection, the guard's contract checks, valid native primitives and
all measured request outcomes. Accuracy and latency are observations, not a
cross-hardware pass threshold. A successful build alone does not validate CUDA.
No CUDA image or Windows installer is claimed by this Metal change.

A dot can coordinate local Codex work on its owner's connected computer; current
documentation allows one personal computer per dot. It does not document a
specific cloud GPU entitlement or a pool of teammates' GPUs. Teammates can run
this handoff with their own Codex/dot access and return artifacts through the
repository. A separately authorized shared GPU host is another option.
See [dot computer access](https://learn.chatgpt.com/docs/dots/computers-and-apps)
and [Codex remote connections](https://learn.chatgpt.com/docs/remote-connections).
