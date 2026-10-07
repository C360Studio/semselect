"""Mechanical fixture checks; never emit held-out text or per-case labels."""
import hashlib
import json
from collections import Counter
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parent / "fixtures"
OPERATIONS = {"similarity", "path", "zone", "count", "avg", "sum", "min", "max", "no_override"}
SUBGROUPS = {"ordinary_search", "negated_quoted", "ambiguous_composed", "unsupported", "instruction_override"}


def load(name):
    return json.loads((ROOT / name).read_text())


def literal_matches(query, values):
    # Catalog identifiers may contain hyphens or underscores. A larger
    # identifier is not a literal mention of a smaller catalog identifier.
    return [value for value in values if re.search(
        r"(?<![\w-])" + re.escape(value) + r"(?![\w-])", query)]


def validate_case(case):
    if set(case) != {"id", "family", "stratum", "provenance", "input", "gold"}:
        raise ValueError("case schema")
    if case["provenance"].get("kind") != "authored" or not case["provenance"].get("source"):
        raise ValueError("provenance")
    inp, gold = case["input"], case["gold"]
    if set(inp) != {"query", "catalog"} or not isinstance(inp["query"], str) or not inp["query"].strip():
        raise ValueError("input schema")
    if set(inp["catalog"]) != {"nodes", "zones", "fields"}:
        raise ValueError("catalog schema")
    for values in inp["catalog"].values():
        if not isinstance(values, list) or not values or any(not isinstance(v, str) or not v for v in values):
            raise ValueError("catalog members")
        if len(values) != len(set(values)):
            raise ValueError("duplicate catalog members")
    if set(gold) != {"operation", "readiness", "options", "executable", "reason"}:
        raise ValueError("gold schema")
    op = gold["operation"]
    if op not in OPERATIONS or type(gold["executable"]) is not bool or not gold["reason"]:
        raise ValueError("gold values")
    expected = {}
    readiness = "valid_plan"
    if op == "no_override":
        readiness = "no_override"
        if case["stratum"] not in SUBGROUPS:
            raise ValueError("no-override subgroup")
    elif op == "similarity":
        expected = {"use_embeddings": True}
    elif op == "count":
        expected = {"aggregation_type": "count"}
    else:
        if op in {"path", "zone"}:
            catalog = "nodes" if op == "path" else "zones"
            binding = "path_start_node"
            expected = {"path_intent": True}
            if op == "zone":
                expected["path_predicates"] = ["located_in"]
        else:
            catalog, binding = "fields", "aggregation_field"
            expected = {"aggregation_type": op}
        matches = literal_matches(inp["query"], inp["catalog"][catalog])
        if len(matches) == 1:
            expected[binding] = matches[0]
        else:
            readiness = "needs_binding"
    # JSON equality alone admits numeric 1 in place of boolean true.
    if json.dumps(gold["options"], sort_keys=True) != json.dumps(expected, sort_keys=True):
        raise ValueError("gold binding/options disagree with literal catalog")
    if gold["readiness"] != readiness or gold["executable"] != (readiness == "valid_plan"):
        raise ValueError("gold readiness")


class FixtureTests(unittest.TestCase):
    def test_schema_counts_and_literal_bindings(self):
        for split, total, per_op, no_override, deficient in (
            ("development", 60, 5, 20, 8), ("heldout", 120, 10, 40, 20)
        ):
            data = load(split + ".json")
            self.assertEqual(set(data), {"schema_version", "split", "cases"})
            self.assertEqual(data["schema_version"], 1)
            self.assertEqual(data["split"], split)
            cases = data["cases"]
            self.assertEqual(len(cases), total)
            for case in cases:
                # Deliberately avoid subTest(case=...) to keep text and gold out
                # of routine failure logs consumed by classifier tuners.
                validate_case(case)
            counts = Counter(c["gold"]["operation"] for c in cases)
            self.assertEqual(counts, {op: no_override if op == "no_override" else per_op for op in OPERATIONS})
            subgroup_counts = Counter(c["stratum"] for c in cases if c["gold"]["operation"] == "no_override")
            self.assertEqual(subgroup_counts, {s: no_override // 5 for s in SUBGROUPS})
            self.assertEqual(sum(c["gold"]["readiness"] == "needs_binding" for c in cases), deficient)

    def test_unique_ids_text_and_disjoint_families_catalogs(self):
        development, heldout = [load(s + ".json")["cases"] for s in ("development", "heldout")]
        combined = development + heldout
        self.assertEqual(len({c["id"] for c in combined}), len(combined))
        self.assertEqual(len({c["input"]["query"].casefold() for c in combined}), len(combined))
        self.assertFalse({c["family"] for c in development} & {c["family"] for c in heldout})
        for key in ("nodes", "zones", "fields"):
            self.assertFalse(set(development[0]["input"]["catalog"][key]) & set(heldout[0]["input"]["catalog"][key]))

    def test_manifest_matches_splits_and_preselected_subsets(self):
        manifest = load("manifest.json")
        by_split = {split: load(split + ".json")["cases"] for split in ("development", "heldout")}
        for split, cases in by_split.items():
            metadata = manifest["splits"][split]
            self.assertEqual(metadata["case_count"], len(cases))
            self.assertEqual(metadata["families"], {c["id"]: c["family"] for c in cases})
            self.assertEqual(metadata["operation_counts"], dict(Counter(c["gold"]["operation"] for c in cases)))
            self.assertEqual(metadata["readiness_counts"], dict(Counter(c["gold"]["readiness"] for c in cases)))
        for selection, split, count in (("feasibility", "development", 12), ("sensitivity", "heldout", 24)):
            ids = manifest[selection]["case_ids"]
            self.assertEqual(len(ids), count)
            self.assertEqual(len(set(ids)), count)
            self.assertTrue(set(ids) <= {c["id"] for c in by_split[split]})
            selected = [c for c in by_split[split] if c["id"] in ids]
            self.assertEqual({c["gold"]["operation"] for c in selected}, OPERATIONS)
            if selection == "sensitivity":
                self.assertEqual({c["stratum"] for c in selected if c["gold"]["operation"] == "no_override"}, SUBGROUPS)

    def test_freeze_covers_all_artifacts_and_author_review_is_not_independent(self):
        freeze = load("freeze.json")
        self.assertEqual(set(freeze["files"]), {"development.json", "heldout.json", "manifest.json", "review.json", "README.md", "freeze-defects.json"})
        for name, digest in freeze["files"].items():
            self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), digest)
        review = load("review.json")
        self.assertEqual(review["status"], "author_checked_independent_review_pending")
        self.assertFalse(review["independent_approval"])
        self.assertFalse(review["candidate_outputs_seen"])
        defects = load("freeze-defects.json")
        self.assertFalse(defects["candidate_outputs_seen"])
        self.assertEqual(len(defects["changed_case_ids"]), 13)
        self.assertNotEqual(defects["superseded_freeze"]["files"]["heldout.json"], freeze["files"]["heldout.json"])

    def test_binding_validation_rejects_invented_or_numeric_boolean_gold(self):
        case = load("development.json")["cases"][5]
        case["gold"]["options"]["path_start_node"] = "not-in-catalog"
        with self.assertRaises(ValueError):
            validate_case(case)
        case = load("development.json")["cases"][0]
        case["gold"]["options"]["use_embeddings"] = 1
        with self.assertRaises(ValueError):
            validate_case(case)


if __name__ == "__main__":
    unittest.main()
