# Execution review record

The original [preparation review](review.md) and design freeze remain unchanged.
This supplement records review of the execution and derived evidence.

## Before inference

A separate read-only reviewer checked the runner, actual runtime adapter, bounded
lifecycle, exact request-byte verification, response preservation, warmups,
denominators and the requirement that both model families pass preflight before
either generates. The implementation owner reproduced malformed-choice and
invalid-UTF-8 failures in offline regressions before fixing them. All raw response
bytes are preserved, including responses rejected by the application contract.

The first Metal attempt stopped after Qwen preflight and clean shutdown, before
any generation. A bind check confused TIME_WAIT with an active listener. The
runtime owner reproduced three failing regressions, replaced the probe with a
strict connect/refused check, and passed 17 runtime tests. Independent review
verified all nine archived original source hashes, the original manifest, zero
inference requests and 128 `not_run` rows. It approved only this operational
amendment; inputs, labels, grading and the resource profile remained unchanged.

## Completed code and Metal evidence

The independent reviewer inspected all 100 code jobs / 224 native rows, including
input hashes, order, native options and exact grading. All three code arms score
18/32 on primary; all four persistent BM25 views also score 18/32, with unchanged
options. This is not a claim that their underlying similarity scores are equal.

It separately audited all 130 Metal request/response pairs: 128 formal and two
excluded warmups. Request hashes, original response-byte hashes and lengths,
native distributions, incompatible-tuple handling, exact grading, paired
corrections/regressions and family counts reproduce. All calls returned HTTP 200.
Primary Qwen and Kev both score 23/32; reverse Qwen scores 23/32 and Kev 22/32.
All four runtime instances exited zero and confirmed their loopback ports closed.

A separate operational audit checked actual token arrays against effective
context/batch settings, prefix-cache observations, usage, offload and shutdown.
Qwen has 65 full prompt evaluations and Kev has 195 native heads, including
warmups. Every task's first cached-token count is zero. Kev's effective batch is
512 despite requested 1024; its longest decision tail is 255 and fits. The audit
does not misread positive within-task progress as cross-request cache reuse.

## Reporting corrections

Independent review found two derived-report provenance weaknesses: reading the
file and hashing it separately could bind different live checkpoints, and the
report did not reject a result referring to another design freeze. Two failing
regressions demonstrated both issues before fixes. The report now grades and
hashes the same bytes and verifies the design. A third test ensures raw-selection
changes remain visible even when both tuples are invalid. None changes inference
or grading rules.

Report schema-valid but incompatible JSON separately from malformed JSON. Count
all invalid responses as failures; do not repair irrelevant node/field selections.
Show raw order changes as well as changes among valid pairs. Treat the full
three-head Kev request as the unit of application work, without claiming an
intrinsic architecture speed comparison. The short result must preserve code
successes lost, missing bindings and the opportunity for ordinary parser fixes.

CPU formal evidence and the final combined documentation require review after
the CPU run completes; passing preflight does not supply that approval.
