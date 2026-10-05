#!/usr/bin/env python3
"""Provision only the approved pinned embedding files, checking every byte."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[3]
SCRATCH = ROOT / ".synthesis"
REVISION = "e596f507467533e48a2e17c007f0e1dacc837b33"
FILES = ("onnx/model.onnx", "config.json", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json")


def main():
    metadata = json.loads((SCRATCH / "provenance/snowflake-hf-metadata.json").read_text())
    if metadata["sha"] != REVISION:
        raise SystemExit("Metadata revision differs from approved revision")
    lock = SCRATCH / "provenance/embedding-files.json"
    if lock.exists():
        raise SystemExit("Embedding lock already exists; refusing to overwrite")
    entries = {x["rfilename"]:x for x in metadata["siblings"]}
    cache = SCRATCH / "model-cache/models--Snowflake--snowflake-arctic-embed-s"
    records = []
    for name in (*FILES, "README.md"):
        entry = entries[name]
        destination = (cache / "snapshots" / REVISION / name) if name in FILES else (SCRATCH / "provenance/snowflake-model-card.md")
        destination.parent.mkdir(parents=True,exist_ok=True)
        if destination.exists():
            raise SystemExit(f"Existing destination: {destination}")
        partial = destination.with_suffix(destination.suffix+".part")
        url = f"https://huggingface.co/Snowflake/snowflake-arctic-embed-s/resolve/{REVISION}/{name}"
        command = ["curl","--fail","--location","--silent","--show-error","--proto","=https","--proto-redir","=https",
                   "--connect-timeout","10","--max-time","120","--retry","2","--retry-max-time","300",
                   "--max-filesize",str(entry["size"]),url,"-o",str(partial)]
        result = subprocess.run(command,capture_output=True,text=True,timeout=360)
        (SCRATCH / "logs" / ("embed-download-"+name.replace("/","_")+".log")).write_text(result.stderr)
        if result.returncode:
            raise SystemExit(f"Download failed for {name}: {result.stderr}")
        data = partial.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        blob = hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest()
        if len(data) != entry["size"]:
            raise SystemExit(f"Size mismatch: {name}")
        if "lfs" in entry:
            if sha != entry["lfs"]["sha256"]:
                raise SystemExit(f"LFS SHA256 mismatch: {name}")
        elif blob != entry["blobId"]:
            raise SystemExit(f"Git blob mismatch: {name}")
        partial.rename(destination)
        destination.chmod(0o444)
        records.append({"path":name,"url":url,"bytes":len(data),"sha256":sha,"git_blob_sha1":blob,
                        "expected_metadata":entry,"artifact":str(destination.relative_to(ROOT))})
        print(f"Verified {name}: {len(data)} bytes, SHA256 {sha}",flush=True)
    refs = cache / "refs"
    refs.mkdir(parents=True,exist_ok=True)
    (refs/"main").write_text(REVISION)
    (refs/"main").chmod(0o444)
    lock.write_text(json.dumps({"model":"Snowflake/snowflake-arctic-embed-s","revision":REVISION,
                               "license":metadata.get("cardData",{}).get("license"),"files":records,
                               "cache_main_ref":REVISION,"cache_mount":"/model-cache","offline_endpoint":"http://127.0.0.1:9"},indent=2)+"\n")


if __name__=="__main__":
    main()
