#!/usr/bin/env python3
"""Derive paired outcomes from preserved rows; does not run a classifier."""
import argparse
import hashlib
import json
from pathlib import Path

import experiment as e
import runner


def correct(row, case):
    return row['status'] == 'ok' and e.exact(row['options'], case['gold']['options'])


def view(rows, arm, order):
    selected = [r for r in rows if r['arm'] == arm and r['view'] == order]
    e.require(len(selected) == 32 and len({r['id'] for r in selected}) == 32, 'comparison needs all 32 unique case records')
    return {r['id']: r for r in selected}


def paired(model, code, cases):
    result = {'corrected': [], 'regressed': [], 'both_correct': [], 'both_wrong': [], 'unassessed': []}
    for ident, case in cases.items():
        if not assessed(model[ident]) or not assessed(code[ident]):
            result['unassessed'].append(ident)
            continue
        m, c = correct(model[ident], case), correct(code[ident], case)
        result['both_correct' if m and c else 'corrected' if m else 'regressed' if c else 'both_wrong'].append(ident)
    return result


def assessed(row):
    return row['status'] not in ('not_run', 'interrupted')


def primary_grade(rows, cases, group):
    value = runner.grade(list(rows.values()),list(cases.values()))[group]
    value['assessed'] = sum(assessed(row) for row in rows.values())
    value['comparison_complete'] = value['assessed'] == value['total']
    value['reported_accuracy'] = value['exact']/value['total'] if value['comparison_complete'] else None
    if not value['comparison_complete']:
        value['median_end_to_end_ms'] = None
        value['p95_end_to_end_ms'] = None
    value['note'] = 'All planned rows remain preserved. exact is an observed count; do not interpret exact/total as accuracy when comparison_complete is false. Incomplete cohorts have no reported latency median or percentile; inspect completed request timings in the raw records.'
    return value


def read_run(path, design_sha):
    raw = path.read_bytes()
    run = json.loads(raw)
    e.require(run.get('execution', {}).get('design_freeze_sha256') == design_sha,
              'result does not reference the verified design freeze')
    return run, hashlib.sha256(raw).hexdigest()


