#!/usr/bin/env python3
"""Fixture freeze for the community-refinement quality pilot (issue #5).

build    write freeze.json: for every family in families.json the frozen
         evidence directory with its checksum file verified, the selection,
         the packets, the final labels, constraints and queries with their
         SHA-256 and counts; the annotation contracts; the model locks; the
         capture profile; the totals
verify   rebuild the record from disk and compare it with freeze.json
         (everything but the build time and commit); exit 1 on any drift,
         including a changed file inside a frozen evidence directory

Legacy SemStreams capture; not a SemEngine result.
"""
import argparse
import collections
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
FREEZE = HERE / 'freeze.json'
FAMILIES = ROOT / 'eval' / 'community-refinement' / 'fixtures' / 'families.json'
EVIDENCE = ROOT / 'docs' / 'evidence'
PROVENANCE = 'legacy SemStreams capture; not a SemEngine result'
STEP = 'fixture freeze (eval/community-refinement/README.md; issue #5 "Fixtures frozen")'
CONTRACTS = ['labels/PROMPT.md', 'labels/REVIEW.md', 'constraints/PROMPT.md', 'constraints/REVIEW.md',
             'queries/PROMPT.md', 'queries/REVIEW.md']
LOCKS = ['models.lock.json', 'models.baseline.lock.json']
VOLATILE = ('built_at', 'git_commit')


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def load(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def rel(path):
    return str(Path(path).resolve().relative_to(ROOT))


def check_sums(evidence_dir):
    """Recompute every entry of the directory's SHA256SUMS; -> (listed, mismatches)."""
    listed, bad = 0, []
    for line in (evidence_dir / 'SHA256SUMS').read_text().splitlines():
        if not line.strip():
            continue
        digest, name = line.split(None, 1)
        listed += 1
        target = evidence_dir / name.lstrip('*')
        if not target.is_file() or sha256_file(target) != digest:
            bad.append(name)
    return listed, bad


def evidence_dir_for(fam, selections_dir):
    sel = selections_dir / f'{fam["id"]}.json'
    if sel.exists():
        return EVIDENCE / load(sel)['evidence']
    dirs = sorted(d for d in EVIDENCE.glob(f'*-legacy-tier1-capture-{fam["id"]}-explicit-only') if (d / 'summary.json').exists())
    return dirs[-1] if dirs else None


def artifact(path, counts=None):
    if not path.exists():
        return None
    out = {'path': rel(path), 'sha256': sha256_file(path)}
    if counts:
        out.update(counts)
    return out


def family_record(fam, here=HERE):
    selections_dir, packets_dir = here / 'selections', here / 'packets'
    ev = evidence_dir_for(fam, selections_dir)
    rec = {'family': fam['id'], 'split': fam['split'], 'corpus': {'repo': fam['repo'], 'commit': fam['commit']},
           'tier0_entities_total': fam.get('entities_total')}
    if ev is None:
        rec['capture'] = 'missing'
        return rec
    summary = load(ev / 'summary.json')
    listed, bad = check_sums(ev)
    rec['capture'] = {
        'evidence': rel(ev), 'sha256sums': sha256_file(ev / 'SHA256SUMS'), 'files_listed': listed,
        'files_matching': listed - len(bad), 'mismatched': bad, 'identity_profile': summary.get('identity_profile'),
        'commit': summary.get('commit'), 'workspace_sha256': summary.get('workspace_sha256'),
        'entities': summary.get('entities'), 'explicit_edges': summary.get('explicit_edges'),
        'partition_hash': summary.get('partition_hash'),
        'level0_communities': (summary.get('partition_level0') or {}).get('communities'),
        'mutual_pairs': (summary.get('mutualknn') or {}).get('mutual_pairs'),
        'structural_checks': summary.get('structural_checks'),
        'hydration_checks': (summary.get('hydration') or {}).get('checks')}
    rec['selection'] = artifact(selections_dir / f'{fam["id"]}.json')
    pk = packets_dir / fam['id']
    if (pk / 'index.json').exists():
        idx = load(pk / 'index.json')
        rec['packets'] = {'index': artifact(pk / 'index.json'), 'pairs': artifact(pk / 'pairs.jsonl', {'packets': idx['pairs']['packets']}),
                          'bundles': artifact(pk / 'bundles.jsonl'), 'tokens': artifact(pk / 'tokens.json')}
    else:
        rec['packets'] = None
    for step, name, key, counter in (('labels', 'labels', 'labels', lambda rows: collections.Counter(r['final'] for r in rows)),
                                     ('constraints', 'constraints', 'constraints', lambda rows: collections.Counter(r['polarity'] for r in rows)),
                                     ('queries', 'queries', 'queries', lambda rows: collections.Counter(r['kind'] for r in rows))):
        final = here / name / f'{fam["id"]}.final.json'
        if final.exists():
            rows = load(final)[key]
            rec[step] = artifact(final, {'final_counts': dict(sorted(counter(rows).items())), 'final_total': len(rows)})
        else:
            rec[step] = None
    return rec


def build(here=HERE, families_path=FAMILIES):
    families = load(families_path)
    records = [family_record(f, here) for f in families['families']]
    totals = collections.Counter()
    for r in records:
        for step in ('labels', 'constraints', 'queries'):
            if r.get(step):
                for k, n in r[step]['final_counts'].items():
                    totals[f'{step}.{k}'] += n
                if step == 'queries' and r['split'] == 'held-out':
                    totals['queries.held-out'] += r[step]['final_total']
    try:
        commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit = None
    return {
        'provenance': PROVENANCE, 'step': STEP,
        'built_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 'git_commit': commit,
        'rulings': 'owner rulings 1 to 9 as recorded on issue #5 and in eval/community-refinement/README.md',
        'capture_profile': families['capture_profile'],
        'families_json': {'path': rel(families_path), 'sha256': sha256_file(families_path)},
        'contracts': {c: sha256_file(here / c) for c in CONTRACTS if (here / c).exists()},
        'model_locks': {l: sha256_file(ROOT / l) for l in LOCKS if (ROOT / l).exists()},
        'families': records,
        'totals': dict(sorted(totals.items())),
    }


def strip(record):
    return {k: v for k, v in record.items() if k not in VOLATILE}


def diff(a, b, path=''):
    """-> list of 'path: old -> new' for every leaf that differs."""
    out = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            out += diff(a.get(k, '<absent>'), b.get(k, '<absent>'), f'{path}.{k}' if path else k)
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b)):
            out += diff(x, y, f'{path}[{i}]')
    elif a != b:
        out.append(f'{path}: {json.dumps(a)[:80]} -> {json.dumps(b)[:80]}')
    return out


