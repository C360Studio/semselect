#!/usr/bin/env python3
"""Write the SemSource tier-1 config for one pilot family and check its workspace.

Reads the measured family list (families.json) and the per-file manifest that
legacy-count/families.py prepare wrote for the same export, refuses to continue
unless the family's commit, file list and workspace hash agree, and writes a
config that ingests only that family: the HTTP embedder and model registry of
SemSource's shipped mvp.json, graph clustering on, and the identity-edge
weights and caps of the semantic profile's structural baseline (semantic edges
stay off, which SemSource cannot enable). No inference, embeddings or labels
are produced here. Legacy SemStreams capture; not a SemEngine result.
"""

import argparse
import hashlib
import json
import sys

# Same AST languages as the tier-0 family count, so the entity set matches.
LANGUAGES = ["go", "typescript", "javascript", "svelte", "python"]

# processor/graph-clustering semanticEnabledEntityIDBaseline at SemStreams
# v1.0.0-beta.160: the structural weights/caps the semantic profile keeps.
STRUCTURAL_BASELINE = {
    "include_siblings": True,
    "include_system_peers": True,
    "sibling_weight": 0.7,
    "max_siblings": 5,
    "system_peer_weight": 0.2,
    "max_system_peers": 8,
}


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--families", required=True, help="eval/community-refinement/fixtures/families.json")
    p.add_argument("--prepare-manifest", required=True, help="manifest.json from families.py prepare")
    p.add_argument("--family", required=True)
    p.add_argument("--mvp", required=True, help="SemSource configs/mvp.json (model registry source)")
    p.add_argument("--config-out", required=True)
    p.add_argument("--family-out", required=True, help="family entry, prepared file list and checks")
    args = p.parse_args()

    families = json.load(open(args.families))
    fam = next((f for f in families["families"] if f["id"] == args.family), None)
    if fam is None:
        sys.exit(f"family {args.family} is not in {args.families}")
    manifest = json.load(open(args.prepare_manifest))
    prepared = next((f for f in manifest["families"] if f["id"] == args.family), None)
    if prepared is None:
        sys.exit(f"family {args.family} was not exported by families.py prepare "
                 f"(generalization families need their own export; not supported yet)")

    problems = []
    if prepared["commit"] != fam["commit"]:
        problems.append(f"commit {prepared['commit']} != {fam['commit']}")
    if prepared["workspace_sha256"] != fam["workspace_sha256"]:
        problems.append(f"workspace_sha256 {prepared['workspace_sha256']} != {fam['workspace_sha256']}")
    if prepared["split"] != fam["split"]:
        problems.append(f"split {prepared['split']} != {fam['split']}")
    if [e["path"] for e in prepared["files"]] != fam["files"]:
        problems.append("file list differs from families.json")
    if problems:
        sys.exit(f"{args.family}: prepared workspace disagrees with families.json: " + "; ".join(problems))

    mvp = json.load(open(args.mvp))
    path = f"/workspace/{fam['id']}"
    sources = []
    if prepared["has_code"]:
        sources.append({"type": "ast", "path": path, "languages": LANGUAGES, "watch": False})
    if prepared["has_docs"]:
        sources.append({"type": "docs", "paths": [path], "watch": False})
    if not sources:
        sys.exit(f"{args.family}: no code or docs to ingest")
    config = {
        "namespace": "semselect",
        "sources": sources,
        "source_roots": [path],
        "graph": {
            "embedder_type": "http",
            "index_workers": 4,
            "coalesce_ms": 200,
            "enable_clustering": True,
            "entity_id_edges": STRUCTURAL_BASELINE,
        },
        "model_registry": mvp["model_registry"],
    }
    with open(args.config_out, "w") as fh:
        json.dump(config, fh, indent=2)
        fh.write("\n")
    out = {
        "provenance": fam["provenance"],
        "family": fam,
        "prepared": prepared,
        "workspace_root": manifest["workspace_root"],
        "config": {
            "path": args.config_out,
            "sha256": sha256_file(args.config_out),
            "mvp_json_sha256": sha256_file(args.mvp),
            "structural_baseline": STRUCTURAL_BASELINE,
            "semantic_edges": "off (SemSource passes no semantic_edges block to graph-clustering)",
        },
        "checks": {"commit": True, "workspace_sha256": True, "split": True, "files": True},
        "families_json_sha256": sha256_file(args.families),
    }
    with open(args.family_out, "w") as fh:
        json.dump(out, fh, indent=2)
        fh.write("\n")
    print(f"{fam['id']}: {fam['split']} {fam['commit'][:12]} files={len(fam['files'])} "
          f"code={prepared['has_code']} docs={prepared['has_docs']} workspace={fam['workspace_sha256'][:12]}")


if __name__ == "__main__":
    main()
