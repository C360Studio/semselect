"""Offline evaluator unit tests; these do not validate model inference."""

import copy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("evaluate", ROOT / "scripts/evaluate.py")
evaluate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluate)


class NativeResponseTests(unittest.TestCase):
    def setUp(self):
        self.labels = ["billing", "technical", "account", "unknown"]
        self.response = {"answers": {"route": {"type": "choice", "choice": "billing", "probabilities": {"billing": 0.7, "technical": 0.1, "account": 0.1, "unknown": 0.1}, "confidence": 0.7}}}

    def test_preserves_native_distribution_and_confidence(self):
        result = evaluate.validate_native(self.response, self.labels)
        self.assertEqual(result["pmax"], 0.7)
        self.assertEqual(result["probabilities"], self.response["answers"]["route"]["probabilities"])
        self.assertEqual(result["confidence"], 0.7)

    def test_rejects_missing_extra_nonfinite_boolean_and_unnormalized_probabilities(self):
        malformed = [
            {"billing": 1.0},
            {"billing": 0.6, "technical": 0.1, "account": 0.1, "unknown": 0.1, "extra": 0.1},
            {"billing": float("nan"), "technical": 0.1, "account": 0.1, "unknown": 0.1},
            {"billing": float("inf"), "technical": 0.0, "account": 0.0, "unknown": 0.0},
            {"billing": True, "technical": 0.0, "account": 0.0, "unknown": 0.0},
            {"billing": 0.8, "technical": 0.1, "account": 0.1, "unknown": 0.1},
            {"billing": 1.1, "technical": -0.1, "account": 0.0, "unknown": 0.0},
            {"billing": 10 ** 400, "technical": 0, "account": 0, "unknown": 0},
            [0.7, 0.1, 0.1, 0.1],
        ]
        for distribution in malformed:
            with self.subTest(distribution=distribution):
                response = copy.deepcopy(self.response)
                response["answers"]["route"]["probabilities"] = distribution
                with self.assertRaises(ValueError):
                    evaluate.validate_native(response, self.labels)

    def test_rejects_nonmaximal_or_unlisted_choices_and_invalid_confidence(self):
        for key, value in (("choice", "technical"), ("choice", "other"), ("choice", []), ("type", "score"), ("confidence", None), ("confidence", 2), ("confidence", True)):
            with self.subTest(key=key, value=value):
                response = copy.deepcopy(self.response)
                response["answers"]["route"][key] = value
                with self.assertRaises(ValueError):
                    evaluate.validate_native(response, self.labels)

    def test_missing_or_malformed_answer(self):
        for response in ({}, [], None, {"answers": {"route": []}}):
            with self.subTest(response=response), self.assertRaises(ValueError):
                evaluate.validate_native(response, self.labels)


class BaselineResponseTests(unittest.TestCase):
    def response(self, answer, finish_reason="stop"):
        return {"choices": [{"finish_reason": finish_reason, "message": {"content": json.dumps(answer)}}]}

    def test_structured_label_has_no_probability(self):
        result = evaluate.validate_baseline(self.response({"route": "billing"}), ["billing", "unknown"])
        self.assertEqual(result, {"choice": "billing", "probabilities": None, "confidence": None, "pmax": None})

    def test_rejects_generated_confidence_extra_labels_and_truncation(self):
        for response in (
            self.response({"route": "billing", "confidence": 0.99}),
            self.response({"route": "other"}),
            self.response({"route": "billing"}, "length"),
            self.response(["billing"]),
            {"choices": []},
            {"choices": [None]},
            {"choices": ["not an object"]},
            {"choices": [{"finish_reason": "stop", "message": {"content": "```json\n{}\n```"}}]},
        ):
            with self.subTest(response=response), self.assertRaises(ValueError):
                evaluate.validate_baseline(response, ["billing", "unknown"])


