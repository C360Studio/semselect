#!/usr/bin/env python3
"""Exact complete-input token checks; no weights or inference needed for CLI.

GLiClass's pinned native formatter constructs the combined sequence, then the
actual tokenizer runs with truncation disabled. DeBERTa encodes every complete
premise/hypothesis pair, including special tokens. Never estimate by characters.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
from types import SimpleNamespace

from adapters import encoded, require, http_json

ROOT = Path(__file__).resolve().parent
TOKEN_LIMIT = 512
GLICLASS_PIPELINE_SHA = '389fc107a1c7a3ebe8d69d4809090a0a3bf23057c0d938a4cf2051f6dc6a1c3b'


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def verify_artifacts(model_dir, arm, *, weights=True):
    require(arm in ('gliclass', 'deberta'), 'unknown specialist')
    lock = json.loads((ROOT / 'locks' / (arm + '.json')).read_text())
    model_dir = Path(model_dir).resolve()
    require(len(lock['revision']) == 40, 'model revision must be immutable')
    for name, expected in lock['files'].items():
        if not weights and name == 'model.safetensors':
            continue
        path = (model_dir / name).resolve()
        require(path.is_relative_to(model_dir), 'artifact path escaped model directory')
        require(path.is_file() and path.stat().st_size == expected['size_bytes'] and
                digest(path) == expected['sha256'], 'model artifact missing or changed: ' + name)
    return lock


def native_formatter(config):
    """Use audited native preparation without invoking its truncating tokenizer."""
    import gliclass.pipeline as pipeline
    require(importlib.metadata.version('gliclass') == '0.1.20', 'GLiClass dependency version drift')
    require(digest(pipeline.__file__) == GLICLASS_PIPELINE_SHA, 'GLiClass pipeline source drift')
    require(config['architecture_type'] == 'uni-encoder' and config['prompt_first'] is True,
            'unsupported GLiClass encoding architecture')
    renderer = object.__new__(pipeline.UniEncoderZeroShotClassificationPipeline)
    renderer.model = SimpleNamespace(config=SimpleNamespace(**config))
    renderer.label_token, renderer.sep_token = '<<LABEL>>', '<<SEP>>'
    return renderer.prepare_input


def encoder_inputs(payload, tokenizer, *, formatter=None, tensors=False):
    """Returns exact encodings and lengths; these same encodings feed inference."""
    require(isinstance(payload, dict) and payload.get('arm') in ('gliclass', 'deberta'), 'invalid specialist request')
    require(set(payload) == {'arm', 'query', 'instruction', 'candidates'}, 'unexpected specialist request fields')
    query, instruction, candidates = payload['query'], payload['instruction'], payload['candidates']
    require(type(query) is str and query.strip() and len(query.encode()) <= 16384, 'invalid query')
    require(type(instruction) is str and instruction.strip() and len(instruction.encode()) <= 16384, 'invalid instruction')
    require(isinstance(candidates, list) and len(candidates) == 9, 'expected nine candidates')
    require(all(isinstance(c, dict) and set(c) == {'id', 'label'} and type(c['id']) is str and
                type(c['label']) is str and c['id'] and c['label'] and
                len(c['id']) <= 128 and len(c['label'].encode()) <= 4096 for c in candidates), 'invalid candidates')
    require(len({c['id'] for c in candidates}) == len(candidates) and
            len({c['label'] for c in candidates}) == len(candidates), 'duplicate candidates')
    kwargs = {'truncation': False, 'add_special_tokens': True, 'padding': False}
    if payload['arm'] == 'gliclass':
        require(formatter is not None, 'native GLiClass formatter required')
        # The checkpoint uses these markers as structural delimiters. Treat a
        # literal collision as an encoding incompatibility, never silently add
        # a tenth class or rewrite user text to make an arm fit.
        require(not any(marker in value for marker in ('<<LABEL>>', '<<SEP>>', '<<EXAMPLE>>')
                        for value in [query, instruction] + [c['label'] for c in candidates]),
                'GLiClass reserved marker collides with semantic input')
        text = formatter(query, [c['label'] for c in candidates], prompt=instruction)
        inputs = tokenizer([text], **kwargs)
    else:
        # Instructions are included in every premise. NLI has no system role.
        premises = [instruction + '\nQuery: ' + query] * len(candidates)
        hypotheses = ['The requested operation is: ' + c['label'] for c in candidates]
        inputs = tokenizer(premises, hypotheses, **kwargs)
    lengths = [len(ids) for ids in inputs['input_ids']]
    require(len(lengths) == (1 if payload['arm'] == 'gliclass' else 9), 'tokenizer changed logical batch')
    require(all(0 < length <= TOKEN_LIMIT for length in lengths), 'complete encoder input exceeds 512 tokens')
    if tensors:
        # Padding occurs only after full untruncated encodings have been checked.
        inputs = tokenizer.pad(inputs, padding=True, return_tensors='pt')
    return inputs, lengths


def preflight_request(request, tokenizer=None, *, formatter=None, identity=None, qwen_url=None, transport=http_json):
    arm, payload = request['arm'], request['payload']
    if arm in ('qwen', 'qwen_json'):
        require(qwen_url is not None and identity is not None, 'Qwen runtime and provenance required')
        rendered = transport(qwen_url + '/apply-template', payload, 10)
        prompt = rendered.get('prompt')
        require(type(prompt) is str, 'runtime template rendering failed')
        tokenized = transport(qwen_url + '/tokenize', {'content': prompt, 'add_special': True, 'parse_special': True}, 10)
        tokens = tokenized.get('tokens')
        require(isinstance(tokens, list) and all(type(t) is int for t in tokens), 'runtime tokenization failed')
        lengths = [len(tokens)]
        limit = 4096
        require(0 < lengths[0] + payload['max_tokens'] <= limit, 'Qwen full input plus output reserve exceeds context')
    elif arm == 'embeddings':
        require(tokenizer is not None and identity is not None, 'verified embedding tokenizer required')
        require(identity.get('runtime_tokenization_audit', {}).get('verbatim_with_special_tokens') is True,
                'semembed native preprocessing must be audited before token preflight')
        texts = payload['input']
        require(isinstance(texts, list) and texts and all(type(text) is str for text in texts), 'embedding input list required')
        tokenizer.no_truncation()
        tokenizer.no_padding()
        lengths = [len(tokenizer.encode(text, add_special_tokens=True).ids) for text in texts]
        limit = TOKEN_LIMIT
        require(all(0 < length <= limit for length in lengths), 'complete embedding input exceeds 512 tokens')
    else:
        require(tokenizer is not None and identity is not None, 'verified tokenizer identity required')
        _, lengths = encoder_inputs(payload, tokenizer, formatter=formatter)
        limit = TOKEN_LIMIT
    return {'arm': arm, 'payload_sha256': hashlib.sha256(encoded(payload)).hexdigest(),
            'tokens': max(lengths), 'max_pair_tokens': max(lengths), 'input_lengths': lengths,
            'limit': limit, 'truncated': False, 'verified': True, 'full_input': True,
            'identity': identity,
            'tokenizer_sha256': hashlib.sha256(encoded(identity)).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--arm', choices=['gliclass', 'deberta', 'qwen', 'embeddings'], required=True)
    parser.add_argument('--model-dir', type=Path)
    parser.add_argument('--qwen-url', help='Loopback llama.cpp base URL, without /v1/chat/completions')
    parser.add_argument('--environment', type=Path, help='Verified Qwen environment record')
    parser.add_argument('--runtime-audit', type=Path, help='Audited semembed tokenizer/preprocessing identity')
    parser.add_argument('--requests', type=Path, required=True, help='JSON array of request envelopes')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.arm == 'embeddings':
        require(args.model_dir and args.environment and args.runtime_audit, 'embedding preflight needs model directory, environment and runtime audit')
        from evidence import verify_environment
        from tokenizers import Tokenizer
        environment = json.loads(args.environment.read_text())
        verify_environment(environment, 'embeddings')
        audit = json.loads(args.runtime_audit.read_text())
        require(audit.get('runtime_sha256') == environment['runtime_sha256'] and audit.get('verbatim_with_special_tokens') is True and audit.get('max_length') == 512,
                'actual semembed tokenizer semantics have not been audited')
        for filename, expected in environment['artifacts'].items():
            path = (args.model_dir/filename).resolve()
            require(path.is_relative_to(args.model_dir.resolve()) and digest(path) == expected, 'embedding artifact missing or changed: '+filename)
        tokenizer = Tokenizer.from_file(str(args.model_dir/'tokenizer.json'))
        identity = {'revision': environment['model_revision'], 'artifacts': environment['artifacts'], 'runtime_tokenization_audit': audit}
        records = []
        for request in json.loads(args.requests.read_text()):
            require(request['arm'] == args.arm, 'mixed-arm preflight input')
            records.append(preflight_request(request, tokenizer, identity=identity))
        with args.output.open('x') as stream:
            json.dump(records, stream, indent=2, allow_nan=False)
            stream.write('\n')
        return
    if args.arm == 'qwen':
        require(args.qwen_url and args.environment, 'Qwen preflight requires URL and environment identity')
        from evidence import verify_environment
        environment = json.loads(args.environment.read_text())
        verify_environment(environment, 'qwen', cpu_probe=environment['architecture'] == 'linux/arm64')
        identity = {'revision': environment['model_revision'], 'artifacts': environment['artifacts'], 'runtime_revision': environment['runtime_revision']}
        records = []
        for request in json.loads(args.requests.read_text()):
            require(request['arm'] == args.arm, 'mixed-arm preflight input')
            records.append(preflight_request(request, identity=identity, qwen_url=args.qwen_url.rstrip('/')))
        with args.output.open('x') as stream:
            json.dump(records, stream, indent=2, allow_nan=False)
            stream.write('\n')
        return
    require(args.model_dir is not None, 'specialist model directory required')
    lock = verify_artifacts(args.model_dir, args.arm, weights=False)
    dependencies = json.loads((ROOT / 'locks/dependencies.json').read_text())['direct_versions']
    require(importlib.metadata.version('transformers') == dependencies['transformers'],
            'tokenizer dependency version differs from lock')
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(args.model_dir, local_files_only=True, trust_remote_code=False)
    config = json.loads((args.model_dir / 'config.json').read_text())
    formatter = native_formatter(config) if args.arm == 'gliclass' else None
    identity = {'revision': lock['revision'], 'artifacts': lock['files'],
                'transformers': importlib.metadata.version('transformers'),
                'pipeline_sha256': GLICLASS_PIPELINE_SHA if formatter else None}
    records = []
    for request in json.loads(args.requests.read_text()):
        require(request['arm'] == args.arm, 'mixed-arm preflight input')
        records.append(preflight_request(request, tokenizer, formatter=formatter, identity=identity))
    with args.output.open('x') as stream:
        json.dump(records, stream, indent=2, allow_nan=False)
        stream.write('\n')


if __name__ == '__main__':
    main()
