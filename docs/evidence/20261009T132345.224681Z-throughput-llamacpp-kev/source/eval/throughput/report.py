#!/usr/bin/env python3
"""Render throughput summaries as Markdown tables with the pre-declared readings.

Usage: python3 eval/throughput/report.py RUN_DIRECTORY [RUN_DIRECTORY ...]
Pass the Kev and the Qwen run together so the shared-state reading can be evaluated.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

import fixtures

SPEEDUP_AT_LEAST = 2.0
P95_RATIO_AT_MOST = 3.0
AGREEMENT_DROP_AT_MOST = 2


def number(value, digits=0):
    return '—' if value is None else f'{value:,.{digits}f}'


def complete(cell):
    return cell is not None and cell.get('status') == 'complete'


def find(cells, workload, arm, slots, concurrency, kv_unified=False):
    for cell in cells:
        profile = cell['profile']
        if (cell['workload'], cell['arm'], profile['slots'], profile['concurrency'], profile['kv_unified']) == \
                (workload, arm, slots, concurrency, kv_unified):
            return cell
    return None


def batching(cells, workload, arm):
    """8 slots vs 1 slot: >=2x decisions/s, p95 <=3x, agreement no more than 2 below."""
    one, eight = find(cells, workload, arm, 1, 1), find(cells, workload, arm, 8, 8)
    reading = {'workload': workload, 'arm': arm, 'result': None}
    if not (complete(one) and complete(eight)) or not one['decisions_per_s'] or not one['request_ms']['p95']:
        reading['reason'] = 'not evaluable: the 1x1 and 8x8 cells must both complete'
        return reading
    speedup = eight['decisions_per_s'] / one['decisions_per_s']
    p95_ratio = eight['request_ms']['p95'] / one['request_ms']['p95']
    drop = one['label_agreement']['matched'] - eight['label_agreement']['matched']
    reading.update(speedup=speedup, p95_ratio=p95_ratio, agreement_drop=drop,
                   result=speedup >= SPEEDUP_AT_LEAST and p95_ratio <= P95_RATIO_AT_MOST and drop <= AGREEMENT_DROP_AT_MOST)
    return reading


def shared_state(cells):
    """Best W2 Kev questions/s vs best W2 Qwen JSON questions/s; every planned cell must complete."""
    planned = {arm: [c for c in fixtures.CELLS if c.workload == 'w2' and c.arm == arm] for arm in ('kev', 'qwen_json')}
    best = {}
    for arm, plan in planned.items():
        found = [find(cells, 'w2', arm, c.slots, c.concurrency, c.kv_unified) for c in plan]
        if not all(complete(cell) for cell in found):
            return {'result': None, 'reason': f'not evaluable: every planned W2 {arm} cell must complete'}
        best[arm] = max(found, key=lambda cell: cell['decisions_per_s'] or 0)
    return {'result': best['kev']['decisions_per_s'] > best['qwen_json']['decisions_per_s'],
            'kev_best': best['kev']['cell'], 'kev_questions_per_s': best['kev']['decisions_per_s'],
            'qwen_best': best['qwen_json']['cell'], 'qwen_questions_per_s': best['qwen_json']['decisions_per_s']}


def readings(cells):
    groups = sorted({(c['workload'], c['arm']) for c in cells})
    return {'batches_usefully': [batching(cells, w, a) for w, a in groups], 'kev_shared_state_advantage': shared_state(cells)}


def verdict(value):
    return {True: 'yes', False: 'no', None: 'not evaluable'}[value]


def table(cells):
    lines = ['| Cell | Slots × clients | Unified KV | Valid / planned questions | Questions/s | Requests/s '
             '| p50 ms | p95 ms | Label agreement | Prompt tokens processed / cached | Busy slots per decode | Status |',
             '| --- | ---: | :---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |']
    for cell in cells:
        profile = cell['profile']
        delta = (cell.get('metrics') or {}).get('measured_delta') or {}
        agreement = cell['label_agreement']
        status = cell['status'] + (f' ({cell["stop_reason"]})' if cell.get('stop_reason') else '')
        lines.append(
            f'| `{cell["cell"]}` | {profile["slots"]} × {profile["concurrency"]} | {"yes" if profile["kv_unified"] else "no"} '
            f'| {cell["valid_questions"]} / {cell["planned_questions"]} | {number(cell["decisions_per_s"], 2)} '
            f'| {number(cell["requests_per_s"], 2)} | {number(cell["request_ms"]["p50"])} | {number(cell["request_ms"]["p95"])} '
            f'| {agreement["matched"]} / {agreement["total"]} '
            f'| {number(delta.get("prompt_tokens_total"))} / {number(delta.get("prompt_tokens_cached_total"))} '
            f'| {number(delta.get("busy_slots_per_decode"), 2)} | {status} |')
    return '\n'.join(lines)


def render(summaries):
    cells = [cell for summary in summaries for cell in summary.get('cells', [])]
    result = readings(cells)
    lines = ['# Throughput screen', '',
             'Small pilot on one laptop; screening readings, not adoption gates or a benchmark. '
             'Questions/s counts valid answers over measured passes only; latency includes queueing. '
             'Prompt tokens and busy slots come from the runtime `/metrics` delta over measured passes.', '']
    for summary in summaries:
        lines.append(f'- `{summary.get("runtime")}` / `{summary.get("model")}`: status {summary.get("status")}, '
                     f'stop reason {summary.get("stop_reason")}, run {summary.get("run_id")}')
    lines += ['', table(cells), '', '## Pre-declared readings', '',
              '| Reading | Speedup (8×8 / 1×1) | p95 ratio | Agreement drop | Result |', '| --- | ---: | ---: | ---: | --- |']
    for reading in result['batches_usefully']:
        lines.append(f'| {reading["workload"].upper()} {reading["arm"]} batches usefully '
                     f'| {number(reading.get("speedup"), 2)} | {number(reading.get("p95_ratio"), 2)} '
                     f'| {number(reading.get("agreement_drop"))} | {verdict(reading["result"])} |')
    shared = result['kev_shared_state_advantage']
    lines += ['', f'Kev shared-state advantage: **{verdict(shared["result"])}**'
              + (f' (Kev best `{shared["kev_best"]}` {number(shared["kev_questions_per_s"], 2)} questions/s vs '
                 f'Qwen JSON best `{shared["qwen_best"]}` {number(shared["qwen_questions_per_s"], 2)})'
                 if shared['result'] is not None else f' ({shared["reason"]})'), '']
    return '\n'.join(lines)


def main(argv):
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    summaries = [json.loads((Path(path) / 'summary.json').read_text()) for path in argv]
    print(render(summaries))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
