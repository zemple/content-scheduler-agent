import copy
import tempfile
import unittest
from pathlib import Path

from scheduling_agent.agent import Agent
from scheduling_agent.domain import Request, ValidationError
from scheduling_agent.models import MockModel
from scheduling_agent.store import Store
from scheduling_agent.tools import Tools, load_world
from tests.test_scheduler import ROOT

def at(hour, day=6):
    return f'2026-11-{day:02d}T{hour:02d}:00:00+08:00'

def item(mid='M01', **extra):
    return {'material_id': mid, 'requested_time': at(18), 'hard_time': False, **extra}

class ConstraintTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'drafts.sqlite')
        self.world = load_world(ROOT / 'data')
        self.world['schedule'] = [{'channel':'video', 'time':at(18)}]

    def tearDown(self):
        self.tmp.cleanup()

    def tools(self, items=None, confirmations=None):
        request = Request.parse({'request_id':'constraints', 'items':items or [item()], 'confirmations':confirmations or {}})
        return Tools(request, self.world, self.store)

    def test_only_earlier_rejects_free_later_time(self):
        t = self.tools([item(adjustment='earlier_only', not_before=at(16), deadline=at(20))])
        result = t.call('save_draft', {'material_id':'M01','suggested_time':at(19)})
        self.assertFalse(result['ok'])
        self.assertEqual(result['error'], 'time_constraint_violation')
        self.assertIn('later_than_preferred', result['violations'])
        self.assertEqual(self.store.drafts(), [])

    def test_earlier_permitted_and_query_distinguishes_constraints(self):
        t = self.tools([item(adjustment='earlier_only', not_before=at(16), deadline=at(20))])
        slots = t.call('query_slots', {'material_id':'M01','start_time':at(17),'count':4})['slots']
        self.assertTrue(slots[0]['available'])
        self.assertTrue(slots[1]['occupied'])
        self.assertFalse(slots[2]['occupied'])
        self.assertIn('later_than_preferred', slots[2]['constraint_violations'])
        self.assertIn('at_or_after_deadline', slots[3]['constraint_violations'])
        self.assertTrue(t.call('save_draft', {'material_id':'M01','suggested_time':at(17)})['ok'])

    def test_only_later_rejects_earlier(self):
        t = self.tools([item(adjustment='later_only')])
        self.assertFalse(t.call('save_draft',{'material_id':'M01','suggested_time':at(17)})['ok'])

    def test_window_inclusive_deadline_exclusive(self):
        t = self.tools([item(not_before=at(16), not_after=at(19), deadline=at(20))])
        slots = t.call('query_slots',{'material_id':'M01','start_time':at(15),'count':6})['slots']
        self.assertFalse(slots[0]['available'])
        self.assertTrue(slots[1]['available'])
        self.assertTrue(slots[4]['available'])
        self.assertFalse(slots[5]['available'])

    def test_explicit_time_confirmation_does_not_override_direction(self):
        t = self.tools([item(hard_time=True, adjustment='earlier_only')], {'M01':at(19)})
        self.assertFalse(t.call('save_draft',{'material_id':'M01','suggested_time':at(19)})['ok'])

    def test_hard_time_alternative_still_pending_within_window(self):
        t = self.tools([item(hard_time=True, adjustment='earlier_only', not_before=at(16))])
        result = t.call('save_draft',{'material_id':'M01','suggested_time':at(17)})
        self.assertEqual(result['draft']['confirmation_status'],'pending')

    def test_flexible_item_cannot_take_other_items_fixed_slot(self):
        self.world['schedule'] = []
        t = self.tools([item('M05', not_before=at(18), not_after=at(20)), item('M01', hard_time=True)])
        result = t.call('save_draft',{'material_id':'M05','suggested_time':at(18)})
        self.assertFalse(result['ok'])
        self.assertIn('reserved_for_fixed_item:M01',result['violations'])
        self.assertTrue(t.call('save_draft',{'material_id':'M01','suggested_time':at(18)})['ok'])
        self.assertTrue(t.call('save_draft',{'material_id':'M05','suggested_time':at(19)})['ok'])

    def test_same_time_different_channel_is_allowed(self):
        self.world['materials']['M05']['channel'] = 'social'
        t = self.tools([item('M05'), item('M01',hard_time=True)])
        self.assertTrue(t.call('save_draft',{'material_id':'M05','suggested_time':at(18)})['ok'])

    def test_invalid_constraints_rejected(self):
        for extra in [dict(adjustment='random'),dict(not_before=at(20),not_after=at(16)),
                      dict(not_before=at(20),deadline=at(20)),dict(not_after='tomorrow'),
                      dict(deadline='2026-11-06T20:00:00'),dict(adjustment=17)]:
            with self.subTest(extra=extra),self.assertRaises(ValidationError):
                self.tools([item(**extra)])

    def test_rule_sensitive_request_fingerprint(self):
        self.tools([item(not_after=at(20))])
        with self.assertRaises(ValidationError):
            self.tools([item(not_after=at(19))])

    def test_mock_searches_earlier_instead_of_forbidden_later_slot(self):
        t = self.tools([item(adjustment='earlier_only',not_before=at(16),deadline=at(20))])
        result, events = Agent(t,MockModel()).run()
        self.assertEqual(result['task_status'],'completed')
        self.assertEqual(result['items'][0]['suggested_time'],'2026-11-06T09:00:00+00:00')
        self.assertGreaterEqual(sum(e['tool']=='query_slots' for e in events),2)

    def test_mock_preserves_fixed_slot_even_flexible_item_listed_first(self):
        self.world['schedule']=[]
        t=self.tools([item('M05',not_before=at(18),not_after=at(20)),item('M01',hard_time=True)])
        result,events=Agent(t,MockModel()).run()
        self.assertEqual(result['task_status'],'completed')
        saved={d['material_id']:d['suggested_time'] for d in self.store.drafts()}
        self.assertEqual(saved['M01'],'2026-11-06T10:00:00+00:00')
        self.assertEqual(saved['M05'],'2026-11-06T11:00:00+00:00')

    def test_new_dataset_has_20_materials_7_days_3_channels(self):
        world=load_world(ROOT/'data')
        self.assertEqual(len(world['materials']),20)
        self.assertEqual({s['time'][:10] for s in world['schedule']}, {f'2026-11-{d:02d}' for d in range(1,8)})
        self.assertEqual({s['channel'] for s in world['schedule']},{'video','social','community'})

    def test_search_at_datetime_boundary_returns_incomplete(self):
        extreme='0001-01-01T00:00:00+00:00'
        self.world['schedule']=[{'channel':'video','time':extreme}]
        t=self.tools([item(requested_time=extreme,adjustment='earlier_only')])
        result,events=Agent(t,MockModel()).run()
        self.assertEqual(result['task_status'],'incomplete')
        self.assertEqual(self.store.drafts(),[])

    def test_final_recheck_includes_newly_viable_fixed_item(self):
        self.world['schedule']=[]
        self.world['materials']['M01']['rights_confirmed']=False
        t=self.tools([item('M01',hard_time=True),item('M05')])
        saved=t.call('save_draft',{'material_id':'M05','suggested_time':at(18)})
        self.assertTrue(saved['ok'])
        self.world['materials']['M01']['rights_confirmed']=True
        validation=t.validate_saved(saved['draft'])
        self.assertFalse(validation['valid'])
        self.assertIn('reserved_for_fixed_item:M01',validation['problems'])
