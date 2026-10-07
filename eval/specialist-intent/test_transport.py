from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import subprocess
import sys
import threading
import unittest

from transport import encoded, loopback, strict_json


@contextmanager
def endpoint(handler):
    server = HTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/classify'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        if thread.is_alive():
            raise AssertionError('owned test server did not stop')


class TransportTests(unittest.TestCase):
    def call(self, url, timeout=.5):
        process = subprocess.run([sys.executable, str(Path(__file__).with_name('transport.py'))], input=encoded({'url': url, 'payload': {'query': 'synthetic'}, 'timeout': timeout}), capture_output=True, timeout=2)
        self.assertEqual(process.returncode, 0, process.stderr)
        return strict_json(process.stdout)

    def test_absolute_deadline_retains_error_duration(self):
        entered = threading.Event()
        release = threading.Event()
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                entered.set()
                release.wait(1)
            def log_message(self, *args):
                pass
        try:
            with endpoint(Handler) as url:
                result = self.call(url, .05)
                self.assertTrue(entered.is_set())
                release.set()
                self.assertEqual(result['status'], 'error')
                self.assertGreaterEqual(result['http_ms'], 40)
                self.assertLess(result['http_ms'], 1000)
        finally:
            release.set()

    def test_non_200_keeps_raw_response(self):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b'{"error":"failure"}')
            def log_message(self, *args):
                pass
        with endpoint(Handler) as url:
            result = self.call(url)
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['http_status'], 500)
        self.assertIn('response_base64', result)

    def test_remote_and_duplicate_json_fail_closed(self):
        with self.assertRaises(ValueError):
            loopback('https://example.com/classify')
        with self.assertRaises(ValueError):
            strict_json('{"operation":"count","operation":"sum"}')


if __name__ == '__main__':
    unittest.main()
