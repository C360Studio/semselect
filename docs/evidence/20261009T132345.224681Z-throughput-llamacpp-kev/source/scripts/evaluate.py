#!/usr/bin/env python3
"""Real HTTP routing smoke evaluation. Uses only Python's standard library."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import platform
import statistics
import sys
import time
from typing import Any
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
THRESHOLDS = (0.0, 0.5, 0.7, 0.9)
MAX_RESPONSE_BYTES = 1024 * 1024


def finite_number(value: Any) -> bool:
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def validate_native(response: Any, labels: list[str]) -> dict[str, Any]:
    """Validate the native distribution without treating it as correctness confidence."""
    try:
        answer = response["answers"]["route"]
        choice = answer["choice"]
        probabilities = answer["probabilities"]
        confidence = answer["confidence"]
    except (KeyError, TypeError) as exc:
        raise ValueError("missing native answers.route fields") from exc
    if answer.get("type") != "choice" or not isinstance(choice, str) or choice not in labels:
        raise ValueError("native answer is not an allowed choice")
    if not isinstance(probabilities, dict) or set(probabilities) != set(labels):
        raise ValueError("probabilities must contain exactly every supplied candidate")
    if any(not finite_number(value) or not 0 <= value <= 1 for value in probabilities.values()):
        raise ValueError("probabilities must be finite numbers in [0, 1]")
    if not math.isclose(sum(probabilities.values()), 1.0, abs_tol=1e-5):
        raise ValueError("candidate probabilities do not sum to one")
    if not finite_number(confidence) or not 0 <= confidence <= 1:
        raise ValueError("native confidence must be finite and in [0, 1]")
    pmax = max(probabilities.values())
    if not math.isclose(probabilities[choice], pmax, abs_tol=1e-7):
        raise ValueError("selected candidate does not have a maximal probability")
    return {"choice": choice, "probabilities": probabilities, "confidence": confidence, "pmax": pmax}


def validate_baseline(response: Any, labels: list[str]) -> dict[str, Any]:
    try:
        choice = response["choices"][0]
        if not isinstance(choice, dict):
            raise ValueError("baseline choice must be an object")
        if choice.get("finish_reason") != "stop":
            raise ValueError("baseline response did not finish normally")
        answer = json.loads(choice["message"]["content"])
    except (KeyError, TypeError, IndexError, json.JSONDecodeError) as exc:
        raise ValueError("missing or malformed baseline structured answer") from exc
    if not isinstance(answer, dict) or set(answer) != {"route"}:
        raise ValueError("baseline answer must contain only route; generated confidence is not accepted")
    if not isinstance(answer["route"], str) or answer["route"] not in labels:
        raise ValueError("baseline route is not an allowed choice")
    return {"choice": answer["route"], "probabilities": None, "confidence": None, "pmax": None}


def build_request(backend: str, model: str, dataset: dict[str, Any], case: dict[str, Any], labels: list[str]) -> dict[str, Any]:
    criteria = {label: dataset["categories"][label] for label in labels}
    if backend == "semselect":
        return {
            "model": model,
            "state": case["text"],
            "questions": {"route": {"type": "choice", "instructions": dataset["instructions"], "criteria": criteria}},
        }
    schema = {
        "type": "object", "properties": {"route": {"type": "string", "enum": labels}},
        "required": ["route"], "additionalProperties": False,
    }
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": dataset["instructions"] + "\nCategories: " + json.dumps(criteria) + '\nReturn only JSON with one "route" field.'},
            {"role": "user", "content": case["text"]},
        ],
        "temperature": 0,
        "max_tokens": 128,
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {"type": "json_schema", "json_schema": {"name": "route", "strict": True, "schema": schema}},
    }


def post_json(url: str, payload: dict[str, Any], timeout: float) -> Any:
    request = urllib.request.Request(url, json.dumps(payload).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError("HTTP response exceeded one MiB")
    result = json.loads(body)
    # Reject nonfinite values even in unused fields, so invalid responses cannot
    # prevent the final strict-JSON report from being saved.
    json.dumps(result, allow_nan=False)
    return result


def percentile(values: list[float], fraction: float) -> float | None:
    """Nearest-rank percentile, including failed request durations."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def summarize(rows: list[dict[str, Any]], unknown: str, backend: str) -> dict[str, Any]:
    total = len(rows)
    valid = [row for row in rows if row["status"] == "ok"]
    correct = sum(row["choice"] == row["expected"] for row in valid)
    accepted = [row for row in valid if row["choice"] != unknown]
    by_case: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        by_case.setdefault(row["id"], {})[row["order"]] = row
    pairs = [pair for pair in by_case.values() if len(pair) == 2 and all(row["status"] == "ok" for row in pair.values())]
    flips = sum(pair["normal"]["choice"] != pair["reverse"]["choice"] for pair in pairs)
    thresholds = []
    if backend == "semselect":
        for threshold in THRESHOLDS:
            selected = [row for row in accepted if row["pmax"] >= threshold]
            wrong = sum(row["choice"] != row["expected"] for row in selected)
            thresholds.append({"pmax_at_least": threshold, "accepted": len(selected), "coverage": len(selected) / total if total else 0, "errors": wrong, "error_rate_when_accepted": wrong / len(selected) if selected else None})
    latencies = [row["latency_ms"] for row in rows]
    accepted_errors = sum(row["choice"] != row["expected"] for row in accepted)
    return {
        "total": total, "valid": len(valid), "correct": correct,
        "accuracy": correct / total if total else 0,
        "status_counts": dict(Counter(row["status"] for row in rows)),
        "unknown_selections": sum(row["choice"] == unknown for row in valid),
        "unknown_selection_rate": sum(row["choice"] == unknown for row in valid) / total if total else 0,
        "unknown_policy": {"accepted": len(accepted), "coverage": len(accepted) / total if total else 0, "errors": accepted_errors, "error_rate_when_accepted": accepted_errors / len(accepted) if accepted else None},
        "native_confidence_available": backend == "semselect",
        "pmax_thresholds": thresholds,
        "order_comparison": {"total_pairs": len(by_case), "valid_pairs": len(pairs), "flips": flips, "flip_rate_valid_pairs": flips / len(pairs) if pairs else None},
        "latency_ms": {"p50": statistics.median(latencies) if latencies else None, "p95": percentile(latencies, 0.95), "max": max(latencies) if latencies else None},
    }


