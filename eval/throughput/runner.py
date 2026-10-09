"""Bounded concurrent request execution and per-cell summary math.

Every planned request becomes exactly one row: ok, invalid, error or not_run.
There are no retries; a failure is evidence, not something to paper over.
"""
from __future__ import annotations

import base64
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import hashlib
import http.client
import json
import statistics
import time
import urllib.error
import urllib.request

import fixtures
import evaluate  # scripts/, put on sys.path by fixtures

TIMEOUT_SECONDS = 30
MAX_CONSECUTIVE_ERRORS = 3
MAX_RESPONSE_BYTES = evaluate.MAX_RESPONSE_BYTES
TOKEN_KEYS = ('prompt_processed', 'prompt_cached', 'input_tokens')


def token_counts(response):
    """Per-request prompt accounting where the endpoint reports it.

    /completion and /v1/chat/completions return timings.prompt_n (processed) and
    timings.cache_n (reused); /v1/systemone returns only usage.input_tokens."""
    counts = {}
    timings = response.get('timings') if isinstance(response, dict) else None
    usage = response.get('usage') if isinstance(response, dict) else None
    if isinstance(timings, dict):
        counts.update(prompt_processed=timings.get('prompt_n'), prompt_cached=timings.get('cache_n'))
    if isinstance(usage, dict) and 'input_tokens' in usage:
        counts['input_tokens'] = usage['input_tokens']
    return {key: value for key, value in counts.items() if type(value) is int}


def base_row(job):
    return {'case_id': job.case_id, 'order': job.order, 'path': job.path,
            'request_sha256': hashlib.sha256(job.body).hexdigest() if job.body is not None else None,
            'reference': dict(job.reference), 'labels': {q: None for q in job.questions},
            'valid_questions': 0, 'matched': 0, 'http_status': None, 'error_kind': None, 'error': None}


