#!/usr/bin/env python3
"""Step 4 of the community-refinement protocol: co-membership constraints.

catalogue <family>|all  write constraints/<family>.catalogue.jsonl from the
                        family's hydration capture: one line per entity with
                        ID, type, path, lines, title, section, signature; no
                        community, no neighbour, no label
passages <family>|all   write constraints/<family>.passages.jsonl: each
                        document passage entity with its verbatim body, so a
                        reviewer can read a passage without inferring which
                        README lines a chunk index covers
validate <family>       check annotator A's constraints/<family>.json (and B's
                        <family>.review.json when present) against PROMPT.md
index                   per-family counts, the agreed (final) constraints, and
                        after-the-fact flags computed from the frozen captures
                        (in the step-3 review set, a mutual-kNN candidate,
                        joined by an explicit edge, same file), written to
                        constraints/<family>.final.json and constraints/index.json

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
ROOT = HERE.parents[3]
SELECTIONS = HERE / 'selections'
CONSTRAINTS = HERE / 'constraints'
EVIDENCE = ROOT / 'docs' / 'evidence'
WORKSPACES = Path('/tmp/semselect-families')
PROVENANCE = 'legacy SemStreams capture; not a SemEngine result'
STEP = 'step 4 co-membership constraints (eval/community-refinement/README.md)'
POLARITIES = ('positive', 'negative')
PER_POLARITY = 10
MIN_CROSS_FILE_POSITIVES = 6
MAX_RATIONALE = 600
CONTAINMENT = {'code.structure.contains', 'code.structure.belongs'}
EVIDENCE_RE = re.compile(r'^(?P<path>[^:]+?)(?::(?P<start>\d+)(?:-(?P<end>\d+))?)?$')
PREDICATES = {
    'path': ('code.artifact.path', 'source.doc.file-path'),
    'title': ('dc.terms.title',),
    'section': ('source.doc.section',),
    'signature': ('code.doc.signature',),
    'start_line': ('code.metric.start-line',),
    'end_line': ('code.metric.end-line',),
    'chunk_index': ('source.doc.chunk-index',),
}


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def load_json(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def read_jsonl(path):
    with open(path, encoding='utf-8') as f:
        return [json.loads(line) for line in f if line.strip()]


def write_json(path, value):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(value, f, indent=2, ensure_ascii=False)
        f.write('\n')


def hydration_dir(family, evidence_root=EVIDENCE):
    dirs = sorted(d for d in Path(evidence_root).glob(f'*-legacy-tier1-capture-{family}-explicit-only')
                  if (d / 'hydration' / 'hydration.json').exists())
    if not dirs:
        raise SystemExit(f'{family}: no hydration capture under {evidence_root}')
    return dirs[-1]


def pair_key(a, b):
    return tuple(sorted((a, b)))


class Family:
    """Everything the validator and index need for one family, read once.
    Nothing here is shown to annotator A except the catalogue."""

    def __init__(self, family, selections_dir=SELECTIONS, constraints_dir=CONSTRAINTS,
                 evidence_root=EVIDENCE, workspaces=WORKSPACES):
        self.family = family
        self.selection = load_json(Path(selections_dir) / f'{family}.json')
        self.split = self.selection['split']
        self.frozen = Path(evidence_root) / self.selection['evidence']
        self.hydration = hydration_dir(family, evidence_root)
        self.family_json = load_json(self.hydration / 'family.json')
        self.files = [e if isinstance(e, str) else e['path'] for e in self.family_json['family']['files']]
        self.workspace = Path(workspaces) / family
        self.catalogue_path = Path(constraints_dir) / f'{family}.catalogue.jsonl'
        self.catalogue = {r['entity_id']: r for r in read_jsonl(self.catalogue_path)} if self.catalogue_path.exists() else {}
        self.explicit = {}
        for e in read_jsonl(self.frozen / 'structural' / 'explicit_edges.jsonl'):
            self.explicit.setdefault(pair_key(e['from'], e['to']), set()).add(e['predicate'])
        self.mutual = {pair_key(r['a'], r['b']) for r in read_jsonl(self.frozen / 'mutualknn' / 'mutual_pairs.jsonl')}
        self.review_set = {pair_key(r['a'], r['b']) for r in self.selection['selected']}

    def workspace_check(self):
        """Recompute families.py's workspace_sha256 over the exported files."""
        if not self.workspace.is_dir():
            return False, f'workspace {self.workspace} is missing'
        try:
            listing = ''.join(f'{sha256_file(self.workspace / p)}  {p}\n' for p in self.files)
        except FileNotFoundError as exc:
            return False, f'workspace file missing: {exc.filename}'
        digest = hashlib.sha256(listing.encode()).hexdigest()
        expected = self.family_json['family']['workspace_sha256']
        return digest == expected, f'workspace sha256 {digest[:12]} vs recorded {expected[:12]}'

    def line_count(self, path):
        with open(self.workspace / path, 'rb') as f:
            return sum(1 for _ in f)


