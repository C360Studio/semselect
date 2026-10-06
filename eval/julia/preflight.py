"""Fail closed before Julia inference if native prompt preprocessing drops text.

Only the pinned text-only Julia experiment is supported. This reconstructs token
budgets, not model scores. The caller supplies the loaded model's /tokenize API
with add_special=false and parse_special=false. Before inference, it must also
compare each returned token array to /tokenize(rendered_prompt) with
add_special=false and parse_special=true.

Runtime source (6c59c40076c00eab49754dc955d7652d93f9e125):
conversion/bert.py:694-725; tools/server/server-decision.cpp:164-202,502-556;
tools/server/server-context.cpp:3395-3415; src/llama-vocab.cpp:3295-3299.
Model values are checked against gguf-metadata.json extracted from the pinned
GGUF, rather than inferred from a model-card context-window claim.
"""

import hashlib
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
MODEL_SHA256 = '1ea6a7e87156eeeda88cb7a36a61265b37ba7b993897b7289b99aea5b5e47069'
MODEL_REVISION = '16fee17949206fbf58da9347daea44d792a81211'
RUNTIME_REVISION = '6c59c40076c00eab49754dc955d7652d93f9e125'
CONTEXT_TOKENS = 1024
MAX_OPTION_TEXT_TOKENS = 48
EXPECTED_TEMPLATE = (
    '<bos>{{ type }} question: {{ instructions if instructions is string else instructions | tojson }}'
    '<eos>{% for o in options %}<mask> {% if o.description %}'
    '{{ o.description if o.description is string else o.description | tojson }}'
    '{% else %}{{ o.key }}{% endif %}{% endfor %}'
    '<eos>{{ state if state is string else state | tojson }}<eos>'
)


def verify_model_metadata(metadata, lock):
    """Validate the saved actual-GGUF metadata and the experiment model pin."""
    if not isinstance(metadata, dict) or not isinstance(lock, dict):
        raise ValueError('Julia metadata and model lock must be objects')
    pins = {'sha256': MODEL_SHA256, 'revision': MODEL_REVISION,
            'runtime_revision': RUNTIME_REVISION}
    for key, expected in pins.items():
        if lock.get(key) != expected:
            raise ValueError(f'Julia model lock changed: {key}; review preflight')
    expected = {
        'general.architecture': 'modern-bert',
        'modern-bert.context_length': 8192,
        'modern-bert.decision.type': 'laya',
        'modern-bert.decision.block_count': 2,
        'modern-bert.decision.max_head_tokens': 256,
        'tokenizer.ggml.bos_token_id': 2,
        'tokenizer.ggml.eos_token_id': 1,
        'tokenizer.ggml.seperator_token_id': 1,
        'tokenizer.ggml.mask_token_id': 4,
        'tokenizer.chat_template.systemone': EXPECTED_TEMPLATE,
    }
    for key, value in expected.items():
        if metadata.get(key) != value:
            raise ValueError(f'Julia GGUF metadata changed: {key}; review preflight')
    provenance = metadata.get('_provenance', {})
    for key, value in {'model_sha256': MODEL_SHA256, 'model_revision': MODEL_REVISION,
                       'runtime_revision': RUNTIME_REVISION}.items():
        if provenance.get(key) != value:
            raise ValueError(f'Julia metadata provenance does not match model lock: {key}')
    special = metadata.get('special_tokens')
    types = metadata.get('special_token_types')
    if not isinstance(special, dict) or not isinstance(types, dict):
        raise ValueError('Julia metadata must include actual special token texts and types')
    for ident, value in {'1': '<eos>', '2': '<bos>', '4': '<mask>'}.items():
        if special.get(ident) != value or types.get(ident) != 3:
            raise ValueError(f'Julia control token changed: {ident}')
    if set(special) != set(types) or any(
            not isinstance(text, str) or not text for text in special.values()):
        raise ValueError('Julia special token metadata is incomplete')
    # GGUF UNKNOWN=2 and CONTROL=3 differ with parse_special=false. USER_DEFINED=4
    # still parses in either mode, including ordinary newlines and HTML tokens.
    controls = [special[ident] for ident, kind in types.items() if kind in (2, 3)]
    return {'head_tokens': expected['modern-bert.decision.max_head_tokens'],
            'control_tokens': controls,
            'template_sha256': hashlib.sha256(EXPECTED_TEMPLATE.encode()).hexdigest(),
            'model_sha256': MODEL_SHA256, 'runtime_revision': RUNTIME_REVISION}


def _load_profile():
    try:
        metadata = json.loads((HERE / 'gguf-metadata.json').read_text())
        lock = json.loads((HERE / 'models.lock.json').read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f'Cannot read Julia pinned metadata: {exc}') from exc
    return verify_model_metadata(metadata, lock)


def _text(value, where, controls):
    if not isinstance(value, str):
        raise ValueError(f'{where}: this experiment requires text')
    if any(marker in value for marker in controls):
        raise ValueError(f'{where}: literal control token would alter the native prompt')
    return value


