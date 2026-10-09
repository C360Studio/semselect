#!/usr/bin/env python3
"""Acquire or verify an exact public artifact; defaults to models.lock.json."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def load_lock(path):
    lock = json.loads(path.read_text())
    if not isinstance(lock, dict):
        raise ValueError('Model lock must be a JSON object')
    patterns = {
        'repository': r'[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*',
        'revision': r'[0-9a-f]{40}',
        'filename': r'[A-Za-z0-9][A-Za-z0-9_.-]*\.gguf',
        'sha256': r'[0-9a-f]{64}',
    }
    for key, pattern in patterns.items():
        value = lock.get(key)
        if not isinstance(value, str) or not re.fullmatch(pattern, value):
            raise ValueError(f'Invalid model lock {key}: use an exact pin and a plain GGUF filename')
    if type(lock.get('size_bytes')) is not int or lock['size_bytes'] <= 0:
        raise ValueError('Model lock size_bytes must be a positive integer')
    if 'runtime_revision' in lock and not re.fullmatch(r'[0-9a-f]{40}', str(lock['runtime_revision'])):
        raise ValueError('Model lock runtime_revision must be a full commit SHA')
    return lock


def verify(path, lock):
    if not path.is_file() or path.stat().st_size != lock['size_bytes']:
        return False
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest() == lock['sha256']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify', action='store_true')
    parser.add_argument('--lock', type=Path, default=ROOT / 'models.lock.json', help='Artifact lock file (default: models.lock.json)')
    args = parser.parse_args()
    try:
        lock = load_lock(args.lock)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    target = ROOT / 'models' / lock['filename']
    if verify(target, lock):
        print(f'Verified {target.name}: {lock["sha256"]}')
        return
    if args.verify:
        raise SystemExit(f'Model missing or checksum mismatch; run python3 scripts/model.py --lock {args.lock}')
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
