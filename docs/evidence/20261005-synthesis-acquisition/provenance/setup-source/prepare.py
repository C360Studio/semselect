#!/usr/bin/env python3
"""Prepare an isolated, pinned SemSource workspace without starting services."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[3]
SCRATCH = ROOT / ".synthesis"
SOURCE_REPO = Path("/Users/coby/Code/c360/semsource")
SOURCE_COMMIT = "4093d3ce421371f4a99d7168e372552899bf6795"
IMAGES = {
    "go": "golang:1.26.6-bookworm@sha256:116d58cbd88c1297624acc6e967a060012422bacf9930927e23fb719189c6f36",
    "nats": "nats:2.14.4-alpine@sha256:f2123f533c2b0cada0a5c5ec434fb2b8cfe1cf220215ef9d7517e1372917ad66",
    "semembed": "ghcr.io/c360studio/semembed:latest@sha256:7972174f8e3462fd38bf3ad95d94870f406bb3d1350e0ea3280ce002cb2e09b2",
    "seminstruct": "ghcr.io/c360studio/seminstruct:qwen3-0.6b@sha256:297507bee8396438fe284f9ed285165c47a565b3f65be177030c473a4b90bdb9",
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def main():
    if (SCRATCH / "provenance" / "prepared.json").exists():
        raise SystemExit("Already prepared; preserve this workspace and its provenance.")
    for directory in ("source", "bin", "logs", "corpus", "config", "provenance", "nats"):
        (SCRATCH / directory).mkdir(parents=True, exist_ok=True)
    if any((SCRATCH / "source").iterdir()) or any((SCRATCH / "corpus").iterdir()):
        raise SystemExit("Source/corpus destination is nonempty; refusing to overwrite.")
    state = subprocess.check_output(["git", "-C", str(SOURCE_REPO), "status", "--porcelain"]).decode()
    head = subprocess.check_output(["git", "-C", str(SOURCE_REPO), "rev-parse", "HEAD"]).decode().strip()
    archive = subprocess.check_output(["git", "-C", str(SOURCE_REPO), "archive", "--format=tar", SOURCE_COMMIT])
    (SCRATCH / "provenance" / "semsource.tar").write_bytes(archive)
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        source.extractall(SCRATCH / "source", filter="data")

    heldout = ROOT / "eval/answerability/heldout"
    manifest = json.loads((heldout / "sources.json").read_text())
    corpus = []
    for entry in manifest["source_files"]:
        if not entry["path"].endswith(".md"):
            continue
        original = heldout / entry["artifact"]
        data = original.read_bytes()
        if digest(data) != entry["sha256"]:
            raise SystemExit(f"Source hash mismatch: {original}")
        target = Path(entry["artifact"]).relative_to("source")
        output = SCRATCH / "corpus" / target
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(data)
        corpus.append({**entry, "container_path": str(Path("/corpus") / target)})
    for entry in manifest["licenses"]:
        data = (heldout / entry["artifact"]).read_bytes()
        if digest(data) != entry["sha256"]:
            raise SystemExit("License hash mismatch")
        (SCRATCH / "provenance" / f"{entry['repository']}-LICENSE").write_bytes(data)

    config = json.loads((SCRATCH / "source/configs/tiers/tier2-compose-dev.json").read_text())
    config.update(namespace="semselectsynth", sources=[{"type": "docs", "paths": ["/corpus"], "watch": False}],
                  source_roots=["/corpus"], workspace_dir="/tmp/workspace", http_port=8080,
                  websocket_bind="127.0.0.1:7890", metrics={"port": 9091})
    config["graph"].update(gateway_bind="127.0.0.1:8082", enable_playground=False)
    save(SCRATCH / "config/semsource.json", config)

    def mount(source, target, readonly=True):
        return {"type": "bind", "source": str(SCRATCH / source), "target": target, "read_only": readonly}

    def service(image, cpu, memory):
        return {"image": IMAGES[image], "pull_policy": "never", "restart": "no", "cpus": cpu,
                "mem_limit": memory, "networks": ["isolated"], "labels": {"io.semselect.experiment": "synthesis-4093d3c"}}

    nats = service("nats", 1, "1g")
    nats.update(command=["-js", "-sd", "/data", "-m", "8222"],
                ports=["127.0.0.1:44222:4222", "127.0.0.1:48222:8222"],
                volumes=[mount("nats", "/data", False)])
    embed = service("semembed", 2, "2g")
    embed.update(environment={"SEMEMBED_PORT": "8081", "SEMEMBED_MODEL": "Snowflake/snowflake-arctic-embed-s"})
    instruct = service("seminstruct", 2, "2g")
    instruct.update(environment={"MODEL_ALIAS": "seminstruct", "MODEL_CONTEXT": "16384", "MODEL_REASONING": "off",
                                 "MODEL_THREADS": "2", "MODEL_PARALLEL": "4"},
                    ports=["127.0.0.1:48083:8083"])
    source = service("go", 4, "4g")
    source.update(entrypoint=["/out/semsource"],
                  command=["run", "--config", "/config/semsource.json", "--nats-url", "nats://nats:4222"],
                  ports=["127.0.0.1:48080:8080"], read_only=True, tmpfs=["/tmp:size=512m"],
                  volumes=[mount("bin", "/out"), mount("config", "/config"), mount("corpus", "/corpus")])
    compose = {"name": "semselect-synthesis-4093d3c", "services": {
        "nats": nats, "semembed": embed, "seminstruct": instruct, "semsource": source},
        "networks": {"isolated": {"internal": True}}}
    save(SCRATCH / "config/compose.json", compose)
    prepared = {"semsource_commit": SOURCE_COMMIT, "checkout_head_at_archive": head, "checkout_porcelain": state,
                "archive_sha256": digest(archive), "images": IMAGES, "corpus": corpus,
                "source_files": {name: digest((SCRATCH / "source" / name).read_bytes()) for name in ("go.mod", "go.sum")},
                "config_sha256": digest((SCRATCH / "config/semsource.json").read_bytes()),
                "scope": "Six public pinned documentation files; no code, gold labels, questions or private checkout corpus."}
    save(SCRATCH / "provenance/prepared.json", prepared)
    print(json.dumps({"prepared": str(SCRATCH), "files": len(corpus), "bytes": sum(e["bytes"] for e in corpus),
                      "archive_sha256": prepared["archive_sha256"]}))


if __name__ == "__main__":
    main()
