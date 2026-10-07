# Historical preparation notes — implementation stage

These notes describe what remained unverified **before** the bounded execution.
The [completed validation](../../../docs/validation-specialist-intent.md) and
[execution evidence](../../../docs/evidence/20261007-specialist-intent/README.md)
supersede the historical download, build and qualification status below. That run
verified the requested model bytes, corrected the CPU wheel closure, built the
pinned image and performed real token/feasibility checks. New runs must verify
their own actual environment; this historical design text is not a current claim
that the recorded experiment remains unrun.

These are preparation locks, not proof of a runnable environment or inference.

The model revisions and all config/tokenizer hashes were resolved from the public
Hugging Face API on 2026-10-07. Small ordinary Git files were downloaded and
SHA-256 hashed. Weight and sentencepiece hashes are publisher LFS metadata; the
model preparation command must verify their downloaded bytes. No specialist
weights have been downloaded or loaded in this implementation task.

`gliclass.json` and `deberta.json` pin the exact requested checkpoints. The
GLiClass 0.1.20 PyPI wheel was downloaded and its native pipeline inspected. Its
uni-encoder formatter places LABEL-prefixed descriptions, SEP, instruction and
query in that order for this model's `prompt_first=true`. Its single-label score
path uses `torch.softmax(logits, dim=-1)`. The adapter calls that pinned native
method and retains the unmodified logits and full score map. It bypasses only
the native tokenizer call, which otherwise silently enables truncation: the
same native rendered input is tokenized with truncation explicitly disabled.
DeBERTa's pinned config maps entailment to index 0 and not_entailment to index 1.
One FP32 forward batch contains all nine full premise/hypothesis pairs; softmax
of their entailment logits is normalized across the nine candidates.

Opaque IDs do not reach either specialist model's encoder; ID remapping is an
adapter check. Description order does reach the encoder. Score ties use the
frozen canonical operation order. Embedding ties use frozen example order, and
the margin compares the strongest alternative operation, matching protocol.json.

Bounded preparation (not performed automatically):

1. Resolve an immutable Python Linux ARM64 base image digest. Record actual
   architecture and image identity. Use the plan's 30-minute setup deadline.
2. In that exact ARM64 image, resolve `requirements.txt` using
   `python -m pip download --only-binary=:all: -r requirements.txt -d wheels`.
   The seeds pin direct versions, not an already verified transitive closure.
   A dependency incompatibility consumes the single corrective attempt; do not
   silently upgrade or substitute a model. GPU wheels are forbidden.
3. Run `python locks/prepare.py wheels --wheelhouse wheels --output
   requirements.lock.txt`. Build using the Dockerfile and the immutable
   `PYTHON_IMAGE` build argument. Installation requires every transitive wheel
   and hash; an incomplete closure fails. Preserve lock, wheels manifest, build
   log, full installed versions, Python version and resulting image digest.
4. Run `python locks/prepare.py model --arm gliclass --output MODEL_DIR` (and
   similarly deberta) to fetch and verify all locked model artifacts. The server
   repeats byte verification before loading and uses local-only loading.
5. Generate complete-input tokenizer records with `preflight.py` for every
   planned payload. Preserve records and identities. Test GLiClass native source
   hash, FP32, four threads, nine NLI pair batch and tokenizer ceiling in the
   actual container. No token preflight has been claimed from offline doubles.
6. Freeze the environment and verify it in the runner before inference. Run one
   loaded arm at a time with four CPUs and 4 GiB hard memory limit. The HTTP
   image binds the container interface; publish only `127.0.0.1:8099:8099` on
   the host. Native invocation defaults to loopback. The runner audits the actual
   container port mapping and resource limits before dispatch. Use
   `../supervise.py` with a new matching Docker `--cidfile` for an owned outer
   process deadline and explicit container cleanup. The specialist also enforces
   startup/request/lifetime watchdogs. Owner handles process registration,
   unique launch logs and verifies no other model remains loaded.

`requirements.lock.txt`, a base/container image digest, full environment and
real tokenizer/feasibility results remain unresolved until this workflow runs.
Dependencies in `dependencies.json` are immutable direct version selections,
not an assertion that their CPU ARM64 wheel combination has been validated.

The semembed source currently inspected at sibling revision
`7ceb5281c96b3664321f3f28c9d7f96acbb41843` accepts the OpenAI-compatible `/v1/embeddings`
float API. Its request model name does not select the model: the response model
is checked. Its usage token counts are approximate and cannot prove input fit.
The existing ONNX artifact lock is referenced by `models.json`; current runtime
bytes, actual precision, four-thread settings and full tokenizer behavior must
be verified separately before its results qualify as the reuse baseline.
