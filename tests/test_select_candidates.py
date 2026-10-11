"""Offline tests for the step-2 candidate selector (no inference, no labels)."""

import importlib.util
import json
import os
import random
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE = os.path.join(HERE, "..", "eval", "community-refinement", "fixtures", "tier1-capture",
                      "select_candidates.py")
spec = importlib.util.spec_from_file_location("select_candidates", MODULE)
sc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sc)


def eid(name, typ="function"):
    return f"semselect.semsource.golang.fam.{typ}.{name}"


def pair(a, b, sim, dominated=False, sim_ba=None, typ_a="function", typ_b="function"):
    return {"a": a, "b": b, "similarity_a_to_b": sim, "similarity_b_to_a": sim if sim_ba is None else sim_ba,
            "rank_a_to_b": 1, "rank_b_to_a": 1, "system_a": "fam", "type_a": typ_a, "system_b": "fam",
            "type_b": typ_b, "same_system": True, "same_type": typ_a == typ_b, "explicit_dominated": dominated}


class SelectionOrderTests(unittest.TestCase):
    def test_cross_partition_first_then_distance_then_hash(self):
        comm = {eid("a"): "c1", eid("b"): "c1", eid("c"): "c2", eid("d"): "c2", eid("e"): "c2"}
        pairs = [
            pair(eid("a"), eid("b"), 0.80),   # same community, distance 0
            pair(eid("a"), eid("c"), 0.95),   # cross, distance 0.15
            pair(eid("b"), eid("d"), 0.76),   # cross, distance 0.04
            pair(eid("c"), eid("d"), 0.81),   # same, distance 0.01
            pair(eid("a"), eid("d"), 0.84),   # cross, distance 0.04 (tie with b-d)
        ]
        selected, report = sc.select(pairs, comm, ceiling=32)
        ids = [(c["a"], c["b"]) for c in selected]
        tied = sorted([(eid("b"), eid("d")), (eid("a"), eid("d"))],
                      key=lambda ab: sc.pair_hash(*ab))
        self.assertEqual(ids[:2], tied, "equal-distance cross pairs order by the stable hash")
        self.assertEqual(ids[2], (eid("a"), eid("c")))
        self.assertEqual(ids[3], (eid("a"), eid("b")), "same-community pairs follow every cross pair")
        self.assertEqual(ids[4], (eid("c"), eid("d")))
        self.assertEqual([c["rank"] for c in selected], [1, 2, 3, 4, 5])
        self.assertEqual(report["cross_partition"], {"available": 3, "selected": 3})

    def test_order_is_independent_of_input_order(self):
        comm = {eid(str(i)): f"c{i % 3}" for i in range(40)}
        rng = random.Random(7)
        pairs = [pair(eid(str(i)), eid(str(j)), round(0.75 + rng.random() * 0.25, 4))
                 for i in range(40) for j in range(i + 1, 40) if rng.random() < 0.2]
        first, _ = sc.select(pairs, comm)
        shuffled = list(pairs)
        rng.shuffle(shuffled)
        second, _ = sc.select(shuffled, comm)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 32)

    def test_pair_hash_is_symmetric_and_distinct(self):
        self.assertEqual(sc.pair_hash("x", "y"), sc.pair_hash("y", "x"))
        self.assertNotEqual(sc.pair_hash("x", "y"), sc.pair_hash("x", "z"))


