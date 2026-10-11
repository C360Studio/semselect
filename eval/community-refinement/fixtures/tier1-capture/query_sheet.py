#!/usr/bin/env python3
"""Step 5 of the community-refinement protocol: known-answer retrieval queries.

validate <family>   check annotator A's queries/<family>.json (and B's
                    <family>.review.json when present) against queries/PROMPT.md
index               per-family counts by kind, the agreed (final) queries, and
                    after-the-fact flags computed from the frozen captures for
                    every gold set (level-0 communities spanned, explicit edges
                    and mutual-kNN pairs among the members, step-4 constraint
                    pairs among the members), written to
                    queries/<family>.final.json and queries/index.json

The catalogue, passages, workspace check and frozen-capture readers come from
constraint_sheet.py (step 4), loaded by path so `python3 -I` works.

Legacy SemStreams capture; not a SemEngine result.
"""
import argparse
import collections
import importlib.util
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location('constraint_sheet', HERE / 'constraint_sheet.py')
cs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cs)

QUERIES = HERE / 'queries'
PROVENANCE = cs.PROVENANCE
STEP = 'step 5 known-answer retrieval queries (eval/community-refinement/README.md)'
KINDS = {'q-01': 'complementary', 'q-02': 'complementary', 'q-03': 'complementary', 'q-04': 'complementary',
         'q-05': 'precise', 'q-06': 'precise'}
CONTAINERS = {'repo', 'folder', 'file', 'doc'}
MAX_QUESTION = 300
MAX_ANSWER = 600
MAX_RATIONALE = 600
MAX_SETS = 4
MAX_SET_MEMBERS = 6
MAX_EVIDENCE = 8
MIN_DISTINCT_PATHS = 3


class QueryFamily(cs.Family):
    """The step-4 family reader plus the frozen level-0 partition, the final
    step-4 constraints and the passages file. Nothing here is shown to
    annotator A except the catalogue and the passages."""

    def __init__(self, family, queries_dir=QUERIES, **dirs):
        super().__init__(family, **dirs)
        self.queries_dir = Path(queries_dir)
        constraints_dir = Path(dirs.get('constraints_dir', cs.CONSTRAINTS))
        self.passages_path = constraints_dir / f'{family}.passages.jsonl'
        partition = self.frozen / 'structural' / 'partition.json'
        self.community = (cs.load_json(partition).get('entity_to_community') or {}).get('0', {}) if partition.exists() else {}
        self.partition_hash = cs.load_json(partition).get('hash') if partition.exists() else None
        self.constraints = {}
        final = constraints_dir / f'{family}.final.json'
        if final.exists():
            for c in cs.load_json(final)['constraints']:
                self.constraints[cs.pair_key(c['a'], c['b'])] = c['polarity']


def short(entity_id):
    return str(entity_id).rsplit('.', 1)[-1][:60]


def check_set(members, kind, fam, tag, problems):
    """Check one gold set; -> frozenset of members, or None when unusable."""
    if not isinstance(members, list) or not members:
        problems.append(f'{tag}: a gold set must be a non-empty list of entity IDs')
        return None
    if len(set(members)) != len(members):
        problems.append(f'{tag}: a member repeats inside a gold set')
    for m in members:
        row = fam.catalogue.get(m)
        if row is None:
            problems.append(f'{tag}: {short(m)} is not in the catalogue')
            return None
        if row.get('type') in CONTAINERS or not row.get('has_body'):
            problems.append(f'{tag}: {short(m)} is a container or carries no text; gold members must carry text')
    paths = {fam.catalogue[m].get('path') for m in members}
    if kind == 'precise' and len(members) != 1:
        problems.append(f'{tag}: a precise gold set has exactly one member')
    if kind == 'complementary':
        if not 2 <= len(members) <= MAX_SET_MEMBERS:
            problems.append(f'{tag}: a complementary gold set has two to {MAX_SET_MEMBERS} members')
        if len(paths) < 2:
            problems.append(f'{tag}: a complementary gold set must span two different files or documents')
    return frozenset(members)


