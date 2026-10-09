#!/usr/bin/env python3
"""Build, serve, or evaluate the pinned runtime natively on Apple Silicon."""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import resource
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
import zipfile

from model import load_lock, verify

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.native'
REVISION = '6c59c40076c00eab49754dc955d7652d93f9e125'
SOURCE_SHA = 'efe5ae8270793300807cd2c0e20e748816f2c153971ce5610450f0e6a23d214e'
CMAKE_URL = 'https://files.pythonhosted.org/packages/ca/f7/f28a1df8d35cb6e37ff087e9f995cc0253ab1ffc55b12cf276436db4d392/cmake-4.1.2-py3-none-macosx_10_10_universal2.whl'
CMAKE_SHA = '415396a7320856c64bd27ca00950b2bbb161604bff60ae5ebf256e2ca08b81ab'
BUILD = CACHE / ('build-' + REVISION[:12])
SERVER = BUILD / 'bin/llama-server'
GUARD = ROOT / 'bin/semselect'


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def acquire(url, path, digest):
    if path.is_file() and sha256(path) == digest:
        return
    partial = path.with_suffix('.partial')
    try:
        subprocess.run(['curl', '--fail', '--location', '--retry', '3', '--connect-timeout', '20',
                        '--max-time', '600', '--output', str(partial), url], check=True)
        if sha256(partial) != digest:
            raise RuntimeError(f'Checksum mismatch: {path.name}')
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)


def build_command(command, timeout, env=None):
    child = subprocess.Popen(command, cwd=ROOT, env=env, start_new_session=True)
    try:
        code = child.wait(timeout=timeout)
        if code:
            raise subprocess.CalledProcessError(code, command)
    except BaseException:
        # CMake/make/clang can have descendants. The new session belongs to this
        # invocation, so cancellation can stop the whole group before unlocking.
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(child.pid, sig)
            except ProcessLookupError:
                break
            except PermissionError:
                # Some agent sandboxes forbid group signals even to owned
                # sessions. Reap the direct child and report the limitation;
                # do not claim that its descendants were stopped.
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
                raise
            if sig == signal.SIGTERM:
                time.sleep(0.25)
        child.wait(timeout=5)
        raise


def runtime_hashes():
    return {p.name: ({'symlink': os.readlink(p)} if p.is_symlink() else {'sha256': sha256(p)})
            for p in sorted(SERVER.parent.iterdir()) if p.is_file() or p.is_symlink()}


def build():
    # All downloaded tools and source stay in this ignored project directory.
    wheel = CACHE / 'cmake-4.1.2.whl'
    acquire(CMAKE_URL, wheel, CMAKE_SHA)
    cmake = CACHE / 'cmake/cmake/data/bin/cmake'
    # These managed directories are disposable. Re-extract verified archives on
    # every build so local cache edits cannot masquerade as the pinned source.
    if (CACHE / 'cmake').exists():
        shutil.rmtree(CACHE / 'cmake')
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(CACHE / 'cmake')
    for binary in cmake.parent.iterdir():
        binary.chmod(0o755)
    archive_path = CACHE / 'llama.tar.gz'
    acquire(f'https://api.github.com/repos/ggml-org/llama.cpp/tarball/{REVISION}', archive_path, SOURCE_SHA)
    source = CACHE / 'ggml-org-llama.cpp-6c59c40'
    if source.exists():
        shutil.rmtree(source)
    with tarfile.open(archive_path) as archive:
        archive.extractall(CACHE, filter='data')
    command = [str(cmake), '-S', str(source), '-B', str(BUILD), '-DCMAKE_BUILD_TYPE=Release',
               '-DGGML_METAL=ON', '-DGGML_METAL_EMBED_LIBRARY=ON', '-DGGML_CUDA=OFF',
               '-DGGML_NATIVE=ON', '-DLLAMA_OPENSSL=OFF', '-DLLAMA_BUILD_TESTS=OFF',
               '-DLLAMA_BUILD_EXAMPLES=OFF', '-DLLAMA_BUILD_UI=OFF']
    # A source archive has no Git metadata. Do not stamp it with the enclosing
    # semselect checkout's commit; the verified source hash records provenance.
    build_env = dict(os.environ, GIT_CEILING_DIRECTORIES=str(CACHE))
    build_command(command, timeout=120, env=build_env)
    build_command([str(cmake), '--build', str(BUILD), '--clean-first', '--target', 'llama-server', '-j', '4'],
                  timeout=1800, env=build_env)
    GUARD.parent.mkdir(exist_ok=True)
    build_command(['go', 'build', '-trimpath', '-o', str(GUARD), './cmd/semselect'], timeout=180)
    (CACHE / 'build.json').write_text(json.dumps({
        'runtime_revision': REVISION, 'source_sha256': SOURCE_SHA, 'cmake_version': '4.1.2',
        'cmake_wheel_sha256': CMAKE_SHA, 'configure_command': command,
        'runtime_files_sha256': runtime_hashes(), 'guard_sha256': sha256(GUARD),
        'platform': platform.platform(),
        'clang': subprocess.check_output(['clang', '--version'], text=True).strip(),
        'go': subprocess.check_output(['go', 'version'], text=True).strip(),
    }, indent=2) + '\n')


