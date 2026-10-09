#!/usr/bin/env python3
"""Prepare legacy family workspaces and assemble the measured family manifest.

prepare: export each repository's committed tree at its pinned commit from the
local sibling checkout (`git archive`, so uncommitted files are never read),
copy only the chosen subtree into its own workspace directory and write a
per-file manifest plus the SemSource tier-0 config.

dupcheck: hash every captured file and compare development families with
held-out families: identical SHA-256, and same-basename files whose lines are
more than 80% identical according to `diff`.

assemble: combine the input spec, per-iteration manifests and KV counts into
eval/community-refinement/fixtures/families.json.

No inference, embeddings or labels are produced here.
"""

import argparse
import fnmatch
import glob
import hashlib
import io
import itertools
import json
import os
import shutil
import subprocess
import sys
import tarfile

CODE_EXT = {".go", ".py", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".mts", ".cts", ".svelte"}
DOC_EXT = {".md", ".mdx", ".adoc", ".txt"}
# c and cpp are omitted: no family carries C or C++ sources.
LANGUAGES = ["go", "typescript", "javascript", "svelte", "python"]
PROVENANCE = ("legacy SemStreams capture (SemSource {semsource}, SemStreams {semstreams}); "
              "not a SemEngine result")
NEAR_DUPLICATE_RATIO = 0.8
HERE = os.path.dirname(os.path.abspath(__file__))


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


def export_repo(name, spec, repos_root, src_root):
    """Extract the committed tree of a local checkout into src_root/<name>@<commit>."""
    local = os.path.join(repos_root, spec["local"])
    commit = git("rev-parse", "--verify", spec["commit"] + "^{commit}", cwd=local)
    dest = os.path.join(src_root, f"{name}@{commit}")
    marker = os.path.join(dest, ".semselect-export")
    if not (os.path.isfile(marker) and open(marker).read().strip() == commit):
        if os.path.exists(dest):
            shutil.rmtree(dest)
        os.makedirs(dest)
        pathspec = ["."] + [f":(exclude,glob){p}" for p in spec.get("export_exclude", [])]
        tar = subprocess.run(["git", "archive", "--format=tar", commit, "--", *pathspec],
                             cwd=local, check=True, capture_output=True).stdout
        with tarfile.open(fileobj=io.BytesIO(tar)) as tf:
            tf.extractall(dest, filter="data")
        with open(marker, "w") as fh:
            fh.write(commit + "\n")
    return dest, commit, git("rev-parse", commit + "^{tree}", cwd=local)


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
    # Workspaces of families no longer in the spec would still be mounted.
    for stale in set(os.listdir(ws_root)) - {f["id"] for f in spec["families"]}:
        if os.path.isdir(os.path.join(ws_root, stale)):
            shutil.rmtree(os.path.join(ws_root, stale))
    manifest = {"workspace_root": ws_root, "repositories": {}, "families": []}
    exports = {}
    for name, rspec in spec["repositories"].items():
        path, commit, tree = export_repo(name, rspec, args.repos_root, args.src_root)
        exports[name] = (path, commit)
        manifest["repositories"][name] = {"commit": commit, "tree": tree,
                                          "export_exclude": rspec.get("export_exclude", []),
                                          "license_file_first_line":
                                              license_line(path, rspec.get("license_file"))}
    for fam in spec["families"]:
        repo, head = exports[fam["repository"]]
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
            "split": fam["split"],
            "repository": fam["repository"],
            "commit": head,
            "include": fam["include"],
            "exclude": fam.get("exclude", []),
            "max_file_bytes": fam.get("max_file_bytes"),
            "unmatched_includes": unmatched,
            "license_file_first_line": manifest["repositories"][fam["repository"]][
                "license_file_first_line"],
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


def line_count(path):
    data = open(path, "rb").read()
    return data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)


def diff_overlap(a, b):
    """Lines `diff` leaves unchanged, as a share of the longer file."""
    res = subprocess.run(["diff", a, b], capture_output=True)
    if res.returncode > 1:
        raise RuntimeError(f"diff {a} {b}: {res.stderr.decode(errors='replace')}")
    la, lb = line_count(a), line_count(b)
    only_a = sum(1 for line in res.stdout.split(b"\n") if line.startswith(b"<"))
    common = la - only_a
    return common, la, lb, (common / max(la, lb) if max(la, lb) else 1.0)


