#!/usr/bin/env python3
"""Wrap a bounded command with read-only Docker/cgroup v2 resource observations."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import time
from typing import Any


CONTAINER_TEMPLATE = (
    '{"id":{{json .Id}},"name":{{json .Name}},"image_id":{{json .Image}},'
    '"started_at":{{json .State.StartedAt}},"running":{{json .State.Running}},'
    '"limits":{"memory_bytes":{{json .HostConfig.Memory}},'
    '"memory_swap_bytes":{{json .HostConfig.MemorySwap}},'
    '"nano_cpus":{{json .HostConfig.NanoCpus}},"cpu_period":{{json .HostConfig.CpuPeriod}},'
    '"cpu_quota":{{json .HostConfig.CpuQuota}},"cpuset_cpus":{{json .HostConfig.CpusetCpus}}}}'
)
DOCKER_TEMPLATE = (
    '{"server_version":{{json .ServerVersion}},"os_type":{{json .OSType}},'
    '"operating_system":{{json .OperatingSystem}},"architecture":{{json .Architecture}},'
    '"kernel_version":{{json .KernelVersion}},"cpu_count":{{json .NCPU}},'
    '"memory_total_bytes":{{json .MemTotal}},"cgroup_version":{{json .CgroupVersion}}}'
)
IMAGE_TEMPLATE = '{"id":{{json .Id}},"os":{{json .Os}},"architecture":{{json .Architecture}}}'


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def probe(command: list[str], timeout: float) -> tuple[str | None, str | None]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)
    if result.returncode:
        return None, f"exit {result.returncode}: {result.stderr.strip()[:2000]}"
    return result.stdout.strip(), None


def probe_json(command: list[str], timeout: float) -> dict[str, Any]:
    output, error = probe(command, timeout)
    if error:
        return {"available": False, "error": error}
    try:
        data = json.loads(output)
        if not isinstance(data, dict):
            raise ValueError("expected a JSON object")
        return {"available": True, "data": data}
    except (TypeError, ValueError) as exc:
        return {"available": False, "error": str(exc)}


def nonnegative_integer(text: str) -> int:
    value = int(text)
    if value < 0:
        raise ValueError("cgroup counter must be nonnegative")
    return value


def parse_cpu_stat(text: str) -> dict[str, int]:
    values = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) != 2 or fields[0] in values:
            raise ValueError("malformed cgroup cpu.stat")
        values[fields[0]] = nonnegative_integer(fields[1])
    if "usage_usec" not in values:
        raise ValueError("cgroup v2 usage_usec is unavailable")
    return values


def snapshot(container: str, timeout: float) -> dict[str, Any]:
    result: dict[str, Any] = {"started_at": timestamp(), "errors": {}}
    for key, filename, parser in (
        ("memory_current_bytes", "memory.current", nonnegative_integer),
        ("memory_peak_lifetime_bytes", "memory.peak", nonnegative_integer),
        ("cpu_stat", "cpu.stat", parse_cpu_stat),
    ):
        output, error = probe(["docker", "exec", container, "cat", "/sys/fs/cgroup/" + filename], timeout)
        result[key] = None
        if error is None:
            try:
                result[key] = parser(output)
            except (TypeError, ValueError) as exc:
                error = str(exc)
        if error:
            result["errors"][key] = error
    result["finished_at"] = timestamp()
    result["available"] = not result["errors"]
    return result


def cpu_delta(before: dict[str, Any], after: dict[str, Any], same_container_run: bool) -> dict[str, Any]:
    if not same_container_run:
        return {"available": False, "reason": "Container identity/start time could not be confirmed unchanged."}
    first, last = before["cpu_stat"], after["cpu_stat"]
    if first is None or last is None:
        return {"available": False, "reason": "cgroup v2 CPU counters unavailable."}
    delta = {key: last[key] - first[key] for key in first.keys() & last.keys()}
    if any(value < 0 for value in delta.values()):
        return {"available": False, "reason": "CPU counters decreased; possible cgroup reset."}
    return {"available": True, "counters": delta, "usage_seconds": delta["usage_usec"] / 1_000_000}


def stop_process_group(process: subprocess.Popen) -> None:
    """Terminate the command and its children after an explicit bounded timeout."""
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    # A leader may exit on SIGTERM while a descendant ignores it. Kill the
    # remaining group even when wait() already reaped the leader.
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        elif process.poll() is None:
            process.kill()
    except ProcessLookupError:
        pass
    process.wait(timeout=5)


def positive_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be finite and greater than zero")
    return number


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", required=True, help="Docker container running inference")
    parser.add_argument("--output", required=True, type=Path, help="Resource report JSON path")
    parser.add_argument("--timeout", type=positive_float, default=1800, help="Wrapped command wall-time limit in seconds (default 1800)")
    parser.add_argument("--probe-timeout", type=positive_float, default=10, help="Each Docker read timeout in seconds")
    parser.add_argument("--notes", default="", help="User-supplied run context")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="Command after --; executed directly without a shell")
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("supply a command after --")
    report: dict[str, Any] = {
        "kind": "docker-cgroup-v2-resource-observation", "started_at": timestamp(),
        "container_requested": args.container, "client_platform": platform.platform(),
        "command_executable": command[0], "command_argument_count": len(command) - 1,
        "command_timeout_seconds": args.timeout, "probe_timeout_seconds": args.probe_timeout,
        "notes": args.notes,
        "measurement_notes": [
            "Read-only cgroup v2 counters; counters are never reset.",
            "memory.current and memory.peak include cgroup-accounted memory such as file cache; neither is process RSS.",
            "memory.peak is the existing cgroup peak, normally since container creation, including startup/cache and earlier activity. It is NOT an interval peak; another actor may have reset it.",
            "CPU counter delta includes all server, idle/background, warmup, concurrent-client, and measurement activity between snapshots; it is not model-only CPU time.",
            "Docker probes occur outside the wrapped command. Evaluator per-request timings do not include wrapper probes.",
            "Snapshots are sequential and are not atomic. Missing cgroup data does not prevent the wrapped command from running.",
            "Docker engine hardware can belong to a Linux VM rather than the physical client host. No GPU measurement is provided.",
            "Only selected Docker fields are collected; container environment, full config, and wrapped command arguments are not persisted.",
        ],
    }
    inspect_command = ["docker", "inspect", "--type", "container", "--format", CONTAINER_TEMPLATE, args.container]
    report["container_before"] = probe_json(inspect_command, args.probe_timeout)
    report["docker_engine"] = probe_json(["docker", "info", "--format", DOCKER_TEMPLATE], args.probe_timeout)
    image_id = report["container_before"].get("data", {}).get("image_id")
    report["image"] = probe_json(["docker", "image", "inspect", "--format", IMAGE_TEMPLATE, image_id], args.probe_timeout) if image_id else {"available": False, "error": "container image ID unavailable"}
    report["before"] = snapshot(args.container, args.probe_timeout)
    print(f"Resource observations started for {args.container}; running {command[0]}.", file=sys.stderr, flush=True)
    start = time.monotonic()
    run: dict[str, Any] = {"started_at": timestamp(), "timed_out": False}
    process = None
    try:
        process = subprocess.Popen(command, start_new_session=os.name == "posix")
        run["exit_code"] = process.wait(timeout=args.timeout)
    except subprocess.TimeoutExpired:
        run.update(timed_out=True, exit_code=124)
        stop_process_group(process)
    except KeyboardInterrupt:
        run.update(interrupted=True, exit_code=130)
        if process is not None:
            stop_process_group(process)
    except OSError as exc:
        run.update(exit_code=127, error=str(exc))
    run["duration_seconds"] = time.monotonic() - start
    run["finished_at"] = timestamp()
    report["command_run"] = run
    report["after"] = snapshot(args.container, args.probe_timeout)
    report["container_after"] = probe_json(inspect_command, args.probe_timeout)
    first = report["container_before"].get("data", {})
    last = report["container_after"].get("data", {})
    same_run = bool(first.get("id")) and all(first.get(key) == last.get(key) for key in ("id", "started_at"))
    report["cpu_delta"] = cpu_delta(report["before"], report["after"], same_run)
    report["finished_at"] = timestamp()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(f"Resources: {args.output}; command exit {run['exit_code']}.", file=sys.stderr, flush=True)
    code = run["exit_code"]
    return code if code >= 0 else 128 - code


if __name__ == "__main__":
    raise SystemExit(main())