def raw_selection(row):
    """Expose invalid cross-field selections without repairing or regrading them."""
    if row.get('selection') is not None:
        return row['selection']
    response = row.get('response', {})
    try:
        if row['arm'] == 'qwen_json':
            return runner.strict_json(response['choices'][0]['message']['content'])
        return {name: answer['choice'] for name, answer in response['answers'].items()}
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def summarize(code_path, model_paths):
    e.verify_freeze()
    design_sha = e.digest(e.ROOT/'freeze.json')
    cases = {c['id']: c for c in e.load('heldout.json')['cases']}
    code, code_sha = read_run(code_path, design_sha)
    e.require(len(code['rows']) == 224, 'missing planned code rows')
    result = {'task': 'bounded classification hints, not search execution', 'sources': {str(code_path): code_sha},
              'design_freeze_sha256': design_sha,
              'execution_status': {'host': code['status']},
              'designated_code_arm': e.load('development-selection.json')['strongest_code_arm'], 'primary': {}, 'paired': {}, 'families': {}, 'sensitivity': {}, 'failures': {}}
    for arm in runner.ARMS:
        rows = view(code['rows'], arm, 'primary')
        result['primary']['host/'+arm] = primary_grade(rows,cases,arm+'/primary')
    hardware_rows = {}
    for path in model_paths:
        run, run_sha = read_run(path, design_sha)
        e.require(len(run['rows']) == 128, 'missing planned model rows')
        result['sources'][str(path)] = run_sha
        hardware = run['hardware']
        e.require(hardware not in result['execution_status'], 'provide only one attempt per hardware; failed attempts remain separate evidence')
        result['execution_status'][hardware] = run['status']
        hardware_rows[hardware] = run['rows']
        for arm in runner.MODEL_ARMS:
            name = hardware+'/'+arm
            primary, reverse = view(run['rows'], arm, 'normal'), view(run['rows'], arm, 'reverse')
            result['primary'][name] = primary_grade(primary,cases,arm+'/normal')
            result['paired'][name] = {baseline: paired(primary, view(code['rows'], baseline, 'primary'), cases) for baseline in runner.ARMS}
            result['families'][name] = {}
            for family in sorted({c['family'] for c in cases.values()}):
                ids = [ident for ident,c in cases.items() if c['family'] == family]
                result['families'][name][family] = {'total':len(ids),'exact':sum(correct(primary[i],cases[i]) for i in ids), 'failed_ids':[i for i in ids if assessed(primary[i]) and not correct(primary[i],cases[i])], 'unassessed_ids':[i for i in ids if not assessed(primary[i])]}
            comparable = [i for i in cases if primary[i]['status'] == reverse[i]['status'] == 'ok']
            raw_pairs = [i for i in cases if raw_selection(primary[i]) is not None and raw_selection(reverse[i]) is not None]
            result['sensitivity'][name] = {
                'primary_exact': sum(correct(primary[i],cases[i]) for i in cases),
                'reverse_exact': sum(correct(reverse[i],cases[i]) for i in cases),
                'primary_complete': all(assessed(primary[i]) for i in cases),
                'reverse_complete': all(assessed(reverse[i]) for i in cases),
                'valid_pairs':len(comparable),
                'changed_options':[i for i in comparable if e.normalize(primary[i]['options']) != e.normalize(reverse[i]['options'])],
                'not_comparable':[i for i in cases if i not in comparable],
                'raw_selection_pairs':len(raw_pairs),
                'changed_raw_selections':[i for i in raw_pairs if raw_selection(primary[i]) != raw_selection(reverse[i])],
                'validity_changed':[i for i in cases if assessed(primary[i]) and assessed(reverse[i]) and (primary[i]['status'] == 'ok') != (reverse[i]['status'] == 'ok')],
            }
            result['failures'][name] = [{'id':i,'query':cases[i]['input']['query'],'expected':cases[i]['gold']['options'],
                                        'actual':primary[i].get('options'),'status':primary[i]['status'],
                                        'raw_selection':raw_selection(primary[i]),
                                        'error':primary[i].get('error'),'reason':cases[i]['gold']['reason']}
                                       for i in cases if assessed(primary[i]) and not correct(primary[i],cases[i])]
        result.setdefault('runtimes',{})[hardware] = [{'arm':rt['arm'],'status':rt.get('status'),'stopped':rt.get('stopped'),
                                                     'cleanup_errors':rt.get('cleanup_errors'), 'child_exit_codes':rt.get('child_exit_codes'),
                                                     'container_exit':rt.get('container_stopped',{}).get('State')}
                                                    for rt in run['runtimes']]
    for arm in runner.ARMS:
        name = 'host/'+arm
        primary = view(code['rows'],arm,'primary')
        result['families'][name] = {}
        for family in sorted({c['family'] for c in cases.values()}):
            ids = [i for i,c in cases.items() if c['family']==family]
            result['families'][name][family] = {'total':len(ids),'exact':sum(correct(primary[i],cases[i]) for i in ids), 'failed_ids':[i for i in ids if not correct(primary[i],cases[i])]}
        result['failures'][name] = [{'id':i,'query':cases[i]['input']['query'],'expected':cases[i]['gold']['options'],
                                    'actual':primary[i].get('options'),'status':primary[i]['status'],'reason':cases[i]['gold']['reason']}
                                   for i in cases if not correct(primary[i],cases[i])]
        if arm != 'keyword':
            normal, reverse = view(code['rows'],arm,'normal'), view(code['rows'],arm,'reverse')
            result['sensitivity'][name] = {'persistent_normal_exact':sum(correct(normal[i],cases[i]) for i in cases),
                'persistent_reverse_exact':sum(correct(reverse[i],cases[i]) for i in cases),
                'normal_changed_from_fresh':[i for i in cases if normal[i].get('options') != primary[i].get('options')],
                'reverse_changed_from_fresh':[i for i in cases if reverse[i].get('options') != primary[i].get('options')]}
    if set(hardware_rows) == {'metal', 'cpu-docker'}:
        result['hardware_agreement'] = {}
        for arm in runner.MODEL_ARMS:
            for order in ('normal', 'reverse'):
                metal = view(hardware_rows['metal'], arm, order)
                cpu = view(hardware_rows['cpu-docker'], arm, order)
                observed = [i for i in cases if raw_selection(metal[i]) is not None and raw_selection(cpu[i]) is not None]
                completed = [i for i in cases if assessed(metal[i]) and assessed(cpu[i])]
                result['hardware_agreement'][arm+'/'+order] = {
                    'raw_selection_pairs': len(observed),
                    'different_raw_selection_ids': [i for i in observed if raw_selection(metal[i]) != raw_selection(cpu[i])],
                    'completed_pairs':len(completed),
                    'different_status_ids': [i for i in completed if metal[i]['status'] != cpu[i]['status']],
                    'not_comparable': [i for i in cases if i not in observed],
                    'note': 'Includes application-invalid tuples. Matching outputs do not establish correctness or identical probabilities.'}
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--code', type=Path, required=True)
    p.add_argument('--models', type=Path, nargs='+', required=True)
    p.add_argument('--output', type=Path, required=True)
    args=p.parse_args()
    result=summarize(args.code,args.models)
    with args.output.open('x') as out:
        json.dump(result,out,indent=2,ensure_ascii=False,allow_nan=False)
        out.write('\n')
    for arm, row in result['primary'].items():
        outcome = f"{row['exact']}/{row['total']} exact" if row['comparison_complete'] else f"incomplete: {row['assessed']}/{row['total']} assessed; no accuracy claim"
        print(arm, outcome, row['status_counts'])


if __name__=='__main__':
    main()
