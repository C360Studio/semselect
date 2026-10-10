#!/usr/bin/env python3
"""Select the review candidates for one family (protocol freeze step 2).

Reads one family's frozen step-1 evidence directory (`summary.json`,
`structural/entities.jsonl`, `mutualknn/mutual_pairs.jsonl`) and writes the
frozen selection: at most 32 mutual-kNN candidates ordered by (1) crossing the
structural-only partition, (2) distance of cosine similarity from 0.8, (3) a
stable hash of the unordered entity IDs (owner ruling 6, 2026-10-10).
Explicit-dominated pairs are excluded. Under the explicit-only floor (ruling 5)
every remaining candidate adds a 0.9 edge where the vote had none, so the
protocol's semantic-no-effect class is empty by construction; the selector
still drops and counts any pair whose endpoint is missing from the frozen
partition. The fraction of all candidates covered is reported. Deterministic:
the output depends only on the input files. No inference and no labels.
Legacy SemStreams capture; not a SemEngine result.
"""

import argparse
import hashlib
import json
import os
import statistics
import sys

CEILING = 32
ANCHOR = 0.8
PROVENANCE = "legacy SemStreams capture; not a SemEngine result"
ORDER = ("crosses the structural-only partition first, then distance of similarity "
         "from the anchor, then the stable hash of the unordered entity IDs")
SIMILARITY = ("minimum of the two directed similarities (the value that gated the weaker "
              "direction); both are recorded")


def pair_hash(a, b):
    """Stable, order-independent hash of a pair: sha256 of the sorted IDs joined by newline."""
    return hashlib.sha256("\n".join(sorted((a, b))).encode()).hexdigest()


def similarity(pair):
    return min(pair["similarity_a_to_b"], pair["similarity_b_to_a"])


def sort_key(candidate):
    return (0 if candidate["crosses_partition"] else 1, candidate["distance"], candidate["hash"])


def select(pairs, community, ceiling=CEILING, anchor=ANCHOR):
    """Order the candidates and take the first `ceiling`.

    pairs: mutual pair records as cmd/mutualknn writes them.
    community: entity ID -> level-0 community ID of the frozen partition.
    Returns (selected, report); selected rows carry a 1-based rank.
    """
    excluded = {"explicit_dominated": 0, "endpoint_not_in_partition": 0}
    candidates = []
    for p in pairs:
        if p["explicit_dominated"]:
            excluded["explicit_dominated"] += 1
            continue
        ca, cb = community.get(p["a"]), community.get(p["b"])
        if ca is None or cb is None:
            excluded["endpoint_not_in_partition"] += 1
            continue
        s = similarity(p)
        candidates.append({
            "rank": None,
            "a": p["a"],
            "b": p["b"],
            "type_a": p["type_a"],
            "type_b": p["type_b"],
            "same_type": p["type_a"] == p["type_b"],
            "similarity": s,
            "similarity_a_to_b": p["similarity_a_to_b"],
            "similarity_b_to_a": p["similarity_b_to_a"],
            "distance": abs(s - anchor),
            "crosses_partition": ca != cb,
            "community_a": ca,
            "community_b": cb,
            "hash": pair_hash(p["a"], p["b"]),
        })
    candidates.sort(key=sort_key)
    selected = candidates[:ceiling]
    for i, c in enumerate(selected, 1):
        c["rank"] = i
    sims = [c["similarity"] for c in selected]
    cross_available = sum(1 for c in candidates if c["crosses_partition"])
    report = {
        "mutual_pairs": len(pairs),
        "excluded": excluded,
        "candidates": len(candidates),
        "selected": len(selected),
        "coverage": (len(selected) / len(candidates)) if candidates else None,
        "cross_partition": {"available": cross_available,
                            "selected": sum(1 for c in selected if c["crosses_partition"])},
        "selected_similarity": {
            "min": min(sims) if sims else None,
            "median": statistics.median(sims) if sims else None,
            "max": max(sims) if sims else None,
        },
        "selected_same_type": sum(1 for c in selected if c["same_type"]),
        "selected_type_pairs": dict(sorted(_count(selected).items())),
        "directed_similarities_equal": sum(
            1 for p in pairs if p["similarity_a_to_b"] == p["similarity_b_to_a"]),
    }
    return selected, report


def _count(selected):
    counts = {}
    for c in selected:
        k = f"{min(c['type_a'], c['type_b'])}/{max(c['type_a'], c['type_b'])}"
        counts[k] = counts.get(k, 0) + 1
    return counts


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_evidence(evidence):
    with open(os.path.join(evidence, "summary.json")) as fh:
        summary = json.load(fh)
    community = {}
    with open(os.path.join(evidence, "structural", "entities.jsonl")) as fh:
        for line in fh:
            r = json.loads(line)
            c = r["community_by_level"].get("0")
            if c is not None:
                community[r["entity_id"]] = c
    with open(os.path.join(evidence, "mutualknn", "mutual_pairs.jsonl")) as fh:
        pairs = [json.loads(line) for line in fh]
    return summary, community, pairs


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--evidence", required=True, help="one family's step-1 evidence directory")
    p.add_argument("--family", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--ceiling", type=int, default=CEILING)
    p.add_argument("--anchor", type=float, default=ANCHOR)
    p.add_argument("--identity-profile", default="explicit-only",
                   help="the evidence must have been captured under this profile (ruling 5)")
    args = p.parse_args(argv)
    if args.ceiling <= 0:
        sys.exit("--ceiling must be positive")

    summary, community, pairs = load_evidence(args.evidence)
    if summary.get("family") != args.family:
        sys.exit(f"{args.evidence} is {summary.get('family')!r}, not {args.family!r}")
    if summary.get("identity_profile") != args.identity_profile:
        sys.exit(f"{args.evidence} was captured under {summary.get('identity_profile')!r}, "
                 f"not {args.identity_profile!r}")
    selected, report = select(pairs, community, args.ceiling, args.anchor)
    out = {
        "provenance": PROVENANCE,
        "family": args.family,
        "split": summary.get("split"),
        "step": "2 (candidate selection), frozen; do not refill from labels or outcomes",
        "evidence": os.path.basename(os.path.normpath(args.evidence)),
        "inputs": {
            "summary_json_sha256": sha256_file(os.path.join(args.evidence, "summary.json")),
            "entities_jsonl_sha256": sha256_file(os.path.join(args.evidence, "structural", "entities.jsonl")),
            "mutual_pairs_jsonl_sha256": sha256_file(os.path.join(args.evidence, "mutualknn", "mutual_pairs.jsonl")),
            "partition_hash": summary.get("partition_hash"),
            "identity_profile": summary.get("identity_profile"),
            "mutualknn": summary.get("mutualknn", {}),
        },
        "parameters": {
            "ceiling": args.ceiling,
            "anchor": args.anchor,
            "order": ORDER,
            "similarity": SIMILARITY,
            "exclusions": "explicit-dominated pairs; pairs with an endpoint outside the frozen partition "
                          "(the semantic-no-effect class, empty under the explicit-only floor)",
        },
        "report": report,
        "selected": selected,
    }
    with open(args.out, "w") as fh:
        json.dump(out, fh, indent=2)
        fh.write("\n")
    r = report
    print(f"{args.family}: mutual {r['mutual_pairs']}, candidates {r['candidates']}, selected {r['selected']} "
          f"(coverage {r['coverage']:.1%}), cross-partition {r['cross_partition']['selected']}/"
          f"{r['cross_partition']['available']}, similarity {r['selected_similarity']['min']:.3f}.."
          f"{r['selected_similarity']['max']:.3f}")


if __name__ == "__main__":
    main()