def load_dataset(path: Path) -> dict[str, Any]:
    dataset = json.loads(path.read_text())
    categories = dataset["categories"]
    if not isinstance(categories, dict) or len(categories) < 2 or dataset["unknown_label"] not in categories:
        raise ValueError("dataset must supply candidate descriptions and an explicit unknown label")
    if any(not isinstance(key, str) or not isinstance(value, str) or not value for key, value in categories.items()):
        raise ValueError("categories must map strings to nonempty descriptions")
    if not isinstance(dataset["instructions"], str) or not dataset["instructions"]:
        raise ValueError("dataset instructions must be a nonempty string")
    cases = dataset["cases"]
    if not cases or len({case["id"] for case in cases}) != len(cases):
        raise ValueError("dataset cases need unique IDs")
    for case in cases:
        if not isinstance(case["text"], str) or case["expected"] not in categories:
            raise ValueError("case must have text and an allowed expected label")
    return dataset


def positive_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError("must be finite and greater than zero")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("semselect", "seminstruct"), required=True)
    parser.add_argument("--url", required=True, help="Base URL, e.g. http://localhost:8084 (no /v1 suffix)")
    parser.add_argument("--model", help="Served model ID; defaults to semselect-kev-4b or qwen3-0.6b")
    parser.add_argument("--dataset", type=Path, default=ROOT / "eval/routing-smoke.json")
    parser.add_argument("--output", type=Path, required=True, help="JSON result file, conventionally results/<name>.json")
    parser.add_argument("--timeout", type=positive_float, default=120, help="Per-request HTTP timeout in seconds")
    parser.add_argument("--warmup", type=int, default=1, help="Warmup request count, excluded from metrics")
    parser.add_argument("--hardware", default="", help="Explicit inference hardware, container limits, runtime configuration")
    parser.add_argument("--notes", default="", help="Run limitations, model revision, resource observations")
    args = parser.parse_args()
    if not 0 <= args.warmup <= 10:
        parser.error("--warmup must be between 0 and 10")
    dataset = load_dataset(args.dataset)
    labels = list(dataset["categories"])
    model = args.model or ("semselect-kev-4b" if args.backend == "semselect" else "qwen3-0.6b")
    endpoint = args.url.rstrip("/") + ("/v1/systemone" if args.backend == "semselect" else "/v1/chat/completions")
    validator = validate_native if args.backend == "semselect" else validate_baseline
    started_at = datetime.now(timezone.utc).isoformat()
    warmups = []
    for index in range(args.warmup):
        start = time.monotonic()
        try:
            response = post_json(endpoint, build_request(args.backend, model, dataset, dataset["cases"][0], labels), args.timeout)
            validator(response, labels)
            warmups.append({"status": "ok", "latency_ms": (time.monotonic() - start) * 1000})
        except (OSError, ValueError, urllib.error.HTTPError) as exc:
            warmups.append({"status": "error", "error": str(exc), "latency_ms": (time.monotonic() - start) * 1000})
        print(f"warmup {index + 1}: {warmups[-1]['status']}", file=sys.stderr, flush=True)
    rows = []
    for case in dataset["cases"]:
        for order, candidates in (("normal", labels), ("reverse", list(reversed(labels)))):
            row = {"id": case["id"], "text": case["text"], "tags": case.get("tags", []), "expected": case["expected"], "order": order, "candidates": candidates}
            start = time.monotonic()
            try:
                response = post_json(endpoint, build_request(args.backend, model, dataset, case, candidates), args.timeout)
                row["response"] = response
                row.update(validator(response, candidates))
                row["status"] = "ok"
            except ValueError as exc:
                row.update(status="invalid", error=str(exc))
            except (OSError, urllib.error.HTTPError) as exc:
                row.update(status="error", error=str(exc))
            row["latency_ms"] = (time.monotonic() - start) * 1000
            rows.append(row)
            print(f"{case['id']}/{order}: {row.get('choice', row['status'])} {row['latency_ms']:.1f}ms", file=sys.stderr, flush=True)
    result = {
        "kind": "routing-smoke-not-benchmark", "started_at": started_at, "finished_at": datetime.now(timezone.utc).isoformat(),
        "backend": args.backend, "endpoint": endpoint, "model": model,
        "metadata": {"hardware_supplied": args.hardware or None, "client_platform": platform.platform(), "client_machine": platform.machine(), "client_cpu_count": os.cpu_count(), "python": platform.python_version(), "notes": args.notes, "timeout_seconds": args.timeout, "resource_measurement": "Not sampled by this harness. Supply runtime resource measurements in --notes; client metadata is not inference hardware."},
        "dataset_path": str(args.dataset.resolve()), "dataset": dataset, "warmup": warmups,
        "metric_notes": "Accuracy and coverage denominators include errors and invalid answers. pmax is a model candidate distribution score, not calibrated correctness confidence. Unknown is always an abstention for coverage. Baseline generated confidence is rejected. Latency includes all measured calls, excludes warmup. Order flip rate uses valid pairs; missing pairs are separately counted. Reversed pairs are correlated observations.",
        "summary": summarize(rows, dataset["unknown_label"], args.backend), "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps(result["summary"], indent=2))
    print(f"Results: {args.output}", file=sys.stderr)
    return 1 if any(row["status"] != "ok" for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
