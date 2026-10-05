#!/usr/bin/env python3
"""Bounded acquisition checks/collection for the separately launched owned stack."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[3]
SCRATCH = ROOT / ".synthesis"
OUT = Path(os.environ.get("SEMSELECT_ACQUISITION_DIR",str(SCRATCH / "acquisition"))).resolve()
COMPOSE_FILE = Path(os.environ.get("SEMSELECT_ACQUISITION_COMPOSE",str(SCRATCH / "config/compose.json"))).resolve()
if not OUT.is_relative_to(SCRATCH) or not COMPOSE_FILE.is_relative_to(SCRATCH):
    raise RuntimeError("Acquisition paths must remain inside experiment scratch")
COMPOSE = ["docker", "compose", "-f", str(COMPOSE_FILE)]
PROJECT = json.loads(COMPOSE_FILE.read_text())["name"]
if not PROJECT.startswith("semselect-synthesis-4093d3c"):
    raise RuntimeError("Expected an experiment-owned Compose project")
SERVICES = ("nats", "semembed", "seminstruct", "semsource")


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def save(name, data):
    (OUT / name).write_text(json.dumps(data, indent=2) + "\n")


def run(cmd, timeout=20):
    result = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"{cmd}: {result.stderr}")
    return result.stdout


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inspect():
    ids = run(COMPOSE + ["ps", "--all", "--quiet"]).split()
    return json.loads(run(["docker", "inspect", *ids])) if ids else []


def get(url):
    with urllib.request.urlopen(url, timeout=3) as response:
        return json.loads(response.read(2_000_000))


def collector(mode, filename=None):
    prepared = json.loads((SCRATCH / "provenance/prepared.json").read_text())
    command = ["docker", "run", "--rm", "--pull=never", "--network", PROJECT + "_isolated",
               "--cpus=1", "--memory=512m", "--read-only", "--name", PROJECT + "-collector",
               "--mount", f"type=bind,src={SCRATCH / 'bin'},dst=/out,readonly",
               "--mount", f"type=bind,src={OUT},dst=/capture",
               prepared["images"]["go"], "/out/capture", "-mode", mode]
    if mode == "capture":
        command += ["-plan", "/capture/query-plan.json", "-out", "/capture"]
    else:
        command += ["-out", "/capture/" + filename]
    result = run(command, 810 if mode == "capture" else 25)
    if result:
        print(result, end="", flush=True)


def preflight():
    existing = inspect()
    if any(x["State"]["Running"] for x in existing):
        raise RuntimeError("Owned project has running containers; refusing a fresh acquisition.")
    for port in (44222, 48222, 48080, 48083):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
    plan = ROOT / "eval/synthesis/query-plan.json"
    if plan.read_bytes() != (OUT / "query-plan.json").read_bytes():
        raise RuntimeError("Plan copy differs from frozen query plan")
    prepared = json.loads((SCRATCH / "provenance/prepared.json").read_text())
    for entry in prepared["corpus"]:
        path = SCRATCH / entry["container_path"].removeprefix("/")
        if digest(path) != entry["sha256"]:
            raise RuntimeError(f"Corpus hash mismatch: {path}")
    images = json.loads(run(["docker", "image", "inspect", *prepared["images"].values()]))
    embedding_lock = None
    if (SCRATCH / "provenance/embedding-files.json").exists():
        embedding_lock = json.loads((SCRATCH / "provenance/embedding-files.json").read_text())
        for entry in embedding_lock["files"]:
            if digest(ROOT / entry["artifact"]) != entry["sha256"]:
                raise RuntimeError(f"Embedding hash mismatch: {entry['path']}")
        ref = SCRATCH / "model-cache/models--Snowflake--snowflake-arctic-embed-s/refs/main"
        if ref.read_text() != embedding_lock["revision"]:
            raise RuntimeError("Embedding main ref is not pinned to approved revision")
    save("preflight.json", {"checked_utc": now(), "running_containers_before":run(["docker", "ps", "--no-trunc", "--format", "{{json .}}"]),
                           "images":images, "prepared":prepared,
                           "build":json.loads((SCRATCH / "provenance/build.json").read_text()),
                           "query_plan_sha256":digest(plan), "capture_source_sha256":digest(ROOT / "eval/synthesis/live/capture.go"),
                           "capture_binary_sha256":digest(SCRATCH / "bin/capture"),
                           "embedding_lock":embedding_lock,
                           "compose_rendered":json.loads(run(COMPOSE + ["config", "--format", "json"]))})
    print("Preflight passed: cache/hash/port/project checks; no services launched.")


def wait_deps():
    started = time.monotonic()
    with (OUT / "dependency-readiness.jsonl").open("x") as log:
        while True:
            sample = {"utc":now(), "elapsed_seconds":time.monotonic()-started}
            try:
                sample["nats"] = get("http://127.0.0.1:48222/healthz")
                sample["seminstruct"] = get("http://127.0.0.1:48083/health")
                state = inspect()
                sample["containers"] = {x["Config"]["Labels"]["com.docker.compose.service"]:x["State"] for x in state}
                sample["ready"] = (sample["nats"].get("status") == "ok" and sample["seminstruct"].get("status") == "ok"
                                   and sample["containers"].get("semembed",{}).get("Health",{}).get("Status") == "healthy")
            except Exception as error:
                sample["error"] = str(error)
            log.write(json.dumps(sample)+"\n"); log.flush()
            if sample.get("ready"):
                save("dependencies-ready.json",sample)
                print("Dependencies ready",flush=True); return
            if time.monotonic()-started >= 120:
                raise RuntimeError("Dependency readiness timed out at 120 seconds")
            time.sleep(2)


def wait_source():
    started = time.monotonic()
    with (OUT / "source-readiness.jsonl").open("x") as log:
        while True:
            sample = {"utc":now(), "elapsed_seconds":time.monotonic()-started}
            try:
                status = get("http://127.0.0.1:48080/source-manifest/status")
                sample["source_status"] = status
                graph_ready = (status.get("phase") == "ready" and status.get("index",{}).get("ready") is True
                               and status.get("embedding",{}).get("ready") is True and status.get("total_entities",0)>0)
                if graph_ready:
                    collector("snapshot", "readiness-kv.json")
                    kv = json.loads((OUT / "readiness-kv.json").read_text())
                    records = kv["buckets"].get("COMMUNITY_SUMMARIES",{})
                    sample["summary_status_counts"] = {}
                    for record in records.values():
                        if not isinstance(record,dict):
                            continue
                        label = record.get("value",{}).get("status", "unknown")
                        sample["summary_status_counts"][label] = sample["summary_status_counts"].get(label,0)+1
                    sample["ready"] = sum(sample["summary_status_counts"].values()) > 0
            except Exception as error:
                sample["error"] = str(error)
            log.write(json.dumps(sample)+"\n"); log.flush()
            if sample.get("ready"):
                save("source-ready.json",sample)
                print(json.dumps({"ready":True,"entities":status["total_entities"],"summary_status_counts":sample["summary_status_counts"]}),flush=True)
                return
            if time.monotonic()-started >= 300:
                raise RuntimeError("Source/index/community readiness timed out at 300 seconds")
            print(json.dumps({"elapsed":round(sample["elapsed_seconds"],1),"phase":sample.get("source_status",{}).get("phase"),"error":sample.get("error")}),flush=True)
            time.sleep(3)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("command",choices=["preflight","wait-deps","wait-source","snapshot","capture","state","logs"])
    parser.add_argument("--label",default="before")
    args=parser.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    if args.command=="preflight": preflight()
    elif args.command=="wait-deps": wait_deps()
    elif args.command=="wait-source": wait_source()
    elif args.command=="snapshot": collector("snapshot",args.label+"-kv.json")
    elif args.command=="capture": collector("capture")
    elif args.command=="state":
        save(args.label+"-runtime.json",{"utc":now(),"containers":inspect()})
    elif args.command=="logs":
        for service in SERVICES:
            (OUT / f"{args.label}-{service}.log").write_text(run(COMPOSE+["logs","--no-color","--timestamps",service],30))


if __name__=="__main__":
    main()