def first(values, keys):
    for k in keys:
        for v in values.get(k, []):
            if v not in (None, ''):
                return v
    return None


def catalogue_rows(hydration):
    rows = []
    for state in read_jsonl(hydration / 'hydration' / 'entity_states.jsonl'):
        values = {}
        for t in (state.get('state') or {}).get('triples') or []:
            values.setdefault(t['predicate'], []).append(t['object'])
        parts = state['entity_id'].split('.')
        row = {'entity_id': state['entity_id'], 'type': parts[4] if len(parts) >= 6 else None,
               'domain': parts[2] if len(parts) >= 6 else None}
        for field, keys in PREDICATES.items():
            v = first(values, keys)
            if field in ('start_line', 'end_line', 'chunk_index') and v is not None:
                try:
                    v = int(v)
                except (TypeError, ValueError):
                    v = None
            row[field] = v
        row['has_body'] = bool(state.get('body_key'))
        rows.append(row)
    rows.sort(key=lambda r: (r['path'] or '~', r['start_line'] or 0, r['chunk_index'] or 0, r['entity_id']))
    return rows


def write_catalogue(family, evidence_root=EVIDENCE, constraints_dir=CONSTRAINTS):
    rows = catalogue_rows(hydration_dir(family, evidence_root))
    out = Path(constraints_dir) / f'{family}.catalogue.jsonl'
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, 'w', encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + '\n')
    return out, len(rows)


def write_passages(family, evidence_root=EVIDENCE, constraints_dir=CONSTRAINTS):
    hydration = hydration_dir(family, evidence_root)
    bodies = {r['key']: r for r in read_jsonl(hydration / 'hydration' / 'bodies.jsonl')}
    rows = []
    for state in read_jsonl(hydration / 'hydration' / 'entity_states.jsonl'):
        parts = state['entity_id'].split('.')
        if len(parts) < 6 or parts[4] != 'chunk' or not state.get('body_key'):
            continue
        body = bodies.get(state['body_key'])
        values = {}
        for t in (state.get('state') or {}).get('triples') or []:
            values.setdefault(t['predicate'], []).append(t['object'])
        rows.append({'entity_id': state['entity_id'], 'path': first(values, PREDICATES['path']),
                     'chunk_index': first(values, PREDICATES['chunk_index']), 'section': first(values, PREDICATES['section']),
                     'body_sha256': body['sha256'] if body else None, 'text': body.get('text') if body else None})
    rows.sort(key=lambda r: (r['path'] or '~', r['chunk_index'] or 0, r['entity_id']))
    out = Path(constraints_dir) / f'{family}.passages.jsonl'
    if not rows:
        if out.exists():
            out.unlink()
        return out, 0
    with open(out, 'w', encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + '\n')
    return out, len(rows)


def expected_ids():
    return [f'pos-{i:02d}' for i in range(1, PER_POLARITY + 1)] + [f'neg-{i:02d}' for i in range(1, PER_POLARITY + 1)]


