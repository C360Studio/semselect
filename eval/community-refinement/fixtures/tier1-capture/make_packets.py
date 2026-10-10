#!/usr/bin/env python3
"""Step 3 of the community-refinement protocol: the bounded reviewer packets.

One packet per frozen step-2 pair, serialized deterministically from the
hydration capture (entity states verbatim plus the bodies the system embedded),
with an 8,192-byte state, the service's question limits, full source references
and omitted-content metadata. The per-entity bundle variant puts one entity's
passage in the state and each selected neighbour in its own question, at most
four questions per request, the split recorded.

Offline: no model is called here. Token verification against both pinned
runtimes is verify_packets.py.

Legacy SemStreams capture; not a SemEngine result.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sys

PROVENANCE = 'legacy SemStreams capture; not a SemEngine result'
STEP = 'step 3: bounded reviewer packets (eval/community-refinement/README.md)'
STATE_BUDGET = 8192          # guard: state must be 1..8192 UTF-8 bytes
INSTRUCTION_LIMIT = 1024     # guard: instructions 1..1024 bytes
DESCRIPTION_LIMIT = 512      # guard: choice descriptions at most 512 bytes
QUESTIONS_PER_REQUEST = 4    # guard: 1..4 questions per request
NEIGHBOUR_CAP = 6            # explicit links listed per direction per entity
CHILDREN_CAP = 8             # contained children listed for a container entity

OBJECTIVE = ('Community objective: put evidence about a shared responsibility together '
             'without letting boilerplate, overloaded terms or generic utility nodes join '
             'unrelated subjects.')
INSTRUCTIONS = ('Should this pair retain its semantic co-location hint for the stated community '
                'objective? Decide from the evidence in the state only. keep: the two passages '
                'address one shared subject or responsibility at this granularity. suppress: the '
                'match is incidental (boilerplate, an overloaded term, a generic utility) and would '
                'mislead the grouping. defer: the visible evidence is insufficient or conflicting.')
CRITERIA = {
    'keep': 'Evidence supports shared subject/responsibility at the chosen granularity',
    'suppress': 'Evidence shows an incidental match that would mislead this grouping',
    'defer': 'Available evidence is insufficient or conflicting',
}

# Predicates the cards read (SemSource source/ast/predicates.go and
# source/vocabulary/predicates.go at the pinned commit).
P = {
    'path': ('code.artifact.path', 'source.doc.file-path'),
    'hash': ('code.artifact.hash', 'source.doc.file-hash'),
    'title': ('dc.terms.title',),
    'language': ('code.artifact.language',),
    'package': ('code.artifact.package',),
    'visibility': ('code.artifact.visibility',),
    'signature': ('code.doc.signature',),
    'comment': ('code.doc.comment',),
    'start': ('code.metric.start-line',),
    'end': ('code.metric.end-line',),
    'section': ('source.doc.section',),
    'chunk_index': ('source.doc.chunk-index',),
    'chunk_count': ('source.doc.chunk-count',),
    'doc_type': ('source.doc.type',),
}
CONTAINS = 'code.structure.contains'
BELONGS = 'code.structure.belongs'


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def read_jsonl(path):
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path, 'rt', encoding='utf-8') as f:
        return [json.loads(line) for line in f if line.strip()]


def load_json(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


class Capture:
    """One hydration capture directory, read once."""

    def __init__(self, evidence):
        self.dir = Path(evidence)
        self.summary = load_json(self.dir / 'summary.json')
        self.family_json = load_json(self.dir / 'family.json')
        self.hydration = load_json(self.dir / 'hydration' / 'hydration.json')
        self.entities = {r['entity_id']: r for r in read_jsonl(self.dir / 'structural' / 'entities.jsonl')}
        self.states = {r['entity_id']: r for r in read_jsonl(self.dir / 'hydration' / 'entity_states.jsonl')}
        self.bodies = {r['key']: r for r in read_jsonl(self.dir / 'hydration' / 'bodies.jsonl')}
        self.explicit = read_jsonl(self.dir / 'structural' / 'explicit_edges.jsonl')
        directed = self.dir / 'mutualknn' / 'directed.jsonl'
        if not directed.exists():
            directed = directed.with_suffix('.jsonl.gz')
        self.neighbours = {r['entity_id']: [s['entity_id'] for s in r.get('similar') or []]
                           for r in read_jsonl(directed)}
        self.hashes = {
            'entities_jsonl_sha256': sha256_file(self.dir / 'structural' / 'entities.jsonl'),
            'mutual_pairs_jsonl_sha256': sha256_file(self.dir / 'mutualknn' / 'mutual_pairs.jsonl'),
            'partition_hash': self.summary['partition_hash'],
        }
        self.out_by = {}
        self.in_by = {}
        for e in self.explicit:
            self.out_by.setdefault(e['from'], []).append(e)
            self.in_by.setdefault(e['to'], []).append(e)

    def check_against(self, selection):
        """The hydration capture must reproduce the frozen step-1 inputs exactly."""
        errors = []
        if selection['family'] != self.summary['family']:
            errors.append(f"family {self.summary['family']} is not the selection's {selection['family']}")
        if selection.get('split') != self.summary.get('split'):
            errors.append('split differs from the selection')
        if selection['inputs']['identity_profile'] != self.summary['identity_profile']:
            errors.append(f"identity profile {self.summary['identity_profile']} is not the selection's "
                          f"{selection['inputs']['identity_profile']}")
        for key, value in self.hashes.items():
            if selection['inputs'].get(key) != value:
                errors.append(f'{key} differs from the frozen selection')
        for name, ok in self.hydration['checks'].items():
            if not ok:
                errors.append(f'hydration check {name} failed')
        missing = [e for r in selection['selected'] for e in (r['a'], r['b']) if e not in self.states]
        if missing:
            errors.append(f'{len(missing)} selected endpoints have no entity state')
        return errors


def triples(state_row):
    values = {}
    for t in state_row.get('state', {}).get('triples') or []:
        values.setdefault(t['predicate'], []).append(t['object'])
    return values


def first(values, keys):
    for k in keys:
        for v in values.get(k, []):
            if v not in (None, ''):
                return v
    return None


def as_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def short(entity_id):
    parts = entity_id.split('.')
    return f'{parts[4]}:{parts[5]}' if len(parts) >= 6 else entity_id


def predicate_name(p):
    return p.rsplit('.', 1)[-1]


def card(capture, entity_id):
    """Everything a packet may say about one entity, before bounding."""
    ent = capture.entities[entity_id]
    values = triples(capture.states[entity_id])
    body_key = capture.states[entity_id].get('body_key')
    body = capture.bodies.get(body_key) if body_key else None
    text = body.get('text') if body else None
    start, end = as_int(first(values, P['start'])), as_int(first(values, P['end']))
    out = sorted(capture.out_by.get(entity_id, []), key=lambda e: (e['predicate'], e['to']))
    inc = sorted(capture.in_by.get(entity_id, []), key=lambda e: (e['predicate'], e['from']))
    children = [e['to'] for e in out if e['predicate'] == CONTAINS]
    revision, revision_of = first(values, P['hash']), 'entity'
    if revision is None:
        # A passage carries no hash of its own; its document's file hash is the
        # revision of the bytes it was split from.
        for parent in (e['to'] for e in out if e['predicate'] == BELONGS):
            if parent in capture.states:
                revision = first(triples(capture.states[parent]), P['hash'])
                if revision is not None:
                    revision_of = 'parent'
                    break
    return {
        'entity_id': entity_id, 'type': ent['type'], 'domain': ent['domain'],
        'path': first(values, P['path']), 'title': first(values, P['title']),
        'language': first(values, P['language']), 'package': first(values, P['package']),
        'visibility': first(values, P['visibility']), 'signature': first(values, P['signature']),
        'comment': first(values, P['comment']), 'start_line': start, 'end_line': end,
        'section': first(values, P['section']), 'chunk_index': as_int(first(values, P['chunk_index'])),
        'chunk_count': as_int(first(values, P['chunk_count'])), 'doc_type': first(values, P['doc_type']),
        'revision': revision, 'revision_of': revision_of,
        'body_key': body_key, 'body_sha256': body.get('sha256') if body else None,
        'body_bytes': body.get('bytes') if body else None, 'body_valid_utf8': body.get('valid_utf8') if body else None,
        'text': text, 'out': out, 'in': inc, 'children': children,
    }


def bound(text, budget):
    """-> (kept text, omitted bytes, kept lines). Cuts on a line end when one is
    in the second half of the budget, else on a UTF-8 boundary."""
    data = text.encode('utf-8')
    if len(data) <= budget:
        return text, 0, text.count('\n') + (0 if text.endswith('\n') or not text else 1)
    if budget <= 0:
        return '', len(data), 0
    cut = data.rfind(b'\n', 0, budget + 1)
    if cut < budget // 2:
        cut = budget
        while cut > 0 and (data[cut] & 0xC0) == 0x80:
            cut -= 1
    kept = data[:cut].decode('utf-8')
    return kept, len(data) - cut, kept.count('\n') + (0 if kept.endswith('\n') or not kept else 1)


def link_lines(c, cap=NEIGHBOUR_CAP):
    lines = []
    for label, edges, other in (('links out', c['out'], 'to'), ('links in', c['in'], 'from')):
        if not edges:
            continue
        if other == 'to':
            shown = [f"{predicate_name(e['predicate'])} {short(e['to'])}" for e in edges[:cap]]
        else:
            shown = [f"{short(e['from'])} {predicate_name(e['predicate'])}" for e in edges[:cap]]
        more = f' (+{len(edges) - cap} more)' if len(edges) > cap else ''
        lines.append(f"  {label} ({len(edges)}): " + '; '.join(shown) + more)
    return lines


def head_line(tag, c):
    where = c['path'] or '(no path)'
    if c['start_line'] and c['end_line']:
        where += f":{c['start_line']}-{c['end_line']}"
    name = c['title'] or short(c['entity_id'])
    bits = [c['type'], repr(name) if c['type'] in ('chunk', 'doc') else name, c['domain'], where]
    for k in ('language', 'package', 'visibility'):
        if c[k]:
            bits.append(f'{k} {c[k]}')
    if c['chunk_index'] is not None:
        bits.append(f"passage {c['chunk_index'] + 1}" + (f" of {c['chunk_count']}" if c['chunk_count'] else ''))
    if c['section']:
        bits.append(f"section {c['section']}")
    if c['revision']:
        bits.append(f"{'file' if c['revision_of'] == 'parent' else 'content'} hash {str(c['revision'])[:12]}")
    return f'[{tag}] ' + ' | '.join(str(b) for b in bits)


def meta_lines(c):
    lines = [head_line('%TAG%', c)]
    if c['signature']:
        lines.append(f"  signature: {c['signature']}")
    if c['comment']:
        comment, omitted, _ = bound(str(c['comment']).strip(), DESCRIPTION_LIMIT)
        lines.append('  doc: ' + comment.replace('\n', ' ') + (f' [+{omitted} bytes]' if omitted else ''))
    lines.extend(link_lines(c))
    if c['children'] and c['text'] is None:
        shown = ', '.join(short(x) for x in c['children'][:CHILDREN_CAP])
        more = f' (+{len(c["children"]) - CHILDREN_CAP} more)' if len(c['children']) > CHILDREN_CAP else ''
        lines.append(f"  contains ({len(c['children'])}): {shown}{more}")
    return lines


def source_block(c, allowance):
    """-> (lines, included bytes, omitted bytes, truncated)."""
    if c['text'] is None:
        reason = 'body stored as non-UTF-8 bytes' if c['body_valid_utf8'] is False else 'no verbatim body for this entity'
        return [f'  source: none ({reason})'], 0, 0, False
    kept, omitted, kept_lines = bound(c['text'], allowance)
    total = c['body_bytes'] if c['body_bytes'] is not None else len(c['text'].encode())
    span = ''
    if c['start_line'] and c['end_line']:
        last = c['start_line'] + max(kept_lines, 1) - 1
        span = f" lines {c['start_line']}-{last if omitted else c['end_line']}"
    note = f'{total - omitted} of {total} bytes shown, {omitted} omitted after line {kept_lines}' if omitted else f'{total} bytes, complete'
    return [f'  source{span} ({note}):', kept.rstrip('\n')], total - omitted, omitted, omitted > 0


def render(header, tags, cards, allowances):
    blocks, sources = [], []
    for tag, c, allowance in zip(tags, cards, allowances):
        lines = [l.replace('%TAG%', tag) for l in meta_lines(c)]
        src, included, omitted, truncated = source_block(c, allowance)
        blocks.append('\n'.join(lines + src))
        sources.append({'entity_id': c['entity_id'], 'type': c['type'], 'path': c['path'],
                        'start_line': c['start_line'], 'end_line': c['end_line'], 'revision': c['revision'],
                        'revision_of': c['revision_of'] if c['revision'] else None,
                        'body_key': c['body_key'], 'body_sha256': c['body_sha256'], 'body_bytes': c['body_bytes'],
                        'included_bytes': included, 'omitted_bytes': omitted, 'truncated': truncated,
                        'links_out': len(c['out']), 'links_in': len(c['in']),
                        'links_listed_out': min(len(c['out']), NEIGHBOUR_CAP), 'links_listed_in': min(len(c['in']), NEIGHBOUR_CAP)})
    return '\n'.join(header + [''] + blocks), sources


def fit(header, tags, cards, budget=STATE_BUDGET):
    """Split the remaining budget between the bodies, longest-first leftovers,
    and shrink until the encoded state fits. Deterministic."""
    wants = [len(c['text'].encode()) if c['text'] is not None else 0 for c in cards]
    probe, _ = render(header, tags, cards, [0] * len(cards))
    remaining = budget - len(probe.encode()) - 64 * len(cards)   # slack for the source note digits
    for _ in range(12):
        if remaining < 0:
            raise ValueError('packet metadata alone exceeds the state budget')
        allowances = allocate(wants, remaining)
        state, sources = render(header, tags, cards, allowances)
        over = len(state.encode()) - budget
        if over <= 0:
            return state, sources
        remaining -= over
    raise ValueError('could not fit the packet into the state budget')


def allocate(wants, remaining):
    n = len(wants)
    if sum(wants) <= remaining:
        return list(wants)
    share = remaining // n
    alloc = [min(w, share) for w in wants]
    left = remaining - sum(alloc)
    for i in sorted(range(n), key=lambda i: (-wants[i], i)):
        extra = min(left, wants[i] - alloc[i])
        alloc[i] += extra
        left -= extra
    return alloc


def rank_of(capture, a, b):
    lst = capture.neighbours.get(a) or []
    return (lst.index(b) + 1, len(lst)) if b in lst else (None, len(lst))


def explicit_between(capture, a, b):
    hits = [e for e in capture.out_by.get(a, []) if e['to'] == b] + [e for e in capture.out_by.get(b, []) if e['to'] == a]
    return sorted({predicate_name(e['predicate']) for e in hits})


def corpus_line(capture):
    fam = capture.summary
    return f"Corpus: {capture.family_json['family']['repo']} at commit {fam['commit'][:12]} (family {fam['family']})."


def pair_header(capture, row):
    ra, na = rank_of(capture, row['a'], row['b'])
    rb, nb = rank_of(capture, row['b'], row['a'])
    link = explicit_between(capture, row['a'], row['b'])
    return [
        OBJECTIVE,
        f"Candidate pair: semantic co-location hint, embedding similarity {row['similarity']:.3f} "
        f"(A lists B at neighbour rank {ra or '-'} of {na}; B lists A at rank {rb or '-'} of {nb}). "
        f"Explicit link between A and B: {', '.join(link) if link else 'none'}. "
        f"Same explicit-structure group: {'no' if row['crosses_partition'] else 'yes'}.",
        corpus_line(capture),
    ], {'rank_a_to_b': ra, 'rank_b_to_a': rb, 'explicit_link': link}


def question_key(prefix, row_hash):
    return f'{prefix}-{row_hash[:12]}'


def pair_packet(capture, row, budget=STATE_BUDGET):
    header, context = pair_header(capture, row)
    cards = [card(capture, row['a']), card(capture, row['b'])]
    state, sources = fit(header, ['A', 'B'], cards, budget)
    request = {'state': state, 'questions': {question_key('pair', row['hash']): {
        'type': 'choice', 'instructions': INSTRUCTIONS, 'criteria': dict(CRITERIA)}}}
    check_request(request)
    return {'rank': row['rank'], 'pair_hash': row['hash'], 'a': row['a'], 'b': row['b'],
            'similarity': row['similarity'], 'crosses_partition': row['crosses_partition'],
            'context': context, 'request': request, 'state_bytes': len(state.encode()),
            'state_sha256': hashlib.sha256(state.encode()).hexdigest(), 'sources': sources}


def check_request(request):
    n = len(request['state'].encode())
    if not 1 <= n <= STATE_BUDGET:
        raise ValueError(f'state is {n} bytes')
    if not 1 <= len(request['questions']) <= QUESTIONS_PER_REQUEST:
        raise ValueError('questions must contain 1..4 entries')
    for key, q in request['questions'].items():
        if not 1 <= len(key.encode()) <= 128 or not 1 <= len(q['instructions'].encode()) <= INSTRUCTION_LIMIT:
            raise ValueError(f'question {key} breaks the guard limits')
        if any(len(d.encode()) > DESCRIPTION_LIMIT for d in q['criteria'].values()):
            raise ValueError('criteria description over 512 bytes')


def excerpt(c, limit):
    """A neighbour's bounded excerpt for a bundle question, single line."""
    if c['text'] is None:
        return '(no verbatim body)', 0
    kept, omitted, _ = bound(c['text'].strip(), limit)
    return ' '.join(kept.split()), omitted


