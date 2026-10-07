#!/usr/bin/env python3
"""Fixed smaller-Qwen evaluation, reusing the reviewed specialist contracts."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import random
import shutil
import sys
import threading
import time

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
sys.path.insert(0, str(ROOT.parent/'specialist-intent'))
import adapters
import evidence as e
import preflight
import resources
import runner as base
import scoring
from transport import encoded

ARMS = ('qwen35_2b', 'qwen3_17b')
STAGES = ('development', 'primary', 'sensitivity')
HISTORICAL = REPO/'results/specialist-intent/20261007-execution/run'
REFERENCE = HISTORICAL/'reports/20261007T153847.325600Z/summary.json'

def protocol(): return e.read(ROOT/'protocol.json')
def model(arm):
    if arm not in ARMS: raise ValueError('unknown arm')
    return e.read(ROOT/'locks'/f'{arm}.json')
def runtime_lock(): return e.read(ROOT/'locks/runtime.json')
def now(): return base.now()
def dataset(split): return base.dataset(split)

def request(case, arm, variant=0, view='primary'):
    req = base.request(case, 'qwen', variant, view)
    req['request']['payload']['model'] = model(arm)['alias']
    req['payload_sha256'] = hashlib.sha256(encoded(req['request']['payload'])).hexdigest()
    req['model_arm'] = arm
    return req

def sources():
    result = e.source_hashes()
    result[str(REFERENCE.relative_to(REPO))] = e.digest(REFERENCE)
    for p in ROOT.rglob('*'):
        if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc':
            result[str(p.relative_to(REPO))] = e.digest(p)
    return result

def prepare(run):
    if run.exists() and any(run.iterdir()): raise ValueError('run must be new or empty')
    if run.resolve().is_relative_to(ROOT): raise ValueError('run must be outside source tree')
    base.verify_run(HISTORICAL)
    run.mkdir(parents=True, exist_ok=True)
    requests=[]
    for arm in ARMS:
        for split in ('development','heldout'):
            for case in dataset(split):
                views = ['primary'] + (['reverse','remap'] if split=='heldout' and case['id'] in base.selected_ids('sensitivity') else [])
                for variant in range(2):
                    requests.extend(request(case,arm,variant,view) for view in views)
    e.save_new(run/'requests.json',requests)
    for arm in ARMS: e.save_new(run/f'requests-{arm}.json',[r for r in requests if r['model_arm']==arm])
    e.save_new(run/'reference.json',e.read(REFERENCE))
    manifest=sources()
    for name in manifest:
        target=run/'source'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(REPO/name,target)
    freeze={'prepared_at':now(),'sources':manifest,'requests_sha256':e.digest(run/'requests.json'),'reference_sha256':e.digest(run/'reference.json'),'arm_requests_sha256':{a:e.digest(run/f'requests-{a}.json') for a in ARMS},'protocol':protocol(),'models':{a:model(a) for a in ARMS},'runtime':runtime_lock(),'cohort':'Previously used authored evaluation cohort; fixed comparative screen, not fresh confirmation.'}
    e.save_new(run/'freeze.json',freeze)
    return {'payloads':len(requests),'freeze_sha256':e.digest(run/'freeze.json')}

def verify_run(run):
    f=e.read(run/'freeze.json')
    if sources()!=f['sources'] or e.digest(run/'requests.json')!=f['requests_sha256'] or e.digest(run/'reference.json')!=f['reference_sha256']: raise ValueError('source, payload or reference freeze changed')
    for a in ARMS:
        if e.digest(run/f'requests-{a}.json')!=f['arm_requests_sha256'][a]: raise ValueError('arm request manifest changed')
    review=e.read(run/'review.json')
    if review.get('freeze_sha256')!=e.digest(run/'freeze.json') or review.get('independent') is not True or any(review.get(k) is not True for k in ('payloads_verified','scoring_verified','lifecycle_verified')): raise ValueError('independent pre-execution review missing or mismatched')
    return f

def requests_for_arm(run,arm): return e.read(run/f'requests-{arm}.json')
def rows(run,stage,arm): return base.stage_rows(run,stage,arm)
def completed(run,stage,arm): return base.stage_complete(run,stage,arm)
def previous_stop(run,arm): return base.previous_stop(run,arm)

def remaining(run,arm):
    spent=sum(r.get('elapsed_ms',0)/1000 for stage in STAGES+('load',) for r in rows(run,stage,arm) if scoring.valid_time(r.get('elapsed_ms')))
    return max(0,protocol()['inference_seconds']-spent)

def verify_selection(run):
    f=e.read(run/'selection-freeze.json')
    if e.digest(run/'selection.json')!=f['selection_sha256']: raise ValueError('selection changed')
    for arm in ARMS:
        if not completed(run,'development',arm) or e.digest(run/'development'/arm/'rows.json')!=f['rows'][arm]:raise ValueError('development rows incomplete/changed')
        p=run/'development'/arm/'environment.json'
        if (e.digest(p) if p.exists() else None)!=f['environments'][arm]:raise ValueError('development environment changed')
    return e.read(run/'selection.json')

def barrier(run,stage,arm):
    verify_run(run)
    if arm not in ARMS or stage not in STAGES+('load',):raise ValueError('invalid stage/arm')
    if stage!='load':
        for earlier in ARMS[:ARMS.index(arm)]:
            if not completed(run,stage,earlier):raise ValueError('earlier arm incomplete')
    if stage!='development':verify_selection(run)
    if stage in ('sensitivity','load') and not all(completed(run,'primary',a) for a in ARMS):raise ValueError('primary barrier incomplete')
    if stage=='load' and not all(completed(run,'sensitivity',a) for a in ARMS):raise ValueError('sensitivity barrier incomplete')

def jobs(run,stage,arm):
    cases=dataset('development' if stage=='development' else 'heldout')
    if stage=='sensitivity':cases=[c for c in cases if c['id'] in base.selected_ids('sensitivity')]
    random.Random(protocol()['seed']).shuffle(cases)
    variants=range(2) if stage=='development' else [verify_selection(run)[arm]['variant']]
    views=('reverse','remap') if stage=='sensitivity' else ('primary',)
    warm=dataset('development')[0]
    result=[{'id':f'warmup-{i}','case_id':warm['id'],'variant':variants[0],'view':'primary','phase':'warmup'} for i in range(3)]
    if stage=='development':result += [{'id':c['id'],'case_id':c['id'],'variant':0,'view':'primary','phase':'feasibility'} for c in dataset('development') if c['id'] in base.selected_ids('feasibility')]
    result += [{'id':c['id'],'case_id':c['id'],'variant':v,'view':view,'phase':'measured'} for v in variants for view in views for c in cases]
    return result

def verify_environment(env,arm):
    m=model(arm);r=runtime_lock()
    expected={'model_revision':m['revision'],'artifacts':{m['filename']:m['sha256']},'runtime_revision':m['runtime_revision'],'runtime_sha256':r['runtime_sha256'],'container_digest':r['container_digest'],'architecture':'linux/arm64','precision':'Q4_K_M','cpu_only':True,'cpus':4,'threads':4,'query_batch_size':1,'memory_limit_bytes':4294967296,'peak_includes_startup':True,'thinking':False,'oom':False,'verified':True}
    if any(env.get(k)!=v for k,v in expected.items()):raise ValueError('runtime/model identity or limits differ from frozen locks')
    if env.get('dependencies')!=r['dependencies']:raise ValueError('runtime dependency identity differs')
    if not e.sha(env.get('startup_log_sha256')) or any(not scoring.valid_time(env.get(k)) for k in ('readiness_seconds','idle_memory_bytes','peak_memory_bytes','cpu_time_seconds')):raise ValueError('runtime measurements missing')
    return env

def inputs(run,arm,planned,env,tokens):
    reqs=requests_for_arm(run,arm)
    lookup={(r['id'],r['variant'],r['view']):r for r in reqs}
    selected=[lookup[(j['case_id'],j['variant'],j['view'])] for j in planned]
    e.verify_tokens(tokens,selected,'qwen',env)
    return selected

def verify_response_record(record,alias,expected_input_tokens):
    response=record.get('response',{})
    usage=response.get('usage',{});timings=response.get('timings',{})
    if response.get('model')!=alias or usage.get('prompt_tokens')!=expected_input_tokens or timings.get('prompt_n')!=expected_input_tokens:
        raise ValueError('runtime model or complete input token count differs from preflight')
    if timings.get('cache_n')!=0 or usage.get('prompt_tokens_details',{}).get('cached_tokens')!=0:
        raise ValueError('cross-request prefix reuse detected or cache audit missing')

def audit_call(record,req,arm,tokens):
    if record.get('response') is not None:
        try:
            expected=next(t['tokens'] for t in tokens if t['payload_sha256']==req['payload_sha256'])
            verify_response_record(record,model(arm)['alias'],expected)
        except (ValueError,KeyError,TypeError,AttributeError,StopIteration) as error:
            record.update(status='error',fatal=True,error=str(error))
        response=record['response']
        if isinstance(response,dict) and any(c.get('finish_reason')=='length' for c in response.get('choices',[]) if isinstance(c,dict)):
            record.update(status='error',fatal=True,error='Qwen output truncated at token limit')
    if 'unknown Qwen operation' in record.get('error',''):
        record['fatal']=True
    return record

def run_stage(run,stage,arm,url=None,environment_path=None,tokens_path=None,unavailable=None):
    barrier(run,stage,arm)
    planned=jobs(run,stage,arm)
    stop=previous_stop(run,arm) or unavailable
    env=None;reqs=[];tokens=[]
    if not stop:
        if not url or not environment_path or not tokens_path:raise ValueError('measured endpoint/environment/tokens required')
        env=verify_environment(e.read(environment_path),arm)
        if env['endpoint']!=url:raise ValueError('endpoint differs')
        original=run/'development'/arm/'environment.json'
        if original.exists():
            prior=e.read(original)
            for key in ('model_revision','artifacts','runtime_revision','runtime_sha256','container_digest','dependencies','architecture','precision'):
                if env[key]!=prior[key]:raise ValueError('runtime changed after development')
        if not resources.capture(env)['verified']:raise ValueError('live resource audit failed')
        tokens=e.read(tokens_path);reqs=inputs(run,arm,planned,env,tokens)
    dest=run/stage/arm;dest.mkdir(parents=True,exist_ok=False)
    output=[{**j,'model_arm':arm,'status':'unattempted'} for j in planned]
    e.save_new(dest/'planned.json',output)
    if env:e.save_new(dest/'environment.json',env);e.save_new(dest/'tokens.json',tokens)
    started=now();deadline=time.monotonic()+remaining(run,arm);failures=0;active=None;call_start=None
    try:
        with (dest/'journal.jsonl').open('x') as journal:
            for index,row in enumerate(output):
                if stop:row['reason']=stop
                elif time.monotonic()>=deadline:stop=row['reason']='inference budget exhausted'
                else:
                    active=row;call_start=time.monotonic()
                    row.update(status='error',attempted=True,started_at=now(),payload_sha256=reqs[index]['payload_sha256'])
                    row.update(audit_call(base.http_call(url,reqs[index],'qwen',min(protocol()['request_seconds'],max(.001,deadline-time.monotonic()))),reqs[index],arm,tokens))
                    active=None
                    failures=failures+1 if row['status']=='error' else 0
                    if row['status']=='error':
                        observation=base.observe(env)
                        if observation.get('oom'):stop='container OOM'
                    if failures>=3:stop='three consecutive runtime failures'
                    if row.get('fatal'):stop='fatal input/mapping/cache verification failure'
                journal.write(json.dumps(row,allow_nan=False)+'\n');journal.flush()
    except BaseException as error:
        stop=f'{type(error).__name__}: {error}'
        if active is not None:active.update(status='error',error='interrupted in flight: '+stop,elapsed_ms=(time.monotonic()-call_start)*1000)
        for row in output:
            if row['status']=='unattempted':row['reason']=stop
        raise
    finally:
        e.save_new(dest/'rows.json',output)
        if env:
            final={**env,**base.observe(env),'rows_sha256':e.digest(dest/'rows.json')};e.save_new(dest/'resources-final.json',final)
            if final.get('oom'):stop='container OOM'
        completion={'started_at':started,'finished_at':now(),'model_arm':arm,'stage':stage,'planned':len(output),'valid':sum(r['status'] in ('selected','deferred') for r in output),'stop_reason':stop,'inference_seconds':sum(r.get('elapsed_ms',0) for r in output)/1000,'rows_sha256':e.digest(dest/'rows.json')}
        e.save_new(dest/'completion.json',completion)
    return completion

def select(run):
    verify_run(run)
    if not all(completed(run,'development',a) for a in ARMS):raise ValueError('both development arms must finish')
    choices={}
    for arm in ARMS:
        data=rows(run,'development',arm)
        choices[arm]=scoring.select_model(dataset('development'),[{'arm':arm,'variant':v,'rows':[r for r in data if r['phase']=='measured' and r['variant']==v]} for v in range(2)])
    choices['selected_at']=now();choices['scope']='development only'
    e.save_new(run/'selection.json',choices)
    e.save_new(run/'selection-freeze.json',{'selection_sha256':e.digest(run/'selection.json'),'rows':{a:e.digest(run/'development'/a/'rows.json') for a in ARMS},'environments':{a:e.digest(run/'development'/a/'environment.json') if (run/'development'/a/'environment.json').exists() else None for a in ARMS}})
    return {a:{k:choices[a][k] for k in ('variant','eligible')} for a in ARMS}

def gates(metric,telemetry,sensitivity):
    p=protocol();q=p['quality'];cost=p['cost'];ret=p['retention']
    complete=metric['total']==120 and metric['valid']==120 and metric['latency_samples']==120
    return {'complete':complete,'absolute_quality':complete and metric['raw_correct']>=q['raw_correct'] and metric['accepted_correct']>=q['accepted_correct'] and metric['wrong_accepted']<=q['wrong_accepted_max'] and metric['wrong_specialized']<=q['wrong_specialized_max'] and metric['invented_binding']<=q['invented_binding_max'],
            'cpu_cost':complete and telemetry.get('verified') is True and telemetry.get('oom') is False and telemetry['peak_memory_bytes']<=cost['peak_memory_bytes'] and telemetry['readiness_seconds']<=cost['readiness_seconds'] and metric['median_ms']<=cost['median_ms'] and metric['p95_ms']<=cost['p95_ms'],
            'retains_4b_quality':complete and metric['accepted_correct']>=ret['accepted_correct'] and metric['wrong_accepted']<=ret['wrong_accepted_max'],
            'stability':set(sensitivity)=={'reverse','remap'} and all(s['complete'] and s['changes']<=p['sensitivity_changes_max'] and s['new_wrong_specialized']==0 for s in sensitivity.values())}

def lifecycle_status(run,arm):
    observations=[];verified=True
    for stage in STAGES+('load',):
        if stage=='load' and not (run/'load'/arm).exists():continue
        path=run/'lifecycle'/(arm+'-'+stage)/'result.json'
        if not path.exists():verified=False;continue
        result=e.read(path);cleanup=result.get('cleanup',{});obs=result.get('final_resources',{})
        verified=verified and result.get('success') is True and cleanup.get('container_shutdown_verified') is True and cleanup.get('cli_reaped') is True and obs.get('verified') is True
        observations.append(obs)
    return {'verified':verified,'observations':observations}

def report(run):
    verify_run(run);selection=verify_selection(run);reference=e.read(run/'reference.json');result={'created_at':now(),'scope':protocol()['scope'],'selection':selection,'reference_sha256':e.digest(run/'reference.json'),'arms':{}}
    for arm in ARMS:
        primary=scoring.grade(dataset('heldout'),[r for r in rows(run,'primary',arm) if r['phase']=='measured'])
        subset=[c for c in dataset('heldout') if c['id'] in base.selected_ids('sensitivity')]
        sensitivities={v:scoring.sensitivity(primary,scoring.grade(subset,[r for r in rows(run,'sensitivity',arm) if r['phase']=='measured' and r['view']==v])) for v in ('reverse','remap')}
        observations=[]
        for stage in STAGES+('load',):
            path=run/stage/arm/'resources-final.json'
            if path.exists():
                obs=e.read(path)
                if obs['rows_sha256']!=e.digest(path.parent/'rows.json'):raise ValueError('resource record mismatches rows')
                observations.append(obs)
        lifecycle=lifecycle_status(run,arm);observations+=lifecycle['observations']
        telemetry={'verified':lifecycle['verified'] and len(observations)>=6 and all(o.get('verified') is True for o in observations),'cleanup_verified':lifecycle['verified'],'oom':any(o.get('oom') for o in observations),'peak_memory_bytes':max((o.get('peak_memory_bytes',0) for o in observations),default=0),'readiness_seconds':max((o.get('readiness_seconds',0) for o in observations),default=0)}
        checks=gates(primary,telemetry,sensitivities);eligible=selection[arm]['eligible'] is True and all(checks[k] for k in ('complete','absolute_quality','cpu_cost','stability'))
        lp=run/'load'/arm/'completion.json';load=e.read(lp) if lp.exists() else None
        load_pass=bool(load and load['valid']==200 and load['errors']==0 and load['latency_samples']==200 and load['p95_ms']<=protocol()['load']['p95_ms'] and load['elapsed_seconds']<=300)
        verdict='inconclusive' if not checks['complete'] or not telemetry['verified'] or not all(x['complete'] for x in sensitivities.values()) else 'candidate for fresh confirmation' if eligible and load_pass else 'load pending' if eligible and load is None else 'does not meet targets'
        result['arms'][arm]={'metrics':primary,'resources':telemetry,'sensitivities':sensitivities,'checks':checks,'load':load,'load_status':'complete' if load else 'required' if eligible else 'skipped: prerequisites failed','verdict':verdict,'remaining_budget_seconds':remaining(run,arm),'paired':{name:scoring.paired(primary,reference['metrics'][name]) for name in ('improved_rules','embeddings','qwen')}}
    return result

def run_load(run,arm,url,environment_path,tokens_path):
    barrier(run,'load',arm)
    current=report(run)['arms'][arm]
    if current['load_status']!='required':raise ValueError('load prerequisites not satisfied or already complete')
    env=verify_environment(e.read(environment_path),arm)
    if env['endpoint']!=url or not resources.capture(env)['verified']:raise ValueError('load endpoint/runtime audit failed')
    variant=verify_selection(run)[arm]['variant'];cases=dataset('heldout');random.Random(protocol()['seed']).shuffle(cases)
    planned=[{'id':f'load-{i}','case_id':cases[i%120]['id'],'variant':variant,'view':'primary','phase':'load'} for i in range(200)]
    tokens=e.read(tokens_path);reqs=inputs(run,arm,planned,env,tokens);dest=run/'load'/arm;dest.mkdir(parents=True,exist_ok=False)
    output=[{**j,'status':'unattempted'} for j in planned];e.save_new(dest/'planned.json',output);e.save_new(dest/'environment.json',env)
    started=time.monotonic();budget=remaining(run,arm);deadline=started+min(300,budget/2);stop=None
    def perform(index,sync):
        row=output[index]
        try:
            sync.wait(timeout=5)
            row.update(attempted=True,started_at=now(),payload_sha256=reqs[index]['payload_sha256'])
            call_start=time.monotonic()
            row.update(audit_call(base.http_call(url,reqs[index],'qwen',min(30,max(.001,deadline-time.monotonic()))),reqs[index],arm,tokens))
        except BaseException as error:
            row.update(status='error',error=f'{type(error).__name__}: {error}')
            if row.get('attempted'):row['elapsed_ms']=(time.monotonic()-call_start)*1000
        return row
    try:
        with ThreadPoolExecutor(max_workers=2) as pool, (dest/'journal.jsonl').open('x') as journal:
            for offset in range(0,200,2):
                if stop or time.monotonic()>=deadline:break
                sync=threading.Barrier(2)
                futures=[pool.submit(perform,i,sync) for i in (offset,offset+1)]
                for future in futures:
                    journal.write(json.dumps(future.result(),allow_nan=False)+'\n');journal.flush()
                if any(output[i]['status']=='error' for i in (offset,offset+1)):stop='load request error'
    except BaseException as error:
        stop=f'{type(error).__name__}: {error}'
        raise
    finally:
        for row in output:
            if row['status']=='unattempted':row['reason']=stop or 'load deadline exhausted'
        e.save_new(dest/'rows.json',output);e.save_new(dest/'resources-final.json',{**env,**base.observe(env),'rows_sha256':e.digest(dest/'rows.json')})
        latency=[r['http_ms'] for r in output if r['status'] in ('selected','deferred')]
        summary={'planned':200,'valid':len(latency),'errors':sum(r['status']=='error' for r in output),'unattempted':sum(r['status']=='unattempted' for r in output),'concurrency':2,'latency_samples':len(latency),'p95_ms':scoring.percentile(latency,.95),'elapsed_seconds':time.monotonic()-started,'stop_reason':stop,'rows_sha256':e.digest(dest/'rows.json')};e.save_new(dest/'completion.json',summary)
    return summary

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('command',choices=['prepare','select','report','unavailable']);parser.add_argument('--run',required=True,type=Path);parser.add_argument('--arm',choices=ARMS);parser.add_argument('--stage',choices=STAGES);parser.add_argument('--reason');a=parser.parse_args()
    if a.command=='prepare':result=prepare(a.run)
    elif a.command=='select':result=select(a.run)
    elif a.command=='unavailable':result=run_stage(a.run,a.stage,a.arm,unavailable=a.reason)
    else:
        value=report(a.run);dest=a.run/'reports'/now().replace(':','').replace('-','');dest.mkdir(parents=True);e.save_new(dest/'summary.json',value)
        result={arm:{'verdict':v['verdict'],'checks':v['checks'],'metrics':{k:v['metrics'][k] for k in ('raw_correct','accepted_correct','wrong_accepted','deferred','median_ms','p95_ms')}} for arm,v in value['arms'].items()}
    print(json.dumps(result,indent=2))
if __name__=='__main__':main()
