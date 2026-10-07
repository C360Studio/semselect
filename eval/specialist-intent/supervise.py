#!/usr/bin/env python3
"""Bound an owned setup/runtime command, retain its launch log, and reap its group.

Use this foreground wrapper for setup and externally provisioned model runtimes.
The application running it remains responsible for registering its actual OS PID.
It never discovers or stops unrelated processes.
"""
from __future__ import annotations

import argparse
import fcntl
from datetime import datetime, timezone
import json
import os
import re
from pathlib import Path
import signal
import subprocess
import time

from evidence import read, save_new

LIMITS = {'setup': 1800, 'runtime': 1920, 'qwen-cpu': 600}


def cleanup_container(cidfile):
    if not cidfile.exists():
        return {'container_created': False, 'container_shutdown_verified': True}
    container = cidfile.read_text().strip()
    if not re.fullmatch('[a-f0-9]{64}', container):
        return {'container_shutdown_verified': False, 'error': 'invalid owned container ID'}
    events = []
    for action in (['stop', '--time', '2'], ['kill']):
        result = subprocess.run(['docker', *action, container], capture_output=True, timeout=10)
        events.append({'action': action, 'exit_code': result.returncode, 'stderr': result.stderr.decode(errors='replace')})
        inspected = subprocess.run(['docker', 'inspect', container], capture_output=True, timeout=10)
        if inspected.returncode == 0:
            if json.loads(inspected.stdout)[0]['State']['Running'] is False:
                return {'container_id': container, 'container_shutdown_verified': True, 'cleanup_events': events}
        elif 'No such' in inspected.stderr.decode(errors='replace'):
            return {'container_id': container, 'container_shutdown_verified': True, 'cleanup_events': events}
    return {'container_id': container, 'container_shutdown_verified': False, 'cleanup_events': events}


def supervise(root, arm, phase, command, cidfile=None):
    if arm not in ('gliclass', 'deberta', 'embeddings', 'qwen') or phase not in LIMITS or not command:
        raise ValueError('unknown arm/phase or empty command')
    root.mkdir(parents=True, exist_ok=True)
    attempts = sorted(p for p in root.glob(f'{arm}-{phase}-*.json') if not p.name.endswith('.launch.json'))
    if Path(command[0]).name == 'docker' and 'run' in command and cidfile is None:
        raise ValueError('owned Docker runs require --cidfile for verified container shutdown')
    if cidfile is not None:
        if cidfile.exists() or '--cidfile' not in command or str(cidfile) not in command:
            raise ValueError('Docker cidfile must be new and supplied explicitly to the owned command')
    if phase == 'setup' and len(attempts) >= 2:
        raise ValueError('setup allows one initial and one corrective dependency attempt')
    spent = sum(read(p)['elapsed_seconds'] for p in attempts) if phase == 'setup' else 0
    remaining = LIMITS[phase]-spent
    if remaining <= 0:
        raise ValueError('cumulative setup budget exhausted')
    ident = f'{arm}-{phase}-{len(attempts)+1}'
    lock = None
    if phase != 'setup':
        lock = (root/'loaded-model.lock').open('a')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lock.close()
            raise ValueError('another owned model is loaded under this supervisor record root')
    logfile = root/(ident+'.log')
    record = {'arm': arm, 'phase': phase, 'command': command, 'cwd': os.getcwd(), 'log': str(logfile.resolve()),
              'started_at': datetime.now(timezone.utc).isoformat(), 'deadline_seconds': remaining}
    started = time.monotonic()
    process = None
    prior = {}
    def interrupted(signum, frame):
        raise KeyboardInterrupt(f'signal {signum}')
    for signum in (signal.SIGTERM, signal.SIGINT):
        prior[signum] = signal.signal(signum, interrupted)
    try:
        with logfile.open('xb') as stream:
            process = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
            record['pid'] = process.pid
            save_new(root/(ident+'.launch.json'), record)
            try:
                record['exit_code'] = process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                record['stop_reason'] = 'deadline exhausted'
    except KeyboardInterrupt:
        record['stop_reason'] = 'operator interruption'
    finally:
        if process is not None:
            # The child owns its process group. Reap remaining children even if
            # the group leader exited early, without touching unrelated PIDs.
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=2)
            record['exit_code'] = process.returncode
            try:
                os.killpg(process.pid, 0)
                record['shutdown_verified'] = False
            except ProcessLookupError:
                record['shutdown_verified'] = True
        for signum, handler in prior.items():
            signal.signal(signum, handler)
        if cidfile is not None:
            try:
                record.update(cleanup_container(cidfile))
                record['shutdown_verified'] = record.get('shutdown_verified', False) and record['container_shutdown_verified']
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                record.update(shutdown_verified=False, cleanup_error=str(error))
        record['elapsed_seconds'] = time.monotonic()-started
        save_new(root/(ident+'.json'), record)
        if lock is not None:
            lock.close()
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--records', required=True, type=Path)
    parser.add_argument('--arm', required=True)
    parser.add_argument('--phase', required=True, choices=list(LIMITS))
    parser.add_argument('--cidfile', type=Path)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    result = supervise(args.records, args.arm, args.phase, command, args.cidfile)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result.get('exit_code') == 0 and result.get('shutdown_verified') else 1)


if __name__ == '__main__':
    main()
