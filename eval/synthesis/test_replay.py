"""Offline invariants for the bounded real-synthesis replay; no model launches."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import context_preflight

spec = importlib.util.spec_from_file_location('replay', Path(__file__).with_name('replay.py'))
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)


def frozen_files(directory, empty=False, failed=False, long=False):
    directory = Path(directory)
    ids = [f'Q{i:02d}' for i in range(13)]
    inputs, captures, statuses = [], [], []
    for ident in ids:
        source = {'id': ident, 'query': 'What does the evidence establish?',
                  'summaries': [] if empty else [{'community_id': 'c', 'summary': 'Known detail.'}], 'total_entities': 0 if empty else 1}
        text = 'X' * 8200 if long else 'Query: What does the evidence establish?\nCluster 1:\nKnown detail.'
        request = {'SystemPrompt': 'Actual synthesis instructions.', 'UserPrompt': text, 'MaxTokens': 500, 'Temperature': .3}
        calls = [] if empty else [{'llm_request': request, 'messages': [{'role': 'system', 'content': request['SystemPrompt']}, {'role': 'user', 'content': text}]}]
        inputs.append(source)
        captures.append({'id': ident, 'mode': 'capture_only', 'input': source, 'calls': calls})
        statuses.append({'id': ident, 'status': 'error' if failed else 'ok', 'error': 'retrieval unavailable' if failed else None})
    input_path, prompt_path, status_path = directory/'input.jsonl', directory/'prompts.jsonl', directory/'statuses.json'
    input_path.write_text(''.join(json.dumps(i)+'\n' for i in inputs))
    prompt_path.write_text(''.join(json.dumps(i)+'\n' for i in captures))
    status_path.write_text(json.dumps(statuses))
    driver = directory/'driver'
    driver.write_text('offline-test-placeholder')
    paths = {'input': input_path, 'prompts': prompt_path, 'driver': driver,
             'acquiredstatuses': status_path, 'instructions_dataset': replay.INSTRUCTIONS}
    freeze = {'ids': ids, 'trials': 2, 'arm_order': replay.ARMS,
              'hashes': {k: replay.metal.sha256(v) for k,v in paths.items()},
              'context_preflight_policy': 'exact-runtime-tokenization-v1'}
    freeze_path = directory/'freeze.json'
    freeze_path.write_text(json.dumps(freeze))
    return SimpleNamespace(input=input_path,prompts=prompt_path,freeze=freeze_path,driver=driver,
                           acquiredstatuses=status_path,output=directory/'out',validate=False)


class FrozenReplayTests(unittest.TestCase):
    def test_changed_frozen_input_cannot_reach_runtime_or_create_results(self):
        with tempfile.TemporaryDirectory() as directory:
            args=frozen_files(directory)
            args.input.write_text(args.input.read_text()+'\n')
            with patch.object(replay,'verify_native') as verify, patch.object(replay,'launch_native') as launch:
                with self.assertRaisesRegex(ValueError,'input hash mismatch'):
                    replay.run(args)
            verify.assert_not_called();launch.assert_not_called()
            self.assertFalse(args.output.exists())

    def test_gate_sees_exact_actual_userprompt_without_gold_or_capture_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            args=frozen_files(directory)
            _,cases,_,dataset=replay.load_frozen(args)
            case=cases['Q00']
            case['gold']={'answer':'GOLD_SENTINEL'}
            case['capture']['grader_notes']='GRADING_SENTINEL'
            response={'choices':[{'finish_reason':'stop','message':{'content':'{"route":"allow"}'}}]}
            with patch.object(replay.gates,'http_call',return_value=response) as call:
                result=replay.gate_call(dataset,case,'qwen_json',{'qwen_json':'qwen'})
            payload=call.call_args.args[1]
            text=payload['messages'][1]['content']
            self.assertNotIn('SENTINEL',text)
            sent=json.loads(text)
            self.assertEqual(sent['passages'][0]['text'],case['capture']['calls'][0]['llm_request']['UserPrompt'])
            self.assertEqual(payload['response_format']['json_schema']['schema']['properties']['route']['enum'],['allow','defer'])
            self.assertEqual(result['status'],'ok')

    def test_acquisition_errors_are_not_semantic_deferrals_and_all_rows_remain(self):
        with tempfile.TemporaryDirectory() as directory:
            args=frozen_files(directory,empty=True,failed=True)
            with patch.object(replay,'verify_native') as verify,patch.object(replay,'launch_native') as launch:
                result=replay.run(args)
            verify.assert_not_called();launch.assert_not_called()
            self.assertEqual(len(result['rows']),13*2*3*2)
            self.assertEqual({r['status'] for r in result['rows']},{'acquisition_error'})
            self.assertTrue(all(r['action'] is None for r in result['rows']))
            self.assertEqual(result['matched_ids'],[])
            self.assertEqual(result['status'],'no_usable_visible_evidence')

    def test_profile_failure_excludes_same_ids_in_all_arms_without_truncation(self):
        with tempfile.TemporaryDirectory() as directory:
            args=frozen_files(directory,long=True)
            _,cases,_,_=replay.load_frozen(args)
            self.assertGreater(cases['Q00']['state_bytes'],8192)
            self.assertEqual(len(cases['Q00']['input']['passages'][0]['text']),8200)
            with patch.object(replay,'verify_native') as verify:
                result=replay.run(args)
            verify.assert_not_called()
            self.assertEqual({r['status'] for r in result['rows']},{'profile_error'})
            self.assertEqual(result['matched_ids'],[])

    def test_empty_model_input_gate_defers_but_control_keeps_actual_driver_behavior(self):
        with tempfile.TemporaryDirectory() as directory:
            args=frozen_files(directory,empty=True)
            _,cases,_,dataset=replay.load_frozen(args)
            case=cases['Q00']
            attempt={'status':'ok','driver_duration_ms':0.2,'record':{'synthesis_outcome':{'Answer':'','Degraded':False}}}
            with patch.object(replay.gates,'http_call') as model,patch.object(replay,'driver_call',return_value=attempt) as driver:
                gate_row={'arm':'kev'}
                replay.execute_row(gate_row,case,dataset,{'kev':'kev'},args.driver,'http://unused','model',args.output)
                self.assertEqual(gate_row['status'],'deferred')
                driver.assert_not_called()
                control_row={'arm':'no_gate'}
                replay.execute_row(control_row,case,dataset,{},args.driver,'http://unused','model',args.output)
            model.assert_not_called()
            self.assertEqual(driver.call_count,1)
            self.assertEqual(control_row['status'],'empty_no_synthesis')

    def test_existing_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            args=frozen_files(directory,empty=True,failed=True)
            args.output.mkdir();evidence=args.output/'replay.json';evidence.write_text('preserve me')
            with self.assertRaises(FileExistsError):replay.run(args)
            self.assertEqual(evidence.read_text(),'preserve me')

    def test_driver_timeout_is_preserved_as_failure_without_invented_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            args=frozen_files(directory)
            _,cases,_,_=replay.load_frozen(args)
            with patch.object(replay.subprocess,'run',side_effect=subprocess.TimeoutExpired(['driver'],22)):
                result=replay.driver_call(args.driver,cases['Q00']['source'],'http://unused','model',args.output)
            self.assertEqual(result['status'],'error')
            self.assertIsNone(result['driver_duration_ms'])
            self.assertIn('TimeoutExpired',result['error'])
            self.assertTrue((args.output/'attempt.json').exists())

    def test_fatal_context_preflight_stops_before_warmup_and_keeps_remaining_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            args=frozen_files(directory)
            locks={'qwen_json':{'alias':'qwen'},'kev':{'alias':'kev'}}
            with patch.object(replay,'verify_native',return_value=({},locks)), \
                    patch.object(replay.gates,'command',return_value='1024'), \
                    patch.object(replay,'launch_native'), \
                    patch.object(context_preflight,'check',side_effect=RuntimeError('tokenizer failed')), \
                    patch.object(replay,'cleanup',return_value={'errors':[]}), \
                    patch.object(replay,'gate_call') as gate, patch.object(replay,'driver_call') as driver:
                with self.assertRaisesRegex(RuntimeError,'tokenizer failed'):
                    replay.run(args)
            gate.assert_not_called();driver.assert_not_called()
            preserved=json.loads((args.output/'replay.json').read_text())
            self.assertEqual(preserved['status'],'failed')
            self.assertEqual(len(preserved['rows']),156)
            self.assertEqual({r['status'] for r in preserved['rows']},{'not_run'})

    def test_cleanup_attempts_each_owned_process_and_checks_all_ports_after_failure(self):
        first,second=Mock(pid=1),Mock(pid=2)
        first.poll.return_value=0;second.poll.return_value=None
        def stop(children):
            if children[0] is second:raise PermissionError('test denial')
        with patch.object(replay.metal,'stop',side_effect=stop) as stop_mock,patch.object(replay,'port_is_listening',side_effect=[False,True,False]):
            result=replay.cleanup([('first',first),('second',second)])
        self.assertEqual(stop_mock.call_count,2)
        self.assertEqual(len(result['ports_closed']),3)
        self.assertGreaterEqual(len(result['errors']),3)


if __name__=='__main__':
    unittest.main()