def validate_sheet(sheet, fam):
    problems = []
    if sheet.get('family') != fam.family:
        problems.append(f"family is {sheet.get('family')!r}, expected {fam.family!r}")
    if sheet.get('split') != fam.split:
        problems.append(f"split is {sheet.get('split')!r}, expected {fam.split!r}")
    if sheet.get('catalogue_sha256') != sha256_file(fam.catalogue_path):
        problems.append('catalogue_sha256 does not match the catalogue file')
    ann = sheet.get('annotator') or {}
    if ann.get('role') != 'A' or not ann.get('model') or ann.get('method') != 'full-source':
        problems.append('annotator must give role A, a model and method full-source')
    rows = sheet.get('constraints')
    if not isinstance(rows, list):
        return problems + ['constraints must be a list']
    ids = [r.get('id') for r in rows]
    if ids != expected_ids():
        problems.append(f'ids must be exactly pos-01..pos-{PER_POLARITY:02d} then neg-01..neg-{PER_POLARITY:02d} in order')
    seen_pairs = {}
    ws_ok, _ = fam.workspace_check()
    cross_file_positives = 0
    for r in rows:
        tag = str(r.get('id'))
        polarity = r.get('polarity')
        if polarity not in POLARITIES or (tag[:3] == 'pos') != (polarity == 'positive'):
            problems.append(f'{tag}: polarity {polarity!r} does not match the id')
        a, b = r.get('a'), r.get('b')
        if a not in fam.catalogue or b not in fam.catalogue:
            problems.append(f'{tag}: an endpoint is not in the catalogue')
            continue
        if a == b:
            problems.append(f'{tag}: both sides are the same entity')
            continue
        key = pair_key(a, b)
        if key in seen_pairs:
            problems.append(f'{tag}: duplicates {seen_pairs[key]}')
        seen_pairs[key] = tag
        ca, cb = fam.catalogue[a], fam.catalogue[b]
        if 'repo' in (ca['type'], cb['type']):
            problems.append(f'{tag}: the repo entity is not allowed')
        if fam.explicit.get(key, set()) & CONTAINMENT:
            problems.append(f'{tag}: trivial containment pair (explicit contains/belongs edge)')
        if polarity == 'positive' and ca.get('path') and cb.get('path') and ca['path'] != cb['path']:
            cross_file_positives += 1
        evidence = r.get('evidence')
        if not isinstance(evidence, list) or not 1 <= len(evidence) <= 3:
            problems.append(f'{tag}: evidence must hold one to three path:lines entries')
            evidence = []
        for ev in evidence:
            m = EVIDENCE_RE.match(str(ev))
            if not m or m['path'] not in fam.files:
                problems.append(f'{tag}: evidence {ev!r} is not a family file (path[:start[-end]])')
                continue
            if m['start'] and ws_ok:
                start, end = int(m['start']), int(m['end'] or m['start'])
                n = fam.line_count(m['path'])
                if not 1 <= start <= end <= n:
                    problems.append(f'{tag}: evidence {ev!r} is outside the file ({n} lines)')
        rationale = r.get('rationale')
        if not isinstance(rationale, str) or not rationale.strip():
            problems.append(f'{tag}: rationale missing')
        elif len(rationale) > MAX_RATIONALE:
            problems.append(f'{tag}: rationale over {MAX_RATIONALE} characters')
    if cross_file_positives < MIN_CROSS_FILE_POSITIVES and len(rows) == 2 * PER_POLARITY:
        problems.append(f'only {cross_file_positives} positives join different files; at least {MIN_CROSS_FILE_POSITIVES} must')
    if not ws_ok:
        problems.append('note: workspace missing or changed, evidence line ranges were not checked')
    return problems


def validate_review(review, sheet_path, fam):
    problems = []
    if review.get('family') != fam.family:
        problems.append('family differs')
    if review.get('sheet_sha256') != sha256_file(sheet_path):
        problems.append("sheet_sha256 does not match annotator A's sheet")
    ann = review.get('annotator') or {}
    if ann.get('role') != 'B' or not ann.get('model') or ann.get('method') != 'full-source':
        problems.append('annotator must give role B, a model and method full-source')
    verdicts = review.get('verdicts')
    if not isinstance(verdicts, list):
        return problems + ['verdicts must be a list']
    ids = collections.Counter(v.get('id') for v in verdicts)
    for i in expected_ids():
        if ids[i] != 1:
            problems.append(f'{i}: {ids[i]} verdicts, expected one')
    for v in verdicts:
        if v.get('verdict') not in ('agree', 'disagree'):
            problems.append(f"{v.get('id')}: verdict {v.get('verdict')!r}")
        if not isinstance(v.get('rationale'), str) or not v['rationale'].strip():
            problems.append(f"{v.get('id')}: rationale missing")
    return problems


def validate(family, **dirs):
    fam = Family(family, **dirs)
    constraints_dir = Path(dirs.get('constraints_dir', CONSTRAINTS))
    results = {'A': None, 'B': None}
    sheet_path = constraints_dir / f'{family}.json'
    if sheet_path.exists():
        results['A'] = validate_sheet(load_json(sheet_path), fam)
        review_path = constraints_dir / f'{family}.review.json'
        if review_path.exists():
            results['B'] = validate_review(load_json(review_path), sheet_path, fam)
    return results


def flags(row, fam):
    key = pair_key(row['a'], row['b'])
    ca, cb = fam.catalogue[row['a']], fam.catalogue[row['b']]
    return {'in_review_set': key in fam.review_set, 'mutual_candidate': key in fam.mutual,
            'explicit_edge': sorted(fam.explicit.get(key, ())), 'same_file': bool(ca.get('path')) and ca.get('path') == cb.get('path')}


