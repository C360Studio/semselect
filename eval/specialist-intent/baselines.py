"""Evaluation-local rules and a bridge to the unchanged pinned Go classifier.

The bridge runs a fresh classifier per query, using the existing training set.
Its cold wall time includes process setup; native classifier timing is retained.
No generated confidence, inference model, or argument copying is added here.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parent
REFERENCE = ROOT.parent / 'query-routing'
ARMS = ('keyword', 'keyword_bm25_default', 'keyword_bm25_tuned', 'improved_rules')
OPERATIONS = ('similarity', 'path', 'zone', 'count', 'avg', 'sum', 'min', 'max', 'no_override')
PATTERNS = {
    'similarity': r'\b(?:similar|similarity|simlar|comparable|resembl\w*|alike|analogous|like)\b',
    'path': r'\b(?:connect\w*|links?|linked|graph edges|relationships?|related|neighbou?rs?|reachable|traverse|dependencies|upstream|downstream|path)\b',
    'zone': r'\b(?:in|within|inside|located in|belonging to)\s+(?:(?:the|this|that)\s+)?(?:zone|area|location|wing|[a-z]+-[\w-]+)\b|\b(?:zone|area)-[\w-]+\b',
    'count': r'\b(?:how many|count|counnt|tally|number of)\b',
    'avg': r'\b(?:average|averge|avg|mean)\b',
    'sum': r'\b(?:sum|total|add up|add together|combined)\b',
    'min': r'\b(?:minimum|minmum|min|smallest|lowest)\b',
    'max': r'\b(?:maximum|maxmimum|max|largest|highest)\b',
}
UNSUPPORTED = re.compile(r'\b(?:median|percentile|standard deviation|variance|rank\w*|sort\w*|top\s+\d+|bottom\s+\d+|yesterday|today|tomorrow|elapsed|last\s+(?:\d+\s+)?(?:hours?|days?|weeks?|months?|years?)|recent|latest|earliest|within\s+\d+\s*(?:km|miles?))\b', re.I)
INSTRUCTION_OVERRIDE = re.compile(r'\b(?:ignore|disregard|override)\b.{0,100}\b(?:instructions?|rules?|system|classification)\b|\b(?:output|return|respond with)\s+(?:only\s+)?["\']?(?:no_override|similarity|path|zone|count|avg|sum|min|max)\b|\b(?:system instruction|classify this text as)\b', re.I)
QUOTED = re.compile(r'"[^"\n]*"|(?<!\w)\x27[^\x27\n]*\x27(?!\w)|`[^`\n]*`|“[^”\n]*”')
NEGATION = re.compile(r"\b(?:not|never|without|do not|don't|dont|avoid|skip|exclude|rather than)\b", re.I)


def improved_operation(query: str) -> str:
    """Small fixed grammar; not a general natural-language parser.

    Negation suppresses its remaining clause, separated by punctuation/contrast.
    Quoted operation mentions are removed; supported affirmative clauses survive.
    Multiple distinct affirmative operations or unsupported requests map to noop.
    """
    _query(query)
    if INSTRUCTION_OVERRIDE.search(query):
        return 'no_override'
    text = QUOTED.sub(' ', query).lower()
    clauses = re.split(r'[;.!?,]|\b(?:but|instead|however)\b', text)
    affirmative = []
    for clause in clauses:
        negation = NEGATION.search(clause)
        affirmative.append(clause[:negation.start()] if negation else clause)
    text = ' '.join(affirmative)
    if UNSUPPORTED.search(text):
        return 'no_override'
    # Count's compound phrase must not also become sum.
    text = re.sub(r'\btotal\s+(?:number|count)\b', 'count', text)
    operations = [op for op, pattern in PATTERNS.items() if re.search(pattern, text)]
    return operations[0] if len(operations) == 1 else 'no_override'


def _query(query):
    if not isinstance(query, str) or not query.strip() or len(query.encode()) > 8192:
        raise ValueError('query must be nonempty and at most 8192 bytes')


def operation_from_native(native: dict) -> str:
    """Decode only the inherited hint vocabulary; malformed/conflicting is error.

    Empty options map to the ordinary no_override label, not upstream abstention.
    Native unsupported options remain errors, rather than becoming correct noops.
    """
    if not isinstance(native, dict) or not isinstance(native.get('Options'), dict):
        raise ValueError('native classification requires Options object')
    options = native['Options']
    allowed = {'use_embeddings', 'path_intent', 'path_start_node', 'path_predicates',
               'aggregation_type', 'aggregation_field'}
    if set(options) - allowed:
        raise ValueError('unsupported native SearchOptions fields')
    for key in ('use_embeddings', 'path_intent'):
        if key in options and type(options[key]) is not bool:
            raise ValueError('native boolean has wrong type')
    for key in ('path_start_node', 'aggregation_type', 'aggregation_field'):
        if key in options and not isinstance(options[key], str):
            raise ValueError('native string has wrong type')
    predicates = options.get('path_predicates', [])
    if predicates not in ([], ['located_in']):
        raise ValueError('unsupported native path predicate')
    operations = []
    if options.get('use_embeddings'):
        operations.append('similarity')
    if options.get('path_intent'):
        operations.append('zone' if predicates else 'path')
    elif predicates or options.get('path_start_node'):
        raise ValueError('native path arguments without path intent')
    aggregation = options.get('aggregation_type', '')
    if aggregation:
        if aggregation not in ('count', 'avg', 'sum', 'min', 'max'):
            raise ValueError('unsupported native aggregation')
        operations.append(aggregation)
    elif options.get('aggregation_field'):
        raise ValueError('native aggregation field without operation')
    if len(operations) > 1:
        raise ValueError('conflicting native operations')
    operation = operations[0] if operations else 'no_override'
    intent = native.get('Intent', '')
    if intent and intent != operation:
        raise ValueError('native intent conflicts with SearchOptions')
    return operation


def classify_code(query: str, arm: str, threshold: float = 0.7,
                  driver: Path | str | None = None, *, timeout: float = 10) -> dict:
    """Return operation and untouched native evidence, with bounded subprocess wait.

    Driver path is required for actual code arms; it must be the separately built
    pinned query-routing driver. Caller freezes its SHA before execution. No
    automatic build/download, substitute implementation, or output cache exists.
    """
    started = time.monotonic()
    result = {'status': 'error', 'operation': None, 'native_options': None,
              'native': None, 'elapsed_ms': 0.0}
    try:
        _query(query)
        if arm not in ARMS:
            raise ValueError('unknown code arm')
        if not isinstance(threshold, (int, float)) or isinstance(threshold, bool) or not math.isfinite(threshold) or not 0 <= threshold <= 1:
            raise ValueError('threshold must be finite and in [0,1]')
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not math.isfinite(timeout) or not 0 < timeout <= 300:
            raise ValueError('timeout must be finite and in (0,300]')
        if arm == 'improved_rules':
            result.update(status='selected', operation=improved_operation(query))
            return result
        if driver is None:
            raise ValueError('pinned query-routing driver path required')
        driver = Path(driver).resolve(strict=True)
        if not driver.is_file():
            raise ValueError('driver must be a regular executable file')
        result['driver_sha256'] = hashlib.sha256(driver.read_bytes()).hexdigest()
        training_path = REFERENCE / 'training.json'
        training = json.loads(training_path.read_text())
        result['training_sha256'] = hashlib.sha256(training_path.read_bytes()).hexdigest()
        payload = {'arm': 'keyword' if arm == 'keyword' else 'keyword_bm25',
                   'threshold': threshold if arm == 'keyword_bm25_tuned' else 0.7,
                   'examples': training, 'queries': [{'id': 'query', 'text': query}]}
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()
        result['input_sha256'] = hashlib.sha256(encoded).hexdigest()
        with tempfile.TemporaryDirectory(prefix='semselect-specialist-code-') as temporary:
            input_path, output_path = Path(temporary) / 'input.json', Path(temporary) / 'output.jsonl'
            input_path.write_bytes(encoded)
            command = [str(driver), '--input', str(input_path), '--output', str(output_path),
                       '--timeout', f'{timeout}s']
            timed_out = False
            try:
                process = subprocess.run(command, capture_output=True, timeout=timeout + 1,
                                         env={**os.environ, 'TZ': 'UTC'})
                result['process'] = {'exit_code': process.returncode, 'stderr': process.stderr.decode(errors='replace')[:8192]}
            except subprocess.TimeoutExpired as exc:
                # subprocess.run kills and reaps its child before raising. Read any
                # synced evidence before the temporary directory is removed.
                timed_out = True
                result['process'] = {'exit_code': None, 'timed_out': True,
                                     'stderr': (exc.stderr or b'').decode(errors='replace')[:8192]}
            if output_path.exists():
                if output_path.stat().st_size > 32 * 1024 * 1024:
                    raise ValueError('driver output exceeds 32 MiB')
                result['records'] = [json.loads(line) for line in output_path.read_text().splitlines()]
            records = result.get('records', [])
            if len(records) == 2 and isinstance(records[1], dict):
                result['native'] = records[1].get('classification')
                if isinstance(result['native'], dict):
                    result['native_options'] = result['native'].get('Options')
            if timed_out:
                raise ValueError('pinned driver timed out; partial native records retained')
            if process.returncode != 0:
                raise ValueError('pinned driver failed; native records retained')
            if (len(records) != 2 or any(not isinstance(row, dict) for row in records)
                    or records[0].get('kind') != 'provenance'
                    or records[1].get('kind') != 'classification' or records[1].get('id') != 'query'
                    or records[1].get('query') != query or records[1].get('status') != 'ok'
                    or any(row.get('input_sha256') != result['input_sha256'] for row in records)):
                raise ValueError('invalid driver result envelope')
            result.update(status='selected', operation=operation_from_native(result['native']))
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        result.update(status='error', operation=None, error=str(exc))
    finally:
        result['elapsed_ms'] = (time.monotonic() - started) * 1000
    return result
