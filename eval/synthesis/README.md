# Does a gate improve the answer?

**Both gates reduced unsupported assertions on this capture. Kev added no observed
advantage over Qwen JSON.** Better evidence is the next priority: the generator
received broad community descriptions and section headings, with too little detail
to fully answer any of the 12 successfully retrieved questions.

This follows the [24-case source pilot](../answerability/heldout/README.md). That
pilot tested evidence sufficiency. Here we captured the actual SemSource graph
answer path and kept the existing SemStreams generator prompt—including its
instruction to acknowledge missing information—unchanged.

## One small table

Primary trial; counts over **12 usable captures**. Each arm also retains the same
13th question's upstream temporal-service failure. “Unsupported” means a claim
not justified by the evidence the generator actually saw; it need not be false
in the underlying repository. No fully supported answer was possible on this set.

| Generator | Added gate | Unsupported assertions | Useful partial answers | Refused / deferred | Degraded |
| --- | --- | ---: | ---: | ---: | ---: |
| Qwen3-0.6B, CPU | None | 10 | 0 | 1 | 1 |
| Qwen3-0.6B, CPU | Qwen JSON | 1 | 0 | 11 | 0 |
| Qwen3-0.6B, CPU | Kev | 1 | 0 | 11 | 0 |
| Qwen3.5-4B, Metal | None | 3 | 1 | 8 | 0 |
| Qwen3.5-4B, Metal | Qwen JSON | 0 | 1 | 11 | 0 |
| Qwen3.5-4B, Metal | Kev | 0 | 1 | 11 | 0 |

Both gates run on **Metal**, including when the generator runs on CPU. These are
separate generator strata, not a CPU-versus-GPU speed test. The 0.6B image is the
shipped wiring-smoke default. The 4B model is an explicit stronger substitution.
One 4B “unsupported” result invents a document section while correctly withholding
the requested number; it is a grounding error, not a wrong numeric answer.

The second trial produced the same gate decisions and the same 4B outcome counts.
The 0.6B baseline had 11 unsupported answers and one refusal, with no timeout.
Repeats reuse the same questions. Independent grading used hidden model/arm
identities; eight borderline answers and their adjudication remain inspectable.

## Two examples that explain the result

**A gate helped with missing detail.** The readiness question retrieved a heading
about a stable registration handle, but no polling protocol or field names. The
4B generator nevertheless recommended polling through the registration handle.
Both gates deferred, avoiding that unsupported recommendation.

**A gate was not enough.** The worktree question exposed “Cadence: coherent
checkpoints, not churn,” but no disk-deletion rule. Both gates allowed it, despite
the strict sufficiency rule requiring all requested details. The 0.6B generator
then invented disk permissions. The 4B generator gave the limited cadence fact
and explicitly said the permission rule was missing. The final answer depended
on generator behavior as well as the gate. An answerability “allow” grants no
permission to act.

## Verdict: improve the evidence, then test a balanced set

This capture shows a useful gating effect, but **does not yet justify adding a
classifier generally**. All 12 inputs lack a complete answer, so always deferring
is also a strong control. We cannot estimate how often either gate would block
fully answerable questions. The existing generator already refused many cases;
blocking those refusals adds no factual-correctness benefit.

Each gated arm made 12 gate calls plus one generator call; the baseline made 12
generator calls. Timing is cache-sensitive: Qwen reused nearly complete prompts
on repeat passes, while Kev reprocessed every prompt. These results do not show
an inherent decision-head efficiency advantage.

**Use a gate when** a balanced test on your actual evidence shows it prevents
unsupported answers while preserving useful supported answers at acceptable cost.
**Prefer the existing path when** retrieval plus the generator already meets that
standard. Improve missing source evidence before expecting a selector to repair it.

Next: compare this community-summary input with the relevant verbatim source
passages, retaining answerable and incomplete cases. Confirm any useful result
with CPU-only gates before asking for CUDA work. The
[validation record](../../docs/validation-synthesis.md),
[frozen evidence rubric](captured/evidence-review.json) and
[complete run](../../docs/evidence/20261005T184218Z-synthesis-replay/README.md)
contain reproduction details, failures and every recorded outcome.