def build_index(**dirs):
    constraints_dir = Path(dirs.get('constraints_dir', CONSTRAINTS))
    families = []
    for cat in sorted(constraints_dir.glob('*.catalogue.jsonl')):
        family = cat.name[:-len('.catalogue.jsonl')]
        fam = Family(family, **dirs)
        checks = validate(family, **dirs)
        row = {'family': family, 'split': fam.split, 'catalogue_entities': len(fam.catalogue)}
        if checks['A'] is None:
            row['annotator_a'] = 'missing'
            families.append(row)
            continue
        if [p for p in checks['A'] if not p.startswith('note:')] or checks['B']:
            raise SystemExit(f'{family}: sheets do not validate; run validate first')
        sheet = load_json(constraints_dir / f'{family}.json')
        flagged = [{**c, 'flags': flags(c, fam)} for c in sheet['constraints']]
        row.update(annotator_a=sheet['annotator'], proposed={p: sum(1 for c in flagged if c['polarity'] == p) for p in POLARITIES},
                   outside_review_set=sum(1 for c in flagged if not c['flags']['in_review_set']),
                   outside_mutual_candidates=sum(1 for c in flagged if not c['flags']['mutual_candidate']),
                   with_explicit_edge=sum(1 for c in flagged if c['flags']['explicit_edge']),
                   same_file=sum(1 for c in flagged if c['flags']['same_file']))
        if checks['B'] is None:
            row.update(annotator_b='missing', review='awaiting annotator B')
        else:
            review = load_json(constraints_dir / f'{family}.review.json')
            verdict = {v['id']: v for v in review['verdicts']}
            final = [c for c in flagged if verdict[c['id']]['verdict'] == 'agree']
            row.update(annotator_b=review['annotator'],
                       agreed={p: sum(1 for c in final if c['polarity'] == p) for p in POLARITIES},
                       dropped=[{'id': c['id'], 'polarity': c['polarity'], 'reason': verdict[c['id']]['rationale']}
                                for c in flagged if verdict[c['id']]['verdict'] == 'disagree'])
            write_json(constraints_dir / f'{family}.final.json', {
                'provenance': PROVENANCE, 'step': STEP, 'family': family, 'split': fam.split,
                'rule': 'constraints both annotators agree on (ruling 7, 2026-10-10); disputed ones dropped',
                'corpus': {'repo': fam.family_json['family']['repo'], 'commit': fam.family_json['family']['commit'],
                           'workspace_sha256': fam.family_json['family']['workspace_sha256']},
                'frozen_capture': self_relative(fam.frozen), 'constraints': final})
        families.append(row)
    totals = collections.Counter()
    for r in families:
        for p, n in (r.get('agreed') or r.get('proposed') or {}).items():
            totals[p] += n
    out = {'provenance': PROVENANCE, 'step': STEP,
           'rule': 'constraints both annotators agree on; disputed ones dropped; flags are after-the-fact and were never shown to annotator A',
           'families': families, 'reviewed_families': sum(1 for r in families if 'agreed' in r), 'totals': dict(totals)}
    write_json(constraints_dir / 'index.json', out)
    return out


def self_relative(path):
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--evidence-root', default=str(EVIDENCE))
    ap.add_argument('--selections-dir', default=str(SELECTIONS))
    ap.add_argument('--constraints-dir', default=str(CONSTRAINTS))
    ap.add_argument('--workspaces', default=str(WORKSPACES))
    sub = ap.add_subparsers(dest='command', required=True)
    sub.add_parser('catalogue').add_argument('family')
    sub.add_parser('passages').add_argument('family')
    sub.add_parser('validate').add_argument('family')
    sub.add_parser('index')
    args = ap.parse_args(argv)
    dirs = {'evidence_root': Path(args.evidence_root), 'selections_dir': Path(args.selections_dir),
            'constraints_dir': Path(args.constraints_dir), 'workspaces': Path(args.workspaces)}
    if args.command in ('catalogue', 'passages'):
        families = ([p.stem for p in sorted(dirs['selections_dir'].glob('*.json')) if p.stem != 'index']
                    if args.family == 'all' else [args.family])
        writer = write_catalogue if args.command == 'catalogue' else write_passages
        for family in families:
            out, n = writer(family, dirs['evidence_root'], dirs['constraints_dir'])
            print(f'{family}: {n} rows -> {out.name} sha256 {sha256_file(out)[:12]}' if n else f'{family}: no passages, no file')
        return 0
    if args.command == 'validate':
        fam = Family(args.family, **dirs)
        ok, detail = fam.workspace_check()
        print(f'{args.family} workspace: {"ok" if ok else "NOT OK"} ({detail}); catalogue sha256 {sha256_file(fam.catalogue_path)}')
        results = validate(args.family, **dirs)
        bad = 0
        for role, problems in results.items():
            if problems is None:
                print(f'{args.family} {role}: no sheet')
            elif [p for p in problems if not p.startswith('note:')]:
                bad += 1
                print(f'{args.family} {role}: {len(problems)} problems')
                for p in problems[:40]:
                    print('  ' + p)
            else:
                print(f'{args.family} {role}: valid' + (' (' + '; '.join(problems) + ')' if problems else ''))
        return 1 if bad or results['A'] is None else 0
    out = build_index(**dirs)
    for r in out['families']:
        print(r['family'], r.get('agreed') or r.get('proposed') or r.get('annotator_a'),
              f"outside review set {r['outside_review_set']}/20, explicit edge {r['with_explicit_edge']}" if 'proposed' in r else '',
              r.get('review', ''))
    print('totals', out['totals'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