def bundle_packets(capture, rows, budget=STATE_BUDGET):
    """One request per (entity, up to four selected neighbours)."""
    by_entity = {}
    for r in rows:
        by_entity.setdefault(r['a'], []).append((r['b'], r))
        by_entity.setdefault(r['b'], []).append((r['a'], r))
    packets = []
    for entity_id in sorted(by_entity):
        neighbours = sorted(by_entity[entity_id], key=lambda x: (x[1]['rank'], x[0]))
        header = [OBJECTIVE, corpus_line(capture),
                  f'Entity A below has {len(neighbours)} candidate semantic neighbours, one per question.']
        c = card(capture, entity_id)
        state, sources = fit(header, ['A'], [c], budget)
        chunks = [neighbours[i:i + QUESTIONS_PER_REQUEST] for i in range(0, len(neighbours), QUESTIONS_PER_REQUEST)]
        for part, chunk in enumerate(chunks, 1):
            questions = {}
            qmeta = []
            for other, row in chunk:
                oc = card(capture, other)
                head = head_line('B', oc)
                fixed = (f"Candidate neighbour {head} (similarity {row['similarity']:.3f}, "
                         f"{'different' if row['crosses_partition'] else 'same'} explicit-structure group). "
                         'Should A and this neighbour retain their semantic co-location hint for the stated '
                         'community objective? keep / suppress / defer as defined. Excerpt: ')
                room = min(DESCRIPTION_LIMIT, INSTRUCTION_LIMIT - len(fixed.encode()))
                if room < 64:
                    head, _, _ = bound(head, 200)
                    fixed = (f"Candidate neighbour {head} (similarity {row['similarity']:.3f}). Should A and this "
                             'neighbour retain their semantic co-location hint for the stated community objective? Excerpt: ')
                    room = min(DESCRIPTION_LIMIT, INSTRUCTION_LIMIT - len(fixed.encode()))
                text, omitted = excerpt(oc, room)
                questions[question_key('nb', row['hash'])] = {'type': 'choice', 'instructions': fixed + text,
                                                             'criteria': dict(CRITERIA)}
                qmeta.append({'pair_hash': row['hash'], 'neighbour': other, 'rank': row['rank'],
                              'excerpt_bytes': len(text.encode()), 'excerpt_omitted_bytes': omitted,
                              'neighbour_body_bytes': oc['body_bytes']})
            request = {'state': state, 'questions': questions}
            check_request(request)
            packets.append({'entity_id': entity_id, 'part': part, 'parts': len(chunks), 'neighbours': len(neighbours),
                            'questions': qmeta, 'request': request, 'state_bytes': len(state.encode()),
                            'state_sha256': hashlib.sha256(state.encode()).hexdigest(), 'sources': sources})
    return packets


