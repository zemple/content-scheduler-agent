import copy
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from scheduling_agent.agent import Agent
from scheduling_agent.models import Action, MockModel, ChatModel, ModelError
from scheduling_agent.store import Store
from scheduling_agent.tools import Tools, load_world
from tests.test_scheduler import ROOT, TIME, ALT, request


class ScriptedModel:
    mode = 'test_double'
    model_name = 'scripted-test-only'

    def __init__(self, actions):
        self.actions = iter(actions)
        self.seen = []

    def decide(self, req, events):
        self.seen.append(copy.deepcopy(events))
        return next(self.actions)


class LoopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.world = load_world(ROOT / 'data')
        self.store = Store(Path(self.tmp.name) / 's.sqlite')

    def tearDown(self):
        self.tmp.cleanup()

    def run_agent(self, req=None, model=None, max_steps=12, failures=None):
        t = Tools(req or request(), self.world, self.store, failures)
        agent = Agent(t, model or MockModel(), max_steps)
        return agent.run()

    def test_normal(self):
        self.world['schedule'] = []
        result, trace = self.run_agent()
        self.assertEqual(result['task_status'], 'completed')
        self.assertEqual(result['items'][0]['draft_status'], 'saved')
        self.assertEqual(trace[-1]['tool'], 'finish')
        self.assertEqual(result['mode'], 'mock')

    def test_conflict_changes_action_and_requires_confirmation(self):
        result, trace = self.run_agent()
        self.assertEqual(result['task_status'], 'awaiting_confirmation')
        query = [e for e in trace if e['tool'] == 'query_slots']
        self.assertGreaterEqual(len(query), 2)
        self.assertFalse(query[0]['observation']['slots'][0]['available'])
        self.assertEqual(result['items'][0]['suggested_time'], '2026-11-01T11:00:00+00:00')
        self.assertEqual(trace[query[0]['step'] - 1]['next_action'], 'query_slots')

    def test_changed_occupancy_changes_suggestion(self):
        first, _ = self.run_agent()
        self.world['schedule'].append({'channel': 'video', 'time': ALT})
        second, _ = self.run_agent(request('r2'))
        self.assertNotEqual(first['items'][0]['suggested_time'], second['items'][0]['suggested_time'])

    def test_missing_and_blocked(self):
        for mid, expected in [('M02', 'needs_input'), ('M03', 'blocked'), ('NO', 'needs_input')]:
            with self.subTest(mid=mid):
                result, _ = self.run_agent(request(mid, mid))
                self.assertEqual(result['task_status'], expected)
                self.assertEqual(result['items'][0]['draft_status'], 'not_saved')

    def test_save_failure_is_not_completed(self):
        result, trace = self.run_agent(failures={'save_draft': -1})
        self.assertEqual(result['task_status'], 'incomplete')
        self.assertEqual(result['items'][0]['draft_status'], 'failed')
        self.assertEqual(self.store.drafts(), [])

    def test_saved_draft_revalidated_when_schedule_changes(self):
        self.world['schedule'] = []
        first, _ = self.run_agent()
        self.world['schedule'].append({'channel': 'video', 'time': TIME})
        second, _ = self.run_agent()
        self.assertEqual(second['task_status'], 'incomplete')
        self.assertEqual(second['items'][0]['draft_status'], 'saved')
        self.assertEqual(second['items'][0]['confirmation_status'], 'needs_revalidation')
        self.assertIn('saved_slot_now_conflicts', second['items'][0]['uncompleted_reasons'])
        self.assertEqual(len(self.store.drafts()), 1)

    def test_saved_draft_revalidated_when_policy_changes(self):
        self.world['schedule'] = []
        self.run_agent()
        self.world['materials']['M01']['claims'] = ['效率提升80%']
        second, _ = self.run_agent()
        self.assertEqual(second['task_status'], 'blocked')
        self.assertEqual(second['items'][0]['confirmation_status'], 'needs_revalidation')
        self.assertIn('saved_material_no_longer_approved', second['items'][0]['uncompleted_reasons'])

    def test_unsaved_alternative_preserves_confirmation_boundary(self):
        result, _ = self.run_agent(failures={'save_draft': -1})
        item = result['items'][0]
        self.assertIn('time_change', item['pending_items'])
        self.assertIn('save_failed', item['uncompleted_reasons'])

    def test_no_slot_explains_checked_window(self):
        from datetime import datetime, timedelta
        self.world['schedule'] = [{'channel': 'video', 'time': (datetime.fromisoformat(TIME) + timedelta(hours=i)).isoformat()} for i in range(10)]
        result, _ = self.run_agent()
        self.assertIn('no_available_slot_in_checked_window', result['items'][0]['uncompleted_reasons'])

    def test_read_failure_is_visible(self):
        result, trace = self.run_agent(failures={'read_material': -1})
        self.assertEqual(result['task_status'], 'incomplete')
        self.assertEqual(result['items'][0]['review_status'], 'not_checked')
        self.assertEqual(trace[0]['observation']['error'], 'tool_unavailable')

    def test_limit_has_explicit_status(self):
        result, trace = self.run_agent(max_steps=1)
        self.assertEqual(result['termination'], 'step_limit')
        self.assertEqual(result['task_status'], 'incomplete')
        self.assertEqual(len(trace), 1)

    def test_model_observes_previous_result(self):
        m = ScriptedModel([Action('read_material', {'material_id': 'M01'}, 'Read metadata'),
                           Action('finish', {}, 'Stop without completing')])
        result, _ = self.run_agent(model=m)
        self.assertEqual(m.seen[1][0]['observation']['material']['title'], '字幕功能演示')
        self.assertEqual(result['task_status'], 'incomplete')

    def test_early_finish_cannot_forge_saved_status(self):
        result, _ = self.run_agent(model=ScriptedModel([Action('finish', {}, 'Everything published and saved')]))
        self.assertEqual(result['task_status'], 'incomplete')
        self.assertEqual(result['items'][0]['draft_status'], 'not_saved')
        self.assertEqual(result['items'][0]['publication_status'], 'not_published')

    def test_premature_finish_returns_observation_then_model_can_continue(self):
        m=ScriptedModel([
            Action('check_rules',{'material_id':'M01'},'Check rules'),
            Action('finish',{},'Other material blocked, stop too early'),
            Action('query_slots',{'material_id':'M01','start_time':ALT,'count':1},'Observe unfinished item and query slot'),
            Action('save_draft',{'material_id':'M01','suggested_time':ALT},'Save pending proposal'),
            Action('finish',{},'All feasible work done')])
        result,trace=self.run_agent(model=m)
        self.assertFalse(trace[1]['observation']['ok'])
        self.assertEqual(trace[1]['observation']['error'],'premature_finish')
        self.assertEqual(m.seen[2][1]['observation']['unfinished_materials'],['M01'])
        self.assertEqual(result['items'][0]['draft_status'],'saved')

    def test_finish_uses_latest_slot_observation(self):
        from scheduling_agent.domain import Request
        self.world['schedule']=[]
        req=Request.parse({'request_id':'slot-changed','items':[
            {'material_id':'M01','requested_time':ALT,'hard_time':False,'not_before':ALT,'not_after':ALT},
            {'material_id':'M05','requested_time':ALT,'hard_time':False}]})
        m=ScriptedModel([
            Action('check_rules',{'material_id':'M01'},'Check'),
            Action('query_slots',{'material_id':'M01','start_time':ALT,'count':1},'Inspect'),
            Action('save_draft',{'material_id':'M05','suggested_time':ALT},'Another item takes slot'),
            Action('query_slots',{'material_id':'M01','start_time':ALT,'count':1},'Recheck unavailable'),
            Action('finish',{},'No remaining permitted slot')])
        result,trace=self.run_agent(req,model=m,max_steps=5)
        self.assertEqual(result['termination'],'finished')
        self.assertTrue(trace[-1]['observation']['ok'])
        self.assertIsNone(result['items'][0]['suggested_time'])

    def test_model_exception_is_reported(self):
        class Broken(ScriptedModel):
            def decide(self, req, events):
                raise ModelError('upstream_unavailable')
        result, trace = self.run_agent(model=Broken([]))
        self.assertEqual(result['termination'], 'model_error')
        self.assertEqual(result['task_status'], 'incomplete')
        self.assertEqual(trace[0]['observation']['error'], 'upstream_unavailable')

    def test_malformed_tool_name_is_model_error(self):
        result, trace = self.run_agent(model=ScriptedModel([Action([], {}, 'Malformed upstream tool name')]))
        self.assertEqual(result['termination'], 'model_error')
        self.assertEqual(trace[0]['observation']['error'], 'invalid_model_action')

    def test_changed_material_channel_invalidates_historical_draft(self):
        self.world['schedule'] = []
        self.run_agent()
        self.world['materials']['M01']['channel'] = 'social'
        result, _ = self.run_agent()
        self.assertEqual(result['task_status'], 'incomplete')
        self.assertIn('saved_channel_changed', result['items'][0]['uncompleted_reasons'])

    def test_multi_material_keeps_successful_result(self):
        from scheduling_agent.domain import Request
        req = Request.parse({'request_id': 'batch', 'items': [
            {'material_id': 'M01', 'requested_time': ALT, 'hard_time': True},
            {'material_id': 'M03', 'requested_time': TIME, 'hard_time': True}]})
        result, _ = self.run_agent(req, max_steps=16)
        self.assertEqual(result['task_status'], 'partial')
        self.assertEqual(result['items'][0]['draft_status'], 'saved')
        self.assertEqual(result['items'][1]['status'], 'blocked')

    def test_two_pending_proposals_in_one_request_do_not_collide(self):
        from scheduling_agent.domain import Request
        req = Request.parse({'request_id': 'two-pending', 'items': [
            {'material_id': mid, 'requested_time': TIME, 'hard_time': True} for mid in ['M01', 'M05']]})
        result, _ = self.run_agent(req)
        self.assertEqual(result['task_status'], 'awaiting_confirmation')
        self.assertNotEqual(result['items'][0]['suggested_time'], result['items'][1]['suggested_time'])

    def test_save_failure_recovery_is_observed(self):
        args = {'material_id': 'M01', 'suggested_time': ALT}
        m = ScriptedModel([Action('save_draft', args, 'Try draft'), Action('save_draft', args, 'Retry observed transient failure'), Action('finish', {}, 'Saved')])
        result, trace = self.run_agent(model=m, failures={'save_draft': 1})
        self.assertFalse(trace[0]['observation']['ok'])
        self.assertTrue(trace[1]['observation']['ok'])
        self.assertEqual(result['items'][0]['draft_status'], 'saved')


