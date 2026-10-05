#!/usr/bin/env python3
"""Offline Linux/ARM64 CGO build in a cached container; no service launch."""
import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[3]
SCRATCH = ROOT / ".synthesis"


def main():
    prepared = json.loads((SCRATCH / "provenance/prepared.json").read_text())
    output = SCRATCH / "provenance/build.json"
    if output.exists() or (SCRATCH / "bin/semsource").exists():
        raise SystemExit("Build evidence exists; refusing to overwrite.")
    image = prepared["images"]["go"]
    inspected = json.loads(subprocess.check_output(["docker", "image", "inspect", image]))[0]
    if (inspected["Os"], inspected["Architecture"]) != ("linux", "arm64"):
        raise SystemExit("Expected the inspected Linux/ARM64 cache.")
    command = ["docker", "run", "--rm", "--pull=never", "--network=none", "--name", "semselect-synthesis-build-4093d3c",
               "--cpus=4", "--memory=8g", "--read-only", "--tmpfs", "/tmp:rw,exec,size=2g", "--tmpfs", "/gocache:rw,size=3g",
               "--mount", f"type=bind,src={SCRATCH / 'source'},dst=/src,readonly",
               "--mount", "type=bind,src=/Users/coby/go/pkg/mod,dst=/go/pkg/mod,readonly",
               "--mount", f"type=bind,src={SCRATCH / 'bin'},dst=/out",
               "--env", "GOPROXY=off", "--env", "GOSUMDB=off", "--env", "GOTOOLCHAIN=local",
               "--env", "CGO_ENABLED=1", "--env", "GOCACHE=/gocache", "--env", "GOTMPDIR=/tmp",
               "--workdir", "/src", image, "go", "build", "-mod=readonly", "-trimpath", "-buildvcs=false", "-p", "4",
               "-o", "/out/semsource", "./cmd/semsource"]
    record = {"started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "command": command,
              "image": {key: inspected[key] for key in ("Id", "RepoDigests", "Architecture", "Os")},
              "semsource_commit": prepared["semsource_commit"], "archive_sha256": prepared["archive_sha256"]}
    started = time.monotonic()
    with (SCRATCH / "logs/build.log").open("w") as log:
        try:
            completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=600)
            record["exit_code"] = completed.returncode
        except subprocess.TimeoutExpired:
            record["timeout_seconds"] = 600
            subprocess.run(["docker", "stop", "--time", "5", "semselect-synthesis-build-4093d3c"],
                           stdout=log, stderr=subprocess.STDOUT, timeout=15)
    record["elapsed_seconds"] = time.monotonic() - started
    binary = SCRATCH / "bin/semsource"
    if record.get("exit_code") == 0 and binary.exists():
        record["binary_sha256"] = hashlib.sha256(binary.read_bytes()).hexdigest()
        record["binary_bytes"] = binary.stat().st_size
    output.write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record))
    raise SystemExit(record.get("exit_code", 1))


if __name__ == "__main__":
    main()