def validate_sheet(sheet, fam):
    problems = []
    if sheet.get('family') != fam.family:
        problems.append(f"family is {sheet.get('family')!r}, expected {fam.family!r}")
    if sheet.get('split') != fam.split:
        problems.append(f"split is {sheet.get('split')!r}, expected {fam.split!r}")
    if sheet.get('catalogue_sha256') != cs.sha256_file(fam.catalogue_path):
        problems.append('catalogue_sha256 does not match the catalogue file')
    if fam.passages_path.exists():
        if sheet.get('passages_sha256') != cs.sha256_file(fam.passages_path):
            problems.append('passages_sha256 does not match the passages file')
    elif sheet.get('passages_sha256') is not None:
        problems.append('passages_sha256 must be null: this family has no passages file')
    ann = sheet.get('annotator') or {}
    if ann.get('role') != 'A' or not ann.get('model') or ann.get('method') != 'full-source':
        problems.append('annotator must give role A, a model and method full-source')
    rows = sheet.get('queries')
    if not isinstance(rows, list):
        return problems + ['queries must be a list']
    if [r.get('id') for r in rows] != list(KINDS):
        problems.append('ids must be exactly q-01..q-06 in order')
    ws_ok, _ = fam.workspace_check()
    primaries = {}
    primary_paths = set()
    for r in rows:
        tag = str(r.get('id'))
        kind = KINDS.get(tag)
        if r.get('kind') != kind:
            problems.append(f'{tag}: kind {r.get("kind")!r} must be {kind!r}')
        question = r.get('question')
        if not isinstance(question, str) or not question.strip():
            problems.append(f'{tag}: question missing')
        else:
            if len(question) > MAX_QUESTION:
                problems.append(f'{tag}: question over {MAX_QUESTION} characters')
            if any(e in question for e in fam.catalogue):
                problems.append(f'{tag}: the question names an entity ID')
            hit = next((p for p in fam.files if p in question), None)
            if hit:
                problems.append(f'{tag}: the question names the file {hit}; ask the way a developer would')
        answer = r.get('answer')
        if not isinstance(answer, str) or not answer.strip():
            problems.append(f'{tag}: answer missing')
        elif len(answer) > MAX_ANSWER:
            problems.append(f'{tag}: answer over {MAX_ANSWER} characters')
        sets = r.get('gold_sets')
        if not isinstance(sets, list) or not 1 <= len(sets) <= MAX_SETS:
            problems.append(f'{tag}: gold_sets must hold one to {MAX_SETS} sets')
            sets = []
        checked = []
        for s in sets:
            fs = check_set(s, kind, fam, tag, problems)
            if fs is None:
                continue
            for other in checked:
                if fs == other:
                    problems.append(f'{tag}: a gold set repeats')
                elif fs < other or fs > other:
                    problems.append(f'{tag}: one gold set contains another; gold sets are minimal')
            checked.append(fs)
        ppaths = set()
        if checked:
            primary = checked[0]
            if primary in primaries:
                problems.append(f'{tag}: the primary gold set repeats that of {primaries[primary]}')
            primaries.setdefault(primary, tag)
            ppaths = {fam.catalogue[m].get('path') for m in primary}
            primary_paths |= ppaths
        evidence = r.get('evidence')
        if not isinstance(evidence, list) or not 1 <= len(evidence) <= MAX_EVIDENCE:
            problems.append(f'{tag}: evidence must hold one to {MAX_EVIDENCE} path:lines entries')
            evidence = []
        cited = set()
        for ev in evidence:
            m = cs.EVIDENCE_RE.match(str(ev))
            if not m or m['path'] not in fam.files:
                problems.append(f'{tag}: evidence {ev!r} is not a family file (path[:start[-end]])')
                continue
            cited.add(m['path'])
            if m['start'] and ws_ok:
                start, end = int(m['start']), int(m['end'] or m['start'])
                n = fam.line_count(m['path'])
                if not 1 <= start <= end <= n:
                    problems.append(f'{tag}: evidence {ev!r} is outside the file ({n} lines)')
        if ppaths and not ppaths <= cited:
            problems.append(f'{tag}: evidence must cite every file of the primary gold set')
        rationale = r.get('rationale')
        if not isinstance(rationale, str) or not rationale.strip():
            problems.append(f'{tag}: rationale missing')
        elif len(rationale) > MAX_RATIONALE:
            problems.append(f'{tag}: rationale over {MAX_RATIONALE} characters')
    if len(rows) == len(KINDS) and len(primary_paths) < MIN_DISTINCT_PATHS:
        problems.append(f'the primary gold sets draw on {len(primary_paths)} files or documents; at least {MIN_DISTINCT_PATHS} needed')
    if not ws_ok:
        problems.append('note: workspace missing or changed, evidence line ranges were not checked')
    return problems


