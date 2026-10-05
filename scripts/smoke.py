#!/usr/bin/env python3
"""Real native inference checks, deliberately separate from contract tests."""
import json
import math
import os
from pathlib import Path
import urllib.request

from evaluate import validate_native

ROOT = Path(__file__).resolve().parents[1]
payload = json.loads((ROOT / 'examples/decision.json').read_text())
url = 'http://127.0.0.1:' + os.environ.get('SEMSELECT_PORT', '8084') + '/v1/systemone'
request = urllib.request.Request(url, json.dumps(payload).encode(), {'Content-Type': 'application/json'})
with urllib.request.urlopen(request, timeout=125) as response:
    result = json.load(response)
validate_native(result, list(payload['questions']['route']['criteria']))
score = result['answers']['urgency']
levels = payload['questions']['urgency']['criteria']
assert score['type'] == 'score' and set(score['probabilities']) == {'0', '1', '2'}
assert all(math.isfinite(p) and 0 <= p <= 1 for p in score['probabilities'].values())
assert math.isclose(sum(score['probabilities'].values()), 1, abs_tol=1e-5)
assert 0 <= score['score'] <= len(levels)-1
assert math.isclose(score['score'], sum(int(i)*p for i,p in score['probabilities'].items()), abs_tol=1e-5)
assert score['legend'] == {str(i): label for i,label in enumerate(levels)}
assert result['answers']['refund']['type'] == 'noul'
assert 0 <= result['answers']['refund']['noul'] <= 1
print(json.dumps(result, indent=2))
print('Real inference shape checks passed. A valid answer does not establish correctness.')
