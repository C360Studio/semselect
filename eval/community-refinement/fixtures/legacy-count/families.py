#!/usr/bin/env python3
"""Prepare legacy family workspaces and assemble the measured family manifest.

prepare: shallow-clone each family repo at its pinned commit (reusing an
existing clone), copy only the chosen subtree into its own workspace directory
and write a per-file manifest plus the SemSource tier-0 config.

assemble: combine the input spec, per-iteration manifests and KV counts into
eval/community-refinement/fixtures/families.json.

No inference, embeddings or labels are produced here.
"""

import argparse
import fnmatch
import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys

CODE_EXT = {".go", ".java", ".py", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".mts", ".cts",
            ".svelte", ".c", ".h"}
DOC_EXT = {".md", ".mdx", ".adoc", ".txt"}
# cpp is deliberately omitted: semsource routes ".h" to the C++ parser whenever
# cpp is declared (processor/ast-source/routing.go), and no family is C++.
LANGUAGES = ["go", "typescript", "javascript", "java", "python", "svelte", "c"]
PROVENANCE = ("legacy SemStreams capture (SemSource {semsource}, SemStreams {semstreams}); "
              "not a SemEngine result")


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


def ensure_clone(fam, src_root):
    repo = os.path.join(src_root, fam["id"], "repo")
    if not os.path.isdir(repo):
        os.makedirs(os.path.dirname(repo), exist_ok=True)
        git("clone", "--quiet", "--depth", "1", fam["repo"], repo)
    if fam.get("commit") and git("rev-parse", "HEAD", cwd=repo) != fam["commit"]:
        git("fetch", "--quiet", "--depth", "1", "origin", fam["commit"], cwd=repo)
        git("checkout", "--quiet", fam["commit"], cwd=repo)
    return repo, git("rev-parse", "HEAD", cwd=repo)