def validate_review(review, sheet_path, fam):
    problems = []
    if review.get('family') != fam.family:
        problems.append('family differs')
    if review.get('sheet_sha256') != cs.sha256_file(sheet_path):
        problems.append("sheet_sha256 does not match annotator A's sheet")
    ann = review.get('annotator') or {}
    if ann.get('role') != 'B' or not ann.get('model') or ann.get('method') != 'full-source':
        problems.append('annotator must give role B, a model and method full-source')
    verdicts = review.get('verdicts')
    if not isinstance(verdicts, list):
        return problems + ['verdicts must be a list']
    a_sets = {r.get('id'): [frozenset(s) for s in r.get('gold_sets') or [] if isinstance(s, list)]
              for r in cs.load_json(sheet_path).get('queries') or []}
    ids = collections.Counter(v.get('id') for v in verdicts)
    for i in KINDS:
        if ids[i] != 1:
            problems.append(f'{i}: {ids[i]} verdicts, expected one')
    for v in verdicts:
        tag = str(v.get('id'))
        if v.get('verdict') not in ('agree', 'disagree'):
            problems.append(f"{tag}: verdict {v.get('verdict')!r}")
        if not isinstance(v.get('rationale'), str) or not v['rationale'].strip():
            problems.append(f'{tag}: rationale missing')
        extra = v.get('additional_sets', [])
        if not isinstance(extra, list):
            problems.append(f'{tag}: additional_sets must be a list of sets')
            continue
        for s in extra:
            fs = check_set(s, KINDS.get(tag), fam, f'{tag} additional set', problems)
            if fs is not None and fs in a_sets.get(tag, []):
                problems.append(f"{tag}: an additional set repeats one of A's gold sets")
    return problems


def validate(family, **dirs):
    fam = QueryFamily(family, **dirs)
    results = {'A': None, 'B': None}
    sheet_path = fam.queries_dir / f'{family}.json'
    if sheet_path.exists():
        results['A'] = validate_sheet(cs.load_json(sheet_path), fam)
        review_path = fam.queries_dir / f'{family}.review.json'
        if review_path.exists():
            results['B'] = validate_review(cs.load_json(review_path), sheet_path, fam)
    return results


def set_flags(members, fam):
    """After-the-fact description of one gold set from the frozen capture."""
    pairs = [cs.pair_key(a, b) for i, a in enumerate(members) for b in members[i + 1:]]
    return {'members': len(members),
            'files': len({fam.catalogue[m].get('path') for m in members}),
            'communities_spanned': len({fam.community.get(m) for m in members}) if fam.community else None,
            'explicit_edges_among_members': sum(1 for p in pairs if fam.explicit.get(p)),
            'mutual_pairs_among_members': sum(1 for p in pairs if p in fam.mutual),
            'constraint_pairs': dict(collections.Counter(fam.constraints[p] for p in pairs if p in fam.constraints))}


