"""Bounded local SGLang/MLX compatibility probe; not an accuracy evaluation."""
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'results/sglang-probe/20261005T151940Z'
MODEL = ROOT / '.sglang/models/Qwen3.5-4B-4bit'
BASE = 'http://127.0.0.1:30101'
RECORD = {'kind': 'compatibility_probe', 'started_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'requests': []}

def save():
    (OUT / 'probe.json').write_text(json.dumps(RECORD, indent=2) + '\n')

def request(path, payload=None, timeout=90):
    start = time.monotonic()
    req = urllib.request.Request(BASE + path, data=None if payload is None else json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status, raw = r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read().decode()
    try:
        body = json.loads(raw)
    except ValueError:
        body = raw
    row = {'path': path, 'request': payload, 'status': status, 'response': body, 'elapsed_seconds': time.monotonic()-start}
    RECORD['requests'].append(row)
    save()
    return row

def check_distribution(values):
    return bool(values) and all(isinstance(v,(int,float)) and math.isfinite(v) and 0 <= v <= 1 for v in values) and abs(sum(values)-1) < 1e-4

with socket.socket() as s:
    if s.connect_ex(('127.0.0.1', 30101)) == 0:
        raise SystemExit('Refusing to launch: port 30101 already used')

args = [sys.executable, '-m', 'sglang.launch_server', '--model-path', str(MODEL), '--served-model-name', 'qwen35-4b-mlx-probe', '--host', '127.0.0.1', '--port', '30101', '--disable-cuda-graph', '--mlx-enable-sampling', '--language-model-only', '--context-length', '4096', '--max-total-tokens', '8192', '--max-running-requests', '1', '--mem-fraction-static', '0.5', '--grammar-backend', 'llguidance']
env = dict(os.environ, SGLANG_USE_MLX='1', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HOME=str(ROOT/'.sglang/hf'), XDG_CACHE_HOME=str(ROOT/'.sglang/cache'), SGLANG_MLX_CACHE_LIMIT_GB='1')
RECORD['launch_arguments'] = args
RECORD['launch_environment'] = {k:env[k] for k in ['SGLANG_USE_MLX','HF_HUB_OFFLINE','TRANSFORMERS_OFFLINE','HF_HOME','XDG_CACHE_HOME','SGLANG_MLX_CACHE_LIMIT_GB']}
save()
with (OUT/'server.log').open('w') as log:
    proc = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, env=env, start_new_session=True)
    RECORD['server_pid'] = proc.pid
    try:
        deadline = time.monotonic()+150
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f'Server exited before readiness: {proc.returncode}')
            try:
                with urllib.request.urlopen(BASE+'/health', timeout=2) as r:
                    if r.status == 200:
                        break
            except (OSError, urllib.error.URLError):
                time.sleep(1)
        else:
            raise RuntimeError('Server did not become ready within 150 seconds')
        RECORD['ready'] = True
        request('/get_server_info')
        text = 'Please refund the duplicate charge on my invoice.'
        chat = {'model':'qwen35-4b-mlx-probe','messages':[{'role':'system','content':'Classify the customer message. Return billing for invoices and payments, otherwise unknown.'},{'role':'user','content':text}], 'temperature':0,'max_tokens':64,'chat_template_kwargs':{'enable_thinking':False},'response_format':{'type':'json_schema','json_schema':{'name':'route','strict':True,'schema':{'type':'object','properties':{'route':{'type':'string','enum':['billing','unknown']}},'required':['route'],'additionalProperties':False}}}}
        for _ in range(2):
            row = request('/v1/chat/completions', chat)
            try:
                answer = json.loads(row['response']['choices'][0]['message']['content'])
                row['valid_schema'] = set(answer) == {'route'} and answer['route'] in ['billing','unknown']
            except (ValueError, KeyError, TypeError, IndexError):
                row['valid_schema'] = False
            save()
        decisions = {'model':'qwen35-4b-mlx-probe','input':text,'return_prompt_token_ids':True,'questions':[{'id':'route','type':'choice','question':'Which department handles this message?','options':[{'name':'billing','description':'Invoices and payments'},{'name':'unknown','description':'No supported department applies'}]},{'id':'refund','type':'yes_no','question':'Does the sender ask for a refund?'},{'id':'urgency','type':'score','question':'What urgency is explicitly supported by the text?','levels':['No urgent deadline','Time sensitive','Immediate danger']}]}
        for _ in range(2):
            row = request('/v1/decisions', decisions)
            answers = row['response'].get('answers',{}) if isinstance(row['response'],dict) else {}
            row['valid_distributions'] = set(answers) == {'route','refund','urgency'} and all(check_distribution(list(a['probabilities'].values())) and math.isfinite(a['label_mass']) and 0 <= a['label_mass'] <= 1 and len(a['label_token_ids']) == len(a['probabilities']) and len(set(a['label_token_ids'])) == len(a['label_token_ids']) and bool(a['prompt_token_ids']) for a in answers.values())
            save()
            if row['valid_distributions']:
                a = answers['route']
                score = request('/v1/score', {'model':'qwen35-4b-mlx-probe','query':[], 'items':[a['prompt_token_ids']], 'label_token_ids':a['label_token_ids'],'apply_softmax':True,'return_token_logprobs':True})
                scores = score['response'].get('scores',[]) if isinstance(score['response'],dict) else []
                score['valid_distribution'] = len(scores) == 1 and len(scores[0]) == len(a['label_token_ids']) and check_distribution(scores[0])
                if score['valid_distribution']:
                    score['max_delta_from_decisions'] = max(abs(x-y) for x,y in zip(scores[0],a['probabilities'].values()))
                save()
    except Exception as e:
        RECORD['failure'] = f'{type(e).__name__}: {e}'
    finally:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)
        RECORD['server_exit_code'] = proc.returncode
        with socket.socket() as s:
            RECORD['port_closed'] = s.connect_ex(('127.0.0.1',30101)) != 0
        RECORD['finished_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        save()
print(json.dumps({k:v for k,v in RECORD.items() if k not in ['requests','launch_arguments','launch_environment']}, indent=2))
