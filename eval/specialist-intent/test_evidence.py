import copy
import json
import unittest
from unittest.mock import patch

import evidence as e
import resources


class EvidenceTests(unittest.TestCase):
    def environment(self):
        lock = e.read(e.ROOT/'locks/gliclass.json')
        return {'model_revision': lock['revision'], 'artifacts': {k: v['sha256'] for k, v in lock['files'].items()},
                'dependencies': e.read(e.ROOT/'locks/dependencies.json')['direct_versions'], 'runtime_sha256': 'a'*64,
                'architecture': 'linux/arm64', 'precision': 'FP32', 'readiness_seconds': 10,
                'idle_memory_bytes': 1000, 'peak_memory_bytes': 2000, 'peak_includes_startup': True,
                'cpu_time_seconds': 1, 'oom': False, 'startup_log_sha256': 'b'*64, 'background_contention': [],
                'cleanup': None, 'verified': True, 'cpu_only': True, 'cpus': 4, 'threads': 4,
                'memory_limit_bytes': 4294967296, 'query_batch_size': 1, 'container_digest': 'sha256:'+'c'*64}

    def test_specialist_metadata_must_match_pinned_artifacts(self):
        env = self.environment()
        e.verify_environment(env, 'gliclass')
        for field, bad in [('model_revision', 'f'*40), ('artifacts', {'made-up': 'd'*64}), ('dependencies', {'torch': '0.0'})]:
            with self.assertRaises(ValueError):
                e.verify_environment(dict(env, **{field: bad}), 'gliclass')

    def test_actual_container_port_and_bounds_checked(self):
        env = self.environment() | {'container_id': 'd'*64, 'endpoint': 'http://127.0.0.1:8099/classify'}
        inspected = {'Id': env['container_id'], 'Image': env['container_digest'], 'HostConfig': {'NanoCpus': 4000000000, 'Memory': 4294967296}, 'State': {'OOMKilled': False, 'Running': True}, 'NetworkSettings': {'Ports': {'8099/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '8099'}]}}}
        image = {'Architecture': 'arm64', 'Os': 'linux'}
        def command(args):
            if args[1] == 'inspect':
                return json.dumps([inspected])
            if args[1] == 'image':
                return json.dumps([image])
            return 'usage_usec 1000000\n' if args[-1].endswith('cpu.stat') else '2000\n1000\n'
        with patch.object(resources, 'command', side_effect=command):
            self.assertTrue(resources.capture(env)['verified'])
            with self.assertRaisesRegex(ValueError, 'endpoint'):
                resources.capture(dict(env, endpoint='http://127.0.0.1:8888/classify'))
            inspected['HostConfig']['NanoCpus'] = 8000000000
            with self.assertRaisesRegex(ValueError, 'limits'):
                resources.capture(env)


if __name__ == '__main__':
    unittest.main()