def available(port):
    with socket.socket() as sock:
        try:
            sock.bind(('127.0.0.1', port))
        except OSError as exc:
            raise RuntimeError(f'Loopback port {port} is in use; stop its service first (task down for Docker)') from exc


def ready(url, children, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(child.poll() is not None for child in children):
            raise RuntimeError('A service exited before readiness; see the runtime/guard logs')
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError):
            pass
        time.sleep(0.25)
    raise RuntimeError(f'Readiness timed out: {url}')


def stop(children):
    # Signal only processes started here. No stale PID files or global process kills.
    for child in reversed(children):
        if child.poll() is None:
            child.terminate()
    deadline = time.monotonic() + 10
    for child in reversed(children):
        try:
            child.wait(timeout=max(0.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)


def metal_offload(log):
    offload = re.search(r'offloaded (\d+)/(\d+) layers to GPU', log)
    device = re.search(r'using device MTL\d+ \(Apple [^)]+\)', log)
    if not offload or int(offload[1]) == 0 or offload[1] != offload[2] or not device:
        raise RuntimeError('Full Metal offload was not confirmed; inspect runtime log')
    return offload.group(0)


def run(args):
    lock_path = ROOT / ('models.baseline.lock.json' if args.baseline else 'models.lock.json')
    lock = load_lock(lock_path)
    model = ROOT / 'models' / lock['filename']
    if lock['runtime_revision'] != REVISION:
        raise RuntimeError('Model and Metal runtime revisions disagree')
    if not verify(model, lock):
        raise RuntimeError(f'Model missing or corrupt; run python3 scripts/model.py --lock {lock_path.name}')
    build_info = json.loads((CACHE / 'build.json').read_text())
    if build_info['runtime_revision'] != REVISION or runtime_hashes() != build_info['runtime_files_sha256'] or sha256(GUARD) != build_info['guard_sha256']:
        raise RuntimeError('Native build provenance mismatch; run task metal:build')
    runtime_port = 8085
    public_port = 8085 if args.baseline else 8084
    available(runtime_port)
    if not args.baseline:
        available(public_port)
    profile = 'metal-qwen35-4b' if args.baseline else 'metal-kev-4b'
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    prefix = ROOT / 'results' / profile / run_id / 'run'
    prefix.parent.mkdir(parents=True)
    command = [str(SERVER), '-m', str(model), '--alias', lock['alias'], '--host', '127.0.0.1',
               '--port', str(runtime_port), '-ngl', '99', '-t', '4', '-tb', '4', '-c', '4096',
               '-b', '512', '-ub', '512', '-np', '1', '--no-context-shift', '--metrics', '-lv', '4']
    children = []
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    start = time.monotonic()
    result = {'run_id': run_id, 'model': lock, 'build': build_info, 'runtime_command': command,
              'evaluation_path': str(prefix.with_suffix('.json')) if args.action == 'evaluate' else None,
              'hardware': subprocess.check_output(['sysctl', '-n', 'machdep.cpu.brand_string'], text=True).strip(),
              'memory_bytes': int(subprocess.check_output(['sysctl', '-n', 'hw.memsize'], text=True)),
              'platform': platform.platform(), 'evaluation': args.action == 'evaluate',
              'notes': 'Native macOS; no container CPU/memory quota. Child resource usage includes startup, runtime, guard and evaluation, with warmup. Peak RSS is the largest child lifetime peak, not summed memory or Metal allocation.'}
    try:
        with prefix.with_suffix('.runtime.log').open('w') as runtime_log, prefix.with_suffix('.guard.log').open('w') as guard_log:
            runtime_env = {key: value for key, value in os.environ.items() if not key.startswith('LLAMA_ARG_')}
            children.append(subprocess.Popen(command, env=runtime_env, stdout=runtime_log, stderr=subprocess.STDOUT))
            ready(f'http://127.0.0.1:{runtime_port}/health', children)
            log = prefix.with_suffix('.runtime.log').read_text()
            result['gpu_offload'] = metal_offload(log)
            if not args.baseline:
                env = dict(os.environ, SEMSELECT_ADDR='127.0.0.1:8084', SEMSELECT_UPSTREAM='http://127.0.0.1:8085',
                           SEMSELECT_MODEL=lock['alias'], SEMSELECT_TIMEOUT='120s')
                children.append(subprocess.Popen([str(GUARD)], env=env, stdout=guard_log, stderr=subprocess.STDOUT))
                ready('http://127.0.0.1:8084/ready', children)
            print(f'Ready: http://127.0.0.1:{public_port}; {result["gpu_offload"]}; logs: {prefix}.*.log', flush=True)
            if args.action == 'evaluate':
                if not args.baseline:
                    with prefix.with_suffix('.primitives.txt').open('w') as output:
                        subprocess.run([sys.executable, str(ROOT / 'scripts/smoke.py')], cwd=ROOT,
                                       env=dict(os.environ, SEMSELECT_PORT='8084'), stdout=output, check=True, timeout=150)
                evaluation = [sys.executable, str(ROOT / 'scripts/evaluate.py'), '--backend',
                              'seminstruct' if args.baseline else 'semselect', '--url', f'http://127.0.0.1:{public_port}',
                              '--model', lock['alias'], '--output', str(prefix.with_suffix('.json')),
                              '--hardware', result['hardware'], '--notes', 'Native Metal; 4 threads; 4096 context; 512 batch/microbatch; one slot; Q4_K_M; see accompanying resources JSON and logs.']
                subprocess.run(evaluation, cwd=ROOT, check=True, timeout=1800)
            else:
                while all(child.poll() is None for child in children):
                    time.sleep(0.25)
                raise RuntimeError('A service exited unexpectedly; inspect logs')
            result['status'] = 'passed'
    except BaseException as exc:
        result['status'] = 'interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed'
        result['error'] = str(exc)
        raise
    finally:
        stop(children)
        after = resource.getrusage(resource.RUSAGE_CHILDREN)
        result.update(wall_seconds=time.monotonic() - start,
                      child_cpu_seconds=(after.ru_utime + after.ru_stime) - (before.ru_utime + before.ru_stime),
                      largest_child_peak_rss_bytes=after.ru_maxrss)
        prefix.with_suffix('.resources.json').write_text(json.dumps(result, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['build', 'serve', 'evaluate'])
    parser.add_argument('--baseline', action='store_true', help='Use the matched Qwen3.5-4B chat baseline')
    args = parser.parse_args()
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        parser.error('This launcher targets native macOS on Apple Silicon')
    CACHE.mkdir(exist_ok=True)
    def interrupted(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    try:
        with (CACHE / 'operation.lock').open('a') as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError('Another Metal build/run owns this checkout; stop it first') from None
            if args.action == 'build':
                build()
            else:
                run(args)
    except KeyboardInterrupt:
        print('Stopped owned Metal processes.', flush=True)
        return 130
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f'Metal operation failed: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
