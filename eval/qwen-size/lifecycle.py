#!/usr/bin/env python3
"""Own one bounded CPU model lifecycle, its evidence, and verified shutdown.

Run this in the foreground and register the PID printed before log redirection.
No model result is interpreted here; the frozen evaluator owns all decisions.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
import uuid

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / 'eval/specialist-intent'))
import evidence as e
import preflight
import resources

PORT = 18095
LABEL = 'io.semselect.qwen-size.lifecycle'


def now():
    return datetime.now(timezone.utc).isoformat()


def file_sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def command(args):
    return subprocess.run(args, capture_output=True, text=True, timeout=15, check=True).stdout


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('local runtime redirect refused')


def get_json(url):
    if not re.fullmatch(r'http://127\.0\.0\.1:18095/(health|props|v1/models)', url):
        raise ValueError('unexpected lifecycle endpoint')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(url, timeout=1) as response:
        data = response.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise ValueError('runtime metadata response exceeds 1 MiB')
    return json.loads(data)


def verify_previous(lifecycle, current):
    for prior in lifecycle.glob('*/launch.json'):
        if prior.parent == current:
            continue
        result = prior.parent / 'result.json'
        if not result.exists():
            raise RuntimeError('prior lifecycle has no final cleanup record: ' + str(prior.parent))
        cleanup = e.read(result).get('cleanup', {})
        if cleanup.get('container_shutdown_verified') is not True or cleanup.get('cli_reaped') is not True:
            raise RuntimeError('prior shutdown unverified: ' + str(prior.parent))


def launch_args(location, model, runtime, name, nonce):
    artifact = Path(model['artifact_path']).resolve()
    if ',' in str(artifact):
        raise ValueError('model bind-mount path contains Docker separator')
    return ['docker', 'run', '--cidfile', str(location / 'container.cid'), '--name', name,
            '--label', LABEL + '=' + nonce, '--platform', 'linux/arm64',
            '--cpus', '4', '--cpuset-cpus', '0-3', '--memory', '4g',
            '-p', f'127.0.0.1:{PORT}:8080',
            '--mount', f'type=bind,src={artifact},dst=/model/{model["filename"]},readonly',
            runtime['image_id'], '-m', '/model/' + model['filename'], '--host', '0.0.0.0',
            '--port', '8080', '--alias', model['alias'], '-ngl', '0', '-t', '4', '-tb', '4',
            '-c', '4096', '-b', '1024', '-ub', '512', '-np', '1', '--no-context-shift',
            '--metrics', '-lv', '4', '--no-cache-prompt', '--cache-reuse', '0', '--cache-ram', '0']


def inspect_owned(name, nonce):
    """Resolve only our unique label, including launch failure before a cidfile."""
    result = subprocess.run(['docker', 'inspect', name], capture_output=True, text=True, timeout=15)
    if result.returncode:
        if 'No such' in result.stderr:
            return None
        raise RuntimeError('cannot inspect owned container: ' + result.stderr)
    found = json.loads(result.stdout)[0]
    if found.get('Config', {}).get('Labels', {}).get(LABEL) != nonce:
        raise RuntimeError('container ownership label differs; refusing cleanup')
    return found


def cleanup_owned(location, name, nonce, process):
    """Persist failures; never infer container shutdown from a dead Docker CLI."""
    record = {'container_shutdown_verified': process is None, 'cli_reaped': process is None}
    try:
        if process is not None:
            owned = inspect_owned(name, nonce)
            if owned is None:
                record.update(container_shutdown_verified=True, container_absent=True)
            else:
                cid = owned['Id']
                cidfile = location / 'container.cid'
                if cidfile.exists() and cidfile.read_text().strip() != cid:
                    raise RuntimeError('cidfile and owned container disagree; refusing cleanup')
                record['container_id'] = cid
                events = record['events'] = []
                for action in (['stop', '--time', '2'], ['kill']):
                    if not owned['State']['Running']:
                        break
                    result = subprocess.run(['docker', *action, cid], capture_output=True, text=True, timeout=15)
                    events.append({'action': action, 'exit_code': result.returncode, 'stderr': result.stderr})
                    owned = inspect_owned(name, nonce)
                    if owned is None:
                        break
                record['container_shutdown_verified'] = owned is None or owned['State']['Running'] is False
                if owned is not None:
                    record['stopped_inspect'] = owned
    except Exception as exc:
        record.update(container_shutdown_verified=False, error=type(exc).__name__ + ': ' + str(exc))
    finally:
        try:
            if process is not None:
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    # This is only the owned CLI; container shutdown remains a separate proof.
                    process.kill()
                    process.wait(timeout=5)
                record.update(cli_reaped=True, docker_exit_code=process.returncode)
        except Exception as exc:
            record.update(cli_reaped=False, cli_reap_error=type(exc).__name__ + ': ' + str(exc))
    return record


def verify_ready(props, models, model, startup_log):
    if props.get('default_generation_settings', {}).get('n_ctx') != 4096 or props.get('total_slots') != 1:
        raise ValueError('runtime context or slot count differs from frozen plan')
    if props.get('model_alias') != model['alias'] or [v.get('id') for v in models.get('data', [])] != [model['alias']]:
        raise ValueError('runtime model alias differs from model lock')
    threads = re.search(r'n_threads\s*=\s*(\d+)\s*\(n_threads_batch\s*=\s*(\d+)\)', startup_log)
    if not threads or threads.groups() != ('4', '4'):
        raise ValueError('startup did not verify four inference and batch threads')
    # /props does not expose n_threads in this pinned runtime; its startup line does.
    return {'inference_threads': 4, 'batch_threads': 4, 'thread_evidence': threads.group(0),
            'context': 4096, 'slots': 1, 'alias': model['alias']}


def verify_running(env, model, runtime, observation, args):
    inspected = observation['inspect']
    if observation.get('verified') is not True or observation.get('oom') or not observation.get('running'):
        raise ValueError('CPU resource capture did not verify a healthy runtime')
    if inspected['HostConfig'].get('CpusetCpus') != '0-3':
        raise ValueError('runtime affinity differs from frozen four CPUs')
    if inspected['Config'].get('Cmd') != args[args.index(runtime['image_id']) + 1:]:
        raise ValueError('actual runtime command differs from frozen launch')
    if any(v.startswith('LLAMA_ARG_') for v in inspected['Config'].get('Env', [])):
        raise ValueError('runtime environment overrides are not permitted')
    mounts = [m for m in inspected['Mounts'] if m['Destination'] == '/model/' + model['filename']]
    if len(mounts) != 1 or mounts[0].get('RW') is not False or Path(mounts[0]['Source']).resolve() != Path(model['artifact_path']).resolve():
        raise ValueError('actual model mount differs from verified read-only artifact')
    actual = command(['docker', 'exec', env['container_id'], 'sha256sum', '/opt/llama/llama-server']).split()[0]
    if actual != runtime['runtime_sha256']:
        raise ValueError('actual runtime binary differs from frozen hash')
    return {'binary_sha256': actual, 'affinity': '0-3', 'command_verified': True, 'model_mount_readonly_verified': True}


def token_records(run, arm, model, runtime, evaluator, base):
    destination = run / ('tokens-' + arm + '.json')
    jobs = evaluator.requests_for_arm(run, arm)
    identity = {'revision': model['revision'], 'artifacts': {model['filename']: model['sha256']},
                'runtime_revision': runtime['runtime_revision']}
    if destination.exists():
        records = e.read(destination)
    else:
        records = [preflight.preflight_request(job['request'], qwen_url=base, identity=identity) for job in jobs]
        # A failed preflight never leaves a partial manifest that looks complete.
        e.verify_tokens(records, jobs, 'qwen', {'model_revision': model['revision'], 'artifacts': identity['artifacts']})
        e.save_new(destination, records)
    e.verify_tokens(records, jobs, 'qwen', {'model_revision': model['revision'], 'artifacts': identity['artifacts']})
    if any(row.get('identity') != identity for row in records):
        raise ValueError('token preflight runtime identity differs from lock')
    return destination


def execute(run, arm, stage, stream, log, evaluator=None):
    if evaluator is None:
        import evaluate as evaluator
    run = Path(run).resolve()
    lifecycle = run / 'lifecycle'
    lifecycle.mkdir(exist_ok=True)
    location = lifecycle / (arm + '-' + stage)
    location.mkdir()
    nonce = uuid.uuid4().hex
    name = 'semselect-qwen-size-' + arm.replace('_', '-') + '-' + stage + '-' + nonce[:10]
    record = {'arm': arm, 'stage': stage, 'pid': os.getpid(), 'started_at': now(),
              'helper_sha256': file_sha(__file__), 'name': name, 'ownership_label': nonce,
              'lifecycle_limit_seconds': 1920}
    process = None
    env = None
    started = time.monotonic()
    guard = (lifecycle / 'loaded-model.lock').open('a')
    try:
        fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        verify_previous(lifecycle, location)
        evaluator.barrier(run, stage, arm)  # Includes source freeze and independent review.
        previous = evaluator.previous_stop(run, arm)
        if not previous and evaluator.remaining(run, arm) <= 0:
            previous = 'inference budget exhausted'
        if previous:
            record['unattempted_reason'] = previous
            if stage != 'load':
                record['completion'] = evaluator.run_stage(run, stage, arm, None, None, None, unavailable=previous)
            record['success'] = True
            return record
        if stage == 'load' and evaluator.report(run)['arms'][arm]['load_status'] != 'required':
            raise ValueError('load prerequisites not satisfied or already complete')
        model, runtime = evaluator.model(arm), evaluator.runtime_lock()
        artifact = Path(model['artifact_path'])
        if artifact.stat().st_size != model['size_bytes'] or file_sha(artifact) != model['sha256']:
            raise ValueError('actual GGUF bytes differ from model lock')
        if model['runtime_revision'] != runtime['runtime_revision']:
            raise ValueError('model and runtime revision pins disagree')
        image = json.loads(command(['docker', 'image', 'inspect', runtime['image_id']]))[0]
        if image['Id'] != runtime['image_id'] or image['Architecture'] != 'arm64' or image['Os'] != 'linux' or image['Config']['Labels'].get('io.semselect.llama-revision') != runtime['runtime_revision']:
            raise ValueError('actual CPU image differs from frozen runtime')
        record['prelaunch_inventory'] = {'docker': command(['docker', 'ps', '--no-trunc', '--format', '{{json .}}']),
                                         'processes': command(['ps', '-Ao', 'pid,pcpu,rss,comm'])}
        args = launch_args(location, model, runtime, name, nonce)
        record['command'] = args
        e.save_new(location / 'launch.json', record)
        env = {'model_revision': model['revision'], 'artifacts': {model['filename']: model['sha256']},
               'dependencies': runtime['dependencies'], 'runtime_revision': runtime['runtime_revision'],
               'runtime_sha256': runtime['runtime_sha256'], 'architecture': 'linux/arm64', 'precision': 'Q4_K_M',
               'cpu_only': True, 'cpus': 4, 'threads': 4, 'query_batch_size': 1, 'memory_limit_bytes': 4294967296,
               'container_digest': runtime['image_id'], 'endpoint': f'http://127.0.0.1:{PORT}/v1/chat/completions',
               'thinking': False, 'prefix_cache_disabled_verified': False,
               'cache_policy': '--no-cache-prompt --cache-reuse 0 --cache-ram 0; per-request evidence must establish effective cache behavior'}
        launch_time = time.monotonic()
        process = subprocess.Popen(args, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
        record['docker_cli_pid'] = process.pid
        base = f'http://127.0.0.1:{PORT}'
        while time.monotonic() - launch_time < 120:
            if process.poll() is not None:
                raise RuntimeError('runtime exited before readiness: ' + str(process.returncode))
            try:
                health = get_json(base + '/health')
                if health.get('status') == 'ok':
                    break
            except (OSError, ValueError):
                pass
            time.sleep(.2)
        else:
            raise TimeoutError('readiness exceeded 120 seconds')
        env['readiness_seconds'] = time.monotonic() - launch_time
        env['container_id'] = (location / 'container.cid').read_text().strip()
        props, models = get_json(base + '/props'), get_json(base + '/v1/models')
        e.save_new(location / 'props.json', props)
        e.save_new(location / 'models.json', models)
        record['ready_contract'] = verify_ready(props, models, model, log.read_text(errors='replace'))
        observation = resources.capture(env)
        record['running_contract'] = verify_running(env, model, runtime, observation, args)
        env.update(idle_memory_bytes=observation['current_memory_bytes'], peak_memory_bytes=observation['peak_memory_bytes'],
                   peak_includes_startup=True, cpu_time_seconds=observation['cpu_time_seconds'], oom=observation['oom'],
                   startup_log_sha256=file_sha(log), verified=True,
                   background_contention=[{'prelaunch_inventory': str(location / 'launch.json'), 'measurement': 'Snapshot only; no claim of otherwise idle host'}],
                   cleanup={'status': 'owned runtime active', 'final_record': str(location / 'result.json')})
        e.save_new(location / 'environment.json', env)
        e.save_new(location / 'ready-resources.json', observation)
        record['health'] = health
        tokens = token_records(run, arm, model, runtime, evaluator, base)
        if stage == 'load':
            record['completion'] = evaluator.run_load(run, arm, env['endpoint'], location / 'environment.json', tokens)
        else:
            record['completion'] = evaluator.run_stage(run, stage, arm, env['endpoint'], location / 'environment.json', tokens)
        record['success'] = True
    except Exception as exc:
        record.update(success=False, error=type(exc).__name__ + ': ' + str(exc))
        traceback.print_exc(file=stream)
    finally:
        try:
            if env is not None:
                cidfile = location / 'container.cid'
                if cidfile.exists():
                    env['container_id'] = cidfile.read_text().strip()
                    record['final_resources'] = resources.capture(env)
        except Exception as exc:
            record['final_resources_error'] = type(exc).__name__ + ': ' + str(exc)
            record['success'] = False
        try:
            record['cleanup'] = cleanup_owned(location, name, nonce, process)
        except Exception as exc:
            record['cleanup'] = {'container_shutdown_verified': False, 'cli_reaped': False, 'error': repr(exc)}
        finally:
            record['success'] = bool(record.get('success') and record['cleanup'].get('container_shutdown_verified') and record['cleanup'].get('cli_reaped'))
            record.update(elapsed_seconds=time.monotonic() - started, finished_at=now())
            try:
                print(json.dumps({key: record[key] for key in ('success', 'error', 'elapsed_seconds', 'unattempted_reason') if key in record}), file=stream, flush=True)
                sys.stdout.flush()
                sys.stderr.flush()
                record['log_sha256'] = file_sha(log)
                e.save_new(location / 'result.json', record)
            finally:
                guard.close()
    return record


def interrupt(signum, frame):
    raise TimeoutError('owned lifecycle interrupted or 32-minute deadline; signal ' + str(signum))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--arm', choices=('qwen35_2b', 'qwen3_17b'), required=True)
    parser.add_argument('--stage', choices=('development', 'primary', 'sensitivity', 'load'), required=True)
    args = parser.parse_args()
    log = REPO / '.enjoy-logs' / ('qwen-size-' + args.arm + '-' + args.stage + '.log')
    log.parent.mkdir(exist_ok=True)
    with log.open('x') as stream:
        announcement = json.dumps({'pid': os.getpid(), 'log': str(log), 'arm': args.arm, 'stage': args.stage})
        print(announcement, flush=True)
        print(announcement, file=stream, flush=True)
        os.dup2(stream.fileno(), 1)
        os.dup2(stream.fileno(), 2)
        for sig in (signal.SIGALRM, signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, interrupt)
        signal.alarm(1920)
        try:
            result = execute(args.run, args.arm, args.stage, stream, log)
        finally:
            signal.alarm(0)
    return 0 if result.get('success') else 1


if __name__ == '__main__':
    raise SystemExit(main())
