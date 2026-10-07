import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import evaluate as ev

class EvaluationTests(unittest.TestCase):
    def test_payload_changes_only_alias(self):
        case={'id':'test','input':{'query':'Count entities.'}}
        for arm in ev.ARMS:
            for variant in (0,1):
                for view in ('primary','reverse','remap'):
                    old=ev.base.request(case,'qwen',variant,view)
                    new=ev.request(case,arm,variant,view)
                    self.assertEqual(new['request']['payload']['model'],ev.model(arm)['alias'])
                    new['request']['payload']['model']=old['request']['payload']['model']
                    self.assertEqual(new['request'],old['request'])

    def test_native_input_and_cache_audit(self):
        record={'status':'selected','response':{'model':'test','usage':{'prompt_tokens':42,'prompt_tokens_details':{'cached_tokens':0}},'timings':{'prompt_n':42,'cache_n':0}}}
        ev.verify_response_record(record,'test',42)
        for section,key,value in [('usage','prompt_tokens',41),('timings','prompt_n',41),('timings','cache_n',1)]:
            changed=copy.deepcopy(record);changed['response'][section][key]=value
            with self.assertRaises(ValueError):ev.verify_response_record(changed,'test',42)

    def test_malformed_or_truncated_response_is_audited(self):
        req={'payload_sha256':'a'*64};tokens=[{'payload_sha256':'a'*64,'tokens':42}]
        response={'model':ev.model(ev.ARMS[0])['alias'],'usage':{'prompt_tokens':42,'prompt_tokens_details':{'cached_tokens':0}},'timings':{'prompt_n':42,'cache_n':0},'choices':[{'finish_reason':'length'}]}
        record={'status':'error','response':response,'error':'Qwen incomplete completion'}
        self.assertTrue(ev.audit_call(record,req,ev.ARMS[0],tokens).get('fatal'))
        record={'status':'error','response':copy.deepcopy(response),'error':'malformed JSON'}
        record['response']['choices'][0]['finish_reason']='stop';record['response']['usage']['prompt_tokens']=41
        self.assertTrue(ev.audit_call(record,req,ev.ARMS[0],tokens).get('fatal'))

    def test_cleanup_is_required_for_report_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            run=Path(temp)
            for stage in ev.STAGES:
                dest=run/'lifecycle'/(ev.ARMS[0]+'-'+stage);dest.mkdir(parents=True)
                ev.e.save_new(dest/'result.json',{'success':True,'cleanup':{'container_shutdown_verified':True,'cli_reaped':True},'final_resources':{'verified':True}})
            self.assertTrue(ev.lifecycle_status(run,ev.ARMS[0])['verified'])
            target=run/'lifecycle'/(ev.ARMS[0]+'-sensitivity')/'result.json'
            record=ev.e.read(target);record['cleanup']['container_shutdown_verified']=False;target.write_text(json.dumps(record))
            self.assertFalse(ev.lifecycle_status(run,ev.ARMS[0])['verified'])

    def test_quality_and_cost_are_separate(self):
        m={'total':120,'valid':120,'latency_samples':120,'raw_correct':111,'accepted_correct':111,'wrong_accepted':8,'wrong_specialized':3,'invented_binding':1,'median_ms':100,'p95_ms':200}
        res={'verified':True,'oom':False,'peak_memory_bytes':1000000,'readiness_seconds':2}
        sensitivity={x:{'complete':True,'changes':0,'new_wrong_specialized':0} for x in ('reverse','remap')}
        g=ev.gates(m,res,sensitivity)
        self.assertTrue(g['retains_4b_quality']);self.assertTrue(g['cpu_cost']);self.assertFalse(g['absolute_quality'])
        m['valid']=119
        self.assertFalse(ev.gates(m,res,sensitivity)['cpu_cost'])

    def test_diagnostic_development_choice_cannot_advance_to_load(self):
        with tempfile.TemporaryDirectory() as temp:
            run=Path(temp)
            ev.e.save_new(run/'reference.json',{'metrics':{name:{} for name in ('improved_rules','embeddings','qwen')}})
            selection={a:{'variant':0,'eligible':False} for a in ev.ARMS}
            passing={k:True for k in ('complete','absolute_quality','cpu_cost','stability','retains_4b_quality')}
            with patch.object(ev,'verify_run'),patch.object(ev,'verify_selection',return_value=selection),patch.object(ev,'gates',return_value=passing),patch.object(ev.scoring,'paired',return_value={}):
                report=ev.report(run)
            for result in report['arms'].values():
                self.assertEqual(result['load_status'],'skipped: prerequisites failed')

    def test_warmups_do_not_expand_measured_cohort(self):
        with patch.object(ev,'verify_selection',return_value={a:{'variant':1} for a in ev.ARMS}):
            for stage,count in [('development',120),('primary',120),('sensitivity',48)]:
                js=ev.jobs(Path('/unused'),stage,ev.ARMS[0])
                self.assertEqual(sum(j['phase']=='measured' for j in js),count)
                self.assertEqual(sum(j['phase']=='warmup' for j in js),3)

    def test_interruption_retains_attempt_and_unattempted_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            run=Path(temp);envfile=run/'env.json';tokenfile=run/'tokens.json'
            envfile.write_text(json.dumps({'endpoint':'http://127.0.0.1:18095/v1/chat/completions'}));tokenfile.write_text('[]')
            planned=[{'id':str(i),'case_id':str(i),'variant':0,'view':'primary','phase':'measured'} for i in range(3)]
            reqs=[{'payload_sha256':'a'*64} for _ in planned]
            with patch.object(ev,'barrier'),patch.object(ev,'jobs',return_value=planned),patch.object(ev,'verify_environment',side_effect=lambda x,a:x),patch.object(ev.resources,'capture',return_value={'verified':True}),patch.object(ev,'inputs',return_value=reqs),patch.object(ev,'remaining',return_value=100),patch.object(ev.base,'http_call',side_effect=KeyboardInterrupt()),patch.object(ev.base,'observe',return_value={'verified':True,'oom':False}):
                with self.assertRaises(KeyboardInterrupt):ev.run_stage(run,'development',ev.ARMS[0],'http://127.0.0.1:18095/v1/chat/completions',envfile,tokenfile)
            output=json.loads((run/'development'/ev.ARMS[0]/'rows.json').read_text())
            self.assertEqual([r['status'] for r in output],['error','unattempted','unattempted'])
            self.assertTrue(output[0]['attempted']);self.assertGreaterEqual(output[0]['elapsed_ms'],0)
            self.assertIn('KeyboardInterrupt',ev.previous_stop(run,ev.ARMS[0]))

    def test_persistent_stop_makes_no_http_calls(self):
        with tempfile.TemporaryDirectory() as temp:
            run=Path(temp)
            with patch.object(ev,'barrier'),patch.object(ev,'jobs',return_value=[{'id':'one','case_id':'one'}]),patch.object(ev,'previous_stop',return_value='container OOM'),patch.object(ev.base,'http_call') as call:
                result=ev.run_stage(run,'primary',ev.ARMS[0])
            call.assert_not_called();self.assertEqual(result['valid'],0);self.assertEqual(result['stop_reason'],'container OOM')

if __name__=='__main__':unittest.main()
