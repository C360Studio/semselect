#!/usr/bin/env python3
"""Verify published archive bytes and every uncompressed member; no extraction."""
import hashlib
import json
from pathlib import Path
import tarfile


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    root = Path(__file__).resolve().parent
    manifest = json.loads((root/'MANIFEST.json').read_bytes())
    count = 0
    for name, expected in manifest['archives'].items():
        path = root/name
        raw = path.read_bytes()
        if sha(raw) != expected['sha256'] or len(raw) != expected['bytes']:
            raise ValueError(f'Archive bytes differ: {name}')
        with tarfile.open(path, 'r:gz') as archive:
            members = archive.getmembers()
            if len(members) != len(expected['members']) or {m.name for m in members} != set(expected['members']):
                raise ValueError(f'Archive membership differs: {name}')
            for member in members:
                if not member.isfile():
                    raise ValueError(f'Unexpected non-file: {member.name}')
                data = archive.extractfile(member).read()
                pin = expected['members'][member.name]
                if sha(data) != pin['sha256'] or len(data) != pin['bytes']:
                    raise ValueError(f'Member bytes differ: {member.name}')
                count += 1
    for name, expected in manifest['files'].items():
        if sha((root/name).read_bytes()) != expected:
            raise ValueError(f'Published file differs: {name}')
    print(f'Verified {len(manifest["archives"])} archives, {count} original files and {len(manifest["files"])} published files.')


if __name__ == '__main__':
    main()