def call(base_url, job, timeout, clock=time.monotonic, origin=0.0):
    """POST the exact body once and classify the outcome."""
    row = base_row(job)
    row.update(status='error', timeout_s=timeout)
    raw = b''
    started = clock()
    try:
        wire = urllib.request.Request(base_url + job.path, job.body, {'Content-Type': 'application/json'})
        try:
            response = urllib.request.urlopen(wire, timeout=timeout)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            row['http_status'] = response.status
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except TimeoutError as exc:
        row.update(error_kind='timeout', error=f'{type(exc).__name__}: {exc}')
    except urllib.error.URLError as exc:
        kind = 'timeout' if isinstance(exc.reason, TimeoutError) else 'connection'
        row.update(error_kind=kind, error=f'URLError: {exc.reason}')
    except (OSError, http.client.HTTPException) as exc:
        row.update(error_kind='connection', error=f'{type(exc).__name__}: {exc}')
    finished = clock()
    kept = raw[:MAX_RESPONSE_BYTES]
    row.update(request_ms=(finished - started) * 1000, started_s=started - origin, finished_s=finished - origin,
               response_bytes_base64=base64.b64encode(kept).decode(), response_bytes_read=len(raw),
               response_sha256=hashlib.sha256(kept).hexdigest(), response_truncated=len(raw) > MAX_RESPONSE_BYTES)
    if row['error_kind'] is not None:
        return row
    if row['http_status'] != 200:
        row.update(error_kind=f'http_{row["http_status"]}', error=f'HTTP {row["http_status"]}')
        return row
    row['status'] = 'invalid'
    try:
        fixtures.require(not row['response_truncated'], 'response exceeded one MiB')
        response = json.loads(raw)
        json.dumps(response, allow_nan=False)
        row['tokens'] = token_counts(response)
        labels = job.parse(response)
    except (ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
        row['error'] = f'{type(exc).__name__}: {exc}'
        return row
    row['labels'] = {q: labels.get(q) for q in job.questions}
    row['valid_questions'] = sum(label is not None for label in row['labels'].values())
    row['matched'] = sum(label is not None and label == job.reference.get(q) for q, label in row['labels'].items())
    if row['valid_questions'] == len(job.questions):
        row['status'] = 'ok'
    else:
        row['error'] = 'one or more answers failed validation'
    return row


def not_run(job, reason):
    row = base_row(job)
    row.update(status='not_run', request_ms=None, error=reason or 'not reached')
    return row


def run_jobs(jobs, send, concurrency, deadline, clock=time.monotonic, on_row=None, state=None,
             timeout=TIMEOUT_SECONDS):
    """Keep up to `concurrency` requests in flight until done, out of budget, or failing.

    send(job, timeout) returns one row. state carries the consecutive-error count and
    stop reason across the phases of one cell. Unstarted requests become not_run rows.
    Returns rows in job order and the elapsed time from first submission to last completion."""
    state = state if state is not None else {}
    state.setdefault('consecutive_errors', 0)
    state.setdefault('stop_reason', None)
    rows = [None] * len(jobs)
    pending = {}
    next_index = 0
    first = last = None
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        while pending or (next_index < len(jobs) and state['stop_reason'] is None):
            while len(pending) < concurrency and next_index < len(jobs) and state['stop_reason'] is None:
                remaining = deadline - clock()
                if remaining <= 0:
                    state['stop_reason'] = 'budget'
                    break
                if first is None:
                    first = clock()
                pending[pool.submit(send, jobs[next_index], min(timeout, remaining))] = next_index
                next_index += 1
            if not pending:
                break
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in sorted(done, key=pending.get):
                index = pending.pop(future)
                rows[index] = future.result()
                last = clock()
                state['consecutive_errors'] = state['consecutive_errors'] + 1 if rows[index]['status'] == 'error' else 0
                if state['consecutive_errors'] >= MAX_CONSECUTIVE_ERRORS and state['stop_reason'] is None:
                    state['stop_reason'] = 'three consecutive runtime errors'
                if on_row:
                    on_row(index, rows[index])
    for index, row in enumerate(rows):
        if row is None:
            rows[index] = not_run(jobs[index], state['stop_reason'])
            if on_row:
                on_row(index, rows[index])
    return rows, (last - first) if first is not None and last is not None else 0.0


def summarize(rows, elapsed_s, questions_per_request):
    """Throughput, latency and agreement for measured rows only (warmup excluded)."""
    attempted = [r for r in rows if r['status'] != 'not_run']
    times = [r['request_ms'] for r in attempted]
    valid_questions = sum(r['valid_questions'] for r in rows)
    valid = sum(r['status'] == 'ok' for r in rows)
    errors = Counter(r['error_kind'] for r in rows if r['status'] == 'error')
    tokens = {}
    for key in TOKEN_KEYS:
        values = [r['tokens'][key] for r in rows if key in r.get('tokens', {})]
        if values:
            tokens[key] = {'requests': len(values), 'sum': sum(values), 'mean': sum(values) / len(values)}
    return {
        'planned': len(rows), 'planned_questions': len(rows) * questions_per_request,
        'valid': valid, 'valid_questions': valid_questions,
        'invalid': sum(r['status'] == 'invalid' for r in rows),
        'not_run': sum(r['status'] == 'not_run' for r in rows),
        'errors': {'total': sum(errors.values()), 'by_kind': dict(sorted(errors.items()))},
        'elapsed_s': elapsed_s,
        'decisions_per_s': valid_questions / elapsed_s if elapsed_s > 0 else None,
        'requests_per_s': valid / elapsed_s if elapsed_s > 0 else None,
        'request_ms': {'p50': statistics.median(times) if times else None,
                       'p95': evaluate.percentile(times, .95), 'samples': len(times)},
        'label_agreement': {'matched': sum(r['matched'] for r in rows),
                            'total': sum(ref is not None for r in rows for ref in r['reference'].values())},
        'tokens_per_request': tokens,
    }
