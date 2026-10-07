#!/usr/bin/env python3
"""One bounded HTTP call in a disposable process; no retries or output caching."""
from __future__ import annotations

import base64
import hashlib
import json
import signal
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()


def strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON key: '+key)
            result[key] = value
        return result
    def invalid(value):
        raise ValueError('nonfinite JSON number: '+value)
    return json.loads(data, object_pairs_hook=pairs, parse_constant=invalid)


def loopback(url):
    parsed = urlparse(url)
    if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', '::1', 'localhost') or parsed.username or parsed.password or parsed.fragment:
        raise ValueError('evaluation endpoint must be loopback HTTP without credentials')
    return url


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('evaluation endpoint redirected')


def call(url, payload, timeout, max_bytes=1048576):
    loopback(url)
    if not 0 < timeout <= 60:
        raise ValueError('deadline must be in (0,60] seconds')
    body = encoded(payload)
    if len(body) > 1048576:
        raise ValueError('request exceeds 1 MiB')
    record = {'status': 'error', 'request_sha256': hashlib.sha256(body).hexdigest()}
    def expired(signum, frame):
        raise TimeoutError('absolute HTTP deadline exceeded')
    prior = signal.signal(signal.SIGALRM, expired)
    started = time.monotonic()
    signal.setitimer(signal.ITIMER_REAL, timeout)
    try:
        # Ignore environment proxies for local evaluation data.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        try:
            response = opener.open(urllib.request.Request(url, body, {'Content-Type': 'application/json'}), timeout=timeout)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            raw = response.read(max_bytes+1)
            record.update(http_status=response.status, response_base64=base64.b64encode(raw).decode(), response_sha256=hashlib.sha256(raw).hexdigest())
        if len(raw) > max_bytes:
            raise ValueError('response exceeds limit')
        if record['http_status'] != 200:
            raise ValueError('HTTP '+str(record['http_status']))
        record['response'] = strict_json(raw)
        record['status'] = 'received'
    except (OSError, ValueError, TimeoutError) as error:
        record['error'] = f'{type(error).__name__}: {error}'
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, prior)
        record['http_ms'] = (time.monotonic()-started)*1000
    return record


if __name__ == '__main__':
    inp = strict_json(sys.stdin.buffer.read(1048577))
    print(json.dumps(call(inp['url'], inp['payload'], inp['timeout']), allow_nan=False))
