from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
import unittest

import fixtures
import runner


class Stub:
    """Loopback HTTP server. respond(path, body) -> (status, bytes) runs in the server thread."""

    def __init__(self, respond):
        self.respond = respond
        self.hits = Counter()
        self.lock = threading.Lock()
        self.inflight = self.max_inflight = 0
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers['Content-Length']))
                with stub.lock:
                    stub.hits[body] += 1
                    stub.inflight += 1
                    stub.max_inflight = max(stub.max_inflight, stub.inflight)
                try:
                    status, payload = stub.respond(self.path, body)
                finally:
                    with stub.lock:
                        stub.inflight -= 1
                try:
                    self.send_response(status)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            do_GET = do_POST

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True)

    @property
    def url(self):
        return f'http://127.0.0.1:{self.server.server_port}'

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def answer(label):
    return 200, json.dumps({'label': label, 'timings': {'prompt_n': 7, 'cache_n': 3}}).encode()


def jobs(count, reference='allow'):
    return [fixtures.Job(f'C{i:02d}', 'normal', '/decide', json.dumps({'id': i}).encode(), ('action',),
                         {'action': reference}, lambda response: {'action': response['label']}) for i in range(count)]


def execute(stub, items, concurrency, timeout=runner.TIMEOUT_SECONDS, state=None):
    send = lambda job, limit: runner.call(stub.url, job, limit)  # noqa: E731
    return runner.run_jobs(items, send, concurrency, time.monotonic() + 60, state=state, timeout=timeout)


class ConcurrencyTests(unittest.TestCase):
    def test_client_concurrency_overlaps_requests(self):
        # Every handler waits for all four; this only succeeds if four requests are in flight together.
        barrier = threading.Barrier(4)

        def respond(path, body):
            try:
                barrier.wait(timeout=10)
            except threading.BrokenBarrierError:
                return 500, b'{"error":"requests did not overlap"}'
            return answer('allow')
        with Stub(respond) as stub:
            rows, elapsed = execute(stub, jobs(4), concurrency=4)
        self.assertEqual([r['status'] for r in rows], ['ok'] * 4)
        self.assertEqual(stub.max_inflight, 4)
        self.assertGreater(elapsed, 0)

    def test_one_client_never_overlaps(self):
        with Stub(lambda path, body: answer('allow')) as stub:
            rows, _ = execute(stub, jobs(5), concurrency=1)
        self.assertEqual(stub.max_inflight, 1)
        self.assertEqual(len(rows), 5)

    def test_rows_keep_job_order_and_exact_bytes(self):
        with Stub(lambda path, body: answer('allow')) as stub:
            items = jobs(6)
            rows, _ = execute(stub, items, concurrency=3)
        self.assertEqual([r['case_id'] for r in rows], [j.case_id for j in items])
        self.assertEqual(set(stub.hits), {j.body for j in items})
        self.assertEqual(rows[0]['tokens'], {'prompt_processed': 7, 'prompt_cached': 3})


