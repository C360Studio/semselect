# Independent specialist fixture and contract review

Date: 2026-10-07. Reviewer: `/root/label_review`, separate from fixture author and classifier/prompt/binder implementers.

Status: **PASS — FIXTURE/CONTRACT REVIEW ONLY**, following the pre-output revisions recorded below. This does not by itself authorize inference; remaining protocol, payload, scoring and runtime gates still apply. No model, classifier, binder, or baseline candidate outputs were inspected. Only plan, fixture text/gold, fixture metadata, and mechanical fixture tests were examined. No deliverable files were edited by this reviewer. The only reviewer-owned output is this record.

## Scope and boundary

Reviewed the complete 60 development and 120 heldout cases against `/tmp/semselect-specialist-plan.md`, including semantic operation, literal catalog binding, missing/ambiguous binding readiness, native option values, executable flag, provenance, subgroup quotas, family separation, and preselected feasibility/sensitivity subsets. Heldout query text, per-case gold and concrete defect examples are withheld from this record and from the tuning/main agents. Concrete examples were sent directly to the separate fixture author for revision before any outputs. The boundary is an access convention in a shared filesystem, not enforced filesystem isolation.

## Initial findings

- Gold operation/binding/readiness/options: 180 reviewed, 180 semantically consistent, 0 label defects found.
- Development: 60 cases, five per specialized operation, 20 no_override, 8 needs_binding; 34 families (26 pairs, eight singletons).
- Heldout: 120 cases, ten per specialized operation, 40 no_override split eight per required subgroup, 20 needs_binding (12 missing, eight ambiguous); 60 executable specialized cases; 64 families (56 pairs, eight singletons).
- Literal node/zone/field catalogs are disjoint between splits, and all retained bindings are literal members of the applicable catalog. Partial options preserve the operation and omit unresolved bindings.
- The 12 feasibility cases are development-only and cover all nine operations. The 24 sensitivity cases are heldout-only, with two per specialized operation and eight no_override covering all five subgroups, including six binding-deficient cases and a typo.
- All five mechanical fixture tests passed: schema/counts/bindings, unique IDs/text and disjoint family IDs/catalogs, manifest and subsets, hash freeze/author-check honesty, invalid-gold rejection. Command: `python3 -m unittest discover -s eval/specialist-intent -p 'test_fixtures.py' -v`.
- Split review found nine high-confidence near-template crossings within composition, negation/quotation and instruction-override constructions. Distinct scenario-family IDs and domain nouns alone do not establish template separation. These need revision. Generic short operation wording was not treated as leakage on its own.

## Initial reviewed bytes

| File | SHA-256 |
|---|---|
| development.json | d3b9f9d54b8bd8e191f183a198be75aeb08a09800453fb4d4ed7e5d49896d0b3 |
| heldout.json | 6ae7e6acd716cfa62477b2302ee3ce64dc3a760799cdbdc384019a1e24624b77 |
| manifest.json | a8fb71083a2b46aa3b9f447df75ab797fd851424eaf74dd45fb8113e9e59e20f |
| review.json | ae6d831c31c3301fb6a3a88f9846a19afa7a6d9296a22565dcd58d416aaa843b |
| README.md | fc5917fc494ee1fad2e81ef33961510659e17e64eb46500a984213e3782e8a06 |
| freeze.json | 0a561d338885fd79afc0625ab519efee9dd76cd05fccf8e88666396477dce6f5 |

## Limits

A stratified authored pilot does not estimate real traffic accuracy, establish absence of pretraining overlap, prove all paraphrase families are linguistically independent, or grant action authorization. Approval here, if subsequently recorded, concerns only fixtures/contract and never replaces artifact pinning, full-input token checks, frozen payload review, scoring review, or other pre-inference gates. Existing freeze/review artifacts correctly distinguish author checking from independent approval.


## Final pre-output re-review

The separate fixture author revised 13 heldout query/reason pairs in response to the nine identified near-template crossings, including adjacent paraphrases. I manually re-reviewed all 13 revised queries and their retained gold against the contract and development split. The new discourse situations resolve the identified crossing concerns without changing operation, binding, readiness, executable flags, quotas, family assignments or subset membership. Gold agreement remains 180/180; unresolved label defects 0; unresolved identified template-crossing defects 0. All five mechanical fixture tests pass again after the relevant revisions.

The fixture author retained the superseded freeze and defect explanation in `freeze-defects.json`, then froze the revised bytes before candidate execution. I inspected that record. Frozen author-review metadata intentionally still says independent review pending: this separate record supplies the independent approval for the exact bytes below, rather than rewriting the author's frozen assertion. No candidate outputs were inspected before or during either review pass.

| Revised/new file | SHA-256 |
|---|---|
| heldout.json | e1e6f88099fc2935797aac2d803c9ec9245872ef706a9c8c96cb2bc787267ba5 |
| freeze-defects.json | b1611000d467b5c61d6206ee4ee4fc4da3bcc4390dacd73cd07f345fa70a7526 |
| freeze.json | bb612a6f9b0859155da23529a3d56a1c0b72cfea7d85da76d6dde3f0f317dfc8 |

Development, manifest, author review and README hashes remain as recorded above. Independent approval is limited to these exact reviewed bytes. Any later semantic fixture changes require renewed review; ordinary operation grammar overlap remains a disclosed pilot limitation rather than a claim of broad language generalization.
