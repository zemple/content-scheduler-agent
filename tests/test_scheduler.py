import copy
import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from scheduling_agent.domain import Request, ValidationError
from scheduling_agent.store import Store
from scheduling_agent.tools import Tools

ROOT = Path(__file__).resolve().parents[1]
TIME = '2026-11-01T18:00:00+08:00'
ALT = '2026-11-01T19:00:00+08:00'


def request(rid='r1', mid='M01', time=TIME, hard=True, confirmations=None):
    return Request.parse({'request_id': rid, 'items': [
        {'material_id': mid, 'requested_time': time, 'hard_time': hard}],
        'confirmations': confirmations or {}})


class BusinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / 'state.sqlite'
        self.world = {name: json.loads((ROOT / 'data' / f'{name}.json').read_text())
                      for name in ('materials', 'rules', 'schedule')}
        self.world['schedule'] = []

    def tearDown(self):
        self.tmp.cleanup()

    def tools(self, req=None, **kwargs):
        return Tools(req or request(), self.world, Store(self.db), **kwargs)

    def test_normal_save_persists_confirmed_draft(self):
        t = self.tools()
        result = t.call('save_draft', {'material_id': 'M01', 'suggested_time': TIME})
        self.assertTrue(result['ok'])
        self.assertEqual(result['draft']['confirmation_status'], 'confirmed')
        self.assertEqual(len(Store(self.db).drafts()), 1)

    def test_missing_info_is_not_approved(self):
        r = self.tools(request(mid='M02')).call('check_rules', {'material_id': 'M02'})
        self.assertEqual(r['review_status'], 'needs_input')
        self.assertIn('rights_confirmed', r['missing_fields'])

    def test_missing_info_cannot_save(self):
        r = self.tools(request(mid='M02')).call('save_draft', {'material_id': 'M02', 'suggested_time': TIME})
        self.assertFalse(r['ok'])
        self.assertEqual(Store(self.db).drafts(), [])

    def test_rule_block(self):
        t = self.tools(request(mid='M03'))
        self.assertEqual(t.call('check_rules', {'material_id': 'M03'})['review_status'], 'blocked')
        self.assertFalse(t.call('save_draft', {'material_id': 'M03', 'suggested_time': TIME})['ok'])

    def test_human_review_only_saves_pending(self):
        t = self.tools(request(mid='M04'))
        r = t.call('save_draft', {'material_id': 'M04', 'suggested_time': TIME})
        self.assertEqual(r['draft']['confirmation_status'], 'pending')
        self.assertIn('manual_review', r['draft']['pending_items'])

    def test_conflict_returns_alternative(self):
        self.world['schedule'] = [{'channel': 'video', 'time': TIME}]
        r = self.tools().call('query_slots', {'material_id': 'M01', 'start_time': TIME, 'count': 3})
        self.assertFalse(r['slots'][0]['available'])
        self.assertTrue(r['slots'][1]['available'])

    def test_occupied_time_cannot_save_even_pending(self):
        self.world['schedule'] = [{'channel': 'video', 'time': TIME}]
        self.assertEqual(self.tools().call('save_draft', {'material_id': 'M01', 'suggested_time': TIME})['error'], 'slot_conflict')

    def test_hard_change_pending_does_not_reserve(self):
        r = self.tools().call('save_draft', {'material_id': 'M01', 'suggested_time': ALT})
        self.assertEqual(r['draft']['confirmation_status'], 'pending')
        self.assertIn('time_change', r['draft']['pending_items'])
        t2 = self.tools(request('r2', time=ALT))
        self.assertTrue(t2.call('save_draft', {'material_id': 'M01', 'suggested_time': ALT})['ok'])

    def test_user_confirmation_allows_changed_time(self):
        r = self.tools(request(confirmations={'M01': ALT})).call('save_draft', {'material_id': 'M01', 'suggested_time': ALT})
        self.assertEqual(r['draft']['confirmation_status'], 'confirmed')

    def test_model_cannot_forge_confirmation(self):
        r = self.tools().call('save_draft', {'material_id': 'M01', 'suggested_time': ALT, 'confirmed': True})
        self.assertEqual(r['error'], 'invalid_arguments')
        self.assertEqual(Store(self.db).drafts(), [])

    def test_flexible_time_can_change(self):
        r = self.tools(request(hard=False)).call('save_draft', {'material_id': 'M01', 'suggested_time': ALT})
        self.assertEqual(r['draft']['confirmation_status'], 'confirmed')

    def test_idempotency_survives_new_process_connection(self):
        r1 = self.tools().call('save_draft', {'material_id': 'M01', 'suggested_time': TIME})
        r2 = self.tools().call('save_draft', {'material_id': 'M01', 'suggested_time': TIME})
        self.assertEqual(r1['draft']['draft_id'], r2['draft']['draft_id'])
        self.assertTrue(r2['duplicate'])
        self.assertEqual(len(Store(self.db).drafts()), 1)

    def test_same_id_different_request_rejected(self):
        self.tools()
        with self.assertRaises(ValidationError):
            self.tools(request(time=ALT))

    def test_same_request_new_suggestion_cannot_overwrite(self):
        t = self.tools()
        t.call('save_draft', {'material_id': 'M01', 'suggested_time': TIME})
        self.assertEqual(t.call('save_draft', {'material_id': 'M01', 'suggested_time': ALT})['error'], 'draft_immutable')

    def test_duplicate_after_new_external_conflict_not_success(self):
        t = self.tools()
        t.call('save_draft', {'material_id': 'M01', 'suggested_time': TIME})
        self.world['schedule'].append({'channel': 'video', 'time': TIME})
        r = t.call('save_draft', {'material_id': 'M01', 'suggested_time': TIME})
        self.assertFalse(r['ok'])
        self.assertEqual(r['error'], 'saved_slot_now_conflicts')
        self.assertEqual(len(Store(self.db).drafts()), 1)

    def test_save_failure_has_no_database_row(self):
        r = self.tools(failures={'save_draft': 1}).call('save_draft', {'material_id': 'M01', 'suggested_time': TIME})
        self.assertFalse(r['ok'])
        self.assertEqual(Store(self.db).drafts(), [])

    def test_timezone_equivalent_conflicts(self):
        self.world['schedule'] = [{'channel': 'video', 'time': '2026-11-01T10:00:00Z'}]
        self.assertEqual(self.tools().call('save_draft', {'material_id': 'M01', 'suggested_time': TIME})['error'], 'slot_conflict')

    def test_save_rechecks_after_query(self):
        t = self.tools()
        t.call('query_slots', {'material_id': 'M01', 'start_time': TIME, 'count': 1})
        self.world['schedule'].append({'channel': 'video', 'time': TIME})
        self.assertEqual(t.call('save_draft', {'material_id': 'M01', 'suggested_time': TIME})['error'], 'slot_conflict')

    def test_concurrent_slot_save_only_one_wins(self):
        t1 = self.tools(request('a'))
        t2 = self.tools(request('b'))
        def save(t):
            return t.call('save_draft', {'material_id': 'M01', 'suggested_time': TIME})
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(save, [t1, t2]))
        self.assertEqual(sum(r['ok'] for r in results), 1)
        self.assertEqual(len(Store(self.db).drafts()), 1)

    def test_unknown_material(self):
        r = self.tools(request(mid='UNKNOWN')).call('read_material', {'material_id': 'UNKNOWN'})
        self.assertEqual(r['error'], 'material_not_found')

    def test_unknown_tool_and_cross_request_material_rejected(self):
        t = self.tools()
        self.assertFalse(t.call('publish', {})['ok'])
        self.assertFalse(t.call('read_material', {'material_id': 'M03'})['ok'])

    def test_invalid_requests(self):
        for change in [None, {}, {'request_id': 'x', 'items': []},
                       {'request_id': 'x', 'items': [{'material_id': 'M01', 'requested_time': '2026-11-01T18:00:00', 'hard_time': True}]},
                       {'request_id': 'x', 'items': [{'material_id': 'M01', 'requested_time': TIME, 'hard_time': 'false'}]}]:
            with self.subTest(change=change), self.assertRaises(ValidationError):
                Request.parse(change)

    def test_non_hour_slot_rejected(self):
        r = self.tools().call('save_draft', {'material_id': 'M01', 'suggested_time': '2026-11-01T18:30:00+08:00'})
        self.assertFalse(r['ok'])

    def test_timezone_conversion_overflow_is_validation_error(self):
        with self.assertRaises(ValidationError):
            request(time='0001-01-01T00:00:00+01:00')

    def test_slot_window_overflow_is_tool_error(self):
        r = self.tools().call('query_slots', {'material_id': 'M01', 'start_time': '9999-12-31T23:00:00Z', 'count': 2})
        self.assertFalse(r['ok'])
        self.assertEqual(r['error'], 'invalid_arguments')