def select_files(fam, repo):
    chosen, unmatched = set(), []
    for pattern in fam["include"]:
        matches = glob.glob(os.path.join(repo, pattern))
        if not matches:
            unmatched.append(pattern)
        for m in matches:
            if os.path.isdir(m):
                for dirpath, dirnames, filenames in os.walk(m):
                    dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                    for f in filenames:
                        chosen.add(os.path.relpath(os.path.join(dirpath, f), repo))
            elif os.path.isfile(m):
                chosen.add(os.path.relpath(m, repo))
    max_bytes = fam.get("max_file_bytes")
    files = []
    for rel in sorted(chosen):
        if any(fnmatch.fnmatch(rel, ex) for ex in fam.get("exclude", [])):
            continue
        if max_bytes and os.path.getsize(os.path.join(repo, rel)) >= max_bytes:
            continue
        files.append(rel)
    return files, unmatched


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def license_line(repo, name):
    if not name:
        return None
    with open(os.path.join(repo, name), encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.strip():
                return line.strip()
    return None


def prepare(args):
    spec = json.load(open(args.input))
    ws_root = os.path.realpath(args.ws_root)
    if os.path.basename(ws_root) != "semselect-families":
        sys.exit(f"refusing to manage workspace root {ws_root}")
    os.makedirs(ws_root, exist_ok=True)
    manifest = {"workspace_root": ws_root, "families": []}
    for fam in spec["families"]:
        repo, head = ensure_clone(fam, args.src_root)
        files, unmatched = select_files(fam, repo)
        ws = os.path.join(ws_root, fam["id"])
        if os.path.exists(ws):
            shutil.rmtree(ws)
        entries = []
        for rel in files:
            dst = os.path.join(ws, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(os.path.join(repo, rel), dst)
            entries.append({"path": rel, "bytes": os.path.getsize(dst), "sha256": sha256_file(dst)})
        listing = "".join(f"{e['sha256']}  {e['path']}\n" for e in entries)
        manifest["families"].append({
            "id": fam["id"],
            "commit": head,
            "include": fam["include"],
            "exclude": fam.get("exclude", []),
            "max_file_bytes": fam.get("max_file_bytes"),
            "unmatched_includes": unmatched,
            "license_file_first_line": license_line(repo, fam.get("license_file")),
            "has_code": any(os.path.splitext(e["path"])[1] in CODE_EXT for e in entries),
            "has_docs": any(os.path.splitext(e["path"])[1].lower() in DOC_EXT for e in entries),
            "files": entries,
            "workspace_sha256": hashlib.sha256(listing.encode()).hexdigest(),
        })
    sources, roots = [], []
    for fam in manifest["families"]:
        path = f"/workspace/{fam['id']}"
        roots.append(path)
        if fam["has_code"]:
            sources.append({"type": "ast", "path": path, "languages": LANGUAGES, "watch": False})
        if fam["has_docs"]:
            sources.append({"type": "docs", "paths": [path], "watch": False})
    config = {
        "namespace": "semselect",
        "sources": sources,
        "source_roots": roots,
        "graph": {"embedder_type": "bm25", "index_workers": 4, "coalesce_ms": 200},
    }
    with open(args.config_out, "w") as fh:
        json.dump(config, fh, indent=2)
        fh.write("\n")
    with open(args.manifest_out, "w") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")
    for fam in manifest["families"]:
        print(f"{fam['id']}: {len(fam['files'])} files, code={fam['has_code']} "
              f"docs={fam['has_docs']} unmatched={fam['unmatched_includes']}")


def assemble(args):
    spec = json.load(open(args.input))
    lo, hi = spec["entity_range"]
    iterations = []
    for it_dir in args.iteration:
        manifest = json.load(open(os.path.join(it_dir, "manifest.json")))
        counts = json.load(open(os.path.join(it_dir, "counts.json")))
        iterations.append((os.path.basename(it_dir.rstrip("/")), manifest, counts))
    label = PROVENANCE.format(semsource=args.semsource, semstreams=args.semstreams)
    out = {
        "schema": "semselect.legacy-family-count/v1",
        "status": "measured family candidate list before capture and labeling",
        "provenance": label,
        "capture_profile": {
            "semsource_commit": args.semsource,
            "semstreams_version": args.semstreams,
            "semsource_image": args.image,
            "tier": "0 (graph.embedder_type=bm25; no HTTP embedder, no semembed, no LLM)",
            "config": "eval/community-refinement/fixtures/legacy-count/family-count.tier0.json",
            "ast_languages": LANGUAGES,
            "entity_range": [lo, hi],
            "evidence": args.evidence,
        },
        "exclusions": ("No labels, embeddings, neighbour results, mutual pairs, partitions, "
                       "review packets, constraints or retrieval questions exist yet. Counts are "
                       "entity totals from a legacy SemStreams capture, not a SemEngine capture."),
        "families": [],
    }
    for fam in spec["families"]:
        history = []
        for name, manifest, counts in iterations:
            m = next(f for f in manifest["families"] if f["id"] == fam["id"])
            c = counts["systems"].get(fam["id"], {"entities": 0})
            history.append({"iteration": name, "include": m["include"], "exclude": m["exclude"],
                            "max_file_bytes": m["max_file_bytes"], "files": len(m["files"]),
                            "entities": c["entities"]})
        name, manifest, counts = iterations[-1]
        m = next(f for f in manifest["families"] if f["id"] == fam["id"])
        c = counts["systems"].get(fam["id"], {"entities": 0, "by_type": {}, "by_domain": {}})
        edges = c.get("edge_proxy")
        if edges:
            # code.structure.* is file/folder/doc containment; the rest are
            # explicit code relations (calls, imports, extends, ...).
            edges["non_containment_edges"] = sum(
                n for p, n in edges["predicates"].items() if not p.startswith("code.structure."))
        out["families"].append({
            "id": fam["id"],
            "split": fam["split"],
            "role": fam["role"],
            "language": fam["language"],
            "repo": fam["repo"],
            "commit": m["commit"],
            "license": fam["license"],
            "license_file": fam["license_file"],
            "license_file_first_line": m["license_file_first_line"],
            "system_segment": fam["id"],
            "include": m["include"],
            "exclude": m["exclude"],
            "max_file_bytes": m["max_file_bytes"],
            "unmatched_includes": m["unmatched_includes"],
            "files": [f["path"] for f in m["files"]],
            "workspace_sha256": m["workspace_sha256"],
            "entities_total": c["entities"],
            "entities_by_type": dict(sorted(c.get("by_type", {}).items())),
            "entities_by_domain": dict(sorted(c.get("by_domain", {}).items())),
            "in_range": lo <= c["entities"] <= hi,
            "edge_proxy": edges,
            "iterations": history,
            "notes": fam.get("notes"),
            "provenance": label,
        })
    with open(args.out, "w") as fh:
        json.dump(out, fh, indent=2)
        fh.write("\n")
    for f in out["families"]:
        print(f"{f['id']}: {f['entities_total']} in_range={f['in_range']}")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    pp = sub.add_parser("prepare")
    pp.add_argument("--input", required=True)
    pp.add_argument("--src-root", default="/tmp/semselect-families-src")
    pp.add_argument("--ws-root", default="/tmp/semselect-families")
    pp.add_argument("--config-out", required=True)
    pp.add_argument("--manifest-out", required=True)
    pa = sub.add_parser("assemble")
    pa.add_argument("--input", required=True)
    pa.add_argument("--iteration", action="append", required=True,
                    help="iteration evidence dir with manifest.json and counts.json, oldest first")
    pa.add_argument("--semsource", required=True)
    pa.add_argument("--semstreams", required=True)
    pa.add_argument("--evidence", required=True)
    pa.add_argument("--image", required=True, help="semsource image ID used for the final iteration")
    pa.add_argument("--out", required=True)
    args = p.parse_args()
    prepare(args) if args.cmd == "prepare" else assemble(args)


if __name__ == "__main__":
    main()