class ExclusionAndCoverageTests(unittest.TestCase):
    def test_exclusions_ceiling_and_coverage(self):
        comm = {eid("a"): "c1", eid("b"): "c1", eid("c"): "c2"}
        pairs = [
            pair(eid("a"), eid("b"), 0.9, dominated=True),
            pair(eid("a"), eid("ghost"), 0.9),              # endpoint not in the frozen partition
            pair(eid("a"), eid("c"), 0.85),
            pair(eid("b"), eid("c"), 0.78),
        ]
        selected, report = sc.select(pairs, comm, ceiling=1)
        self.assertEqual(report["excluded"], {"explicit_dominated": 1, "endpoint_not_in_partition": 1})
        self.assertEqual(report["mutual_pairs"], 4)
        self.assertEqual(report["candidates"], 2)
        self.assertEqual(report["selected"], 1)
        self.assertAlmostEqual(report["coverage"], 0.5)
        self.assertEqual((selected[0]["a"], selected[0]["b"]), (eid("b"), eid("c")),
                         "0.78 is nearer the 0.8 anchor than 0.85")

    def test_fewer_candidates_than_ceiling_selects_all(self):
        comm = {eid("a"): "c1", eid("b"): "c2"}
        selected, report = sc.select([pair(eid("a"), eid("b"), 0.8)], comm)
        self.assertEqual(len(selected), 1)
        self.assertEqual(report["coverage"], 1.0)

    def test_no_candidates(self):
        selected, report = sc.select([pair(eid("a"), eid("b"), 0.8, dominated=True)], {eid("a"): "c", eid("b"): "c"})
        self.assertEqual(selected, [])
        self.assertIsNone(report["coverage"])
        self.assertIsNone(report["selected_similarity"]["median"])

    def test_similarity_is_the_weaker_direction(self):
        comm = {eid("a"): "c1", eid("b"): "c1"}
        selected, report = sc.select([pair(eid("a"), eid("b"), 0.95, sim_ba=0.77)], comm)
        self.assertEqual(selected[0]["similarity"], 0.77)
        self.assertAlmostEqual(selected[0]["distance"], 0.03)
        self.assertEqual(report["directed_similarities_equal"], 0)


class MainTests(unittest.TestCase):
    def write_evidence(self, root, family, profile):
        os.makedirs(os.path.join(root, "structural"))
        os.makedirs(os.path.join(root, "mutualknn"))
        with open(os.path.join(root, "summary.json"), "w") as fh:
            json.dump({"family": family, "split": "development", "identity_profile": profile,
                       "partition_hash": "abc", "mutualknn": {"k": 8, "threshold": 0.75}}, fh)
        with open(os.path.join(root, "structural", "entities.jsonl"), "w") as fh:
            for name, c in (("a", "c1"), ("b", "c1"), ("c", "c2")):
                fh.write(json.dumps({"entity_id": eid(name), "community_by_level": {"0": c, "1": "x"}}) + "\n")
        with open(os.path.join(root, "mutualknn", "mutual_pairs.jsonl"), "w") as fh:
            for p in (pair(eid("a"), eid("b"), 0.81), pair(eid("a"), eid("c"), 0.9), pair(eid("b"), eid("c"), 0.76)):
                fh.write(json.dumps(p) + "\n")

    def test_writes_frozen_selection_with_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            ev = os.path.join(tmp, "20261010T000000Z-legacy-tier1-capture-fam-explicit-only")
            self.write_evidence(ev, "fam", "explicit-only")
            out = os.path.join(tmp, "fam.json")
            sc.main(["--evidence", ev, "--family", "fam", "--out", out, "--ceiling", "2"])
            with open(out) as fh:
                sel = json.load(fh)
            self.assertEqual(sel["family"], "fam")
            self.assertEqual(sel["evidence"], os.path.basename(ev))
            self.assertEqual(sel["inputs"]["partition_hash"], "abc")
            self.assertEqual(len(sel["inputs"]["mutual_pairs_jsonl_sha256"]), 64)
            self.assertEqual([c["rank"] for c in sel["selected"]], [1, 2])
            self.assertTrue(all(c["crosses_partition"] for c in sel["selected"]))
            self.assertEqual(sel["report"]["coverage"], 2 / 3)
            self.assertIn("not a SemEngine result", sel["provenance"])

    def test_refuses_wrong_family_or_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            ev = os.path.join(tmp, "ev")
            self.write_evidence(ev, "fam", "semantic-baseline")
            out = os.path.join(tmp, "fam.json")
            with self.assertRaises(SystemExit):
                sc.main(["--evidence", ev, "--family", "fam", "--out", out])
            with self.assertRaises(SystemExit):
                sc.main(["--evidence", ev, "--family", "other", "--out", out,
                         "--identity-profile", "semantic-baseline"])
            self.assertFalse(os.path.exists(out))


if __name__ == "__main__":
    unittest.main()
