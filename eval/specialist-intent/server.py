#!/usr/bin/env python3
"""Serial, bounded loopback CPU specialist HTTP server; one loaded model.

Run inside the separately pinned CPU container. A request deadline at the caller
does not interrupt a torch kernel: the owner must enforce the outer process
deadline and terminate this process on a wedge. No output cache is implemented.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import socket
import time
import threading

from adapters import MAX_BODY, encoded, entailment_index, finite, require, strict_json
from preflight import encoder_inputs, native_formatter, verify_artifacts

PAIR_BATCH = 9


class BoundedHTTPServer(HTTPServer):
    request_queue_size = 2


def watchdog(seconds):
    """Kill this owned process even if a native torch kernel never returns."""
    timer = threading.Timer(seconds, lambda: os._exit(124))
    timer.daemon = True
    timer.start()
    return timer


class Engine:
    def __init__(self, arm, model_dir):
        self.arm = arm
        self.lock = verify_artifacts(model_dir, arm)
        require(platform.system() == 'Linux' and platform.machine() == 'aarch64',
                'specialist execution requires frozen Linux ARM64 CPU environment')
        environment = json.loads((Path(__file__).parent / 'locks/dependencies.json').read_text())
        for package, version in environment['direct_versions'].items():
            require(importlib.metadata.version(package) == version, 'dependency version mismatch: ' + package)
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        self.torch = torch
        torch.set_num_threads(4)
        torch.set_num_interop_threads(1)
        torch.manual_seed(0)
        torch.use_deterministic_algorithms(True)
        self.tokenizer = AutoTokenizer.from_pretrained(model_dir, local_files_only=True, trust_remote_code=False)
        self.formatter = None
        if arm == 'gliclass':
            from gliclass import GLiClassModel
            from gliclass.pipeline import BaseZeroShotClassificationPipeline
            config = json.loads((Path(model_dir) / 'config.json').read_text())
            self.formatter = native_formatter(config)
            self.model = GLiClassModel.from_pretrained(model_dir, local_files_only=True)
            self.native_scores = BaseZeroShotClassificationPipeline._postprocess_logits
        else:
            self.model = AutoModelForSequenceClassification.from_pretrained(
                model_dir, local_files_only=True, trust_remote_code=False)
            require(self.model.config.label2id == {'entailment': 0, 'not_entailment': 1},
                    'pinned NLI native label mapping changed')
        self.model = self.model.to(device='cpu', dtype=torch.float32).eval()
        require(all(p.device.type == 'cpu' and (not p.is_floating_point() or p.dtype == torch.float32)
                    for p in self.model.parameters()), 'CPU FP32 requirement violated')

    def classify(self, payload):
        require(payload.get('arm') == self.arm, 'request sent to wrong loaded model')
        started = time.monotonic()
        cpu_started = time.process_time()
        inputs, lengths = encoder_inputs(payload, self.tokenizer, formatter=self.formatter, tensors=True)
        candidates = payload['candidates']
        with self.torch.inference_mode():
            if self.arm == 'gliclass':
                logits = self.model(**inputs, max_num_classes=9).logits
                require(tuple(logits.shape) == (1, 9), 'invalid GLiClass native score shape')
                labels = [c['label'] for c in candidates]
                _, native = self.native_scores(logits[0], labels, 'single-label', 0)
                values = logits[0].tolist()
                rows = [{'id': c['id'], 'score': finite(native[c['label']]), 'logit': finite(value)}
                        for c, value in zip(candidates, values)]
            else:
                # All nine pairs form one logical request and one fixed batch.
                logits = self.model(**inputs).logits
                require(tuple(logits.shape) == (PAIR_BATCH, 2), 'invalid NLI native score shape')
                index = entailment_index(self.model.config.label2id)
                native = self.torch.softmax(logits[:, index], dim=0).tolist()
                rows = [{'id': c['id'], 'score': finite(score), 'logits': [finite(x) for x in row]}
                        for c, score, row in zip(candidates, native, logits.tolist())]
        response = {'arm': self.arm, 'scores': rows, 'truncated': False, 'input_lengths': lengths,
                    'cpu_seconds': time.process_time() - cpu_started, 'elapsed_seconds': time.monotonic() - started,
                    'precision': 'float32', 'inference_threads': 4, 'query_batch': 1}
        if self.arm == 'deberta':
            response.update(label2id=self.model.config.label2id, pair_batch=PAIR_BATCH)
        return response


def handler_for(engine):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.0'

        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def reply(self, status, value):
            body = encoded(value)
            require(len(body) <= MAX_BODY, 'response size limit exceeded')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path != '/health':
                return self.reply(404, {'error': 'unknown endpoint'})
            return self.reply(200, {'ready': True, 'arm': engine.arm, 'revision': engine.lock['revision']})

        def do_POST(self):
            timer = watchdog(10)
            try:
                return self.bounded_post()
            finally:
                timer.cancel()

        def bounded_post(self):
            if self.path != '/classify':
                return self.reply(404, {'error': 'unknown endpoint'})
            try:
                require(not self.headers.get('Transfer-Encoding'), 'chunked requests unsupported')
                lengths = self.headers.get_all('Content-Length') or []
                require(len(lengths) == 1, 'exact Content-Length required')
                size = int(lengths[0])
                require(0 < size <= MAX_BODY, 'request size limit exceeded')
                body = self.rfile.read(size)
                require(len(body) == size, 'incomplete request body')
                payload = strict_json(body)
                require(isinstance(payload, dict), 'JSON object required')
                result = engine.classify(payload)
            except (ValueError, TypeError, KeyError, socket.timeout) as exc:
                return self.reply(400, {'error': str(exc)})
            except Exception as exc:
                # Preserve visible runtime failure, never turn it into no_override.
                self.log_error('inference failed: %s', exc)
                return self.reply(500, {'error': type(exc).__name__})
            return self.reply(200, result)
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--arm', required=True, choices=['gliclass', 'deberta'])
    parser.add_argument('--model-dir', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8099)
    parser.add_argument('--host', choices=['127.0.0.1', '0.0.0.0'], default='127.0.0.1')
    parser.add_argument('--lifetime-seconds', type=int, default=1920)
    args = parser.parse_args()
    require(1024 <= args.port <= 65535, 'invalid port')
    require(0 < args.lifetime_seconds <= 1920, 'outer process lifetime must be at most 32 minutes')
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    lifetime = watchdog(args.lifetime_seconds)
    readiness = watchdog(120)
    engine = Engine(args.arm, args.model_dir)
    readiness.cancel()
    # Serial server bounds query concurrency to one; concurrency-two load test
    # measures queueing as part of its HTTP latency rather than parallel kernels.
    server = BoundedHTTPServer((args.host, args.port), handler_for(engine))
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
        lifetime.cancel()


if __name__ == '__main__':
    main()
