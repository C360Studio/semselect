#!/usr/bin/env python3
"""Explicit artifact acquisition / wheel-lock construction, never inference.

Model command downloads only immutable locked URLs and verifies every byte.
Wheel command converts a resolved Linux ARM64 wheelhouse into a requirements
file with hashes; pip --require-hashes validates closure during image creation.
"""
import argparse
import email
import hashlib
import json
from pathlib import Path
import re
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def acquire(arm, output):
    lock = json.loads((ROOT / (arm + '.json')).read_text())
    output.mkdir(parents=True, exist_ok=True)
    for name, entry in lock['files'].items():
        dest = output / name
        if dest.exists():
            if dest.stat().st_size != entry['size_bytes'] or sha(dest) != entry['sha256']:
                raise ValueError('existing artifact mismatch: ' + str(dest))
            continue
        url = f"https://huggingface.co/{lock['repository']}/resolve/{lock['revision']}/{name}"
        tmp = dest.with_name(dest.name + '.partial')
        try:
            with urllib.request.urlopen(url, timeout=60) as response, tmp.open('xb') as stream:
                count = 0
                while chunk := response.read(1024 * 1024):
                    count += len(chunk)
                    if count > entry['size_bytes']:
                        raise ValueError('artifact exceeds locked size')
                    stream.write(chunk)
            if count != entry['size_bytes'] or sha(tmp) != entry['sha256']:
                raise ValueError('artifact checksum mismatch: ' + name)
            tmp.rename(dest)
        finally:
            tmp.unlink(missing_ok=True)


def wheel_lock(wheelhouse, output):
    rows, seen = [], set()
    for wheel in sorted(wheelhouse.glob('*.whl')):
        with zipfile.ZipFile(wheel) as archive:
            # A wheel can contain vendored dependencies with their own nested
            # metadata. Only the top-level dist-info describes this wheel.
            names = [n for n in archive.namelist() if n.count('/') == 1 and n.endswith('.dist-info/METADATA')]
            if len(names) != 1:
                raise ValueError('ambiguous wheel metadata')
            metadata = email.message_from_bytes(archive.read(names[0]))
        name = re.sub(r'[-_.]+', '-', metadata['Name']).lower()
        if name in seen:
            raise ValueError('multiple wheels for one package: ' + name)
        seen.add(name)
        if any(word in name for word in ('nvidia', 'cuda', 'triton')):
            raise ValueError('GPU dependency in CPU wheelhouse: ' + name)
        rows.append(f"{name}=={metadata['Version']} --hash=sha256:{sha(wheel)}")
    expected = json.loads((ROOT / 'dependencies.json').read_text())['direct_versions']
    if not set(expected).issubset(seen):
        raise ValueError('wheelhouse omits direct dependencies')
    for package, version in expected.items():
        if not any(row.startswith(f'{package}=={version} ') for row in rows):
            raise ValueError('direct dependency version mismatch: ' + package)
    with output.open('x') as stream:
        stream.write('\n'.join(rows) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    model = sub.add_parser('model')
    model.add_argument('--arm', required=True, choices=['gliclass', 'deberta'])
    model.add_argument('--output', required=True, type=Path)
    wheels = sub.add_parser('wheels')
    wheels.add_argument('--wheelhouse', required=True, type=Path)
    wheels.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.command == 'model':
        acquire(args.arm, args.output)
    else:
        wheel_lock(args.wheelhouse, args.output)


if __name__ == '__main__':
    main()
