#!/usr/bin/env python3
"""Recompute descriptive tables; human/agent grades remain explicit input evidence."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import statistics
import math


def summarize(root):
    replay = json.loads((root / 'replay.json').read_text())
    grades = {r['id']: r for r in json.loads((root / 'grading/grades.json').read_text())}
    blind = {r['id']: r for r in json.loads((root / 'grading/blind-answers.json').read_text())}
    mapping = json.loads((root / 'grading/private-map.json').read_text())
    assert len(grades) == len(blind) == len(mapping) == 55
    assert set(grades) == set(blind) == {r['id'] for r in mapping}
    by_key = {}
    for item in mapping:
        grade = grades[item['id']]
        assert grade['case_id'] == item['case_id'] == blind[item['id']]['case_id']
        for key in ('support_quote', 'unsupported_quote', 'missing_ack_quote'):
            if grade.get(key):
                assert grade[key] in blind[item['id']]['answer']
        key = (item['stratum'], item['trial'], item['arm'], item['case_id'])
        assert key not in by_key
        by_key[key] = grade
    rows = replay['rows']
    assert len(rows) == len({(r['stratum'], r['trial'], r['arm'], r['id']) for r in rows}) == 156
    groups = []
    for stratum in ('cpu_06b', 'metal_4b'):
        for trial in (1, 2):
            for arm in ('no_gate', 'qwen_json', 'kev'):
                selected = [r for r in rows if (r['stratum'], r['trial'], r['arm']) == (stratum, trial, arm)]
                assert {r['id'] for r in selected} == {f'S{i:02}' for i in range(1, 14)}
                outcomes = Counter()
                for row in selected:
                    key = (stratum, trial, arm, row['id'])
                    if row['status'] == 'answered':
                        grade = by_key[key]
                        outcomes[grade['grade']] += 1
                    else:
                        assert key not in by_key
                        outcomes[row['status']] += 1
                times = sorted(r['total_ms'] for r in selected if r['total_ms'] is not None)
                calls = [call for r in selected for call in r.get('synthesis', {}).get('record', {}).get('calls', [])]
                groups.append({'stratum': stratum, 'trial': trial, 'arm': arm, 'planned': 13,
                               'outcomes': dict(outcomes), 'measured_timing_cases': len(times),
                               'median_ms': statistics.median(times), 'p95_ms': times[math.ceil(len(times)*.95)-1],
                               'total_component_ms': sum(times),
                               'gate_calls': sum(r.get('gate', {}).get('model_called', False) for r in selected),
                               'synthesis_calls': len(calls),
                               'synthesis_requests_written': sum(c['requests_written'] for c in calls),
                               'gate_allows': [r['id'] for r in selected if r.get('action') == 'allow']})
    return {'scope': 'Primary trial1; trial2 is correlated repetition. Every row includes all13 planned IDs; one common upstream failure. Timings exclude acquisition and process startup/I/O; cache effects preclude efficiency claims.',
            'fully_sufficient_cases': 0, 'false_deferral_rate': None,
            'blind_grade_counts': dict(Counter(r['grade'] for r in grades.values())),
            'ambiguous_grade_ids': [r['id'] for r in grades.values() if r.get('ambiguous')],
            'groups': groups, 'replay_sha256': hashlib.sha256((root/'replay.json').read_bytes()).hexdigest()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    text = json.dumps(summarize(args.directory), indent=2) + '\n'
    if args.output:
        with args.output.open('x') as stream:
            stream.write(text)
    else:
        print(text, end='')
