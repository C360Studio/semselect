# Tier-1 family capture (freeze steps 1 to 5)

**Claim, 2026-10-09. Nothing has been captured yet.** This directory will hold
the per-family capture that turns the measured family list in
[`../families.json`](../families.json) into frozen pilot fixtures, following
the protocol's [corpus, labels and evidence freeze](../../README.md#corpus-labels-and-evidence-freeze)
and the owner rulings recorded on
[issue #5](https://github.com/C360Studio/semselect/issues/5). Every output is
a **legacy SemStreams capture; not a SemEngine result**, until the SemEngine
port exists and confirms the provider chain.

## What each family will get

| Step | Output | How |
| --- | --- | --- |
| 1. Neighbours and partition | directed neighbour results (`k=8`, threshold recorded), mutual pairs, explicit and identity memberships, effective weights, the structural-only partition, embedding identity (semembed image digest and model), source and config hashes | one legacy tier-1 stack **per family**, so the similarity index holds that family alone; the [legacy capture tool](../../legacy-capture/README.md) reads neighbours and explicit edges; the structural-only partition comes from the legacy clustering, which never had the semantic tier on |
| 2. Candidate selection | at most 32 effective candidates, with the fraction of all candidates they cover | deterministic selection, offline; the "distance from 0.8" key is reconsidered after the first family's distribution is seen (ruling 2) |
| 3. Reviewer packets | one packet per candidate, 8,192-byte state bound, per-entity bundle variant, both tokenizers verified | serializer, offline |
| 4. Co-membership constraints | 20 per family, ten positive and ten negative | people, from full source evidence |
| 5. Retrieval queries | 6 per family with gold evidence sets | people |

Labels (`keep`, `suppress`, `defer`) follow step 3: annotator A is the owner;
annotator B may be a Codex session; the owner resolves disagreements before
inference, and the record discloses which labels are model-written (ruling 3).

## Order

Development families first (semselect-service, semselect-docs,
semengine-natsclient, semengine-message), then the eight held-out families,
then the two generalization families. Held-out captures are frozen before any
held-out label is read.

## Stop-point

The first piece of work is the per-family runner: export one family from
`families.json` at its pinned commit, bring up the legacy tier-1 stack on it,
wait for `embedding.ready`, run the capture tool, read the structural-only
partition, write the evidence directory with `SHA256SUMS`, tear down. No
runnable task is claimed yet.