def write_jsonl(path, rows):
    with open(path, 'w', encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + '\n')


def stats(values):
    values = sorted(values)
    return {'min': values[0], 'median': values[len(values) // 2], 'max': values[-1]} if values else None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--selection', required=True, help='frozen step-2 selection JSON')
    ap.add_argument('--evidence', required=True, help='hydration capture directory (run-family.sh output)')
    ap.add_argument('--out', required=True, help='output directory (created; must not exist)')
    ap.add_argument('--state-budget', type=int, default=STATE_BUDGET)
    args = ap.parse_args(argv)
    if not 1 <= args.state_budget <= STATE_BUDGET:
        ap.error(f'--state-budget must be 1..{STATE_BUDGET}')
    out = Path(args.out)
    if out.exists():
        ap.error(f'{out} already exists; refusing to overwrite frozen packets')
    selection = load_json(args.selection)
    capture = Capture(args.evidence)
    errors = capture.check_against(selection)
    if errors:
        for e in errors:
            print('refused:', e, file=sys.stderr)
        return 2
    rows = selection['selected']
    pairs = [pair_packet(capture, r, args.state_budget) for r in rows]
    bundles = bundle_packets(capture, rows, args.state_budget)
    out.mkdir(parents=True)
    write_jsonl(out / 'pairs.jsonl', pairs)
    write_jsonl(out / 'bundles.jsonl', bundles)
    truncated = sum(1 for p in pairs for s in p['sources'] if s['truncated'])
    index = {
        'provenance': PROVENANCE, 'step': STEP, 'family': selection['family'], 'split': selection['split'],
        'selection': {'path': str(args.selection), 'sha256': sha256_file(args.selection), 'selected': len(rows)},
        'evidence': {'path': str(capture.dir), 'summary_evidence': capture.summary.get('evidence'), **capture.hashes,
                     'hydration': {k: capture.hydration[k] for k in ('entities', 'with_body_handle', 'bodies_fetched', 'checks')}},
        'parameters': {'state_budget': args.state_budget, 'instruction_limit': INSTRUCTION_LIMIT,
                       'description_limit': DESCRIPTION_LIMIT, 'questions_per_request': QUESTIONS_PER_REQUEST,
                       'neighbour_cap': NEIGHBOUR_CAP, 'children_cap': CHILDREN_CAP},
        'prompt': {'objective': OBJECTIVE, 'instructions': INSTRUCTIONS, 'criteria': CRITERIA},
        'pairs': {'packets': len(pairs), 'state_bytes': stats([p['state_bytes'] for p in pairs]),
                  'sources_truncated': truncated, 'sources_without_body': sum(1 for p in pairs for s in p['sources'] if s['body_key'] is None),
                  'sha256': sha256_file(out / 'pairs.jsonl')},
        'bundles': {'requests': len(bundles), 'entities': len({b['entity_id'] for b in bundles}),
                    'split_entities': len({b['entity_id'] for b in bundles if b['parts'] > 1}),
                    'questions': sum(len(b['questions']) for b in bundles),
                    'state_bytes': stats([b['state_bytes'] for b in bundles]),
                    'excerpts_truncated': sum(1 for b in bundles for q in b['questions'] if q['excerpt_omitted_bytes']),
                    'sha256': sha256_file(out / 'bundles.jsonl')},
    }
    with open(out / 'index.json', 'w', encoding='utf-8') as f:
        json.dump(index, f, indent=2, ensure_ascii=False)
        f.write('\n')
    print(f"{selection['family']}: {len(pairs)} pair packets, state bytes {index['pairs']['state_bytes']}, "
          f"{truncated} truncated sources; {len(bundles)} bundle requests over {index['bundles']['entities']} entities")
    return 0


if __name__ == '__main__':
    sys.exit(main())