def verify(freeze_path=FREEZE, here=HERE, families_path=FAMILIES):
    """-> list of problems; empty when the disk still matches the freeze."""
    stored = load(freeze_path)
    problems = diff(strip(stored), strip(build(here, families_path)))
    for r in stored['families']:
        cap = r.get('capture')
        if cap == 'missing' or cap is None:
            problems.append(f'{r["family"]}: no capture frozen')
        elif cap.get('mismatched'):
            problems.append(f'{r["family"]}: files changed inside the frozen evidence directory: {cap["mismatched"]}')
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--freeze', default=str(FREEZE))
    sub = ap.add_subparsers(dest='command', required=True)
    sub.add_parser('build')
    sub.add_parser('verify')
    args = ap.parse_args(argv)
    if args.command == 'build':
        record = build()
        with open(args.freeze, 'w', encoding='utf-8') as f:
            json.dump(record, f, indent=2, ensure_ascii=False)
            f.write('\n')
        for r in record['families']:
            cap = r['capture']
            print(r['family'], r['split'], 'capture missing' if cap == 'missing' else
                  f"{cap['evidence'].rsplit('/', 1)[-1]} files {cap['files_matching']}/{cap['files_listed']} entities {cap['entities']}",
                  ' '.join(f"{s}={r[s]['final_total']}" for s in ('labels', 'constraints', 'queries') if r.get(s)))
        print('totals', record['totals'])
        bad = [r['family'] for r in record['families'] if r['capture'] == 'missing' or r['capture']['mismatched']]
        return 1 if bad else 0
    problems = verify(Path(args.freeze))
    for p in problems:
        print('  ' + p)
    print('freeze: ' + ('intact' if not problems else f'{len(problems)} problems'))
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
