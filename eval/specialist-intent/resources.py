"""Read-only Docker cgroup observations; never starts/stops somebody else's model."""
from __future__ import annotations

import json
import re
import subprocess
from urllib.parse import urlparse
from datetime import datetime, timezone


def command(args):
    result = subprocess.run(args, capture_output=True, timeout=10, check=True)
    return result.stdout.decode()


def capture(environment):
    container = environment.get('container_id', '')
    if not re.fullmatch('[a-f0-9]{64}', container):
        raise ValueError('full Docker container ID required for cgroup accounting')
    inspect = json.loads(command(['docker', 'inspect', container]))[0]
    host = inspect['HostConfig']
    state = inspect['State']
    if inspect['Id'] != container or host['NanoCpus'] != 4000000000 or host['Memory'] != 4294967296:
        raise ValueError('actual container does not have frozen CPU/memory limits')
    if host.get('DeviceRequests') or host.get('Devices'):
        raise ValueError('CPU-only container must not expose device accelerators')
    image = json.loads(command(['docker', 'image', 'inspect', inspect['Image']]))[0]
    if image.get('Architecture') != 'arm64' or image.get('Os') != 'linux' or inspect['Image'] != environment['container_digest']:
        raise ValueError('actual runtime image/architecture differs from environment')
    endpoint = urlparse(environment.get('endpoint', ''))
    if endpoint.scheme != 'http' or endpoint.hostname != '127.0.0.1' or not endpoint.port:
        raise ValueError('container endpoint must use explicit host loopback port')
    bindings = [b for rows in inspect['NetworkSettings']['Ports'].values() if rows for b in rows]
    if not any(b['HostIp'] == '127.0.0.1' and b['HostPort'] == str(endpoint.port) for b in bindings):
        raise ValueError('endpoint is not the inspected container published loopback port')
    if any(b['HostIp'] != '127.0.0.1' for b in bindings):
        raise ValueError('evaluation container must publish only host loopback ports')
    observation = {'container_id': container, 'observed_at': datetime.now(timezone.utc).isoformat(),
                   'oom': state['OOMKilled'], 'running': state['Running'], 'inspect': inspect,
                   'verified': False, 'peak_includes_startup': True}
    if not state['Running']:
        return observation
    # memory.peak is cumulative since container creation, so it includes loading,
    # warmup, the example index and requests rather than a late sampled RSS peak.
    memory = command(['docker', 'exec', container, 'cat', '/sys/fs/cgroup/memory.peak', '/sys/fs/cgroup/memory.current']).splitlines()
    cpu = command(['docker', 'exec', container, 'cat', '/sys/fs/cgroup/cpu.stat'])
    cpu_values = dict(line.split() for line in cpu.splitlines())
    observation.update(peak_memory_bytes=int(memory[0]), current_memory_bytes=int(memory[1]),
                       cpu_time_seconds=int(cpu_values['usage_usec'])/1000000, verified=not state['OOMKilled'])
    return observation
