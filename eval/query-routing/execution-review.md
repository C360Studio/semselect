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

## CPU confirmation and intentional stop

Independent review verified all 65 CPU Qwen request/response byte pairs, grading
and operational records. Primary is 23/32 exact, with every raw selection matching
Metal; reverse is 22/32. Reversed R16 alone differs across hardware, with CPU
adding an incompatible node. Both Qwen containers exited zero without OOM.

The user explicitly stopped CPU Kev after three completed formal calls took
185–196 seconds each. One call was interrupted and 60 remain unattempted. A
separate operational review verified preserved response/preflight counts, twelve
completed decision heads, one interrupted head and stopped state for all four
owned CPU containers. Formal Kev exited 137 during bounded shutdown, without OOM;
its guard exited zero. The original failed/KeyboardInterrupt result is retained
alongside an operator-stop supplement. This is not a completed CPU Kev comparison.

A failing report regression showed that an unattempted case was previously
counted as a paired regression and hardware difference. The report now separates
unassessed cases and suppresses reported cohort accuracy for incomplete primary
runs, without changing the raw records or grading of completed responses.
All five report tests pass.

The same regression was expanded to cover both interrupted and unattempted cases
and reproduced inappropriate cohort latency summaries before a further reporting
fix. Incomplete primary cohorts now report neither median nor percentile latency;
completed request timings remain available in the raw records. Complete cohorts
retain their latency summaries.

The independent reviewer approved the final combined documentation, reporting
changes and evidence archive. It passed all five report tests and verified seven
archives, 389 original files and eleven published files. Recomputing from extracted
code/Metal/CPU bytes matched the combined summary and source hashes, apart from
extraction-dependent path strings. All five earlier archives and `summary-metal.json`
remain byte-identical to commit `9db8ba1`. Approval covers completed code, Metal
and CPU Qwen, and partial CPU Kev compatibility/timing observations only.
