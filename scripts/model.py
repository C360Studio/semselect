#!/usr/bin/env python3
"""Acquire or verify the exact public artifact in models.lock.json (stdlib only)."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def verify(path, lock):
    if not path.is_file() or path.stat().st_size != lock['size_bytes']:
        return False
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest() == lock['sha256']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    lock = json.loads((ROOT / 'models.lock.json').read_text())
    target = ROOT / 'models' / lock['filename']
    if verify(target, lock):
        print(f'Verified {target.name}: {lock["sha256"]}')
        return
    if args.verify:
        raise SystemExit('Model missing or checksum mismatch; run task model:fetch')
    target.parent.mkdir(exist_ok=True)
    # An exclusive lock avoids concurrent downloads moving each other's partial file.
    lock_path = target.with_suffix('.lock')
    fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    partial = target.with_suffix('.partial')
    try:
        url = f'https://huggingface.co/{lock["repository"]}/resolve/{lock["revision"]}/{lock["filename"]}'
        subprocess.run(['curl', '--fail', '--location', '--retry', '3', '--connect-timeout', '20', '--max-time', '1800', '--output', str(partial), url], check=True)
        if not verify(partial, lock):
            raise SystemExit('Downloaded model failed size/SHA-256 verification')
        partial.chmod(0o644)
        partial.replace(target)
        print(f'Verified {target.name}: {lock["sha256"]}')
    finally:
        os.close(fd)
        partial.unlink(missing_ok=True)
        lock_path.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