class FailureTests(unittest.TestCase):
    def test_429_is_recorded_once_and_not_retried(self):
        items = jobs(3)

        def respond(path, body):
            return (429, b'{"error":"busy"}') if body == items[1].body else answer('allow')
        with Stub(respond) as stub:
            rows, _ = execute(stub, items, concurrency=1)
        self.assertEqual(stub.hits[items[1].body], 1)
        self.assertEqual((rows[1]['status'], rows[1]['http_status'], rows[1]['error_kind']), ('error', 429, 'http_429'))
        self.assertEqual(rows[1]['response_bytes_base64'], 'eyJlcnJvciI6ImJ1c3kifQ==')
        self.assertEqual([rows[0]['status'], rows[2]['status']], ['ok', 'ok'])

    def test_timeout_is_recorded_once_and_not_retried(self):
        items = jobs(2)
        release = threading.Event()

        def respond(path, body):
            if body == items[0].body:
                release.wait(timeout=10)
            return answer('allow')
        try:
            with Stub(respond) as stub:
                rows, _ = execute(stub, items, concurrency=1, timeout=0.2)
                release.set()
        finally:
            release.set()
        self.assertEqual(stub.hits[items[0].body], 1)
        self.assertEqual((rows[0]['status'], rows[0]['error_kind']), ('error', 'timeout'))
        self.assertEqual(rows[1]['status'], 'ok')

    def test_three_consecutive_runtime_errors_stop_the_cell(self):
        items = jobs(10)
        with Stub(lambda path, body: (503, b'{"error":"loading"}')) as stub:
            state = {}
            rows, _ = execute(stub, items, concurrency=1, state=state)
        self.assertEqual(sum(stub.hits.values()), 3)
        self.assertEqual([r['status'] for r in rows], ['error'] * 3 + ['not_run'] * 7)
        self.assertEqual(state['stop_reason'], 'three consecutive runtime errors')
        self.assertTrue(all(r['error'] == state['stop_reason'] for r in rows[3:]))

    def test_errors_separated_by_success_do_not_stop(self):
        items = jobs(6)
        failing = {items[i].body for i in (0, 1, 3, 4)}
        with Stub(lambda path, body: (500, b'{}') if body in failing else answer('allow')) as stub:
            state = {}
            rows, _ = execute(stub, items, concurrency=1, state=state)
        self.assertIsNone(state['stop_reason'])
        self.assertEqual(sum(r['status'] == 'not_run' for r in rows), 0)

    def test_budget_stops_submission_and_bounds_each_timeout(self):
        now = [0.0]

        def respond(path, body):
            now[0] += 10
            return answer('allow')
        clock = lambda: now[0]  # noqa: E731
        with Stub(respond) as stub:
            state = {}
            rows, elapsed = runner.run_jobs(jobs(5), lambda job, limit: runner.call(stub.url, job, limit, clock),
                                            1, deadline=25, clock=clock, state=state)
        self.assertEqual(state['stop_reason'], 'budget')
        self.assertEqual([r['status'] for r in rows], ['ok'] * 3 + ['not_run'] * 2)
        self.assertEqual([r['timeout_s'] for r in rows[:3]], [25, 15, 5])
        self.assertEqual(elapsed, 30)

    def test_oversized_response_is_bounded_and_invalid(self):
        payload = b'{"label":"' + b'a' * runner.MAX_RESPONSE_BYTES + b'"}'
        with Stub(lambda path, body: (200, payload)) as stub:
            rows, _ = execute(stub, jobs(1), concurrency=1)
        self.assertEqual(rows[0]['status'], 'invalid')
        self.assertTrue(rows[0]['response_truncated'])
        self.assertEqual(rows[0]['response_bytes_read'], runner.MAX_RESPONSE_BYTES + 1)


class SummaryTests(unittest.TestCase):
    def row(self, status, ms, valid=0, matched=0, kind=None):
        return {'status': status, 'request_ms': ms, 'valid_questions': valid, 'matched': matched,
                'error_kind': kind, 'reference': {'action': 'allow'}, 'tokens': {'prompt_processed': 10}}

    def test_summary_math(self):
        rows = [self.row('ok', 100, 1, 1), self.row('ok', 200, 1, 0), self.row('invalid', 300),
                self.row('error', 400, kind='http_429'), self.row('error', 500, kind='timeout'),
                self.row('not_run', None)]
        summary = runner.summarize(rows, 2.0, 1)
        self.assertEqual((summary['planned'], summary['valid'], summary['valid_questions']), (6, 2, 2))
        self.assertEqual((summary['invalid'], summary['not_run']), (1, 1))
        self.assertEqual(summary['errors'], {'total': 2, 'by_kind': {'http_429': 1, 'timeout': 1}})
        self.assertEqual((summary['decisions_per_s'], summary['requests_per_s']), (1.0, 1.0))
        # Nearest-rank p95 over attempted requests, failures included, like scripts/evaluate.py.
        self.assertEqual(summary['request_ms'], {'p50': 300, 'p95': 500, 'samples': 5})
        self.assertEqual(summary['label_agreement'], {'matched': 1, 'total': 6})
        self.assertEqual(summary['tokens_per_request']['prompt_processed'], {'requests': 6, 'sum': 60, 'mean': 10})

    def test_bundled_questions_count_each_valid_answer(self):
        rows = [dict(self.row('ok', 10, 3, 2), reference={'a': 1, 'b': 2, 'c': 3}),
                dict(self.row('invalid', 10, 2, 2), reference={'a': 1, 'b': 2, 'c': 3})]
        summary = runner.summarize(rows, 1.0, 3)
        self.assertEqual((summary['planned_questions'], summary['valid_questions'], summary['decisions_per_s']), (6, 5, 5.0))
        self.assertEqual(summary['label_agreement'], {'matched': 4, 'total': 6})

    def test_no_elapsed_time_has_no_rate(self):
        summary = runner.summarize([self.row('not_run', None)], 0.0, 1)
        self.assertIsNone(summary['decisions_per_s'])
        self.assertEqual(summary['request_ms']['p95'], None)


if __name__ == '__main__':
    unittest.main()
