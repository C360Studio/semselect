# Specialist-intent authored fixtures

This is a stratified authored pilot: 60 development cases and 120 held-out
cases. It contains no production traffic, and aggregate accuracy is not an
estimate of production accuracy. Synthetic wording cannot establish freedom
from model pretraining overlap. Existing query-routing fixtures were consulted
only as development background; none was imported as a held-out case.

`development.json` is tuning material. `heldout.json` is restricted by an **access
convention**, not filesystem isolation. The separate fixture owner authored and
checked both splits without candidate outputs. Prompt, rule and binder owners
must not read held-out text or gold while tuning. A label reviewer may inspect
it without editing classifiers or prompts. Mechanical validation and full-input
tokenization may read held-out data but must not print its text or labels into
tuning channels. The runner must send only query text and permitted task context,
never the case ID, family, provenance, stratum, reason or gold.

Each case has an opaque ID, scenario family, wording/subgroup stratum, authored
provenance, query, supplied literal catalog and gold operation/readiness/options.
Catalog membership is a binding constraint, not evidence of authorization or
real-world existence. Node, zone and numeric field catalogs differ between
splits. Similarity and count need no separately extracted argument. Path and zone
need one literal node or zone; numeric aggregates need one literal field.
Missing or multiple relevant literals produce `needs_binding`, preserve the
partial intent/type options and are never executable. `no_override` has empty
options and is never counted as an executable specialized plan.

Scenario families group related scenarios and intentional paraphrases in one
split; development uses campus/building scenarios and held-out uses industrial,
canal and manufacturing scenarios. Both necessarily share the fixed operation
vocabulary and some ordinary grammatical forms. This is a controlled split of
authored families, not a proof of broad linguistic or domain generalization.

`manifest.json` records family assignments, exact aggregate counts and fixed
feasibility/sensitivity IDs without per-case text or labels. The 12 feasibility
queries are development only. The 24 sensitivity IDs were selected before
candidate execution to cover all operations, missing bindings, typos and all
five no-override subgroups; repeats are not independent examples.

`review.json` honestly records author checking and the outstanding independent
label review. `freeze.json` hashes the dataset bytes and metadata. A hash freeze
does **not** mean independent approval. Independent review must precede inference
and be recorded separately without rewriting frozen labels. A discovered label
defect requires a retained defect record and a new explicit freeze decision;
never silently repair labels after seeing candidate outcomes.

Mechanical checks: `python3 -m unittest discover -s eval/specialist-intent -p
'test_fixtures.py'`. These validate construction and hashes, not model quality
or independent semantic agreement.
