#!/usr/bin/env python3
"""Validate and index the step-3 label sheets (labels/<family>.json by
annotator A, labels/<family>.review.json by annotator B).

validate <family>   every packet labelled exactly once, labels in the set,
                    evidence quotes found verbatim in the packet state
index               per-family label counts, A/B agreement and confusion,
                    final labels (A where A and B agree, defer otherwise),
                    written to labels/<family>.final.json and labels/index.json

Legacy SemStreams capture; not a SemEngine result.
"""
import argparse
import collections
import hashlib
import json
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
PACKETS = HERE / 'packets'
LABELS = HERE / 'labels'
LABEL_SET = ('keep', 'suppress', 'defer')
MAX_QUOTE = 300
MAX_RATIONALE = 600
PROVENANCE = 'legacy SemStreams capture; not a SemEngine result'


def load_json(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def read_jsonl(path):
    with open(path, encoding='utf-8') as f:
        return [json.loads(line) for line in f if line.strip()]


def squash(text):
    return re.sub(r'\s+', ' ', text).strip()


def packets_for(family, packets_dir=PACKETS):
    index = load_json(packets_dir / family / 'index.json')
    pairs = read_jsonl(packets_dir / family / 'pairs.jsonl')
    return index, {p['pair_hash']: p for p in pairs}


def validate_sheet(sheet, family, index, pairs):
    """-> list of problems (empty when the sheet is valid)."""
    problems = []
    if sheet.get('family') != family:
        problems.append(f"family is {sheet.get('family')!r}, expected {family!r}")
    if sheet.get('split') != index['split']:
        problems.append(f"split is {sheet.get('split')!r}, expected {index['split']!r}")
    if sheet.get('packets_sha256') != index['pairs']['sha256']:
        problems.append('packets_sha256 does not match packets/<family>/index.json pairs.sha256')
    ann = sheet.get('annotator') or {}
    if ann.get('role') not in ('A', 'B') or not ann.get('model') or ann.get('method') != 'packet-only':
        problems.append('annotator must give role A or B, a model and method packet-only')
    rows = sheet.get('labels')
    if not isinstance(rows, list):
        return problems + ['labels must be a list']
    seen = collections.Counter(r.get('pair_hash') for r in rows)
    for h, n in seen.items():
        if n > 1:
            problems.append(f'pair {h[:12]} labelled {n} times')
    for h in pairs:
        if h not in seen:
            problems.append(f'pair {h[:12]} (rank {pairs[h]["rank"]}) is not labelled')
    for r in rows:
        h = r.get('pair_hash')
        tag = f'pair {str(h)[:12]}'
        if h not in pairs:
            problems.append(f'{tag} is not in the packets')
            continue
        if r.get('rank') != pairs[h]['rank']:
            problems.append(f'{tag}: rank {r.get("rank")} should be {pairs[h]["rank"]}')
        if r.get('label') not in LABEL_SET:
            problems.append(f'{tag}: label {r.get("label")!r} not in {LABEL_SET}')
        quotes = r.get('evidence')
        if not isinstance(quotes, list) or not 1 <= len(quotes) <= 3:
            problems.append(f'{tag}: evidence must hold one to three quotes')
            quotes = []
        state = squash(pairs[h]['request']['state'])
        for q in quotes:
            if not isinstance(q, str) or not q.strip():
                problems.append(f'{tag}: empty evidence quote')
            elif len(q) > MAX_QUOTE:
                problems.append(f'{tag}: evidence quote over {MAX_QUOTE} characters')
            elif squash(q) not in state:
                problems.append(f'{tag}: evidence quote not found verbatim in the packet: {q[:60]!r}')
        rationale = r.get('rationale')
        if not isinstance(rationale, str) or not rationale.strip():
            problems.append(f'{tag}: rationale missing')
        elif len(rationale) > MAX_RATIONALE:
            problems.append(f'{tag}: rationale over {MAX_RATIONALE} characters')
    order = [r.get('rank') for r in rows if r.get('pair_hash') in pairs and isinstance(r.get('rank'), int)]
    if order != sorted(order):
        problems.append('labels are not in rank order')
    return problems


def validate(family, packets_dir=PACKETS, labels_dir=LABELS):
    index, pairs = packets_for(family, packets_dir)
    results = {}
    for role, name in (('A', f'{family}.json'), ('B', f'{family}.review.json')):
        path = labels_dir / name
        if not path.exists():
            results[role] = None
            continue
        problems = validate_sheet(load_json(path), family, index, pairs)
        results[role] = problems
    return results


def final_labels(a_rows, b_rows):
    """A's label where A and B agree, defer otherwise."""
    b = {r['pair_hash']: r for r in b_rows}
    out = []
    for r in a_rows:
        other = b[r['pair_hash']]
        agreed = r['label'] == other['label']
        out.append({'pair_hash': r['pair_hash'], 'rank': r['rank'], 'a': r['label'], 'b': other['label'],
                    'final': r['label'] if agreed else 'defer', 'agreed': agreed})
    return out


def build_index(packets_dir=PACKETS, labels_dir=LABELS):
    families = []
    for d in sorted(p for p in packets_dir.iterdir() if p.is_dir()):
        family = d.name
        index, pairs = packets_for(family, packets_dir)
        checks = validate(family, packets_dir, labels_dir)
        if checks['A'] is None:
            families.append({'family': family, 'split': index['split'], 'packets': len(pairs), 'annotator_a': 'missing'})
            continue
        if checks['A'] or checks['B']:
            raise SystemExit(f'{family}: sheets do not validate; run validate first')
        a = load_json(labels_dir / f'{family}.json')
        row = {'family': family, 'split': index['split'], 'packets': len(pairs),
               'annotator_a': a['annotator'], 'a_counts': dict(collections.Counter(r['label'] for r in a['labels']))}
        if checks['B'] is None:
            row.update(annotator_b='missing', review='awaiting annotator B')
        else:
            b = load_json(labels_dir / f'{family}.review.json')
            final = final_labels(a['labels'], b['labels'])
            confusion = collections.Counter((f['a'], f['b']) for f in final)
            row.update(annotator_b=b['annotator'], b_counts=dict(collections.Counter(r['label'] for r in b['labels'])),
                       agreed=sum(1 for f in final if f['agreed']), disagreed=sum(1 for f in final if not f['agreed']),
                       b_changed_after_reading_a=sum(1 for r in b['labels'] if r.get('review_note')),
                       confusion={f'{x}/{y}': n for (x, y), n in sorted(confusion.items())},
                       final_counts=dict(collections.Counter(f['final'] for f in final)))
            with open(labels_dir / f'{family}.final.json', 'w', encoding='utf-8') as f:
                json.dump({'provenance': PROVENANCE, 'family': family, 'split': index['split'],
                           'rule': 'A where A and B agree, defer otherwise (ruling 7, 2026-10-10)',
                           'packets_sha256': index['pairs']['sha256'], 'labels': final}, f, indent=2)
                f.write('\n')
        families.append(row)
    totals = collections.Counter()
    for row in families:
        for k, v in (row.get('final_counts') or row.get('a_counts') or {}).items():
            totals[k] += v
    out = {'provenance': PROVENANCE, 'step': 'step 3 labels',
           'rule': 'A where A and B agree, defer otherwise; held-out labels never tune anything',
           'families': families, 'reviewed_families': sum(1 for r in families if 'final_counts' in r),
           'totals': dict(totals)}
    with open(labels_dir / 'index.json', 'w', encoding='utf-8') as f:
        json.dump(out, f, indent=2)
        f.write('\n')
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='command', required=True)
    v = sub.add_parser('validate')
    v.add_argument('family')
    sub.add_parser('index')
    ap.add_argument('--packets-dir', default=str(PACKETS))
    ap.add_argument('--labels-dir', default=str(LABELS))
    args = ap.parse_args(argv)
    packets_dir, labels_dir = Path(args.packets_dir), Path(args.labels_dir)
    if args.command == 'validate':
        results = validate(args.family, packets_dir, labels_dir)
        bad = 0
        for role, problems in results.items():
            if problems is None:
                print(f'{args.family} {role}: no sheet')
            elif problems:
                bad += len(problems)
                print(f'{args.family} {role}: {len(problems)} problems')
                for p in problems[:40]:
                    print('  ' + p)
            else:
                print(f'{args.family} {role}: valid')
        return 1 if bad or results['A'] is None else 0
    out = build_index(packets_dir, labels_dir)
    for row in out['families']:
        print(row['family'], row.get('final_counts') or row.get('a_counts') or row.get('annotator_a'),
              f"agreed {row['agreed']}/{row['packets']}" if 'agreed' in row else row.get('review', ''))
    print('totals', out['totals'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