def _options(question, where, controls):
    if not isinstance(question, dict) or set(question) - {'type', 'instructions', 'criteria'}:
        raise ValueError(f'{where}: unsupported question fields')
    kind = question.get('type')
    criteria = question.get('criteria')
    if kind == 'choice':
        if not isinstance(criteria, dict) or not 1 <= len(criteria) <= 255:
            raise ValueError(f'{where}: Choice needs 1 to 255 options')
        pairs = list(criteria.items())
    elif kind == 'score':
        if not isinstance(criteria, list) or not 2 <= len(criteria) <= 10:
            raise ValueError(f'{where}: Score needs 2 to 10 ordered levels')
        pairs = [(str(index), description) for index, description in enumerate(criteria)]
    elif kind == 'noul':
        if criteria is not None and (not isinstance(criteria, dict) or set(criteria) - {'false', 'true'}):
            raise ValueError(f'{where}: Noul criteria may contain only false and true')
        pairs = [(key, (criteria or {}).get(key)) for key in ('false', 'true')]
    else:
        raise ValueError(f'{where}: unsupported question type')
    options = []
    for key, description in pairs:
        _text(key, f'{where}.criteria key', controls)
        if description is not None:
            _text(description, f'{where}.criteria.{key}', controls)
        # Julia's actual GGUF template uses a truthy description instead of the
        # key, including for Score and Noul; it does not concatenate the two.
        options.append({'key': key, 'description': description,
                        'text': description if description else key})
    return kind, options


def check_request(payload, tokenize):
    """Return exact untruncated head tokens, or raise ValueError before inference.

    This only checks the fixed 1024-context, 1024-batch, 1024-ubatch experiment.
    It neither alters the supplied payload nor repairs oversized requests.
    """
    profile = _load_profile()
    controls = profile['control_tokens']
    if not isinstance(payload, dict) or set(payload) - {'model', 'state', 'questions'}:
        raise ValueError('Julia preflight supports text-only SystemOne request fields')
    state = _text(payload.get('state'), 'state', controls)
    questions = payload.get('questions')
    if not isinstance(questions, dict) or not questions:
        raise ValueError('questions must be a nonempty object')

    def tokens(text):
        value = tokenize(text)
        if not isinstance(value, list) or any(type(item) is not int or item < 0 for item in value):
            raise ValueError('tokenizer must return an array of nonnegative integer token IDs')
        if text and not value:
            raise ValueError('tokenizer returned no tokens for nonempty text')
        return value

    state_ids = tokens(state)
    result = {'policy': 'pinned-julia-native-budget-v1', 'fits': True,
              'context_tokens': CONTEXT_TOKENS, 'batch_tokens': CONTEXT_TOKENS,
              'ubatch_tokens': CONTEXT_TOKENS, 'inference_calls': 0,
              'template_sha256': profile['template_sha256'], 'model_sha256': MODEL_SHA256,
              'native_tokenization_parity_required': True, 'heads': {}}
    for ident, question in questions.items():
        _text(ident, 'question id', controls)
        kind, options = _options(question, ident, controls)
        instructions = _text(question.get('instructions'), f'{ident}.instructions', controls)
        question_text = f'{kind} question: {instructions}'
        question_ids = tokens(question_text)
        option_ids = []
        for option in options:
            ids = tokens(' ' + option['text'])
            if len(ids) > MAX_OPTION_TEXT_TOKENS:
                raise ValueError(f'{ident}.criteria.{option["key"]}: native option truncation '
                                 f'({len(ids)} text tokens > {MAX_OPTION_TEXT_TOKENS})')
            option_ids.append([4] + ids)
        total_options = sum(map(len, option_ids))
        head_limit = profile['head_tokens']
        if total_options + 16 > head_limit:
            cap = max(4, (head_limit - min(head_limit, 16)) // len(options))
            if any(len(ids) > cap for ids in option_ids):
                raise ValueError(f'{ident}: native option truncation to fit {head_limit}-token head')
        question_limit = max(8, head_limit - min(head_limit, total_options))
        if len(question_ids) > question_limit:
            raise ValueError(f'{ident}: native instruction truncation '
                             f'({len(question_ids)} tokens > {question_limit})')
        # Each text span ends at a native special token boundary. Structural
        # IDs come from verified metadata, never tokenizing '<bos>' as text.
        all_ids = [2] + question_ids + [1]
        for ids in option_ids:
            all_ids.extend(ids)
        all_ids.extend([1] + state_ids + [1])
        if len(all_ids) > CONTEXT_TOKENS:
            raise ValueError(f'{ident}: full prompt {len(all_ids)} exceeds '
                             f'{CONTEXT_TOKENS}-token context/physical batch')
        prompt = ('<bos>' + question_text + '<eos>'
                  + ''.join('<mask> ' + option['text'] for option in options)
                  + '<eos>' + state + '<eos>')
        result['heads'][ident] = {
            'type': kind, 'options': options, 'rendered_prompt': prompt,
            'tokens': all_ids, 'prompt_tokens': len(all_ids),
            'instruction_tokens': len(question_ids), 'instruction_limit': question_limit,
            'option_text_tokens': [len(ids) - 1 for ids in option_ids],
            'max_head_tokens': head_limit, 'truncated': False,
        }
    result['total_input_tokens'] = sum(head['prompt_tokens'] for head in result['heads'].values())
    return result
