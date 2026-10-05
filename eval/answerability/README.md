# Does the evidence answer the question?

Follow-up: the [24-case real-source pilot](heldout/README.md) now exposes failures
and a limited aggregate advantage for Kev. This page preserves the earlier
teaching result.

**Verdict: worth a held-out test of the task; keep Qwen JSON as the model baseline.**
On these 12 teaching cases, both Qwen and Kev identified missing/conflicting
answers that a control with no semantic gate allowed through. Kev did not improve
any decision over Qwen. This explains a useful job for a classifier, but does not
yet justify adopting a specialized decision model.

## Three examples explain the boundary

- **Code is enough — [A01](examples.md#a01).** “What port does seminstruct use?”
  The caller supplies an exact fact key and `8083` from a pinned service table.
  Code resolves it. Neither model is called.
- **Relevance is not an answer — [A06](examples.md#a06).** An employee asks for
  the monthly expense deadline. The passage describes the portal and Finance
  review, but supplies no date. The no-added-gate control allows it;
  Qwen and Kev both defer.
- **Combining evidence can be enough — [A10](examples.md#a10).** One passage
  assigns travel claims to category T; another says Finance Operations reviews
  category T. Both models allow an answer to “Who reviews travel claims?”
  Deferring everything would lose this useful case.

Neither model failed on this small set. That is a reason to test unseen evidence,
not evidence of general reliability. Read all [12 complete examples](examples.md).

## Measured on the laptop

Apple M3 Pro, native Metal, 2026-10-05. The main result was fixed before inference:
**trial 1, normal ordering, 12 cases: five answerable and seven unsupported.**
Shared coded facts/applicability checks resolve four correctly: one allow and
three deferrals. The other eight need a text judgment.

| Approach after shared code checks | Unsupported allowed / 7 | Answerable allowed / 5 | Answerable deferred / 5 | Median decision time on 8 unresolved cases |
| --- | ---: | ---: | ---: | ---: |
| No added semantic gate | 4 | 5 | 0 | <1 ms |
| Qwen3.5-4B JSON | 0 | 5 | 0 | 553 ms |
| Kev-4B through semselect | 0 | 5 | 0 | 530 ms |

No invalid responses, transport errors or unnecessary deferrals occurred. Repeats
and reversed evidence/candidate order retained every label: 32/32 model calls
correct per model across four correlated views. These remain **12 examples**.

Timing includes preparation and HTTP, excluding startup/warmup. Caching was enabled
in both configurations, but Qwen reused prefixes while Kev reprocessed each full
prompt. Models and serving paths also differ. The small timing difference does
not justify switching models.

## What this comparison means

The control allows unresolved cases after shared code checks. It does not rerun
fusion or establish four bad final answers: the existing generator could already
refuse. The fact records and metadata are trusted teaching inputs, not an existing
SemSource output contract. Expense policies are fictional; A01 uses a pinned
SemSource excerpt. Labels were reviewed before inference and excluded from
requests. No prompts or thresholds were tuned after viewing results.

Allow means “evidence suffices to attempt an answer,” not permission for a business
action. This experiment does not test calibration, actual Jev or CPU/CUDA quality.

## Reproduce or inspect

```sh
task answerability:validate  # offline fixture checks
task answerability:metal     # cached pinned models and native build required
```

See the [method, checks and resource record](../../docs/validation-answerability.md)
and [complete raw run](../../docs/evidence/20261005T172542.446287Z-answerability-metal/comparison.json).

## Next bounded step

Freeze unseen real-source families and compare the same approaches, including
applicable code checks. Then test whether a gate improves the complete answering
pipeline enough to justify its extra call. Repeat a useful result on CPU/Docker
before asking for CUDA help.

**Use an answerability check when relevant text can omit the requested fact and
that distinction matters to the caller. Prefer code when facts are explicit;
prefer the existing model/pipeline when an added classifier shows no useful gain.**
