#!/usr/bin/env python3
"""Verify the immutable evidence package, optionally against local originals."""
import argparse
import hashlib
import json
from pathlib import Path


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--local", action="store_true")
    args = parser.parse_args()
    base = Path(__file__).resolve().parent
    root = base.parents[2]
    expected = (base / "MANIFEST.sha256").read_text().split()[0]
    assert sha((base / "MANIFEST.json").read_bytes()) == expected, "Manifest hash mismatch"
    manifest = json.loads((base / "MANIFEST.json").read_text())
    copied = projected = 0
    for entry in manifest["files"]:
        path = (base / entry["path"]).resolve()
        assert path.is_relative_to(base), "Invalid artifact path"
        data = path.read_bytes()
        assert len(data) == entry["bytes"] and sha(data) == entry["sha256"], entry["path"]
        if args.local and "original_local_path" in entry:
            original = (root / entry["original_local_path"]).read_bytes()
            assert sha(original) == entry["original_sha256"], entry["original_local_path"]
            if entry["kind"] == "byte_copy":
                assert data == original, entry["path"]
        copied += entry["kind"] == "byte_copy"
        projected += entry["kind"] == "projection"
    expected_paths = {e["path"] for e in manifest["files"]} | {"MANIFEST.json", "MANIFEST.sha256"}
    actual_paths = {str(p.relative_to(base)) for p in base.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
    assert actual_paths == expected_paths, "Unexpected or missing evidence files"
    inputs = [json.loads(x) for x in (base / "attempt2/inputs.jsonl").read_text().splitlines()]
    statuses = [json.loads(x) for x in (base / "attempt2/status.jsonl").read_text().splitlines()]
    plan = json.loads((base / "attempt2/query-plan.json").read_text())["queries"]
    assert len(inputs) == len(statuses) == len(plan) == 13
    assert [(r["id"], r["query"]) for r in inputs] == [(r["id"], r["query"]) for r in plan]
    for row, status in zip(inputs, statuses):
        assert row["id"] == status["id"] and status["attempts"] == 1
        if status["status"] == "captured":
            raw = (base / "attempt2/raw" / f"{row['id']}.response.json").read_bytes()
            body = json.loads(raw)
            assert sha(raw) == status["response_sha256"]
            assert row["summaries"] == body["community_summaries"]
            assert row["total_entities"] == body["count"]
    assert sum(s["status"] == "captured" for s in statuses) == 12
    assert statuses[5]["id"] == "S06" and statuses[5]["status"] == "invalid_response"
    print(f"Verified {len(manifest['files'])} artifacts: {copied} byte copies, {projected} explicit projections; all 13 planned records and 12 raw summary/count mappings.")


if __name__ == "__main__":
    main()