def build_index(**dirs):
    queries_dir = Path(dirs.pop('queries_dir', QUERIES))
    constraints_dir = Path(dirs.get('constraints_dir', cs.CONSTRAINTS))
    families = []
    for cat in sorted(constraints_dir.glob('*.catalogue.jsonl')):
        family = cat.name[:-len('.catalogue.jsonl')]
        fam = QueryFamily(family, queries_dir=queries_dir, **dirs)
        checks = validate(family, queries_dir=queries_dir, **dirs)
        row = {'family': family, 'split': fam.split, 'catalogue_entities': len(fam.catalogue)}
        if checks['A'] is None:
            row['annotator_a'] = 'missing'
            families.append(row)
            continue
        if [p for p in checks['A'] if not p.startswith('note:')] or checks['B']:
            raise SystemExit(f'{family}: sheets do not validate; run validate first')
        sheet = cs.load_json(queries_dir / f'{family}.json')
        flagged = [{**q, 'flags': [set_flags(s, fam) for s in q['gold_sets']]} for q in sheet['queries']]
        complementary = [q for q in flagged if q['kind'] == 'complementary']
        row.update(annotator_a=sheet['annotator'],
                   proposed=dict(collections.Counter(q['kind'] for q in flagged)),
                   alternative_sets=sum(len(q['gold_sets']) - 1 for q in flagged),
                   primary_set_spans_communities=sum(1 for q in complementary if (q['flags'][0]['communities_spanned'] or 0) > 1),
                   some_set_within_one_community=sum(1 for q in complementary if any(f['communities_spanned'] == 1 for f in q['flags'])),
                   primary_set_has_explicit_edge=sum(1 for q in complementary if q['flags'][0]['explicit_edges_among_members']),
                   primary_set_has_mutual_pair=sum(1 for q in complementary if q['flags'][0]['mutual_pairs_among_members']),
                   primary_set_has_constraint_pair=sum(1 for q in complementary if q['flags'][0]['constraint_pairs']))
        if checks['B'] is None:
            row.update(annotator_b='missing', review='awaiting annotator B')
        else:
            review = cs.load_json(queries_dir / f'{family}.review.json')
            verdict = {v['id']: v for v in review['verdicts']}
            final = [{**q, 'b_additional_sets': verdict[q['id']].get('additional_sets', [])}
                     for q in flagged if verdict[q['id']]['verdict'] == 'agree']
            row.update(annotator_b=review['annotator'],
                       agreed=dict(collections.Counter(q['kind'] for q in final)),
                       dropped=[{'id': q['id'], 'kind': q['kind'], 'reason': verdict[q['id']]['rationale']}
                                for q in flagged if verdict[q['id']]['verdict'] == 'disagree'],
                       b_additional_sets=sum(len(q['b_additional_sets']) for q in final))
            cs.write_json(queries_dir / f'{family}.final.json', {
                'provenance': PROVENANCE, 'step': STEP, 'family': family, 'split': fam.split,
                'rule': "queries both annotators agree on (ruling 7, 2026-10-10); disputed ones dropped; gold sets are annotator A's, "
                        'sets B added are recorded under b_additional_sets and are not gold',
                'success': 'the budgeted evidence contains every member of at least one gold set',
                'corpus': {'repo': fam.family_json['family']['repo'], 'commit': fam.family_json['family']['commit'],
                           'workspace_sha256': fam.family_json['family']['workspace_sha256']},
                'frozen_capture': cs.self_relative(fam.frozen), 'partition_hash': fam.partition_hash, 'queries': final})
        families.append(row)
    totals = collections.Counter()
    for r in families:
        counts = r.get('agreed') or r.get('proposed') or {}
        for k, n in counts.items():
            totals[k] += n
        totals[r['split']] += sum(counts.values())
    out = {'provenance': PROVENANCE, 'step': STEP,
           'rule': 'queries both annotators agree on; disputed ones dropped; flags are after-the-fact and were never shown to annotator A',
           'families': families, 'reviewed_families': sum(1 for r in families if 'agreed' in r), 'totals': dict(totals)}
    cs.write_json(queries_dir / 'index.json', out)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--evidence-root', default=str(cs.EVIDENCE))
    ap.add_argument('--selections-dir', default=str(cs.SELECTIONS))
    ap.add_argument('--constraints-dir', default=str(cs.CONSTRAINTS))
    ap.add_argument('--queries-dir', default=str(QUERIES))
    ap.add_argument('--workspaces', default=str(cs.WORKSPACES))
    sub = ap.add_subparsers(dest='command', required=True)
    sub.add_parser('validate').add_argument('family')
    sub.add_parser('index')
    args = ap.parse_args(argv)
    dirs = {'evidence_root': Path(args.evidence_root), 'selections_dir': Path(args.selections_dir),
            'constraints_dir': Path(args.constraints_dir), 'workspaces': Path(args.workspaces)}
    if args.command == 'validate':
        fam = QueryFamily(args.family, queries_dir=Path(args.queries_dir), **dirs)
        ok, detail = fam.workspace_check()
        passages = cs.sha256_file(fam.passages_path) if fam.passages_path.exists() else None
        print(f'{args.family} workspace: {"ok" if ok else "NOT OK"} ({detail}); catalogue sha256 {cs.sha256_file(fam.catalogue_path)}; '
              f'passages sha256 {passages}')
        results = validate(args.family, queries_dir=Path(args.queries_dir), **dirs)
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
    out = build_index(queries_dir=Path(args.queries_dir), **dirs)
    for r in out['families']:
        print(r['family'], r.get('agreed') or r.get('proposed') or r.get('annotator_a'),
              f"primary set spans communities {r['primary_set_spans_communities']}/4, alternatives {r['alternative_sets']}" if 'proposed' in r else '',
              r.get('review', ''))
    print('totals', out['totals'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
