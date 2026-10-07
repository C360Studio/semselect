# Bounded specialist intent pilot

This implements the 2026-10-07 specialist-classifier test design as an isolated
evaluation harness. **The bounded real-inference run is complete: keep Qwen4B
and the improved-code baseline; the tested specialists did not qualify.** See the
[validation report](../../docs/validation-specialist-intent.md),
[published evidence](../../docs/evidence/20261007-specialist-intent/README.md) and
[current app decision](../../docs/when-to-use.md#current-query-classification-decision).
The native service and model defaults remain unchanged.

The [implementation-only validation](validation.md) below predates that execution.
Original freezes and exact graded sources remain in the evidence archive; current
README updates are navigation changes, not a rewrite of the measured run.

The machine contract is [protocol.json](protocol.json). The authored fixtures
contain 60 development and 120 held-out cases, including 20 held-out specialized
queries with unresolved bindings. A separate fixture owner prepared them; an
independent reviewer checked every label and resolved template overlap before
outputs. See [fixture review](reviews/fixtures.md), the superseded freeze record
in `fixtures/freeze-defects.json`, and [implementation review](reviews/implementation.md).
Those records are not approval of an unrun environment or token manifest.

## Implemented comparison

- GLiClass instruct-base v1.0 and DeBERTa-v3-base-zeroshot-v2.0-c use pinned native
  label/logit mappings, FP32, complete 512-token input checks and nine operations.
- Qwen uses the existing pinned 4B GGUF, one operation-only JSON response,
  thinking disabled and actual llama.cpp template/tokenization preflight.
- The unchanged SemStreams keyword/BM25 driver and example set are reused.
  Default and development-selected BM25 thresholds remain separate. A small
  evaluation-local improved rule arm and one caller-owned literal binder are
  tested separately; native code SearchOptions remain visible in raw records
  and the report.
- Semembed uses the same example texts as BM25, nearest-example cosine,
  example-order ties, and a competing-operation margin. Example vectors are
  reused after development; query responses are never cached. Its existing
  artifact lock is retained. Each new run must verify its actual runtime,
  precision, thread settings and tokenizer behavior; the completed run's checks
  are preserved in the [execution evidence](../../docs/evidence/20261007-specialist-intent/README.md).

The runner separates selected, deferred, error and unattempted. `no_override`
is an ordinary operation. Binding readiness is never a confidence deferral.
No classification runs a search or authorizes an action.

## Offline checks

From the repository root:

```sh
task specialist:test
```

This runs the existing Go driver race tests, builds it with cached pinned
dependencies, and runs all specialist tests including actual keyword/BM25
calls, HTTP deadline tests and owned-process cleanup. Loopback socket access
is needed for the HTTP tests. No inference, model download or Docker runtime
launch occurs. [validation.md](validation.md) records observed checks and limits.

## Preparation and boundaries

Keep results, downloaded model artifacts and supervisor logs outside this source
directory, for example under `results/specialist-intent/RUN_ID/`. The runner
creates new files/directories and refuses to overwrite a prior stage. Source,
fixture or protocol changes invalidate a prepared run.

1. For a new run, reproduce the bounded environment preparation and verify its
   actual artifacts. The completed Linux ARM64 dependency closure, image digests
   and setup correction are preserved in the [execution evidence](../../docs/evidence/20261007-specialist-intent/README.md);
   [preparation notes](locks/README.md) retain the earlier design history.
   Acquire the exact requested checkpoints, verify every byte, and preserve
   licenses. An unavailable arm is recorded explicitly, never substituted.
2. Use `supervise.py` for setup (30 minutes cumulative, at most two attempts)
   and externally owned runtimes. Use one supervisor record root throughout
   the run: its lock prevents two owned models from being loaded together.
   Inspect existing processes before launching; record background contention.
   Register real OS PIDs and unique launch logs in the host application when
   launching from an agent. The supervisor does not register them itself.
3. Record actual startup/readiness, installed versions, image ID, artifact hashes,
   architecture, precision, CPU time and cgroup memory in an environment JSON.
   `environment.template.json` lists the required fields; its nulls and
   `verified:false` deliberately cannot pass validation. Use actual evidence,
   not copied assertions. Cold startup and index setup are separate from warm
   latency. Semembed's approximate response usage is not a token-count proof.
4. Finish tuning on development only. Tuners must not inspect held-out text,
   gold or candidate outputs. Shared files are governed by an access convention,
   not claimed filesystem isolation. The runner may read held-out bytes only
   to generate/check frozen requests and, after selection, to score results.
5. Prepare immutable source/request manifests. Preparation performs no inference:

```sh
task specialist:prepare -- --run results/specialist-intent/RUN_ID \
  --driver /tmp/semselect-specialist-query-driver
```

The preparation captures current relevant sibling source hashes read-only. A
material mismatch or missing checkout stops code execution until the comparison
target is resolved; the historical pin is never called current production.
`source/` preserves the exact implementation/fixture/reference bytes. Preparation
also emits `requests-ARM.json` arrays for token preflight.

For example, after acquiring the exact model artifacts and qualified environment:

```sh
python3 eval/specialist-intent/preflight.py --arm gliclass \
  --model-dir /absolute/path/to/gliclass \
  --requests results/specialist-intent/RUN_ID/requests-gliclass.json \
  --output results/specialist-intent/RUN_ID/tokens-gliclass.json
```

DeBERTa follows the same command. For Qwen use `--arm qwen --qwen-url
http://127.0.0.1:PORT --environment ENV.json`; it calls the actual pinned runtime's
`/apply-template` and `/tokenize`, reserving 64 output tokens. For embeddings use
`--arm embeddings --model-dir ... --environment ENV.json --runtime-audit AUDIT.json`.
The embedding audit must identify the actual runtime SHA, `max_length:512` and
verified `verbatim_with_special_tokens:true` preprocessing; otherwise preflight
stops rather than assuming semembed's behavior. Complete-input tokenizer identity
must match the loaded artifact manifest. No arm silently truncates or chunks.

After preparing a run, independent payload/scoring review must supply its
`review.json` with `freeze_sha256`, reviewer identity, `independent:true`, and
`fixture_labels_verified`, `payloads_verified`, `scoring_verified` all true.
Do not copy an earlier code-review approval into a new execution record. If
actual token fit fails, that is a protocol defect requiring a new preparation,
not permission to trim just one model's input.

## Staged execution

Use `runner.py run --run RUN --stage STAGE --arm ARM` in the fixed order
`code`, `embeddings`, `gliclass`, `deberta`, `qwen`. Complete **all development
arms before selecting or running any held-out case**. The code arm takes
`--driver /tmp/semselect-specialist-query-driver`. Learned arms also take
`--url`, `--environment ENV.json` and `--tokens TOKENS.json`.

```sh
python3 eval/specialist-intent/runner.py run --run RUN \
  --stage development --arm code --driver /tmp/semselect-specialist-query-driver
```

Use actual loopback endpoints: specialists `/classify`, Qwen
`/v1/chat/completions`, semembed `/v1/embeddings`. CPU images must publish only
host loopback, e.g. `-p 127.0.0.1:8099:8099`, with `--platform linux/arm64
--cpus 4 --memory 4g`. The image's entrypoint binds its container interface.
The runner verifies live image identity, architecture, cgroup limits and
endpoint-to-container port mapping before requests, then records cumulative
peak memory/CPU/OOM after each stage. Restarted containers retain the exact
development-selected runtime/artifact/dependency identity.

Use an owned foreground Docker launch with a new cidfile under supervision:

```sh
python3 eval/specialist-intent/supervise.py --records RUN/supervisor \
  --arm gliclass --phase runtime --cidfile /absolute/new/container.cid -- \
  docker run --cidfile /absolute/new/container.cid --platform linux/arm64 \
  --cpus 4 --memory 4g -p 127.0.0.1:8099:8099 \
  -v /absolute/model:/model:ro IMAGE_ID --arm gliclass --model-dir /model
```

Start the runner separately while this foreground supervisor is alive. Never
reuse a preexisting cidfile. The supervisor terminates only its own process
group/container, retains logs and verifies shutdown. Specialist watchdogs bound
startup to 120 seconds, each request to ten seconds and lifetime to at most
1,920 seconds. Native Qwen can be supervised directly; Docker-backed semembed
uses the explicit cidfile cleanup path. Record actual readiness, don't infer it
from a living process. Retain cgroup observations before removing a container.

If setup/compatibility fails, close that arm with `--unavailable 'recorded reason'`.
All planned rows remain unattempted. This is diagnostic continuation and cannot
produce a promotion. Fatal input/mapping failure, OOM, exhausted budgets and
three consecutive runtime errors persist across later stages. Setup failures
need their original logs; a prose reason is not a compatibility measurement.

Development performs three excluded warmups, the fixed 12-case feasibility set
and at most two 60-case prompt variants. Thresholds use saved scores, not extra
calls. An optional `probe-cpu` command for Qwen before its Metal development stage
uses at most 12 designated development cases plus three warmups, a 60-second
request deadline and ten-minute total cap. Its results never supply full-cohort
CPU quality or a CPU speed ratio. CPU probe work counts against Qwen's own
30-minute inference allowance.

```sh
python3 eval/specialist-intent/runner.py select --run RUN
```

Selection fixes the specialist nominee, both prompt/acceptance choices, BM25
threshold, code winner and reuse comparator before held-out access. It binds
development evidence and environment hashes. All held-out phases reject drift.

Next run `--stage primary` for all arms in the same order. Run `--stage sensitivity`
for GLiClass, DeBERTa and Qwen in that order: 24 fixed cases with reversed labels
and separately remapped IDs. Neither specialist encoder consumes opaque IDs, so
its ID-remapping result is an adapter-mapping check only.

`runner.py load --run RUN --url ... --environment ... --tokens ...` is available
only for the preselected nominee after all preceding gates pass. It dispatches
200 requests at concurrency two within five minutes, stops on fatal/repeated
errors, and preserves every unattempted row. It does not add independent quality
examples. Each model retains its own 30-minute allowance; embeddings have ten
minutes and code five. Warmups, failures, CPU probe and index setup stay in the
appropriate budget ledger.

## Reporting and decision

```sh
python3 eval/specialist-intent/runner.py report --run RUN
```

Each invocation creates a new timestamped report directory with `summary.json`
and a readable `verdict.md`. It reports intent, accepted intent, complete binding/
readiness and executable success separately, alongside native code full outputs,
failure/completion denominators, success latency and timeout-inclusive durations,
strata, confusion, recall, coverage/error tradeoffs and paired regressions.
Tradeoff curves on held-out scores are diagnostic, never a new threshold choice.
Repeated scenario families use a seeded cluster bootstrap; other proportions
use Wilson intervals. Bootstrap intervals can degenerate at zero/all-success
outcomes and do not certify rare-error safety.

Every advancement gate must pass against **both** the fixed code winner and
embedding baseline, plus Qwen quality and the mandatory load check. No valid
runtime or telemetry can be inferred from missing rows. A result is `prototype
one backend`, `keep the baseline`, or `inconclusive`. Any prototype still needs
representative traffic, a caller error budget and separately authorized service
work. The existing measured guidance in `docs/when-to-use.md` remains unchanged
until this experiment produces real evidence.