def dupcheck(args):
    manifest = json.load(open(args.manifest))
    root = manifest["workspace_root"]
    files = [(fam["split"], fam["id"], e["path"], e["sha256"])
             for fam in manifest["families"] for e in fam["files"]]
    dev = [f for f in files if f[0] == "development"]
    held = [f for f in files if f[0] == "held-out"]
    identical, pairs = [], []
    for d, h in itertools.product(dev, held):
        if d[3] == h[3]:
            identical.append({"sha256": d[3], "development": f"{d[1]}/{d[2]}",
                              "held_out": f"{h[1]}/{h[2]}"})
        if os.path.basename(d[2]) == os.path.basename(h[2]):
            common, la, lb, ratio = diff_overlap(os.path.join(root, d[1], d[2]),
                                                 os.path.join(root, h[1], h[2]))
            pairs.append({"basename": os.path.basename(d[2]), "development": f"{d[1]}/{d[2]}",
                          "held_out": f"{h[1]}/{h[2]}", "development_lines": la,
                          "held_out_lines": lb, "unchanged_lines": common,
                          "ratio": round(ratio, 4)})
    flagged = sorted({p["development"] for p in identical} |
                     {p["development"] for p in pairs if p["ratio"] > NEAR_DUPLICATE_RATIO})
    out = {
        "rule": ("identical SHA-256, or same basename with more than "
                 f"{NEAR_DUPLICATE_RATIO:.0%} of the longer file's lines unchanged by diff"),
        "files_hashed": len(files),
        "development_files": len(dev),
        "held_out_files": len(held),
        "identical": identical,
        "same_basename_pairs": sorted(pairs, key=lambda p: -p["ratio"]),
        "flagged_development_files": flagged,
    }
    with open(args.out, "w") as fh:
        json.dump(out, fh, indent=2)
        fh.write("\n")
    print(f"dupcheck: {len(files)} files hashed ({len(dev)} development, {len(held)} held-out); "
          f"{len(identical)} identical, {len(pairs)} same-basename pairs, "
          f"max ratio {max((p['ratio'] for p in pairs), default=0)}; flagged {flagged}")


def assemble(args):
    spec = json.load(open(args.input))
    lo, hi = spec["entity_range"]
    iterations = []
    for it_dir in args.iteration:
        manifest = json.load(open(os.path.join(it_dir, "manifest.json")))
        counts = json.load(open(os.path.join(it_dir, "counts.json")))
        iterations.append((os.path.basename(it_dir.rstrip("/")), manifest, counts))
    label = PROVENANCE.format(semsource=args.semsource, semstreams=args.semstreams)
    final_manifest = iterations[-1][1]
    dup = json.load(open(os.path.join(args.iteration[-1], "dupcheck.json")))
    out = {
        "schema": "semselect.legacy-family-count/v2",
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
            "runtime_note": ("semstreams_version is the library SemSource links at runtime; the "
                             "fixture source commits are under repositories"),
        },
        "repositories": {
            name: {"url": r["url"], "commit": final_manifest["repositories"][name]["commit"],
                   "tree": final_manifest["repositories"][name]["tree"],
                   "license": r["license"], "license_file": r["license_file"],
                   "license_file_first_line":
                       final_manifest["repositories"][name]["license_file_first_line"],
                   "split": r["split"], "export": "git archive of the commit (committed files only)",
                   "export_exclude": r.get("export_exclude", [])}
            for name, r in spec["repositories"].items()},
        "duplicate_check": {
            "rule": dup["rule"],
            "evidence": os.path.join(args.evidence, os.path.basename(args.iteration[-1].rstrip("/")),
                                     "dupcheck.json"),
            "files_hashed": dup["files_hashed"],
            "identical": len(dup["identical"]),
            "same_basename_pairs": len(dup["same_basename_pairs"]),
            "max_ratio": max((p["ratio"] for p in dup["same_basename_pairs"]), default=None),
            "flagged_development_files": dup["flagged_development_files"],
            "result": ("nothing flagged; no file was moved out of a development family"
                       if not dup["flagged_development_files"] else
                       "flagged files must be moved out of their development family"),
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
            "repo": spec["repositories"][fam["repository"]]["url"],
            "commit": m["commit"],
            "license": spec["repositories"][fam["repository"]]["license"],
            "license_file": spec["repositories"][fam["repository"]]["license_file"],
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
    gen = spec.get("generalization")
    if gen:
        # Copied, not re-measured (owner ruling 4, issue #5).
        root = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
        earlier = json.loads(git("show", f"{gen['manifest_commit']}:{gen['manifest']}", cwd=root))
        for fid in gen["ids"]:
            entry = dict(next(f for f in earlier["families"] if f["id"] == fid))
            entry["split"] = "generalization"
            entry["copied_from"] = f"{gen['manifest_commit']}:{gen['manifest']}"
            entry["evidence"] = gen["evidence"]
            entry["generalization_note"] = gen["note"]
            out["families"].append(entry)
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
    pp.add_argument("--repos-root", default=os.path.join(HERE, "..", "..", "..", "..", ".."),
                    help="directory holding the sibling repository checkouts")
    pp.add_argument("--config-out", required=True)
    pp.add_argument("--manifest-out", required=True)
    pd = sub.add_parser("dupcheck")
    pd.add_argument("--manifest", required=True)
    pd.add_argument("--out", required=True)
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
    {"prepare": prepare, "dupcheck": dupcheck, "assemble": assemble}[args.cmd](args)


if __name__ == "__main__":
    main()