class MetricTests(unittest.TestCase):
    def rows(self):
        return [
            {"id": "a", "order": "normal", "status": "ok", "choice": "billing", "expected": "billing", "pmax": 0.9, "latency_ms": 10},
            {"id": "a", "order": "reverse", "status": "ok", "choice": "technical", "expected": "billing", "pmax": 0.6, "latency_ms": 20},
            {"id": "b", "order": "normal", "status": "ok", "choice": "unknown", "expected": "unknown", "pmax": 0.99, "latency_ms": 30},
            {"id": "b", "order": "reverse", "status": "ok", "choice": "unknown", "expected": "unknown", "pmax": 0.95, "latency_ms": 40},
            {"id": "c", "order": "normal", "status": "error", "expected": "account", "latency_ms": 50},
            {"id": "c", "order": "reverse", "status": "invalid", "expected": "account", "latency_ms": 60},
        ]

    def test_failures_count_against_accuracy_and_coverage(self):
        summary = evaluate.summarize(self.rows(), "unknown", "semselect")
        self.assertEqual(summary["accuracy"], 0.5)
        self.assertEqual(summary["unknown_selection_rate"], 2 / 6)
        self.assertEqual(summary["status_counts"], {"ok": 4, "error": 1, "invalid": 1})
        self.assertEqual(summary["unknown_policy"]["coverage"], 2 / 6)
        self.assertEqual(summary["unknown_policy"]["error_rate_when_accepted"], 0.5)
        self.assertEqual(summary["order_comparison"], {"total_pairs": 3, "valid_pairs": 2, "flips": 1, "flip_rate_valid_pairs": 0.5})
        self.assertEqual(summary["latency_ms"], {"p50": 35, "p95": 60, "max": 60})

    def test_thresholds_reject_unknown_even_with_high_probability(self):
        thresholds = evaluate.summarize(self.rows(), "unknown", "semselect")["pmax_thresholds"]
        self.assertEqual([item["accepted"] for item in thresholds], [2, 2, 1, 1])
        self.assertEqual(thresholds[2]["coverage"], 1 / 6)
        self.assertEqual(thresholds[2]["error_rate_when_accepted"], 0)

    def test_baseline_has_only_unknown_abstention_policy(self):
        summary = evaluate.summarize(self.rows(), "unknown", "seminstruct")
        self.assertFalse(summary["native_confidence_available"])
        self.assertEqual(summary["pmax_thresholds"], [])
        self.assertEqual(summary["unknown_policy"]["coverage"], 2 / 6)

    def test_empty_accepted_set_has_undefined_conditional_error(self):
        summary = evaluate.summarize(self.rows()[2:], "unknown", "semselect")
        self.assertIsNone(summary["unknown_policy"]["error_rate_when_accepted"])
        self.assertTrue(all(item["error_rate_when_accepted"] is None for item in summary["pmax_thresholds"]))


class DatasetAndRequestTests(unittest.TestCase):
    def test_http_json_rejects_nonfinite_values_before_result_persistence(self):
        for body in (b'{"score": NaN}', b'{"score": 1e999}'):
            response = MagicMock()
            response.__enter__.return_value.read.return_value = body
            with self.subTest(body=body), patch.object(evaluate.urllib.request, "urlopen", return_value=response), self.assertRaises(ValueError):
                evaluate.post_json("http://localhost/test", {}, 1)

    def test_dataset_covers_requested_risks_and_reversal_preserves_descriptions(self):
        dataset = evaluate.load_dataset(ROOT / "eval/routing-smoke.json")
        tags = {tag for case in dataset["cases"] for tag in case["tags"]}
        self.assertTrue({"clear", "paraphrase", "ambiguous", "multi-intent", "negation", "out-of-taxonomy", "injection"} <= tags)
        labels = list(reversed(dataset["categories"]))
        request = evaluate.build_request("semselect", "model", dataset, dataset["cases"][0], labels)
        self.assertEqual(list(request["questions"]["route"]["criteria"]), labels)
        self.assertEqual(request["questions"]["route"]["criteria"], dataset["categories"])

    def test_baseline_uses_same_descriptions_and_enum(self):
        dataset = evaluate.load_dataset(ROOT / "eval/routing-smoke.json")
        labels = list(dataset["categories"])
        request = evaluate.build_request("seminstruct", "model", dataset, dataset["cases"][0], labels)
        schema = request["response_format"]["json_schema"]["schema"]
        self.assertEqual(schema["properties"]["route"]["enum"], labels)
        self.assertFalse(schema["additionalProperties"])
        for description in dataset["categories"].values():
            self.assertIn(description, request["messages"][0]["content"])


if __name__ == "__main__":
    unittest.main()