class APITests(unittest.TestCase):
    def test_native_function_call_and_tool_observation_round_trip(self):
        bodies = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                bodies.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                name = 'read_material' if len(bodies) == 1 else 'finish'
                args = {'material_id': 'M01', 'reason': 'Read input'} if len(bodies) == 1 else {'reason': 'Observed metadata'}
                data = json.dumps({'id': 'response-test', 'model': 'fake-http', 'usage': {'total_tokens': 1}, 'choices': [{'message': {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'c' + str(len(bodies)), 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]}}]}).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(data)
            def log_message(self, *args):
                pass
        server = HTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            m = ChatModel('test-key', 'test-model', f'http://127.0.0.1:{server.server_port}/v1', timeout=2)
            action = m.decide(request(), [])
            self.assertEqual(action.tool, 'read_material')
            events = [{'step': 1, 'tool': action.tool, 'arguments': action.arguments, 'reason': action.reason,
                       'observation': {'ok': True, 'material': {'title': 'changed'}}, 'model_metadata': action.metadata}]
            second = m.decide(request(), events)
            self.assertEqual(second.tool, 'finish')
            messages = bodies[1]['messages']
            self.assertEqual(messages[-1]['role'], 'tool')
            self.assertEqual(json.loads(messages[-1]['content'])['material']['title'], 'changed')
            self.assertEqual(messages[-1]['tool_call_id'], messages[-2]['tool_calls'][0]['id'])
            events.append({'step':2,'tool':'finish','arguments':{},'reason':second.reason,
                           'observation':{'ok':False,'error':'premature_finish','unfinished_materials':['M01']},
                           'model_metadata':second.metadata})
            m.decide(request(), events)
            self.assertEqual(json.loads(bodies[-1]['messages'][-1]['content'])['error'],'premature_finish')
        finally:
            server.shutdown()
            server.server_close()
            worker.join()

    def test_invalid_model_config(self):
        for key, model, base in [('', 'x', 'https://api.openai.com/v1'), ('secret', '', 'https://api.openai.com/v1'), ('secret', 'x', 'http://public.example/v1')]:
            with self.subTest(base=base), self.assertRaises(ModelError):
                ChatModel(key, model, base)
